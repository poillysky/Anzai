"""Short-horizon bias (~5 min) from EM 1-minute intraday points.

Not a forecast API — classical micro-momentum (ROC + slope + vs short MA).
Label as 短线倾向 / 约5分偏涨跌, never as guaranteed prediction.

Gold day24 (积存金 / AU9999) — three layers (MTFA / 三重滤网):
  L1 macro: 4h/2h 定大趋势方向（偏强/偏弱/震荡）
  L2 near:  原始 tip 斜率+速度抓拐点（含快速下跌/上涨）
  L3 outlook: 只解读 L1×L2 一致性，不做价位预测
"""

from __future__ import annotations

import math
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, time, timezone, timedelta
from typing import Literal

from app.providers.gold import (
    GOLD_ETF_ALIASES,
    jd_sku_by_symbol,
    _fetch_em_au9999_chart,
    _fetch_jd_chart,
)
from app.providers.intraday import IntradayPoint, get_intraday
from app.providers.session import cn_session

BiasKind = Literal["up", "down", "flat", "na", "closed"]

_LOOKBACK = 5
_LOOKBACK_LONG = 10
_MIN_POINTS = 3
_DEADBAND_CN = 0.0008
_DEADBAND_GOLD = 0.0015
_DEADBAND_MAX_MULT = 3.5
_VOL_K = 1.15  # adaptive: max(base, k * σ_ret * √n)
_ROC_FLOOR_RATIO = 0.55
_RANGE_FLAT = 0.0004
_VOL_CONFIRM_RATIO = 0.55  # tip vol vs earlier baseline
_STALE_CN_MIN = 3
_STALE_GDS_MIN = 5
_STALE_JD_SEC = 10 * 60
_OPEN_WATCH_MIN = 5  # minutes after 09:30 / 13:00
_HYST_SEC = 50.0
_MAX_BATCH = 40
_POOL = 8

_CST = timezone(timedelta(hours=8))

_GOLD_ETF_KEYS = {f"{m}:{s}" for s, m, _ in GOLD_ETF_ALIASES}

_GOLD_LABEL_MAP = {
    "约5分偏涨": "金价偏涨",
    "约5分偏跌": "金价偏跌",
    "约5分震荡": "金价震荡",
    "震荡偏涨": "震荡偏涨",
    "震荡偏跌": "震荡偏跌",
    "偏跌抬头": "偏跌抬头",
    "偏涨回落": "偏涨回落",
    "短线暂无": "金价暂无",
    "开盘观察": "金价观察",
    "短线陈旧": "金价陈旧",
}

# sku / key → (monotonic_ts, last_price, chart_n)
_JD_FINGERPRINT: dict[str, tuple[float, float, int]] = {}
# market:symbol → (bias, monotonic_ts, score)
_HYST: dict[str, tuple[BiasKind, float, float]] = {}


def _is_gold_etf(symbol: str, market: str) -> bool:
    return f"{(market or '').upper()}:{(symbol or '').strip()}" in _GOLD_ETF_KEYS


def _is_gold_market(market: str, symbol: str = "") -> bool:
    m = (market or "").upper()
    if m in {"JD", "GDS"}:
        return True
    return _is_gold_etf(symbol, m)


def _base_deadband(market: str, symbol: str = "") -> float:
    return _DEADBAND_GOLD if _is_gold_market(market, symbol) else _DEADBAND_CN


def _closed_bias(symbol: str = "", market: str = "", *, label: str | None = None) -> ShortBias:
    mkt = (market or "").upper()
    if label:
        closed_label = label
    elif mkt == "GDS":
        closed_label = "日盘收盘"
    elif _is_gold_etf(symbol, mkt):
        closed_label = "金ETF收盘"
    else:
        closed_label = "已收盘"
    return ShortBias(
        symbol=symbol,
        market=mkt,
        bias="closed",
        label=closed_label,
        score=None,
        lookback_min=_LOOKBACK,
        sample_n=0,
        roc_pct=None,
        as_of=None,
    )


