"""Day / month / year portfolio P&L rolls from daily snapshots.

- **live**: written when user opens 仓库 / returns (today's mark-to-market)
- **bought**: backfilled from each holding's bought_at × current shares ×
  daily kline (A-share/ETF) or OTC NAV history (OF); JD has no reliable daily
  series (live snapshot only). Live rows always win for the same date.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import Holding, PortfolioDailySnapshot
from app.providers.cn_calendar import shanghai_today
from app.providers.kline import get_daily_klines
from app.schemas import (
    PortfolioReturnsBucket,
    PortfolioReturnsSummary,
    PortfolioSummary,
)
from app.services.holding_dates import normalize_bought_at

logger = logging.getLogger(__name__)

_KLINE_LIMIT = 260


def upsert_today_snapshot(
    db: Session,
    user_id: int,
    portfolio: PortfolioSummary,
) -> None:
    """Persist today's live mark-to-market day_pnl (Shanghai calendar)."""
    trade_date = shanghai_today().isoformat()
    row = (
        db.query(PortfolioDailySnapshot)
        .filter(
            PortfolioDailySnapshot.user_id == user_id,
            PortfolioDailySnapshot.trade_date == trade_date,
        )
        .first()
    )
    if row is None:
        row = PortfolioDailySnapshot(user_id=user_id, trade_date=trade_date)
        db.add(row)
    row.total_market_value = float(portfolio.total_market_value)
    row.total_cost = float(portfolio.total_cost)
    row.day_pnl = float(portfolio.day_pnl)
    row.day_pnl_pct = float(portfolio.day_pnl_pct)
    row.source = "live"
    db.commit()


def purge_estimated_snapshots(db: Session, user_id: int) -> None:
    """Drop legacy kline-estimate rows (pre bought_at)."""
    deleted = (
        db.query(PortfolioDailySnapshot)
        .filter(
            PortfolioDailySnapshot.user_id == user_id,
            PortfolioDailySnapshot.source == "estimated",
        )
        .delete(synchronize_session=False)
    )
    if deleted:
        db.commit()


def ensure_bought_history(db: Session, user_id: int) -> None:
    """Rebuild source=bought days from holdings.bought_at × current shares × kline."""
    # Refresh bought series when holdings / buy dates change
    db.query(PortfolioDailySnapshot).filter(
        PortfolioDailySnapshot.user_id == user_id,
        PortfolioDailySnapshot.source == "bought",
    ).delete(synchronize_session=False)
    db.commit()

    holdings = (
        db.query(Holding)
        .filter(Holding.user_id == user_id, Holding.shares > 0)
        .all()
    )
    if not holdings:
        return

    live_dates = {
        str(r[0])
        for r in db.query(PortfolioDailySnapshot.trade_date)
        .filter(
            PortfolioDailySnapshot.user_id == user_id,
            PortfolioDailySnapshot.source == "live",
        )
        .all()
    }
    today = shanghai_today().isoformat()

    # date -> [day_pnl, market_value, prev_mv]
    by_date: dict[str, list[float]] = {}
    total_cost = 0.0

    for h in holdings:
        total_cost += float(h.shares) * float(h.cost)
        mkt = (h.market or "").upper()
        start = normalize_bought_at(
            getattr(h, "bought_at", None) or "",
            fallback=(h.created_at.date() if h.created_at else None),
        )
        shares = float(h.shares)
        if shares <= 0:
            continue

        bars: list[tuple[str, float]] = []
        if mkt == "OF":
            try:
                from app.providers.fund import fetch_otc_nav_history

                hist = fetch_otc_nav_history(h.symbol, days=min(_KLINE_LIMIT, 120))
                bars = [(d[:10], float(nav)) for d, nav in hist if nav and float(nav) > 0]
            except Exception as exc:
                logger.warning(
                    "bought backfill OF nav failed %s: %s",
                    h.symbol,
                    exc,
                )
                continue
        elif mkt == "JD":
            # 积存金无可靠日K；今日靠 live snapshot，历史不硬编
            continue
        else:
            try:
                _, kline_bars = get_daily_klines(h.symbol, h.market, limit=_KLINE_LIMIT)
            except Exception as exc:
                logger.warning(
                    "bought backfill kline failed %s:%s: %s",
                    h.market,
                    h.symbol,
                    exc,
                )
                continue
            bars = [
                (b.date[:10], float(b.close))
                for b in kline_bars
                if b.close and float(b.close) > 0
            ]

        if len(bars) < 2:
            continue
        for i in range(1, len(bars)):
            prev_d, prev_px = bars[i - 1]
            cur_d, cur_px = bars[i]
            d = cur_d
            if d < start or d >= today or d in live_dates:
                continue
            if prev_px <= 0 or cur_px <= 0:
                continue
            day_pnl = shares * (cur_px - prev_px)
            mv = shares * cur_px
            prev_mv = shares * prev_px
            bucket = by_date.setdefault(d, [0.0, 0.0, 0.0])
            bucket[0] += day_pnl
            bucket[1] += mv
            bucket[2] += prev_mv

    if not by_date:
        return

    # Upsert by trade_date — unique is (user_id, trade_date) across sources;
    # concurrent / leftover rows must not 500 the warehouse tab.
    for d, (day_pnl, mv, prev_mv) in by_date.items():
        pct = (day_pnl / prev_mv * 100) if prev_mv > 0 else 0.0
        row = (
            db.query(PortfolioDailySnapshot)
            .filter(
                PortfolioDailySnapshot.user_id == user_id,
                PortfolioDailySnapshot.trade_date == d,
            )
            .first()
        )
        if row is not None:
            if row.source == "live":
                continue
            row.total_market_value = round(mv, 2)
            row.total_cost = round(total_cost, 2)
            row.day_pnl = round(day_pnl, 2)
            row.day_pnl_pct = round(pct, 2)
            row.source = "bought"
            continue
        db.add(
            PortfolioDailySnapshot(
                user_id=user_id,
                trade_date=d,
                total_market_value=round(mv, 2),
                total_cost=round(total_cost, 2),
                day_pnl=round(day_pnl, 2),
                day_pnl_pct=round(pct, 2),
                source="bought",
            )
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("ensure_bought_history commit failed user=%s", user_id)


def _parse_ref(ref: str | None) -> date:
    if not ref:
        return shanghai_today()
    try:
        return date.fromisoformat(ref[:10])
    except ValueError:
        return shanghai_today()


def _month_bounds(d: date) -> tuple[date, date]:
    start = d.replace(day=1)
    last = calendar.monthrange(d.year, d.month)[1]
    end = d.replace(day=last)
    return start, end


def _year_bounds(d: date) -> tuple[date, date]:
    return date(d.year, 1, 1), date(d.year, 12, 31)


def _shift_month(d: date, delta: int) -> date:
    y, m = d.year, d.month + delta
    while m > 12:
        m -= 12
        y += 1
    while m < 1:
        m += 12
        y -= 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(d.day, last))


