from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.models import WatchlistItem
from app.providers.gold import get_gold_board, list_gold_etfs
from app.providers.intraday import get_intraday
from app.providers.leaders import get_leaders
from app.providers.macro import (
    calendar_clock_line,
    freshness_label,
    get_macro_quotes,
)
from app.providers.quote import Quote, fetch_sina_int, get_quote, get_quotes, normalize_symbol
from app.providers.search import search_symbols
from app.providers.session import session_for_index_key
from app.providers.short_bias import get_short_biases
from app.providers.depth_flow import get_depth_flow
from app.schemas import (
    DepthFlowOut,
    BookLevelOut,
    GoldBoardItemOut,
    GoldBoardOut,
    GoldBoardSectionOut,
    GoldEtfOut,
    MoneyFlowDayOut,
    MacroQuoteOut,
    MacroTopicOut,
    OrderBookOut,
    IndexQuoteOut,
    IntradayOut,
    IntradayPointOut,
    LeadersOut,
    LeaderStockOut,
    QuoteOut,
    SearchHitOut,
    SearchOut,
    SessionOut,
    ShortBiasBatchOut,
    ShortBiasOut,
    WatchlistCreate,
    WatchlistOut,
)

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(require_user)])

# Sina A-share: sh000001 / sz399001 / sz399006
# Chart indices: (key, symbol, market, name, em_secid|None)
CHART_INDICES: list[tuple[str, str, str, str, str | None]] = [
    ("sh-composite", "000001", "SH", "上证指数", None),
    ("sz-component", "399001", "SZ", "深证成指", None),
    ("chinext", "399006", "SZ", "创业板指", None),
    ("hk-hsi", "HSI", "HK", "恒生指数", "100.HSI"),
    ("us-nasdaq", "NDX", "US", "纳斯达克", "100.NDX"),
]

CN_INDICES: list[tuple[str, str, str, str]] = [
    (k, sym, mkt, name) for k, sym, mkt, name, _ in CHART_INDICES if mkt in ("SH", "SZ")
]

# Sina int_*: (key, our_symbol, sina_code, name)
HK_INDICES: list[tuple[str, str, str, str]] = [
    ("hk-hsi", "HSI", "int_hangseng", "恒生指数"),
]

US_INDICES: list[tuple[str, str, str, str]] = [
    ("us-nasdaq", "IXIC", "int_nasdaq", "纳斯达克"),
]


@router.get("/indices", response_model=list[IndexQuoteOut])
def indices() -> list[IndexQuoteOut]:
    """Realtime major indices: 上证 / 深成 / 创业 / 恒生 / 纳斯达克."""
    pairs = [(sym, mkt) for _, sym, mkt, _ in CN_INDICES]
    quotes = get_quotes(pairs)

    def _fill_int(codes: list[tuple[str, str, str]], market: str) -> dict[str, Quote]:
        fetched = fetch_sina_int(codes)
        for sym, _, name in codes:
            if sym not in fetched:
                fetched[sym] = Quote(
                    symbol=sym,
                    name=name,
                    market=market,
                    price=0.0,
                    change_pct=None,
                    prev_close=None,
                )
            else:
                fetched[sym].market = market
        return fetched

    hk_codes = [(sym, sina, name) for _, sym, sina, name in HK_INDICES]
    us_codes = [(sym, sina, name) for _, sym, sina, name in US_INDICES]
    hk_quotes = _fill_int(hk_codes, "HK")
    us_quotes = _fill_int(us_codes, "US")

    out: list[IndexQuoteOut] = []
    for key, sym, mkt, fallback_name in CN_INDICES:
        q = quotes.get(sym)
        out.append(
            IndexQuoteOut(
                key=key,
                symbol=sym,
                name=fallback_name,
                market=mkt,
                price=q.price if q else 0.0,
                change_pct=q.change_pct if q else None,
                prev_close=q.prev_close if q else None,
            )
        )
    for key, sym, _, fallback_name in HK_INDICES:
        q = hk_quotes.get(sym)
        out.append(
            IndexQuoteOut(
                key=key,
                symbol=sym,
                name=fallback_name,
                market="HK",
                price=q.price if q else 0.0,
                change_pct=q.change_pct if q else None,
                prev_close=q.prev_close if q else None,
            )
        )
    for key, sym, _, fallback_name in US_INDICES:
        q = us_quotes.get(sym)
        out.append(
            IndexQuoteOut(
                key=key,
                symbol=sym,
                name=fallback_name,
                market="US",
                price=q.price if q else 0.0,
                change_pct=q.change_pct if q else None,
                prev_close=q.prev_close if q else None,
            )
        )
    return out


