from datetime import datetime

from pydantic import BaseModel, Field


class HoldingBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    name: str = ""
    market: str = Field(default="SH", pattern="^(SH|SZ|JD)$")
    shares: float = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    tags: str = ""
    bought_at: str = ""  # YYYY-MM-DD; empty → server uses Shanghai today


class HoldingCreate(HoldingBase):
    pass


class HoldingUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=16)
    name: str | None = None
    market: str | None = Field(default=None, pattern="^(SH|SZ|JD)$")
    shares: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    tags: str | None = None
    bought_at: str | None = None
    # Fill price / date for this shares delta — 今日盈亏今买/今卖成交额
    trade_price: float | None = Field(default=None, gt=0)
    trade_date: str | None = None  # YYYY-MM-DD of this fill; empty → Shanghai today


class HoldingOut(HoldingBase):
    id: int
    bought_at: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    market_value: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    day_pnl: float | None = None
    day_pnl_pct: float | None = None
    weight: float | None = None

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    total_cost: float
    total_market_value: float
    total_pnl: float
    total_pnl_pct: float
    day_pnl: float = 0.0
    day_pnl_pct: float = 0.0
    holdings: list[HoldingOut]


class PortfolioReturnsBucket(BaseModel):
    key: str
    label: str
    pnl: float
    pnl_pct: float
    market_value: float = 0.0
    source: str = "live"


class PortfolioReturnsSummary(BaseModel):
    dim: str  # day | month | year
    ref: str  # YYYY-MM-DD anchor
    label: str
    pnl: float
    pnl_pct: float
    end_market_value: float = 0.0
    trading_days: int = 0
    has_estimated: bool = False
    note: str = ""
    prev_ref: str
    next_ref: str | None = None
    buckets: list[PortfolioReturnsBucket] = []


class WatchlistCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    name: str = ""
    market: str = Field(default="SH", pattern="^(SH|SZ)$")


class WatchlistOut(WatchlistCreate):
    id: int
    created_at: datetime | None = None
    last_price: float | None = None
    change_pct: float | None = None

    model_config = {"from_attributes": True}


class QuoteOut(BaseModel):
    symbol: str
    name: str
    market: str
    price: float
    change_pct: float | None = None
    prev_close: float | None = None


class IndexQuoteOut(QuoteOut):
    """Major index quote for market dashboards."""

    key: str = ""  # e.g. sh-composite


class MacroQuoteOut(BaseModel):
    key: str
    name: str
    price: float
    unit: str = ""
    change_pct: float | None = None
    prev: float | None = None
    as_of: str | None = None
    live: bool = True
    venue: str = ""
    freshness: str = ""


class MacroTopicOut(BaseModel):
    topic: str
    calendar: str = ""
    quotes: list[MacroQuoteOut] = []
    hint: str = ""
    note: str = ""


class GoldEtfOut(BaseModel):
    symbol: str
    market: str
    name: str
    price: float
    change_pct: float | None = None
    prev_close: float | None = None


class GoldBoardItemOut(BaseModel):
    id: str
    name: str
    section: str
    price: float | None = None
    change_pct: float | None = None
    prev: float | None = None
    unit: str = "元/克"
    freshness: str = ""
    note: str = ""
    holdable: bool = False
    symbol: str = ""
    market: str = ""
    chart: list[float] = []
    chart_times: list[str] = []
    chart_slots: int = 0
    chart_session: str = ""


class GoldBoardSectionOut(BaseModel):
    id: str
    title: str
    subtitle: str = ""
    items: list[GoldBoardItemOut] = []


class GoldBoardOut(BaseModel):
    sections: list[GoldBoardSectionOut] = []
    note: str = ""


class IntradayPointOut(BaseModel):
    time: str
    price: float
    avg: float | None = None


class IntradayOut(BaseModel):
    key: str
    symbol: str
    name: str
    market: str
    prev_close: float | None = None
    open_price: float | None = None
    session: str = "cn"
    points: list[IntradayPointOut]


class ShortBiasOut(BaseModel):
    """~5min micro-momentum from 1m intraday — tendency, not a forecast."""

    symbol: str
    market: str
    bias: str  # up | down | flat | na | closed
    label: str
    score: float | None = None
    lookback_min: int = 5
    sample_n: int = 0
    roc_pct: float | None = None
    as_of: str | None = None


class ShortBiasBatchOut(BaseModel):
    items: list[ShortBiasOut]
    note: str = "近5根分时动量倾向，非预测、非投资建议"


class BookLevelOut(BaseModel):
    price: float
    volume: float  # 手


class OrderBookOut(BaseModel):
    symbol: str
    market: str
    name: str
    bids: list[BookLevelOut]
    asks: list[BookLevelOut]
    as_of: str | None = None
    source: str = ""
    live: bool = False


class MoneyFlowDayOut(BaseModel):
    date: str
    main_net: float
    super_net: float
    large_net: float
    mid_net: float
    small_net: float
    main_pct: float | None = None


class DepthFlowOut(BaseModel):
    symbol: str
    market: str
    name: str
    book: OrderBookOut | None = None
    flow_days: list[MoneyFlowDayOut] = []
    flow_bias: str = "na"  # in | out | flat | na
    flow_label: str = ""
    session_state: str = "closed"
    book_live: bool = False
    note: str = "主力按成交额分档，非庄家身份；非投资建议"


