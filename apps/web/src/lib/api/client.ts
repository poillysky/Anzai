import type {
  AnalysisCatalog,
  AnalysisJob,
  AnalysisJobCreate,
  AnalysisProfile,
  AnalysisRecipe,
  HoldingCreate,
  HoldingUpdate,
  IndexQuote,
  IntradaySeries,
  FundBoard,
  FundNavHistory,
  FundSearchResult,
  GoldBoard,
  GoldEtf,
  LeadersBoard,
  MacroTopic,
  MarketSession,
  NewsFeed,
  NewsBoard,
  NewsMacroPulse,
  NewsInterest,
  NewsArticle,
  AnzaiIdentity,
  NotifySettings,
  NotifyRunResult,
  PortfolioReturnsDim,
  PortfolioReturnsSummary,
  PortfolioSummary,
  SearchResult,
  ShortBiasBatch,
  DepthFlow,
  WatchlistItem,
} from "@/lib/types";
import {
  clearSession,
  getAccessToken,
  type AuthTokenResponse,
  type AuthUser,
} from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/backend";

function headers(includeAuth = true): HeadersInit {
  const h: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (includeAuth) {
    const token = getAccessToken();
    if (token) {
      h.Authorization = `Bearer ${token}`;
    }
  }
  return h;
}