@router.get("/macro", response_model=MacroTopicOut)
def market_macro(topic: str = Query(default="gold")) -> MacroTopicOut:
    """Macro / commodity reference quotes (gold spot & futures are view-only)."""
    topic_id, quotes, err = get_macro_quotes(topic or "gold")
    # Drop A-share ETF rows here — they belong on /gold-etfs + detail modal
    ref_quotes = [q for q in quotes if q.venue != "a_share"]
    return MacroTopicOut(
        topic=topic_id or (topic or "gold"),
        calendar=calendar_clock_line(),
        quotes=[
            MacroQuoteOut(
                key=q.key,
                name=q.name,
                price=q.price,
                unit=q.unit,
                change_pct=q.change_pct,
                prev=q.prev,
                as_of=q.as_of,
                live=q.live,
                venue=q.venue,
                freshness=freshness_label(q.as_of, venue=q.venue),
            )
            for q in ref_quotes
        ],
        hint=err or "",
        note="现货/外盘为参考价，不可入仓；下方黄金 ETF 可看分时并加入仓库",
    )


@router.get("/gold-etfs", response_model=list[GoldEtfOut])
def gold_etfs() -> list[GoldEtfOut]:
    """Curated holdable gold ETFs (A-share) for Market discovery."""
    rows = list_gold_etfs()
    return [
        GoldEtfOut(
            symbol=r.symbol,
            market=r.market,
            name=r.name,
            price=r.price,
            change_pct=r.change_pct,
            prev_close=r.prev_close,
        )
        for r in rows
    ]


@router.get("/gold-board", response_model=GoldBoardOut)
def gold_board() -> GoldBoardOut:
    """Gold board: domestic (浙商/民生) / international (伦敦/纽约) / shop (周大福等门店)."""
    board = get_gold_board()
    return GoldBoardOut(
        note=board.note,
        sections=[
            GoldBoardSectionOut(
                id=sec.id,
                title=sec.title,
                subtitle=sec.subtitle,
                items=[
                    GoldBoardItemOut(
                        id=it.id,
                        name=it.name,
                        section=it.section,
                        price=it.price,
                        change_pct=it.change_pct,
                        prev=it.prev,
                        unit=it.unit,
                        freshness=it.freshness,
                        note=it.note,
                        holdable=it.holdable,
                        symbol=it.symbol,
                        market=it.market,
                        chart=it.chart,
                        chart_times=it.chart_times,
                        chart_slots=it.chart_slots,
                        chart_session=it.chart_session,
                    )
                    for it in sec.items
                ],
            )
            for sec in board.sections
        ],
    )


@router.get("/session", response_model=SessionOut)
def market_session(key: str = Query(default="sh-composite")) -> SessionOut:
    """Trading session strip for the selected index tab."""
    s = session_for_index_key(key)
    return SessionOut(market=s.market, state=s.state, label=s.label, detail=s.detail)


