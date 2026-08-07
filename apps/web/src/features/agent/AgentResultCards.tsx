"use client";

import Link from "next/link";

export type AgentCard =
  | {
      kind: "portfolio";
      total_market_value?: number;
      day_pnl_pct?: number | null;
      total_pnl_pct?: number | null;
      count?: number;
      holdings?: Array<{
        symbol: string;
        name: string;
        weight?: number | null;
        day_pnl_pct?: number | null;
        quote_chg?: number | null;
      }>;
    }
  | {
      kind: "rebalance";
      empty?: boolean;
      stance?: string;
      day_pnl_pct?: number | null;
      head?: { symbol: string; name: string; weight?: number };
      notes?: string[];
    }
  | {
      kind: "analysis";
      job_id?: number;
      scope?: "portfolio" | "symbol" | string;
      status?: string;
      title?: string;
      label?: string;
      degree?: string;
      symbol?: string;
      name?: string;
      ack?: string;
    };

function fmtPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtMoney(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return Math.round(v).toLocaleString("zh-CN");
}

/** Compact scannable result cards under assistant bubble (session-live only). */
export function AgentResultCards({ cards }: { cards: AgentCard[] }) {
  if (!cards.length) return null;
  return (
    <div className="agent-result-cards">
      {cards.map((card, i) => {
        if (card.kind === "portfolio") {
          return (
            <div key={`pf-${i}`} className="agent-result-card">
              <div className="agent-result-card-title">仓库快照</div>
              <div className="agent-result-card-meta">
                市值 {fmtMoney(card.total_market_value)} · 今日盈亏{" "}
                {fmtPct(card.day_pnl_pct)} · 累计 {fmtPct(card.total_pnl_pct)}
                {card.count != null ? ` · ${card.count} 只` : ""}
              </div>
              <div className="agent-result-card-hint">
                今日盈亏 ≠ 行情涨跌（对昨收）
              </div>
              {(card.holdings || []).length > 0 ? (
                <ul className="agent-result-card-list">
                  {(card.holdings || []).map((h) => (
                    <li key={h.symbol}>
                      <span className="agent-result-name">{h.name}</span>
                      <span className="agent-result-nums">
                        仓 {h.weight != null ? `${h.weight}%` : "—"} · 盈亏{" "}
                        {fmtPct(h.day_pnl_pct)} · 行情 {fmtPct(h.quote_chg)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="agent-result-card-hint">暂无持仓</div>
              )}
            </div>
          );
        }
        if (card.kind === "analysis") {
          const scopeLabel =
            card.scope === "symbol"
              ? card.name || card.symbol
                ? `个股 ${card.name || card.symbol}`
                : "个股"
              : "仓库";
          return (
            <div
              key={`an-${i}`}
              className="agent-result-card agent-result-card-analysis"
              data-live="1"
            >
              <div className="agent-result-card-title">
                <span className="agent-analysis-dot" aria-hidden />
                {card.title || "分析进行中"}
              </div>
              <div className="agent-result-card-meta">
                已经在分析{scopeLabel}
                {card.degree
                  ? ` · ${card.degree === "standard" ? "标准档" : card.degree}`
                  : ""}
              </div>
              <div className="agent-result-card-hint">
                {card.ack || "你可以继续聊；跑完后问我，或去分析页看报告。"}
              </div>
              <Link href="/analysis" className="agent-result-card-link">
                去分析页看进度
              </Link>
            </div>
          );
        }
        return (
          <div key={`rb-${i}`} className="agent-result-card">
            <div className="agent-result-card-title">调仓草案</div>
            {card.empty ? (
              <div className="agent-result-card-meta">{card.stance || "空仓"}</div>
            ) : (
              <>
                <div className="agent-result-card-meta">
                  倾向：{card.stance || "观望"}
                  {card.day_pnl_pct != null
                    ? ` · 组合今日盈亏 ${fmtPct(card.day_pnl_pct)}`
                    : ""}
                </div>
                {card.head ? (
                  <div className="agent-result-card-hint">
                    头仓 {card.head.name}
                    {card.head.weight != null ? ` ${card.head.weight}%` : ""}
                  </div>
                ) : null}
                {(card.notes || []).length > 0 ? (
                  <ul className="agent-result-card-list">
                    {(card.notes || []).map((n) => (
                      <li key={n}>{n}</li>
                    ))}
                  </ul>
                ) : null}
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
