"""Analysis jobs: EvidenceSnapshot + multi-agent committee (template fallback)."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models import AnalysisJob, AnalysisProfile, Holding
from app.providers.cn_calendar import normalize_a_share_day_move, shanghai_now
from app.providers.depth_flow import get_depth_flow
from app.providers.intraday import get_intraday
from app.providers.session import cn_session
from app.providers.kline import get_daily_klines
from app.providers.macro import format_macro_text, topics_mentioned
from app.providers.news import get_holdings_news, get_market_news
from app.services.analysis_recipes import (
    AGENT_LABELS,
    get_recipe,
    list_degrees,
    recipe_for_degree,
    resolve_evidence_tier,
)
from app.services.analysis_tiers import TIER_IDS, normalize_degree
from app.services.portfolio import build_portfolio, consolidate_same_symbol
from app.providers.quote import get_quotes, normalize_symbol
from app.services.rebalance import draft_rebalance_from_snapshot

logger = logging.getLogger(__name__)

_INTRADAY_CAP = 5
_DEPTH_FLOW_CAP = 5
_NEWS_LIGHT = 5
_NEWS_STANDARD = 12
_NEWS_DEEP = 20
_GOLD_ETF_SYMS = frozenset({"159937", "518660", "518880", "518800", "159934"})


def _asset_kind(market: str, symbol: str, name: str = "") -> str:
    """股票 / 场内ETF / 黄金ETF / 场外基金 / 黄金积存 — 委员会证据用。"""
    m = (market or "").upper()
    sym = (symbol or "").strip()
    nm = name or ""
    if m == "JD":
        return "黄金积存"
    if m == "GDS" or sym.upper() == "AU9999":
        return "黄金现货"
    if m == "OF":
        return "场外基金"
    if sym in _GOLD_ETF_SYMS or "黄金" in nm:
        return "黄金ETF"
    if sym.startswith(("51", "56", "58", "15", "16", "12")):
        return "场内ETF"
    return "股票"


def _knowledge_queries(
    scope: str,
    quote_rows: list[dict[str, Any]],
    *,
    head_weight: float | None = None,
) -> list[str]:
    """检索词：纪律/框架向，不当行情关键词。"""
    queries: list[str] = []
    kinds = {str(r.get("asset_kind") or "") for r in quote_rows}
    names = [
        str(r.get("name") or r.get("symbol") or "").strip()
        for r in quote_rows[:3]
        if str(r.get("name") or r.get("symbol") or "").strip()
    ]
    if scope == "portfolio":
        queries.append("持仓分散 仓位偏重 宜减不宜加")
        if isinstance(head_weight, (int, float)) and float(head_weight) >= 35:
            queries.append("仓位太重 别再加仓 观望")
        if any("黄金" in k for k in kinds):
            queries.append("黄金还追吗 仓位纪律")
            queries.append("黄金投资风险 免责 强制召回")
        if any(k in ("场外基金", "场内ETF") or "基金" in k for k in kinds):
            queries.append("基金仓位 观望 加减")
            queries.append("基金投资风险 免责 强制召回")
        if any(k == "股票" for k in kinds):
            queries.append("股票投资风险 免责 强制召回")
    else:
        row = quote_rows[0] if quote_rows else {}
        name = str(row.get("name") or row.get("symbol") or "").strip()
        kind = str(row.get("asset_kind") or "")
        if name:
            queries.append(f"{name} {kind} 怎么看 买卖纪律".strip())
        if "黄金" in kind or "黄金" in name:
            queries.append("黄金还追吗")
            queries.append("黄金投资风险 免责 强制召回")
        elif kind in ("场外基金", "场内ETF") or "基金" in kind:
            queries.append("基金仓位 观望 加减")
            queries.append("基金投资风险 免责 强制召回")
        elif "转债" in kind or "转债" in name:
            queries.append("可转债风险 强赎 强制召回")
        elif kind == "股票" or "股" in kind or "股" in name:
            queries.append("追高 仓位纪律 宜减不宜加")
            queries.append("股票投资风险 免责 强制召回")
        else:
            queries.append("追高 仓位纪律 宜减不宜加")
    # 去重保序
    out: list[str] = []
    seen: set[str] = set()
    for q in queries:
        q = " ".join(q.split())
        if not q or q in seen:
            continue
        seen.add(q)
        out.append(q)
    return out[:4]


def _pack_knowledge_hits(
    scope: str,
    quote_rows: list[dict[str, Any]],
    *,
    evidence_tier: str,
    head_weight: float | None = None,
) -> list[dict[str, Any]]:
    """Freeze vector/keyword knowledge cards into analysis evidence."""
    from app.services.knowledge import search_cards
    from app.services import knowledge_pg as pg

    lim = {"light": 2, "standard": 3, "deep": 4}.get(evidence_tier, 3)
    queries = _knowledge_queries(scope, quote_rows, head_weight=head_weight)
    if not queries:
        return []
    backend = "Postgres/pgvector" if pg.knowledge_db_configured() else "本地索引"
    merged: dict[str, dict[str, Any]] = {}
    for q in queries:
        try:
            hits = search_cards(q, limit=lim, mode="auto")
        except Exception:
            logger.exception("knowledge search failed q=%s", q)
            continue
        for card, score, channel in hits:
            prev = merged.get(card.id)
            if prev is not None and float(prev.get("score") or 0) >= float(score):
                continue
            body = " ".join((card.body or "").split())
            if len(body) > 220:
                body = body[:220] + "…"
            merged[card.id] = {
                "id": card.id,
                "title": card.title,
                "tags": list(card.tags[:6]),
                "source": card.source or "经验库",
                "date": card.date or "",
                "body": body,
                "score": round(float(score), 3),
                "channel": channel,
                "backend": backend,
                "query": q,
            }
    rows = sorted(merged.values(), key=lambda x: float(x.get("score") or 0), reverse=True)
    # 黄金/基金/股票：至少保留一条「强制召回」风险/免责片段
    kinds = {str(r.get("asset_kind") or "") for r in quote_rows}
    names = " ".join(
        str(r.get("name") or r.get("symbol") or "") for r in quote_rows[:3]
    )
    force_q = ""
    if any("黄金" in k for k in kinds) or "黄金" in names:
        force_q = "黄金投资风险 免责 强制召回"
    elif any(k in ("场外基金", "场内ETF") or "基金" in k for k in kinds):
        force_q = "基金投资风险 免责 强制召回"
    elif any("转债" in k for k in kinds) or "转债" in names:
        force_q = "可转债风险 强赎 强制召回"
    elif any(k == "股票" for k in kinds) or "股" in names:
        force_q = "股票投资风险 免责 强制召回"
    force_row: dict[str, Any] | None = None
    if force_q:
        for h in rows:
            if "强制召回" in (h.get("tags") or []):
                force_row = h
                break
        if force_row is None:
            try:
                risk_hits = search_cards(force_q, limit=3)
            except Exception:
                risk_hits = []
            for card, score, channel in risk_hits:
                tags = list(card.tags or [])
                if "强制召回" not in tags:
                    continue
                body = " ".join((card.body or "").split())
                if len(body) > 220:
                    body = body[:220] + "…"
                force_row = {
                    "id": card.id,
                    "title": card.title,
                    "tags": tags[:6],
                    "source": card.source or "经验库",
                    "date": card.date or "",
                    "body": body,
                    "score": round(float(score), 3),
                    "channel": channel,
                    "backend": backend,
                    "query": "强制召回",
                }
                break
    if force_row is not None:
        rest = [r for r in rows if r.get("id") != force_row.get("id")]
        rows = ([force_row] + rest)[:lim] if lim else [force_row]
    else:
        rows = rows[:lim]
    return rows


def _pack_depth_flow_row(symbol: str, market: str, name: str = "") -> dict[str, Any]:
    """Compact 盘口/资金 for committee evidence (no raw book dump)."""
    snap = get_depth_flow(symbol, market, flow_days=3)
    last = snap.flow_days[-1] if snap.flow_days else None
    row: dict[str, Any] = {
        "symbol": snap.symbol,
        "market": snap.market,
        "name": snap.name or name or symbol,
        "session_state": snap.session_state,
        "book_live": snap.book_live,
        "flow_bias": snap.flow_bias,
        "flow_label": snap.flow_label,
    }
    if last:
        row.update(
            {
                "flow_date": last.date,
                "main_net": last.main_net,
                "main_pct": last.main_pct,
                "super_net": last.super_net,
                "large_net": last.large_net,
                "mid_net": last.mid_net,
                "small_net": last.small_net,
            }
        )
    if snap.book_live and snap.book:
        b1 = snap.book.bids[0] if snap.book.bids else None
        a1 = snap.book.asks[0] if snap.book.asks else None
        if b1:
            row["bid1_price"] = b1.price
            row["bid1_vol"] = b1.volume
        if a1:
            row["ask1_price"] = a1.price
            row["ask1_vol"] = a1.volume
    return row


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_profile(db: Session, user_id: int) -> AnalysisProfile:
    row = db.query(AnalysisProfile).filter(AnalysisProfile.user_id == user_id).first()
    if row is None:
        row = AnalysisProfile(user_id=user_id, degree="standard")
        db.add(row)
        db.commit()
        db.refresh(row)
    elif row.degree not in TIER_IDS:
        row.degree = normalize_degree(row.degree)
        db.commit()
        db.refresh(row)
    return row


def set_profile_degree(db: Session, user_id: int, degree: str) -> AnalysisProfile:
    row = get_or_create_profile(db, user_id)
    row.degree = normalize_degree(degree)
    db.commit()
    db.refresh(row)
    return row


def profile_out(db: Session, user_id: int) -> dict[str, Any]:
    row = get_or_create_profile(db, user_id)
    degrees = list_degrees()
    meta = next((d for d in degrees if d["id"] == row.degree), degrees[0])
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
    on_progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    def prog(pct: int, label: str) -> None:
        if on_progress:
            on_progress(max(0, min(100, int(pct))), label)

    prog(4, "拉取报价…")
    pairs = [(s["symbol"], s["market"]) for s in symbols]
    quotes = get_quotes(pairs) if pairs else {}

    quote_rows: list[dict[str, Any]] = []
    for s in symbols:
        q = quotes.get(s["symbol"])
        as_of = q.as_of if q else None
        day = normalize_a_share_day_move(q.change_pct if q else None, as_of)
        price = q.price if q else None
        prev_close = q.prev_close if q else None
        # 跨日未开盘：今日涨跌归零，昨收对齐现价（与仓库一致）
        if not day["fresh_today"] and isinstance(price, (int, float)) and price > 0:
            prev_close = float(price)
        quote_rows.append(
            {
                "symbol": s["symbol"],
                "market": s["market"],
                "name": (q.name if q and q.name else s.get("name") or s["symbol"]),
                "asset_kind": _asset_kind(
                    s["market"],
                    s["symbol"],
                    (q.name if q and q.name else s.get("name") or ""),
                ),
                "price": price,
                "change_pct": day["change_pct"],
                "last_session_change_pct": day["last_session_change_pct"],
                "prev_close": prev_close,
                "as_of": as_of,
                "fresh_today": day["fresh_today"],
                "day_label": day["day_label"],
            }
        )

    prog(10, "组合持仓切片…")
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
                "day_pnl_pct": h.day_pnl_pct,
                "market_value": h.market_value,
                "shares": h.shares,
                "cost": h.cost,
                # 仓库已按上海日历归零跨日涨跌，分析沿用同一套数
                "change_pct": h.change_pct,
                "prev_close": h.prev_close,
                "last_price": h.last_price,
                "bought_at": h.bought_at,
            }
    except Exception:
        logger.exception("portfolio slice failed")

    for row in quote_rows:
        slice_h = holding_map.get(row["symbol"])
        row["in_portfolio"] = slice_h is not None
        if slice_h:
            # 仓位/成本/今日盈亏来自仓库；行情涨跌与持仓今日盈亏分列
            for k in (
                "weight",
                "pnl_pct",
                "day_pnl",
                "day_pnl_pct",
                "market_value",
                "shares",
                "cost",
                "bought_at",
            ):
                if slice_h.get(k) is not None:
                    row[k] = slice_h[k]
            if slice_h.get("change_pct") is not None:
                row["change_pct"] = slice_h["change_pct"]
            if slice_h.get("prev_close") is not None:
                row["prev_close"] = slice_h["prev_close"]
            if slice_h.get("last_price") is not None:
                row["price"] = slice_h["last_price"]

    # 组合：按仓位权重优先拉分时/资金/新闻（避免 Holding.id 序漏掉头仓）
    ordered_symbols = list(symbols)
    if scope == "portfolio" and any(
        holding_map.get(s["symbol"], {}).get("weight") is not None for s in symbols
    ):
        ordered_symbols = sorted(
            symbols,
            key=lambda s: float(holding_map.get(s["symbol"], {}).get("weight") or 0),
            reverse=True,
        )
        quote_rows.sort(
            key=lambda r: float(r.get("weight") or 0),
            reverse=True,
        )

    prog(16, "指数行情…")
    indices: list[dict[str, Any]] = []
    try:
        idx_quotes = get_quotes([("000001", "SH"), ("399001", "SZ"), ("399006", "SZ")])
        for sym, name in (("000001", "上证"), ("399001", "深成"), ("399006", "创业")):
            q = idx_quotes.get(sym)
            if q:
                day = normalize_a_share_day_move(q.change_pct, q.as_of)
                indices.append(
                    {
                        "symbol": sym,
                        "name": name,
                        "price": q.price,
                        "change_pct": day["change_pct"],
                        "last_session_change_pct": day["last_session_change_pct"],
                        "as_of": q.as_of,
                        "fresh_today": day["fresh_today"],
                        "day_label": day["day_label"],
                    }
                )
    except Exception:
        logger.exception("index snapshot failed")

    prog(22, "分时数据…")
    intraday: list[dict[str, Any]] = []
    # 分时仅场内 SH/SZ；场外基金 / 积存金无交易所分时
    board_syms = [
        s
        for s in ordered_symbols
        if (s.get("market") or "").upper() in {"SH", "SZ"}
    ]
    if evidence_tier in ("standard", "deep") and board_syms:
        for s in board_syms[:_INTRADAY_CAP]:
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

    # 把分时开盘价并入标的行，证据可写「昨收 / 今开 / 最新」
    open_by_sym = {
        str(r.get("symbol") or ""): r.get("open")
        for r in intraday
        if r.get("open") is not None and r.get("symbol")
    }
    for row in quote_rows:
        op = open_by_sym.get(str(row.get("symbol") or ""))
        if op is not None:
            row["open"] = op

    prog(24, "盘口与资金…")
    depth_flow: list[dict[str, Any]] = []
    # 仓库：场内头部 + 全部 OF/JD 说明行；个股：轻量 2 / 标准·深度 5
    if ordered_symbols:
        if scope == "portfolio":
            board_first = [
                s
                for s in ordered_symbols
                if (s.get("market") or "").upper() in {"SH", "SZ"}
            ]
            off_board = [
                s
                for s in ordered_symbols
                if (s.get("market") or "").upper() in {"OF", "JD", "GDS"}
            ]
            depth_targets = board_first[:_DEPTH_FLOW_CAP] + off_board
        else:
            depth_cap = 2 if evidence_tier == "light" else _DEPTH_FLOW_CAP
            depth_targets = ordered_symbols[:depth_cap]
        for s in depth_targets:
            try:
                depth_flow.append(
                    _pack_depth_flow_row(
                        s["symbol"],
                        s.get("market") or "SH",
                        s.get("name") or "",
                    )
                )
            except Exception:
                logger.exception("depth_flow for %s failed", s.get("symbol"))

    prog(28, "相关资讯…")
    news_limit = {
        "light": _NEWS_LIGHT,
        "standard": _NEWS_STANDARD,
        "deep": _NEWS_DEEP,
    }.get(evidence_tier, _NEWS_STANDARD)
    news_items: list[dict[str, Any]] = []
    try:
        from app.services.news_relevance import news_items_to_dicts, rank_and_trim_news

        sym_list = [s["symbol"] for s in ordered_symbols]
        name_map = {
            s["symbol"]: (s.get("name") or "").strip()
            for s in ordered_symbols
            if (s.get("name") or "").strip()
        }
        # 报价行里的简称更准（ETF 尤甚）
        for row in quote_rows:
            sym = str(row.get("symbol") or "")
            nm = str(row.get("name") or "").strip()
            if sym and nm:
                name_map[sym] = nm
        mkt_map = {
            s["symbol"]: (s.get("market") or "SH")
            for s in ordered_symbols
            if s.get("symbol")
        }
        asset_kinds = [
            str(r.get("asset_kind") or "").strip()
            for r in quote_rows
            if str(r.get("asset_kind") or "").strip()
        ]
        name_list = list({v for v in name_map.values() if v})

        # Over-fetch then relevance-trim (pool >> final news_limit)
        holding_pool = max(news_limit * 2, news_limit + 4)
        headline_pool = 0
        world_pool = 0
        if evidence_tier == "light":
            world_pool = 6
        elif evidence_tier == "standard":
            headline_pool = 16
            world_pool = 16
        else:  # deep
            headline_pool = 24
            world_pool = 24

        pool: list[dict[str, Any]] = []
        if sym_list:
            raw = get_holdings_news(
                sym_list,
                limit=holding_pool,
                names=name_map,
                markets=mkt_map,
            )
            pool.extend(news_items_to_dicts(raw, board="holding"))
        if headline_pool > 0:
            try:
                _title, head_rows = get_market_news(limit=headline_pool, board="headline")
                pool.extend(news_items_to_dicts(head_rows, board="headline"))
            except Exception:
                logger.exception("headline news for analysis failed")
        if world_pool > 0:
            try:
                _wt, world_rows = get_market_news(limit=world_pool, board="world")
                pool.extend(news_items_to_dicts(world_rows, board="world"))
            except Exception:
                logger.exception("world news for analysis failed")

        news_items = rank_and_trim_news(
            pool,
            limit=news_limit,
            symbols=sym_list,
            names=name_list,
            asset_kinds=asset_kinds,
        )
        # Keep evidence payload compact
        for n in news_items:
            n["summary"] = (n.get("summary") or "")[:280]
            n.setdefault("symbols", [])
            n.setdefault("region", "cn")
    except Exception:
        logger.exception("news snapshot failed")

    # Multi-period returns from daily K (5d / ~1m / ~3m)
    prog(32, "日K多周期…")
    kline_limit = 20 if evidence_tier == "light" else (70 if evidence_tier == "standard" else 90)
    n_rows = max(len(quote_rows), 1)
    for qi, row in enumerate(quote_rows):
        if n_rows > 1:
            prog(32 + int(6 * qi / n_rows), f"日K {qi + 1}/{n_rows}…")
        try:
            _name, bars = get_daily_klines(
                row["symbol"], row.get("market") or "SH", limit=kline_limit
            )
            row["periods"] = _period_returns(bars)
        except Exception:
            logger.exception("kline periods for %s failed", row.get("symbol"))
            row["periods"] = {}

    prog(40, "宏观与日历…")
    # Conditional macro: topic from names / gold ETF codes
    macro_blocks: list[str] = []
    probe = " ".join(
        f"{r.get('name') or ''} {r.get('symbol') or ''}" for r in quote_rows
    )
    try:
        topic_ids = [str(t.get("id") or "") for t in topics_mentioned(probe)]
        if any(
            r.get("symbol") in {"159937", "518660", "518880", "518800", "159934"}
            or (r.get("market") or "").upper() == "JD"
            for r in quote_rows
        ):
            if "gold" not in topic_ids:
                topic_ids.insert(0, "gold")
        seen: set[str] = set()
        for tid in topic_ids:
            if not tid or tid in seen:
                continue
            seen.add(tid)
            macro_blocks.append(format_macro_text(tid))
            if len(macro_blocks) >= 2:
                break
    except Exception:
        logger.exception("macro snapshot failed")

    prog(42, "经验知识库…")
    knowledge_hits: list[dict[str, Any]] = []
    try:
        ranked_for_kw = sorted(
            quote_rows,
            key=lambda r: float(r.get("weight") or 0),
            reverse=True,
        )
        head_w = None
        if ranked_for_kw and ranked_for_kw[0].get("weight") is not None:
            head_w = float(ranked_for_kw[0]["weight"])
        knowledge_hits = _pack_knowledge_hits(
            scope,
            quote_rows,
            evidence_tier=evidence_tier,
            head_weight=head_w,
        )
    except Exception:
        logger.exception("knowledge snapshot failed")

    prog(44, "证据已齐，召开委员会…")
    sess = cn_session()
    sh_now = shanghai_now()
    from app.services.analysis_orchestra import compute_structure_facts

    structure = compute_structure_facts(
        {"quotes": quote_rows, "indices": indices, "news": news_items, "portfolio": portfolio_slice}
    )
    return {
        "scope": scope,
        "evidence_tier": evidence_tier,
        "captured_at": _now().isoformat(),
        "structure": structure,
        "calendar": {
            "shanghai_date": sh_now.date().isoformat(),
            "shanghai_time": sh_now.strftime("%Y-%m-%d %H:%M"),
            "session_state": sess.state,
            "session_label": sess.label,
            "session_detail": sess.detail,
            "today_means": (
                f"上海日历日 {sh_now.date().isoformat()}；"
                "非今日标签的是昨收/旧点，禁止说成今日盘中"
            ),
        },
        "quotes": quote_rows,
        "indices": indices,
        "intraday": intraday,
        "depth_flow": depth_flow,
        "news": news_items,
        "portfolio": portfolio_slice,
        "macro": macro_blocks,
        "knowledge": knowledge_hits,
    }


def _period_returns(bars: list[Any]) -> dict[str, float | None]:
    """bars oldest→newest; return % changes over approx windows."""
    if not bars or len(bars) < 2:
        return {}
    end = float(getattr(bars[-1], "close", 0) or 0)
    if end <= 0:
        return {}

    def ret(offset: int) -> float | None:
        if len(bars) <= offset:
            return None
        start = float(getattr(bars[-(offset + 1)], "close", 0) or 0)
        if start <= 0:
            return None
        return round((end / start - 1) * 100, 2)

    as_of = str(getattr(bars[-1], "date", "") or "")[:10] or None
    out: dict[str, float | str | None] = {"d5": ret(5), "m1": ret(22), "m3": ret(66)}
    if as_of:
        out["as_of"] = as_of
    return out


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
    depth = list(snapshot.get("depth_flow") or [])
    with_flow = [d for d in depth if d.get("main_net") is not None]
    if with_flow:
        net_sum = sum(float(d["main_net"]) for d in with_flow)
        flow_stance = "偏多" if net_sum > 0 else ("偏空" if net_sum < 0 else "中性")
        add_agent(
            "flow",
            f"头部主力净合计 {net_sum / 1e8:+.2f}亿",
            flow_stance,
            0.55,
            [str(d.get("flow_label") or "") for d in with_flow[:2] if d.get("flow_label")],
        )
    else:
        flow_stance = "偏多" if up > down else ("偏空" if down > up else "中性")
        add_agent("flow", f"涨{up}跌{down}（无分档资金）", flow_stance, 0.4, [])

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
        name = q.get("name") or q["symbol"]
        if st == "偏多":
            summary = f"{name}：短线偏强，可以考虑持有观望"
        elif st == "偏空":
            summary = f"{name}：偏弱，可以考虑宜减不宜加"
        else:
            summary = f"{name}：观望为主"
        if scope == "symbol" and not q.get("in_portfolio"):
            summary = f"{name}：观察标的，先看走势再决定"
        items.append(
            {
                "symbol": q["symbol"],
                "name": name,
                "market": q.get("market") or "SH",
                "stance": st,
                "change_pct": chg,
                "weight": float(w) if w is not None else None,
                "summary": summary,
            }
        )

    highlights: list[str] = []
    if scope == "portfolio":
        top_names = [
            str(q.get("name") or q.get("symbol") or "")
            for q in (top or ranked[:2])
            if str(q.get("name") or q.get("symbol") or "")
        ][:2]
        action = {
            "偏多": "可以考虑持有观望",
            "偏空": "可以考虑宜减不宜加",
        }.get(verdict_stance, "观望为主")
        if len(top_names) >= 2:
            verdict = f"{top_names[0]}、{top_names[1]}是当前主仓，整体【{verdict_stance}】，{action}。"
        elif top_names:
            verdict = f"{top_names[0]}是当前主仓，整体【{verdict_stance}】，{action}。"
        else:
            verdict = f"组合整体【{verdict_stance}】，{action}。"
        if conc is not None and conc >= 30:
            highlights.append("仓位偏集中，注意别单吊一只")
        if avg_chg is not None and sh_chg is not None:
            diff = avg_chg - sh_chg
            if abs(diff) >= 0.3:
                highlights.append(
                    "持仓今天相对大盘偏强" if diff > 0 else "持仓今天相对大盘偏弱"
                )
        if verdict_stance == "偏空" and conc is not None and conc >= 30:
            highlights.append("可以考虑分批再平衡")
        elif verdict_stance == "偏多":
            highlights.append("短线偏好尚可，对照成本再决定加减")
    else:
        q0 = quotes[0] if quotes else None
        name = (q0.get("name") if q0 else None) or (q0["symbol"] if q0 else "标的")
        action = {
            "偏多": "可以考虑持有观望",
            "偏空": "可以考虑宜减不宜加",
        }.get(verdict_stance, "观望为主")
        verdict = f"{name} 整体【{verdict_stance}】，{action}。"
        if q0 and q0.get("in_portfolio"):
            highlights.append("已在仓库持仓里，对照成本看")
        if news:
            highlights.append("有相关资讯，可对照一下")
        else:
            highlights.append("暂无强相关资讯")

    highlights = highlights[:2]
    conf = 0.55 + (0.05 if avg_chg is not None else 0) + (0.05 if news else 0)

    watch: list[str] = []
    for it in items[:3]:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        st = it.get("stance") or verdict_stance
        if st == "偏空":
            watch.append(f"重点看{name}：偏弱，可以考虑宜减不宜加")
        elif conc is not None and float(it.get("weight") or 0) >= 35:
            watch.append(f"重点看{name}：仓位偏重，组合跟着它走，波动要心里有数")
        elif st == "偏多":
            watch.append(f"重点看{name}：短线偏强，冲高别盲目加")
        else:
            watch.append(f"重点看{name}：先盯住，有动静再动")
    watch = watch[:3]

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
        "watch": watch,
        "holding_lines": [],
        "items": items,
        "bullets": highlights,
        "structure": [],
        "actions": [],
        "agents": agent_steps + [judge],
        "template": True,
        "degraded": True,
        "failed_seats": [],
        "quality_note": "委员会未完整跑通，已回退模板快评。",
    }


def _resolve_job_targets(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    if scope == "portfolio":
        targets = _holding_targets(db, user_id)
        if not targets:
            raise ValueError("持仓为空，无法巡检。请先在仓库录入标的。")
        return targets
    if not symbols:
        raise ValueError("请指定至少一只标的（股票 / ETF / 基金 / 黄金均可）")
    targets: list[dict[str, str]] = []
    for s in symbols[:5]:
        raw_mkt = (s.get("market") or "SH").strip().upper()
        if raw_mkt == "HK":
            raise ValueError("港股不开标准分析；请选 A 股、场内 ETF、场外基金或积存金")
        if raw_mkt not in {"SH", "SZ", "JD", "OF", "GDS"}:
            raise ValueError("标的市场仅支持 SH / SZ / OF / JD / GDS")
        sym, mkt = normalize_symbol(s["symbol"], raw_mkt)
        targets.append(
            {
                "symbol": sym,
                "market": mkt,
                "name": (s.get("name") or "").strip() or sym,
            }
        )
    return targets


def prepare_job(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None,
    degree: str | None,
) -> tuple[AnalysisJob, list[dict[str, str]], str, str]:
    """Create running job row; return (job, targets, degree, recipe_id)."""
    profile = get_or_create_profile(db, user_id)
    # 仓库巡检固定标准档（完整委员会），不走轻量、不跟档位选择器
    if scope == "portfolio":
        deg = "standard"
    else:
        deg = normalize_degree(degree or profile.degree or "standard")
    recipe = recipe_for_degree(deg)
    rid = recipe["id"]
    targets = _resolve_job_targets(db, user_id=user_id, scope=scope, symbols=symbols)
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
    return job, targets, deg, rid


def _finalize_job(
    db: Session,
    job: AnalysisJob,
    *,
    snapshot: dict[str, Any] | None,
    report: dict[str, Any] | None,
    error: str = "",
) -> AnalysisJob:
    if snapshot is not None:
        job.snapshot_json = json.dumps(snapshot, ensure_ascii=False)
    if report is not None:
        job.report_json = json.dumps(report, ensure_ascii=False)
        job.status = "done"
        job.error = ""
    else:
        job.status = "failed"
        job.error = (error or "分析失败")[:500]
    job.finished_at = _now()
    db.commit()
    db.refresh(job)
    try:
        prune_old_analysis_jobs(db, int(job.user_id), scope=str(job.scope or ""))
    except Exception:
        logger.exception("prune analysis jobs failed for user %s", job.user_id)
    return job


_ANALYSIS_JOBS_KEEP = 8


def prune_old_analysis_jobs(
    db: Session,
    user_id: int,
    *,
    scope: str = "",
    keep: int = _ANALYSIS_JOBS_KEEP,
) -> int:
    """Keep newest N finished jobs per scope (or all scopes if scope empty); delete older blobs."""
    scopes = [scope] if scope else ["portfolio", "symbol"]
    deleted = 0
    for sc in scopes:
        q = (
            db.query(AnalysisJob)
            .filter(
                AnalysisJob.user_id == user_id,
                AnalysisJob.scope == sc,
                AnalysisJob.status.in_(("done", "failed")),
            )
            .order_by(AnalysisJob.id.desc())
        )
        rows = q.offset(max(1, int(keep))).all()
        for row in rows:
            db.delete(row)
            deleted += 1
    if deleted:
        db.commit()
    return deleted


def create_and_run_job(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None,
    recipe_id: str | None,
    degree: str | None,
) -> AnalysisJob:
    """Sync run (committee with template fallback). Used by POST /jobs."""
    del recipe_id
    job, targets, deg, rid = prepare_job(
        db, user_id=user_id, scope=scope, symbols=symbols, degree=degree
    )
    tier = resolve_evidence_tier(deg, rid)
    try:
        snapshot = build_snapshot(
            db, user_id=user_id, scope=scope, symbols=targets, evidence_tier=tier
        )
        report = _run_committee_or_template(snapshot, scope=scope, degree=deg, recipe_id=rid)
        return _finalize_job(db, job, snapshot=snapshot, report=report)
    except Exception as exc:
        logger.exception("analysis job %s failed", job.id)
        return _finalize_job(db, job, snapshot=None, report=None, error=str(exc))


def _run_committee_or_template(
    snapshot: dict[str, Any],
    *,
    scope: str,
    degree: str,
    recipe_id: str,
) -> dict[str, Any]:
    from app.services import analysis_orchestra as orch

    try:
        report = orch.run_committee(snapshot, scope=scope, degree=degree)
    except Exception:
        logger.exception("committee failed; falling back to template")
        report = build_template_report(snapshot, recipe_id=recipe_id, scope=scope)
    rb = draft_rebalance_from_snapshot(snapshot)
    if rb is not None:
        report["rebalance"] = rb
        # 无 LLM actions 时，用草案 notes 填白话建议
        if not report.get("actions") and rb.get("notes"):
            report["actions"] = [
                f"调仓倾向：{rb.get('stance') or '观望'}（非下单）",
                *list(rb.get("notes") or [])[:1],
            ][:2]
    return report


def _attach_rebalance(snapshot: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    rb = draft_rebalance_from_snapshot(snapshot)
    if rb is not None:
        report["rebalance"] = rb
        if not report.get("actions") and rb.get("notes"):
            report["actions"] = [
                f"调仓倾向：{rb.get('stance') or '观望'}（非下单）",
                *list(rb.get("notes") or [])[:1],
            ][:2]
    return report


def execute_job_events(
    *,
    job_id: int,
    user_id: int,
    scope: str,
    targets: list[dict[str, str]],
    degree: str,
    recipe_id: str,
) -> Iterator[dict[str, Any]]:
    """
    Run evidence + committee for an already-prepared running job.
    Yields the same event types as the analysis page SSE.
    Caller owns DB finalize via events; this opens its own sessions.
    """
    import queue
    import threading

    from app.database import SessionLocal
    from app.services import analysis_live as live
    from app.services import analysis_orchestra as orch

    live.ensure_job(job_id)

    def _emit(ev: dict[str, Any]) -> dict[str, Any]:
        live.publish(job_id, ev)
        return ev

    yield _emit({"type": "meta", "job_id": job_id, "scope": scope, "degree": degree})
    yield _emit({"type": "progress", "pct": 2, "label": "准备分析…", "stage": "prep"})
    tier = resolve_evidence_tier(degree, recipe_id)

    prog_q: queue.Queue[dict[str, Any] | None] = queue.Queue()
    box: dict[str, Any] = {}

    def _snap_worker() -> None:
        def on_progress(pct: int, label: str) -> None:
            prog_q.put(
                {
                    "type": "progress",
                    "pct": pct,
                    "label": label,
                    "stage": "evidence",
                }
            )

        snap_db = SessionLocal()
        try:
            box["snapshot"] = build_snapshot(
                snap_db,
                user_id=user_id,
                scope=scope,
                symbols=targets,
                evidence_tier=tier,
                on_progress=on_progress,
            )
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc
            logger.exception("snapshot failed")
        finally:
            snap_db.close()
            prog_q.put(None)

    threading.Thread(target=_snap_worker, daemon=True).start()
    while True:
        ev = prog_q.get()
        if ev is None:
            break
        yield _emit(ev)

    db = SessionLocal()
    try:
        job = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id)
            .first()
        )
        if job is None:
            yield _emit({"type": "error", "message": "任务不存在"})
            yield _emit({"type": "done", "job_id": job_id, "status": "failed"})
            return

        if "error" in box:
            exc = box["error"]
            _finalize_job(db, job, snapshot=None, report=None, error=str(exc))
            yield _emit({"type": "error", "message": str(exc)[:400]})
            yield _emit(
                {
                    "type": "done",
                    "job_id": job_id,
                    "status": "failed",
                    "job": job_to_out(job),
                }
            )
            return

        snapshot = box["snapshot"]
        yield _emit(
            {
                "type": "progress",
                "pct": 48,
                "label": "委员会开会…",
                "stage": "committee",
            }
        )

        report: dict[str, Any] | None = None
        saw_report = False
        try:
            for ev in orch.iter_committee_events(snapshot, scope=scope, degree=degree):
                if ev.get("type") == "report" and isinstance(ev.get("report"), dict):
                    report = _attach_rebalance(snapshot, ev["report"])
                    ev = {**ev, "report": report}
                    saw_report = True
                yield _emit(ev)
        except Exception as exc:
            logger.exception("stream committee failed")
            yield _emit({"type": "error", "message": str(exc)[:400]})

        if report is None:
            report = build_template_report(snapshot, recipe_id=recipe_id, scope=scope)
            report = _attach_rebalance(snapshot, report)
            if not saw_report:
                yield _emit({"type": "report", "report": report})
            yield _emit({"type": "stage", "stage": "fallback", "label": "已回退模板报告"})

        _finalize_job(db, job, snapshot=snapshot, report=report)
        yield _emit({"type": "progress", "pct": 100, "label": "完成", "stage": "done"})
        yield _emit(
            {
                "type": "done",
                "job_id": job_id,
                "status": "done",
                "job": job_to_out(job),
            }
        )
    finally:
        db.close()


def iter_job_events(
    db: Session,
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None,
    degree: str | None,
) -> Iterator[dict[str, Any]]:
    """Prepare job, stream evidence+committee progress, persist result."""
    job, targets, deg, rid = prepare_job(
        db, user_id=user_id, scope=scope, symbols=symbols, degree=degree
    )
    # Detach so execute_job_events can use its own sessions freely
    job_id = int(job.id)
    db.expunge(job)
    yield from execute_job_events(
        job_id=job_id,
        user_id=user_id,
        scope=scope,
        targets=targets,
        degree=deg,
        recipe_id=rid,
    )


def iter_attach_job_events(
    *,
    user_id: int,
    job_id: int,
) -> Iterator[dict[str, Any]]:
    """Attach to a running (or just-finished) job: replay bus history + live tail."""
    from app.database import SessionLocal
    from app.services import analysis_live as live

    db = SessionLocal()
    try:
        job = get_job(db, job_id, user_id)
        if job is None:
            yield {"type": "error", "message": "任务不存在"}
            yield {"type": "done", "job_id": job_id, "status": "failed"}
            return
        status = job.status
        err = (job.error or "")[:400]
        out = job_to_out(job)
    finally:
        db.close()

    hist = live.history(job_id)
    if not hist and status != "running":
        # Finished before bus existed / process restart — return final snapshot
        if status == "done" and out.get("report"):
            yield {"type": "report", "report": out["report"], "job_id": job_id}
        if status == "failed":
            yield {"type": "error", "message": err or "分析失败"}
        yield {
            "type": "done",
            "job_id": job_id,
            "status": status,
            "job": out,
        }
        return

    for ev in live.subscribe(job_id):
        if ev.get("type") == "ping":
            continue
        yield ev


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


def running_job(
    db: Session, user_id: int, scope: str | None = None
) -> AnalysisJob | None:
    """Most recent in-flight analysis (analysis page or agent-started)."""
    q = db.query(AnalysisJob).filter(
        AnalysisJob.user_id == user_id,
        AnalysisJob.status == "running",
    )
    if scope:
        q = q.filter(AnalysisJob.scope == scope)
    return q.order_by(AnalysisJob.id.desc()).first()


def start_job_background(
    *,
    user_id: int,
    scope: str,
    symbols: list[dict[str, str]] | None = None,
    degree: str | None = None,
) -> AnalysisJob:
    """
    Create a running job and finish it on a daemon thread (emits live bus events).
    Chat can continue; analysis page may attach via GET /jobs/{id}/stream.
    """
    import threading

    from app.database import SessionLocal
    from app.services import analysis_live as live

    db = SessionLocal()
    try:
        existing = running_job(db, user_id)
        if existing is not None:
            db.expunge(existing)
            return existing
        job, targets, deg, rid = prepare_job(
            db, user_id=user_id, scope=scope, symbols=symbols, degree=degree
        )
        job_id = int(job.id)
        live.ensure_job(job_id)
    finally:
        db.close()

    def _worker() -> None:
        try:
            for _ev in execute_job_events(
                job_id=job_id,
                user_id=user_id,
                scope=scope,
                targets=targets,
                degree=deg,
                recipe_id=rid,
            ):
                pass
        except Exception:
            logger.exception("background analysis job %s failed", job_id)
            wdb = SessionLocal()
            try:
                row = (
                    wdb.query(AnalysisJob)
                    .filter(AnalysisJob.id == job_id, AnalysisJob.user_id == user_id)
                    .first()
                )
                if row is not None and row.status == "running":
                    _finalize_job(wdb, row, snapshot=None, report=None, error="分析失败")
                    live.publish(
                        job_id,
                        {
                            "type": "done",
                            "job_id": job_id,
                            "status": "failed",
                            "job": job_to_out(row),
                        },
                    )
            finally:
                wdb.close()

    threading.Thread(target=_worker, name=f"analysis-job-{job_id}", daemon=True).start()
    db2 = SessionLocal()
    try:
        row = db2.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
        if row is None:
            raise RuntimeError("分析任务创建失败")
        db2.expunge(row)
        return row
    finally:
        db2.close()
