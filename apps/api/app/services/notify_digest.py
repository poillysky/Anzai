"""Daily portfolio analysis → WeChat digest (per-user)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.providers.notify import NotifyResult, send_wechat_notify
from app.services import analysis as analysis_svc
from app.services.notify_settings import get_notify_cfg, list_enabled_notify_users

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "notify_digest_state.json"


def _now_bj() -> datetime:
    return datetime.now(_CST)


def _today_bj() -> str:
    return _now_bj().strftime("%Y-%m-%d")


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_PATH.exists():
            return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("notify state read failed")
    return {}


def _save_state(data: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("notify state write failed")


def _user_sent_today(state: dict[str, Any], user_id: int, today: str) -> bool:
    users = state.get("users") if isinstance(state.get("users"), dict) else {}
    entry = users.get(str(user_id)) if isinstance(users, dict) else None
    if isinstance(entry, dict):
        return entry.get("last_sent_date") == today
    # legacy flat key
    return state.get("last_sent_date") == today and state.get("last_user_id") == user_id


def _mark_sent(user_id: int, *, job_id: int, channel: str) -> None:
    today = _today_bj()
    state = _load_state()
    users = state.get("users") if isinstance(state.get("users"), dict) else {}
    users[str(user_id)] = {
        "last_sent_date": today,
        "last_job_id": job_id,
        "last_channel": channel,
        "updated_at": _now_bj().isoformat(),
    }
    state["users"] = users
    _save_state(state)


def format_digest_text(report: dict[str, Any], *, portfolio: dict[str, Any] | None = None) -> str:
    lines: list[str] = []
    day = _today_bj()
    lines.append(f"安崽 · 仓库日报 {day}")
    lines.append("")

    if portfolio:
        total = portfolio.get("total_market_value")
        day_pnl = portfolio.get("day_pnl")
        day_pct = portfolio.get("day_pnl_pct")
        bits: list[str] = []
        if isinstance(total, (int, float)):
            bits.append(f"市值约 {total:,.0f}")
        if isinstance(day_pnl, (int, float)):
            sign = "+" if day_pnl >= 0 else ""
            bits.append(f"今日 {sign}{day_pnl:,.0f}")
        if isinstance(day_pct, (int, float)):
            sign = "+" if day_pct >= 0 else ""
            bits.append(f"{sign}{day_pct:.2f}%")
        if bits:
            lines.append(" · ".join(bits))
            lines.append("")

    verdict = str(report.get("verdict") or report.get("summary") or "").strip()
    stance = str(report.get("stance") or "").strip()
    if verdict:
        if stance and stance not in verdict:
            lines.append(f"【{stance}】{verdict}")
        else:
            lines.append(verdict)
        lines.append("")

    highlights = report.get("highlights") if isinstance(report.get("highlights"), list) else []
    for h in highlights[:3]:
        t = str(h).strip()
        if t:
            lines.append(f"· {t}")

    actions = report.get("actions") if isinstance(report.get("actions"), list) else []
    for a in actions[:2]:
        t = str(a).strip()
        if t:
            lines.append(f"→ {t}")

    items = report.get("items") if isinstance(report.get("items"), list) else []
    if items and not highlights:
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or it.get("symbol") or "").strip()
            summary = str(it.get("summary") or "").strip()
            if name and summary:
                lines.append(f"· {name}：{summary}")

    lines.append("")
    lines.append("仅供参考，不构成投资建议。")
    text = "\n".join(lines).strip()
    if len(text) > 3500:
        text = text[:3400].rstrip() + "\n…（已截断）"
    return text


def parse_weekdays(raw: str) -> set[int]:
    text = (raw or "0,1,2,3,4").strip()
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            d = int(part)
            if 0 <= d <= 6:
                out.add(d)
        except ValueError:
            continue
    return out or {0, 1, 2, 3, 4}


def cfg_due(cfg: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or _now_bj()
    if now.weekday() not in parse_weekdays(str(cfg.get("weekdays") or "")):
        return False
    return now.hour == int(cfg.get("hour") or 15) and now.minute == int(cfg.get("minute") or 10)


def run_portfolio_digest(
    db: Session,
    user_id: int,
    *,
    force: bool = False,
    dry_run: bool = False,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run portfolio analysis for one user and push to their WeChat bridge."""
    cfg = cfg or get_notify_cfg(db, user_id)
    if not cfg.get("enabled") and not dry_run and not force:
        return {"ok": False, "skipped": True, "reason": "未开启微信日报"}

    today = _today_bj()
    state = _load_state()
    if not force and _user_sent_today(state, user_id, today):
        return {
            "ok": True,
            "skipped": True,
            "reason": f"今日已推送过（{today}）",
            "last_sent_date": today,
        }

    degree = str(cfg.get("degree") or "light").strip() or "light"
    try:
        job = analysis_svc.create_and_run_job(
            db,
            user_id=user_id,
            scope="portfolio",
            symbols=None,
            recipe_id=None,
            degree=degree,
        )
    except Exception as exc:
        logger.exception("digest analysis failed user=%s", user_id)
        return {"ok": False, "reason": f"分析失败：{exc}"[:300]}

    if job.status != "done" or not job.report_json:
        return {
            "ok": False,
            "reason": f"分析未完成：{job.error or job.status}",
            "job_id": job.id,
        }

    try:
        report = json.loads(job.report_json)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "报告 JSON 损坏", "job_id": job.id}

    portfolio: dict[str, Any] | None = None
    try:
        snap = json.loads(job.snapshot_json) if job.snapshot_json else {}
        raw_pf = snap.get("portfolio") if isinstance(snap, dict) else None
        if isinstance(raw_pf, dict):
            portfolio = raw_pf
    except Exception:
        portfolio = None

    title = f"安崽仓库日报 · {today}"
    body = format_digest_text(report, portfolio=portfolio)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "user_id": user_id,
            "job_id": job.id,
            "title": title,
            "content": body,
        }

    result: NotifyResult = send_wechat_notify(
        channel=str(cfg.get("channel") or ""),
        token=str(cfg.get("token") or ""),
        title=title,
        content=body,
        wxpusher_uid=str(cfg.get("wxpusher_uid") or ""),
    )
    if result.ok:
        _mark_sent(user_id, job_id=job.id, channel=result.channel)
    return {
        "ok": result.ok,
        "channel": result.channel,
        "detail": result.detail,
        "user_id": user_id,
        "job_id": job.id,
        "title": title,
        "content_preview": body[:200],
        "reason": "" if result.ok else result.detail,
    }


def run_due_digests(db: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Scheduler tick: push for each enabled user whose clock matches."""
    now = now or _now_bj()
    results: list[dict[str, Any]] = []
    for user_id, cfg in list_enabled_notify_users(db):
        if not cfg_due(cfg, now):
            continue
        try:
            results.append(run_portfolio_digest(db, user_id, cfg=cfg, force=False))
        except Exception as exc:
            logger.exception("digest for user %s failed", user_id)
            results.append({"ok": False, "user_id": user_id, "reason": str(exc)[:200]})
    return results
