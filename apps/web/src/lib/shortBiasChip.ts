/** Short-bias chip copy shared by portfolio + market gold list. */

import type { ShortBias } from "@/lib/types";

const GOLD_ETF_KEYS = new Set([
  "SH:518880",
  "SH:518800",
  "SZ:159937",
  "SZ:159934",
  "SH:518660",
]);

export function isGoldEtfKey(market: string, symbol: string): boolean {
  const m = (market || "").toUpperCase();
  return GOLD_ETF_KEYS.has(`${m}:${(symbol || "").trim()}`);
}

export function isGoldBiasKey(market: string, symbol: string): boolean {
  const m = (market || "").toUpperCase();
  if (m === "JD" || m === "GDS") return true;
  return isGoldEtfKey(m, symbol);
}

export function biasMidText(bias: ShortBias, _market: string, _symbol: string): string {
  if (bias.summary && bias.summary.trim()) {
    return bias.summary.trim();
  }
  return biasChipText(bias, _market, _symbol);
}

export type BiasLineTone = "up" | "down" | "flat";

/** 单行文案语义：涨红 / 跌绿 / 平黄 */
export function biasLineTone(line: string): BiasLineTone {
  const t = line.trim();
  if (!t) return "flat";
  // 动作词优先（「偏强里回落」算跌色）
  if (/快速下跌|往下|回落|压力|下探|延续偏弱/.test(t)) return "down";
  if (/快速上涨|往上|抬头|反弹|延续偏强|动能偏强|上攻/.test(t)) return "up";
  if (/偏强/.test(t)) return "up";
  if (/偏弱/.test(t)) return "down";
  if (/走平|震荡|观望|未明|方向|不明/.test(t)) return "flat";
  return "flat";
}

export function biasMidSegments(
  bias: ShortBias,
  market: string,
  symbol: string,
): { text: string; tone: BiasLineTone }[] {
  const raw = biasMidText(bias, market, symbol);
  const lines = raw
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  if (lines.length === 0) return [{ text: raw || "—", tone: "flat" }];
  return lines.map((text) => ({ text, tone: biasLineTone(text) }));
}

export function biasChipText(bias: ShortBias, market: string, symbol: string): string {
  if (bias.summary && bias.summary.trim()) {
    const parts = bias.summary
      .split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean);
    return parts[parts.length - 1] || bias.summary.trim();
  }
  const gold = isGoldBiasKey(market, symbol);
  if (bias.bias === "closed") {
    if ((market || "").toUpperCase() === "GDS") {
      return bias.label.includes("休市") ? "上金所休市" : "日盘收盘";
    }
    return "已收盘";
  }
  if (
    bias.label.includes("观察") ||
    bias.label.includes("陈旧") ||
    bias.label === "积存金" ||
    bias.label.includes("暂无") ||
    bias.label.includes("震荡偏") ||
    bias.label.includes("偏跌抬头") ||
    bias.label.includes("偏涨回落")
  ) {
    return bias.label;
  }
  if (gold || bias.label.startsWith("金价")) {
    if (bias.bias === "up") return "金价偏涨";
    if (bias.bias === "down") return "金价偏跌";
    if (bias.bias === "flat") return "金价震荡";
    return bias.label || "金价";
  }
  if (bias.bias === "up") return "短线偏涨";
  if (bias.bias === "down") return "短线偏跌";
  if (bias.bias === "na") return bias.label || "短线暂无";
  return "短线震荡";
}

export function biasChipTitle(bias: ShortBias, market: string, symbol: string): string {
  const gold = isGoldBiasKey(market, symbol);
  if (bias.bias === "closed") {
    if ((market || "").toUpperCase() === "GDS") {
      return `${bias.label} · 上金所现货时段，非预测`;
    }
    return "场内时段已结束";
  }
  if (bias.label.includes("观察")) {
    return "开盘初波动大，暂不标方向 · 非预测";
  }
  if (bias.label.includes("陈旧")) {
    return "分时末点偏旧，倾向暂停 · 非预测";
  }
  // Prefer backend multi-horizon narrative (4h/2h/1h/近5分)
  if (bias.detail && bias.detail.trim()) {
    return bias.detail.trim();
  }
  const micro =
    bias.bias === "flat" &&
    bias.roc_pct != null &&
    Math.abs(bias.roc_pct) < (gold ? 0.15 : 0.08);
  const roc =
    bias.roc_pct != null && !micro
      ? ` ${bias.roc_pct > 0 ? "+" : ""}${bias.roc_pct.toFixed(2)}%`
      : micro
        ? " · 近端几乎无涨跌"
        : "";
  if (bias.label.includes("偏跌抬头")) {
    return `${bias.label}${roc} · 偏跌过程中近端抬头，疑似拐点 · 非预测`;
  }
  if (bias.label.includes("偏涨回落")) {
    return `${bias.label}${roc} · 偏涨过程中近端回落，疑似拐点 · 非预测`;
  }
  if (bias.label.includes("震荡偏跌")) {
    const move =
      bias.roc_pct != null
        ? ` 近端高点回撤 ${bias.roc_pct > 0 ? "+" : ""}${bias.roc_pct.toFixed(2)}%`
        : "";
    return `${bias.label}${move} · 看图右侧下探，非预测`;
  }
  if (bias.label.includes("震荡偏涨")) {
    const move =
      bias.roc_pct != null
        ? ` 近端低点抬升 ${bias.roc_pct > 0 ? "+" : ""}${bias.roc_pct.toFixed(2)}%`
        : "";
    return `${bias.label}${move} · 看图右侧上移，非预测`;
  }
  if (gold || market === "JD" || market === "GDS") {
    return `${bias.label || "金价近端"}${roc} · 积存金/金价动量，非预测`;
  }
  return `${bias.label}${roc} · 近5分动量，非预测`;
}

export function biasChipClass(bias: ShortBias): string {
  if (bias.bias === "closed") {
    return "portfolio-card-bias-chip bias-closed";
  }
  if (bias.label.includes("观察") || bias.label.includes("陈旧") || bias.bias === "na") {
    return "portfolio-card-bias-chip bias-flat";
  }
  // 拐点：偏跌抬头→偏涨色；偏涨回落→偏跌色（已由 bias 字段表达）
  return `portfolio-card-bias-chip bias-${bias.bias}`;
}

/** Prefer market:symbol; fall back for legacy empty AU9999 rows. */
export function goldBiasKey(item: {
  id?: string;
  market?: string;
  symbol?: string;
}): string | null {
  const m = (item.market || "").trim().toUpperCase();
  const s = (item.symbol || "").trim();
  if (m && s) return `${m}:${s}`;
  if (item.id === "au9999") return "GDS:AU9999";
  return null;
}
