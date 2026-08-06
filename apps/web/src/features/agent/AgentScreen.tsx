"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { History, MessageSquare, Plus, Settings } from "@/components/ui/icons";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { api } from "@/lib/api";
import { haptics } from "@/lib/haptics";
import type { AnzaiIdentity } from "@/lib/types";
import { AgentHistory } from "@/features/agent/AgentHistory";
import {
  AgentSettings,
  type AgentSettingsPage,
} from "@/features/agent/AgentSettings";

type ChatMsg = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolNote?: string;
};
type StackPage = "chat" | "history" | AgentSettingsPage;

function greetBubble(text: string): ChatMsg {
  return {
    id: `greet-${Date.now()}`,
    role: "assistant",
    content: text || "嗨嗨，安崽来啦～想聊啥跟安崽说呀。",
  };
}

/** 安崽真人对话：多会话 + 气泡线程 + Push 设置 */
export default function AgentScreen() {
  const { toast } = useOverlay();
  const [stack, setStack] = useState<StackPage[]>(["chat"]);
  const page = stack[stack.length - 1] ?? "chat";
  const [identity, setIdentity] = useState<AnzaiIdentity | null>(null);
  const [greeting, setGreeting] = useState("嗨嗨，安崽来啦～先选身份，安崽好陪你聊。");
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  streamingRef.current = streaming;

  const push = useCallback((next: StackPage) => {
    setStack((s) => (s[s.length - 1] === next ? s : [...s, next]));
  }, []);

  const pop = useCallback(() => {
    setStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
  }, []);

  const applySessionMessages = useCallback(
    (saved: ChatMsg[], greet: string, force = false) => {
      setMessages((prev) => {
        // 发送中勿被 session 回灌盖掉；用 ref 避免 streaming 变化重绑 loadSession
        if (!force && streamingRef.current && prev.some((m) => m.role === "user")) {
          return prev;
        }
        if (saved.length > 0) return saved;
        return [greetBubble(greet)];
      });
    },
    [],
  );

  const loadSession = useCallback(
    async (cid?: number | null, opts?: { force?: boolean }) => {
      try {
        const s = await api.getAgentSession(cid);
        setIdentity(s.identity);
        setGreeting(s.greeting || "");
        setConversationId(s.conversation_id ?? null);
        const saved = (s.messages || [])
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map((m) => ({
            id: m.id || `m-${m.role}-${m.content.slice(0, 12)}`,
            role: m.role as "user" | "assistant",
            content: m.content,
          }));
        applySessionMessages(saved, s.greeting || "", opts?.force);
      } catch {
        try {
          const id = await api.getIdentity();
          setIdentity(id);
        } catch {
          /* ignore */
        }
      }
    },
    [applySessionMessages],
  );

  // 仅挂载时拉会话；勿依赖 loadSession 随 streaming 重建，否则 cleanup 会 abort 正在发送的请求
  useEffect(() => {
    void loadSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount only
  }, []);

  useEffect(() => {
    return () => abortRef.current?.abort();
  }, []);

  useEffect(() => {
    const el = threadRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messages, streaming]);

  const onIdentitySaved = useCallback((data: AnzaiIdentity, nextGreeting: string) => {
    setIdentity(data);
    if (!nextGreeting) return;
    setGreeting(nextGreeting);
    setMessages((prev) => {
      const real = prev.filter((m) => m.id !== "greet" && !m.id.startsWith("greet-"));
      if (real.length > 0) return prev;
      return [
        greetBubble(
          nextGreeting || `好呀，安崽记住啦～你是安崽的「${data.label}」，想聊啥跟安崽说。`,
        ),
      ];
    });
  }, []);

  const clearHistory = useCallback(async () => {
    try {
      await api.clearAgentMessages();
      const created = await api.createAgentConversation(false);
      setConversationId(created.conversation.id);
      setMessages([greetBubble(greeting || "清空啦～安崽又在这儿，想聊啥呀？")]);
      haptics.success();
      toast("对话记录已清空", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "清空失败", "warning");
    }
  }, [greeting, toast]);

  const startNewConversation = useCallback(async () => {
    if (streaming) {
      toast("等这句说完再开新对话", "warning");
      return;
    }
    try {
      abortRef.current?.abort();
      const res = await api.createAgentConversation(true);
      setConversationId(res.conversation.id);
      setMessages([greetBubble(greeting || "新对话开始啦～安崽在呢，想聊啥？")]);
      setStack(["chat"]);
      haptics.success();
      toast("已开新对话", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "新开失败", "warning");
    }
  }, [greeting, streaming, toast]);

  const switchConversation = useCallback(
    async (id: number) => {
      if (streaming) {
        toast("等这句说完再切换", "warning");
        return;
      }
      abortRef.current?.abort();
      await loadSession(id, { force: true });
      setStack(["chat"]);
      haptics.tap();
    },
    [loadSession, streaming, toast],
  );

  const send = useCallback(
    async (text: string) => {
      const content = text.trim();
      if (!content || streaming) return;
      if (!identity?.configured) {
        toast("先到设置里选「你是安崽的谁」", "warning");
        push("settings");
        push("identity");
        return;
      }

      const userMsg: ChatMsg = {
        id: `u-${Date.now()}`,
        role: "user",
        content,
      };
      const assistantId = `a-${Date.now()}`;
      const next = [...messages.filter((m) => m.id !== "greet" || messages.length > 1), userMsg];
      setMessages([...next, { id: assistantId, role: "assistant", content: "" }]);
      setInput("");
      setStreaming(true);
      haptics.tap();

      const history = [...next]
        .filter((m) => m.role === "user" || (m.role === "assistant" && m.content))
        .map((m) => ({ role: m.role, content: m.content }));

      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;

      let assembled = "";
      let sawError = false;
      let sawDone = false;

      const applyAssistant = (body: string, toolNote?: string) => {
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.id === assistantId);
          if (idx >= 0) {
            const copy = prev.slice();
            copy[idx] = {
              ...copy[idx],
              content: body,
              ...(toolNote !== undefined ? { toolNote } : {}),
            };
            return copy;
          }
          return [
            ...prev.filter((m) => m.id !== assistantId),
            {
              id: assistantId,
              role: "assistant" as const,
              content: body,
              ...(toolNote ? { toolNote } : {}),
            },
          ];
        });
      };

      let toolNote = "";

      try {
        await api.streamAgentChat(
          history,
          (ev) => {
            if (ev.type === "meta" && typeof ev.conversation_id === "number") {
              setConversationId(ev.conversation_id);
            }
            if (ev.type === "tool_start" || ev.type === "tool_status") {
              const label = typeof ev.label === "string" ? ev.label : "查询中";
              toolNote = label;
              applyAssistant(assembled, toolNote);
            } else if (ev.type === "tool_result") {
              const label = typeof ev.label === "string" ? ev.label : "已查到";
              toolNote = `${label} · 完成`;
              applyAssistant(assembled, toolNote);
            } else if (ev.type === "token") {
              const piece = typeof ev.text === "string" ? ev.text : "";
              if (!piece) return;
              assembled += piece;
              applyAssistant(assembled, toolNote || undefined);
            } else if (ev.type === "error") {
              sawError = true;
              const msg = ev.message || "生成失败";
              toast(msg, "warning");
              if (!assembled) {
                assembled = `（出错）${msg}`;
                applyAssistant(assembled);
              }
            } else if (ev.type === "done") {
              sawDone = true;
              if (assembled) applyAssistant(assembled, "");
            }
          },
          ac.signal,
          conversationId,
        );
        if (!assembled && !sawError) {
          applyAssistant("（没有收到模型内容，请稍后重试或检查 /admin/llm 连接）");
          toast("模型没有返回内容", "warning");
        }
      } catch (e) {
        if ((e as Error).name !== "AbortError") {
          const msg = e instanceof Error ? e.message : "发送失败";
          toast(msg, "warning");
          if (!assembled) applyAssistant(`（出错）${msg}`);
        }
      } finally {
        if (!sawDone && !assembled && !sawError) {
          /* aborted */
        }
        setStreaming(false);
      }
    },
    [conversationId, identity, messages, push, streaming, toast],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void send(input);
  };

  const overlayOpen = page !== "chat";
  const settingsOpen = page === "settings" || page === "account" || page === "identity";
  const historyOpen = page === "history";

  return (
    <div className={`agent-screen${overlayOpen ? " agent-screen--push" : ""}`}>
      <div
        className={`agent-layer agent-layer-chat${overlayOpen ? " is-back" : " is-front"}`}
        aria-hidden={overlayOpen}
      >
        <header className="agent-nav">
          <button
            type="button"
            className="agent-nav-icon"
            aria-label="对话记录"
            onClick={() => {
              haptics.tap();
              push("history");
            }}
          >
            <History size={20} strokeWidth={2} absoluteStrokeWidth aria-hidden />
          </button>
          <div className="agent-nav-brand">
            <span className="agent-nav-face" aria-hidden>
              <img src="/avatars/anzai.png" alt="" width={28} height={28} />
            </span>
            <h1 className="agent-nav-title">安崽</h1>
          </div>
          <div className="agent-nav-actions">
            <button
              type="button"
              className="agent-nav-icon"
              aria-label="新对话"
              disabled={streaming}
              onClick={() => void startNewConversation()}
            >
              <Plus size={20} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            </button>
            <button
              type="button"
              className="agent-nav-icon"
              aria-label="设置"
              onClick={() => {
                haptics.tap();
                push("settings");
              }}
            >
              <Settings size={20} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            </button>
          </div>
        </header>

        <section className="agent-thread" aria-label="对话">
          <div className="agent-thread-body" ref={threadRef}>
            {messages.length === 0 ? (
              <div className="agent-thread-empty">
                <MessageSquare size={22} strokeWidth={1.75} absoluteStrokeWidth aria-hidden />
                <p>{greeting || "嗨嗨，安崽来啦～想聊啥跟安崽说呀。"}</p>
              </div>
            ) : null}
            {messages.map((m) => (
              <div
                key={m.id}
                className={`agent-bubble-row ${m.role === "user" ? "is-user" : "is-bot"}`}
              >
                {m.role === "assistant" ? (
                  <span className="agent-avatar" aria-hidden>
                    <img src="/avatars/anzai.png" alt="" width={32} height={32} />
                  </span>
                ) : null}
                <div
                  className={`agent-bubble ${m.role === "user" ? "agent-bubble-user" : "agent-bubble-bot"}`}
                >
                  {m.toolNote ? <div className="agent-tool-note">{m.toolNote}</div> : null}
                  {m.content || (streaming ? "…" : "")}
                </div>
              </div>
            ))}
          </div>
        </section>

        <form className="agent-composer-shell" onSubmit={onSubmit}>
          <input
            className="agent-composer-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onFocus={() => {
              window.requestAnimationFrame(() => {
                const el = threadRef.current;
                if (el) el.scrollTop = el.scrollHeight;
              });
              window.setTimeout(() => {
                const el = threadRef.current;
                if (el) el.scrollTop = el.scrollHeight;
              }, 280);
            }}
            placeholder="跟安崽聊仓位…"
            disabled={streaming}
            autoCapitalize="none"
            autoCorrect="off"
            enterKeyHint="send"
          />
          <button type="submit" className="agent-send" disabled={streaming || !input.trim()}>
            发送
          </button>
        </form>
      </div>

      {historyOpen ? (
        <div className="agent-layer agent-layer-settings is-front">
          <AgentHistory
            activeId={conversationId}
            onBack={pop}
            onSelect={(id) => void switchConversation(id)}
            onClosedActive={(nextId) => void switchConversation(nextId)}
            onDeleted={(nextId) => void switchConversation(nextId)}
          />
        </div>
      ) : null}

      {settingsOpen ? (
        <div className="agent-layer agent-layer-settings is-front">
          <AgentSettings
            page={page as AgentSettingsPage}
            identity={identity}
            onBack={pop}
            onNavigate={push}
            onIdentitySaved={onIdentitySaved}
            onClearHistory={() => void clearHistory()}
          />
        </div>
      ) : null}
    </div>
  );
}
