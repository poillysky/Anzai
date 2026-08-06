"""News feeds: market / holdings / interests."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.models import Holding, NewsInterest
from app.providers.news import (
    MARKET_BOARDS,
    get_article,
    get_holdings_news,
    get_interests_news,
    get_market_news,
    list_market_boards,
)
from app.schemas import (
    NewsArticleOut,
    NewsBoardOut,
    NewsBoardsOut,
    NewsFeedOut,
    NewsInterestCreate,
    NewsInterestOut,
    NewsInterestsOut,
    NewsItemOut,
)

router = APIRouter(prefix="/news", tags=["news"], dependencies=[Depends(require_user)])

_BOARD_IDS = {b["id"] for b in MARKET_BOARDS}
_INTEREST_CAP = 8


def _to_out(items) -> list[NewsItemOut]:
    return [
        NewsItemOut(
            id=i.id,
            title=i.title,
            summary=i.summary,
            source=i.source,
            published_at=i.published_at,
            url=i.url,
            symbols=list(i.symbols),
        )
        for i in items
    ]


def _normalize_keyword(raw: str) -> str:
    return " ".join((raw or "").strip().split())


@router.get("/boards", response_model=NewsBoardsOut)
def market_boards() -> NewsBoardsOut:
    return NewsBoardsOut(
        items=[NewsBoardOut(id=b["id"], label=b["label"]) for b in list_market_boards()]
    )


@router.get("/market", response_model=NewsFeedOut)
def market_news(
    limit: int = Query(default=100, ge=1, le=100),
    board: str = Query(default="headline", max_length=32),
) -> NewsFeedOut:
    board_id = board if board in _BOARD_IDS else "headline"
    title, items = get_market_news(limit=limit, board=board_id)
    return NewsFeedOut(
        kind="market",
        title=title,
        board=board_id,
        items=_to_out(items),
    )


@router.get("/holdings", response_model=NewsFeedOut)
def holdings_news(
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NewsFeedOut:
    rows = (
        db.query(Holding.symbol)
        .filter(Holding.user_id == user.id)
        .order_by(Holding.id.asc())
        .all()
    )
    symbols = [str(r[0]) for r in rows if r and r[0]]
    items = get_holdings_news(symbols, limit=limit)
    return NewsFeedOut(kind="holdings", title="持仓相关", board="", items=_to_out(items))


@router.get("/interests", response_model=NewsInterestsOut)
def list_interests(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NewsInterestsOut:
    rows = (
        db.query(NewsInterest)
        .filter(NewsInterest.user_id == user.id)
        .order_by(NewsInterest.id.asc())
        .all()
    )
    return NewsInterestsOut(items=[NewsInterestOut.model_validate(r) for r in rows])


@router.get("/interests/feed", response_model=NewsFeedOut)
def interests_feed(
    limit: int = Query(default=100, ge=1, le=100),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NewsFeedOut:
    rows = (
        db.query(NewsInterest.keyword)
        .filter(NewsInterest.user_id == user.id)
        .order_by(NewsInterest.id.asc())
        .all()
    )
    keywords = [str(r[0]) for r in rows if r and r[0]]
    items = get_interests_news(keywords, limit=limit)
    return NewsFeedOut(kind="interests", title="我的兴趣", board="", items=_to_out(items))


@router.post("/interests", response_model=NewsInterestOut)
def add_interest(
    body: NewsInterestCreate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NewsInterestOut:
    kw = _normalize_keyword(body.keyword)
    if not kw:
        raise HTTPException(status_code=400, detail="Keyword required")
    if len(kw) > 16:
        raise HTTPException(status_code=400, detail="Keyword too long")

    count = db.query(NewsInterest).filter(NewsInterest.user_id == user.id).count()
    if count >= _INTEREST_CAP:
        raise HTTPException(status_code=400, detail=f"最多 {_INTEREST_CAP} 个兴趣词")

    existing = (
        db.query(NewsInterest)
        .filter(NewsInterest.user_id == user.id, NewsInterest.keyword == kw)
        .first()
    )
    if existing:
        return NewsInterestOut.model_validate(existing)

    row = NewsInterest(user_id=user.id, keyword=kw)
    db.add(row)
    db.commit()
    db.refresh(row)
    return NewsInterestOut.model_validate(row)


@router.delete("/interests/{interest_id}", status_code=204)
def remove_interest(
    interest_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> None:
    row = (
        db.query(NewsInterest)
        .filter(NewsInterest.id == interest_id, NewsInterest.user_id == user.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Interest not found")
    db.delete(row)
    db.commit()


@router.get("/article", response_model=NewsArticleOut)
def article_detail(
    id: str = Query(..., min_length=4, max_length=512, description="文章 code 或原文链接"),
) -> NewsArticleOut:
    """In-app reader payload — EM / Sina / 同花顺 plain text (+ images when found)."""
    art = get_article(id)
    if art is None:
        raise HTTPException(status_code=404, detail="Article not found")
    return NewsArticleOut(
        id=art.id,
        title=art.title,
        body=art.body,
        source=art.source,
        published_at=art.published_at,
        url=art.url,
        images=list(art.images or []),
    )
