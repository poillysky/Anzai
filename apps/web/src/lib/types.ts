export type Holding = {
  id: number;
  symbol: string;
  name: string;
  market: "SH" | "SZ" | "JD" | "OF";
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

/** Fund board item — ETF (live) or OTC open-end (daily NAV). */
export type FundBoardItem = GoldBoardItem & {
  kind?: "etf" | "otc" | string;
};

export type FundBoardGroup = {
  id: string;
  title: string;
  items: FundBoardItem[];
};

export type FundBoardSection = {
  id: string;
  title: string;
  subtitle?: string;
  items: FundBoardItem[];
  /** Present on 行业：煤炭 / 贵金属 / 电子… */
  groups?: FundBoardGroup[];
};

export type FundBoard = {
  sections: FundBoardSection[];
  note?: string;
};

export type FundSearchHit = {
  symbol: string;
  name: string;
  market: string;
  kind: string;
  price?: number | null;
  change_pct?: number | null;
  as_of?: string;
  fund_type?: string;
};

export type FundSearchResult = {
  query: string;
  items: FundSearchHit[];
};

export type IntradayPoint = {
  time: string;
  price: number;
  avg?: number | null;
};

export type FundNavHistory = {
  symbol: string;
  name?: string;
  as_of?: string;
  nav?: number | null;
  change_pct?: number | null;
  points: IntradayPoint[];
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
  /** 完整多周期说明（备用） */
  detail?: string | null;
  /** 卡片短文，如「偏涨暂稳」「偏跌中抬头」 */
  summary?: string | null;
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
  prev_close?: number | null;
  amount?: number | null;
  turnover?: number | null;
  /** search/ipo: stock | etf | ipo … */
  kind?: string | null;
  note?: string | null;
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
  kind: "stock" | "etf" | "index" | "us" | "ipo" | string;
  price?: number | null;
  change_pct?: number | null;
  /** 新股待上市等补充说明 */
  note?: string | null;
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
  region?: string;
};

export type NewsFeed = {
  kind: "market" | "holdings" | "interests" | string;
  title: string;
  board?: string;
  items: NewsItem[];
  note?: string;
};

export type NewsBoard = {
  id: string;
  label: string;
};

export type NewsMacroPulseItem = {
  key: string;
  name: string;
  price: number;
  unit?: string;
  change_pct?: number | null;
  freshness?: string;
};

export type NewsMacroPulse = {
  as_of: string;
  weekday: string;
  session_hint: string;
  calendar: string;
  items: NewsMacroPulseItem[];
  note?: string;
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
  market?: "SH" | "SZ" | "JD" | "OF";
  shares: number;
  cost: number;
  tags?: string;
  bought_at?: string;
};

export type HoldingUpdate = {
  symbol?: string;
  name?: string;
  market?: "SH" | "SZ" | "JD" | "OF";
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

export type AnalysisRebalance = {
  kind?: "rebalance" | string;
  empty?: boolean;
  stance?: string;
  day_pnl_pct?: number | null;
  head?: { symbol: string; name: string; weight?: number } | null;
  notes?: string[];
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
  /** Deterministic 调仓草案（仓库巡检） */
  rebalance?: AnalysisRebalance | null;
  template?: boolean;
  /** 部分席位失败或模板兜底 */
  degraded?: boolean;
  failed_seats?: string[];
  quality_note?: string;
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
