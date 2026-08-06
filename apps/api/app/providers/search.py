"""Symbol / name search via East Money suggest API."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from app.providers.quote import get_quote, get_quotes

logger = logging.getLogger(__name__)

_EM_SUGGEST = "https://searchapi.eastmoney.com/api/suggest/get"
_EM_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"
_CODE_RE = re.compile(r"^\d{6}$")


@dataclass
class SearchHit:
    symbol: str
    name: str
    market: str
    kind: str  # stock | etf | index | us
    price: float | None = None
    change_pct: float | None = None


def _market_from_row(mkt_num: object, market_type: object, symbol: str, classify: str = "") -> str:
    c = (classify or "").upper()
    if c == "HK" or "港" in (classify or ""):
        return "HK"
    try:
        n = int(mkt_num)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        try:
            n = int(market_type)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            n = -1
    # East Money: 1 SH, 0/2 SZ, 116 HK, 105/106 US…
    if n == 116:
        return "HK"
    if n in (1, 17):
        return "SH"
    if n in (0, 2):
        return "SZ"
    if n in (105, 106, 107) or n >= 100:
        return "US"
    if symbol and symbol[0].isalpha():
        return "US"
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _kind_from_row(classify: str, security_type_name: str, market: str) -> str:
    c = (classify or "").lower()
    st = security_type_name or ""
    if market == "HK":
        return "hk"
    if market == "US":
        return "us"
    if "index" in c or "指数" in st:
        return "index"
    if c == "fund" or "基金" in st or "ETF" in st.upper():
        return "etf"
    return "stock"


def _mock_search(q: str, limit: int) -> list[SearchHit]:
    pool = [
        SearchHit("510300", "沪深300ETF", "SH", "etf", 4.12, 0.35),
        SearchHit("510050", "上证50ETF", "SH", "etf", 2.88, -0.12),
        SearchHit("159915", "创业板ETF", "SZ", "etf", 1.95, 0.80),
        SearchHit("600519", "贵州茅台", "SH", "stock", 1680.0, 1.2),
        SearchHit("300750", "宁德时代", "SZ", "stock", 185.0, -0.5),
        SearchHit("513100", "纳指ETF", "SH", "etf", 1.42, 0.6),
    ]
    ql = q.strip().lower()
    hits = [
        h
        for h in pool
        if ql in h.symbol.lower() or ql in h.name.lower() or ql in h.name
    ]
    return hits[:limit] or pool[: min(3, limit)]


def search_symbols(q: str, limit: int = 12) -> list[SearchHit]:
    query = (q or "").strip()
    if not query:
        return []

    rows: list[dict] = []
    try:
        with httpx.Client(timeout=6.0) as client:
            resp = client.get(
                _EM_SUGGEST,
                params={
                    "input": query,
                    "type": "14",
                    "token": _EM_TOKEN,
                    "count": str(min(max(limit, 1), 20)),
                },
                headers={"Referer": "https://www.eastmoney.com/"},
            )
            resp.raise_for_status()
            payload = resp.json()
            table = payload.get("QuotationCodeTable") or {}
            data = table.get("Data") or []
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
    except Exception:
        logger.exception("EM suggest failed for %s", query)
        rows = []

    hits: list[SearchHit] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        symbol = str(row.get("Code") or row.get("UnifiedCode") or "").strip()
        name = str(row.get("Name") or "").strip() or symbol
        if not symbol:
            continue
        classify = str(row.get("Classify") or "")
        # Skip sector boards — not useful for lookup
        if classify.upper() == "BK":
            continue
        market = _market_from_row(
            row.get("MktNum"),
            row.get("MarketType"),
            symbol,
            classify,
        )
        kind = _kind_from_row(
            str(row.get("Classify") or ""),
            str(row.get("SecurityTypeName") or ""),
            market,
        )
        key = (market, symbol)
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(symbol=symbol, name=name, market=market, kind=kind))
        if len(hits) >= limit:
            break

    # Exact 6-digit code: ensure quote even if suggest misses
    if _CODE_RE.match(query) and not any(h.symbol == query for h in hits):
        market = "SH" if query.startswith(("5", "6", "9")) else "SZ"
        try:
            qte = get_quote(query, market)
            hits.insert(
                0,
                SearchHit(
                    symbol=qte.symbol,
                    name=qte.name or query,
                    market=qte.market,
                    kind="etf" if query.startswith(("15", "16", "50", "51", "56", "58")) else "stock",
                    price=qte.price,
                    change_pct=qte.change_pct,
                ),
            )
            hits = hits[:limit]
        except Exception:
            logger.exception("quote fallback failed for %s", query)

    if not hits:
        return _mock_search(query, limit)

    cn_hk = [(h.symbol, h.market) for h in hits if h.market in ("SH", "SZ", "HK")]
    quotes = get_quotes(cn_hk) if cn_hk else {}
    for h in hits:
        qte = quotes.get(h.symbol)
        if qte:
            h.price = qte.price
            h.change_pct = qte.change_pct
            if qte.name:
                h.name = qte.name
    return hits


def format_search_summary(q: str, limit: int = 5) -> str:
    hits = search_symbols(q, limit=max(1, min(int(limit or 5), 8)))
    if not hits:
        return f"没搜到「{q}」对应的股票/ETF（勿编造代码）"
    lines = [f"【搜到 · {q}】优先用第 1 条再查行情；回答用人话带代码即可"]
    for i, h in enumerate(hits, 1):
        chg = f"{h.change_pct:+.2f}%" if h.change_pct is not None else "—"
        px = f"{h.price}" if h.price is not None else "—"
        lines.append(
            f"{i}. {h.name}（{h.symbol} {h.market}）· {h.kind} · 现价 {px} · {chg}"
        )
    return "\n".join(lines)


def resolve_best_symbol(q: str) -> SearchHit | None:
    """Best single hit for name→code deep prefetch."""
    hits = search_symbols(q, limit=5)
    if not hits:
        return None
    # Prefer A-share stock/etf over US/HK when query is Chinese
    cn = [h for h in hits if h.market in ("SH", "SZ") and h.kind in ("stock", "etf", "index")]
    return (cn or hits)[0]
