from sqlalchemy.orm import Session

from app.models import Holding
from app.providers.cn_calendar import quote_counts_for_day_pnl
from app.schemas import HoldingOut, PortfolioSummary
from app.services.holding_dates import earlier_bought_at, normalize_bought_at
from app.services.holding_day_lots import (
    day_pnl_parts,
    ensure_day_session,
    merge_day_buy_lots,
    refresh_day_buy_lot,
)
from app.providers.quote import get_quotes


def consolidate_same_symbol(db: Session, user_id: int) -> bool:
    """One row per (user, market, symbol). Weighted-average cost; UI shows current state only."""
    rows = (
        db.query(Holding)
        .filter(Holding.user_id == user_id)
        .order_by(Holding.id.asc())
        .all()
    )
    buckets: dict[tuple[str, str], list[Holding]] = {}
    for row in rows:
        buckets.setdefault((row.market, row.symbol), []).append(row)

    changed = False
    for lots in buckets.values():
        if len(lots) <= 1:
            continue
        primary = lots[0]
        total_shares = sum(float(h.shares) for h in lots)
        total_basis = sum(float(h.shares) * float(h.cost) for h in lots)
        primary.shares = total_shares
        primary.cost = (total_basis / total_shares) if total_shares > 0 else float(primary.cost)
        bought = normalize_bought_at(getattr(primary, "bought_at", None) or "")
        refresh_day_buy_lot(primary)
        for h in lots[1:]:
            bought = earlier_bought_at(bought, getattr(h, "bought_at", None))
            merge_day_buy_lots(primary, h)
            db.delete(h)
        if (getattr(primary, "bought_at", "") or "") != bought:
            primary.bought_at = bought
        changed = True

    if changed:
        db.commit()
    return changed


def build_portfolio(db: Session, user_id: int) -> PortfolioSummary:
    rows = (
        db.query(Holding)
        .filter(Holding.user_id == user_id)
        .order_by(Holding.id.asc())
        .all()
    )
    quotes = get_quotes([(h.symbol, h.market) for h in rows]) if rows else {}

    holdings: list[HoldingOut] = []
    total_cost = 0.0
    total_mv = 0.0
    total_prev_mv = 0.0
    total_day_pnl = 0.0

    for h in rows:
        q = quotes.get(f"{h.market}:{h.symbol}") or quotes.get(h.symbol)
        price = q.price if q and q.price > 0 else h.cost
        prev_close = q.prev_close if q else None
        change_pct = q.change_pct if q else None
        as_of = q.as_of if q else None
        name = h.name or (q.name if q else h.symbol)
        cost_value = h.shares * h.cost
        market_value = h.shares * price
        pnl = market_value - cost_value
        pnl_pct = (pnl / cost_value * 100) if cost_value > 0 else 0.0

        bought_at = normalize_bought_at(
            getattr(h, "bought_at", None) or "",
            fallback=(h.created_at.date() if h.created_at else None),
        )
        ensure_day_session(h)
        day_s, day_c = refresh_day_buy_lot(h)

        # A 股：跨日仍挂昨收 → 今日归零；场外净值常 T−1，按 NAV 日放行
        fresh_for_day = quote_counts_for_day_pnl(as_of, h.market)

        day_pnl: float | None = None
        day_pnl_pct: float | None = None
        if fresh_for_day:
            day_pnl, baseline = day_pnl_parts(
                shares=float(h.shares),
                cost=float(h.cost),
                price=float(price),
                prev_close=prev_close,
                bought_at=bought_at,
                day_buy_shares=day_s,
                day_buy_cost=day_c,
                sod_shares=float(getattr(h, "sod_shares", 0) or 0),
                day_buy_amount=float(getattr(h, "day_buy_amount", 0) or 0),
                day_sell_amount=float(getattr(h, "day_sell_amount", 0) or 0),
            )
            # Only invent prev from change_pct when cashflow baseline is empty
            # (no SOD / no buys) — never overwrite a real cash-flow day_pnl.
            if (
                baseline <= 1e-9
                and float(getattr(h, "day_buy_amount", 0) or 0) <= 0
                and float(getattr(h, "day_sell_amount", 0) or 0) <= 0
                and change_pct is not None
                and price > 0
            ):
                prev = price / (1 + change_pct / 100)
                day_pnl = round(h.shares * (price - prev), 2)
                baseline = h.shares * prev
            day_pnl_pct = (
                round(float(day_pnl or 0) / baseline * 100, 2) if baseline > 1e-9 else 0.0
            )
            total_prev_mv += baseline
            total_day_pnl += float(day_pnl or 0)
        else:
            day_pnl = 0.0
            day_pnl_pct = 0.0
            change_pct = 0.0
            # 昨收对齐现价，跨日未开盘时今日涨跌视为 0
            if price > 0:
                prev_close = price

        total_cost += cost_value
        total_mv += market_value
        holdings.append(
            HoldingOut(
                id=h.id,
                symbol=h.symbol,
                name=name,
                market=h.market,
                shares=h.shares,
                cost=h.cost,
                tags=h.tags,
                bought_at=bought_at,
                created_at=h.created_at,
                updated_at=h.updated_at,
                last_price=price,
                prev_close=prev_close,
                change_pct=change_pct,
                market_value=round(market_value, 2),
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 2),
                day_pnl=day_pnl,
                day_pnl_pct=day_pnl_pct,
            )
        )

    for item in holdings:
        if total_mv > 0 and item.market_value is not None:
            item.weight = round(item.market_value / total_mv * 100, 2)
        else:
            item.weight = 0.0

    total_pnl = total_mv - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    day_pnl_pct = (total_day_pnl / total_prev_mv * 100) if total_prev_mv > 0 else 0.0

    return PortfolioSummary(
        total_cost=round(total_cost, 2),
        total_market_value=round(total_mv, 2),
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        day_pnl=round(total_day_pnl, 2),
        day_pnl_pct=round(day_pnl_pct, 2),
        holdings=holdings,
    )
