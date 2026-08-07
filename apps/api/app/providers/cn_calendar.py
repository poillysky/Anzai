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
    return d == shanghai_today()


def a_share_day_label(as_of: str | None) -> str:
    """Evidence / prompt tag so models don't call last session 「今日」."""
    d = parse_as_of_date(as_of)
    today = shanghai_today()
    if d is None:
        return "时间未知"
    if d == today:
        return "今日"
    return f"收盘·{d.isoformat()}（非今日）"


def normalize_a_share_day_move(
    change_pct: float | None,
    as_of: str | None,
) -> dict[str, float | str | bool | None]:
    """Split calendar-today move vs last printed session move.

    Cross-calendar stale quotes: today move = 0; keep last session % under
    ``last_session_change_pct`` with an explicit non-今日 label.
    """
    fresh = quote_is_for_shanghai_today(as_of)
    label = a_share_day_label(as_of)
    raw = float(change_pct) if isinstance(change_pct, (int, float)) else None
    if fresh:
        return {
            "fresh_today": True,
            "day_label": label,
            "change_pct": raw,
            "last_session_change_pct": raw,
            "as_of": as_of,
        }
    return {
        "fresh_today": False,
        "day_label": label,
        "change_pct": 0.0 if raw is not None else None,
        "last_session_change_pct": raw,
        "as_of": as_of,
    }
