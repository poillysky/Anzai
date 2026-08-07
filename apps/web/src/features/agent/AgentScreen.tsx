"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { History, MessageSquare, Mic, MicOff, Plus, Settings } from "@/components/ui/icons";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { api } from "@/lib/api";
import { haptics } from "@/lib/haptics";
import { useSpeechDictation } from "@/hooks/useSpeechDictation";
import type { AnzaiIdentity } from "@/lib/types";
import { AgentHistory } from "@/features/agent/AgentHistory";
import {
  AgentResultCards,
  type AgentCard,
} from "@/features/agent/AgentResultCards";
import {
  AgentSettings,
  type AgentSettingsPage,
} from "@/features/agent/AgentSettings";

type ChatMsg = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolNote?: string;
  toolSteps?: string[];
  cards?: AgentCard[];
};
type StackPage = "chat" | "history" | AgentSettingsPage;

function greetBubble(text: string): ChatMsg {
  return {
    id: `greet-${Date.now()}`,
    role: "assistant",
    content: text || "嗨嗨，安崽来啦～想聊啥跟安崽说呀。",
  };
}

function isAgentCard(raw: unknown): raw is AgentCard {
  if (!raw || typeof raw !== "object") return false;
  const kind = (raw as { kind?: string }).kind;
  return kind === "portfolio" || kind === "rebalance" || kind === "analysis";
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
  const voiceBaseRef = useRef("");

  const onVoiceTranscript = useCallback((text: string) => {
    const base = voiceBaseRef.current;
    const joined = !base ? text : /[\s\u3000]$/.test(base) ? `${base}${text}` : `${base} ${text}`;
    setInput(joined);
  }, []);

  const {
    listening: voiceListening,
    toggle: toggleVoice,
    stop: stopVoice,
  } = useSpeechDictation({
    lang: "zh-CN",
    onTranscript: onVoiceTranscript,
    onUnsupported: () => {
      toast("当前环境不支持网页语音，可用系统键盘上的麦克风", "warning");
    },
    onError: (msg) => toast(msg, "warning"),
  });

  const onMicClick = useCallback(() => {
    if (streaming) return;
    haptics.tap();
    if (voiceListening) {
      stopVoice();
      return;
    }
    voiceBaseRef.current = input;
    toggleVoice();
  }, [input, streaming, stopVoice, toggleVoice, voiceListening]);

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

  // 仅真正卸载时中断（登录页卸掉 TabCache）。Tab 切换由 TabCache 保活，不应走到这里。
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const pinThreadBottom = useCallback(() => {
    const el = threadRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, []);

  useEffect(() => {
    pinThreadBottom();
  }, [messages, streaming, pinThreadBottom]);

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
      await loadSession(created.conversation.id, { force: true });
      haptics.success();
      toast("对话记录已清空", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "清空失败", "warning");
    }
  }, [loadSession, toast]);

  const startNewConversation = useCallback(async () => {
    if (streaming) {
      toast("等这句说完再开新对话", "warning");
      return;
    }
    try {
      abortRef.current?.abort();
      const res = await api.createAgentConversation(true);
      setConversationId(res.conversation.id);
      await loadSession(res.conversation.id, { force: true });
      setStack(["chat"]);
      haptics.success();
      toast("已开新对话", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "新开失败", "warning");
    }
  }, [loadSession, streaming, toast]);

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
      stopVoice();
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

      const applyAssistant = (
        body: string,
        opts?: { toolNote?: string; toolSteps?: string[]; cards?: AgentCard[] },
      ) => {
        setMessages((prev) => {
          const idx = prev.findIndex((m) => m.id === assistantId);
          const patch: ChatMsg = {
            id: assistantId,
            role: "assistant",
            content: body,
            ...(opts?.toolNote !== undefined ? { toolNote: opts.toolNote } : {}),
            ...(opts?.toolSteps !== undefined ? { toolSteps: opts.toolSteps } : {}),
            ...(opts?.cards !== undefined ? { cards: opts.cards } : {}),
          };
          if (idx >= 0) {
            const copy = prev.slice();
            copy[idx] = { ...copy[idx], ...patch, content: body };
            if (opts?.toolNote === undefined) delete (copy[idx] as { toolNote?: string }).toolNote;
            if (opts?.toolNote !== undefined) copy[idx].toolNote = opts.toolNote;
            if (opts?.toolSteps !== undefined) copy[idx].toolSteps = opts.toolSteps;
            if (opts?.cards !== undefined) copy[idx].cards = opts.cards;
            return copy;
          }
          return [...prev.filter((m) => m.id !== assistantId), patch];
        });
      };

      let toolNote = "";
      const toolSteps: string[] = [];
      const cards: AgentCard[] = [];
      /** Placeholder ack until model tokens arrive (analysis start). */
      let analysisAckPinned = false;

      const pushStep = (label: string) => {
        if (!label) return;
        if (toolSteps[toolSteps.length - 1] === label) return;
        toolSteps.push(label);
        if (toolSteps.length > 8) toolSteps.shift();
      };

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
              pushStep(label);
              applyAssistant(assembled, {
                toolNote,
                toolSteps: [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "tool_result") {
              const label = typeof ev.label === "string" ? ev.label : "已查到";
              toolNote = `${label} · 完成`;
              pushStep(`${label} · 完成`);
              applyAssistant(assembled, {
                toolNote,
                toolSteps: [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "card" && isAgentCard(ev.card)) {
              const kind = ev.card.kind;
              const idx = cards.findIndex((c) => c.kind === kind);
              if (idx >= 0) cards[idx] = ev.card;
              else cards.push(ev.card);
              if (ev.card.kind === "analysis") {
                const ack =
                  (typeof ev.card.ack === "string" && ev.card.ack.trim()) ||
                  "已经在分析了，你可以继续聊；去「分析」页也能看进度。";
                toast(ack, "success");
                if (!assembled) {
                  assembled = ack;
                  analysisAckPinned = true;
                }
              }
              applyAssistant(assembled, {
                toolNote: toolNote || undefined,
                toolSteps: [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "token") {
              const piece = typeof ev.text === "string" ? ev.text : "";
              if (!piece) return;
              if (analysisAckPinned) {
                assembled = piece;
                analysisAckPinned = false;
              } else {
                assembled += piece;
              }
              applyAssistant(assembled, {
                toolNote: toolNote || undefined,
                toolSteps: [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "error") {
              sawError = true;
              const msg = ev.message || "生成失败";
              toast(msg, "warning");
              if (!assembled) {
                assembled = `（出错）${msg}`;
                applyAssistant(assembled, {
                  toolSteps: [...toolSteps],
                  cards: [...cards],
                });
              }
            } else if (ev.type === "done") {
              sawDone = true;
              if (assembled) {
                applyAssistant(assembled, {
                  toolNote: "",
                  toolSteps: [...toolSteps],
                  cards: [...cards],
                });
              }
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
    [conversationId, identity, messages, push, stopVoice, streaming, toast],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    stopVoice();
    void send(input);
  };

  const overlayOpen = page !== "chat";
  const settingsOpen = page === "settings" || page === "account" || page === "identity" || page === "notify";
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

        <div className="agent-kb-lift">
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
                    {m.toolSteps && m.toolSteps.length > 0 ? (
                      <div className="agent-tool-steps" aria-label="查询步骤">
                        {m.toolSteps.map((step, i) => (
                          <span key={`${step}-${i}`} className="agent-tool-step">
                            {step}
                          </span>
                        ))}
                      </div>
                    ) : m.toolNote ? (
                      <div className="agent-tool-note">{m.toolNote}</div>
                    ) : null}
                    {m.cards && m.cards.length > 0 ? (
                      <AgentResultCards cards={m.cards} />
                    ) : null}
                    {m.content ? (
                      <div className="agent-bubble-text">{m.content}</div>
                    ) : streaming && m.role === "assistant" ? (
                      <div
                        className="agent-thinking"
                        aria-live="polite"
                        aria-label="安崽思考中"
                      >
                        <span className="agent-thinking-label">安崽在想</span>
                        <span className="agent-thinking-dots" aria-hidden>
                          <i />
                          <i />
                          <i />
                        </span>
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <form className="agent-composer-shell" onSubmit={onSubmit}>
            <div className="agent-composer-row">
              <button
                type="button"
                className="agent-mic"
                data-listening={voiceListening ? "1" : "0"}
                aria-label={voiceListening ? "停止语音输入" : "语音输入"}
                aria-pressed={voiceListening}
                disabled={streaming}
                onClick={onMicClick}
              >
                {voiceListening ? (
                  <MicOff size={20} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                ) : (
                  <Mic size={20} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                )}
              </button>
              <input
                className="agent-composer-input"
                value={input}
                onChange={(e) => {
                  if (voiceListening) stopVoice();
                  setInput(e.target.value);
                }}
                onFocus={() => {
                  window.requestAnimationFrame(pinThreadBottom);
                }}
                placeholder={voiceListening ? "正在听…" : "跟安崽聊仓位…"}
                disabled={streaming}
                autoCapitalize="none"
                autoCorrect="off"
                enterKeyHint="send"
              />
              <button type="submit" className="agent-send" disabled={streaming || !input.trim()}>
                发送
              </button>
            </div>
          </form>
        </div>
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
