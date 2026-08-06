"use client";

import { getOverlayRoot } from "@/components/overlay/OverlayContext";
import { haptics } from "@/lib/haptics";
import { lockUnderlyingScroll, unlockUnderlyingScroll } from "@/lib/iosKeyboard";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

export type ActionSheetItem = {
  label: string;
  destructive?: boolean;
  onClick: () => void | Promise<void>;
};

type Props = {
  open: boolean;
  title?: string;
  onClose: () => void;
  actions: ActionSheetItem[];
};

/**
 * Bottom action sheet — no inputs (docs/弹窗键盘规范.md §5).
 * Locks underlying scroll while open; do not put form fields here — use CenterModal.
 */
export function ActionSheet({ open, title, onClose, actions }: Props) {
  const [root, setRoot] = useState<HTMLElement | null>(() => getOverlayRoot());

  useEffect(() => {
    if (!root) setRoot(getOverlayRoot());
  }, [root, open]);

  useEffect(() => {
    if (!open) return;
    haptics.tap();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    document.documentElement.dataset.modal = "1";
    lockUnderlyingScroll();
    return () => {
      document.body.style.overflow = prev;
      delete document.documentElement.dataset.modal;
      unlockUnderlyingScroll();
    };
  }, [open]);

  if (!open || !root) return null;

  return createPortal(
    <div
      className="sheet-overlay"
      role="presentation"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="sheet-panel" role="dialog" aria-modal="true">
        <div className="sheet-group">
          {title ? <div className="sheet-title">{title}</div> : null}
          <div className="sheet-actions">
            {actions.map((a) => (
              <button
                key={a.label}
                type="button"
                className={`sheet-action ${a.destructive ? "sheet-action-danger" : ""}`}
                onClick={() => {
                  void (async () => {
                    if (a.destructive) haptics.warning();
                    else haptics.selection();
                    try {
                      await a.onClick();
                    } finally {
                      onClose();
                    }
                  })();
                }}
              >
                {a.label}
              </button>
            ))}
          </div>
        </div>
        <button type="button" className="sheet-cancel" onClick={onClose}>
          取消
        </button>
      </div>
    </div>,
    root,
  );
}
