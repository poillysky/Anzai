"use client";

import Link from "next/link";
import type { AgentCard } from "@/features/agent/AgentResultCards";

type AnalysisCard = Extract<AgentCard, { kind: "analysis" }>;

function scopeTitle(card: AnalysisCard): string {
  if (card.scope === "symbol") {
    const nm = (card.name || card.symbol || "").trim();
    return nm ? nm : "这只标的";
  }
  return "仓库";
}

function etaText(card: AnalysisCard): string {
  if (Array.isArray(card.eta_minutes) && card.eta_minutes.length >= 2) {
    return `${card.eta_minutes[0]}～${card.eta_minutes[1]} 分钟`;
  }
  return "2～3 分钟";
}

/** 分析等待：单块面板，避免步骤条 + 卡片 + 文案三重堆叠 */
export function AgentAnalysisWait({
  card,
  status,
}: {
  card: AnalysisCard;
  status?: string;
}) {
  const target = scopeTitle(card);
  const eta = etaText(card);
  const phase =
    (status || "").includes("整理") || (status || "").includes("好了")
      ? "整理结论中"
      : "委员会开会中";

  return (
    <div className="agent-wait" aria-live="polite" aria-label="安崽分析中">
      <div className="agent-wait-sheen" aria-hidden />
      <div className="agent-wait-top">
        <span className="agent-wait-pulse" aria-hidden />
        <div className="agent-wait-copy">
          <div className="agent-wait-title">安崽分析中</div>
          <div className="agent-wait-meta">
            {target}
            <span className="agent-wait-dot">·</span>
            预计 {eta}
          </div>
        </div>
      </div>
      <div className="agent-wait-bar" aria-hidden>
        <i />
      </div>
      <div className="agent-wait-foot">
        <span className="agent-wait-phase">{phase}</span>
        <Link href="/analysis" className="agent-wait-link">
          看进度
        </Link>
      </div>
    </div>
  );
}
