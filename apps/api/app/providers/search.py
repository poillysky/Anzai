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
_EM_IPO = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_CODE_RE = re.compile(r"^\d{6}$")
_HK_CODE_RE = re.compile(r"^\d{5}$")

# IPO apply calendar cache: code -> {issue_price, apply_date, apply_code, name}
_IPO_CACHE: dict[str, dict[str, object]] = {}
_IPO_CACHE_AT = 0.0
_IPO_TTL = 30 * 60

# 聊天口语 → 纯名称，否则东财 suggest 常返回空再误落到 mock
_SEARCH_STRIP = (
    "港股的",
    "港股",
    "A股的",
    "A股",
    "股价",
    "现价",
    "最新价",
    "报价",
    "价格",
    "多少钱",
    "多少",
    "现在",
    "查一下",
    "查下",
    "查询",
    "帮我查",
    "帮我看看",
    "帮我看",
    "看看",
    "看下",
    "一下",
    "怎么样",
    "怎样",
    "怎么看",
    "情况",
    "走势",
    "行情",
    "今天",
    "吗",
    "呢",
    "啊",
    "的",
)


@dataclass
class SearchHit:
    symbol: str
    name: str
    market: str
    kind: str  # stock | etf | index | hk | us | ipo
    price: float | None = None
    change_pct: float | None = None
    note: str | None = None


def clean_search_query(q: str) -> str:
    """Strip chatter so suggest gets a company/code token (小米股价→小米)."""
    s = (q or "").strip()
    if not s:
        return ""
    for w in _SEARCH_STRIP:
        s = s.replace(w, " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s[:32]


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
    """Offline/demo only: only return hits that actually match the query."""
    pool = [
        SearchHit("510300", "沪深300ETF", "SH", "etf", 4.12, 0.35),
        SearchHit("510050", "上证50ETF", "SH", "etf", 2.88, -0.12),
        SearchHit("159915", "创业板ETF", "SZ", "etf", 1.95, 0.80),
        SearchHit("600519", "贵州茅台", "SH", "stock", 1680.0, 1.2),
        SearchHit("300750", "宁德时代", "SZ", "stock", 185.0, -0.5),
        SearchHit("513100", "纳指ETF", "SH", "etf", 1.42, 0.6),
        SearchHit("01810", "小米集团－Ｗ", "HK", "hk", 26.84, -2.89),
        SearchHit("00700", "腾讯控股", "HK", "hk", 480.0, -2.4),
    ]
    ql = q.strip().lower()
    if not ql:
        return []
    hits = [
        h
        for h in pool
        if ql in h.symbol.lower() or ql in h.name.lower() or ql in h.name
    ]
    return hits[:limit]


def _fetch_suggest_rows(query: str, limit: int) -> list[dict]:
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
                return [r for r in data if isinstance(r, dict)]
    except Exception:
        logger.exception("EM suggest failed for %s", query)
    return []


def _rows_to_hits(rows: list[dict], limit: int) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        symbol = str(row.get("Code") or row.get("UnifiedCode") or "").strip()
        name = str(row.get("Name") or "").strip() or symbol
        if not symbol:
            continue
        classify = str(row.get("Classify") or "")
        st_name = str(row.get("SecurityTypeName") or "")
        # Skip sector boards / futures — not useful for chat quote lookup
        if classify.upper() == "BK":
            continue
        if "期货" in st_name or "FUT" in classify.upper():
            continue
        market = _market_from_row(
            row.get("MktNum"),
            row.get("MarketType"),
            symbol,
            classify,
        )
        kind = _kind_from_row(classify, st_name, market)
        key = (market, symbol)
        if key in seen:
            continue
        seen.add(key)
        hits.append(SearchHit(symbol=symbol, name=name, market=market, kind=kind))
        if len(hits) >= limit:
            break
    return hits


def _quote_code_fallback(query: str, hits: list[SearchHit], limit: int) -> list[SearchHit]:
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
                    kind="etf"
                    if query.startswith(("15", "16", "50", "51", "56", "58"))
                    else "stock",
                    price=qte.price,
                    change_pct=qte.change_pct,
                ),
            )
            return hits[:limit]
        except Exception:
            logger.exception("quote fallback failed for %s", query)
    if _HK_CODE_RE.match(query):
        code = query.zfill(5)
        if not any(h.symbol == code and h.market == "HK" for h in hits):
            try:
                qte = get_quote(code, "HK")
                hits.insert(
                    0,
                    SearchHit(
                        symbol=qte.symbol,
                        name=qte.name or code,
                        market="HK",
                        kind="hk",
                        price=qte.price,
                        change_pct=qte.change_pct,
                    ),
                )
                return hits[:limit]
            except Exception:
                logger.exception("HK quote fallback failed for %s", code)
    return hits


