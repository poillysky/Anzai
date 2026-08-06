"""Normalize holding bought_at (YYYY-MM-DD)."""

from __future__ import annotations

from datetime import date, datetime

from app.providers.cn_calendar import shanghai_today


def normalize_bought_at(value: str | None, *, fallback: date | None = None) -> str:
    """Return YYYY-MM-DD; empty/invalid → Shanghai today (or fallback)."""
    base = fallback or shanghai_today()
    if value is None:
        return base.isoformat()
    s = str(value).strip()[:10]
    if not s:
        return base.isoformat()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        pass
    # created_at-style timestamps
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return base.isoformat()


def earlier_bought_at(a: str | None, b: str | None) -> str:
    """Keep the earlier of two buy dates (both normalized)."""
    na = normalize_bought_at(a)
    nb = normalize_bought_at(b)
    return na if na <= nb else nb
