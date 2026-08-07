"use client";

import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { History, MessageSquare, Mic, MicOff, Plus, Settings } from "@/components/ui/icons";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { api } from "@/lib/api";
import { haptics } from "@/lib/haptics";
import { useSpeechDictation } from "@/hooks/useSpeechDictation";
import { useTabActive } from "@/hooks/useTabActive";
import { cacheDelete, cachePeek, cacheSet, cacheSWR, PrefetchKeys, PrefetchTtl } from "@/lib/prefetch";
import { notifyAnalysisJob } from "@/lib/analysisEvents";
import { useShellStack } from "@/hooks/useShellStack";
import { ShellBase, ShellLayer, ShellRoot } from "@/components/layout/ShellStack";
import type { AnzaiIdentity } from "@/lib/types";
import { AgentHistory } from "@/features/agent/AgentHistory";
import {
  AgentResultCards,
  type AgentCard,
} from "@/features/agent/AgentResultCards";
import { AgentAnalysisWait } from "@/features/agent/AgentAnalysisWait";
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
type AgentSessionPayload = Awaited<ReturnType<typeof api.getAgentSession>>;

function greetBubble(text: string): ChatMsg {
  return {
    id: `greet-${Date.now()}`,
    role: "assistant",
    content: text || "嗨嗨，安崽来啦～想聊啥跟安崽说呀。",
  };
}

function messagesFromSession(s: AgentSessionPayload, greet: string): ChatMsg[] {
  const saved = (s.messages || [])
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m) => ({
      id: m.id || `m-${m.role}-${m.content.slice(0, 12)}`,
      role: m.role as "user" | "assistant",
      content: m.content,
    }));
  if (saved.length > 0) return saved;
  return [greetBubble(greet || s.greeting || "")];
}

function isAgentCard(raw: unknown): raw is AgentCard {
  if (!raw || typeof raw !== "object") return false;
  const kind = (raw as { kind?: string }).kind;
  return kind === "portfolio" || kind === "rebalance" || kind === "analysis";
}

function runningAnalysisCard(cards: AgentCard[] | undefined): Extract<AgentCard, { kind: "analysis" }> | null {
  const c = cards?.find((x) => x.kind === "analysis");
  if (!c || c.kind !== "analysis") return null;
  if (c.status && c.status !== "running") return null;
  return c;
}

/** 输入框上方预制常见问法（短文案，避免撑宽手机框） */
const AGENT_QUICK_CHIPS = [
  { label: "分析仓库", send: "帮我分析下仓库" },
  { label: "今天大盘", send: "今天大盘怎么样" },
  { label: "我的仓位", send: "看看我的仓位" },
  { label: "黄金现价", send: "黄金现在什么价" },
] as const;