def _fetch_ipo_by_codes(codes: list[str]) -> dict[str, dict[str, object]]:
    """East Money 新股申购表 — 未上市代码用发行价补全搜索展示。"""
    global _IPO_CACHE_AT, _IPO_CACHE
    import time

    now = time.time()
    need = [c for c in codes if c and c not in _IPO_CACHE]
    if need and (now - _IPO_CACHE_AT > _IPO_TTL or not _IPO_CACHE):
        try:
            with httpx.Client(timeout=8.0) as client:
                # Recent apply calendar (covers upcoming IPOs)
                resp = client.get(
                    _EM_IPO,
                    params={
                        "sortColumns": "APPLY_DATE",
                        "sortTypes": "-1",
                        "pageSize": "80",
                        "pageNumber": "1",
                        "reportName": "RPTA_APP_IPOAPPLY",
                        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,APPLY_CODE,ISSUE_PRICE,APPLY_DATE,LISTING_DATE",
                        "source": "WEB",
                        "client": "WEB",
                    },
                    headers={"Referer": "https://data.eastmoney.com/xg/"},
                )
                resp.raise_for_status()
                payload = resp.json()
                rows = ((payload.get("result") or {}) if isinstance(payload, dict) else {}).get(
                    "data"
                ) or []
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        code = str(row.get("SECURITY_CODE") or "").strip()
                        if not code:
                            continue
                        _IPO_CACHE[code] = {
                            "name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                            "issue_price": row.get("ISSUE_PRICE"),
                            "apply_date": str(row.get("APPLY_DATE") or "")[:10],
                            "listing_date": str(row.get("LISTING_DATE") or "")[:10],
                            "apply_code": str(row.get("APPLY_CODE") or "").strip(),
                        }
                    _IPO_CACHE_AT = now
        except Exception:
            logger.exception("EM IPO calendar fetch failed")

    # Targeted lookup for codes still missing (older / filtered out of top page)
    missing = [c for c in codes if c and c not in _IPO_CACHE]
    for code in missing[:6]:
        try:
            with httpx.Client(timeout=6.0) as client:
                resp = client.get(
                    _EM_IPO,
                    params={
                        "reportName": "RPTA_APP_IPOAPPLY",
                        "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,APPLY_CODE,ISSUE_PRICE,APPLY_DATE,LISTING_DATE",
                        "filter": f'(SECURITY_CODE="{code}")',
                        "pageNumber": "1",
                        "pageSize": "5",
                        "source": "WEB",
                        "client": "WEB",
                    },
                    headers={"Referer": "https://data.eastmoney.com/xg/"},
                )
                resp.raise_for_status()
                payload = resp.json()
                rows = ((payload.get("result") or {}) if isinstance(payload, dict) else {}).get(
                    "data"
                ) or []
                if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                    row = rows[0]
                    _IPO_CACHE[code] = {
                        "name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                        "issue_price": row.get("ISSUE_PRICE"),
                        "apply_date": str(row.get("APPLY_DATE") or "")[:10],
                        "listing_date": str(row.get("LISTING_DATE") or "")[:10],
                        "apply_code": str(row.get("APPLY_CODE") or "").strip(),
                    }
        except Exception:
            logger.exception("EM IPO lookup failed for %s", code)

    return {c: _IPO_CACHE[c] for c in codes if c in _IPO_CACHE}


def _enrich_ipo_hits(hits: list[SearchHit]) -> list[SearchHit]:
    """未上市 / 无行情：挂发行价与申购提示，避免「查不到」观感。"""
    need = [h.symbol for h in hits if h.market in ("SH", "SZ") and (h.price is None or h.price <= 0)]
    if not need:
        return hits
    ipo_map = _fetch_ipo_by_codes(need)
    for h in hits:
        row = ipo_map.get(h.symbol)
        if not row:
            continue
        issue = row.get("issue_price")
        if isinstance(issue, (int, float)) and float(issue) > 0 and (h.price is None or h.price <= 0):
            h.price = float(issue)
        if row.get("name") and (not h.name or h.name == h.symbol):
            h.name = str(row["name"])
        apply_d = str(row.get("apply_date") or "").strip()
        list_d = str(row.get("listing_date") or "").strip()
        bits = ["新股待上市"]
        if isinstance(issue, (int, float)) and float(issue) > 0:
            bits.append(f"发行价 {float(issue):.2f}")
        if apply_d and apply_d != "None":
            bits.append(f"申购 {apply_d}")
        elif list_d and list_d != "None":
            bits.append(f"上市 {list_d}")
        h.note = " · ".join(bits)
        if h.kind == "stock":
            h.kind = "ipo"
    return hits


def is_pending_ipo(symbol: str, market: str) -> bool:
    """True if A-share code is in IPO calendar and not yet trading (no live quote)."""
    from app.providers.cn_calendar import parse_as_of_date, shanghai_today

    sym = (symbol or "").strip()
    mkt = (market or "").upper()
    if not sym or mkt not in {"SH", "SZ"}:
        return False
    try:
        qte = get_quote(sym, mkt)
        if qte and qte.live and qte.price and float(qte.price) > 0:
            return False
    except Exception:
        pass
    row = _fetch_ipo_by_codes([sym]).get(sym)
    if not row:
        return False
    listing = str(row.get("listing_date") or "").strip()
    if not listing or listing in {"None", "null"}:
        return True
    ld = parse_as_of_date(listing)
    if ld is None:
        return True
    return ld > shanghai_today()


