from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.models import Holding
from app.schemas import (
    HoldingCreate,
    HoldingOut,
    HoldingUpdate,
    PortfolioReturnsSummary,
    PortfolioSummary,
)
from app.services.holding_dates import earlier_bought_at, normalize_bought_at
from app.services.holding_day_lots import apply_share_cost_delta, ensure_day_session, record_day_buy
from app.services.portfolio import build_portfolio, consolidate_same_symbol
from app.services.portfolio_returns import build_returns_summary, upsert_today_snapshot
from app.services.quote import get_quote, normalize_symbol

router = APIRouter(prefix="/holdings", tags=["holdings"], dependencies=[Depends(require_user)])


@router.get("", response_model=PortfolioSummary)
def list_holdings(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> PortfolioSummary:
    consolidate_same_symbol(db, user.id)
    portfolio = build_portfolio(db, user.id)
    try:
        # Persist SOD / day-cashflow heal from ensure_day_session
        db.commit()
    except Exception:
        db.rollback()
    try:
        upsert_today_snapshot(db, user.id, portfolio)
    except Exception:
        # Snapshot must not break the live warehouse view
        pass
    return portfolio


@router.get("/returns", response_model=PortfolioReturnsSummary)
def portfolio_returns(
    dim: str = Query(default="day", pattern="^(day|month|year)$"),
    ref: str | None = Query(default=None, description="YYYY-MM-DD anchor"),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> PortfolioReturnsSummary:
    consolidate_same_symbol(db, user.id)
    portfolio = build_portfolio(db, user.id)
    return build_returns_summary(db, user.id, dim=dim, ref=ref, portfolio=portfolio)


@router.post("", response_model=HoldingOut, status_code=status.HTTP_201_CREATED)
def create_holding(
    payload: HoldingCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> HoldingOut:
    symbol, market = normalize_symbol(payload.symbol, payload.market)
    name = payload.name
    if not name:
        try:
            name = get_quote(symbol, market).name
        except Exception:
            name = symbol

    existing = (
        db.query(Holding)
        .filter(
            Holding.user_id == user.id,
            Holding.symbol == symbol,
            Holding.market == market,
        )
        .order_by(Holding.id.asc())
        .first()
    )
    bought = normalize_bought_at(payload.bought_at)

    if existing is not None:
        old_s = float(existing.shares)
        old_c = float(existing.cost)
        qty = float(payload.shares)
        px = float(payload.cost)
        # Roll SOD on pre-trade shares — never snapshot after shares already increased
        ensure_day_session(existing)
        next_s = old_s + qty
        existing.shares = next_s
        existing.cost = ((old_s * old_c + qty * px) / next_s) if next_s > 0 else old_c
        existing.bought_at = earlier_bought_at(getattr(existing, "bought_at", None), bought)
        record_day_buy(existing, qty, px, bought)
        if name and not existing.name:
            existing.name = name
        db.commit()
        db.refresh(existing)
        portfolio = build_portfolio(db, user.id)
        for h in portfolio.holdings:
            if h.id == existing.id:
                return h
        return HoldingOut.model_validate(existing)

    row = Holding(
        user_id=user.id,
        symbol=symbol,
        name=name,
        market=market,
        shares=payload.shares,
        cost=payload.cost,
        tags=payload.tags,
        bought_at=bought,
    )
    db.add(row)
    # New row: SOD=0 before recording today's buy notional
    ensure_day_session(row, is_new=True)
    record_day_buy(row, float(payload.shares), float(payload.cost), bought)
    db.commit()
    db.refresh(row)
    portfolio = build_portfolio(db, user.id)
    for h in portfolio.holdings:
        if h.id == row.id:
            return h
    return HoldingOut.model_validate(row)


@router.patch("/{holding_id}", response_model=HoldingOut)
def update_holding(
    holding_id: int,
    payload: HoldingUpdate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> HoldingOut:
    row = (
        db.query(Holding)
        .filter(Holding.id == holding_id, Holding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Holding not found")

    data = payload.model_dump(exclude_unset=True)
    trade_price = data.pop("trade_price", None)
    trade_date = data.pop("trade_date", None)
    if "symbol" in data or "market" in data:
        symbol, market = normalize_symbol(
            data.get("symbol", row.symbol),
            data.get("market", row.market),
        )
        data["symbol"] = symbol
        data["market"] = market
    if "bought_at" in data:
        data["bought_at"] = normalize_bought_at(data.get("bought_at"))

    old_shares = float(row.shares)
    old_cost = float(row.cost)
    # Snapshot SOD before mutating shares (calendar roll must see pre-trade size)
    ensure_day_session(row)
    for key, value in data.items():
        setattr(row, key, value)

    fill_day = normalize_bought_at(trade_date) if trade_date else None
    if fill_day and ("shares" in data or trade_price is not None):
        # Position start date = earliest of existing and this fill
        row.bought_at = earlier_bought_at(getattr(row, "bought_at", None), fill_day)

    if "shares" in data or "cost" in data:
        apply_share_cost_delta(
            row,
            old_shares=old_shares,
            old_cost=old_cost,
            new_shares=float(row.shares),
            new_cost=float(row.cost),
            trade_price=float(trade_price) if trade_price is not None else None,
            trade_date=fill_day,
        )

    db.commit()
    db.refresh(row)
    portfolio = build_portfolio(db, user.id)
    for h in portfolio.holdings:
        if h.id == row.id:
            return h
    return HoldingOut.model_validate(row)


@router.delete("/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_holding(
    holding_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> None:
    row = (
        db.query(Holding)
        .filter(Holding.id == holding_id, Holding.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Holding not found")
    db.delete(row)
    db.commit()
