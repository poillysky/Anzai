"use client";

import { useId, useMemo, useRef, useState, type PointerEvent } from "react";
import type { IntradayPoint } from "@/lib/types";

type Props = {
  points: IntradayPoint[];
  prevClose?: number | null;
  changePct?: number | null;
  /** cn A-share · hk · us overnight · day24 · comex 外盘金 · daily 按日(基金) */
  session?: "cn" | "us" | "hk" | "day24" | "comex" | "daily" | string;
  label?: string;
  interactive?: boolean;
  compact?: boolean;
  /**
   * 已有点等距铺满整宽（与日K相同拉伸）。
   * 基金页分时用此模式，避免「按交易时段留白」与日K整宽拉伸不一致。
   */
  fillWidth?: boolean;
};

const CN_SLOTS = 242;
const CN_AM = 121;
const HK_SLOTS = 331;
const HK_AM = 151;
const US_SLOTS = 390;
/** One slot per minute — 00:00 … 23:59 (axis label ends at 24:00). */
const DAY24_SLOTS = 24 * 60;
/** COMEX/伦敦金电子盘（北京·夏令）：06:00 → 次日 05:00，约 23h；对齐东财 trendsTotal≈1381 */
const COMEX_OPEN = 6 * 60;
const COMEX_SLOTS = 23 * 60 + 1;

/** Plot viewBox: labels overlay inside plot (not a side gutter). */
const VB_W = 360;
const VB_H = 142;
const PAD = { top: 10, right: 10, bottom: 26, left: 8 };
const PLOT_W = VB_W - PAD.left - PAD.right;
const PLOT_H = VB_H - PAD.top - PAD.bottom;

const C = {
  prev: "rgba(245, 197, 66, 0.4)",
  grid: "rgba(255, 255, 255, 0.035)",
  gridStrong: "rgba(255, 255, 255, 0.055)",
  axis: "rgba(255, 255, 255, 0.32)",
  frame: "rgba(255, 255, 255, 0.06)",
  up: "var(--up)",
  down: "var(--down)",
  tipBg: "rgba(18, 18, 20, 0.94)",
};

function cnTimeToSlot(hhmm: string): number | null {
  const parts = hhmm.split(":");
  if (parts.length < 2) return null;
  const h = Number(parts[0]);
  const m = Number(parts[1]);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  const mins = h * 60 + m;
  const amStart = 9 * 60 + 30;
  const amEnd = 11 * 60 + 30;
  const pmStart = 13 * 60;
  const pmEnd = 15 * 60;
  if (mins >= amStart && mins <= amEnd) return mins - amStart;
  if (mins >= pmStart && mins <= pmEnd) return CN_AM + (mins - pmStart);
  return null;
}

function hkTimeToSlot(hhmm: string): number | null {
  const parts = hhmm.split(":");
  if (parts.length < 2) return null;
  const h = Number(parts[0]);
  const m = Number(parts[1]);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  const mins = h * 60 + m;
  const amStart = 9 * 60 + 30;
  const amEnd = 12 * 60;
  const pmStart = 13 * 60;
  const pmEnd = 16 * 60;
  if (mins >= amStart && mins <= amEnd) return mins - amStart;
  if (mins >= pmStart && mins <= pmEnd) return HK_AM + (mins - pmStart);
  return null;
}

function usTimeToSlot(hhmm: string): number | null {
  const parts = hhmm.split(":");
  if (parts.length < 2) return null;
  const h = Number(parts[0]);
  const m = Number(parts[1]);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  const mins = h * 60 + m;
  const start = 21 * 60 + 30;
  if (mins >= start) return mins - start;
  if (mins <= 4 * 60) return 24 * 60 - start + mins;
  return null;
}

/** Full-day axis 00:00–24:00 (浙商/民生积存金等近全天品种). */
function day24TimeToSlot(hhmm: string): number | null {
  const parts = hhmm.split(":");
  if (parts.length < 2) return null;
  let h = Number(parts[0]);
  const m = Number(parts[1]);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  if (h === 24 && m === 0) return DAY24_SLOTS - 1;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  return Math.min(h * 60 + m, DAY24_SLOTS - 1);
}

