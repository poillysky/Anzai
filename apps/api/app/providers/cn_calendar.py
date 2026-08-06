"""Asia/Shanghai calendar helpers — 「今日」以日历日切，不过夜沿用昨盘涨跌。"""

from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

_SH = ZoneInfo("Asia/Shanghai")


def shanghai_now() -> datetime:
    return datetime.now(_SH)


def shanghai_today() -> date:
    return shanghai_now().date()


def parse_as_of_date(as_of: str | None) -> date | None:
    if not as_of or as_of == "mock":
        return None
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", str(as_of))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def quote_is_for_shanghai_today(as_of: str | None) -> bool:
    """True if quote timestamp is on Shanghai's calendar today.

    After 24:00, yesterday's close (e.g. 15:00 as_of) must not count as 「今日」.
    Missing as_of → keep numbers (caller cannot prove staleness).
    """
    d = parse_as_of_date(as_of)
    if d is None:
        return True
    return d >= shanghai_today()
