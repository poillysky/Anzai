"""Agent turn: wait for analysis job, keep SSE alive, then inject report for the model."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

_JOB_ID_RE = re.compile(r"任务\s*#(\d+)")


def estimate_eta_minutes(degree: str | None) -> tuple[int, int]:
    d = (degree or "standard").strip().lower()
    if d == "light":
        return 1, 2
    if d == "deep":
        return 4, 6
    return 2, 3


def friendly_wait_line(*, label: str, degree: str | None) -> str:
    lo, hi = estimate_eta_minutes(degree)
    tip = label.strip() or "这份报告"
    return f"安崽正在快马加鞭分析{tip}，预计约 {lo}～{hi} 分钟，请耐心等待～"


def parse_job_id_from_start_text(text: str) -> int | None:
    m = _JOB_ID_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def analysis_label_from_job(job: Any) -> str:
    scope = str(getattr(job, "scope", "") or "portfolio")
    if scope == "portfolio":
        return "仓库"
    try:
        import json

        raw = getattr(job, "symbols_json", None) or "[]"
        symbols = json.loads(raw) if isinstance(raw, str) else (raw or [])
        if isinstance(symbols, list) and symbols:
            s0 = symbols[0] if isinstance(symbols[0], dict) else {}
            nm = str(s0.get("name") or s0.get("symbol") or "").strip()
            if nm:
                return nm
    except Exception:
        pass
    return "这只标的"


async def iter_wait_analysis(
    *,
    user_id: int,
    job_id: int,
    label: str = "",
    degree: str | None = None,
    timeout_s: float = 280.0,
    interval_s: float = 4.0,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield SSE-ish dicts while polling:
      {type: token|tool_status|error, ...}
    Final yield includes done flag + report text for model injection:
      {type: "analysis_ready", text: "...", ok: bool}
    """
    from app.database import SessionLocal
    from app.services import analysis as analysis_svc
    from app.services import analysis_pending as pending_svc
    from app.services.agent_tools import _tool_analysis_snapshot

    wait_line = friendly_wait_line(label=label, degree=degree)
    # 前端用 card 展示等待面板，不往气泡塞重复文案
    yield {
        "type": "tool_status",
        "label": "安崽分析中",
        "name": "start_analysis",
        "ack": wait_line,
    }

    deadline = time.monotonic() + max(60.0, float(timeout_s))
    last_status = "running"
    ticks = 0
    while time.monotonic() < deadline:
        await asyncio.sleep(interval_s)
        ticks += 1
        db = SessionLocal()
        try:
            job = analysis_svc.get_job(db, job_id, user_id)
            if job is None:
                yield {
                    "type": "analysis_ready",
                    "ok": False,
                    "text": "【分析状态】任务找不到了，可能已被清理。请稍后再试或去「分析」页看看。",
                }
                return
            st = str(job.status or "")
            if st != last_status:
                last_status = st
            if st == "running":
                if ticks % 3 == 1:
                    yield {
                        "type": "tool_status",
                        "label": "安崽分析中",
                        "name": "start_analysis",
                    }
                continue
            # done / failed / other
            pending_svc.clear_pending(user_id)
            # Re-mark briefly so snapshot can treat as just_ready? Better: build snapshot directly.
            snap = _snapshot_for_finished_job(db, user_id, job)
            ok = st == "done"
            yield {
                "type": "tool_status",
                "label": "整理结论中" if ok else "分析未完成",
                "name": "start_analysis",
            }
            yield {"type": "analysis_ready", "ok": ok, "text": snap}
            return
        except Exception:
            logger.exception("poll analysis job failed")
            yield {
                "type": "tool_status",
                "label": "分析状态刷新中…",
                "name": "start_analysis",
            }
        finally:
            db.close()

    # timeout
    db = SessionLocal()
    try:
        snap = _tool_analysis_snapshot(db, user_id)
        yield {
            "type": "analysis_ready",
            "ok": False,
            "text": (
                "【分析状态】这轮等得有点久，委员会还没完全出炉。"
                "你可以去「分析」页看进度，或稍后再问我一声，我再把结论带上。\n"
                + snap
            ),
        }
    finally:
        db.close()


def _snapshot_for_finished_job(db: Any, user_id: int, job: Any) -> str:
    """Format finished job like get_analysis_snapshot with 刚跑完播报指令."""
    import json

    from app.services.agent_context import _summarize_report

    lines: list[str] = [
        "【分析状态】",
        "【刚跑完·必须主动播报】委员会已结束。"
        "请直接用人话讲清结论（verdict + 倾向），"
        "不要再说「还在分析/请等待」；可提一句去「分析」页看全文。"
        "禁止 Markdown 加粗星号。",
    ]
    st = str(job.status or "")
    if st == "failed":
        lines.append(
            f"任务 #{job.id} 失败：{(job.error or '未知错误')[:200]}。"
            "话术：老实说没跑通，可请用户再试或去分析页看。"
        )
        return "\n".join(lines)

    report: dict[str, Any] | None = None
    if job.report_json:
        try:
            raw = json.loads(job.report_json)
            if isinstance(raw, dict):
                report = raw
        except json.JSONDecodeError:
            report = None

    lines.append(
        f"最近完成：任务 #{job.id} · {job.scope} · {job.degree} · 配方 {job.recipe_id}"
        "（刚出炉，请主动播报）"
    )
    if report and report.get("template"):
        lines.append("质量：模板兜底（委员会未完整跑通），播报时要诚实说「简化版/兜底」。")
    elif report and report.get("degraded"):
        fails = report.get("failed_seats") or []
        bit = "、".join(str(x) for x in fails[:4]) if fails else "部分席位"
        lines.append(f"质量：部分席位异常（{bit}），播报时提一句「有的席没谈成」。")
    lines.extend(_summarize_report(report))
    return "\n".join(lines)
