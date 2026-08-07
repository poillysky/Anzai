"""Build compact market + portfolio + analysis context for 安崽 chat."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import AnalysisJob
from app.providers.quote import get_quotes
from app.services.portfolio import build_portfolio, consolidate_same_symbol

logger = logging.getLogger(__name__)

# Major board indices injected every turn (same source as /api/market/indices)
_BOARD_INDICES: list[tuple[str, str, str]] = [
    ("000001", "SH", "上证指数"),
    ("399001", "SZ", "深证成指"),
    ("399006", "SZ", "创业板指"),
]


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_points(price: float, prev: float | None) -> str:
    if prev is None or prev <= 0:
        return "—"
    return f"{price - prev:+.2f}"


def market_context() -> str:
    """Realtime board indices — numbers the model must cite, not invent."""
    lines = [
        "【行情快照】",
        "以下点位/涨跌来自行情接口；回答「涨了多少、现在多少点」必须用这里的数，禁止编造。",
    ]
    try:
        quotes = get_quotes([(sym, mkt) for sym, mkt, _ in _BOARD_INDICES])
    except Exception:
        logger.exception("market_context quote fetch failed")
        lines.append("（行情暂时拉不到，请让用户看「市场」页，你不要猜点位）")
        return "\n".join(lines)

    any_ok = False
    for sym, _mkt, name in _BOARD_INDICES:
        q = quotes.get(sym)
        if q is None or not q.price or q.price <= 0:
            lines.append(f"- {name}（{sym}）：暂无报价")
            continue
        any_ok = True
        chg_pts = _fmt_points(q.price, q.prev_close)
        lines.append(
            f"- {name}（{sym}）：现价 {q.price:.2f} · "
            f"涨跌 {chg_pts} 点 · 涨跌幅 {_fmt_pct(q.change_pct)}"
            + (f" · 昨收 {q.prev_close:.2f}" if q.prev_close else "")
        )
    if not any_ok:
        lines.append("（行情暂时拉不到，请让用户看「市场」页，你不要猜点位）")
    return "\n".join(lines)


def portfolio_context(db: Session, user_id: int) -> str:
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    lines = [
        "【仓库持仓】",
        (
            f"总市值 {_fmt_money(pf.total_market_value)} · "
            f"总成本 {_fmt_money(pf.total_cost)} · "
            f"累计盈亏 {_fmt_money(pf.total_pnl)}（{_fmt_pct(pf.total_pnl_pct)}） · "
            f"今日 {_fmt_money(pf.day_pnl)}（{_fmt_pct(pf.day_pnl_pct)}）"
        ),
    ]
    if not pf.holdings:
        lines.append("（暂无持仓）")
        return "\n".join(lines)

    ranked = sorted(
        pf.holdings,
        key=lambda h: float(h.market_value or 0),
        reverse=True,
    )
    for h in ranked[:20]:
        lines.append(
            f"- {h.symbol} {h.name or ''} "
            f"市值{_fmt_money(h.market_value)} "
            f"盈亏{_fmt_money(h.pnl)}（{_fmt_pct(h.pnl_pct)}） "
            f"今日盈亏{_fmt_money(h.day_pnl)}（{_fmt_pct(h.day_pnl_pct)}） "
            f"行情{_fmt_pct(h.change_pct)} "
            f"仓位{_fmt_pct(h.weight)}"
        )
    if len(ranked) > 20:
        lines.append(f"…另有 {len(ranked) - 20} 只未列出")
    return "\n".join(lines)


def _summarize_report(report: dict[str, Any] | None) -> list[str]:
    if not report or not isinstance(report, dict):
        return ["（暂无分析报告，可先去「分析」页跑一趟）"]
    lines: list[str] = []
    verdict = report.get("verdict") or report.get("summary") or ""
    if isinstance(verdict, dict):
        verdict = verdict.get("text") or verdict.get("summary") or ""
    if verdict:
        lines.append(f"结论：{str(verdict)[:400]}")
    stance = report.get("stance")
    if stance:
        lines.append(f"立场：{stance}")
    if report.get("template"):
        lines.append("来源：模板快评（委员会未跑通时的兜底）")
    elif report.get("degraded"):
        fails = report.get("failed_seats") or []
        bit = "、".join(str(x) for x in fails[:4]) if fails else "部分席位"
        lines.append(f"来源：委员会（降级 · {bit}异常）")
    else:
        lines.append("来源：多 Agent 委员会")
    note = str(report.get("quality_note") or "").strip()
    if note:
        lines.append(f"质量说明：{note[:160]}")

    highlights = report.get("highlights") or []
    if isinstance(highlights, list) and highlights:
        lines.append("要点：" + "；".join(str(h)[:80] for h in highlights[:5]))

    actions = report.get("actions") or []
    if isinstance(actions, list) and actions:
        lines.append("建议：" + "；".join(str(a)[:80] for a in actions[:4]))

    agents = report.get("agents") or report.get("agent_steps") or []
    if isinstance(agents, list) and agents:
        for step in agents[:6]:
            if not isinstance(step, dict):
                continue
            label = step.get("label") or step.get("id") or "席位"
            summary = step.get("summary") or step.get("stance") or ""
            st = str(step.get("status") or "done")
            mark = "（失败）" if st == "failed" else ""
            lines.append(f"- {label}{mark}：{summary}"[:120])
    items = report.get("items") or []
    if isinstance(items, list) and items:
        for it in items[:6]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"· {it.get('symbol')} {it.get('name') or ''} "
                f"{it.get('summary') or it.get('stance') or ''}"[:120]
            )

    if not lines:
        lines.append(json.dumps(report, ensure_ascii=False)[:600])
    return lines


def analysis_context(db: Session, user_id: int) -> str:
    """Very short analysis hint — not a full report dump."""
    from app.services import analysis as analysis_svc

    lines = ["【最近分析】"]
    running = analysis_svc.running_job(db, user_id)
    if running is not None:
        lines.append(
            f"进行中：#{running.id} {running.scope}/{running.degree} — "
            "结论未出；用户问起就说还在跑，跑完下轮带上。"
        )

    job = analysis_svc.latest_job(db, user_id)
    if job is None:
        if running is None:
            lines.append("（暂无已完成报告）")
        return "\n".join(lines)

    report: dict[str, Any] | None = None
    if job.report_json:
        try:
            raw = json.loads(job.report_json)
            if isinstance(raw, dict):
                report = raw
        except json.JSONDecodeError:
            report = None

    lines.append(f"已完成：#{job.id} · {job.scope} · {job.degree}")
    if report:
        verdict = report.get("verdict") or report.get("summary") or ""
        if isinstance(verdict, dict):
            verdict = verdict.get("text") or verdict.get("summary") or ""
        if verdict:
            lines.append(f"结论：{str(verdict)[:160]}")
        stance = report.get("stance")
        if stance:
            lines.append(f"立场：{stance}")
    return "\n".join(lines)


# After a job finishes, keep a detailed summary in chat for this many *user* turns.
ANALYSIS_FOLLOW_USER_TURNS = 6


def _parse_job_report(job: AnalysisJob | None) -> dict[str, Any] | None:
    if job is None or not getattr(job, "report_json", None):
        return None
    try:
        raw = json.loads(job.report_json)
        return raw if isinstance(raw, dict) else None
    except (TypeError, json.JSONDecodeError):
        return None


def _user_turns_since_job_finish(
    db: Session,
    user_id: int,
    *,
    conversation_id: int | None,
    finished_at: datetime,
) -> int:
    """Count user messages in this conversation after the job finished."""
    from app.models import AgentMessage
    from app.services import agent_history as history_svc

    conv_id = conversation_id
    if conv_id is None:
        try:
            conv = history_svc.get_or_create_active(db, user_id)
            conv_id = conv.id
        except Exception:
            return ANALYSIS_FOLLOW_USER_TURNS + 1

    q = db.query(AgentMessage).filter(
        AgentMessage.user_id == user_id,
        AgentMessage.conversation_id == conv_id,
        AgentMessage.role == "user",
        AgentMessage.created_at > finished_at,
    )
    return int(q.count() or 0)


def analysis_follow_context(
    db: Session,
    user_id: int,
    *,
    conversation_id: int | None = None,
) -> str | None:
    """Detailed report summary for N user turns after analysis completes.

    Returns None when outside the follow window (caller may fall back to short hint).
    """
    from app.services import analysis as analysis_svc

    running = analysis_svc.running_job(db, user_id)
    if running is not None:
        # New run in progress — don't keep pushing the previous detailed dump
        return None

    job = analysis_svc.latest_job(db, user_id)
    if job is None or str(job.status or "") != "done":
        return None
    finished = getattr(job, "finished_at", None) or getattr(job, "created_at", None)
    if finished is None:
        return None

    # prepare_chat 在 append_user_message 之前调用本函数，DB 计数不含本轮 → +1
    prior = _user_turns_since_job_finish(
        db, user_id, conversation_id=conversation_id, finished_at=finished
    )
    turns = prior + 1
    if turns > ANALYSIS_FOLLOW_USER_TURNS:
        return None

    report = _parse_job_report(job)
    remaining = max(0, ANALYSIS_FOLLOW_USER_TURNS - turns)
    lines = [
        "【近期分析·详细摘要】",
        f"任务 #{job.id} · {job.scope} · {job.degree}"
        f"（完成后第 {turns}/{ANALYSIS_FOLLOW_USER_TURNS} 轮；"
        f"约还可跟 {remaining} 轮用户追问）",
        "用法：用户追问结论/风险/某席位/某标的时据此回答；"
        "没问分析时不要整段复读；数字仍以本轮实时查询为准。",
    ]
    if report and report.get("template"):
        lines.append("质量：模板兜底，提及时诚实说简化版。")
    elif report and report.get("degraded"):
        fails = report.get("failed_seats") or []
        bit = "、".join(str(x) for x in fails[:4]) if fails else "部分席位"
        lines.append(f"质量：降级（{bit}），可提一句有的席没谈成。")
    lines.extend(_summarize_report(report))
    return "\n".join(lines)


def silent_portfolio_context(db: Session, user_id: int) -> str:
    """Compact warehouse facts for awareness — not a broadcast script."""
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    lines = [
        "【心里有数·仓库】",
        "字段：day_pnl=今日盈亏(账户现金流转)；quote_chg=行情涨跌(对昨收)；勿混用。",
        "仅供心里对齐。用户没问仓位/盈亏时：回复里禁止出现市值、day_pnl、quote_chg、持仓名、分散建议。",
    ]
    holdings = list(pf.holdings or [])
    if not holdings:
        lines.append("暂无持仓。")
    else:
        lines.append(
            f"总市值 {_fmt_money(pf.total_market_value)} · "
            f"day_pnl {_fmt_pct(pf.day_pnl_pct)} · "
            f"累计 {_fmt_pct(pf.total_pnl_pct)} · "
            f"共 {len(holdings)} 只"
        )
        ranked = sorted(holdings, key=lambda h: float(h.market_value or 0), reverse=True)
        for h in ranked[:3]:
            w = h.weight
            w_s = f"{w:.1f}%" if w is not None else "—"
            lines.append(
                f"- {h.symbol} {h.name or ''} "
                f"day_pnl={_fmt_pct(h.day_pnl_pct)} · "
                f"quote_chg={_fmt_pct(h.change_pct)} · "
                f"仓位{w_s}"
            )
        if len(ranked) > 3:
            lines.append(f"…另有 {len(ranked) - 3} 只，明细用 get_portfolio")

    # 轻量「上次巡检」记忆：一句结论，避免每轮复读全文；有进行中则先提示
    try:
        from app.services import analysis as analysis_svc

        running = analysis_svc.running_job(db, user_id)
        if running is not None:
            lines.append(
                f"分析进行中：{running.scope}/{running.degree}（任务 #{running.id}）。"
                "用户没问进度时别念报告；问到了就说还在跑、跑完下轮会带上结论。"
            )
        job = analysis_svc.latest_job(db, user_id)
        report = None
        if job is not None and job.report_json:
            try:
                raw = json.loads(job.report_json)
                if isinstance(raw, dict):
                    report = raw
            except json.JSONDecodeError:
                report = None
        if isinstance(report, dict):
            verdict = str(report.get("verdict") or "").strip()
            if verdict:
                lines.append(
                    f"最近分析结论（已完成·心里有数，可在对话里带一句）：{verdict[:160]}"
                )
    except Exception:
        pass

    return "\n".join(lines)


def build_user_context(
    db: Session,
    user_id: int,
    *,
    omit_portfolio: bool = False,
    include_portfolio: bool | None = None,
    include_analysis: bool | None = None,
) -> str:
    """Legacy helper — chat path prefers agent_scene.build_scene_context."""
    want_pf = (
        include_portfolio
        if include_portfolio is not None
        else (not omit_portfolio)
    )
    want_an = include_analysis if include_analysis is not None else want_pf
    parts = [
        "【数据说明】指数/个股/商品/新闻以【本轮实时查询】或工具为准；昨收要说清。"
    ]
    if want_pf:
        parts.append(silent_portfolio_context(db, user_id))
    if want_an:
        parts.append(analysis_context(db, user_id))
    if not want_pf and not want_an:
        parts.append("本轮未附仓库/报告，勿编造账户数字。")
    return "\n\n".join(parts)