/** 外盘金：06:00 开 → 跨零点 → 05:00 收；休市 05:00–06:00 不落点。 */
function comexTimeToSlot(hhmm: string): number | null {
  const parts = hhmm.split(":");
  if (parts.length < 2) return null;
  const h = Number(parts[0]);
  const m = Number(parts[1]);
  if (Number.isNaN(h) || Number.isNaN(m)) return null;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  const mins = h * 60 + m;
  // 日切休市
  if (mins > 5 * 60 && mins < COMEX_OPEN) return null;
  let slot: number;
  if (mins >= COMEX_OPEN) slot = mins - COMEX_OPEN;
  else slot = 24 * 60 - COMEX_OPEN + mins;
  if (slot < 0 || slot >= COMEX_SLOTS) return null;
  return slot;
}

function fmtPrice(n: number): string {
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtPct(n: number): string {
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

type Coord = {
  x: number;
  y: number;
  slot: number;
  price: number;
  avg: number | null;
  time: string;
};

type Built = {
  pricePathsUp: string;
  pricePathsDown: string;
  areaPath: string;
  last: Coord | null;
  coords: Coord[];
  yTicks: { y: number; price: number; pct: number; isPrev: boolean }[];
  yLabels: { y: number; price: number; pct: number; isPrev: boolean; kind: "hi" | "prev" | "lo" }[];
  midX: number;
  prevY: number;
  up: boolean;
  prev: number;
  timeLabels: { slot: number; label: string }[];
  sessionSlots: number;
  /** Actual series extreme coords for peak/trough markers */
  hiCoord: Coord | null;
  loCoord: Coord | null;
};

/** Split polyline at prev-close crossings: above → red, below → green. */
function splitByPrevClose(
  coords: Coord[],
  prev: number,
  yAt: (price: number) => number,
): { up: string; down: string } {
  if (coords.length < 2) return { up: "", down: "" };

  type Pt = { x: number; y: number };
  const upSegs: string[] = [];
  const downSegs: string[] = [];

  const flush = (seg: Pt[], tone: "up" | "down") => {
    if (seg.length < 2) return;
    const d = seg
      .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(2)},${c.y.toFixed(2)}`)
      .join(" ");
    (tone === "up" ? upSegs : downSegs).push(d);
  };

  const sideOf = (price: number): "up" | "down" => (price >= prev ? "up" : "down");

  let seg: Pt[] = [{ x: coords[0].x, y: coords[0].y }];
  let tone = sideOf(coords[0].price);

  for (let i = 1; i < coords.length; i++) {
    const a = coords[i - 1];
    const b = coords[i];
    const crossed =
      (a.price > prev && b.price < prev) || (a.price < prev && b.price > prev);

    if (crossed) {
      const r = (prev - a.price) / (b.price - a.price);
      const cross = { x: a.x + (b.x - a.x) * r, y: yAt(prev) };
      seg.push(cross);
      flush(seg, tone);
      tone = sideOf(b.price);
      seg = [cross, { x: b.x, y: b.y }];
    } else {
      seg.push({ x: b.x, y: b.y });
      tone = sideOf(b.price);
    }
  }
  flush(seg, tone);
  return { up: upSegs.join(""), down: downSegs.join("") };
}

export function IndexSparkline({
  points,
  prevClose,
  changePct,
  session = "cn",
  label = "分时走势",
  interactive = true,
  compact = false,
  fillWidth = false,
}: Props) {
  const gid = useId().replace(/:/g, "");
  const fillUpId = `fill-up-${gid}`;
  const fillDnId = `fill-dn-${gid}`;
  const clipId = `clip-${gid}`;
  const clipAboveId = `clip-above-${gid}`;
  const clipBelowId = `clip-below-${gid}`;
  const svgRef = useRef<SVGSVGElement>(null);
  const [cursor, setCursor] = useState<Coord | null>(null);
  const isUs = session === "us";
  const isHk = session === "hk";
  const isDay24 = session === "day24";
  const isComex = session === "comex";
  const isDaily = session === "daily";
  /** 日K 或显式 fillWidth：等距铺满，不按交易时段留白 */
  const useEqualSpace = isDaily || fillWidth;

  const built = useMemo<Built | null>(() => {
    if (points.length < 2) return null;

    const sessionSlots = useEqualSpace
      ? Math.max(points.length, 2)
      : isUs
        ? US_SLOTS
        : isHk
          ? HK_SLOTS
          : isComex
            ? COMEX_SLOTS
            : isDay24
              ? DAY24_SLOTS
              : CN_SLOTS;
    const midSlot = useEqualSpace
      ? Math.floor((sessionSlots - 1) / 2)
      : isUs
        ? Math.round(US_SLOTS / 2)
        : isHk
          ? HK_AM
          : isComex
            ? Math.round(COMEX_SLOTS / 2)
            : isDay24
              ? 12 * 60
              : CN_AM;
    const toSlot = isUs
      ? usTimeToSlot
      : isHk
        ? hkTimeToSlot
        : isComex
          ? comexTimeToSlot
          : isDay24
            ? day24TimeToSlot
            : cnTimeToSlot;
    const timeLabels = isUs
      ? [
          { slot: 0, label: "21:30" },
          { slot: Math.round(US_SLOTS * 0.33), label: "23:00" },
          { slot: midSlot, label: "00:00" },
          { slot: Math.round(US_SLOTS * 0.75), label: "02:00" },
          { slot: US_SLOTS - 1, label: "04:00" },
        ]
      : isHk
        ? [
            { slot: 0, label: "09:30" },
            { slot: 60, label: "10:30" },
            { slot: HK_AM, label: "12:00" },
            { slot: HK_AM + 30, label: "13:30" },
            { slot: HK_SLOTS - 1, label: "16:00" },
          ]
        : isComex
          ? [
              { slot: 0, label: "06:00" },
              { slot: 6 * 60, label: "12:00" },
              { slot: 12 * 60, label: "18:00" },
              { slot: 18 * 60, label: "00:00" },
              { slot: COMEX_SLOTS - 1, label: "05:00" },
            ]
          : isDay24
            ? [
                { slot: 0, label: "00:00" },
                { slot: 6 * 60, label: "06:00" },
                { slot: 12 * 60, label: "12:00" },
                { slot: 18 * 60, label: "18:00" },
                { slot: DAY24_SLOTS - 1, label: "24:00" },
              ]
            : [
                { slot: 0, label: "09:30" },
                { slot: 60, label: "10:30" },
                { slot: CN_AM, label: "11:30" },
                { slot: CN_AM + 30, label: "13:30" },
                { slot: CN_SLOTS - 1, label: "15:00" },
              ];

    const slotted: { slot: number; price: number; avg: number | null; time: string }[] = [];
    if (useEqualSpace) {
      // 等距铺满整宽（日K / 基金分时）
      points.forEach((p, i) => {
        slotted.push({
          slot: i,
          price: p.price,
          avg: p.avg ?? null,
          time: p.time,
        });
      });
    } else {
      for (const p of points) {
        const slot = toSlot(p.time);
        if (slot == null) continue;
        slotted.push({ slot, price: p.price, avg: p.avg ?? null, time: p.time });
      }
      if (slotted.length < 2) {
        points.forEach((p, i) => {
          const slot = Math.round((i / Math.max(points.length - 1, 1)) * (sessionSlots - 1));
          const synth =
            isDay24 || isComex
              ? `${String(Math.floor(slot / 60)).padStart(2, "0")}:${String(slot % 60).padStart(2, "0")}`
              : p.time;
          slotted.push({
            slot,
            price: p.price,
            avg: p.avg ?? null,
            time: /^\d{1,2}:\d{2}/.test(p.time) ? p.time : synth,
          });
        });
      }
    }

    // AU9999 等：夜盘跨日折返时按顺序铺满；国际金用 comex 固定开收，不把右端改成「最新时刻」
    let axisLabels = timeLabels;
    if (useEqualSpace && slotted.length >= 2) {
      const n = slotted.length;
      const at = (t: number) => slotted[Math.min(n - 1, Math.round(t * (n - 1)))];
      axisLabels = [0, 0.25, 0.5, 0.75, 1].map((t) => {
        const p = at(t);
        return { slot: p.slot, label: (p.time || "").slice(0, 5) };
      });
    } else if (isDay24 && slotted.length >= 2) {
      let wraps = false;
      for (let i = 1; i < slotted.length; i++) {
        if (slotted[i].slot + 30 < slotted[i - 1].slot) {
          wraps = true;
          break;
        }
      }
      if (wraps) {
        const n = slotted.length;
        for (let i = 0; i < n; i++) {
          slotted[i] = {
            ...slotted[i],
            slot: Math.round((i / Math.max(n - 1, 1)) * (sessionSlots - 1)),
          };
        }
        const at = (t: number) => slotted[Math.min(n - 1, Math.round(t * (n - 1)))];
        axisLabels = [0, 0.25, 0.5, 0.75, 1].map((t) => {
          const p = at(t);
          return { slot: p.slot, label: p.time.slice(0, 5) };
        });
      }
    }
    const prices = slotted.map((s) => s.price);
    const prev = prevClose && prevClose > 0 ? prevClose : prices[0];
    const dataHi = Math.max(...prices);
    const dataLo = Math.min(...prices);
    let lo = Math.min(dataLo, prev);
    let hi = Math.max(dataHi, prev);
    // Symmetric padding around range (East Money style)
    const spanRaw = hi - lo || prev * 0.002;
    const pad = Math.max(spanRaw * 0.08, prev * 0.0006);
    lo -= pad;
    hi += pad;
    const span = hi - lo;

    const xAt = (slot: number) =>
      PAD.left + (Math.min(Math.max(slot, 0), sessionSlots - 1) / (sessionSlots - 1)) * PLOT_W;
    const yAt = (price: number) => PAD.top + ((hi - price) / span) * PLOT_H;

    const priceCoords: Coord[] = slotted.map((s) => ({
      x: xAt(s.slot),
      y: yAt(s.price),
      ...s,
    }));

    const toPath = (coords: { x: number; y: number }[]) =>
      coords
        .map((c, i) => `${i === 0 ? "M" : "L"}${c.x.toFixed(2)},${c.y.toFixed(2)}`)
        .join(" ");

    const pricePath = toPath(priceCoords);
    const lastPt = priceCoords[priceCoords.length - 1];
    const areaPath = `${pricePath} L${lastPt.x.toFixed(2)},${(PAD.top + PLOT_H).toFixed(2)} L${priceCoords[0].x.toFixed(2)},${(PAD.top + PLOT_H).toFixed(2)} Z`;
    const { up: pricePathsUp, down: pricePathsDown } = splitByPrevClose(
      priceCoords,
      prev,
      yAt,
    );

    // Grid lines (5) + labels at real series 高 / 昨收 / 低
    const levels = [0, 0.25, 0.5, 0.75, 1].map((t) => hi - span * t);
    const yTicks = levels.map((price) => ({
      y: yAt(price),
      price,
      pct: ((price - prev) / prev) * 100,
      isPrev: Math.abs(price - prev) / span < 0.02,
    }));
    const prevY = yAt(prev);
    if (!yTicks.some((t) => Math.abs(t.y - prevY) < 4)) {
      yTicks.push({ y: prevY, price: prev, pct: 0, isPrev: true });
      yTicks.sort((a, b) => a.y - b.y);
    } else {
      const nearest = yTicks.reduce((a, b) =>
        Math.abs(a.y - prevY) < Math.abs(b.y - prevY) ? a : b,
      );
      nearest.isPrev = true;
      nearest.price = prev;
      nearest.pct = 0;
      nearest.y = prevY;
    }

    // First occurrence of extremes (stable if flat)
    const hiCoord = priceCoords.find((c) => c.price === dataHi) ?? null;
    const loCoord = priceCoords.find((c) => c.price === dataLo) ?? null;

    const yLabels: Built["yLabels"] = [
      {
        y: yAt(dataHi),
        price: dataHi,
        pct: ((dataHi - prev) / prev) * 100,
        isPrev: false,
        kind: "hi",
      },
      { y: prevY, price: prev, pct: 0, isPrev: true, kind: "prev" },
      {
        y: yAt(dataLo),
        price: dataLo,
        pct: ((dataLo - prev) / prev) * 100,
        isPrev: false,
        kind: "lo",
      },
    ];

    const up = changePct != null ? changePct >= 0 : lastPt.price >= prev;

    return {
      pricePathsUp,
      pricePathsDown,
      areaPath,
      last: lastPt,
      coords: priceCoords,
      yTicks,
      yLabels,
      midX: xAt(midSlot),
      prevY,
      up,
      prev,
      timeLabels: axisLabels,
      sessionSlots,
      hiCoord,
      loCoord,
    };
  }, [points, prevClose, changePct, isUs, isHk, isDay24, isComex, useEqualSpace]);

  function pickAtClientX(clientX: number) {
    if (!built || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const xSvg = ((clientX - rect.left) / rect.width) * VB_W;
    let best = built.coords[0];
    let bestDist = Infinity;
    for (const c of built.coords) {
      const d = Math.abs(c.x - xSvg);
      if (d < bestDist) {
        bestDist = d;
        best = c;
      }
    }
    setCursor(best);
  }

  function onPointerDown(e: PointerEvent<SVGSVGElement>) {
    if (!interactive) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pickAtClientX(e.clientX);
  }

  function onPointerMove(e: PointerEvent<SVGSVGElement>) {
    if (!interactive || !cursor) return;
    pickAtClientX(e.clientX);
  }

  function onPointerUp(e: PointerEvent<SVGSVGElement>) {
    if (!interactive) return;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
    setCursor(null);
  }

  if (!built) {
    return (
      <div
        className={`market-spark-frame${compact ? " market-spark-frame-compact" : ""}`}
        aria-hidden
      >
        <div className="market-spark market-spark-empty" />
      </div>
    );
  }

  const tip = cursor;
  const tipPct = tip ? ((tip.price - built.prev) / built.prev) * 100 : 0;
  const lastTone = built.last && built.last.price >= built.prev ? C.up : C.down;
  const tipTone = tip && tip.price >= built.prev ? C.up : C.down;
  const aboveH = Math.max(0, built.prevY - PAD.top);
  const belowH = Math.max(0, PAD.top + PLOT_H - built.prevY);

  return (
    <div className={`market-spark-frame${compact ? " market-spark-frame-compact" : ""}`}>
      <svg
        ref={svgRef}
        className={`market-spark market-spark-pro${compact ? " market-spark-compact" : ""}${interactive ? " market-spark-interactive" : ""}`}
        viewBox={`0 0 ${VB_W} ${VB_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={label}
        shapeRendering="geometricPrecision"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
      <defs>
        <linearGradient id={fillUpId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#ff453a" stopOpacity={0.28} />
          <stop offset="55%" stopColor="#ff453a" stopOpacity={0.06} />
          <stop offset="100%" stopColor="#ff453a" stopOpacity={0} />
        </linearGradient>
        <linearGradient id={fillDnId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#32d74b" stopOpacity={0.24} />
          <stop offset="55%" stopColor="#32d74b" stopOpacity={0.05} />
          <stop offset="100%" stopColor="#32d74b" stopOpacity={0} />
        </linearGradient>
        <clipPath id={clipId}>
          <rect x={PAD.left} y={PAD.top} width={PLOT_W} height={PLOT_H} />
        </clipPath>
        <clipPath id={clipAboveId}>
          <rect x={PAD.left} y={PAD.top} width={PLOT_W} height={aboveH} />
        </clipPath>
        <clipPath id={clipBelowId}>
          <rect x={PAD.left} y={built.prevY} width={PLOT_W} height={belowH} />
        </clipPath>
      </defs>

      {/* Plot well */}
      <rect
        x={PAD.left}
        y={PAD.top}
        width={PLOT_W}
        height={PLOT_H}
        fill="rgba(0,0,0,0.18)"
        stroke={C.frame}
        strokeWidth={0.5}
      />

      {/* Quiet grids — skip 昨收 slot; drawn separately */}
      {built.yTicks
        .filter((t) => !t.isPrev)
        .map((t, i) => (
          <line
            key={`yg-${i}`}
            x1={PAD.left}
            y1={t.y}
            x2={PAD.left + PLOT_W}
            y2={t.y}
            stroke={i === 0 || i === built.yTicks.filter((x) => !x.isPrev).length - 1 ? C.gridStrong : C.grid}
            strokeWidth={0.4}
            opacity={0.8}
          />
        ))}

      {/* 昨收 — yellow reference (also drives red/green fill split) */}
      <line
        x1={PAD.left}
        y1={built.prevY}
        x2={PAD.left + PLOT_W}
        y2={built.prevY}
        stroke={C.prev}
        strokeWidth={0.9}
        strokeDasharray="3.5 2.5"
      />

      {/* Midday divider */}
      <line
        x1={built.midX}
        y1={PAD.top}
        x2={built.midX}
        y2={PAD.top + PLOT_H}
        stroke={C.gridStrong}
        strokeWidth={0.6}
        strokeDasharray="2 3"
      />

      <g clipPath={`url(#${clipId})`}>
        <g clipPath={`url(#${clipAboveId})`}>
          <path d={built.areaPath} fill={`url(#${fillUpId})`} />
        </g>
        <g clipPath={`url(#${clipBelowId})`}>
          <path d={built.areaPath} fill={`url(#${fillDnId})`} />
        </g>
        {built.pricePathsUp && (
          <path
            d={built.pricePathsUp}
            fill="none"
            stroke={C.up}
            strokeWidth={1.45}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
        {built.pricePathsDown && (
          <path
            d={built.pricePathsDown}
            fill="none"
            stroke={C.down}
            strokeWidth={1.45}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
        {built.last && !tip && (
          <>
            <circle cx={built.last.x} cy={built.last.y} r={5} fill={lastTone} opacity={0.18} />
            <circle cx={built.last.x} cy={built.last.y} r={2.4} fill={lastTone} />
            <circle cx={built.last.x} cy={built.last.y} r={1} fill="#fff" />
          </>
        )}
        {tip && (
          <>
            <line
              x1={tip.x}
              y1={PAD.top}
              x2={tip.x}
              y2={PAD.top + PLOT_H}
              stroke="rgba(255,255,255,0.65)"
              strokeWidth={0.9}
            />
            <line
              x1={PAD.left}
              y1={tip.y}
              x2={PAD.left + PLOT_W}
              y2={tip.y}
              stroke="rgba(255,255,255,0.28)"
              strokeWidth={0.7}
              strokeDasharray="2 2"
            />
            <circle cx={tip.x} cy={tip.y} r={3.4} fill={tipTone} />
            <circle cx={tip.x} cy={tip.y} r={1.4} fill="#1a1a1c" />
          </>
        )}
      </g>

      {/* High / low markers */}
      {!tip &&
        (
          [
            { c: built.hiCoord, kind: "hi" as const, fill: C.up },
            { c: built.loCoord, kind: "lo" as const, fill: C.down },
          ] as const
        ).map(({ c, kind, fill }) => {
          if (!c) return null;
          const placeRight = c.x < PAD.left + PLOT_W * 0.7;
          const tx = placeRight ? c.x + 5 : c.x - 5;
          const ty =
            kind === "hi"
              ? Math.max(PAD.top + 9, c.y - 5)
              : Math.min(PAD.top + PLOT_H - 1, c.y + 11);
          return (
            <g key={kind} pointerEvents="none">
              <circle
                cx={c.x}
                cy={c.y}
                r={2.2}
                fill={fill}
                stroke="rgba(0,0,0,0.35)"
                strokeWidth={0.6}
              />
              <text
                x={tx}
                y={ty}
                textAnchor={placeRight ? "start" : "end"}
                fill={fill}
                fontSize={9}
                fontWeight={650}
                fontFamily='ui-monospace, "SF Mono", Menlo, monospace'
                style={{ paintOrder: "stroke", stroke: "rgba(12,12,14,0.72)", strokeWidth: 2.5 }}
              >
                {fmtPrice(c.price)}
              </text>
            </g>
          );
        })}

      {tip && (
        <g>
          {(() => {
            const tipLabel = `${tip.time}  ${fmtPrice(tip.price)}`;
            const boxW = Math.min(118, Math.max(96, 8 + tipLabel.length * 6.2));
            const boxH = 30;
            const bx = Math.min(
              Math.max(tip.x - boxW / 2, PAD.left),
              PAD.left + PLOT_W - boxW,
            );
            const by = Math.max(
              PAD.top + 2,
              Math.min(tip.y - boxH - 7, PAD.top + PLOT_H - boxH - 2),
            );
            return (
              <>
                <path
                  d={`M${tip.x.toFixed(1)},${(by + boxH + 5).toFixed(1)} L${(tip.x - 4).toFixed(1)},${(by + boxH).toFixed(1)} L${(tip.x + 4).toFixed(1)},${(by + boxH).toFixed(1)} Z`}
                  fill={C.tipBg}
                />
                <rect
                  x={bx}
                  y={by}
                  width={boxW}
                  height={boxH}
                  rx={6}
                  fill={C.tipBg}
                  stroke="rgba(255,255,255,0.14)"
                  strokeWidth={0.5}
                />
                <text
                  x={bx + boxW / 2}
                  y={by + 13}
                  textAnchor="middle"
                  fill="#f5f5f7"
                  fontSize={10}
                  fontWeight={600}
                  fontFamily='ui-monospace, "SF Mono", Menlo, monospace'
                >
                  {tipLabel}
                </text>
                <text
                  x={bx + boxW / 2}
                  y={by + 25}
                  textAnchor="middle"
                  fill={tipPct >= 0 ? C.up : C.down}
                  fontSize={9}
                  fontFamily='ui-monospace, "SF Mono", Menlo, monospace'
                >
                  {fmtPct(tipPct)}
                </text>
              </>
            );
          })()}
        </g>
      )}

      {/* Time axis */}
      {built.timeLabels.map(({ slot, label: tl }, i) => {
        const x = PAD.left + (slot / (built.sessionSlots - 1)) * PLOT_W;
        const anchor =
          slot === 0 ? "start" : slot >= built.sessionSlots - 1 ? "end" : "middle";
        return (
          <text
            key={`t-${i}-${tl}`}
            x={x}
            y={PAD.top + PLOT_H + 11}
            textAnchor={anchor}
            fill={C.axis}
            fontSize={7}
            fontFamily='-apple-system, "PingFang SC", sans-serif'
          >
            {tl}
          </text>
        );
      })}
      </svg>
      <div className="market-spark-yaxis" aria-hidden>
        {built.yLabels
          .filter((t) => t.kind === "prev")
          .map((t, i) => (
            <span
              key={`yl-prev-${i}`}
              className="market-spark-yaxis-tick is-prev"
              data-kind="prev"
              style={{ top: `${(t.y / VB_H) * 100}%` }}
            >
              {fmtPrice(t.price)}
            </span>
          ))}
      </div>
    </div>
  );
}

/** Key price levels for horizontal “价位气泡” (hi / mid / prev / lo). */
export function getIntradayLevelBubbles(
  points: IntradayPoint[],
  prevClose?: number | null,
): { key: string; label: string; value: number }[] {
  if (points.length < 2) return [];
  const prices = points.map((p) => p.price);
  const prev = prevClose && prevClose > 0 ? prevClose : prices[0];
  const hi = Math.max(...prices);
  const lo = Math.min(...prices);
  const last = prices[prices.length - 1];
  const rows: { key: string; label: string; value: number }[] = [
    { key: "last", label: "最新", value: last },
    { key: "hi", label: "高", value: hi },
    { key: "prev", label: "昨收", value: prev },
    { key: "lo", label: "低", value: lo },
  ];
  const seen = new Set<string>();
  return rows.filter((r) => {
    const k = r.value.toFixed(2);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
