export type Holding = {
  id: number;
  symbol: string;
  name: string;
  market: "SH" | "SZ";
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
  session?: "cn" | "us" | string;
  points: IntradayPoint[];
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
  market?: "SH" | "SZ";
  shares: number;
  cost: number;
  tags?: string;
  bought_at?: string;
};

export type HoldingUpdate = {
  symbol?: string;
  name?: string;
  market?: "SH" | "SZ";
  shares?: number;
  cost?: number;
  tags?: string;
  bought_at?: string;
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

export type AnalysisReport = {
  verdict: string;
  stance: string;
  confidence: number;
  highlights?: string[];
  items?: AnalysisSymbolSummary[];
  bullets?: string[];
  structure?: AnalysisStructureRow[];
  actions?: string[];
  agents?: AnalysisAgentStep[];
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
