"""当日盈亏 — 对齐券商现金流转口径（长桥 / 老虎 / 东莞证券公开说明）。

公式：
  当日盈亏 = 现价×现仓 − 昨收×日初仓 + 当日卖出成交额 − 当日买入成交额
  当日盈亏% 分母 ≈ 昨收×日初仓 + 当日买入成交额（老虎：昨收市值+今日开仓金额）

成本 / 浮动盈亏仍用买入均价（减仓不改成本）；与当日盈亏分列。
"""

from __future__ import annotations

from typing import Any

from app.providers.cn_calendar import shanghai_today
from app.services.holding_dates import normalize_bought_at


def _today() -> str:
    return shanghai_today().isoformat()


def ensure_day_session(holding: Any, *, is_new: bool = False) -> None:
    """Calendar-day roll: snapshot SOD shares, zero today's cash flows.

    Important: only ``sod_asof`` marks a completed roll. Do NOT treat
    ``day_buy_asof`` alone as rolled — older code set that without SOD,
    which made day P&L collapse to full market value (100%).
    """
    today = _today()
    sod_asof = str(getattr(holding, "sod_asof", None) or "").strip()[:10]
    shares = float(getattr(holding, "shares", 0) or 0)

    if sod_asof != today:
        holding.sod_shares = 0.0 if is_new else max(shares, 0.0)
        holding.sod_asof = today
        holding.day_buy_amount = 0.0
        holding.day_sell_amount = 0.0
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
        holding.day_buy_asof = today
        return

    # Same calendar day but SOD never initialized (migrate / partial write)
    sod = float(getattr(holding, "sod_shares", 0) or 0)
    buy = float(getattr(holding, "day_buy_amount", 0) or 0)
    sell = float(getattr(holding, "day_sell_amount", 0) or 0)
    if is_new:
        return
    if sod > 0 or buy > 0 or sell > 0 or shares <= 0:
        return
    bought = str(getattr(holding, "bought_at", None) or "").strip()[:10]
    cost = float(getattr(holding, "cost", 0) or 0)
    if bought == today and cost > 0:
        # First buy today without cashflow row — synthesize buy notional
        holding.day_buy_amount = shares * cost
        holding.day_buy_shares = shares
        holding.day_buy_cost = cost
        holding.day_buy_asof = today
        holding.sod_shares = 0.0
    else:
        # Overnight book — snapshot current shares as SOD
        holding.sod_shares = shares



def refresh_day_buy_lot(holding: Any) -> tuple[float, float]:
    """Compat: ensure session + return (day_buy_shares, day_buy_cost)."""
    ensure_day_session(holding)
    shares = float(getattr(holding, "day_buy_shares", 0) or 0)
    cost = float(getattr(holding, "day_buy_cost", 0) or 0)
    total = float(getattr(holding, "shares", 0) or 0)
    if shares > total and total >= 0:
        shares = total
        holding.day_buy_shares = shares
    if shares <= 0:
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
        return 0.0, 0.0
    return shares, cost


def record_day_buy(holding: Any, qty: float, price: float, buy_date: str | None = None) -> None:
    """Record a buy for day P&L.

    - buy_date == today → day_buy_amount += qty×price (今日盈亏相对买入价)
    - buy_date < today  → sod_shares += qty (昨仓，今日盈亏相对昨收)
    """
    if qty <= 0 or price <= 0:
        return
    today = _today()
    day = normalize_bought_at(buy_date) if buy_date is not None else today
    q = float(qty)
    px = float(price)

    if day < today:
        sod_asof = str(getattr(holding, "sod_asof", None) or "").strip()[:10]
        if sod_asof != today:
            shares = float(getattr(holding, "shares", 0) or 0)
            holding.sod_shares = max(shares, 0.0)
            holding.sod_asof = today
            holding.day_buy_amount = 0.0
            holding.day_sell_amount = 0.0
            holding.day_buy_shares = 0.0
            holding.day_buy_cost = 0.0
            holding.day_buy_asof = today
        else:
            holding.sod_shares = float(getattr(holding, "sod_shares", 0) or 0) + q
        return

    if day > today:
        day = today

    sod_asof = str(getattr(holding, "sod_asof", None) or "").strip()[:10]
    if sod_asof != today:
        shares = float(getattr(holding, "shares", 0) or 0)
        holding.sod_shares = max(shares - q, 0.0)
        holding.sod_asof = today
        holding.day_buy_amount = 0.0
        holding.day_sell_amount = 0.0
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
        holding.day_buy_asof = today
    holding.day_buy_amount = float(getattr(holding, "day_buy_amount", 0) or 0) + q * px
    old_s = float(getattr(holding, "day_buy_shares", 0) or 0)
    old_c = float(getattr(holding, "day_buy_cost", 0) or 0)
    next_s = old_s + q
    holding.day_buy_shares = next_s
    holding.day_buy_cost = (old_s * old_c + q * px) / next_s if next_s > 0 else px
    holding.day_buy_asof = today