def _na_bias(symbol: str, market: str, label: str, *, as_of: str | None = None, n: int = 0) -> ShortBias:
    return ShortBias(
        symbol=symbol,
        market=market,
        bias="na",
        label=label,
        score=None,
        lookback_min=_LOOKBACK,
        sample_n=n,
        roc_pct=None,
        as_of=as_of,
    )


def _cn_short_session_open() -> bool:
    return cn_session().state == "trading"


def _now_bj() -> datetime:
    return datetime.now(_CST)


def _au9999_session(now: datetime | None = None) -> tuple[bool, str]:
    now = now or _now_bj()
    if now.weekday() >= 5:
        return False, "上金所休市"
    t = now.time()
    if t >= time(20, 0) or t <= time(2, 30):
        return True, "上金所"
    if time(9, 0) <= t <= time(15, 30):
        return True, "上金所"
    if time(15, 30) < t < time(20, 0):
        return False, "日盘收盘"
    return False, "上金所休市"


def _cn_open_watch(now: datetime | None = None) -> bool:
    """True in first minutes of AM/PM continuous auction — labels only, not closed."""
    now = now or _now_bj()
    if now.weekday() >= 5:
        return False
    t = now.time()
    am0, am1 = time(9, 30), time(9, 30 + _OPEN_WATCH_MIN)
    pm0, pm1 = time(13, 0), time(13, _OPEN_WATCH_MIN)
    return (am0 <= t < am1) or (pm0 <= t < pm1)


