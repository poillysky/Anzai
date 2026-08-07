export function formatMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "--";
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatSignedMoney(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "--";
  const abs = formatMoney(Math.abs(n));
  if (n > 0) return `+${abs}`;
  if (n < 0) return `-${abs}`;
  return abs;
}

export function formatPct(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "--";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

/** Compact CN amount: 1.2亿 / 3456万 / 12.3万 */
export function formatAmount(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "--";
  const abs = Math.abs(n);
  if (abs >= 1e8) return `${(n / 1e8).toFixed(2)}亿`;
  if (abs >= 1e4) return `${(n / 1e4).toFixed(1)}万`;
  return n.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

/** Absolute price change vs prev close. */
export function formatChange(
  price: number | null | undefined,
  prevClose: number | null | undefined,
): string {
  if (price == null || prevClose == null || Number.isNaN(price) || Number.isNaN(prevClose)) {
    return "--";
  }
  const d = price - prevClose;
  const abs = Math.abs(d).toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  if (d > 0) return `+${abs}`;
  if (d < 0) return `-${abs}`;
  return abs;
}

export function pnlArrow(n: number | null | undefined): string {
  if (n == null || n === 0) return "";
  return n > 0 ? "↑" : "↓";
}

export function pnlClass(n: number | null | undefined): string {
  if (n == null || n === 0) return "text-mute";
  return n > 0 ? "text-up" : "text-down";
}

/**
 * Quote tone for UI. Prefer change_pct; when it rounds to 0% (common in
 * 集合竞价 / tiny moves), fall back to raw price vs prev_close so red/green still shows.
 */
export function pnlTone(
  changePct: number | null | undefined,
  price?: number | null,
  prevClose?: number | null,
): string {
  if (changePct != null && changePct !== 0 && !Number.isNaN(changePct)) {
    return pnlClass(changePct);
  }
  if (
    price != null &&
    prevClose != null &&
    !Number.isNaN(price) &&
    !Number.isNaN(prevClose) &&
    prevClose > 0
  ) {
    const d = price - prevClose;
    if (d > 0) return "text-up";
    if (d < 0) return "text-down";
  }
  return "text-mute";
}

export function pnlArrowTone(
  changePct: number | null | undefined,
  price?: number | null,
  prevClose?: number | null,
): string {
  const tone = pnlTone(changePct, price, prevClose);
  if (tone === "text-up") return "↑";
  if (tone === "text-down") return "↓";
  return "";
}