/** 安崽真人对话：多会话 + 气泡线程 + Push 设置 */
export default function AgentScreen() {
  const { toast } = useOverlay();
  const tabActive = useTabActive("/agent");
  const { page, overlayOpen, push, pop, popSoft, reset } = useShellStack<StackPage>({
    root: "chat",
  });
  const [identity, setIdentity] = useState<AnzaiIdentity | null>(() => {
    const s = cachePeek<AgentSessionPayload>(PrefetchKeys.agentSession);
    return s?.identity ?? null;
  });
  const [greeting, setGreeting] = useState(() => {
    const s = cachePeek<AgentSessionPayload>(PrefetchKeys.agentSession);
    return s?.greeting || "嗨嗨，安崽来啦～先选身份，安崽好陪你聊。";
  });
  const [conversationId, setConversationId] = useState<number | null>(() => {
    const s = cachePeek<AgentSessionPayload>(PrefetchKeys.agentSession);
    return s?.conversation_id ?? null;
  });
  const [messages, setMessages] = useState<ChatMsg[]>(() => {
    const s = cachePeek<AgentSessionPayload>(PrefetchKeys.agentSession);
    return s ? messagesFromSession(s, s.greeting || "") : [];
  });
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const threadRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const streamingRef = useRef(false);
  streamingRef.current = streaming;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;
  const conversationIdRef = useRef(conversationId);
  conversationIdRef.current = conversationId;
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const greetingRef = useRef(greeting);
  greetingRef.current = greeting;
  const voiceBaseRef = useRef("");

  /** Keep prefetch cache aligned with live thread so tab re-focus SWR cannot wipe chat. */
  const writebackSessionCache = useCallback((msgs: ChatMsg[], cid: number | null) => {
    const prev = cachePeek<AgentSessionPayload>(PrefetchKeys.agentSession);
    const persisted = msgs
      .filter(
        (m) =>
          m.id !== "greet" &&
          (m.role === "user" || m.role === "assistant") &&
          Boolean(m.content?.trim()),
      )
      .map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
      }));
    cacheSet(PrefetchKeys.agentSession, {
      ...(prev || {}),
      identity: identityRef.current ?? prev?.identity,
      greeting: greetingRef.current || prev?.greeting || "",
      conversation_id: cid ?? prev?.conversation_id ?? null,
      messages: persisted,
    } as AgentSessionPayload);
  }, []);

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

  const applySessionMessages = useCallback(
    (saved: ChatMsg[], greet: string, force = false) => {
      setMessages((prev) => {
        // 发送中勿被 session 回灌盖掉；用 ref 避免 streaming 变化重绑 loadSession
        if (!force && streamingRef.current && prev.some((m) => m.role === "user")) {
          return prev;
        }
        // 非强制：本地线程比缓存更长（刚聊完尚未网络同步）时保留本地
        if (!force) {
          const prevReal = prev.filter((m) => m.id !== "greet" && m.content?.trim());
          const savedReal = saved.filter((m) => m.id !== "greet" && m.content?.trim());
          if (prevReal.length > savedReal.length) return prev;
        }
        if (saved.length > 0) return saved;
        return [greetBubble(greet)];
      });
    },
    [],
  );

  const applySessionPayload = useCallback(
    (s: AgentSessionPayload, force = false) => {
      cacheSet(PrefetchKeys.agentSession, s);
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
      applySessionMessages(saved, s.greeting || "", force);
    },
    [applySessionMessages],
  );

  const loadSession = useCallback(
    async (cid?: number | null, opts?: { force?: boolean }) => {
      try {
        if (cid != null && cid > 0) {
          const s = await api.getAgentSession(cid);
          applySessionPayload(s, opts?.force);
          return;
        }
        if (opts?.force) {
          const s = await api.getAgentSession();
          applySessionPayload(s, true);
          return;
        }
        await cacheSWR(
          PrefetchKeys.agentSession,
          () => api.getAgentSession(),
          PrefetchTtl.agentSession,
          (s) => applySessionPayload(s, false),
        );
      } catch {
        try {
          const id = await api.getIdentity();
          setIdentity(id);
        } catch {
          /* ignore */
        }
      }
    },
    [applySessionPayload],
  );

  // 进入安崽 tab 时 SWR 拉会话；TabCache 保活，勿在 streaming 中重绑 abort
  useEffect(() => {
    if (!tabActive) return;
    void loadSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tab focus only
  }, [tabActive]);

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
      reset();
      haptics.success();
      toast("已开新对话", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "新开失败", "warning");
    }
  }, [loadSession, reset, streaming, toast]);

  const switchConversation = useCallback(
    async (id: number) => {
      if (streaming) {
        toast("等这句说完再切换", "warning");
        return;
      }
      abortRef.current?.abort();
      await loadSession(id, { force: true });
      reset();
      haptics.tap();
    },
    [loadSession, reset, streaming, toast],
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

      // Invalidate warm session so tab re-focus cannot restore pre-chat snapshot
      cacheDelete(PrefetchKeys.agentSession);

      let assembled = "";
      let sawError = false;
      let sawDone = false;
      let liveCid = conversationId;

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
      /** Analysis wait panel mode — hide step spam & duplicate wait copy. */
      let analysisWaiting = false;

      const pushStep = (label: string) => {
        if (!label || analysisWaiting) return;
        if (toolSteps[toolSteps.length - 1] === label) return;
        toolSteps.push(label);
        if (toolSteps.length > 8) toolSteps.shift();
      };

      try {
        await api.streamAgentChat(
          history,
          (ev) => {
            if (ev.type === "meta" && typeof ev.conversation_id === "number") {
              liveCid = ev.conversation_id;
              setConversationId(ev.conversation_id);
            }
            if (ev.type === "tool_start" || ev.type === "tool_status") {
              const label = typeof ev.label === "string" ? ev.label : "查询中";
              toolNote = label;
              if (
                label.includes("分析") ||
                label.includes("整理结论") ||
                label.includes("委员会")
              ) {
                analysisWaiting = true;
              }
              if (label.includes("整理回答") || label.includes("整理结论")) {
                // keep waiting panel until tokens arrive; just refresh status
                analysisWaiting = true;
              }
              pushStep(label);
              applyAssistant(assembled, {
                toolNote: analysisWaiting ? toolNote : toolNote || undefined,
                toolSteps: analysisWaiting ? [] : [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "tool_result") {
              const label = typeof ev.label === "string" ? ev.label : "已查到";
              toolNote = `${label} · 完成`;
              pushStep(`${label} · 完成`);
              applyAssistant(assembled, {
                toolNote: analysisWaiting ? toolNote : toolNote || undefined,
                toolSteps: analysisWaiting ? [] : [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "card" && isAgentCard(ev.card)) {
              const kind = ev.card.kind;
              const idx = cards.findIndex((c) => c.kind === kind);
              if (idx >= 0) cards[idx] = ev.card;
              else cards.push(ev.card);
              if (ev.card.kind === "analysis") {
                analysisWaiting = true;
                toast("安崽开始分析了", "success");
                notifyAnalysisJob({
                  phase: "start",
                  jobId: typeof ev.card.job_id === "number" ? ev.card.job_id : undefined,
                  scope: ev.card.scope,
                });
              }
              applyAssistant(assembled, {
                toolNote: analysisWaiting ? toolNote || "安崽分析中" : toolNote || undefined,
                toolSteps: analysisWaiting ? [] : [...toolSteps],
                cards: [...cards],
              });
            } else if (ev.type === "token") {
              const piece = typeof ev.text === "string" ? ev.text : "";
              if (!piece) return;
              // 跳过旧版等待文案 token（若仍有）
              if (
                analysisWaiting &&
                !assembled &&
                (piece.includes("请耐心等待") || piece.includes("快马加鞭"))
              ) {
                return;
              }
              if (analysisWaiting && assembled === "") {
                analysisWaiting = false;
                // mark analysis card done for UI
                const ai = cards.findIndex((c) => c.kind === "analysis");
                if (ai >= 0 && cards[ai].kind === "analysis") {
                  cards[ai] = { ...cards[ai], status: "done", title: "分析报告" };
                  notifyAnalysisJob({
                    phase: "done",
                    jobId: cards[ai].job_id,
                    scope: cards[ai].scope,
                  });
                }
              }
              assembled += piece;
              applyAssistant(assembled, {
                toolNote: "",
                toolSteps: [],
                cards: [...cards],
              });
            } else if (ev.type === "final" && typeof ev.text === "string" && ev.text) {
              assembled = ev.text;
              applyAssistant(assembled, {
                toolNote: "",
                toolSteps: [],
                cards: [...cards],
              });
            } else if (ev.type === "error") {
              sawError = true;
              analysisWaiting = false;
              const msg = ev.message || "生成失败";
              toast(msg, "warning");
              if (!assembled) {
                assembled = `（出错）${msg}`;
                applyAssistant(assembled, {
                  toolSteps: [],
                  cards: [...cards],
                });
              }
            } else if (ev.type === "done") {
              sawDone = true;
              analysisWaiting = false;
              const ai = cards.findIndex((c) => c.kind === "analysis");
              if (ai >= 0 && cards[ai].kind === "analysis") {
                if (cards[ai].status === "running") {
                  cards[ai] = { ...cards[ai], status: "done" };
                }
                notifyAnalysisJob({
                  phase: "done",
                  jobId: cards[ai].job_id,
                  scope: cards[ai].scope,
                });
              }
              if (assembled) {
                applyAssistant(assembled, {
                  toolNote: "",
                  toolSteps: [],
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
        const cid = liveCid ?? conversationIdRef.current;
        const fallbackBody =
          assembled.trim() ||
          (sawError
            ? "（出错）"
            : "（没有收到模型内容，请稍后重试或检查 /admin/llm 连接）");
        const snapshot: ChatMsg[] = [
          ...next,
          {
            id: assistantId,
            role: "assistant",
            content: assembled.trim() ? assembled : sawError || !sawDone ? fallbackBody : "",
            ...(cards.length ? { cards: [...cards] } : {}),
          },
        ];
        // Prefer composed snapshot — setMessages may not have flushed yet
        writebackSessionCache(
          snapshot.filter((m) => m.role === "user" || Boolean(m.content?.trim())),
          cid,
        );
      }
    },
    [conversationId, identity, messages, push, stopVoice, streaming, toast, writebackSessionCache],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    stopVoice();
    void send(input);
  };

  const settingsOpen = page === "settings" || page === "account" || page === "identity" || page === "notify";
  const historyOpen = page === "history";

  return (
    <ShellRoot className="agent-screen" pushed={overlayOpen}>
      <ShellBase className="agent-layer-chat" behind={overlayOpen}>
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
              {messages.map((m) => {
                const waitCard =
                  streaming && m.role === "assistant" && !m.content.trim()
                    ? runningAnalysisCard(m.cards)
                    : null;
                const showWait = Boolean(waitCard);
                const otherCards = (m.cards || []).filter(
                  (c) => !(c.kind === "analysis" && (c.status === "running" || showWait)),
                );
                return (
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
                    className={`agent-bubble ${m.role === "user" ? "agent-bubble-user" : "agent-bubble-bot"}${showWait ? " agent-bubble-wait" : ""}`}
                  >
                    {showWait && waitCard ? (
                      <AgentAnalysisWait card={waitCard} status={m.toolNote} />
                    ) : (
                      <>
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
                        {otherCards.length > 0 ? (
                          <AgentResultCards cards={otherCards} />
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
                      </>
                    )}
                  </div>
                </div>
                );
              })}
            </div>
          </section>

          <form className="agent-composer-shell" onSubmit={onSubmit}>
            <div className="agent-chips" role="list" aria-label="常见问题">
              {AGENT_QUICK_CHIPS.map((q) => (
                <button
                  key={q.send}
                  type="button"
                  className="agent-chip"
                  role="listitem"
                  disabled={streaming}
                  onClick={() => {
                    haptics.tap();
                    void send(q.send);
                  }}
                >
                  {q.label}
                </button>
              ))}
            </div>
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
      </ShellBase>

      {historyOpen ? (
        <ShellLayer className="agent-layer-settings" onEdgeBack={popSoft}>
          <AgentHistory
            activeId={conversationId}
            onBack={pop}
            onSelect={(id) => void switchConversation(id)}
            onClosedActive={(nextId) => void switchConversation(nextId)}
            onDeleted={(nextId) => void switchConversation(nextId)}
          />
        </ShellLayer>
      ) : null}

      {settingsOpen ? (
        <ShellLayer className="agent-layer-settings" onEdgeBack={popSoft}>
          <AgentSettings
            page={page as AgentSettingsPage}
            identity={identity}
            onBack={pop}
            onNavigate={push}
            onIdentitySaved={onIdentitySaved}
            onClearHistory={() => void clearHistory()}
          />
        </ShellLayer>
      ) : null}
    </ShellRoot>
  );
}
