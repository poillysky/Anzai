"use client";

import { CandlestickChart, MessagesSquare, Scale, Sparkles, Users } from "@/components/ui/icons";
import type { AnalysisAgentStep, AnalysisReport } from "@/lib/types";

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

function AgentCards({ agents }: { agents: AnalysisAgentStep[] }) {
  const seats = agents.filter((a) => a.id !== "judge");
  if (!seats.length) return null;
  return (
    <section className="inset-group analysis-agents" style={{ marginTop: 12 }} aria-label="委员会席位">
      <div className="inset-group-header">
        <span className="skeleton-block-head">
          <Users size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
          委员会过程
        </span>
      </div>
      <ul className="analysis-agent-list">
        {seats.map((a) => {
          const failed = a.status === "failed";
          return (
            <li
              key={`${a.id}-${a.summary?.slice(0, 12)}`}
              className="analysis-agent-card"
              data-status={failed ? "failed" : "done"}
            >
              <div className="analysis-agent-top">
                <span className="analysis-agent-label">
                  {a.label || a.id}
                  {failed ? (
                    <span className="analysis-agent-fail-tag">失败</span>
                  ) : null}
                </span>
                <span className={`analysis-item-stance ${stanceClass(a.stance)}`}>
                  {failed ? "未完成" : a.stance}
                </span>
              </div>
              <p className="analysis-agent-summary">{a.summary}</p>
              {!failed && a.bullets?.length ? (
                <ul className="analysis-agent-bullets">
                  {a.bullets.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function reportQualityBadge(report: AnalysisReport): {
  label: string;
  kind: "live" | "template" | "degraded";
} {
  if (report.template) return { label: "模板兜底", kind: "template" };
  if (report.degraded || (report.failed_seats?.length ?? 0) > 0) {
    return { label: "部分失败", kind: "degraded" };
  }
  return { label: "委员会", kind: "live" };
}

export function AnalysisReportBlocks({
  report,
  emptyHint,
  progressLabel,
  liveAgents,
}: {
  report: AnalysisReport | null;
  emptyHint: string;
  progressLabel?: string | null;
  liveAgents?: AnalysisAgentStep[];
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
        <p className="skeleton-placeholder">{progressLabel || emptyHint}</p>
        {liveAgents && liveAgents.length > 0 ? <AgentCards agents={liveAgents} /> : null}
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
  const agents = report.agents?.length ? report.agents : liveAgents || [];
  const debate = report.debate || [];
  const quality = reportQualityBadge(report);
  const qualityNote =
    report.quality_note ||
    (report.template
      ? "委员会未完整跑通，以下为模板快评。"
      : report.failed_seats?.length
        ? `${report.failed_seats.join("、")}未谈成，结论已降级。`
        : "");

  return (
    <>
      <section className="hero-summary analysis-verdict" aria-label="总结报告">
        <div className="skeleton-block-head">
          <Sparkles size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
          <span className="hero-label" style={{ margin: 0 }}>
            总结报告
          </span>
          <span
            className={
              quality.kind === "live"
                ? "analysis-badge analysis-badge-live"
                : quality.kind === "degraded"
                  ? "analysis-badge analysis-badge-warn"
                  : "analysis-badge"
            }
          >
            {quality.label}
          </span>
        </div>
        {qualityNote ? (
          <p className="analysis-quality-note" role="status">
            {qualityNote}
          </p>
        ) : null}
        <p className="analysis-verdict-text">{report.verdict}</p>
        <div className="analysis-meta-row">
          <span className={stanceClass(report.stance)}>{report.stance}</span>
          {typeof report.confidence === "number" ? (
            <span className="text-mute">置信 {Math.round(report.confidence * 100)}%</span>
          ) : null}
        </div>
        {(report.watch?.length ?? 0) > 0 ? (
          <div className="analysis-watch-block">
            <p className="analysis-watch-label">重点注意</p>
            <ul className="analysis-watch-list">
              {report.watch!.map((w, i) => {
                const ref = report.watch_refs?.[i];
                return (
                  <li key={`${w}-${i}`}>
                    <span>{w}</span>
                    {ref ? <span className="analysis-watch-ref">{ref}</span> : null}
                  </li>
                );
              })}
            </ul>
            {(report.unresolved?.length ?? 0) > 0 ? (
              <p className="analysis-unresolved text-mute">
                未决：{report.unresolved!.join("；")}
              </p>
            ) : null}
          </div>
        ) : null}
        {highlights.length > 0 ? (
          <ul className="analysis-highlights">
            {highlights.map((h) => (
              <li key={h}>{h}</li>
            ))}
          </ul>
        ) : null}
        {report.actions?.length ? (
          <ul className="analysis-actions">
            {report.actions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        ) : null}
      </section>

      {report.rebalance && !report.rebalance.empty ? (
        <section
          className="inset-group analysis-rebalance"
          style={{ marginTop: 12 }}
          aria-label="再平衡建议"
        >
          <div className="inset-group-header">
            <span className="skeleton-block-head">
              <Scale size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
              再平衡建议
            </span>
          </div>
          <div className="analysis-rebalance-body">
            <p className="analysis-rebalance-stance">
              倾向：{report.rebalance.stance || "观望"}
              {report.rebalance.day_pnl_pct != null
                ? ` · 组合今日盈亏 ${formatPct(report.rebalance.day_pnl_pct)}`
                : ""}
            </p>
            {report.rebalance.head ? (
              <p className="analysis-rebalance-head text-mute">
                头仓 {report.rebalance.head.name}
                {report.rebalance.head.weight != null
                  ? ` ${report.rebalance.head.weight}%`
                  : ""}
              </p>
            ) : null}
            {(report.rebalance.notes || []).length > 0 ? (
              <ul className="analysis-rebalance-notes">
                {report.rebalance.notes!.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            ) : null}
            <p className="analysis-rebalance-hint text-mute">倾向性草案，非下单</p>
          </div>
        </section>
      ) : null}

      <AgentCards agents={agents} />

      {debate.length > 0 ? (
        <section className="inset-group analysis-debate" style={{ marginTop: 12 }} aria-label="辩证轨迹">
          <div className="inset-group-header">
            <span className="skeleton-block-head">
              <MessagesSquare size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
              辩证轨迹
            </span>
          </div>
          <ul className="analysis-debate-list">
            {debate.map((d) => (
              <li key={d.round} className="analysis-debate-card">
                <div className="analysis-agent-top">
                  <span className="analysis-agent-label">第 {d.round} 回合</span>
                  <span className={`analysis-item-stance ${stanceClass(d.stance || "中性")}`}>
                    {d.stance || "中性"}
                  </span>
                </div>
                <p className="analysis-agent-summary">{d.summary}</p>
                {d.bull_points?.length ? (
                  <p className="analysis-debate-side text-up">多：{d.bull_points.join(" · ")}</p>
                ) : null}
                {d.bear_points?.length ? (
                  <p className="analysis-debate-side text-down">空：{d.bear_points.join(" · ")}</p>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="inset-group analysis-items" style={{ marginTop: 12 }} aria-label="持仓要点">
        <div className="inset-group-header">
          <span className="skeleton-block-head">
            <CandlestickChart size={14} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            持仓要点
          </span>
        </div>
        {items.length === 0 ? (
          <p className="skeleton-placeholder" style={{ padding: "10px 12px" }}>
            暂无持仓摘要
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
