"use client";

import {
  useCallback,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
  type TouchEvent,
} from "react";

const EDGE_ZONE = 28;
const ACTIVATE = 72;

type ShellRootProps = {
  children: ReactNode;
  className?: string;
  /** When true, root layer is visually behind an overlay (hidden). */
  pushed?: boolean;
};

/** Screen root that hosts push overlays. */
export function ShellRoot({ children, className = "", pushed = false }: ShellRootProps) {
  return (
    <div className={`shell-root${pushed ? " shell-root--push" : ""} ${className}`.trim()}>
      {children}
    </div>
  );
}

type ShellLayerProps = {
  children: ReactNode;
  className?: string;
  role?: string;
  /** Edge-swipe to go back (iOS-like). */
  onEdgeBack?: () => void;
  /** Disable edge gesture (e.g. horizontal chip scroller owns the touch). */
  edgeBackDisabled?: boolean;
};

/**
 * Absolute overlay layer with push-in animation + optional left-edge back.
 */
export function ShellLayer({
  children,
  className = "",
  role = "dialog",
  onEdgeBack,
  edgeBackDisabled = false,
}: ShellLayerProps) {
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);
  const startRef = useRef<{ x: number; y: number; active: boolean } | null>(null);

  const onTouchStart = useCallback(
    (e: TouchEvent) => {
      if (edgeBackDisabled || !onEdgeBack) return;
      const t = e.touches[0];
      if (!t || t.clientX > EDGE_ZONE) {
        startRef.current = null;
        return;
      }
      startRef.current = { x: t.clientX, y: t.clientY, active: true };
      setDragging(true);
    },
    [edgeBackDisabled, onEdgeBack],
  );

  const onTouchMove = useCallback(
    (e: TouchEvent) => {
      const start = startRef.current;
      if (!start?.active || !onEdgeBack) return;
      const t = e.touches[0];
      if (!t) return;
      const dx = t.clientX - start.x;
      const dy = Math.abs(t.clientY - start.y);
      if (dy > 48 && Math.abs(dx) < dy) {
        startRef.current = null;
        setDragX(0);
        setDragging(false);
        return;
      }
      if (dx > 0) {
        setDragX(Math.min(dx, typeof window !== "undefined" ? window.innerWidth : 400));
      }
    },
    [onEdgeBack],
  );

  const onTouchEnd = useCallback(() => {
    const start = startRef.current;
    startRef.current = null;
    setDragging(false);
    if (!onEdgeBack) {
      setDragX(0);
      return;
    }
    if (dragX >= ACTIVATE) {
      setDragX(0);
      onEdgeBack();
      return;
    }
    setDragX(0);
  }, [dragX, onEdgeBack]);

  const style: CSSProperties | undefined =
    dragX > 0
      ? {
          transform: `translateX(${dragX}px)`,
          transition: dragging ? "none" : undefined,
        }
      : undefined;

  return (
    <div
      className={`shell-layer is-front ${className}`.trim()}
      role={role}
      style={style}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      onTouchCancel={onTouchEnd}
    >
      {onEdgeBack && !edgeBackDisabled ? (
        <div className="shell-edge-hit" aria-hidden />
      ) : null}
      {children}
    </div>
  );
}

type ShellBaseProps = {
  children: ReactNode;
  className?: string;
  /** Hidden when overlay is open (keeps mount / scroll). */
  behind?: boolean;
};

export function ShellBase({ children, className = "", behind = false }: ShellBaseProps) {
  return (
    <div
      className={`shell-layer shell-layer-base${behind ? " is-back" : " is-front"} ${className}`.trim()}
      aria-hidden={behind}
    >
      {children}
    </div>
  );
}
