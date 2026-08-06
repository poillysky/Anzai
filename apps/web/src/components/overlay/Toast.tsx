"use client";

import {
  Check,
  CircleAlert,
  Info,
  type LucideIcon,
} from "@/components/ui/icons";

export type ToastKind = "info" | "success" | "warning";

export type ToastItem = {
  id: number;
  message: string;
  kind: ToastKind;
};

const TOAST_ICON: Record<ToastKind, LucideIcon> = {
  success: Check,
  warning: CircleAlert,
  info: Info,
};

/** iOS-style center HUD toast — icon + short message. */
export function ToastView({ item }: { item: ToastItem }) {
  const Icon = TOAST_ICON[item.kind];
  return (
    <div className={`toast toast-${item.kind}`} role="status">
      <span className="toast-icon" aria-hidden>
        <Icon size={18} strokeWidth={2.4} absoluteStrokeWidth />
      </span>
      <span className="toast-msg">{item.message}</span>
    </div>
  );
}
