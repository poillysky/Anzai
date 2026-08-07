"""场内 ETF + 场外基金看板 — 行情「基金」页。

数据源与股票榜一致走东财动态接口（不维护手写基金名单）：
- 场内 ETF：push2 clist（b:MK0021…）
- 行业 chips：行业板块 clist（m:90+t:2），再按板块名匹配 ETF
- 跨境：东财跨境/港股 ETF 板块（MK0843/MK0844）
- 场外：天天基金 rankhandler 排行
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from app.providers.eastmoney import EM_HEADERS, clist_urls, em_float
from app.providers.kline import get_daily_klines

logger = logging.getLogger(__name__)

_FUND_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://fund.eastmoney.com/",
}

_NAV_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_NAV_TTL = 300.0
_HIST_CACHE: dict[str, tuple[float, list[tuple[str, float]]]] = {}
_HIST_TTL = 600.0
_SEARCH_CACHE: dict[str, tuple[float, list[dict[str, object]]]] = {}
_SEARCH_TTL = 120.0

# 与股票 leaders 同源 clist
_ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"
_CROSS_FS = "b:MK0843,b:MK0844"  # 港股通/海外相关 ETF 板块
_INDUSTRY_FS = "m:90+t:2+f:!50"
_CLIST_FIELDS = "f12,f13,f14,f2,f3,f6,f18"
_LIST_LIMIT = 100
_INDUSTRY_GROUP_LIMIT = 40

# 宽基：只定义「指数类」名称规则（不是基金名单），名单由 clist 动态过滤
_BROAD_NAME_KEYS = (
    "沪深300",
    "中证500",
    "中证1000",
    "中证100",
    "中证800",
    "中证A50",
    "中证A500",
    "上证50",
    "上证180",
    "上证指数",
    "深证成指",
    "创业板",
    "科创50",
    "科创100",
    "科创综指",
    "红利",
)

_BOARD_CACHE: tuple[float, list[dict[str, object]]] | None = None
_BOARD_TTL = 120.0
_ETF_UNIVERSE_CACHE: tuple[float, list[dict[str, object]]] | None = None
_ETF_UNIVERSE_TTL = 90.0
_OTC_RANK_CACHE: tuple[float, list[dict[str, object]]] | None = None
_OTC_RANK_TTL = 120.0


@dataclass
class FundBoardItem:
    id: str
    name: str
    section: str
    price: float | None = None
    change_pct: float | None = None
    prev: float | None = None
    unit: str = "元"
    freshness: str = ""
    note: str = ""
    holdable: bool = True
    symbol: str = ""
    market: str = ""
    kind: str = "etf"  # etf | otc
    chart: list[float] = field(default_factory=list)
    chart_times: list[str] = field(default_factory=list)
    chart_slots: int = 0
    chart_session: str = "cn"


@dataclass
class FundBoardGroup:
    id: str
    title: str
    items: list[FundBoardItem] = field(default_factory=list)


@dataclass
class FundBoardSection:
    id: str
    title: str
    subtitle: str = ""
    items: list[FundBoardItem] = field(default_factory=list)
    groups: list[FundBoardGroup] = field(default_factory=list)


@dataclass
class FundBoard:
    sections: list[FundBoardSection]
    note: str = ""


@dataclass
class FundSearchHit:
    symbol: str
    name: str
    market: str = "OF"
    kind: str = "otc"
    price: float | None = None
    change_pct: float | None = None
    as_of: str = ""
    fund_type: str = ""


def _parse_float(raw: object) -> float | None:
    if raw is None or raw == "" or raw == "--" or raw == "-":
        return None
    try:
        return float(str(raw).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _market_from_f13(f13: object, symbol: str) -> str:
    try:
        n = int(f13)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = -1
    if n in (1, 17):
        return "SH"
    if n in (0, 2):
        return "SZ"
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _clist_get(
    *,
    fs: str,
    fid: str = "f6",
    po: str = "1",
    pn: int = 1,
    pz: int = 100,
) -> tuple[int, list[dict[str, object]]]:
    params = {
        "pn": pn,
        "pz": min(100, max(1, pz)),
        "po": po,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": fid,
        "fs": fs,
        "fields": _CLIST_FIELDS,
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
    }
    last_exc: Exception | None = None
    for url in clist_urls():
        try:
            with httpx.Client(timeout=12.0, headers=EM_HEADERS) as client:
                res = client.get(url, params=params)
                res.raise_for_status()
                data = res.json().get("data") or {}
                total = int(data.get("total") or 0)
                diff = data.get("diff") or []
                rows = [x for x in diff if isinstance(x, dict)]
                return total, rows
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        logger.warning("fund clist failed fs=%s: %s", fs, last_exc)
    return 0, []


def _fetch_etf_universe() -> list[dict[str, object]]:
    """全市场 ETF 快照（分页），供宽基过滤 / 行业名匹配。"""
    global _ETF_UNIVERSE_CACHE
    now = time.time()
    if _ETF_UNIVERSE_CACHE and now - _ETF_UNIVERSE_CACHE[0] < _ETF_UNIVERSE_TTL:
        return _ETF_UNIVERSE_CACHE[1]

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    total, first = _clist_get(fs=_ETF_FS, fid="f6", pn=1, pz=100)
    for row in first:
        sym = str(row.get("f12") or "")
        if sym and sym not in seen:
            seen.add(sym)
            out.append(row)
    pages = max(1, min(20, (total + 99) // 100)) if total else 1
    for pn in range(2, pages + 1):
        time.sleep(0.08)
        _, rows = _clist_get(fs=_ETF_FS, fid="f6", pn=pn, pz=100)
        if not rows:
            break
        for row in rows:
            sym = str(row.get("f12") or "")
            if sym and sym not in seen:
                seen.add(sym)
                out.append(row)
    _ETF_UNIVERSE_CACHE = (now, out)
    return out


def _fetch_industry_boards() -> list[dict[str, object]]:
    global _BOARD_CACHE
    now = time.time()
    if _BOARD_CACHE and now - _BOARD_CACHE[0] < _BOARD_TTL:
        return _BOARD_CACHE[1]

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    total, first = _clist_get(fs=_INDUSTRY_FS, fid="f3", pn=1, pz=100)
    for row in first:
        code = str(row.get("f12") or "")
        if code and code not in seen:
            seen.add(code)
            out.append(row)
    pages = max(1, min(6, (total + 99) // 100)) if total else 1
    for pn in range(2, pages + 1):
        time.sleep(0.08)
        _, rows = _clist_get(fs=_INDUSTRY_FS, fid="f3", pn=pn, pz=100)
        if not rows:
            break
        for row in rows:
            code = str(row.get("f12") or "")
            if code and code not in seen:
                seen.add(code)
                out.append(row)
    _BOARD_CACHE = (now, out)
    return out


def _fetch_cross_etfs(limit: int = _LIST_LIMIT) -> list[dict[str, object]]:
    _, rows = _clist_get(fs=_CROSS_FS, fid="f6", pn=1, pz=min(100, limit))
    return rows[:limit]


def _fetch_otc_rank(limit: int = _LIST_LIMIT) -> list[dict[str, object]]:
    """天天基金排行 — 场外开放式，日涨跌。"""
    global _OTC_RANK_CACHE
    now = time.time()
    if _OTC_RANK_CACHE and now - _OTC_RANK_CACHE[0] < _OTC_RANK_TTL:
        return _OTC_RANK_CACHE[1][:limit]

    url = (
        "https://fund.eastmoney.com/data/rankhandler.aspx"
        f"?op=ph&dt=kf&ft=all&rs=&gs=0&sc=zzf&st=desc&pi=1&pn={min(100, max(1, limit))}&dx=1"
    )
    out: list[dict[str, object]] = []
    try:
        with httpx.Client(timeout=15.0, headers=_FUND_HEADERS, follow_redirects=True) as client:
            res = client.get(url)
            res.raise_for_status()
            text = res.text or ""
        m = re.search(r"datas:\[(.*?)\]", text, re.S)
        if not m:
            return []
        chunks = re.findall(r'"([^"]*)"', m.group(1))
        for chunk in chunks:
            parts = chunk.split(",")
            if len(parts) < 7:
                continue
            code = parts[0].strip()
            name = parts[1].strip()
            as_of = parts[3].strip()
            nav = _parse_float(parts[4])
            # rankhandler: …,日涨幅,周涨幅,…（sc=zzf 按周涨幅排序）
            day_chg = _parse_float(parts[6])
            if not code or not name:
                continue
            out.append(
                {
                    "symbol": code,
                    "name": name,
                    "nav": nav,
                    "change_pct": day_chg,
                    "as_of": as_of,
                }
            )
        _OTC_RANK_CACHE = (now, out)
    except Exception:
        logger.exception("otc rankhandler failed")
    return out[:limit]


def _row_amount(row: dict[str, object]) -> float:
    return em_float(row.get("f6")) or 0.0


def _item_from_etf_row(row: dict[str, object], section: str) -> FundBoardItem | None:
    sym = str(row.get("f12") or "").strip()
    if not sym:
        return None
    mkt = _market_from_f13(row.get("f13"), sym)
    name = str(row.get("f14") or sym).strip() or sym
    price = em_float(row.get("f2"))
    prev = em_float(row.get("f18"))
    if price is None or price <= 0:
        price = prev
    return FundBoardItem(
        id=f"{mkt}-{sym}",
        name=name,
        section=section,
        price=price if price and price > 0 else None,
        change_pct=em_float(row.get("f3")),
        prev=prev,
        unit="元",
        freshness="",
        note=f"{mkt}{sym}",
        holdable=True,
        symbol=sym,
        market=mkt,
        kind="etf",
        chart_session="cn",
    )


def _item_from_otc_row(row: dict[str, object]) -> FundBoardItem:
    sym = str(row.get("symbol") or "")
    name = str(row.get("name") or sym)
    as_of = str(row.get("as_of") or "")
    nav = row.get("nav") if isinstance(row.get("nav"), (int, float)) else None
    chg = row.get("change_pct") if isinstance(row.get("change_pct"), (int, float)) else None
    price = float(nav) if nav and float(nav) > 0 else None
    change_pct = float(chg) if chg is not None else None
    # 昨净值：由当日净值与日涨跌幅反推，供抬头涨跌额
    prev: float | None = None
    if price is not None and change_pct is not None and change_pct > -100:
        denom = 1.0 + change_pct / 100.0
        if denom > 0:
            prev = round(price / denom, 4)
    return FundBoardItem(
        id=f"OF-{sym}",
        name=name,
        section="otc",
        price=price,
        change_pct=change_pct,
        prev=prev,
        unit="净值",
        freshness=as_of,
        note=f"日净值{(' · ' + as_of) if as_of else ''}",
        holdable=True,
        symbol=sym,
        market="OF",
        kind="otc",
        chart_session="cn",
    )


def _board_match_key(title: str) -> str:
    t = (title or "").strip()
    for s in ("Ⅱ", "Ⅲ", "行业", "板块", "概念"):
        t = t.replace(s, "")
    return t.strip()


def _filter_etfs_by_keys(
    rows: list[dict[str, object]], keys: tuple[str, ...], limit: int
) -> list[dict[str, object]]:
    hits = [
        r
        for r in rows
        if any(k in str(r.get("f14") or "") for k in keys)
    ]
    hits.sort(key=_row_amount, reverse=True)
    return hits[:limit]


def _filter_etfs_by_board_name(
    rows: list[dict[str, object]], board_title: str, limit: int
) -> list[dict[str, object]]:
    key = _board_match_key(board_title)
    if len(key) < 2:
        return []
    full = [r for r in rows if key in str(r.get("f14") or "")]
    pool = full if full else [r for r in rows if key[:2] in str(r.get("f14") or "")]
    pool.sort(key=_row_amount, reverse=True)
    return pool[:limit]


def fetch_etf_daily_history(
    symbol: str, market: str, days: int = 30
) -> tuple[str, list[tuple[str, float]]]:
    """场内 ETF 日 K 收盘价序列（按日趋势）。"""
    name, bars = get_daily_klines(symbol, market, limit=days)
    points = [(b.date, float(b.close)) for b in bars if b.close and b.close > 0]
    return name or symbol, points


def _nav_from_lsjz(client: httpx.Client, code: str, name: str = "") -> dict[str, object] | None:
    """Latest daily NAV via history API (no GSZ valuation)."""
    try:
        res = client.get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": code, "pageIndex": 1, "pageSize": 2},
        )
        res.raise_for_status()
        data = res.json()
        rows = ((data.get("Data") or {}) if isinstance(data, dict) else {}).get("LSJZList") or []
        if not isinstance(rows, list) or not rows:
            return None
        latest = rows[0] if isinstance(rows[0], dict) else None
        if not latest:
            return None
        nav = _parse_float(latest.get("DWJZ"))
        chg = _parse_float(latest.get("JZZZL"))
        as_of = str(latest.get("FSRQ") or "").strip()
        if nav is None:
            return None
        display = (name or "").strip()
        return {
            "name": display if display and display != code else code,
            "nav": nav,
            "change_pct": chg,
            "as_of": as_of,
        }
    except Exception:
        logger.exception("lsjz nav failed for %s", code)
        return None


def fetch_otc_nav_batch(codes: list[str]) -> dict[str, dict[str, object]]:
    """Daily NAV only (NAV / PDATE / NAVCHGRT) — ignore intraday GSZ valuation."""
    now = time.time()
    out: dict[str, dict[str, object]] = {}
    missing: list[str] = []
    for code in codes:
        hit = _NAV_CACHE.get(code)
        if hit and now - hit[0] < _NAV_TTL:
            out[code] = hit[1]
        else:
            missing.append(code)
    if not missing:
        return out

    try:
        with httpx.Client(timeout=12.0, headers=_FUND_HEADERS, follow_redirects=True) as client:
            for i in range(0, len(missing), 20):
                chunk = missing[i : i + 20]
                rows: list = []
                try:
                    res = client.get(
                        "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFInfo",
                        params={
                            "plat": "Android",
                            "deviceid": "anzai-etf",
                            "product": "EFund",
                            "Version": "6.2.4",
                            "Fcodes": ",".join(chunk),
                            "appType": "ttjj",
                        },
                    )
                    res.raise_for_status()
                    data = res.json()
                    if data.get("Success") and isinstance(data.get("Datas"), list):
                        rows = data["Datas"]
                except Exception:
                    logger.exception("otc fund nav batch request failed")
                    rows = []

                got: set[str] = set()
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    code = str(row.get("FCODE") or "").strip()
                    if not code:
                        continue
                    payload = {
                        "name": str(row.get("SHORTNAME") or code),
                        "nav": _parse_float(row.get("NAV")),
                        "change_pct": _parse_float(row.get("NAVCHGRT")),
                        "as_of": str(row.get("PDATE") or "").strip(),
                    }
                    if payload["nav"] is None:
                        continue
                    _NAV_CACHE[code] = (now, payload)
                    out[code] = payload
                    got.add(code)

                # Fallback: per-code daily history when batch busy / empty
                for code in chunk:
                    if code in got:
                        continue
                    payload = _nav_from_lsjz(client, code)
                    if payload:
                        _NAV_CACHE[code] = (now, payload)
                        out[code] = payload
    except Exception:
        logger.exception("otc fund nav batch failed")

    return out


def fetch_otc_nav_history(code: str, days: int = 30) -> list[tuple[str, float]]:
    """Ascending daily NAV points (date, nav) for sparkline — not realtime.

    东财 lsjz 单页最多约 20 条，需翻页才能凑满 days 个净值日。
    """
    code = (code or "").strip()
    if not code:
        return []
    days = max(5, min(int(days or 30), 90))
    now = time.time()
    cached = _HIST_CACHE.get(code)
    if cached and now - cached[0] < _HIST_TTL and len(cached[1]) >= days:
        return cached[1][-days:]

    try:
        page_size = 20  # API hard-caps ~20 regardless of pageSize param
        pages_needed = max(1, (days + page_size - 1) // page_size)
        points: list[tuple[str, float]] = []
        with httpx.Client(timeout=12.0, headers=_FUND_HEADERS, follow_redirects=True) as client:
            for page in range(1, pages_needed + 2):  # +1 buffer if gaps
                res = client.get(
                    "https://api.fund.eastmoney.com/f10/lsjz",
                    params={
                        "fundCode": code,
                        "pageIndex": page,
                        "pageSize": page_size,
                    },
                )
                res.raise_for_status()
                data = res.json()
                rows = ((data.get("Data") or {}) if isinstance(data, dict) else {}).get(
                    "LSJZList"
                ) or []
                if not isinstance(rows, list) or not rows:
                    break
                # API newest-first; collect then sort
                batch: list[tuple[str, float]] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    d = str(row.get("FSRQ") or "").strip()[:10]
                    nav = _parse_float(row.get("DWJZ"))
                    if d and nav is not None and nav > 0:
                        batch.append((d, nav))
                if not batch:
                    break
                points.extend(batch)
                if len(points) >= days:
                    break
                if len(rows) < page_size:
                    break
        # chronological, dedupe by date, keep last `days`
        points.sort(key=lambda x: x[0])
        dedup: list[tuple[str, float]] = []
        seen: set[str] = set()
        for d, nav in points:
            if d in seen:
                continue
            seen.add(d)
            dedup.append((d, nav))
        _HIST_CACHE[code] = (now, dedup)
        return dedup[-days:]
    except Exception:
        logger.exception("otc fund history failed for %s", code)
        return []


def _etf_match_score(query: str, symbol: str, name: str) -> int:
    q = (query or "").strip().lower()
    if not q:
        return 0
    sl = (symbol or "").strip().lower()
    nl = (name or "").strip().lower().replace(" ", "")
    if not sl:
        return 0
    if q == sl or q.zfill(6) == sl:
        return 100
    if sl.startswith(q) or q.startswith(sl):
        return 80
    if q in nl:
        return 50
    if q in sl:
        return 40
    return 0


def _search_listed_etfs(keyword: str, limit: int) -> list[FundSearchHit]:
    """Match keyword against Eastmoney ETF universe (场内)."""
    q = (keyword or "").strip()
    if not q:
        return []
    scored: list[tuple[int, float, FundSearchHit]] = []
    for row in _fetch_etf_universe():
        sym = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip() or sym
        score = _etf_match_score(q, sym, name)
        if score <= 0:
            continue
        mkt = _market_from_f13(row.get("f13"), sym)
        price = em_float(row.get("f2"))
        prev = em_float(row.get("f18"))
        if price is None or price <= 0:
            price = prev
        scored.append(
            (
                score,
                _row_amount(row),
                FundSearchHit(
                    symbol=sym,
                    name=name,
                    market=mkt,
                    kind="etf",
                    price=price if price and price > 0 else None,
                    change_pct=em_float(row.get("f3")),
                    as_of="",
                    fund_type="场内ETF",
                ),
            )
        )
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [hit for _, _, hit in scored[:limit]]


def search_otc_funds(keyword: str, limit: int = 20) -> list[FundSearchHit]:
    """Search open-end funds by code/name — daily NAV fields only."""
    q = (keyword or "").strip()
    if len(q) < 1:
        return []
    limit = max(1, min(int(limit or 20), 30))
    now = time.time()
    cache_key = q.lower()
    cached = _SEARCH_CACHE.get(cache_key)
    if cached and now - cached[0] < _SEARCH_TTL:
        return [
            FundSearchHit(
                symbol=str(row.get("symbol") or ""),
                name=str(row.get("name") or ""),
                market=str(row.get("market") or "OF"),
                kind=str(row.get("kind") or "otc"),
                price=_parse_float(row.get("price")),
                change_pct=_parse_float(row.get("change_pct")),
                as_of=str(row.get("as_of") or ""),
                fund_type=str(row.get("fund_type") or ""),
            )
            for row in cached[1][:limit]
            if isinstance(row, dict) and row.get("symbol")
        ]

    hits: list[FundSearchHit] = []
    try:
        with httpx.Client(timeout=10.0, headers=_FUND_HEADERS, follow_redirects=True) as client:
            res = client.get(
                "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx",
                params={"m": 1, "key": q},
            )
            res.raise_for_status()
            data = res.json()
            rows = data.get("Datas") or []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                # CATEGORY 700 = 基金
                cat = row.get("CATEGORY")
                try:
                    if cat is not None and int(cat) != 700:
                        continue
                except (TypeError, ValueError):
                    continue
                code = str(row.get("CODE") or "").strip()
                name = str(row.get("NAME") or "").strip()
                if not code or not name:
                    continue
                base = row.get("FundBaseInfo") if isinstance(row.get("FundBaseInfo"), dict) else {}
                nav = _parse_float(base.get("DWJZ")) if base else None
                # Prefer published NAV change; never use GSZ valuation
                chg = _parse_float(base.get("RZDF")) if base else None
                if chg is None and base:
                    chg = _parse_float(base.get("JZZZL"))
                as_of = str((base or {}).get("FSRQ") or "").strip()
                ftype = str((base or {}).get("FTYPE") or "").strip()
                hits.append(
                    FundSearchHit(
                        symbol=code,
                        name=name,
                        market="OF",
                        kind="otc",
                        price=nav,
                        change_pct=chg,
                        as_of=as_of,
                        fund_type=ftype,
                    )
                )
                if len(hits) >= limit:
                    break
    except Exception:
        logger.exception("otc fund search failed for %s", q)

    _SEARCH_CACHE[cache_key] = (
        now,
        [
            {
                "symbol": h.symbol,
                "name": h.name,
                "market": h.market,
                "kind": h.kind,
                "price": h.price,
                "change_pct": h.change_pct,
                "as_of": h.as_of,
                "fund_type": h.fund_type,
            }
            for h in hits
        ],
    )
    return hits


def search_funds(keyword: str, limit: int = 20) -> list[FundSearchHit]:
    """场内 ETF + 场外开放式，按代码/名称搜索。"""
    q = (keyword or "").strip()
    if len(q) < 1:
        return []
    limit = max(1, min(int(limit or 20), 30))
    # Split budget: prefer listing ETFs first, then OTC
    etf_budget = max(8, limit // 2)
    etf_hits = _search_listed_etfs(q, etf_budget)
    otc_hits = search_otc_funds(q, limit)
    seen: set[tuple[str, str]] = set()
    merged: list[FundSearchHit] = []
    for hit in (*etf_hits, *otc_hits):
        key = (hit.market.upper(), hit.symbol)
        if key in seen:
            continue
        seen.add(key)
        merged.append(hit)
        if len(merged) >= limit:
            break
    return merged


def get_fund_board() -> FundBoard:
    """东财动态：宽基 / 行业 / 跨境 ETF + 场外排行（每类最多 100）。"""
    universe = _fetch_etf_universe()
    boards = _fetch_industry_boards()
    cross_rows = _fetch_cross_etfs(_LIST_LIMIT)
    otc_rows = _fetch_otc_rank(_LIST_LIMIT)

    broad_rows = _filter_etfs_by_keys(universe, _BROAD_NAME_KEYS, _LIST_LIMIT)
    broad_items = [
        it
        for row in broad_rows
        if (it := _item_from_etf_row(row, "broad")) is not None
    ]

    cross_items = [
        it
        for row in cross_rows
        if (it := _item_from_etf_row(row, "theme")) is not None
    ]

    sector_groups: list[FundBoardGroup] = []
    scored: list[tuple[int, float, FundBoardGroup]] = []
    for board in boards:
        code = str(board.get("f12") or "").strip()
        title = str(board.get("f14") or "").strip()
        if not code or not title:
            continue
        matched = _filter_etfs_by_board_name(universe, title, _LIST_LIMIT)
        if not matched:
            continue
        items = [
            it
            for row in matched
            if (it := _item_from_etf_row(row, "sector")) is not None
        ]
        if not items:
            continue
        chg = abs(em_float(board.get("f3")) or 0.0)
        scored.append(
            (
                len(items),
                chg,
                FundBoardGroup(id=code.lower(), title=title, items=items),
            )
        )
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    sector_groups = [g for _, _, g in scored[:_INDUSTRY_GROUP_LIMIT]]
    sector_flat = [it for g in sector_groups for it in g.items]

    otc_items = [_item_from_otc_row(r) for r in otc_rows if r.get("symbol")]

    return FundBoard(
        sections=[
            FundBoardSection(
                id="broad",
                title="宽基",
                subtitle="东财场内指数类 ETF · 成交额排序",
                items=broad_items,
            ),
            FundBoardSection(
                id="sector",
                title="行业",
                subtitle="东财行业板块 · 匹配场内主题 ETF",
                items=sector_flat,
                groups=sector_groups,
            ),
            FundBoardSection(
                id="theme",
                title="跨境",
                subtitle="东财港股通/海外 ETF 板块",
                items=cross_items,
            ),
            FundBoardSection(
                id="otc",
                title="场外",
                subtitle="天天基金开放式排行 · 日净值",
                items=otc_items,
            ),
        ],
        note="列表来自东财动态接口（与股票榜同源）；场外为日净值；可搜场内 ETF 与场外基金",
    )

