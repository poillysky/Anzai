"use client";

import type { ReactNode } from "react";

type Props = {
  active?: boolean;
  onClick: () => void;
  /** 左侧：名次 / 场内场外标签 */
  leading?: ReactNode;
  name: ReactNode;
  meta: ReactNode;
  /** 卡片中间区（如金价偏势说明），无则不占列 */
  mid?: ReactNode;
  price: ReactNode;
  /** 右侧角标或成交/换手组合，由调用方带好 class */
  badge: ReactNode;
};

/**
 * 行情列表统一行：宽度锁死 + 名称省略，避免场外长名把整页向右撑开。
 */
export function MarketRow({
  active = false,
  onClick,
  leading,
  name,
  meta,
  mid,
  price,
  badge,
}: Props) {
  const hasMid = mid != null && mid !== false && mid !== "";
  return (
    <button
      type="button"
      className={`market-row${hasMid ? " market-row--mid" : ""}`}
      data-active={active ? "1" : "0"}
      onClick={onClick}
    >
      {leading != null ? <span className="market-row-leading">{leading}</span> : null}
      <span className="market-row-main">
        <span className="market-row-name">{name}</span>
        <span className="market-row-meta">{meta}</span>
      </span>
      {hasMid ? <span className="market-row-mid">{mid}</span> : null}
      <span className="market-row-side">
        <span className="market-row-price">{price}</span>
        {badge}
      </span>
    </button>
  );
}
