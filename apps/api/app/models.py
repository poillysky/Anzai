from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | user
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Holding(Base):
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    market: Mapped[str] = mapped_column(String(8), default="SH")  # SH / SZ / JD（积存金）
    shares: Mapped[float] = mapped_column(Float, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0)
    tags: Mapped[str] = mapped_column(String(128), default="")
    # First buy / position start — YYYY-MM-DD; default Shanghai today on create
    bought_at: Mapped[str] = mapped_column(String(10), default="")
    # Same-day top-up lot (for 今日盈亏 split); reset when day_buy_asof ≠ Shanghai today
    day_buy_shares: Mapped[float] = mapped_column(Float, default=0.0)
    day_buy_cost: Mapped[float] = mapped_column(Float, default=0.0)
    day_buy_asof: Mapped[str] = mapped_column(String(10), default="")
    # Broker cash-flow day P&L: SOD shares + today's buy/sell notional
    sod_shares: Mapped[float] = mapped_column(Float, default=0.0)
    sod_asof: Mapped[str] = mapped_column(String(10), default="")
    day_buy_amount: Mapped[float] = mapped_column(Float, default=0.0)
    day_sell_amount: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    market: Mapped[str] = mapped_column(String(8), default="SH")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NewsInterest(Base):
    """User-curated news keywords for the 兴趣 feed."""

    __tablename__ = "news_interests"
    __table_args__ = (UniqueConstraint("user_id", "keyword", name="uq_interest_user_keyword"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    keyword: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserPreference(Base):
    __tablename__ = "preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, default=0)
    risk_level: Mapped[str] = mapped_column(String(32), default="moderate")
    target_allocation: Mapped[str] = mapped_column(Text, default="{}")  # JSON string
    notes: Mapped[str] = mapped_column(Text, default="")
    # PWA「你是安崽的谁」— drives chat tone (dad → 跟爸爸说话)
    identity_role: Mapped[str] = mapped_column(String(32), default="")
    identity_label: Mapped[str] = mapped_column(String(32), default="")
    # WeChat digest (Server酱 / PushPlus / WxPusher) — per-user JSON
    notify_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisProfile(Base):
    """Per-user warehouse analysis degree."""

    __tablename__ = "analysis_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, default=0)
    degree: Mapped[str] = mapped_column(String(16), default="standard")  # light|standard|deep
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisJob(Base):
    """One analysis run: portfolio巡检 or symbol深度."""

    __tablename__ = "analysis_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    scope: Mapped[str] = mapped_column(String(16), index=True)  # portfolio | symbol
    symbols_json: Mapped[str] = mapped_column(Text, default="[]")
    recipe_id: Mapped[str] = mapped_column(String(32), default="balanced")
    degree: Mapped[str] = mapped_column(String(16), default="standard")
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    report_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentConversation(Base):
    """安崽对话会话（可关闭、可新开；消息挂 conversation_id）。"""

    __tablename__ = "agent_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    title: Mapped[str] = mapped_column(String(64), default="新对话")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(Base):
    """Per-user 安崽 chat messages (scoped by conversation)."""

    __tablename__ = "agent_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    conversation_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(16), index=True)  # user | assistant
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PortfolioDailySnapshot(Base):
    """One row per user per Shanghai trade calendar day — for day/month/year P&L rolls."""

    __tablename__ = "portfolio_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("user_id", "trade_date", name="uq_portfolio_snap_user_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True, default=0)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    total_market_value: Mapped[float] = mapped_column(Float, default=0)
    total_cost: Mapped[float] = mapped_column(Float, default=0)
    day_pnl: Mapped[float] = mapped_column(Float, default=0)
    day_pnl_pct: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(16), default="live")  # live | estimated
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
