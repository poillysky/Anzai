"use client";

import {
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { haptics } from "@/lib/haptics";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  /** Visual press class variant */
  variant?: "default" | "row" | "ghost";
  haptic?: boolean;
};

/**
 * Shared press feedback — scale + optional haptic.
 * Prefer this (or .btn / .row-pressable) over ad-hoc :active styles.
 */
export function Pressable({
  children,
  className = "",
  variant = "default",
  haptic = false,
  onPointerDown,
  onPointerUp,
  onPointerCancel,
  onPointerLeave,
  onClick,
  type = "button",
  ...rest
}: Props) {
  const [pressed, setPressed] = useState(false);

  return (
    <button
      type={type}
      className={[
        "pressable",
        variant === "row" ? "row-pressable" : "",
        variant === "ghost" ? "btn btn-ghost" : "",
        variant === "default" ? "pressable-default" : "",
        pressed ? "is-pressed" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      onPointerDown={(e) => {
        setPressed(true);
        onPointerDown?.(e);
      }}
      onPointerUp={(e) => {
        setPressed(false);
        onPointerUp?.(e);
      }}
      onPointerCancel={(e) => {
        setPressed(false);
        onPointerCancel?.(e);
      }}
      onPointerLeave={(e) => {
        setPressed(false);
        onPointerLeave?.(e);
      }}
      onClick={(e) => {
        if (haptic) haptics.tap();
        onClick?.(e);
      }}
      {...rest}
    >
      {children}
    </button>
  );
}
