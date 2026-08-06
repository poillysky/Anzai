"use client";

import { useCallback, useEffect, useState } from "react";
import { ChevronLeft, MessageSquare, MoreHorizontal } from "@/components/ui/icons";
import { ActionSheet } from "@/components/overlay/ActionSheet";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { api } from "@/lib/api";
import { haptics } from "@/lib/haptics";

export type AgentConversationItem = {
  id: number;
  title: string;
  status: string;
  preview?: string;
  updated_at?: string | null;
  closed_at?: string | null;
};

type Props = {
  activeId: number | null;
  onBack: () => void;
  onSelect: (id: number) => void;
  onClosedActive: (nextActiveId: number) => void;
  onDeleted: (nextActiveId: number) => void;
};

function fmtTime(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const mm = `${d.getMonth() + 1}`.padStart(2, "0");
  const dd = `${d.getDate()}`.padStart(2, "0");
  const hh = `${d.getHours()}`.padStart(2, "0");
  const mi = `${d.getMinutes()}`.padStart(2, "0");
  return `${mm}-${dd} ${hh}:${mi}`;
}

/** 安崽对话记录：切换 / 关闭 / 删除（列表无描边，操作进 ActionSheet） */
export function AgentHistory({
  activeId,
  onBack,
  onSelect,
  onClosedActive,
  onDeleted,
}: Props) {
  const { toast } = useOverlay();
  const [items, setItems] = useState<AgentConversationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [menuId, setMenuId] = useState<number | null>(null);

  const menuItem = items.find((x) => x.id === menuId) || null;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.listAgentConversations();
      setItems(res.items || []);
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载失败", "warning");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const closeOne = async (id: number) => {
    if (busyId != null) return;
    setBusyId(id);
    setMenuId(null);
    try {
      const res = await api.closeAgentConversation(id);
      haptics.success();
      toast("对话已关闭", "success");
      await reload();
      if (activeId === id && res.active?.id) {
        onClosedActive(res.active.id);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : "关闭失败", "warning");
    } finally {
      setBusyId(null);
    }
  };

  const deleteOne = async (id: number) => {
    if (busyId != null) return;
    setBusyId(id);
    setMenuId(null);
    try {
      const res = await api.deleteAgentConversation(id);
      haptics.success();
      toast("对话已删除", "success");
      await reload();
      if (activeId === id && res.active?.id) {
        onDeleted(res.active.id);
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "warning");
    } finally {
      setBusyId(null);
    }
  };

  const sheetActions = (() => {
    if (!menuItem) return [];
    const actions: { label: string; destructive?: boolean; onClick: () => void }[] = [];
    if (menuItem.status !== "closed") {
      actions.push({
        label: "关闭对话",
        onClick: () => void closeOne(menuItem.id),
      });
    }
    actions.push({
      label: "删除对话",
      destructive: true,
      onClick: () => void deleteOne(menuItem.id),
    });
    return actions;
  })();

  return (
    <div className="agent-history">
      <header className="agent-nav">
        <button
          type="button"
          className="agent-nav-back"
          aria-label="返回"
          onClick={() => {
            haptics.tap();
            onBack();
          }}
        >
          <ChevronLeft size={22} strokeWidth={2} absoluteStrokeWidth aria-hidden />
        </button>
        <h1 className="agent-nav-title">对话记录</h1>
        <span className="agent-nav-spacer" aria-hidden />
      </header>

      <div className="agent-history-body">
        {loading ? <p className="agent-history-hint">加载中…</p> : null}
        {!loading && items.length === 0 ? (
          <div className="agent-thread-empty">
            <MessageSquare size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
            <p>还没有对话记录</p>
          </div>
        ) : null}
        <ul className="agent-history-list">
          {items.map((it) => {
            const closed = it.status === "closed";
            const active = activeId === it.id;
            return (
              <li
                key={it.id}
                className={`agent-history-item${active ? " is-active" : ""}${closed ? " is-closed" : ""}`}
              >
                <button
                  type="button"
                  className="agent-history-main"
                  onClick={() => {
                    haptics.tap();
                    onSelect(it.id);
                  }}
                >
                  <span className="agent-history-top">
                    <span className="agent-history-title">{it.title || "新对话"}</span>
                    <span className="agent-history-time">{fmtTime(it.updated_at)}</span>
                  </span>
                  {it.preview ? (
                    <span className="agent-history-preview">{it.preview}</span>
                  ) : (
                    <span className="agent-history-preview is-empty">暂无消息</span>
                  )}
                  <span className="agent-history-meta">
                    {active && !closed ? (
                      <span className="agent-history-badge is-live">当前</span>
                    ) : null}
                    {closed ? <span className="agent-history-badge">已关闭</span> : null}
                  </span>
                </button>
                <button
                  type="button"
                  className="agent-history-more"
                  aria-label="更多操作"
                  disabled={busyId === it.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    haptics.tap();
                    setMenuId(it.id);
                  }}
                >
                  <MoreHorizontal size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <ActionSheet
        open={menuId != null}
        title={menuItem?.title || "对话操作"}
        onClose={() => setMenuId(null)}
        actions={sheetActions}
      />
    </div>
  );
}
