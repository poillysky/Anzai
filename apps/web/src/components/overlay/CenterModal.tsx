"use client";

import {
  lockUnderlyingScroll,
  pinDocumentScroll,
  pinUnderlyingScrollers,
  unlockUnderlyingScroll,
} from "@/lib/iosKeyboard";
import { getOverlayRoot } from "@/components/overlay/OverlayContext";
import { X } from "@/components/ui/icons";
import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

type Props = {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
};

/** Match `--motion-enter` (+buffer); focus after settle. */
const ENTRANCE_MS = 280;

/**
 * Centered form modal — **only** allowed input dialog (docs/弹窗键盘规范.md).
 *
 * Contract for every future modal with inputs:
 * - Scrim `.modal-overlay` stays full-bleed (page never shows through)
 * - Shell scroll frozen via `lockUnderlyingScroll` (background does not move)
 * - Only `.modal-lift` translates with `--keyboard-inset` (input stays above keyboard)
 * - Autofocus: mark field `data-autofocus`, never raw `autoFocus` / scrollIntoView
 */
export function CenterModal({ open, title, onClose, children, footer }: Props) {
  const [root, setRoot] = useState<HTMLElement | null>(() => getOverlayRoot());

  useEffect(() => {
    if (!root) setRoot(getOverlayRoot());
  }, [root, open]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.dataset.modal = "1";
    lockUnderlyingScroll();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onFocusIn = () => {
      pinDocumentScroll();
      pinUnderlyingScrollers();
    };

    window.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.body.style.overflow = prev;
      delete document.documentElement.dataset.modal;
      unlockUnderlyingScroll();
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
    };
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !root) return;
    const id = window.setTimeout(() => {
      const el = root.querySelector<HTMLElement>(".modal-card [data-autofocus]");
      if (!el) return;
      el.focus({ preventScroll: true });
      pinDocumentScroll();
      pinUnderlyingScrollers();
    }, ENTRANCE_MS);
    return () => window.clearTimeout(id);
  }, [open, root]);

  if (!open || !root) return null;

  return createPortal(
    <div
      className="modal-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget || (e.target as HTMLElement).classList?.contains("modal-lift")) {
          onClose();
        }
      }}
    >
      <div className="modal-lift">
        <div
          className="modal-card"
          role="dialog"
          aria-modal="true"
          aria-label={title}
        >
          <header className="modal-header">
            <h2 className="modal-title">{title}</h2>
            <button type="button" className="modal-close" onClick={onClose} aria-label="关闭">
              <X size={16} strokeWidth={2.25} absoluteStrokeWidth />
            </button>
          </header>
          <div className="modal-body">{children}</div>
          {footer ? <footer className="modal-footer">{footer}</footer> : null}
        </div>
      </div>
    </div>,
    root,
  );
}
