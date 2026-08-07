"""Quote provider — Sina realtime; mock only when QUOTE_PROVIDER=mock (never silent)."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "Quote"]] = {}
_CACHE_TTL = 15.0

# Ambiguous bare codes that are indices on a specific market (not the SZ stock).
_INDEX_DEFAULT_MARKET: dict[str, str] = {
    "000001": "SH",  # 上证指数；平安银行是 SZ:000001，须显式传 SZ
    "000016": "SH",  # 上证50
    "000300": "SH",  # 沪深300
    "000688": "SH",  # 科创50
    "000905": "SH",  # 中证500
    "399001": "SZ",  # 深证成指
    "399006": "SZ",  # 创业板指
    "399673": "SZ",  # 创业板50
}


@dataclass
class Quote:
    symbol: str
    name: str
    market: str
    price: float
    change_pct: float | None = None
    prev_close: float | None = None
    as_of: str | None = None  # e.g. "2026-08-05 15:00:03"
    live: bool = True


def normalize_symbol(symbol: str, market: str | None = None) -> tuple[str, str]:
    s = symbol.strip()
    m_in = (market or "").strip().upper()
    # 积存金：catalog id（zs-jcj）或 JD sku，不走 A 股推断
    if m_in == "JD":
        return s.lower(), "JD"
    # 上金所现货参考（AU9999），不可入仓但可分析
    if m_in == "GDS" or s.upper() in {"AU9999", "GDS_AU9999"}:
        return "AU9999", "GDS"
    # 场外开放式：六位基金代码，勿按 A 股规则改市场
    if m_in == "OF":
        code = re.sub(r"^(OF|SH|SZ)", "", s, flags=re.I).strip()
        return code, "OF"
    s = s.upper()
    s = re.sub(r"^(SH|SZ|HK|US|JD|OF|GDS)", "", s)
    if market:
        m = market.upper()
    elif s in _INDEX_DEFAULT_MARKET:
        m = _INDEX_DEFAULT_MARKET[s]
    elif re.fullmatch(r"\d{5}", s):
        # 五位代码按港股（00700）；A 股为六位
        m = "HK"
    elif s.startswith(("5", "6", "9")):
        m = "SH"
    else:
        m = "SZ"
    if m == "HK" and s.isdigit():
        s = s.zfill(5)
    return s, m


def _sina_code(symbol: str, market: str) -> str:
    if market == "HK":
        # 00700 -> hk00700
        return f"hk{symbol.zfill(5) if symbol.isdigit() else symbol.lower()}"
    prefix = "sh" if market == "SH" else "sz"
    return f"{prefix}{symbol}"


def _parse_as_of(parts: list[str]) -> str | None:
    """Sina A/HK lines usually put date/time near the end."""
    date = ""
    tod = ""
    for p in parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
            date = p
        elif re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", p):
            tod = p
    if date and tod:
        return f"{date} {tod}"
    return date or tod or None


def _empty_quote(symbol: str, market: str, name: str = "") -> Quote:
    return Quote(
        symbol=symbol,
        name=name or symbol,
        market=market,
        price=0.0,
        change_pct=None,
        prev_close=None,
        as_of=None,
        live=False,
    )


def _parse_sina_a(line: str) -> Quote | None:
    if "hq_str_" not in line or '=""' in line or '="' not in line:
        return None
    try:
        code_part, payload = line.split("=", 1)
        code = code_part.split("_")[-1]  # sh510300
        if code.startswith("hk"):
            return None
        market = "SH" if code.startswith("sh") else "SZ"
        symbol = code[2:]
        raw = payload.strip().strip(";").strip('"')
        parts = raw.split(",")
        if len(parts) < 4:
            return None
        name = parts[0]
        prev_close = float(parts[2] or 0)
        price = float(parts[3] or 0)
        # Pre-open: Sina often leaves price=0 on SZ indices — fall back to prev close
        # then recompute change (0.00% flat) so UI doesn't show "--".
        if price <= 0 and prev_close > 0:
            price = prev_close
        change_pct = None
        if prev_close > 0 and price > 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        if price <= 0:
            return _empty_quote(symbol, market, name)
        return Quote(
            symbol=symbol,
            name=name,
            market=market,
            price=price,
            change_pct=change_pct,
            prev_close=prev_close or None,
            as_of=_parse_as_of(parts),
            live=True,
        )
    except Exception:
        logger.exception("Failed parsing sina A-share line: %s", line)
        return None


def _parse_sina_hk(line: str) -> Quote | None:
    """Sina hkXXXX: eng,name,open,prev,high,low,price,change,pct,..."""
    if "hq_str_hk" not in line or '="' not in line:
        return None
    try:
        code_part, payload = line.split("=", 1)
        code = code_part.split("hq_str_")[-1]  # hk00700
        if not code.startswith("hk"):
            return None
        symbol = code[2:]
        raw = payload.strip().strip(";").strip('"')
        if not raw:
            return None
        parts = raw.split(",")
        if len(parts) < 9:
            return None
        name = parts[1] or parts[0] or symbol
        prev_close = float(parts[3] or 0)
        price = float(parts[6] or 0)
        change_pct = float(parts[8] or 0) if parts[8] else None
        if price <= 0 and prev_close > 0:
            price = prev_close
        if change_pct is None and prev_close > 0 and price > 0:
            change_pct = round((price - prev_close) / prev_close * 100, 2)
        if price <= 0:
            return _empty_quote(symbol, "HK", name)
        return Quote(
            symbol=symbol,
            name=name,
            market="HK",
            price=price,
            change_pct=round(change_pct, 2) if change_pct is not None else None,
            prev_close=prev_close or None,
            as_of=_parse_as_of(parts),
            live=True,
        )
    except Exception:
        logger.exception("Failed parsing sina HK line: %s", line)
        return None


def _fetch_sina(symbols: list[tuple[str, str]]) -> dict[str, Quote]:
    if not symbols:
        return {}
    codes = ",".join(_sina_code(s, m) for s, m in symbols)
    url = f"https://hq.sinajs.cn/list={codes}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=8.0, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        raw_bytes = resp.content
        for enc in ("gbk", "utf-8"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                text = raw_bytes.decode("gbk", errors="ignore")

    result: dict[str, Quote] = {}
    for line in text.strip().splitlines():
        q = _parse_sina_hk(line) if "hq_str_hk" in line else _parse_sina_a(line)
        if q:
            result[q.symbol] = q
    return result


def _mock_quote(symbol: str, market: str) -> Quote:
    """Deterministic placeholder — only when QUOTE_PROVIDER=mock."""
    index_names = {
        "000001": "上证指数",
        "399001": "深证成指",
        "399006": "创业板指",
        "IXIC": "纳斯达克",
    }
    seed = sum(ord(c) for c in symbol) % 100
    if symbol in ("000001", "399001", "399006"):
        price = 3000.0 + seed * 3.5
    elif symbol == "IXIC":
        price = 20000.0 + seed * 20
    else:
        price = 1.0 + seed / 10
    return Quote(
        symbol=symbol,
        name=index_names.get(symbol, f"ETF{symbol}"),
        market=market,
        price=round(price, 2 if market == "US" or symbol in index_names else 3),
        change_pct=round((seed - 50) / 50, 2),
        prev_close=round(price * 0.99, 2 if market == "US" or symbol in index_names else 3),
        as_of="mock",
        live=False,
    )


def fetch_sina_int(codes: list[tuple[str, str, str]]) -> dict[str, Quote]:
    """Fetch Sina international indices.

    codes: list of (our_symbol, sina_code, display_name)
    e.g. ("IXIC", "int_nasdaq", "纳斯达克")
    Response: name,price,change,change_pct
    """
    if not codes:
        return {}
    url = f"https://hq.sinajs.cn/list={','.join(c for _, c, _ in codes)}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    result: dict[str, Quote] = {}
    try:
        with httpx.Client(timeout=8.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw_bytes = resp.content
            for enc in ("gbk", "utf-8"):
                try:
                    text = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = raw_bytes.decode("gbk", errors="ignore")
        by_code = {sina: (sym, name) for sym, sina, name in codes}
        for line in text.strip().splitlines():
            if "hq_str_" not in line or '="' not in line:
                continue
            try:
                code_part, payload = line.split("=", 1)
                sina_code = code_part.split("hq_str_")[-1]
                raw = payload.strip().strip(";").strip('"')
                if not raw or sina_code not in by_code:
                    continue
                parts = raw.split(",")
                if len(parts) < 4:
                    continue
                sym, fallback = by_code[sina_code]
                price = float(parts[1] or 0)
                change_pct = float(parts[3] or 0)
                change = float(parts[2] or 0)
                prev = price - change if price else None
                result[sym] = Quote(
                    symbol=sym,
                    name=fallback,
                    market="US",  # caller may overwrite (HK / US)
                    price=price,
                    change_pct=round(change_pct, 2),
                    prev_close=round(prev, 2) if prev else None,
                    as_of=_parse_as_of(parts),
                    live=price > 0,
                )
            except Exception:
                logger.exception("Failed parsing sina int line: %s", line)
    except Exception:
        logger.exception("Sina int quote fetch failed")
    return result


def get_quotes(items: list[tuple[str, str]]) -> dict[str, Quote]:
    """items: list of (symbol, market). Returns map by symbol and market:symbol.

    Never silently injects fake prices when sina fails — returns live=False / price=0.
    Mock numbers only if QUOTE_PROVIDER=mock.
    """
    now = time.time()
    use_mock = (get_settings().quote_provider or "sina").strip().lower() == "mock"
    pending: list[tuple[str, str]] = []
    pending_jd: list[str] = []
    pending_of: list[str] = []
    pending_gds: list[str] = []
    out: dict[str, Quote] = {}

    def _put(sym: str, mkt: str, q: Quote) -> None:
        out[sym] = q
        out[f"{mkt}:{sym}"] = q

    for symbol, market in items:
        sym, mkt = normalize_symbol(symbol, market)
        if use_mock:
            _put(sym, mkt, _mock_quote(sym, mkt))
            continue
        if mkt == "JD":
            cache_key = f"JD:{sym}"
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL:
                _put(sym, mkt, cached[1])
            else:
                pending_jd.append(sym)
            continue
        if mkt == "GDS":
            cache_key = f"GDS:{sym}"
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL:
                _put(sym, mkt, cached[1])
            else:
                pending_gds.append(sym)
            continue
        if mkt == "OF":
            cache_key = f"OF:{sym}"
            cached = _CACHE.get(cache_key)
            if cached and now - cached[0] < _CACHE_TTL:
                _put(sym, mkt, cached[1])
            else:
                pending_of.append(sym)
            continue
        # Unsupported markets for sina A/HK batch (US uses other helpers)
        if mkt not in ("SH", "SZ", "HK"):
            _put(sym, mkt, _empty_quote(sym, mkt))
            continue
        cache_key = f"{mkt}:{sym}"
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            _put(sym, mkt, cached[1])
        else:
            pending.append((sym, mkt))

    if pending:
        fetched: dict[str, Quote] = {}
        try:
            fetched = _fetch_sina(pending)
        except Exception:
            logger.exception("Quote fetch failed (no mock fallback)")

        for sym, mkt in pending:
            q = fetched.get(sym)
            if q is None or not q.live or q.price <= 0:
                q = _empty_quote(sym, mkt, (q.name if q else "") or sym)
                logger.warning("No live quote for %s:%s", mkt, sym)
            _CACHE[f"{mkt}:{sym}"] = (now, q)
            _put(sym, mkt, q)

    if pending_jd:
        try:
            from app.providers.gold import get_jd_holding_quotes

            jd_fetched = get_jd_holding_quotes(pending_jd)
        except Exception:
            logger.exception("JD gold quote fetch failed")
            jd_fetched = {}
        for sym in pending_jd:
            q = jd_fetched.get(sym)
            if q is None or not q.live or q.price <= 0:
                q = _empty_quote(sym, "JD", (q.name if q else "") or sym)
                logger.warning("No live quote for JD:%s", sym)
            _CACHE[f"JD:{sym}"] = (now, q)
            _put(sym, "JD", q)

    if pending_gds:
        try:
            from app.providers.gold import get_gds_holding_quotes

            gds_fetched = get_gds_holding_quotes(pending_gds)
        except Exception:
            logger.exception("GDS AU9999 quote fetch failed")
            gds_fetched = {}
        for sym in pending_gds:
            q = gds_fetched.get(sym) or gds_fetched.get("AU9999")
            if q is None or not q.live or q.price <= 0:
                q = _empty_quote(sym, "GDS", "AU9999")
                logger.warning("No live quote for GDS:%s", sym)
            _CACHE[f"GDS:{sym}"] = (now, q)
            _put(sym, "GDS", q)

    if pending_of:
        try:
            from app.providers.fund import fetch_otc_nav_batch

            nav_map = fetch_otc_nav_batch(list(dict.fromkeys(pending_of)))
        except Exception:
            logger.exception("OF fund NAV quote fetch failed")
            nav_map = {}
        for sym in pending_of:
            nav = nav_map.get(sym) or {}
            price = nav.get("nav") if isinstance(nav.get("nav"), (int, float)) else None
            chg = nav.get("change_pct") if isinstance(nav.get("change_pct"), (int, float)) else None
            as_of = str(nav.get("as_of") or "") or None
            name = str(nav.get("name") or "").strip() or sym
            prev = None
            if (
                isinstance(price, (int, float))
                and price > 0
                and isinstance(chg, (int, float))
            ):
                # 由日涨跌反推昨净值，供仓库「今日盈亏」
                try:
                    prev = float(price) / (1.0 + float(chg) / 100.0)
                except ZeroDivisionError:
                    prev = None
            if price is None or float(price) <= 0:
                q = _empty_quote(sym, "OF", name)
                logger.warning("No NAV quote for OF:%s", sym)
            else:
                q = Quote(
                    symbol=sym,
                    name=name,
                    market="OF",
                    price=float(price),
                    change_pct=float(chg) if chg is not None else None,
                    prev_close=round(prev, 4) if prev and prev > 0 else None,
                    as_of=as_of,
                    live=True,
                )
            _CACHE[f"OF:{sym}"] = (now, q)
            _put(sym, "OF", q)

    return out


def get_quote(symbol: str, market: str = "SH") -> Quote:
    sym, mkt = normalize_symbol(symbol, market)
    return get_quotes([(sym, mkt)])[sym]


def provider_name() -> str:
    return get_settings().quote_provider
