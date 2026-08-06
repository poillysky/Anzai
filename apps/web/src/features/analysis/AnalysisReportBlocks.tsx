"use client";

import { CandlestickChart, Sparkles } from "@/components/ui/icons";
import type { AnalysisReport } from "@/lib/types";

function pctClass(v?: number | null) {
  if (v == null || Number.isNaN(v)) return "text-mute";
  if (v > 0) return "text-up";
  if (v < 0) return "text-down";
  return "text-mute";
}

function formatPct(v?: number | null) {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function stanceClass(stance: string) {
  if (stance === "偏多") return "text-up";
  if (stance === "偏空") return "text-down";
  return "text-mute";
}

export function AnalysisReportBlocks({
  report,
  emptyHint,
}: {
  report: AnalysisReport | null;
  emptyHint: string;
}) {
  if (!report) {
    return (
      <section className="hero-summary" aria-label="总结报告">
        <div className="skeleton-block-head">
          <Sparkles size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
          <span className="hero-label" style={{ margin: 0 }}>
            总结报告
          </span>
        </div>
        <p className="skeleton-placeholder">{emptyHint}</p>
      </section>
    );
  }

  const highlights =
    report.highlights?.length ? report.highlights : (report.bullets ?? []).slice(0, 2);
  const items = report.items?.length
    ? report.items
    : (report.structure ?? []).map((row) => ({
        symbol: row.symbol || row.name,
        name: row.name,
        stance: "中性",
        change_pct: row.change_pct,
        weight: row.weight,
        summary:
          row.weight != null
            ? `仓位 ${row.weight.toFixed(1)}% · 今日 ${formatPct(row.change_pct)}`
            : `今日 ${formatPct(row.change_pct)}`,
      }));

  return (
    <>
      <section className="hero-summary analysis-verdict" aria-label="总结报告">
        <div className="skeleton-block-head">
          <Sparkles size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
          <span className="hero-label" style={{ margin: 0 }}>
            总结报告
          </span>
          {report.template ? <span className="analysis-badge">模板</span> : null}
        </div>
        <p className="analysis-verdict-text">{report.verdict}</p>
        <div className="analysis-meta-row">
          <span className={stanceClass(report.stance)}>{report.stance}</span>
        </div>
        {highlights.length > 0 ? (
          <ul className="analysis-highlights">
            {highlights.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="inset-group analysis-items" style={{ marginTop: 12 }} aria-label="个股要点">
        <div className="inset-group-header">
          <span className="skeleton-block-head">
            <CandlestickChart size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            个股要点
          </span>
        </div>
        {items.length === 0 ? (
          <p className="skeleton-placeholder" style={{ padding: "10px 12px" }}>
            暂无个股摘要
          </p>
        ) : (
          <ul className="analysis-item-list">
            {items.map((row) => (
              <li key={row.symbol} className="analysis-item-row">
                <div className="analysis-item-top">
                  <span className="analysis-item-name">{row.name}</span>
                  <span className={pctClass(row.change_pct)}>{formatPct(row.change_pct)}</span>
                </div>
                <div className="analysis-item-bottom">
                  <span className={`analysis-item-stance ${stanceClass(row.stance)}`}>
                    {row.stance}
                  </span>
                  <span className="analysis-item-summary">{row.summary}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
