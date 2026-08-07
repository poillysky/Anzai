"use client";

import { useEffect } from "react";

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const el = target.closest(
    "input, textarea, select, [contenteditable='true'], [contenteditable=''], .allow-select",
  );
  return Boolean(el);
}

/**
 * Make the PWA feel like a native app: no text selection / long-press callout /
 * image drag / right-click menu outside editable fields.
 */
export function useAppNativeFeel(): void {
  useEffect(() => {
    const onContextMenu = (e: MouseEvent) => {
      if (!isEditableTarget(e.target)) e.preventDefault();
    };
    const onSelectStart = (e: Event) => {
      if (!isEditableTarget(e.target)) e.preventDefault();
    };
    const onDragStart = (e: DragEvent) => {
      if (!isEditableTarget(e.target)) e.preventDefault();
    };

    document.addEventListener("contextmenu", onContextMenu);
    document.addEventListener("selectstart", onSelectStart);
    document.addEventListener("dragstart", onDragStart);
    return () => {
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("selectstart", onSelectStart);
      document.removeEventListener("dragstart", onDragStart);
    };
  }, []);
}