def _parse_hhmm(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    s = str(raw).strip()
    if " " in s:
        s = s.split(" ")[-1]
    parts = s.replace("：", ":").split(":")
    if len(parts) < 2:
        return None
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def _minutes_behind(tip_hhmm: str | None, now: datetime | None = None) -> float | None:
    """How many minutes the tip clock lags Shanghai now (same calendar day / wrap)."""
    parsed = _parse_hhmm(tip_hhmm)
    if not parsed:
        return None
    now = now or _now_bj()
    th, tm = parsed
    tip_mins = th * 60 + tm
    now_mins = now.hour * 60 + now.minute
    lag = now_mins - tip_mins
    # overnight gold: tip 23:50, now 00:10 → lag negative large; normalize
    if lag < -12 * 60:
        lag += 24 * 60
    if lag > 12 * 60:
        lag -= 24 * 60
    return float(lag)


@dataclass(frozen=True)
class ShortBias:
    symbol: str
    market: str
    bias: BiasKind
    label: str
    score: float | None
    lookback_min: int
    sample_n: int
    roc_pct: float | None
    as_of: str | None
    # 多周期完整说明（备用）
    detail: str | None = None
    # 卡片上直接展示：①大趋势 ②近端动向 ③可能走向
    summary: str | None = None


def _linreg_slope(ys: list[float]) -> float:
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx = (n - 1) / 2.0
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den > 1e-12 else 0.0


def _stdev(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var) if var > 0 else 0.0


def _sample_even(prices: list[float], n: int) -> list[float]:
    if n <= 0 or not prices:
        return []
    if len(prices) <= n:
        return list(prices)
    if n == 1:
        return [prices[-1]]
    out: list[float] = []
    last_i = len(prices) - 1
    for k in range(n):
        i = round(k * last_i / (n - 1))
        out.append(prices[i])
    return out


def _adaptive_deadband(prices: list[float], base: float) -> float:
    rets: list[float] = []
    for i in range(1, len(prices)):
        a, b = prices[i - 1], prices[i]
        if a > 0 and b > 0:
            rets.append((b - a) / a)
    if len(rets) < 2:
        return base
    sigma = _stdev(rets)
    adaptive = _VOL_K * sigma * math.sqrt(max(len(prices) - 1, 1))
    return min(max(base, adaptive), base * _DEADBAND_MAX_MULT)


def _volume_confirms(points: list[IntradayPoint], lookback: int) -> bool | None:
    """True=ok, False=weak volume, None=no volume data."""
    vols = [p.volume for p in points if p.volume is not None and p.volume >= 0]
    if len(vols) < lookback + 2:
        return None
    tip = vols[-lookback:]
    base = vols[-(lookback * 2) : -lookback] or vols[:-lookback]
    if not tip or not base:
        return None
    tip_m = sum(tip) / len(tip)
    base_m = sum(base) / len(base)
    if base_m <= 1e-9:
        return None
    return tip_m >= base_m * _VOL_CONFIRM_RATIO


def _core_signal(
    prices: list[float],
    *,
    band: float,
) -> tuple[BiasKind, str, float, float]:
    """Return bias, label, score, roc."""
    roc_floor = band * _ROC_FLOOR_RATIO
    first, last = prices[0], prices[-1]
    roc = (last - first) / first if first > 0 else 0.0
    mean = sum(prices) / len(prices)
    vs_ma = (last - mean) / mean if mean > 0 else 0.0
    slope = _linreg_slope(prices)
    slope_total = (slope * (len(prices) - 1) / mean) if mean > 0 else 0.0
    hi, lo = max(prices), min(prices)
    span = (hi - lo) / mean if mean > 0 else 0.0
    score = 0.55 * roc + 0.30 * slope_total + 0.15 * vs_ma

    if span < _RANGE_FLAT:
        return "flat", "约5分震荡", score, roc
    if score > band and roc > roc_floor and slope_total >= -roc_floor * 0.5:
        return "up", "约5分偏涨", score, roc
    if score < -band and roc < -roc_floor and slope_total <= roc_floor * 0.5:
        return "down", "约5分偏跌", score, roc
    return "flat", "约5分震荡", score, roc


def compute_short_bias(
    points: list[IntradayPoint],
    *,
    symbol: str = "",
    market: str = "",
    lookback: int = _LOOKBACK,
    deadband: float | None = None,
    apply_volume: bool = True,
    apply_multi_horizon: bool = True,
) -> ShortBias:
    base = deadband if deadband is not None else _base_deadband(market, symbol)

    if len(points) < _MIN_POINTS:
        return _na_bias(
            symbol,
            market,
            "短线暂无",
            as_of=points[-1].time if points else None,
            n=len(points),
        )

    window = points[-min(max(lookback, _LOOKBACK_LONG), len(points)) :]
    prices_all = [p.price for p in window if p.price and p.price > 0]
    if len(prices_all) < _MIN_POINTS:
        return _na_bias(
            symbol,
            market,
            "短线暂无",
            as_of=window[-1].time if window else None,
            n=len(prices_all),
        )

    prices = prices_all[-lookback:] if len(prices_all) >= lookback else prices_all
    if len(prices) < _MIN_POINTS:
        return _na_bias(symbol, market, "短线暂无", as_of=window[-1].time, n=len(prices))

    band = _adaptive_deadband(prices_all[-max(lookback * 2, len(prices)) :], base)
    bias, label, score, roc = _core_signal(prices, band=band)

    # Multi-horizon: short vs longer must not strongly disagree
    if apply_multi_horizon and len(prices_all) >= _LOOKBACK_LONG and bias in {"up", "down"}:
        long_prices = prices_all[-_LOOKBACK_LONG:]
        long_band = _adaptive_deadband(long_prices, base)
        long_bias, _, _, _ = _core_signal(long_prices, band=long_band)
        if long_bias in {"up", "down"} and long_bias != bias:
            bias, label = "flat", "约5分震荡"

    # Volume: weak tip volume demotes directional → flat
    if apply_volume and bias in {"up", "down"}:
        conf = _volume_confirms(points, lookback)
        if conf is False:
            bias, label = "flat", "约5分震荡"

    return ShortBias(
        symbol=symbol,
        market=market,
        bias=bias,
        label=label,
        score=round(score, 6),
        lookback_min=lookback,
        sample_n=len(prices),
        roc_pct=round(roc * 100, 3),
        as_of=window[-1].time,
    )


def _as_gold_labels(result: ShortBias, *, na_label: str = "金价暂无") -> ShortBias:
    label_map = {
        **_GOLD_LABEL_MAP,
        "短线暂无": na_label,
        "金价暂无": na_label,
    }
    return ShortBias(
        symbol=result.symbol,
        market=result.market,
        bias=result.bias,
        label=label_map.get(result.label, result.label),
        score=result.score,
        lookback_min=result.lookback_min,
        sample_n=result.sample_n,
        roc_pct=result.roc_pct,
        as_of=result.as_of,
        detail=result.detail,
        summary=result.summary,
    )


def _apply_hysteresis(key: str, result: ShortBias) -> ShortBias:
    """Avoid up↔down flicker within a short window."""
    now = time_mod.monotonic()
    prev = _HYST.get(key)
    if prev is None:
        _HYST[key] = (result.bias, now, float(result.score or 0))
        return result
    prev_bias, prev_ts, _prev_score = prev
    age = now - prev_ts
    if (
        age < _HYST_SEC
        and prev_bias in {"up", "down"}
        and result.bias in {"up", "down"}
        and result.bias != prev_bias
    ):
        # Opposing flip too fast → park on flat once
        flat = ShortBias(
            symbol=result.symbol,
            market=result.market,
            bias="flat",
            label="约5分震荡" if not _is_gold_market(result.market, result.symbol) else "金价震荡",
            score=result.score,
            lookback_min=result.lookback_min,
            sample_n=result.sample_n,
            roc_pct=result.roc_pct,
            as_of=result.as_of,
            detail=result.detail,
            summary=result.summary,
        )
        _HYST[key] = ("flat", now, float(result.score or 0))
        return flat
    _HYST[key] = (result.bias, now, float(result.score or 0))
    return result


def _jd_stale(sku: str, chart: list[float]) -> bool:
    if not chart:
        return True
    last = float(chart[-1])
    n = len(chart)
    now = time_mod.monotonic()
    prev = _JD_FINGERPRINT.get(sku)
    _JD_FINGERPRINT[sku] = (now, last, n)
    if prev is None:
        return False
    pts, pl, pn = prev
    if now - pts < _STALE_JD_SEC:
        return False
    # Unchanged length + price for too long while we keep polling
    return pl == last and pn == n


def _last_swing_extreme(prices: list[float], *, kind: str) -> tuple[int, float] | None:
    """Last local high/low with side room — matches 'afternoon peak then fade' on day24."""
    n = len(prices)
    if n < 12:
        return None
    w = max(3, n // 25)
    tip_guard = max(5, n // 12)
    found: list[tuple[int, float]] = []
    for i in range(w, n - tip_guard):
        left = prices[i - w : i]
        right = prices[i + 1 : i + w + 1]
        if not left or not right:
            continue
        px = prices[i]
        if kind == "high" and px >= max(left) and px >= max(right):
            found.append((i, px))
        elif kind == "low" and px <= min(left) and px <= min(right):
            found.append((i, px))
    return found[-1] if found else None


def _tail_slope_frac(prices: list[float]) -> float:
    if len(prices) < 4:
        return 0.0
    tail = prices[-max(4, len(prices) // 3) :]
    mean = sum(tail) / len(tail)
    if mean <= 0:
        return 0.0
    slope = _linreg_slope(tail)
    return slope * (len(tail) - 1) / mean


def _window_metrics(prices: list[float], n: int) -> tuple[float | None, float | None, int]:
    """ROC + tip slope over last n bars. Returns (roc, slope_frac, used_n)."""
    if n <= 0 or len(prices) < _MIN_POINTS:
        return None, None, 0
    w = prices[-min(n, len(prices)) :]
    if len(w) < _MIN_POINTS:
        return None, None, len(w)
    first, last = w[0], w[-1]
    roc = (last - first) / first if first > 0 else 0.0
    return roc, _tail_slope_frac(w), len(w)


def _fmt_roc(roc: float | None) -> str:
    if roc is None:
        return "—"
    return f"{roc * 100:+.2f}%"


def _dir_word(roc: float | None, slope: float | None, band: float) -> str:
    """偏涨 / 偏跌 / 走平 — from ROC with slope as tie-break."""
    if roc is None:
        return "不足"
    if roc >= band:
        return "偏涨"
    if roc <= -band:
        return "偏跌"
    if slope is not None:
        if slope >= band * 0.7:
            return "抬头"
        if slope <= -band * 0.7:
            return "回落"
    return "走平"


def _near_tip_state(prices: list[float], *, band: float) -> tuple[str, float, float, float]:
    """原始 tip：斜率 + 速度。Returns (kind, tip_roc, tip_slope, speed)."""
    if len(prices) < 4:
        return "na", 0.0, 0.0, 0.0

    n = min(12, len(prices))
    tip = prices[-n:]
    mean = sum(tip) / len(tip)
    if mean <= 0:
        return "na", 0.0, 0.0, 0.0

    a3, b3 = tip[-3], tip[-1]
    roc3 = (b3 - a3) / a3 if a3 > 0 else 0.0
    roc_n = (tip[-1] - tip[0]) / tip[0] if tip[0] > 0 else 0.0
    slope = _linreg_slope(tip)
    slope_frac = slope * (len(tip) - 1) / mean

    rets: list[float] = []
    for i in range(1, len(tip)):
        p0, p1 = tip[i - 1], tip[i]
        if p0 > 0:
            rets.append((p1 - p0) / p0)
    sigma = _stdev(rets) if len(rets) >= 2 else 0.0
    noise = max(sigma, band * 0.35, 1e-6)

    tip_move = roc3 if abs(roc3) >= abs(roc_n) * 0.55 else roc_n
    tip_slope = slope_frac
    speed = max(abs(tip_move), abs(tip_slope)) / noise
    signed = tip_move if abs(tip_move) >= abs(tip_slope) * 0.6 else tip_slope
    thr = band * 0.45
    fast_thr = band * 1.2

    if signed <= -thr or tip_slope <= -thr * 0.85:
        kind = "fast_down" if (speed >= 2.2 or abs(signed) >= fast_thr or abs(tip_slope) >= fast_thr) else "down"
        return kind, tip_move, tip_slope, speed
    if signed >= thr or tip_slope >= thr * 0.85:
        kind = "fast_up" if (speed >= 2.2 or abs(signed) >= fast_thr or abs(tip_slope) >= fast_thr) else "up"
        return kind, tip_move, tip_slope, speed
    if tip_slope <= -thr * 0.7:
        return ("fast_down" if speed >= 2.0 else "down"), tip_move, tip_slope, speed
    if tip_slope >= thr * 0.7:
        return ("fast_up" if speed >= 2.0 else "up"), tip_move, tip_slope, speed
    return "flat", tip_move, tip_slope, speed


@dataclass(frozen=True)
class _MacroTrend:
    """层1：大趋势（高周期定方向，不越权看 tip）。"""

    side: BiasKind  # up | down | flat
    text: str
    votes: int
    score: float
    narr_roc: float
    sample_n: int


@dataclass(frozen=True)
class _NearInflection:
    """层2：近端拐点（原始 tip 斜率/速度，禁止抽稀）。"""

    kind: str  # fast_down | down | flat | up | fast_up | na
    text: str
    turn: str  # "" | turn_up | turn_down
    tip_roc: float
    tip_slope: float
    speed: float


def _layer_macro_trend(clean: list[float], slots: int) -> _MacroTrend | None:
    """层1 大趋势：约 4h/2h ROC+斜率，辅以 6h 结构回撤加权。"""
    day = slots if slots > 0 else max(len(clean), 24 * 60)
    n_4h = min(len(clean), max(60, day // 6))
    n_2h = min(len(clean), max(40, day // 12))
    n_6h = min(len(clean), max(72, day // 4))
    mid = clean[-n_6h:]
    if len(mid) < _MIN_POINTS:
        return None

    band = _DEADBAND_GOLD
    last = mid[-1]
    mean = sum(mid) / len(mid)
    roc_6h = (last - mid[0]) / mid[0] if mid[0] > 0 else 0.0
    slope_t = _tail_slope_frac(mid)
    vs_mean = (last - mean) / mean if mean > 0 else 0.0

    roc_4h, sl_4h, _ = _window_metrics(clean, n_4h)
    roc_2h, sl_2h, _ = _window_metrics(clean, n_2h)
    d4 = _dir_word(roc_4h, sl_4h, band)
    d2 = _dir_word(roc_2h, sl_2h, band)

    votes = 0
    for d in (d4, d2):
        if d == "偏涨":
            votes += 1
        elif d == "偏跌":
            votes -= 1
    if roc_6h >= band:
        votes += 1
    elif roc_6h <= -band:
        votes -= 1
    if slope_t >= band * 0.7:
        votes += 1
    elif slope_t <= -band * 0.7:
        votes -= 1
    if vs_mean >= band * 0.45:
        votes += 1
    elif vs_mean <= -band * 0.45:
        votes -= 1

    # 结构回撤只加权，不单独定文案
    tip_guard = max(5, len(mid) // 12)
    swing = _last_swing_extreme(mid, kind="high")
    hi_i = max(range(0, len(mid) - tip_guard), key=lambda i: mid[i])
    peak_px = swing[1] if swing and swing[1] >= mid[hi_i] else mid[hi_i]
    dd = (last - peak_px) / peak_px if peak_px > 0 else None
    swing_lo = _last_swing_extreme(mid, kind="low")
    lo_i = min(range(0, len(mid) - tip_guard), key=lambda i: mid[i])
    trough_px = swing_lo[1] if swing_lo and swing_lo[1] <= mid[lo_i] else mid[lo_i]
    ur = (last - trough_px) / trough_px if trough_px > 0 else None
    if dd is not None and dd <= -band:
        votes -= 1
    if ur is not None and ur >= band:
        votes += 1

    if votes >= 2 or (votes >= 1 and d4 == "偏涨" and d2 != "偏跌"):
        side: BiasKind = "up"
        text = "大趋势偏强"
    elif votes <= -2 or (votes <= -1 and d4 == "偏跌" and d2 != "偏涨"):
        side = "down"
        text = "大趋势偏弱"
    else:
        side = "flat"
        text = "大趋势来回震荡"

    score = 0.4 * roc_6h + 0.35 * slope_t + 0.25 * vs_mean
    if dd is not None and votes < 0:
        score = 0.45 * score + 0.55 * dd
        narr = dd
    elif ur is not None and votes > 0:
        score = 0.45 * score + 0.55 * ur
        narr = ur
    else:
        narr = roc_6h

    return _MacroTrend(
        side=side,
        text=text,
        votes=votes,
        score=float(score),
        narr_roc=float(narr),
        sample_n=len(mid),
    )


def _layer_near_inflection(
    clean: list[float],
    *,
    band: float,
    macro_side: BiasKind,
) -> _NearInflection:
    """层2 近端拐点：原始 tip（约 8–12 根），禁止均匀抽样。"""
    tip_raw = clean[-min(12, len(clean)) :]
    kind, tip_roc, tip_slope, speed = _near_tip_state(tip_raw, band=band)

    if kind == "fast_down":
        text = "近端快速下跌"
    elif kind == "down":
        text = "近端往下走"
    elif kind == "fast_up":
        text = "近端快速上涨"
    elif kind == "up":
        text = "近端往上走"
    elif kind == "flat":
        text = "近端几乎走平"
    else:
        text = "近端方向不明"

    tip_up = kind in {"fast_up", "up"}
    tip_dn = kind in {"fast_down", "down"}
    turn = ""
    if macro_side == "down" and tip_up:
        turn = "turn_up"
    elif macro_side == "up" and tip_dn:
        turn = "turn_down"
    elif macro_side == "flat" and tip_up:
        turn = "turn_up"
    elif macro_side == "flat" and tip_dn:
        turn = "turn_down"

    return _NearInflection(
        kind=kind,
        text=text,
        turn=turn,
        tip_roc=float(tip_roc),
        tip_slope=float(tip_slope),
        speed=float(speed),
    )


def _layer_outlook(macro: _MacroTrend, near: _NearInflection) -> str:
    """层3 综合：只做 L1×L2 一致性解读，不算第三套价位预测。"""
    trend = macro.text
    kind = near.kind
    turn = near.turn

    if kind == "fast_down":
        if trend.endswith("偏强"):
            return "偏强里正在快速回落"
        if turn == "turn_down":
            return "冲高后快速回落"
        return "短线压力偏大"
    if kind == "fast_up":
        if trend.endswith("偏弱"):
            return "偏弱里正在快速反弹"
        if turn == "turn_up":
            return "止跌后快速抬头"
        return "短线动能偏强"
    if turn == "turn_up":
        return "可能止跌抬头"
    if turn == "turn_down":
        return "可能冲高回落"
    if trend.endswith("偏强") and kind == "up":
        return "暂看延续偏强"
    if trend.endswith("偏弱") and kind == "down":
        return "暂看延续偏弱"
    if trend.endswith("偏强") and kind in {"down", "flat"}:
        return "偏强里注意回落"
    if trend.endswith("偏弱") and kind in {"up", "flat"}:
        return "偏弱里留意反弹"
    if trend.endswith("震荡"):
        return "先看能不能走出方向"
    return "方向未明，先观望"


def _analyze_gold_day_chart(
    clean: list[float],
    *,
    symbol: str,
    market: str,
    slots: int,
    as_of: str | None,
) -> ShortBias:
    """金价 day24 偏势：L1 大趋势 → L2 近端拐点 → L3 综合解读（非预测）。

    依据多周期共振 / 三重滤网：每层只做本职，不越权混算。
    """
    macro = _layer_macro_trend(clean, slots)
    if macro is None:
        return _na_bias(symbol, market, "金价暂无", as_of=as_of, n=len(clean))

    near = _layer_near_inflection(clean, band=_DEADBAND_GOLD, macro_side=macro.side)
    outlook = _layer_outlook(macro, near)

    # bias / label：近端优先（急跌勿配全日偏涨色）
    if near.kind in {"fast_down", "down"}:
        bias: BiasKind = "down"
        label = (
            "偏涨回落"
            if near.turn == "turn_down"
            else ("约5分偏跌" if near.kind == "fast_down" else "震荡偏跌")
        )
        narr_roc = near.tip_roc
        score = 0.4 * macro.score + 0.6 * near.tip_roc
    elif near.kind in {"fast_up", "up"}:
        bias = "up"
        label = (
            "偏跌抬头"
            if near.turn == "turn_up"
            else ("约5分偏涨" if near.kind == "fast_up" else "震荡偏涨")
        )
        narr_roc = near.tip_roc
        score = 0.4 * macro.score + 0.6 * near.tip_roc
    elif macro.side == "up":
        bias = "up"
        label = "震荡偏涨"
        narr_roc = macro.narr_roc
        score = macro.score
    elif macro.side == "down":
        bias = "down"
        label = "震荡偏跌"
        narr_roc = macro.narr_roc
        score = macro.score
    else:
        bias = "flat"
        label = "约5分震荡"
        narr_roc = near.tip_roc if abs(near.tip_roc) > abs(macro.narr_roc) * 0.5 else macro.narr_roc
        score = macro.score

    summary = f"{macro.text}\n{near.text}\n{outlook}"
    detail = f"{macro.text} · {near.text} · {outlook} · 非预测"

    return ShortBias(
        symbol=symbol,
        market=market,
        bias=bias,
        label=label,
        score=round(score, 6),
        lookback_min=_LOOKBACK,
        sample_n=macro.sample_n,
        roc_pct=round(narr_roc * 100, 3),
        as_of=as_of,
        detail=detail,
        summary=summary,
    )


def get_jd_short_bias(symbol: str) -> ShortBias:
    sym = (symbol or "").strip().lower()
    sku = jd_sku_by_symbol(sym)
    if not sku:
        return _na_bias(sym, "JD", "积存金")

    _name, chart, slots = _fetch_jd_chart(sku)
    if _jd_stale(sku, chart):
        return _na_bias(sym, "JD", "金价陈旧", n=len(chart))

    clean = [float(p) for p in chart if p and p > 0]
    if len(clean) < _MIN_POINTS:
        return _na_bias(sym, "JD", "积存金", n=len(clean))

    raw = _analyze_gold_day_chart(
        clean,
        symbol=sym,
        market="JD",
        slots=slots,
        as_of=str(len(clean) - 1),
    )
    return _apply_hysteresis(f"JD:{sym}", _as_gold_labels(raw, na_label="积存金"))


def get_gds_short_bias(symbol: str = "AU9999") -> ShortBias:
    sym = (symbol or "AU9999").strip().upper() or "AU9999"
    open_, closed_label = _au9999_session()
    if not open_:
        return _closed_bias(sym, "GDS", label=closed_label)

    prices, times, slots = _fetch_em_au9999_chart()
    n = min(len(prices), len(times)) if times else len(prices)
    if n < _MIN_POINTS:
        return _na_bias(sym, "GDS", "金价暂无", n=n)

    tip_t = times[n - 1] if times and n else None
    lag = _minutes_behind(tip_t)
    if lag is not None and lag > _STALE_GDS_MIN:
        return _na_bias(sym, "GDS", "金价陈旧", as_of=tip_t, n=n)

    clean = [float(prices[i]) for i in range(n) if prices[i] and prices[i] > 0]
    if len(clean) < _MIN_POINTS:
        return _na_bias(sym, "GDS", "金价暂无", n=len(clean))

    raw = _analyze_gold_day_chart(
        clean,
        symbol=sym,
        market="GDS",
        slots=slots or 24 * 60,
        as_of=tip_t,
    )
    return _apply_hysteresis(f"GDS:{sym}", _as_gold_labels(raw))


def get_short_bias(symbol: str, market: str = "SH") -> ShortBias:
    mkt = (market or "SH").upper()
    sym = symbol.strip()
    if mkt == "JD":
        return get_jd_short_bias(sym)
    if mkt == "GDS":
        return get_gds_short_bias(sym)
    if not _cn_short_session_open():
        return _closed_bias(sym, mkt)

    if _cn_open_watch():
        label = "金价观察" if _is_gold_etf(sym, mkt) else "开盘观察"
        return ShortBias(
            symbol=sym,
            market=mkt,
            bias="flat",
            label=label,
            score=None,
            lookback_min=_LOOKBACK,
            sample_n=0,
            roc_pct=None,
            as_of=None,
        )

    series = get_intraday(sym, mkt, sym)
    if series.points:
        lag = _minutes_behind(series.points[-1].time)
        if lag is not None and lag > _STALE_CN_MIN:
            lab = "金价陈旧" if _is_gold_etf(sym, mkt) else "短线陈旧"
            return _na_bias(sym, mkt, lab, as_of=series.points[-1].time, n=len(series.points))

    result = compute_short_bias(
        series.points,
        symbol=series.symbol,
        market=series.market,
        lookback=_LOOKBACK,
        deadband=_base_deadband(series.market, series.symbol),
        apply_volume=True,
    )
    if _is_gold_etf(result.symbol, result.market):
        result = _as_gold_labels(result)
    return _apply_hysteresis(f"{result.market}:{result.symbol}", result)


def get_short_biases(pairs: list[tuple[str, str]]) -> list[ShortBias]:
    uniq: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sym, mkt in pairs:
        s, m = sym.strip(), (mkt or "SH").upper()
        if not s:
            continue
        key = f"{m}:{s}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append((s, m))
        if len(uniq) >= _MAX_BATCH:
            break

    if not uniq:
        return []

    cn_open = _cn_short_session_open()
    out: dict[str, ShortBias] = {}
    need_fetch: list[tuple[str, str]] = []

    for s, m in uniq:
        key = f"{m}:{s}"
        if m in ("JD", "GDS"):
            need_fetch.append((s, m))
        elif not cn_open:
            out[key] = _closed_bias(s, m)
        else:
            need_fetch.append((s, m))

    if need_fetch:
        with ThreadPoolExecutor(max_workers=min(_POOL, len(need_fetch))) as ex:
            futs = {ex.submit(get_short_bias, s, m): f"{m}:{s}" for s, m in need_fetch}
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    out[key] = fut.result()
                except Exception:
                    s, m = key.split(":", 1)
                    na = "积存金" if m == "JD" else ("金价暂无" if m == "GDS" else "短线暂无")
                    out[key] = _na_bias(s, m, na)

    return [out[f"{m}:{s}"] for s, m in uniq]
