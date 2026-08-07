/** Short-bias chip copy shared by portfolio + market gold list. */

import type { ShortBias } from "@/lib/types";

const GOLD_ETF_KEYS = new Set([
  "SH:518880",
  "SH:518800",
  "SZ:159937",
  "SZ:159934",
  "SH:518660",
]);

export function isGoldBiasKey(market: string, symbol: string): boolean {
  const m = (market || "").toUpperCase();
  if (m === "JD" || m === "GDS") return true;
  return GOLD_ETF_KEYS.has(`${m}:${(symbol || "").trim()}`);
}

export function biasChipText(bias: ShortBias, market: string, symbol: string): string {
  const gold = isGoldBiasKey(market, symbol);
  if (bias.bias === "closed") {
    if ((market || "").toUpperCase() === "GDS") {
      return bias.label.includes("休市") ? "上金所休市" : "日盘收盘";
    }
    return gold ? "金收盘" : "已收盘";
  }
  // Pass through special states from backend
  if (
    bias.label.includes("观察") ||
    bias.label.includes("陈旧") ||
    bias.label === "积存金" ||
    bias.label.includes("暂无") ||
    bias.label.includes("震荡偏")
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
    return gold ? "黄金ETF场内时段已结束" : "A股连续竞价已结束";
  }
  if (bias.label.includes("观察")) {
    return "开盘初波动大，暂不标方向 · 非预测";
  }
  if (bias.label.includes("陈旧")) {
    return "分时末点偏旧，倾向暂停 · 非预测";
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

export function biasChipClass(bias: ShortBias, market: string, symbol: string): string {
  if (bias.bias === "closed" && isGoldBiasKey(market, symbol)) {
    return "portfolio-card-bias-chip bias-closed bias-gold";
  }
  if (bias.label.includes("观察") || bias.label.includes("陈旧") || bias.bias === "na") {
    return "portfolio-card-bias-chip bias-flat";
  }
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