def _attach_quotes(hits: list[SearchHit]) -> list[SearchHit]:
    cn_hk = [(h.symbol, h.market) for h in hits if h.market in ("SH", "SZ", "HK")]
    quotes = get_quotes(cn_hk) if cn_hk else {}
    for h in hits:
        qte = quotes.get(h.symbol)
        if qte:
            if qte.price and qte.price > 0:
                h.price = qte.price
                h.change_pct = qte.change_pct
            if qte.name and qte.live:
                h.name = qte.name
    return _enrich_ipo_hits(hits)


def search_symbols(q: str, limit: int = 12) -> list[SearchHit]:
    raw = (q or "").strip()
    if not raw:
        return []
    query = clean_search_query(raw) or raw
    prefer_hk = "港" in raw or "HK" in raw.upper() or "hk" in raw

    rows = _fetch_suggest_rows(query, limit)
    # 口语残留导致空结果时，再试清洗后的核心词
    if not rows and query != raw:
        rows = _fetch_suggest_rows(query, limit)
    if not rows and raw != query:
        rows = _fetch_suggest_rows(raw, limit)

    hits = _rows_to_hits(rows, max(limit * 2, 12))
    hits = _quote_code_fallback(query, hits, max(limit * 2, 12))

    if prefer_hk:
        hits = sorted(hits, key=lambda h: (0 if h.market == "HK" else 1))

    # 主连正股优先于轮证（81810 WR / 购沽）
    def _is_warrant(h: SearchHit) -> bool:
        n = h.name or ""
        return any(x in n for x in ("ＷＲ", "WR", "购", "沽", "轮证")) or (
            h.market == "HK" and h.symbol.isdigit() and not h.symbol.startswith("0")
        )

    hits = sorted(hits, key=lambda h: (1 if _is_warrant(h) else 0))
    hits = hits[:limit]

    if not hits:
        return _mock_search(query, limit)

    return _attach_quotes(hits)


def format_search_summary(q: str, limit: int = 5) -> str:
    hits = search_symbols(q, limit=max(1, min(int(limit or 5), 8)))
    if not hits:
        return f"没搜到「{q}」对应的股票/ETF（勿编造代码）"
    label = clean_search_query(q) or q
    lines = [f"【搜到 · {label}】优先用第 1 条再查行情；回答用人话带代码即可"]
    for i, h in enumerate(hits, 1):
        chg = f"{h.change_pct:+.2f}%" if h.change_pct is not None else "—"
        px = f"{h.price}" if h.price is not None else "—"
        lines.append(
            f"{i}. {h.name}（{h.symbol} {h.market}）· {h.kind} · 现价 {px} · {chg}"
            + (f" · {h.note}" if h.note else "")
        )
    return "\n".join(lines)


def _name_match_score(query: str, hit: SearchHit) -> int:
    """Higher = better name/code match for resolve_best."""
    q = (query or "").strip().lower()
    if not q:
        return 0
    name = (hit.name or "").lower().replace("－", "-").replace(" ", "")
    sym = (hit.symbol or "").lower()
    score = 0
    if q == sym or q.zfill(5) == sym:
        score += 100
    if name.startswith(q) or q in name:
        score += 50
    if any(tok and tok in name for tok in re.split(r"[\s\-－]+", q) if len(tok) >= 2):
        score += 20
    # 正股优于轮证/期货残留
    if hit.kind in ("stock", "hk", "etf"):
        score += 5
    if any(x in (hit.name or "") for x in ("ＷＲ", "WR", "购", "沽")):
        score -= 40
    if hit.market == "US":
        score -= 10
    return score


def resolve_best_symbol(q: str) -> SearchHit | None:
    """Best single hit for name→code deep prefetch."""
    raw = (q or "").strip()
    if not raw:
        return None
    prefer_hk = "港" in raw or "HK" in raw.upper()
    core = clean_search_query(raw) or raw
    hits = search_symbols(raw, limit=8)
    if not hits:
        return None

    ranked = sorted(
        hits,
        key=lambda h: (
            _name_match_score(core, h),
            1 if prefer_hk and h.market == "HK" else 0,
            1 if h.market in ("SH", "SZ") and not prefer_hk else 0,
        ),
        reverse=True,
    )
    best = ranked[0]
    # 名称几乎对不上时，勿把无关 A 股 ETF 当真命中
    if _name_match_score(core, best) < 20 and not (
        core.isdigit() and best.symbol in {core, core.zfill(5), core.zfill(6)}
    ):
        # 仍允许港股/正股名包含核心字（小米→小米集团）
        if core not in (best.name or "") and not (best.name or "").startswith(core):
            hk_hit = next(
                (
                    h
                    for h in ranked
                    if h.market == "HK" and _name_match_score(core, h) >= 20
                ),
                None,
            )
            if hk_hit:
                return hk_hit
            return None
    return best
