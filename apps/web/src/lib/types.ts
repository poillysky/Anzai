export type Holding = {
  id: number;
  symbol: string;
  name: string;
  market: "SH" | "SZ" | "JD";
  shares: number;
  cost: number;
  tags: string;
  /** YYYY-MM-DD first buy / position start */
  bought_at?: string;
  last_price?: number | null;
  prev_close?: number | null;
  change_pct?: number | null;
  market_value?: number | null;
  pnl?: number | null;
  pnl_pct?: number | null;
  day_pnl?: number | null;
  day_pnl_pct?: number | null;
  weight?: number | null;
};

export type PortfolioSummary = {
  total_cost: number;
  total_market_value: number;
  total_pnl: number;
  total_pnl_pct: number;
  day_pnl?: number;
  day_pnl_pct?: number;
  holdings: Holding[];
};

export type PortfolioReturnsDim = "day" | "month" | "year";

export type PortfolioReturnsBucket = {
  key: string;
  label: string;
  pnl: number;
  pnl_pct: number;
  market_value?: number;
  source?: string;
};

export type PortfolioReturnsSummary = {
  dim: PortfolioReturnsDim;
  ref: string;
  label: string;
  pnl: number;
  pnl_pct: number;
  end_market_value?: number;
  trading_days?: number;
  has_estimated?: boolean;
  note?: string;
  prev_ref: string;
  next_ref?: string | null;
  buckets: PortfolioReturnsBucket[];
};

export type WatchlistItem = {
  id: number;
  symbol: string;
  name: string;
  market: "SH" | "SZ";
  last_price?: number | null;
  change_pct?: number | null;
};

export type IndexQuote = {
  key: string;
  symbol: string;
  name: string;
  market: "SH" | "SZ" | "US" | "HK";
  price: number;
  change_pct?: number | null;
  prev_close?: number | null;
};

export type MacroQuote = {
  key: string;
  name: string;
  price: number;
  unit?: string;
  change_pct?: number | null;
  prev?: number | null;
  as_of?: string | null;
  live?: boolean;
  venue?: string;
  freshness?: string;
};

export type MacroTopic = {
  topic: string;
  calendar?: string;
  quotes: MacroQuote[];
  hint?: string;
  note?: string;
};

export type GoldEtf = {
  symbol: string;
  market: string;
  name: string;
  price: number;
  change_pct?: number | null;
  prev_close?: number | null;
};

export type GoldBoardItem = {
  id: string;
  name: string;
  section: string;
  price?: number | null;
  change_pct?: number | null;
  prev?: number | null;
  unit?: string;
  freshness?: string;
  note?: string;
  holdable?: boolean;
  symbol?: string;
  market?: string;
  chart?: number[];
  /** HH:mm for each chart point (AU9999); omit → synthesize by session */
  chart_times?: string[];
  /** Full-session slot count (JD pointCount); partial chart maps into this span. */
  chart_slots?: number;
  /** cn | us | day24 | comex */
  chart_session?: string;
};

export type GoldBoardSection = {
  id: string;
  title: string;
  subtitle?: string;
  items: GoldBoardItem[];
};

export type GoldBoard = {
  sections: GoldBoardSection[];
  note?: string;
};

export type IntradayPoint = {
  time: string;
  price: number;
  avg?: number | null;
};

export type IntradaySeries = {
  key: string;
  symbol: string;
  name: string;
  market: string;
  prev_close?: number | null;
  /** 今开 — stock yellow reference line */
  open_price?: number | null;
  session?: "cn" | "us" | "hk" | "day24" | string;
  points: IntradayPoint[];
};

/** ~5min micro-momentum from 1m bars — tendency, not a forecast */
export type ShortBias = {
  symbol: string;
  market: string;
  bias: "up" | "down" | "flat" | "na" | "closed" | string;
  label: string;
  score?: number | null;
  lookback_min?: number;
  sample_n?: number;
  roc_pct?: number | null;
  as_of?: string | null;
};

export type ShortBiasBatch = {
  items: ShortBias[];
  note?: string;
};

export type BookLevel = {
  price: number;
  volume: number;
};

export type OrderBook = {
  symbol: string;
  market: string;
  name: string;
  bids: BookLevel[];
  asks: BookLevel[];
  as_of?: string | null;
  source?: string;
  live?: boolean;
};

export type MoneyFlowDay = {
  date: string;
  main_net: number;
  super_net: number;
  large_net: number;
  mid_net: number;
  small_net: number;
  main_pct?: number | null;
};

export type DepthFlow = {
  symbol: string;
  market: string;
  name: string;
  book?: OrderBook | null;
  flow_days: MoneyFlowDay[];
  flow_bias: "in" | "out" | "flat" | "na" | string;
  flow_label: string;
  session_state?: string;
  book_live?: boolean;
  note?: string;
};

export type LeaderStock = {
  symbol: string;
  name: string;
  market: string;
  price: number;
  change_pct?: number | null;
  amount?: number | null;
  turnover?: number | null;
};

export type LeadersBoard = {
  key: string;
  kind: "up" | "down" | "amount" | "turnover" | "etf" | string;
  title: string;
  items: LeaderStock[];
};

export type MarketSession = {
  market: string;
  state: string;
  label: string;
  detail: string;
};