class LeaderStockOut(BaseModel):
    symbol: str
    name: str
    market: str
    price: float
    change_pct: float | None = None
    amount: float | None = None
    turnover: float | None = None


class LeadersOut(BaseModel):
    key: str
    kind: str = "up"
    title: str
    items: list[LeaderStockOut]


class SessionOut(BaseModel):
    market: str
    state: str
    label: str
    detail: str


class SearchHitOut(BaseModel):
    symbol: str
    name: str
    market: str
    kind: str = "stock"  # stock | etf | index | us
    price: float | None = None
    change_pct: float | None = None


class SearchOut(BaseModel):
    query: str
    items: list[SearchHitOut]


class NewsItemOut(BaseModel):
    id: str
    title: str
    summary: str = ""
    source: str = ""
    published_at: str = ""
    url: str = ""
    symbols: list[str] = []


class NewsFeedOut(BaseModel):
    kind: str  # market | holdings
    title: str
    board: str = ""
    items: list[NewsItemOut]


class NewsBoardOut(BaseModel):
    id: str
    label: str


class NewsBoardsOut(BaseModel):
    items: list[NewsBoardOut]


class NewsArticleOut(BaseModel):
    id: str
    title: str
    body: str = ""
    source: str = ""
    published_at: str = ""
    url: str = ""
    images: list[str] = []


class NewsInterestCreate(BaseModel):
    keyword: str = Field(..., min_length=1, max_length=16)


class NewsInterestOut(BaseModel):
    id: int
    keyword: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class NewsInterestsOut(BaseModel):
    items: list[NewsInterestOut]


class HealthOut(BaseModel):
    status: str
    app: str
    quote_provider: str


# --- Analysis (multi-agent P0) ---


class AnalysisProfileOut(BaseModel):
    degree: str
    degree_label: str = ""
    blurb: str = ""
    default_recipe: str = ""
    updated_at: datetime | None = None


class AnalysisProfileUpdate(BaseModel):
    degree: str = Field(..., pattern="^(light|standard|deep)$")


class AnalysisDegreeOut(BaseModel):
    id: str
    label: str
    default_recipe: str
    evidence_tier: str
    blurb: str


class AnalysisModeOut(BaseModel):
    id: str
    label: str
    question: str
    default_recipe: str
    default_recipe_label: str = ""


class AnalysisRecipeOut(BaseModel):
    id: str
    label: str
    agents: list[str]
    weights: dict[str, float]
    evidence_tier: str
    modes: list[str] = []
    agent_labels: dict[str, str] = {}


class AnalysisCatalogOut(BaseModel):
    degrees: list[AnalysisDegreeOut]
    modes: list[AnalysisModeOut]
    recipes: list[AnalysisRecipeOut]


class AnalysisSymbolIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    market: str = Field(default="SH", pattern="^(SH|SZ)$")
    name: str = ""


class AnalysisJobCreate(BaseModel):
    scope: str = Field(..., pattern="^(portfolio|symbol)$")
    symbols: list[AnalysisSymbolIn] = []
    recipe_id: str | None = None
    degree: str | None = Field(default=None, pattern="^(light|standard|deep)$")
    mode: str | None = None


class AnalysisAgentStepOut(BaseModel):
    id: str
    label: str
    status: str  # pending | running | done | failed | skipped
    summary: str = ""
    stance: str = "中性"
    confidence: float = 0.5
    bullets: list[str] = []
    weight: float | None = None


class AnalysisSymbolSummaryOut(BaseModel):
    symbol: str
    name: str = ""
    market: str = "SH"
    stance: str = "中性"
    change_pct: float | None = None
    weight: float | None = None
    summary: str = ""


class AnalysisDebateRoundOut(BaseModel):
    round: int = 1
    summary: str = ""
    stance: str = "中性"
    bull_points: list[str] = []
    bear_points: list[str] = []
    open_questions: list[str] = []
    bullets: list[str] = []


class AnalysisReportOut(BaseModel):
    verdict: str
    stance: str = "中性"
    confidence: float = 0.5
    highlights: list[str] = []  # ≤2 overall key points
    watch: list[str] = []  # stocks / points to watch after overall verdict
    holding_lines: list[str] = []  # evidence one-liners for current holdings
    items: list[AnalysisSymbolSummaryOut] = []  # per-symbol briefs
    # retained for P1 / older clients; UI no longer surfaces these
    bullets: list[str] = []
    structure: list[dict] = []
    actions: list[str] = []
    agents: list[AnalysisAgentStepOut] = []
    debate: list[AnalysisDebateRoundOut] = []
    template: bool = False


class AnalysisJobOut(BaseModel):
    id: int
    scope: str
    symbols: list[dict] = []
    recipe_id: str
    degree: str
    status: str
    error: str = ""
    report: AnalysisReportOut | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


# --- Auth / users ---


class AuthStatusOut(BaseModel):
    has_users: bool


class AuthLoginIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)


class AuthBootstrapIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)
    identity_role: str = Field(..., min_length=1, max_length=32)
    identity_label: str = Field(default="", max_length=16)


class AuthRegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)
    identity_role: str = Field(..., min_length=1, max_length=32)
    identity_label: str = Field(default="", max_length=16)


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class AuthTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)
    role: str = Field(default="user", pattern="^(admin|user)$")


class UserPasswordIn(BaseModel):
    password: str = Field(..., min_length=4, max_length=128)