def record_day_sell(holding: Any, qty: float, price: float) -> None:
    """Accumulate a sale into today's sell notional; peel day-lot shares first."""
    if qty <= 0 or price <= 0:
        return
    sod_asof = str(getattr(holding, "sod_asof", None) or "").strip()[:10]
    today = _today()
    if sod_asof != today:
        # Shares may already be reduced — restore SOD ≈ current + sold qty
        shares = float(getattr(holding, "shares", 0) or 0)
        holding.sod_shares = max(shares + float(qty), 0.0)
        holding.sod_asof = today
        holding.day_buy_amount = 0.0
        holding.day_sell_amount = 0.0
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
        holding.day_buy_asof = today
    q = float(qty)
    px = float(price)
    holding.day_sell_amount = float(getattr(holding, "day_sell_amount", 0) or 0) + q * px
    old_s = float(getattr(holding, "day_buy_shares", 0) or 0)
    if old_s <= 0:
        return
    take = min(q, old_s)
    left = old_s - take
    if left <= 1e-9:
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
    else:
        holding.day_buy_shares = left


def reduce_day_buy(holding: Any, qty: float) -> None:
    """Legacy peel without sell price — prefer record_day_sell."""
    if qty <= 0:
        return
    ensure_day_session(holding)
    old_s = float(getattr(holding, "day_buy_shares", 0) or 0)
    if old_s <= 0:
        return
    take = min(float(qty), old_s)
    left = old_s - take
    if left <= 1e-9:
        holding.day_buy_shares = 0.0
        holding.day_buy_cost = 0.0
    else:
        holding.day_buy_shares = left


def apply_share_cost_delta(
    holding: Any,
    *,
    old_shares: float,
    old_cost: float,
    new_shares: float,
    new_cost: float,
    trade_price: float | None = None,
    trade_date: str | None = None,
    mark_price: float | None = None,
) -> None:
    """Infer 补仓/减仓 from an update of totals and adjust today's cash flows."""
    delta = float(new_shares) - float(old_shares)
    fill_day = normalize_bought_at(trade_date) if trade_date else _today()
    if delta > 1e-9:
        basis_delta = float(new_shares) * float(new_cost) - float(old_shares) * float(old_cost)
        px = float(trade_price) if trade_price and trade_price > 0 else (
            basis_delta / delta if delta else float(new_cost)
        )
        if px <= 0:
            px = float(new_cost) if float(new_cost) > 0 else float(old_cost)
        record_day_buy(holding, delta, px, fill_day)
    elif delta < -1e-9:
        qty = -delta
        if trade_price is not None and float(trade_price) > 0:
            px = float(trade_price)
        elif mark_price is not None and float(mark_price) > 0:
            # Prefer live mark over book cost so day sell notional ≈ broker
            px = float(mark_price)
        else:
            px = float(old_cost) if float(old_cost) > 0 else float(new_cost)
        record_day_sell(holding, qty, px)


def merge_day_buy_lots(primary: Any, donor: Any) -> None:
    """Merge donor's same-day cash flows into primary when consolidating rows."""
    ensure_day_session(primary)
    d_asof = str(getattr(donor, "sod_asof", None) or getattr(donor, "day_buy_asof", None) or "").strip()[:10]
    today = _today()
    if d_asof != today:
        return
    primary.day_buy_amount = float(getattr(primary, "day_buy_amount", 0) or 0) + float(
        getattr(donor, "day_buy_amount", 0) or 0
    )
    primary.day_sell_amount = float(getattr(primary, "day_sell_amount", 0) or 0) + float(
        getattr(donor, "day_sell_amount", 0) or 0
    )
    # SOD: keep primary's (already today's snapshot); if primary sod was 0 and donor had sod, add
    p_sod = float(getattr(primary, "sod_shares", 0) or 0)
    d_sod = float(getattr(donor, "sod_shares", 0) or 0)
    if p_sod <= 0 and d_sod > 0:
        primary.sod_shares = d_sod
    elif d_sod > 0:
        primary.sod_shares = p_sod + d_sod
    # Mirror day-lot shares
    p_s = float(getattr(primary, "day_buy_shares", 0) or 0)
    p_c = float(getattr(primary, "day_buy_cost", 0) or 0)
    d_s = float(getattr(donor, "day_buy_shares", 0) or 0)
    d_c = float(getattr(donor, "day_buy_cost", 0) or 0)
    if d_s > 0:
        next_s = p_s + d_s
        primary.day_buy_shares = next_s
        primary.day_buy_cost = (p_s * p_c + d_s * d_c) / next_s if next_s > 0 else d_c
        primary.day_buy_asof = today


