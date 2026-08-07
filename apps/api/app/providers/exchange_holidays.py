"""Exchange holiday calendars (SSE/SZSE, HKEX, NYSE) for session status.

Dates follow published exchange notices for 2025–2026; extend yearly when
exchanges release the next schedule. Weekends are handled by callers.
"""

from __future__ import annotations

from datetime import date, timedelta


def _daterange(y: int, m1: int, d1: int, m2: int, d2: int) -> set[date]:
    start = date(y, m1, d1)
    end = date(y, m2, d2)
    out: set[date] = set()
    cur = start
    while cur <= end:
        out.add(cur)
        cur += timedelta(days=1)
    return out


def _dates(*ymd: tuple[int, int, int]) -> set[date]:
    return {date(y, m, d) for y, m, d in ymd}


# 沪深北 — 上交所公告〔2024〕38 号 / 〔2025〕45 号
_CN_CLOSED: set[date] = set()
_CN_CLOSED |= {date(2025, 1, 1)}
_CN_CLOSED |= _daterange(2025, 1, 28, 2, 4)
_CN_CLOSED |= _daterange(2025, 4, 4, 4, 6)
_CN_CLOSED |= _daterange(2025, 5, 1, 5, 5)
_CN_CLOSED |= _daterange(2025, 5, 31, 6, 2)
_CN_CLOSED |= _daterange(2025, 10, 1, 10, 8)
_CN_CLOSED |= _daterange(2026, 1, 1, 1, 3)
_CN_CLOSED |= _daterange(2026, 2, 15, 2, 23)
_CN_CLOSED |= _daterange(2026, 4, 4, 4, 6)
_CN_CLOSED |= _daterange(2026, 5, 1, 5, 5)
_CN_CLOSED |= _daterange(2026, 6, 19, 6, 21)
_CN_CLOSED |= _daterange(2026, 9, 25, 9, 27)
_CN_CLOSED |= _daterange(2026, 10, 1, 10, 7)

# 港交所证券 hols 2025–2026（全日休市；半日另见 HK_HALF_DAY）
_HK_CLOSED: set[date] = set()
# 2025 (gov / HKEX common list)
_HK_CLOSED |= _dates(
    (2025, 1, 1),
    (2025, 1, 29),
    (2025, 1, 30),
    (2025, 1, 31),
    (2025, 4, 4),
    (2025, 4, 18),
    (2025, 4, 19),
    (2025, 4, 21),
    (2025, 5, 1),
    (2025, 5, 5),
    (2025, 5, 31),
    (2025, 7, 1),
    (2025, 10, 1),
    (2025, 10, 7),
    (2025, 12, 25),
    (2025, 12, 26),
)
# 2026 HKEX circular CE/SEHK/CT/075/2025
_HK_CLOSED |= _dates(
    (2026, 1, 1),
    (2026, 2, 17),
    (2026, 2, 18),
    (2026, 2, 19),
    (2026, 4, 3),
    (2026, 4, 6),
    (2026, 4, 7),
    (2026, 5, 1),
    (2026, 5, 25),
    (2026, 6, 19),
    (2026, 7, 1),
    (2026, 10, 1),
    (2026, 10, 19),
    (2026, 12, 25),
)

_HK_HALF_DAY: set[date] = _dates(
    (2025, 1, 28),
    (2025, 12, 24),
    (2025, 12, 31),
    (2026, 2, 16),
    (2026, 12, 24),
    (2026, 12, 31),
)

# NYSE full-day closings
_US_CLOSED: set[date] = _dates(
    (2025, 1, 1),
    (2025, 1, 20),
    (2025, 2, 17),
    (2025, 4, 18),
    (2025, 5, 26),
    (2025, 6, 19),
    (2025, 7, 4),
    (2025, 9, 1),
    (2025, 11, 27),
    (2025, 12, 25),
    (2026, 1, 1),
    (2026, 1, 19),
    (2026, 2, 16),
    (2026, 4, 3),
    (2026, 5, 25),
    (2026, 6, 19),
    (2026, 7, 3),
    (2026, 9, 7),
    (2026, 11, 26),
    (2026, 12, 25),
)

_US_EARLY_CLOSE: set[date] = _dates(
    (2025, 7, 3),
    (2025, 11, 28),
    (2025, 12, 24),
    (2026, 11, 27),
    (2026, 12, 24),
)


def is_cn_exchange_holiday(d: date) -> bool:
    """True on SSE/SZSE published holiday (may include weekends in ranges)."""
    return d in _CN_CLOSED


def is_cn_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _CN_CLOSED


def is_hk_exchange_holiday(d: date) -> bool:
    return d in _HK_CLOSED


def is_hk_half_day(d: date) -> bool:
    return d in _HK_HALF_DAY


def is_hk_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _HK_CLOSED


def is_us_exchange_holiday(d: date) -> bool:
    return d in _US_CLOSED


def is_us_early_close(d: date) -> bool:
    return d in _US_EARLY_CLOSE


def is_us_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in _US_CLOSED
