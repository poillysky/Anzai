from datetime import datetime

from pydantic import BaseModel, Field


class HoldingBase(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    name: str = ""
    market: str = Field(default="SH", pattern="^(SH|SZ)$")
    shares: float = Field(default=0, ge=0)
    cost: float = Field(default=0, ge=0)
    tags: str = ""
    bought_at: str = ""  # YYYY-MM-DD; empty → server uses Shanghai today


class HoldingCreate(HoldingBase):
    pass


class HoldingUpdate(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=16)
    name: str | None = None
    market: str | None = Field(default=None, pattern="^(SH|SZ)$")
    shares: float | None = Field(default=None, ge=0)
    cost: float | None = Field(default=None, ge=0)
    tags: str | None = None
    bought_at: str | None = None


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
    session: str = "cn"
    points: list[IntradayPointOut]


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


class AnalysisReportOut(BaseModel):
    verdict: str
    stance: str = "中性"
    confidence: float = 0.5
    highlights: list[str] = []  # ≤2 overall key points
    items: list[AnalysisSymbolSummaryOut] = []  # per-symbol briefs
    # retained for P1 / older clients; UI no longer surfaces these
    bullets: list[str] = []
    structure: list[dict] = []
    actions: list[str] = []
    agents: list[AnalysisAgentStepOut] = []
    template: bool = True


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


class AuthRegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=4, max_length=128)


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
