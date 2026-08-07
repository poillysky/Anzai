"""Intraday (分时) trends via East Money — open + close resilient."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.providers.eastmoney import EM_HEADERS, em_float, host_label, trends_urls

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "IntradaySeries"]] = {}
_CACHE_TTL = 30.0


@dataclass
class IntradayPoint:
    time: str
    price: float
    avg: float | None = None
    volume: float | None = None


@dataclass
class IntradaySeries:
    symbol: str
    market: str
    name: str
    prev_close: float | None
    points: list[IntradayPoint]
    session: str = "cn"  # cn | us | hk
    open_price: float | None = None  # 今开


def _secid(symbol: str, market: str, override: str | None = None) -> str:
    if override:
        return override
    mkt = market.upper()
    sym = symbol.strip()
    if mkt == "HK":
        code = sym.zfill(5) if sym.isdigit() else sym
        return f"116.{code}"
    if mkt == "US":
        return f"105.{sym}"
    prefix = "1" if mkt == "SH" else "0"
    return f"{prefix}.{sym}"


def _parse_trends(data: dict) -> tuple[list[IntradayPoint], float | None, str | None, float | None]:
    """Parse EM trends2 payload. Row: time,open,close,high,low,vol,amount,avg."""
    trends = data.get("trends") or []
    points: list[IntradayPoint] = []
    day_open: float | None = None
    for row in trends:
        parts = str(row).split(",")
        if len(parts) < 3:
            continue
        t = parts[0].split(" ")[-1] if " " in parts[0] else parts[0]
        close = em_float(parts[2]) if len(parts) > 2 else None
        open_px = em_float(parts[1]) if len(parts) > 1 else None
        if day_open is None and open_px and open_px > 0:
            day_open = open_px
        price = close if close and close > 0 else (open_px if open_px and open_px > 0 else None)
        if price is None or price <= 0:
            continue
        avg = em_float(parts[7]) if len(parts) > 7 else None
        vol = em_float(parts[5]) if len(parts) > 5 else None
        points.append(IntradayPoint(time=t, price=price, avg=avg, volume=vol))
    prev_close = em_float(data.get("preClose"))
    # Some payloads expose open on the root
    if day_open is None:
        day_open = em_float(data.get("open")) or em_float(data.get("openPrice"))
    name = data.get("name")
    return points, prev_close, str(name) if name else None, day_open


def _fetch_em_trends(secid: str) -> dict | None:
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1",
    }
    last_err: Exception | None = None
    best_empty: dict | None = None
    with httpx.Client(timeout=8.0, headers=EM_HEADERS, follow_redirects=True) as client:
        for url in trends_urls():
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                trends = data.get("trends") or []
                if trends:
                    return data
                # Keep a payload that at least has preClose (pre-open / after-hours edge)
                if data and (data.get("preClose") is not None or data.get("name")):
                    best_empty = data
                logger.info("Intraday empty from %s for %s", host_label(url), secid)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "Intraday fetch miss %s for %s: %s",
                    host_label(url),
                    secid,
                    type(exc).__name__,
                )
    if best_empty is not None:
        return best_empty
    if last_err is not None:
        logger.warning("Intraday all hosts failed for %s: %s", secid, type(last_err).__name__)
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
        points, prev_close, em_name, day_open = _parse_trends(data)
        # Pre-open: no trends yet — seed one flat point at 昨收 so chart isn't blank
        if not points and prev_close and prev_close > 0:
            points = [IntradayPoint(time="09:30", price=prev_close, avg=prev_close)]
            if day_open is None:
                day_open = prev_close
        series = IntradaySeries(
            symbol=sym,
            market=mkt,
            name=(em_name or name or sym),
            prev_close=prev_close,
            points=points,
            session=sess,
            open_price=day_open,
        )
    else:
        logger.warning("Intraday unavailable for %s.%s; returning empty series", mkt, sym)
        series = IntradaySeries(
            symbol=sym,
            market=mkt,
            name=name or sym,
            prev_close=None,
            points=[],
            session=sess,
            open_price=None,
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