export type SearchHit = {
  symbol: string;
  name: string;
  market: string;
  kind: "stock" | "etf" | "index" | "us" | string;
  price?: number | null;
  change_pct?: number | null;
};

export type SearchResult = {
  query: string;
  items: SearchHit[];
};

export type NewsItem = {
  id: string;
  title: string;
  summary: string;
  source: string;
  published_at: string;
  url: string;
  symbols: string[];
};

export type NewsFeed = {
  kind: "market" | "holdings" | "interests" | string;
  title: string;
  board?: string;
  items: NewsItem[];
};

export type NewsBoard = {
  id: string;
  label: string;
};

export type NewsInterest = {
  id: number;
  keyword: string;
  created_at?: string | null;
};

/** PWA「你是安崽的谁」— per-user chat relationship */
export type AnzaiIdentityRole = {
  id: string;
  label: string;
  call_as: string;
};

export type AnzaiIdentity = {
  role: string;
  label: string;
  call_as: string;
  configured: boolean;
  relation_prompt: string;
  roles: AnzaiIdentityRole[];
};

export type NotifyChannelId = "serverchan" | "pushplus" | "wxpusher" | string;

export type NotifySettings = {
  enabled: boolean;
  channel: NotifyChannelId;
  token_set: boolean;
  token_preview: string;
  wxpusher_uid: string;
  hour: number;
  minute: number;
  weekdays: string;
  degree: string;
  configured: boolean;
  channels: { id: string; label: string }[];
  degrees: { id: string; label: string }[];
};

export type NotifyRunResult = {
  ok: boolean;
  skipped?: boolean;
  reason?: string;
  channel?: string;
  detail?: string;
  job_id?: number | null;
  title?: string;
  content?: string;
  content_preview?: string;
  dry_run?: boolean;
};

export type NewsArticle = {
  id: string;
  title: string;
  body: string;
  source: string;
  published_at: string;
  url: string;
  images?: string[];
};

export type HoldingCreate = {
  symbol: string;
  name?: string;
  market?: "SH" | "SZ" | "JD";
  shares: number;
  cost: number;
  tags?: string;
  bought_at?: string;
};

export type HoldingUpdate = {
  symbol?: string;
  name?: string;
  market?: "SH" | "SZ" | "JD";
  shares?: number;
  cost?: number;
  tags?: string;
  bought_at?: string;
  /** Fill price for shares delta — 今日盈亏今买/今卖成交额 */
  trade_price?: number;
  /** YYYY-MM-DD of this fill; omit → today. Past date → 昨仓进日初仓 */
  trade_date?: string;
};

export type AnalysisDegree = {
  id: "light" | "standard" | "deep" | string;
  label: string;
  default_recipe: string;
  evidence_tier: string;
  blurb: string;
};

export type AnalysisMode = {
  id: string;
  label: string;
  question: string;
  default_recipe: string;
  default_recipe_label?: string;
};

export type AnalysisRecipe = {
  id: string;
  label: string;
  agents: string[];
  weights: Record<string, number>;
  evidence_tier: string;
  modes: string[];
  agent_labels?: Record<string, string>;
};

export type AnalysisCatalog = {
  degrees: AnalysisDegree[];
  modes: AnalysisMode[];
  recipes: AnalysisRecipe[];
};

export type AnalysisProfile = {
  degree: string;
  degree_label: string;
  blurb: string;
  default_recipe: string;
  updated_at?: string | null;
};

export type AnalysisAgentStep = {
  id: string;
  label: string;
  status: string;
  summary: string;
  stance: string;
  confidence: number;
  bullets: string[];
  weight?: number | null;
};

export type AnalysisSymbolSummary = {
  symbol: string;
  name: string;
  market?: string;
  stance: string;
  change_pct?: number | null;
  weight?: number | null;
  summary: string;
};

export type AnalysisDebateRound = {
  round: number;
  summary?: string;
  stance?: string;
  bull_points?: string[];
  bear_points?: string[];
  open_questions?: string[];
  bullets?: string[];
};

export type AnalysisReport = {
  verdict: string;
  stance: string;
  confidence: number;
  highlights?: string[];
  /** Plain sentences: which stocks need attention after overall verdict */
  watch?: string[];
  /** Evidence citations aligned with watch[] */
  watch_refs?: string[];
  open_resolutions?: string[];
  unresolved?: string[];
  /** Evidence-backed one-liners for current holdings */
  holding_lines?: string[];
  items?: AnalysisSymbolSummary[];
  bullets?: string[];
  structure?: AnalysisStructureRow[];
  actions?: string[];
  agents?: AnalysisAgentStep[];
  debate?: AnalysisDebateRound[];
  template?: boolean;
};

export type AnalysisStructureRow = {
  name: string;
  symbol?: string;
  weight?: number | null;
  change_pct?: number | null;
};

export type AnalysisJob = {
  id: number;
  scope: "portfolio" | "symbol" | string;
  symbols: Array<{ symbol: string; market: string; name?: string }>;
  recipe_id: string;
  degree: string;
  status: string;
  error?: string;
  report?: AnalysisReport | null;
  created_at?: string | null;
  finished_at?: string | null;
};

export type AnalysisJobCreate = {
  scope: "portfolio" | "symbol";
  symbols?: Array<{ symbol: string; market: "SH" | "SZ" | string; name?: string }>;
  recipe_id?: string;
  degree?: "light" | "standard" | "deep" | string;
  mode?: string;
};
