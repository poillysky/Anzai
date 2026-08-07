"""A-share / HK / US market session status.

CN/HK wall clock: Asia/Shanghai (no DST). US: America/New_York (DST-aware).
Holidays: ``exchange_holidays`` (SSE/HKEX/NYSE published calendars).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.providers.exchange_holidays import (
    is_cn_exchange_holiday,
    is_hk_exchange_holiday,
    is_hk_half_day,
    is_us_early_close,
    is_us_exchange_holiday,
)

# Fallback when tzdata is missing (Windows without tzdata package)
CST = timezone(timedelta(hours=8))

try:
    _SH = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    _SH = CST  # type: ignore[assignment]

try:
    _ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    _ET = None


@dataclass
class SessionStatus:
    market: str  # CN | US | HK
    state: str  # trading | lunch | pre | auction | closed | weekend | holiday
    label: str
    detail: str


def _now_sh() -> datetime:
    return datetime.now(_SH)


def _now_et() -> datetime:
    if _ET is not None:
        return datetime.now(_ET)
    # Rough EST/EDT fallback: second Sunday Mar → first Sunday Nov = EDT (UTC-4)
    utc = datetime.now(timezone.utc)
    y = utc.year

    def nth_weekday(month: int, weekday: int, n: int) -> datetime:
        d = datetime(y, month, 1, tzinfo=timezone.utc)
        while d.weekday() != weekday:
            d += timedelta(days=1)
        return d + timedelta(weeks=n - 1)

    dst_start = nth_weekday(3, 6, 2).replace(hour=7)  # 02:00 ET ≈ 07:00 UTC
    dst_end = nth_weekday(11, 6, 1).replace(hour=6)
    offset = timedelta(hours=-4) if dst_start <= utc < dst_end else timedelta(hours=-5)
    return utc.astimezone(timezone(offset))


def cn_session(now: datetime | None = None) -> SessionStatus:
    now = now or _now_sh()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_SH)
    else:
        now = now.astimezone(_SH)
    d = now.date()
    wd = d.weekday()
    if wd >= 5:
        return SessionStatus("CN", "weekend", "周末休市", "下一交易日 09:30 开盘")
    if is_cn_exchange_holiday(d):
        return SessionStatus("CN", "holiday", "节假日休市", "下一交易日 09:30 开盘")
    t = now.time()
    if t < time(9, 15):
        return SessionStatus("CN", "pre", "未开盘", "集合竞价 09:15 · 开盘 09:30")
    if time(9, 15) <= t < time(9, 30):
        return SessionStatus("CN", "auction", "集合竞价", "连续竞价 09:30 开始")
    if time(9, 30) <= t <= time(11, 30):
        return SessionStatus("CN", "trading", "交易中", "上午盘 09:30–11:30")
    if time(11, 30) < t < time(13, 0):
        return SessionStatus("CN", "lunch", "午间休市", "下午盘 13:00 开盘")
    if time(13, 0) <= t < time(14, 57):
        return SessionStatus("CN", "trading", "交易中", "下午盘 13:00–15:00")
    if time(14, 57) <= t <= time(15, 0):
        return SessionStatus("CN", "auction", "收盘集合竞价", "15:00 收盘")
    return SessionStatus("CN", "closed", "已收盘", "今日交易结束")


def us_session_bj(now: datetime | None = None) -> SessionStatus:
    """US regular hours via America/New_York (DST-aware), labels in 北京语境."""
    et = now
    if et is None:
        et = _now_et()
    elif et.tzinfo is None:
        et = et.replace(tzinfo=_ET or timezone(timedelta(hours=-5)))
    else:
        et = et.astimezone(_ET) if _ET is not None else et

    d = et.date()
    wd = d.weekday()
    if wd >= 5:
        return SessionStatus("US", "weekend", "美股休市", "下个交易日 09:30（美东）")
    if is_us_exchange_holiday(d):
        return SessionStatus("US", "holiday", "美股节假日休市", "下个交易日 09:30（美东）")

    t = et.time()
    close = time(13, 0) if is_us_early_close(d) else time(16, 0)
    close_label = "13:00 早收" if is_us_early_close(d) else "16:00"

    if t < time(9, 30):
        return SessionStatus("US", "pre", "美股未开盘", "常规 09:30–16:00（美东）")
    if t < close:
        return SessionStatus(
            "US",
            "trading",
            "美股交易中",
            f"常规时段至 {close_label}（美东）",
        )
    return SessionStatus("US", "closed", "美股已收盘", "下一时段 09:30（美东）")


def hk_session(now: datetime | None = None) -> SessionStatus:
    """Hong Kong: 09:30–12:00 / 13:00–16:00 (HKT = CST)."""
    now = now or _now_sh()
    if now.tzinfo is None:
        now = now.replace(tzinfo=_SH)
    else:
        now = now.astimezone(_SH)
    d = now.date()
    wd = d.weekday()
    if wd >= 5:
        return SessionStatus("HK", "weekend", "港股休市", "下一交易日 09:30 开盘")
    if is_hk_exchange_holiday(d):
        return SessionStatus("HK", "holiday", "港股节假日休市", "下一交易日 09:30 开盘")
    t = now.time()
    half = is_hk_half_day(d)
    if t < time(9, 30):
        return SessionStatus("HK", "pre", "港股未开盘", "开盘 09:30")
    if time(9, 30) <= t <= time(12, 0):
        return SessionStatus("HK", "trading", "港股交易中", "上午盘 09:30–12:00")
    if half:
        return SessionStatus("HK", "closed", "港股半日收盘", "今日无下午盘")
    if time(12, 0) < t < time(13, 0):
        return SessionStatus("HK", "lunch", "港股午休", "下午盘 13:00 开盘")
    if time(13, 0) <= t <= time(16, 0):
        return SessionStatus("HK", "trading", "港股交易中", "下午盘 13:00–16:00")
    return SessionStatus("HK", "closed", "港股已收盘", "今日交易结束")


def session_for_index_key(key: str) -> SessionStatus:
    if key == "us-nasdaq":
        return us_session_bj()
    if key == "hk-hsi":
        return hk_session()
    return cn_session()
