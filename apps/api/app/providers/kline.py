"""Daily K-line — Sina primary, East Money fallback."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, tuple[str, list["KlineBar"]]]] = {}
_CACHE_TTL = 120.0

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.sina.com.cn",
}


@dataclass
class KlineBar:
    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float = 0.0
    change_pct: float | None = None


def _sina_symbol(symbol: str, market: str) -> str:
    mkt = market.upper()
    prefix = "sh" if mkt == "SH" else "sz"
    return f"{prefix}{symbol.strip().lower()}"


def _with_change_pct(bars: list[KlineBar]) -> list[KlineBar]:
    out: list[KlineBar] = []
    prev_close: float | None = None
    for b in bars:
        chg = None
        if prev_close and prev_close > 0 and b.close > 0:
            chg = round((b.close / prev_close - 1) * 100, 2)
        out.append(
            KlineBar(
                date=b.date,
                open=b.open,
                close=b.close,
                high=b.high,
                low=b.low,
                volume=b.volume,
                amount=b.amount,
                change_pct=chg if b.change_pct is None else b.change_pct,
            )
        )
        prev_close = b.close
    return out


def _fetch_sina(symbol: str, market: str, limit: int) -> list[KlineBar]:
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "CN_MarketData.getKLineData"
    )
    params = {
        "symbol": _sina_symbol(symbol, market),
        "scale": "240",  # daily
        "ma": "no",
        "datalen": str(limit),
    }
    with httpx.Client(timeout=12.0, headers=_HEADERS, follow_redirects=True) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        rows = resp.json()
    if not isinstance(rows, list):
        return []
    bars: list[KlineBar] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            close = float(row.get("close") or 0)
            if close <= 0:
                continue
            bars.append(
                KlineBar(
                    date=str(row.get("day") or ""),
                    open=float(row.get("open") or 0),
                    close=close,
                    high=float(row.get("high") or 0),
                    low=float(row.get("low") or 0),
                    volume=float(row.get("volume") or 0),
                )
            )
        except (TypeError, ValueError):
            continue
    return _with_change_pct(bars)


def _fetch_em(symbol: str, market: str, limit: int) -> tuple[str, list[KlineBar]]:
    prefix = "1" if market.upper() == "SH" else "0"
    secid = f"{prefix}.{symbol.strip()}"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": "101",
        "fqt": "1",
        "end": "20500101",
        "lmt": str(limit),
    }
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    urls = (
        "https://push2delay.eastmoney.com/api/qt/stock/kline/get",
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
    )
    with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                raw = data.get("klines") or []
                if not raw:
                    continue
                name = str(data.get("name") or "")
                bars: list[KlineBar] = []
                for row in raw:
                    parts = str(row).split(",")
                    if len(parts) < 7:
                        continue
                    try:
                        close = float(parts[2] or 0)
                        if close <= 0:
                            continue
                        chg = float(parts[8]) if len(parts) > 8 and parts[8] else None
                        bars.append(
                            KlineBar(
                                date=parts[0],
                                open=float(parts[1] or 0),
                                close=close,
                                high=float(parts[3] or 0),
                                low=float(parts[4] or 0),
                                volume=float(parts[5] or 0),
                                amount=float(parts[6] or 0),
                                change_pct=chg,
                            )
                        )
                    except ValueError:
                        continue
                if bars:
                    return name, bars
            except Exception as exc:
                logger.warning("EM kline miss %s %s: %s", url.split("/")[2], secid, exc)
    return "", []


def get_daily_klines(
    symbol: str,
    market: str = "SH",
    *,
    limit: int = 30,
) -> tuple[str, list[KlineBar]]:
    """Return (name, bars oldest→newest)."""
    sym = symbol.strip()
    mkt = market.upper()
    lim = max(5, min(int(limit or 30), 260))
    cache_key = f"{mkt}:{sym}:{lim}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    name = ""
    bars: list[KlineBar] = []
    try:
        bars = _fetch_sina(sym, mkt, lim)
    except Exception as exc:
        logger.warning("Sina kline failed %s:%s: %s", mkt, sym, exc)

    if not bars:
        name, bars = _fetch_em(sym, mkt, lim)

    if not name and bars:
        # best-effort name from quote cache later; leave blank
        name = ""

    result = (name, bars)
    if bars:
        _CACHE[cache_key] = (now, result)
    return result


def _sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return round(sum(closes[-n:]) / n, 4)


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def format_kline_summary(
    symbol: str,
    market: str = "SH",
    *,
    limit: int = 30,
) -> str:
    name, bars = get_daily_klines(symbol, market, limit=limit)
    if not bars:
        return f"{symbol} 暂无日K（勿编造均线/涨跌）"
    if not name:
        try:
            from app.providers.quote import get_quote

            q = get_quote(symbol, market)
            if q and q.name:
                name = q.name
        except Exception:
            pass
    closes = [b.close for b in bars]
    last = bars[-1]
    first = bars[0]
    period_chg = None
    if first.close > 0:
        period_chg = round((last.close / first.close - 1) * 100, 2)
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    label = name or symbol
    lines = [
        f"【最近走势 · {label}（{symbol} {market.upper()}）】近 {len(bars)} 个交易日",
        (
            f"最新 {last.date} 收盘 {last.close} · 开盘 {last.open} · "
            f"最高 {last.high} · 最低 {last.low}"
            + (f" · 当天 {_fmt_pct(last.change_pct)}" if last.change_pct is not None else "")
        ),
        (
            f"这段时间最高 {max(highs):.4g} / 最低 {min(lows):.4g}"
            + (f" · 整段涨跌 {_fmt_pct(period_chg)}" if period_chg is not None else "")
        ),
    ]
    ma_bits = []
    if ma5 is not None:
        ma_bits.append(f"近5日均价约 {ma5}")
    if ma10 is not None:
        ma_bits.append(f"近10日均价约 {ma10}")
    if ma20 is not None:
        ma_bits.append(f"近20日均价约 {ma20}")
    if ma_bits:
        pos = []
        if ma5 is not None:
            pos.append("比5日均价高" if last.close >= ma5 else "比5日均价低")
        if ma20 is not None:
            pos.append("比20日均价高" if last.close >= ma20 else "比20日均价低")
        lines.append("均价参考：" + " · ".join(ma_bits) + ("；现在" + "、".join(pos) if pos else ""))
    tail = bars[-5:]
    lines.append(
        "近5个交易日收盘："
        + " → ".join(f"{b.date[5:]} {b.close}({_fmt_pct(b.change_pct)})" for b in tail)
    )
    return "\n".join(lines)
