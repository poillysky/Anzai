/** 金额 ÷ 单价 → 数量 */
export function qtyFromAmount(
  amount: number,
  unitPrice: number,
  decimals = 2,
): number | null {
  if (!Number.isFinite(amount) || amount <= 0) return null;
  if (!Number.isFinite(unitPrice) || unitPrice <= 0) return null;
  const f = 10 ** decimals;
  return Math.round((amount / unitPrice) * f) / f;
}

/** 场外申购：投入金额 ÷ 确认净值 → 份额（两位小数） */
export function otcSharesFromAmount(amount: number, nav: number): number | null {
  return qtyFromAmount(amount, nav, 2);
}

/** 积存金买入：投入金额 ÷ 金价 → 克数（三位小数，常见口径） */
export function goldGramsFromAmount(
  amount: number,
  pricePerGram: number,
): number | null {
  return qtyFromAmount(amount, pricePerGram, 3);
}
