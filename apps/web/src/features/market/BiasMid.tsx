"use client";

import {
  biasMidSegments,
  type BiasLineTone,
} from "@/lib/shortBiasChip";
import type { ShortBias } from "@/lib/types";

import "./market.css";

type Props = {
  bias: ShortBias;
  market: string;
  symbol: string;
  className?: string;
};

const TONE_CLASS: Record<BiasLineTone, string> = {
  up: "bias-mid-up",
  down: "bias-mid-down",
  flat: "bias-mid-flat",
};

/** 三行偏势：分段着色（涨红 / 平黄 / 跌绿） */
export function BiasMid({ bias, market, symbol, className }: Props) {
  const segs = biasMidSegments(bias, market, symbol);
  return (
    <span className={`bias-mid${className ? ` ${className}` : ""}`}>
      {segs.map((s, i) => (
        <span key={`${i}-${s.text}`} className={`bias-mid-line ${TONE_CLASS[s.tone]}`}>
          {s.text}
        </span>
      ))}
    </span>
  );
}
