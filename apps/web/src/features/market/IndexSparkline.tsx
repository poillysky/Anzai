"use client";

import { useId, useMemo, useRef, useState, type PointerEvent } from "react";
import type { IntradayPoint } from "@/lib/types";

type Props = {
  points: IntradayPoint[];
  prevClose?: number | null;
  changePct?: number | null;
  session?: "cn" | "us" | string;
  label?: string;
  interactive?: boolean;
  compact?: boolean;
};

const CN_SLOTS = 242;
const CN_AM = 121;
const HK_SLOTS = 331;
const HK_AM = 151;
const US_SLOTS = 390;

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
  yLabels: { y: number; price: number; pct: number; isPrev: boolean }[];
  midX: number;
  prevY: number;
  up: boolean;
  prev: number;
  timeLabels: { slot: number; label: string }[];
  sessionSlots: number;
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

  const built = useMemo<Built | null>(() => {
    if (points.length < 2) return null;

    const sessionSlots = isUs ? US_SLOTS : isHk ? HK_SLOTS : CN_SLOTS;
    const midSlot = isUs ? Math.round(US_SLOTS / 2) : isHk ? HK_AM : CN_AM;
    const toSlot = isUs ? usTimeToSlot : isHk ? hkTimeToSlot : cnTimeToSlot;
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
        : [
            { slot: 0, label: "09:30" },
            { slot: 60, label: "10:30" },
            { slot: CN_AM, label: "11:30" },
            { slot: CN_AM + 30, label: "13:30" },
            { slot: CN_SLOTS - 1, label: "15:00" },
          ];

    const slotted: { slot: number; price: number; avg: number | null; time: string }[] = [];
    for (const p of points) {
      const slot = toSlot(p.time);
      if (slot == null) continue;
      slotted.push({ slot, price: p.price, avg: p.avg ?? null, time: p.time });
    }
    if (slotted.length < 2) {
      points.forEach((p, i) => {
        slotted.push({
          slot: Math.round((i / Math.max(points.length - 1, 1)) * (sessionSlots - 1)),
          price: p.price,
          avg: p.avg ?? null,
          time: p.time,
        });
      });
    }

    const prices = slotted.map((s) => s.price);
    const prev = prevClose && prevClose > 0 ? prevClose : prices[0];
    let lo = Math.min(...prices, prev);
    let hi = Math.max(...prices, prev);
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

    // Grid lines (5) + compact labels (高 / 昨收 / 低 only — less clutter)
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

    const yLabels = [
      { y: yAt(hi), price: hi, pct: ((hi - prev) / prev) * 100, isPrev: false },
      { y: prevY, price: prev, pct: 0, isPrev: true },
      { y: yAt(lo), price: lo, pct: ((lo - prev) / prev) * 100, isPrev: false },
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
      timeLabels,
      sessionSlots,
    };
  }, [points, prevClose, changePct, isUs, isHk]);

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
        preserveAspectRatio="xMidYMid meet"
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

      {/* Quiet grids */}
      {built.yTicks.map((t, i) => (
        <line
          key={`yg-${i}`}
          x1={PAD.left}
          y1={t.y}
          x2={PAD.left + PLOT_W}
          y2={t.y}
          stroke={t.isPrev ? C.prev : i === 0 || i === built.yTicks.length - 1 ? C.gridStrong : C.grid}
          strokeWidth={t.isPrev ? 0.85 : 0.4}
          strokeDasharray={t.isPrev ? "3.5 2.5" : undefined}
          opacity={t.isPrev ? 1 : 0.8}
        />
      ))}

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
      {built.timeLabels.map(({ slot, label: tl }) => {
        const x = PAD.left + (slot / (built.sessionSlots - 1)) * PLOT_W;
        const anchor =
          slot === 0 ? "start" : slot >= built.sessionSlots - 1 ? "end" : "middle";
        return (
          <text
            key={tl}
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
        {built.yLabels.map((t, i) => {
          const tone =
            t.isPrev ? "is-prev" : t.pct > 0.01 ? "text-up" : t.pct < -0.01 ? "text-down" : "text-mute";
          return (
            <span
              key={`yl-${i}`}
              className={`market-spark-yaxis-tick ${tone}`}
              style={{ top: `${(t.y / VB_H) * 100}%` }}
            >
              {fmtPrice(t.price)}
            </span>
          );
        })}
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