async function request<T>(
  path: string,
  init?: RequestInit & { auth?: boolean },
): Promise<T> {
  const includeAuth = init?.auth !== false;
  const { auth: _auth, ...rest } = init || {};
  const res = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: {
      ...headers(includeAuth),
      ...(rest.headers || {}),
    },
    cache: "no-store",
  });
  if (res.status === 401 && includeAuth && typeof window !== "undefined") {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export type AnalysisStreamEvent = {
  type: string;
  label?: string;
  pct?: number;
  stage?: string;
  message?: string;
  status?: string;
  job_id?: number;
  job?: AnalysisJob;
  report?: AnalysisJob["report"];
  agent?: import("@/lib/types").AnalysisAgentStep;
  [k: string]: unknown;
};

export type AnalysisStreamEventHandler = (ev: AnalysisStreamEvent) => void;

async function _consumeAnalysisSse(
  res: Response,
  onEvent: AnalysisStreamEventHandler,
): Promise<void> {
  if (res.status === 401 && typeof window !== "undefined") {
    clearSession();
    if (!window.location.pathname.startsWith("/login")) {
      window.location.assign("/login");
    }
    throw new Error("未登录");
  }
  if (!res.ok || !res.body) {
    const text = await res.text();
    let message = text || `Request failed: ${res.status}`;
    try {
      const j = JSON.parse(text) as { detail?: string; message?: string };
      message = j.message || j.detail || message;
    } catch {
      /* plain */
    }
    onEvent({ type: "error", message });
    onEvent({ type: "done", status: "failed" });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  const flushLine = (line: string) => {
    const trimmed = line.trimEnd();
    if (!trimmed || trimmed.startsWith(":")) return;
    if (!trimmed.startsWith("data:")) return;
    const raw = trimmed.slice(5).trim();
    if (!raw || raw === "[DONE]") {
      if (raw === "[DONE]") onEvent({ type: "done" });
      return;
    }
    try {
      onEvent(JSON.parse(raw) as AnalysisStreamEvent);
    } catch {
      /* ignore partial / malformed */
    }
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const parts = buf.split(/\r?\n/);
    buf = parts.pop() || "";
    for (const line of parts) flushLine(line);
  }
  if (buf.trim()) flushLine(buf);
}

/** Domain API client — all backend calls go through here. */
export const api = {
  health: () => request<{ status: string; app: string }>("/health", { auth: false }),

  authStatus: (init?: { signal?: AbortSignal }) =>
    request<{ has_users: boolean }>("/api/auth/status", {
      auth: false,
      signal: init?.signal,
    }),
  bootstrap: (username: string, password: string, identityRole: string, identityLabel = "") =>
    request<AuthTokenResponse>("/api/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({
        username,
        password,
        identity_role: identityRole,
        identity_label: identityLabel,
      }),
      auth: false,
    }),
  register: (username: string, password: string, identityRole: string, identityLabel = "") =>
    request<AuthTokenResponse>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({
        username,
        password,
        identity_role: identityRole,
        identity_label: identityLabel,
      }),
      auth: false,
    }),
  login: (username: string, password: string) =>
    request<AuthTokenResponse>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
      auth: false,
    }),
  me: () => request<AuthUser>("/api/auth/me"),
  logout: () => request<{ status: string }>("/api/auth/logout", { method: "POST" }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ status: string }>("/api/me/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: currentPassword,
        new_password: newPassword,
      }),
    }),

  getPortfolio: () => request<PortfolioSummary>("/api/holdings"),
  getPortfolioReturns: (dim: PortfolioReturnsDim = "day", ref?: string) => {
    const qs = new URLSearchParams({ dim });
    if (ref) qs.set("ref", ref);
    return request<PortfolioReturnsSummary>(`/api/holdings/returns?${qs}`);
  },
  createHolding: (body: HoldingCreate) =>
    request("/api/holdings", { method: "POST", body: JSON.stringify(body) }),
  updateHolding: (id: number, body: HoldingUpdate) =>
    request(`/api/holdings/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteHolding: (id: number) =>
    request<void>(`/api/holdings/${id}`, { method: "DELETE" }),
  getIndices: () => request<IndexQuote[]>("/api/market/indices"),
  getMacro: (topic = "gold") =>
    request<MacroTopic>(`/api/market/macro?topic=${encodeURIComponent(topic)}`),
  getGoldEtfs: () => request<GoldEtf[]>("/api/market/gold-etfs"),
  getGoldBoard: () => request<GoldBoard>("/api/market/gold-board"),
  getFundBoard: () => request<FundBoard>("/api/market/fund-board"),
  searchFunds: (q: string, limit = 20) =>
    request<FundSearchResult>(
      `/api/market/fund-search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  getFundNavHistory: (code: string, days = 30, market = "OF") =>
    request<FundNavHistory>(
      `/api/market/fund-nav/${encodeURIComponent(code)}?days=${days}&market=${encodeURIComponent(market)}`,
    ),
  getSession: (key = "sh-composite") =>
    request<MarketSession>(`/api/market/session?key=${encodeURIComponent(key)}`),
  getIntraday: (key = "sh-composite") =>
    request<IntradaySeries>(`/api/market/intraday?key=${encodeURIComponent(key)}`),
  getSymbolIntraday: (symbol: string, market: string) =>
    request<IntradaySeries>(
      `/api/market/intraday?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}`,
    ),
  getShortBias: (keys: string[]) =>
    request<ShortBiasBatch>(
      `/api/market/short-bias?keys=${encodeURIComponent(keys.join(","))}`,
    ),
  getDepthFlow: (symbol: string, market: string, days = 5) =>
    request<DepthFlow>(
      `/api/market/depth-flow?symbol=${encodeURIComponent(symbol)}&market=${encodeURIComponent(market)}&days=${days}`,
    ),
  getLeaders: (
    key = "sh-composite",
    kind: "up" | "down" | "amount" | "turnover" | "etf" = "up",
    limit = 100,
  ) =>
    request<LeadersBoard>(
      `/api/market/leaders?key=${encodeURIComponent(key)}&kind=${kind}&limit=${limit}`,
    ),
  searchSymbols: (q: string, limit = 12) =>
    request<SearchResult>(
      `/api/market/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  getMarketNews: (limit = 100, board = "headline") =>
    request<NewsFeed>(
      `/api/news/market?limit=${limit}&board=${encodeURIComponent(board)}`,
    ),
  getNewsBoards: () => request<{ items: NewsBoard[] }>("/api/news/boards"),
  getNewsMacroPulse: () => request<NewsMacroPulse>("/api/news/macro-pulse"),
  getHoldingsNews: (limit = 100) =>
    request<NewsFeed>(`/api/news/holdings?limit=${limit}`),
  getNewsInterests: () =>
    request<{ items: NewsInterest[] }>("/api/news/interests"),
  addNewsInterest: (keyword: string) =>
    request<NewsInterest>("/api/news/interests", {
      method: "POST",
      body: JSON.stringify({ keyword }),
    }),
  removeNewsInterest: (id: number) =>
    request<void>(`/api/news/interests/${id}`, { method: "DELETE" }),
  getInterestsNews: (limit = 100) =>
    request<NewsFeed>(`/api/news/interests/feed?limit=${limit}`),
  getNewsArticle: (id: string) =>
    request<NewsArticle>(`/api/news/article?id=${encodeURIComponent(id)}`),
  getWatchlist: () => request<WatchlistItem[]>("/api/market/watchlist"),
  addWatchlist: (symbol: string, market: "SH" | "SZ" = "SH") =>
    request<WatchlistItem>("/api/market/watchlist", {
      method: "POST",
      body: JSON.stringify({ symbol, market }),
    }),
  removeWatchlist: (id: number) =>
    request<void>(`/api/market/watchlist/${id}`, { method: "DELETE" }),

  getAnalysisCatalog: () => request<AnalysisCatalog>("/api/analysis/catalog"),
  getAnalysisRecipes: (mode?: string) =>
    request<AnalysisRecipe[]>(
      mode
        ? `/api/analysis/recipes?mode=${encodeURIComponent(mode)}`
        : "/api/analysis/recipes",
    ),
  getAnalysisProfile: () => request<AnalysisProfile>("/api/analysis/profile"),
  putAnalysisProfile: (degree: string) =>
    request<AnalysisProfile>("/api/analysis/profile", {
      method: "PUT",
      body: JSON.stringify({ degree }),
    }),
  createAnalysisJob: (body: AnalysisJobCreate) =>
    request<AnalysisJob>("/api/analysis/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** SSE multi-agent committee: meta / stage / agent_* / report / error / done */
  streamAnalysisJob: async (
    body: AnalysisJobCreate,
    onEvent: AnalysisStreamEventHandler,
    signal?: AbortSignal,
  ) => {
    const token = getAccessToken();
    const res = await fetch(`${API_BASE}/api/analysis/jobs/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
      cache: "no-store",
    });
    await _consumeAnalysisSse(res, onEvent);
  },

  /** Attach to agent/background job — same event shapes as streamAnalysisJob */
  streamAnalysisJobAttach: async (
    jobId: number,
    onEvent: AnalysisStreamEventHandler,
    signal?: AbortSignal,
  ) => {
    const token = getAccessToken();
    const res = await fetch(`${API_BASE}/api/analysis/jobs/${jobId}/stream`, {
      method: "GET",
      headers: {
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      signal,
      cache: "no-store",
    });
    await _consumeAnalysisSse(res, onEvent);
  },
  getAnalysisJob: (id: number) => request<AnalysisJob>(`/api/analysis/jobs/${id}`),
  getLatestAnalysis: (scope?: "portfolio" | "symbol") =>
    request<AnalysisJob | null>(
      scope
        ? `/api/analysis/latest?scope=${encodeURIComponent(scope)}`
        : "/api/analysis/latest",
    ),
  getRunningAnalysis: (scope?: "portfolio" | "symbol") =>
    request<AnalysisJob | null>(
      scope
        ? `/api/analysis/running?scope=${encodeURIComponent(scope)}`
        : "/api/analysis/running",
    ),

  getIdentity: () => request<AnzaiIdentity>("/api/me/identity"),
  putIdentity: (role: string, label = "") =>
    request<AnzaiIdentity>("/api/me/identity", {
      method: "PUT",
      body: JSON.stringify({ role, label }),
    }),

  getNotifySettings: () => request<NotifySettings>("/api/notify/settings"),
  putNotifySettings: (body: {
    enabled?: boolean;
    channel?: string;
    token?: string;
    wxpusher_uid?: string;
    hour?: number;
    minute?: number;
    weekdays?: string;
    degree?: string;
  }) =>
    request<NotifySettings>("/api/notify/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testNotify: () =>
    request<NotifyRunResult>("/api/notify/test", { method: "POST" }),
  runNotifyDigest: (opts?: { force?: boolean; dry_run?: boolean }) => {
    const q = new URLSearchParams();
    if (opts?.force) q.set("force", "1");
    if (opts?.dry_run) q.set("dry_run", "1");
    const qs = q.toString();
    return request<NotifyRunResult>(
      `/api/notify/run-digest${qs ? `?${qs}` : ""}`,
      { method: "POST" },
    );
  },

  getAgentSession: (conversationId?: number | null) => {
    const q =
      conversationId != null && conversationId > 0
        ? `?conversation_id=${conversationId}`
        : "";
    return request<{
      enabled: boolean;
      identity: AnzaiIdentity;
      preset_id: string;
      preset_name: string;
      suggested_chips: string[];
      greeting: string;
      conversation_id: number;
      conversation: {
        id: number;
        title: string;
        status: string;
        preview?: string;
        updated_at?: string | null;
      };
      messages: {
        id: string;
        role: "user" | "assistant";
        content: string;
        created_at?: string | null;
      }[];
    }>(`/api/agent/session${q}`);
  },

  listAgentConversations: () =>
    request<{
      items: {
        id: number;
        title: string;
        status: string;
        preview?: string;
        updated_at?: string | null;
        closed_at?: string | null;
      }[];
    }>("/api/agent/conversations"),

  createAgentConversation: (closeCurrent = true) =>
    request<{
      status: string;
      conversation: { id: number; title: string; status: string };
    }>("/api/agent/conversations", {
      method: "POST",
      body: JSON.stringify({ close_current: closeCurrent }),
    }),

  closeAgentConversation: (conversationId: number) =>
    request<{
      status: string;
      conversation: { id: number; title: string; status: string };
      active: { id: number; title: string; status: string };
    }>(`/api/agent/conversations/${conversationId}/close`, { method: "POST" }),

  deleteAgentConversation: (conversationId: number) =>
    request<{
      status: string;
      active: { id: number; title: string; status: string };
    }>(`/api/agent/conversations/${conversationId}`, { method: "DELETE" }),

  clearAgentMessages: (conversationId?: number | null) => {
    const q =
      conversationId != null && conversationId > 0
        ? `?conversation_id=${conversationId}`
        : "";
    return request<{ status: string; deleted: number }>(`/api/agent/messages${q}`, {
      method: "DELETE",
    });
  },

  /**
   * Stream 安崽 chat (SSE). Events: meta / tool_start / tool_result / tool_status / card / token / error / done.
   * Uses same-origin Route Handler (not rewrite) so bytes are not buffered.
   */
  streamAgentChat: async (
    messages: { role: string; content: string }[],
    onEvent: (ev: {
      type: string;
      text?: string;
      message?: string;
      [k: string]: unknown;
    }) => void,
    signal?: AbortSignal,
    conversationId?: number | null,
  ) => {
    const token = getAccessToken();
    const streamUrl = `${API_BASE}/api/agent/chat`;
    const res = await fetch(streamUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        messages,
        ...(conversationId != null && conversationId > 0
          ? { conversation_id: conversationId }
          : {}),
      }),
      signal,
      cache: "no-store",
    });
    if (res.status === 401 && typeof window !== "undefined") {
      clearSession();
      if (!window.location.pathname.startsWith("/login")) {
        window.location.assign("/login");
      }
      throw new Error("未登录");
    }
    if (!res.ok || !res.body) {
      const text = await res.text();
      let message = text || `Request failed: ${res.status}`;
      try {
        const j = JSON.parse(text) as { detail?: string; message?: string };
        message = j.message || j.detail || message;
      } catch {
        /* plain */
      }
      onEvent({ type: "error", message });
      onEvent({ type: "done" });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    let sawDone = false;

    const flushLine = (line: string) => {
      const trimmed = line.trim();
      if (!trimmed || !trimmed.startsWith("data:")) return;
      const raw = trimmed.slice(5).trim();
      if (!raw || raw === "[DONE]") {
        if (raw === "[DONE]" && !sawDone) {
          sawDone = true;
          onEvent({ type: "done" });
        }
        return;
      }
      try {
        const ev = JSON.parse(raw) as { type: string };
        if (ev.type === "done") sawDone = true;
        onEvent(ev);
      } catch {
        /* ignore partial json */
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // SSE events are separated by blank lines; also split single \n for proxies
      const parts = buf.split(/\r?\n/);
      buf = parts.pop() || "";
      for (const line of parts) flushLine(line);
    }
    if (buf.trim()) flushLine(buf);
    // Only synthesize done if the server never sent one (abrupt EOF)
    if (!sawDone) onEvent({ type: "done" });
  },
};

export { API_BASE };
