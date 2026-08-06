"""Build compact market + portfolio + analysis context for 安崽 chat."""

from __future__ import annotations

import json
import logging
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
            f"今日{_fmt_pct(h.change_pct)} "
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

    highlights = report.get("highlights") or []
    if isinstance(highlights, list) and highlights:
        lines.append("要点：" + "；".join(str(h)[:80] for h in highlights[:5]))

    agents = report.get("agents") or report.get("agent_steps") or []
    if isinstance(agents, list) and agents:
        for step in agents[:4]:
            if not isinstance(step, dict):
                continue
            label = step.get("label") or step.get("id") or "席位"
            summary = step.get("summary") or step.get("stance") or ""
            lines.append(f"- {label}：{summary}"[:120])

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
    job = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.user_id == user_id, AnalysisJob.status == "done")
        .order_by(AnalysisJob.id.desc())
        .first()
    )
    lines = ["【最近分析】"]
    if job is None:
        lines.append("（暂无）")
        return "\n".join(lines)

    report: dict[str, Any] | None = None
    if job.report_json:
        try:
            raw = json.loads(job.report_json)
            if isinstance(raw, dict):
                report = raw
        except json.JSONDecodeError:
            report = None

    lines.append(f"范围 {job.scope} · 档位 {job.degree}")
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


def silent_portfolio_context(db: Session, user_id: int) -> str:
    """Compact warehouse facts for awareness — not a broadcast script."""
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    lines = [
        "【心里有数·仓库】",
        "仅供心里对齐。用户没问仓位/盈亏/风险时：回复里禁止出现市值、今日涨跌%、持仓名、分散建议。",
    ]
    holdings = list(pf.holdings or [])
    if not holdings:
        lines.append("暂无持仓。")
        return "\n".join(lines)

    lines.append(
        f"总市值 {_fmt_money(pf.total_market_value)} · "
        f"今日 {_fmt_pct(pf.day_pnl_pct)} · "
        f"累计 {_fmt_pct(pf.total_pnl_pct)} · "
        f"共 {len(holdings)} 只"
    )
    ranked = sorted(holdings, key=lambda h: float(h.market_value or 0), reverse=True)
    for h in ranked[:3]:
        w = h.weight
        w_s = f"{w:.1f}%" if w is not None else "—"
        lines.append(
            f"- {h.symbol} {h.name or ''} "
            f"今日{_fmt_pct(h.change_pct)} · 仓位{w_s}"
        )
    if len(ranked) > 3:
        lines.append(f"…另有 {len(ranked) - 3} 只，明细用 get_portfolio")
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