@router.get("/intraday", response_model=IntradayOut)
def intraday(
    key: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    market: str = Query(default="SH"),
) -> IntradayOut:
    """Intraday chart for a major index key, or any A-share symbol."""
    if symbol:
        sym, mkt = normalize_symbol(symbol, market)
        if mkt == "US" or (sym and sym[0].isalpha()):
            return IntradayOut(
                key=sym,
                symbol=sym,
                name=sym,
                market="US",
                prev_close=None,
                open_price=None,
                session="us",
                points=[],
            )
        series = get_intraday(sym, mkt, sym, session="cn")
        return IntradayOut(
            key=sym,
            symbol=series.symbol,
            name=series.name or sym,
            market=series.market,
            prev_close=series.prev_close,
            open_price=series.open_price,
            session=series.session,
            points=[IntradayPointOut(time=p.time, price=p.price, avg=p.avg) for p in series.points],
        )

    meta = next((m for m in CHART_INDICES if m[0] == (key or "sh-composite")), CHART_INDICES[0])
    k, sym, mkt, name, em_secid = meta
    sess = "us" if mkt == "US" else ("hk" if mkt == "HK" else "cn")
    series = get_intraday(
        sym,
        mkt,
        name,
        em_secid=em_secid,
        session=sess,
    )
    return IntradayOut(
        key=k,
        symbol=series.symbol,
        name=name,
        market=series.market,
        prev_close=series.prev_close,
        open_price=series.open_price,
        session=series.session,
        points=[IntradayPointOut(time=p.time, price=p.price, avg=p.avg) for p in series.points],
    )


@router.get("/short-bias", response_model=ShortBiasBatchOut)
def short_bias(
    keys: str = Query(
        ...,
        description="Comma-separated MARKET:SYMBOL, e.g. SH:601138,SZ:159915",
    ),
) -> ShortBiasBatchOut:
    """Batch ~5min short-horizon bias from 1-minute intraday (momentum, not forecast)."""
    pairs: list[tuple[str, str]] = []
    for raw in keys.split(","):
        part = raw.strip()
        if not part:
            continue
        if ":" in part:
            mkt, sym = part.split(":", 1)
            pairs.append((sym.strip(), mkt.strip().upper() or "SH"))
        else:
            sym, mkt = normalize_symbol(part, "SH")
            pairs.append((sym, mkt))
    items = get_short_biases(pairs)
    return ShortBiasBatchOut(
        items=[
            ShortBiasOut(
                symbol=b.symbol,
                market=b.market,
                bias=b.bias,
                label=b.label,
                score=b.score,
                lookback_min=b.lookback_min,
                sample_n=b.sample_n,
                roc_pct=b.roc_pct,
                as_of=b.as_of,
            )
            for b in items
        ]
    )


@router.get("/depth-flow", response_model=DepthFlowOut)
def depth_flow(
    symbol: str = Query(..., min_length=1),
    market: str = Query(default="SH"),
    days: int = Query(default=5, ge=1, le=30),
) -> DepthFlowOut:
    """买卖五档 + 近几日资金流向（主力为成交额分档，非庄家）。"""
    sym, mkt = normalize_symbol(symbol, market)
    snap = get_depth_flow(sym, mkt, flow_days=days)
    book_out = None
    if snap.book:
        book_out = OrderBookOut(
            symbol=snap.book.symbol,
            market=snap.book.market,
            name=snap.book.name,
            bids=[BookLevelOut(price=x.price, volume=x.volume) for x in snap.book.bids],
            asks=[BookLevelOut(price=x.price, volume=x.volume) for x in snap.book.asks],
            as_of=snap.book.as_of,
            source=snap.book.source,
            live=snap.book.live,
        )
    return DepthFlowOut(
        symbol=snap.symbol,
        market=snap.market,
        name=snap.name,
        book=book_out,
        flow_days=[
            MoneyFlowDayOut(
                date=d.date,
                main_net=d.main_net,
                super_net=d.super_net,
                large_net=d.large_net,
                mid_net=d.mid_net,
                small_net=d.small_net,
                main_pct=d.main_pct,
            )
            for d in snap.flow_days
        ],
        flow_bias=snap.flow_bias,
        flow_label=snap.flow_label,
        session_state=snap.session_state,
        book_live=snap.book_live,
        note=snap.note,
    )


