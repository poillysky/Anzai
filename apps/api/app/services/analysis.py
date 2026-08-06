"""Analysis jobs: EvidenceSnapshot + rule-based (template) reports. LLM agents in P1."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import AnalysisJob, AnalysisProfile, Holding
from app.providers.intraday import get_intraday
from app.providers.news import get_holdings_news
from app.services.analysis_recipes import (
    AGENT_LABELS,
    get_recipe,
    list_degrees,
    recipe_for_degree,
    resolve_evidence_tier,
)
from app.services.portfolio import build_portfolio, consolidate_same_symbol
from app.services.quote import get_quotes, normalize_symbol

logger = logging.getLogger(__name__)

_INTRADAY_CAP = 5
_NEWS_LIGHT = 5
_NEWS_STANDARD = 12
_NEWS_DEEP = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_profile(db: Session, user_id: int) -> AnalysisProfile:
    row = db.query(AnalysisProfile).filter(AnalysisProfile.user_id == user_id).first()
    if row is None:
        row = AnalysisProfile(user_id=user_id, degree="standard")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def set_profile_degree(db: Session, user_id: int, degree: str) -> AnalysisProfile:
    row = get_or_create_profile(db, user_id)
    row.degree = degree
    db.commit()
    db.refresh(row)
    return row


def profile_out(db: Session, user_id: int) -> dict[str, Any]:
    row = get_or_create_profile(db, user_id)
    meta = next((d for d in list_degrees() if d["id"] == row.degree), list_degrees()[1])
    return {
        "degree": row.degree,
        "degree_label": meta["label"],
        "blurb": meta["blurb"],
        "default_recipe": meta["default_recipe"],
        "updated_at": row.updated_at,
    }


def _parse_symbols_json(raw: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def job_to_out(job: AnalysisJob) -> dict[str, Any]:
    report = None
    if job.status == "done" and job.report_json:
        try:
            report = json.loads(job.report_json)
        except json.JSONDecodeError:
            report = None
    return {
        "id": job.id,
        "scope": job.scope,
        "symbols": _parse_symbols_json(job.symbols_json),
        "recipe_id": job.recipe_id,
        "degree": job.degree,
        "status": job.status,
        "error": job.error or "",
        "report": report,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
    }


def _holding_targets(db: Session, user_id: int) -> list[dict[str, str]]:
    consolidate_same_symbol(db, user_id)
    rows = (
        db.query(Holding)
        .filter(Holding.user_id == user_id)
        .order_by(Holding.id.asc())
        .all()
    )
    return [
        {"symbol": h.symbol, "market": h.market, "name": h.name or h.symbol}
        for h in rows
    ]


def build_snapshot(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]],
    evidence_tier: str,
) -> dict[str, Any]:
    pairs = [(s["symbol"], s["market"]) for s in symbols]
    quotes = get_quotes(pairs) if pairs else {}

    quote_rows: list[dict[str, Any]] = []
    for s in symbols:
        q = quotes.get(s["symbol"])
        quote_rows.append(
            {
                "symbol": s["symbol"],
                "market": s["market"],
                "name": (q.name if q and q.name else s.get("name") or s["symbol"]),
                "price": q.price if q else None,
                "change_pct": q.change_pct if q else None,
                "prev_close": q.prev_close if q else None,
            }
        )

    portfolio_slice: dict[str, Any] | None = None
    holding_map: dict[str, Any] = {}
    try:
        portfolio = build_portfolio(db, user_id)
        portfolio_slice = {
            "total_market_value": portfolio.total_market_value,
            "total_pnl": portfolio.total_pnl,
            "total_pnl_pct": portfolio.total_pnl_pct,
            "day_pnl": portfolio.day_pnl,
            "day_pnl_pct": portfolio.day_pnl_pct,
            "holding_count": len(portfolio.holdings),
        }
        for h in portfolio.holdings:
            holding_map[h.symbol] = {
                "weight": h.weight,
                "pnl_pct": h.pnl_pct,
                "day_pnl": h.day_pnl,
                "market_value": h.market_value,
                "shares": h.shares,
                "cost": h.cost,
            }
    except Exception:
        logger.exception("portfolio slice failed")

    for row in quote_rows:
        slice_h = holding_map.get(row["symbol"])
        row["in_portfolio"] = slice_h is not None
        if slice_h:
            row.update(slice_h)

    indices: list[dict[str, Any]] = []
    try:
        idx_quotes = get_quotes([("000001", "SH"), ("399001", "SZ"), ("399006", "SZ")])
        for sym, name in (("000001", "上证"), ("399001", "深成"), ("399006", "创业")):
            q = idx_quotes.get(sym)
            if q:
                indices.append(
                    {
                        "symbol": sym,
                        "name": name,
                        "price": q.price,
                        "change_pct": q.change_pct,
                    }
                )
    except Exception:
        logger.exception("index snapshot failed")

    intraday: list[dict[str, Any]] = []
    if evidence_tier in ("standard", "deep") and symbols:
        for s in symbols[:_INTRADAY_CAP]:
            try:
                series = get_intraday(s["symbol"], s["market"], s.get("name") or "")
                pts = series.points
                first = pts[0].price if pts else None
                last = pts[-1].price if pts else None
                intraday.append(
                    {
                        "symbol": s["symbol"],
                        "point_count": len(pts),
                        "open": first,
                        "last": last,
                        "prev_close": series.prev_close,
                    }
                )
            except Exception:
                logger.exception("intraday for %s failed", s["symbol"])

    news_limit = {
        "light": _NEWS_LIGHT,
        "standard": _NEWS_STANDARD,
        "deep": _NEWS_DEEP,
    }.get(evidence_tier, _NEWS_STANDARD)
    news_items: list[dict[str, Any]] = []
    try:
        sym_list = [s["symbol"] for s in symbols]
        if sym_list:
            raw = get_holdings_news(sym_list, limit=news_limit)
            for i in raw:
                news_items.append(
                    {
                        "id": i.id,
                        "title": i.title,
                        "summary": (i.summary or "")[:280],
                        "source": i.source,
                        "published_at": i.published_at,
                        "symbols": list(i.symbols),
                    }
                )
    except Exception:
        logger.exception("news snapshot failed")

    return {
        "scope": scope,
        "evidence_tier": evidence_tier,
        "captured_at": _now().isoformat(),
        "quotes": quote_rows,
        "indices": indices,
        "intraday": intraday,
        "news": news_items,
        "portfolio": portfolio_slice,
    }


def _stance_from_pct(pct: float | None) -> str:
    if pct is None:
        return "数据不足"
    if pct >= 1.0:
        return "偏多"
    if pct <= -1.0:
        return "偏空"
    return "中性"


def build_template_report(
    snapshot: dict[str, Any],
    *,
    recipe_id: str,
    scope: str,
) -> dict[str, Any]:
    recipe = get_recipe(recipe_id)
    agents_enabled: list[str] = list(recipe["agents"])
    weights: dict[str, float] = dict(recipe["weights"])
    quotes: list[dict[str, Any]] = snapshot.get("quotes") or []
    news: list[dict[str, Any]] = snapshot.get("news") or []
    portfolio = snapshot.get("portfolio") or {}
    indices: list[dict[str, Any]] = snapshot.get("indices") or []

    avg_chg = None
    chgs = [q["change_pct"] for q in quotes if q.get("change_pct") is not None]
    if chgs:
        avg_chg = sum(chgs) / len(chgs)

    sh = next((i for i in indices if i.get("symbol") == "000001"), None)
    sh_chg = sh.get("change_pct") if sh else None

    ranked = sorted(
        quotes,
        key=lambda x: float(x.get("weight") or 0),
        reverse=True,
    )
    top = [q for q in ranked if q.get("weight") is not None][:5]
    conc = float(top[0]["weight"]) if top and top[0].get("weight") is not None else None

    agent_steps: list[dict[str, Any]] = []

    def add_agent(aid: str, summary: str, stance: str, confidence: float, bullets: list[str]) -> None:
        if aid not in agents_enabled:
            return
        agent_steps.append(
            {
                "id": aid,
                "label": AGENT_LABELS.get(aid, aid),
                "status": "done",
                "summary": summary,
                "stance": stance,
                "confidence": round(confidence, 2),
                "bullets": bullets[:3],
                "weight": weights.get(aid),
            }
        )

    if avg_chg is None:
        add_agent("trend", "暂无可用涨跌数据", "数据不足", 0.3, [])
    else:
        rel = ""
        if sh_chg is not None:
            diff = avg_chg - sh_chg
            if diff > 0.3:
                rel = "相对上证偏强"
            elif diff < -0.3:
                rel = "相对上证偏弱"
            else:
                rel = "与上证大致同步"
        add_agent(
            "trend",
            f"均涨跌 {avg_chg:+.2f}%",
            _stance_from_pct(avg_chg),
            0.65,
            [rel] if rel else [],
        )

    if news:
        add_agent("news", f"相关资讯 {len(news)} 条", "中性", 0.55, [])
    else:
        add_agent("news", "暂无相关资讯", "数据不足", 0.35, [])

    up = sum(1 for q in quotes if (q.get("change_pct") or 0) > 0)
    down = sum(1 for q in quotes if (q.get("change_pct") or 0) < 0)
    flow_stance = "偏多" if up > down else ("偏空" if down > up else "中性")
    add_agent("flow", f"涨{up}跌{down}", flow_stance, 0.5, [])

    risk_stance = "偏空" if conc is not None and conc >= 35 else "中性"
    add_agent(
        "risk",
        f"头部权重 {conc:.0f}%" if conc is not None else "无持仓权重",
        risk_stance,
        0.6,
        [],
    )

    score = 0.0
    wsum = 0.0
    stance_score = {"偏多": 1.0, "中性": 0.0, "偏空": -1.0, "数据不足": 0.0}
    for step in agent_steps:
        w = float(step.get("weight") or 0)
        score += stance_score.get(step["stance"], 0.0) * w
        wsum += w
    blend = score / wsum if wsum else 0.0
    if blend >= 0.25:
        verdict_stance = "偏多"
    elif blend <= -0.25:
        verdict_stance = "偏空"
    else:
        verdict_stance = "中性"

    # Per-symbol briefs (sorted by weight desc, else by |change|)
    items_src = ranked if any(q.get("weight") is not None for q in quotes) else sorted(
        quotes,
        key=lambda x: abs(float(x.get("change_pct") or 0)),
        reverse=True,
    )
    items: list[dict[str, Any]] = []
    for q in items_src:
        chg = q.get("change_pct")
        w = q.get("weight")
        st = _stance_from_pct(chg if isinstance(chg, (int, float)) else None)
        bits: list[str] = []
        if chg is not None:
            bits.append(f"今日 {chg:+.2f}%")
        if w is not None:
            bits.append(f"仓位 {float(w):.1f}%")
        if q.get("pnl_pct") is not None and q.get("in_portfolio"):
            bits.append(f"相对成本 {float(q['pnl_pct']):+.1f}%")
        if scope == "symbol" and not q.get("in_portfolio"):
            bits.append("观察标的")
        if not bits:
            bits.append("数据有限")
        items.append(
            {
                "symbol": q["symbol"],
                "name": q.get("name") or q["symbol"],
                "market": q.get("market") or "SH",
                "stance": st,
                "change_pct": chg,
                "weight": float(w) if w is not None else None,
                "summary": " · ".join(bits),
            }
        )

    highlights: list[str] = []
    if scope == "portfolio":
        day = portfolio.get("day_pnl_pct")
        if day is not None:
            verdict = f"组合今日 {day:+.2f}%，整体【{verdict_stance}】"
        else:
            verdict = f"组合整体【{verdict_stance}】"
        if conc is not None:
            if conc >= 30:
                highlights.append(f"头部偏集中（约 {conc:.0f}%），注意风险")
            else:
                highlights.append(f"头部权重约 {conc:.0f}%")
        if avg_chg is not None and sh_chg is not None:
            diff = avg_chg - sh_chg
            if abs(diff) >= 0.3:
                highlights.append(
                    "持仓相对上证偏强" if diff > 0 else "持仓相对上证偏弱"
                )
        if verdict_stance == "偏空" and conc is not None and conc >= 30:
            highlights.append("可考虑分批再平衡（仅建议）")
        elif verdict_stance == "偏多":
            highlights.append("短线偏好尚可，对照成本与仓位")
    else:
        q0 = quotes[0] if quotes else None
        name = (q0.get("name") if q0 else None) or (q0["symbol"] if q0 else "标的")
        chg = q0.get("change_pct") if q0 else None
        chg_txt = f"{chg:+.2f}%" if chg is not None else "涨跌暂缺"
        verdict = f"{name} 今日 {chg_txt}，【{verdict_stance}】"
        if q0 and q0.get("in_portfolio") and q0.get("weight") is not None:
            highlights.append(f"占组合约 {float(q0['weight']):.1f}%")
        if news:
            highlights.append(f"相关资讯 {len(news)} 条可关注")
        elif not news:
            highlights.append("暂无强相关资讯")

    highlights = highlights[:2]
    conf = 0.55 + (0.05 if avg_chg is not None else 0) + (0.05 if news else 0)

    judge = {
        "id": "judge",
        "label": AGENT_LABELS["judge"],
        "status": "done",
        "summary": verdict,
        "stance": verdict_stance,
        "confidence": round(min(conf, 0.85), 2),
        "bullets": highlights,
        "weight": None,
    }

    return {
        "verdict": verdict,
        "stance": verdict_stance,
        "confidence": judge["confidence"],
        "highlights": highlights,
        "items": items,
        "bullets": highlights,
        "structure": [],
        "actions": [],
        "agents": agent_steps + [judge],
        "template": True,
    }


def create_and_run_job(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None,
    recipe_id: str | None,
    degree: str | None,
) -> AnalysisJob:
    """Both portfolio & symbol jobs share the same three tiers (admin-configured)."""
    del recipe_id  # tiers are the only recipe source now
    profile = get_or_create_profile(db, user_id)
    deg = degree or profile.degree or "standard"
    if deg not in ("light", "standard", "deep"):
        deg = "standard"
    recipe = recipe_for_degree(deg)
    rid = recipe["id"]

    if scope == "portfolio":
        targets = _holding_targets(db, user_id)
        if not targets:
            raise ValueError("持仓为空，无法巡检。请先在仓库录入标的。")
    else:
        if not symbols:
            raise ValueError("请指定至少一只股票")
        targets = []
        for s in symbols[:5]:
            sym, mkt = normalize_symbol(s["symbol"], s.get("market") or "SH")
            targets.append(
                {
                    "symbol": sym,
                    "market": mkt,
                    "name": (s.get("name") or "").strip() or sym,
                }
            )

    tier = resolve_evidence_tier(deg, rid)
    job = AnalysisJob(
        user_id=user_id,
        scope=scope,
        symbols_json=json.dumps(targets, ensure_ascii=False),
        recipe_id=rid,
        degree=deg,
        status="running",
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    try:
        snapshot = build_snapshot(
            db, user_id=user_id, scope=scope, symbols=targets, evidence_tier=tier
        )
        report = build_template_report(snapshot, recipe_id=rid, scope=scope)
        job.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        job.report_json = json.dumps(report, ensure_ascii=False)
        job.status = "done"
        job.finished_at = _now()
        job.error = ""
    except Exception as exc:
        logger.exception("analysis job %s failed", job.id)
        job.status = "failed"
        job.error = str(exc)[:500]
        job.finished_at = _now()

    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: int, user_id: int) -> AnalysisJob | None:
    return (
        db.query(AnalysisJob)
        .filter(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id)
        .first()
    )


def latest_job(
    db: Session, user_id: int, scope: str | None = None
) -> AnalysisJob | None:
    q = db.query(AnalysisJob).filter(
        AnalysisJob.user_id == user_id,
        AnalysisJob.status == "done",
    )
    if scope:
        q = q.filter(AnalysisJob.scope == scope)
    return q.order_by(AnalysisJob.id.desc()).first()
