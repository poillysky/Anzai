"""Shared East Money helpers — resilient for both open and closed sessions.

push2 is flaky (often disconnects). push2delay keeps last session data after hours
and may show '-' for live price before continuous auction — callers must use em_float
and fall back to 昨收 fields (f18 / preClose).
"""

from __future__ import annotations

from typing import Iterable

from app.providers.session import cn_session

EM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

_PUSH2 = "https://push2.eastmoney.com"
_DELAY = "https://push2delay.eastmoney.com"
_HIS = "https://push2his.eastmoney.com"


def em_float(value: object) -> float | None:
    """Parse EM numeric fields; treat '-' / blank as missing (not 0)."""
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def prefer_delay_first() -> bool:
    """After close / lunch / weekend / pre-open: delay host is more reliable than push2."""
    try:
        state = cn_session().state
    except Exception:
        return False
    return state in {"closed", "lunch", "weekend", "pre"}


def _order(prefer_delay: bool, live: str, delay: str, *rest: str) -> tuple[str, ...]:
    if prefer_delay:
        return (delay, live, *rest)
    return (live, delay, *rest)


def clist_urls() -> tuple[str, ...]:
    """Board / leaderboard clist endpoints."""
    return _order(
        prefer_delay_first(),
        f"{_PUSH2}/api/qt/clist/get",
        f"{_DELAY}/api/qt/clist/get",
    )


def trends_urls() -> tuple[str, ...]:
    """Intraday trends2 endpoints (his as last resort)."""
    prefer = prefer_delay_first()
    live = f"{_PUSH2}/api/qt/stock/trends2/get"
    delay = f"{_DELAY}/api/qt/stock/trends2/get"
    his = f"{_HIS}/api/qt/stock/trends2/get"
    if prefer:
        return (delay, his, live)
    return (live, delay, his)


def stock_get_urls() -> tuple[str, ...]:
    """Single-stock / board quote get endpoints."""
    return _order(
        prefer_delay_first(),
        f"{_PUSH2}/api/qt/stock/get",
        f"{_DELAY}/api/qt/stock/get",
    )


def host_label(url: str) -> str:
    try:
        return url.split("/")[2]
    except IndexError:
        return url


def first_nonempty(rows: Iterable[object]) -> bool:
    for _ in rows:
        return True
    return False