def _shift_period(d: date, dim: str, delta: int) -> date:
    if dim == "day":
        return _shift_month(d, delta)
    try:
        return d.replace(year=d.year + delta)
    except ValueError:
        return d.replace(year=d.year + delta, day=28)


def _period_label(dim: str, d: date) -> str:
    if dim == "day":
        return f"{d.year}年 {d.month}月"
    return f"{d.year}年"


def _aggregate(
    rows: list[PortfolioDailySnapshot],
) -> tuple[float, float, float, bool]:
    if not rows:
        return 0.0, 0.0, 0.0, False
    pnl = sum(float(r.day_pnl) for r in rows)
    first = min(rows, key=lambda r: r.trade_date)
    start_prev = float(first.total_market_value) - float(first.day_pnl)
    pct = (pnl / start_prev * 100) if start_prev > 0 else 0.0
    end_mv = float(max(rows, key=lambda r: r.trade_date).total_market_value)
    has_bought = any(r.source == "bought" for r in rows)
    return round(pnl, 2), round(pct, 2), round(end_mv, 2), has_bought


def build_returns_summary(
    db: Session,
    user_id: int,
    *,
    dim: str = "day",
    ref: str | None = None,
    portfolio: PortfolioSummary | None = None,
) -> PortfolioReturnsSummary:
    dim = dim if dim in ("day", "month", "year") else "day"
    purge_estimated_snapshots(db, user_id)
    if portfolio is not None:
        try:
            upsert_today_snapshot(db, user_id, portfolio)
        except Exception:
            logger.exception("upsert_today_snapshot failed user=%s", user_id)
            db.rollback()
    try:
        ensure_bought_history(db, user_id)
    except Exception:
        logger.exception("ensure_bought_history failed user=%s", user_id)
        db.rollback()

    anchor = _parse_ref(ref)
    today = shanghai_today()
    if dim == "day":
        start, end = _month_bounds(anchor)
    else:
        start, end = _year_bounds(anchor)

    start_s, end_s = start.isoformat(), end.isoformat()
    rows = (
        db.query(PortfolioDailySnapshot)
        .filter(
            PortfolioDailySnapshot.user_id == user_id,
            PortfolioDailySnapshot.source.in_(("live", "bought")),
            PortfolioDailySnapshot.trade_date >= start_s,
            PortfolioDailySnapshot.trade_date <= end_s,
        )
        .order_by(PortfolioDailySnapshot.trade_date.asc())
        .all()
    )

    pnl, pct, end_mv, has_bought = _aggregate(rows)
    buckets: list[PortfolioReturnsBucket] = []

    if dim == "day":
        for r in rows:
            buckets.append(
                PortfolioReturnsBucket(
                    key=r.trade_date,
                    label=str(int(r.trade_date[8:10])),
                    pnl=round(float(r.day_pnl), 2),
                    pnl_pct=round(float(r.day_pnl_pct), 2),
                    market_value=round(float(r.total_market_value), 2),
                    source=r.source,
                )
            )
    else:
        by_m: dict[str, list[PortfolioDailySnapshot]] = {}
        for r in rows:
            by_m.setdefault(r.trade_date[:7], []).append(r)
        for ym in sorted(by_m.keys()):
            m_rows = by_m[ym]
            m_pnl, m_pct, m_mv, m_bought = _aggregate(m_rows)
            month_n = int(ym[5:7])
            buckets.append(
                PortfolioReturnsBucket(
                    key=ym,
                    label=f"{month_n}月",
                    pnl=m_pnl,
                    pnl_pct=m_pct,
                    market_value=m_mv,
                    source="bought" if m_bought else "live",
                )
            )

    prev_d = _shift_period(anchor, dim, -1)
    next_d = _shift_period(anchor, dim, 1)
    can_next = next_d <= today

    if not rows:
        note = "暂无记录；请在持仓里填写买入日，或打开仓库写入今日盈亏"
    elif has_bought:
        note = "历史日按买入日起、当前份额×日K回补；今日为仓库实时盈亏"
    else:
        note = "仅展示已落库的真实日盈亏"

    return PortfolioReturnsSummary(
        dim=dim,
        ref=anchor.isoformat(),
        label=_period_label(dim, anchor),
        pnl=pnl,
        pnl_pct=pct,
        end_market_value=end_mv,
        trading_days=len(rows),
        has_estimated=has_bought,
        note=note,
        prev_ref=prev_d.isoformat(),
        next_ref=next_d.isoformat() if can_next else None,
        buckets=buckets,
    )
