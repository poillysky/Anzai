/** Cross-tab signal: Agent started/finished an analysis job. */

import { cacheDelete, PrefetchKeys } from "@/lib/prefetch";

export const ANALYSIS_JOB_EVENT = "anzai:analysis-job";

export type AnalysisJobEventDetail = {
  phase: "start" | "done";
  jobId?: number;
  scope?: string;
};

export function notifyAnalysisJob(detail: AnalysisJobEventDetail): void {
  if (typeof window === "undefined") return;
  const scope =
    detail.scope === "symbol" || detail.scope === "portfolio"
      ? detail.scope
      : "portfolio";
  cacheDelete(PrefetchKeys.analysisLatest(scope));
  if (detail.phase === "done") {
    cacheDelete(PrefetchKeys.analysisLatest("portfolio"));
    cacheDelete(PrefetchKeys.analysisLatest("symbol"));
  }
  window.dispatchEvent(new CustomEvent(ANALYSIS_JOB_EVENT, { detail }));
}
