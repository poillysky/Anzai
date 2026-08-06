"""A-share / US market session status (Asia/Shanghai wall clock)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

# China Standard Time (no DST)
CST = timezone(timedelta(hours=8))


@dataclass
class SessionStatus:
    market: str  # CN | US
    state: str  # trading | lunch | pre | closed | weekend
    label: str
    detail: str


def _now_cst() -> datetime:
    return datetime.now(CST)


def cn_session(now: datetime | None = None) -> SessionStatus:
    now = now or _now_cst()
    wd = now.weekday()  # 0=Mon
    if wd >= 5:
        return SessionStatus("CN", "weekend", "周末休市", "下一交易日 09:30 开盘")
    t = now.time()
    if t < time(9, 15):
        return SessionStatus("CN", "pre", "未开盘", "集合竞价 09:15 · 开盘 09:30")
    if time(9, 15) <= t < time(9, 30):
        return SessionStatus("CN", "pre", "集合竞价", "连续竞价 09:30 开始")
    if time(9, 30) <= t <= time(11, 30):
        return SessionStatus("CN", "trading", "交易中", "上午盘 09:30–11:30")
    if time(11, 30) < t < time(13, 0):
        return SessionStatus("CN", "lunch", "午间休市", "下午盘 13:00 开盘")
    if time(13, 0) <= t <= time(15, 0):
        return SessionStatus("CN", "trading", "交易中", "下午盘 13:00–15:00")
    return SessionStatus("CN", "closed", "已收盘", "今日交易结束")


def us_session_bj(now: datetime | None = None) -> SessionStatus:
    """US regular hours in Beijing time: ~21:30–04:00 (approx, ignores DST edge)."""
    now = now or _now_cst()
    wd = now.weekday()
    t = now.time()
    # Rough: Sun–Fri night sessions; Fri after 04:00 closed until Sun 21:30
    if wd == 5 and t >= time(4, 0):
        return SessionStatus("US", "weekend", "美股休市", "周日晚约 21:30 开盘（北京）")
    if wd == 6 and t < time(21, 30):
        return SessionStatus("US", "weekend", "美股休市", "今晚约 21:30 开盘（北京）")
    if t >= time(21, 30) or t <= time(4, 0):
        return SessionStatus("US", "trading", "美股交易中", "常规时段约 21:30–04:00（北京）")
    return SessionStatus("US", "closed", "美股已收盘", "下一时段约 21:30（北京）")


def hk_session(now: datetime | None = None) -> SessionStatus:
    """Hong Kong: 09:30–12:00 / 13:00–16:00 (HKT = CST)."""
    now = now or _now_cst()
    wd = now.weekday()
    if wd >= 5:
        return SessionStatus("HK", "weekend", "港股休市", "下一交易日 09:30 开盘")
    t = now.time()
    if t < time(9, 30):
        return SessionStatus("HK", "pre", "港股未开盘", "开盘 09:30")
    if time(9, 30) <= t <= time(12, 0):
        return SessionStatus("HK", "trading", "港股交易中", "上午盘 09:30–12:00")
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