def day_pnl_cashflow(
    *,
    shares: float,
    price: float,
    prev_close: float | None,
    sod_shares: float,
    day_buy_amount: float,
    day_sell_amount: float,
) -> tuple[float, float]:
    """Broker cash-flow day P&L → (pnl, baseline_for_pct)."""
    cur = float(shares)
    px = float(price)
    sod = max(float(sod_shares), 0.0)
    buy = max(float(day_buy_amount), 0.0)
    sell = max(float(day_sell_amount), 0.0)
    pc = float(prev_close) if prev_close is not None and float(prev_close) > 0 else None

    # Guard: uninitialized SOD+cashflow would yield pnl ≈ full market value (100%)
    if sod <= 0 and buy <= 0 and sell <= 0 and cur > 0 and pc is not None:
        pnl = round(cur * (px - pc), 2)
        baseline = cur * pc
        return pnl, baseline

    current_mv = cur * px
    yesterday_mv = (pc * sod) if pc is not None else 0.0
    # No quote prev_close but had overnight shares: treat as flat vs mark (avoid inventing)
    if pc is None and sod > 0 and cur > 0:
        yesterday_mv = sod * px

    pnl = current_mv - yesterday_mv + sell - buy
    # 老虎：分母 = 昨收市值 + 今日开仓金额
    baseline = yesterday_mv + buy
    if baseline <= 1e-9:
        baseline = buy if buy > 0 else (current_mv if current_mv > 0 else 0.0)
    return round(pnl, 2), baseline


def day_pnl_parts(
    *,
    shares: float,
    cost: float,
    price: float,
    prev_close: float | None,
    bought_at: str,
    day_buy_shares: float,
    day_buy_cost: float,
    sod_shares: float | None = None,
    day_buy_amount: float | None = None,
    day_sell_amount: float | None = None,
) -> tuple[float, float]:
    """Prefer cash-flow fields; fall back to legacy 昨仓/今买拆分 for old rows."""
    if day_buy_amount is not None or day_sell_amount is not None or sod_shares is not None:
        buy_amt = float(day_buy_amount or 0)
        sell_amt = float(day_sell_amount or 0)
        sod = float(sod_shares) if sod_shares is not None else max(
            float(shares) - min(max(float(day_buy_shares), 0.0), float(shares)),
            0.0,
        )
        # If we have day-lot but no buy amount yet (partial migrate), synthesize
        if buy_amt <= 0 and float(day_buy_shares) > 0:
            unit = float(day_buy_cost) if float(day_buy_cost) > 0 else float(cost)
            buy_amt = float(day_buy_shares) * unit
        return day_pnl_cashflow(
            shares=shares,
            price=price,
            prev_close=prev_close,
            sod_shares=sod,
            day_buy_amount=buy_amt,
            day_sell_amount=sell_amt,
        )

    # Legacy split (no sod / cashflow columns populated)
    today = _today()
    total = float(shares)
    if total <= 0:
        return 0.0, 0.0
    day_s = min(max(float(day_buy_shares), 0.0), total)
    if day_s <= 0 and bought_at == today and float(cost) > 0:
        day_s = total
        day_buy_cost = float(cost)
    old_s = total - day_s
    pnl = 0.0
    base = 0.0
    if old_s > 0 and prev_close is not None and prev_close > 0:
        pnl += old_s * (price - float(prev_close))
        base += old_s * float(prev_close)
    elif old_s > 0 and float(cost) > 0:
        pnl += old_s * (price - float(cost))
        base += old_s * float(cost)
    if day_s > 0:
        unit = float(day_buy_cost) if float(day_buy_cost) > 0 else float(cost)
        if unit > 0:
            pnl += day_s * (price - unit)
            base += day_s * unit
    return round(pnl, 2), base