@router.get("/leaders", response_model=LeadersOut)
def leaders(
    key: str = Query(default="sh-composite"),
    kind: str = Query(default="up", pattern="^(up|down|amount|turnover|etf)$"),
    limit: int = Query(default=100, ge=1, le=100),
) -> LeadersOut:
    """Board list: 涨幅/跌幅/成交额/换手率/相关ETF."""
    title, board_kind, rows = get_leaders(key, kind=kind, limit=limit)
    return LeadersOut(
        key=key,
        kind=board_kind,
        title=title,
        items=[
            LeaderStockOut(
                symbol=r.symbol,
                name=r.name,
                market=r.market,
                price=r.price,
                change_pct=r.change_pct,
                amount=r.amount,
                turnover=r.turnover,
            )
            for r in rows
        ],
    )


@router.get("/search", response_model=SearchOut)
def search(
    q: str = Query(..., min_length=1, max_length=32),
    limit: int = Query(default=12, ge=1, le=20),
) -> SearchOut:
    """Suggest stocks / ETFs by code or name (East Money)."""
    hits = search_symbols(q, limit=limit)
    return SearchOut(
        query=q.strip(),
        items=[
            SearchHitOut(
                symbol=h.symbol,
                name=h.name,
                market=h.market,
                kind=h.kind,
                price=h.price,
                change_pct=h.change_pct,
            )
            for h in hits
        ],
    )


@router.get("/quote", response_model=QuoteOut)
def quote(
    symbol: str = Query(..., min_length=1),
    market: str = Query(default="SH"),
) -> QuoteOut:
    sym, mkt = normalize_symbol(symbol, market)
    q = get_quote(sym, mkt)
    return QuoteOut(
        symbol=q.symbol,
        name=q.name,
        market=q.market,
        price=q.price,
        change_pct=q.change_pct,
        prev_close=q.prev_close,
    )


@router.get("/watchlist", response_model=list[WatchlistOut])
def list_watchlist(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> list[WatchlistOut]:
    rows = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id)
        .order_by(WatchlistItem.id.asc())
        .all()
    )
    quotes = get_quotes([(r.symbol, r.market) for r in rows]) if rows else {}
    out: list[WatchlistOut] = []
    for r in rows:
        q = quotes.get(r.symbol)
        out.append(
            WatchlistOut(
                id=r.id,
                symbol=r.symbol,
                name=r.name or (q.name if q else r.symbol),
                market=r.market,
                created_at=r.created_at,
                last_price=q.price if q else None,
                change_pct=q.change_pct if q else None,
            )
        )
    return out


@router.post("/watchlist", response_model=WatchlistOut, status_code=201)
def add_watchlist(
    payload: WatchlistCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> WatchlistOut:
    symbol, market = normalize_symbol(payload.symbol, payload.market)
    existing = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.user_id == user.id, WatchlistItem.symbol == symbol)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Already in watchlist")

    name = payload.name
    q = None
    try:
        q = get_quote(symbol, market)
        name = name or q.name
    except Exception:
        name = name or symbol

    row = WatchlistItem(user_id=user.id, symbol=symbol, name=name, market=market)
    db.add(row)
    db.commit()
    db.refresh(row)
    return WatchlistOut(
        id=row.id,
        symbol=row.symbol,
        name=row.name,
        market=row.market,
        created_at=row.created_at,
        last_price=q.price if q else None,
        change_pct=q.change_pct if q else None,
    )


@router.delete("/watchlist/{item_id}", status_code=204)
def remove_watchlist(
    item_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> None:
    row = (
        db.query(WatchlistItem)
        .filter(WatchlistItem.id == item_id, WatchlistItem.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    db.delete(row)
    db.commit()
