"""Intraday (分时) trends via East Money."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "IntradaySeries"]] = {}
_CACHE_TTL = 30.0

# push2 often empty/unstable after close; push2delay keeps the day's series.
_TRENDS_URLS = (
    "https://push2.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2delay.eastmoney.com/api/qt/stock/trends2/get",
    "https://push2his.eastmoney.com/api/qt/stock/trends2/get",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


@dataclass
class IntradayPoint:
    time: str
    price: float
    avg: float | None = None


@dataclass
class IntradaySeries:
    symbol: str
    market: str
    name: str
    prev_close: float | None
    points: list[IntradayPoint]
    session: str = "cn"  # cn | us | hk


def _secid(symbol: str, market: str, override: str | None = None) -> str:
    if override:
        return override
    # East Money: SH=1.*, SZ=0.*
    prefix = "1" if market.upper() == "SH" else "0"
    return f"{prefix}.{symbol}"


def _parse_trends(data: dict) -> tuple[list[IntradayPoint], float | None, str | None]:
    """Parse EM trends2 payload. Row: time,open,close,high,low,vol,amount,avg."""
    trends = data.get("trends") or []
    points: list[IntradayPoint] = []
    for row in trends:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        t = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]
        # Prefer 收盘 (close); fall back to 开盘 if close missing
        close = float(parts[2] or 0) if parts[2] else 0.0
        open_px = float(parts[1] or 0) if parts[1] else 0.0
        price = close if close > 0 else open_px
        if price <= 0:
            continue
        avg = float(parts[7]) if len(parts) > 7 and parts[7] else None
        points.append(IntradayPoint(time=t, price=price, avg=avg))
    prev = data.get("preClose")
    prev_close = float(prev) if prev is not None else None
    name = data.get("name")
    return points, prev_close, str(name) if name else None


def _fetch_em_trends(secid: str) -> dict | None:
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1",
    }
    last_err: Exception | None = None
    with httpx.Client(timeout=8.0, headers=_HEADERS, follow_redirects=True) as client:
        for url in _TRENDS_URLS:
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                trends = data.get("trends") or []
                if trends:
                    return data
                logger.info("Intraday empty from %s for %s", url.split("/")[2], secid)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "Intraday fetch miss %s for %s: %s",
                    url.split("/")[2],
                    secid,
                    exc,
                )
    if last_err is not None:
        logger.exception(
            "Intraday all hosts failed for %s",
            secid,
            exc_info=last_err,
        )
    return None


def get_intraday(
    symbol: str,
    market: str = "SH",
    name: str = "",
    *,
    em_secid: str | None = None,
    session: str = "cn",
) -> IntradaySeries:
    sym = symbol.strip()
    mkt = market.upper()
    if session == "us" or mkt == "US":
        sess = "us"
    elif session == "hk" or mkt == "HK":
        sess = "hk"
    else:
        sess = "cn"
    cache_key = f"{mkt}:{sym}:{em_secid or ''}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    secid = _secid(sym, mkt, em_secid)
    data = _fetch_em_trends(secid)
    if data:
        points, prev_close, em_name = _parse_trends(data)
        series = IntradaySeries(
            symbol=sym,
            market=mkt,
            name=(em_name or name or sym),
            prev_close=prev_close,
            points=points,
            session=sess,
        )
    else:
        # Prefer empty over synthetic sawtooth — chart stays blank, hero quote still live
        logger.warning("Intraday unavailable for %s.%s; returning empty series", mkt, sym)
        series = IntradaySeries(
            symbol=sym,
            market=mkt,
            name=name or sym,
            prev_close=None,
            points=[],
            session=sess,
        )

    _CACHE[cache_key] = (now, series)
    return series


def format_intraday_summary(
    symbol: str,
    market: str = "SH",
    name: str = "",
) -> str:
    """Compact Chinese summary for agent tools (not full point dump)."""
    series = get_intraday(symbol, market, name)
    if not series.points:
        return f"{symbol} 暂无分时数据（勿编造分时走势）"
    prices = [p.price for p in series.points]
    first = prices[0]
    last = prices[-1]
    hi = max(prices)
    lo = min(prices)
    prev = series.prev_close
    chg = None
    if prev and prev > 0:
        chg = round((last / prev - 1) * 100, 2)
    # Rough shape: compare last third vs first third
    n = len(prices)
    a = sum(prices[: max(1, n // 3)]) / max(1, n // 3)
    b = sum(prices[-max(1, n // 3) :]) / max(1, n // 3)
    if b > a * 1.003:
        shape = "今天整体往上走"
    elif b < a * 0.997:
        shape = "今天整体往下走"
    else:
        shape = "今天多半在横着晃"
    span = hi - lo
    pos = "—"
    if span > 0:
        pct = (last - lo) / span
        if pct >= 0.75:
            pos = "现在价靠近今天高点"
        elif pct <= 0.25:
            pos = "现在价靠近今天低点"
        else:
            pos = "现在价在今天中间一带"
    label = series.name or name or symbol
    lines = [
        f"【今天盘中 · {label}（{symbol} {series.market}）】",
        (
            f"现在 {last} · 开盘附近 {first} · 最高 {hi} · 最低 {lo}"
            + (f" · 比昨收 {chg:+.2f}%" if chg is not None else "")
        ),
        f"{shape}；{pos}。",
        f"时间：{series.points[0].time} → {series.points[-1].time}",
    ]
    return "\n".join(lines)
