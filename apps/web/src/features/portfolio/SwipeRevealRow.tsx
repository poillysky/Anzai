"use client";

import { Trash2 } from "@/components/ui/icons";
import { haptics } from "@/lib/haptics";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";

const ACTION_W = 76;
const OPEN_THRESHOLD = 40;

type Props = {
  /** Controlled: which row is open (null = all closed). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDelete: () => void;
  children: ReactNode;
  className?: string;
  /** Disable swipe (e.g. empty state). */
  disabled?: boolean;
};

/**
 * iOS-style left-swipe to reveal delete.
 * Vertical scroll stays free until horizontal intent is clear.
 * Pointer capture only after horizontal lock — otherwise child button clicks die.
 * Delete paint is gated by `is-revealing` so closed rows never leak #ff3b30.
 */
export function SwipeRevealRow({
  open,
  onOpenChange,
  onDelete,
  children,
  className,
  disabled,
}: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const startX = useRef(0);
  const startY = useRef(0);
  const baseX = useRef(0);
  const curX = useRef(0);
  const axis = useRef<"none" | "h" | "v">("none");
  const dragging = useRef(false);
  const capturing = useRef(false);
  const suppressClick = useRef(false);
  const [revealing, setRevealing] = useState(open);

  const setX = useCallback((x: number, animate: boolean) => {
    const el = trackRef.current;
    if (!el) return;
    const clamped = Math.max(-ACTION_W, Math.min(0, x));
    curX.current = clamped;
    el.style.transition = animate ? "transform 0.22s cubic-bezier(0.2, 0.8, 0.2, 1)" : "none";
    el.style.transform = `translate3d(${clamped}px,0,0)`;
    const next = clamped < -0.5;
    setRevealing((prev) => (prev === next ? prev : next));
  }, []);

  useEffect(() => {
    setX(open ? -ACTION_W : 0, true);
  }, [open, setX]);

  const releaseCapture = (el: HTMLElement, pointerId: number) => {
    if (!capturing.current) return;
    capturing.current = false;
    try {
      el.releasePointerCapture(pointerId);
    } catch {
      /* ignore */
    }
  };

  const onPointerDown = (e: ReactPointerEvent) => {
    if (disabled || e.button !== 0) return;
    dragging.current = true;
    axis.current = "none";
    suppressClick.current = false;
    startX.current = e.clientX;
    startY.current = e.clientY;
    baseX.current = open ? -ACTION_W : 0;
    curX.current = baseX.current;
    // Do not capture yet — capture on pointerdown steals the click from children.
  };

  const onPointerMove = (e: ReactPointerEvent) => {
    if (!dragging.current) return;
    const dx = e.clientX - startX.current;
    const dy = e.clientY - startY.current;
    if (axis.current === "none") {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      if (Math.abs(dy) > Math.abs(dx)) {
        axis.current = "v";
        dragging.current = false;
        return;
      }
      axis.current = "h";
      haptics.selection();
      try {
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        capturing.current = true;
      } catch {
        /* ignore */
      }
    }
    if (axis.current !== "h") return;
    e.preventDefault();
    setX(baseX.current + dx, false);
  };

  const endDrag = (e: ReactPointerEvent) => {
    const wasH = axis.current === "h";
    const el = e.currentTarget as HTMLElement;
    if (!dragging.current && !wasH) {
      axis.current = "none";
      releaseCapture(el, e.pointerId);
      return;
    }
    dragging.current = false;
    if (!wasH) {
      axis.current = "none";
      releaseCapture(el, e.pointerId);
      return;
    }
    axis.current = "none";
    releaseCapture(el, e.pointerId);
    if (Math.abs(curX.current - baseX.current) > 6) {
      suppressClick.current = true;
    }
    const shouldOpen = curX.current < -OPEN_THRESHOLD;
    onOpenChange(shouldOpen);
    setX(shouldOpen ? -ACTION_W : 0, true);
  };

  const onClickCapture = (e: ReactMouseEvent) => {
    if (!suppressClick.current) return;
    e.preventDefault();
    e.stopPropagation();
    suppressClick.current = false;
  };

  const style: CSSProperties = {
    ["--swipe-action-w" as string]: `${ACTION_W}px`,
  };

  return (
    <div
      className={`swipe-reveal ${revealing ? "is-revealing" : ""} ${className ?? ""}`.trim()}
      style={style}
      data-revealing={revealing ? "1" : "0"}
    >
      <button
        type="button"
        className="swipe-reveal-action"
        tabIndex={open ? 0 : -1}
        aria-hidden={!open}
        onClick={(e) => {
          e.stopPropagation();
          haptics.warning();
          onDelete();
        }}
      >
        <Trash2 size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
        删除
      </button>
      <div
        ref={trackRef}
        className="swipe-reveal-track"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onClickCapture={onClickCapture}
      >
        {children}
      </div>
    </div>
  );
}
