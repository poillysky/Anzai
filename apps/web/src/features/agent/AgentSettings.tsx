"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bell,
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  EyeOff,
  KeyRound,
  LogOut,
  Trash2,
  UserRound,
} from "@/components/ui/icons";
import { CenterModal } from "@/components/overlay/CenterModal";
import { useOverlay } from "@/components/overlay/OverlayContext";
import { api } from "@/lib/api";
import { clearSession, getStoredUser, setSession, getAccessToken } from "@/lib/auth";
import { haptics } from "@/lib/haptics";
import type { AnzaiIdentity, AnzaiIdentityRole, NotifySettings } from "@/lib/types";
import type { AuthUser } from "@/lib/auth";

export type AgentSettingsPage = "settings" | "account" | "identity" | "notify";

function errMsg(e: unknown, fallback = "操作失败"): string {
  if (!(e instanceof Error)) return fallback;
  try {
    const j = JSON.parse(e.message) as { detail?: string | { msg?: string }[] };
    if (typeof j.detail === "string") return j.detail;
  } catch {
    /* plain text */
  }
  return e.message || fallback;
}

/** 与后端 identity.IDENTITY_ROLES 对齐的兜底目录（session 未带 roles 时仍可选） */
const FALLBACK_ROLES: AnzaiIdentityRole[] = [
  { id: "dad", label: "爸爸", call_as: "爸" },
  { id: "mom", label: "妈妈", call_as: "妈" },
  { id: "grandpa", label: "爷爷", call_as: "爷爷" },
  { id: "grandma", label: "奶奶", call_as: "奶奶" },
  { id: "brother", label: "哥哥", call_as: "哥" },
  { id: "sister", label: "姐姐", call_as: "姐" },
  { id: "friend", label: "朋友", call_as: "你" },
  { id: "wife", label: "老婆", call_as: "老婆" },
  { id: "partner", label: "伴侣", call_as: "亲爱的" },
  { id: "husband", label: "老公", call_as: "老公" },
  { id: "self", label: "就是我自己", call_as: "你" },
  { id: "custom", label: "自定义", call_as: "" },
];

type Props = {
  page: AgentSettingsPage;
  identity: AnzaiIdentity | null;
  onBack: () => void;
  onNavigate: (page: AgentSettingsPage) => void;
  onIdentitySaved: (data: AnzaiIdentity, greeting: string) => void;
  onClearHistory?: () => void;
};

const TITLES: Record<AgentSettingsPage, string> = {
  settings: "设置",
  account: "用户账号",
  identity: "身份设定",
  notify: "微信日报",
};

const WEEKDAY_OPTS: { id: number; label: string }[] = [
  { id: 0, label: "一" },
  { id: 1, label: "二" },
  { id: 2, label: "三" },
  { id: 3, label: "四" },
  { id: 4, label: "五" },
  { id: 5, label: "六" },
  { id: 6, label: "日" },
];

function parseWeekdays(raw: string): Set<number> {
  const set = new Set<number>();
  for (const p of (raw || "").split(",")) {
    const n = Number(p.trim());
    if (Number.isInteger(n) && n >= 0 && n <= 6) set.add(n);
  }
  return set.size ? set : new Set([0, 1, 2, 3, 4]);
}

function encodeWeekdays(set: Set<number>): string {
  return [...set].sort((a, b) => a - b).join(",");
}

/** 安崽内 Push 设置：账号 · 身份（学 BrewStory 列表 Push，不含 Admin LLM） */
export function AgentSettings({
  page,
  identity,
  onBack,
  onNavigate,
  onIdentitySaved,
  onClearHistory,
}: Props) {
  const { toast } = useOverlay();
  const [profile, setProfile] = useState<AuthUser | null>(() => getStoredUser());
  const [saving, setSaving] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [customLabel, setCustomLabel] = useState("");
  const [pwdOpen, setPwdOpen] = useState(false);
  const [pwdCurrent, setPwdCurrent] = useState("");
  const [pwdNew, setPwdNew] = useState("");
  const [pwdConfirm, setPwdConfirm] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [roles, setRoles] = useState<AnzaiIdentityRole[]>(() =>
    identity?.roles?.length ? identity.roles : FALLBACK_ROLES,
  );
  const [localIdentity, setLocalIdentity] = useState<AnzaiIdentity | null>(identity);
  const [notify, setNotify] = useState<NotifySettings | null>(null);
  const [notifyToken, setNotifyToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [notifyBusy, setNotifyBusy] = useState(false);

  useEffect(() => {
    setLocalIdentity(identity);
    if (identity?.roles?.length) setRoles(identity.roles);
  }, [identity]);

  useEffect(() => {
    if (page !== "account") return;
    let cancelled = false;
    void (async () => {
      try {
        const me = await api.me();
        if (cancelled) return;
        setProfile(me);
        const token = getAccessToken();
        if (token) setSession(token, me);
      } catch {
        /* keep cached */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [page]);

  useEffect(() => {
    if (page !== "identity" && page !== "settings") return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.getIdentity();
        if (cancelled) return;
        setLocalIdentity(data);
        if (data.roles?.length) setRoles(data.roles);
        // Sync parent identity/roles without resetting chat (empty greeting)
        onIdentitySaved(data, "");
      } catch {
        /* keep fallback catalog */
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh when page opens
  }, [page]);

  const submitPassword = async () => {
    const cur = pwdCurrent;
    const next = pwdNew.trim();
    if (next.length < 4) {
      toast("新密码至少 4 位", "warning");
      return;
    }
    if (next !== pwdConfirm.trim()) {
      toast("两次输入的新密码不一致", "warning");
      return;
    }
    setSaving(true);
    try {
      await api.changePassword(cur, next);
      haptics.success();
      toast("密码已更新", "success");
      setPwdOpen(false);
      setPwdCurrent("");
      setPwdNew("");
      setPwdConfirm("");
      setShowPwd(false);
    } catch (e) {
      toast(errMsg(e, "修改密码失败"), "warning");
    } finally {
      setSaving(false);
    }
  };

  const pickRole = async (role: string, label = "") => {
    setSaving(true);
    try {
      const data = await api.putIdentity(role, label);
      setLocalIdentity(data);
      if (data.roles?.length) setRoles(data.roles);
      const s = await api.getAgentSession();
      onIdentitySaved(data, s.greeting || "");
      haptics.success();
      toast(
        role === "wife"
          ? "已设为老婆 · 对话将注入对老婆的语气"
          : `已设为${data.label || "该身份"} · 语气随【关系】提示词变化`,
        "success",
      );
      setCustomOpen(false);
    } catch (e) {
      toast(errMsg(e, "保存失败"), "warning");
    } finally {
      setSaving(false);
    }
  };

  useEffect(() => {
    if (page !== "notify" && page !== "settings") return;
    let cancelled = false;
    void (async () => {
      try {
        const data = await api.getNotifySettings();
        if (cancelled) return;
        setNotify(data);
        if (page === "notify") setNotifyToken("");
      } catch (e) {
        if (!cancelled && page === "notify") {
          toast(errMsg(e, "加载微信日报设置失败"), "warning");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const notifyMeta = useMemo(() => {
    if (!notify) return "按账号独立配置";
    if (!notify.enabled) return "未开启";
    const ch =
      notify.channels.find((c) => c.id === notify.channel)?.label || notify.channel;
    const hm = `${String(notify.hour).padStart(2, "0")}:${String(notify.minute).padStart(2, "0")}`;
    return notify.configured ? `${ch} · ${hm}` : `${ch} · 待填密钥`;
  }, [notify]);

  const saveNotify = async (patch: Parameters<typeof api.putNotifySettings>[0]) => {
    setNotifyBusy(true);
    try {
      const data = await api.putNotifySettings(patch);
      setNotify(data);
      if (patch.token) setNotifyToken("");
      haptics.success();
      toast("已保存", "success");
    } catch (e) {
      toast(errMsg(e, "保存失败"), "warning");
    } finally {
      setNotifyBusy(false);
    }
  };

  const identityDetail = useMemo(() => {
    const id = localIdentity || identity;
    if (!id?.configured) return "未设置";
    return id.label || "已设置";
  }, [identity, localIdentity]);

  const current = localIdentity || identity;
  const user = profile;

  return (
    <div className="agent-settings agent-settings-open">
      <header className="agent-nav">
        <button
          type="button"
          className="agent-nav-back"
          onClick={() => {
            haptics.tap();
            onBack();
          }}
          aria-label="返回"
        >
          <ChevronLeft size={22} strokeWidth={2} absoluteStrokeWidth aria-hidden />
        </button>
        <h1 className="agent-nav-title">{TITLES[page]}</h1>
        <span className="agent-nav-spacer" aria-hidden />
      </header>

      <div className="agent-settings-body">
        {page === "settings" ? (
          <section className="inset-group agent-settings-list" aria-label="设置">
            <button
              type="button"
              className="agent-settings-row"
              onClick={() => {
                haptics.tap();
                onNavigate("account");
              }}
            >
              <span className="agent-settings-row-main">
                <span className="agent-settings-row-label">用户账号</span>
                <span className="agent-settings-row-meta">
                  {user?.username ? `${user.username} · 改密 / 退出` : "改密 / 退出"}
                </span>
              </span>
              <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            </button>
            <button
              type="button"
              className="agent-settings-row"
              onClick={() => {
                haptics.tap();
                onNavigate("identity");
              }}
            >
              <span className="agent-settings-row-main">
                <span className="agent-settings-row-label">身份设定</span>
                <span className="agent-settings-row-meta">{identityDetail}</span>
              </span>
              <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            </button>
            <button
              type="button"
              className="agent-settings-row"
              onClick={() => {
                haptics.tap();
                onNavigate("notify");
              }}
            >
              <span className="agent-settings-row-main">
                <span className="agent-settings-row-label">微信日报</span>
                <span className="agent-settings-row-meta">{notifyMeta}</span>
              </span>
              <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
            </button>
            <button
              type="button"
              className="agent-settings-row agent-settings-row-danger"
              onClick={() => {
                haptics.tap();
                if (!onClearHistory) return;
                if (typeof window !== "undefined" && !window.confirm("清空本账号在服务器上的对话记录？")) {
                  return;
                }
                onClearHistory();
              }}
            >
              <span className="agent-settings-row-main">
                <span className="agent-settings-row-label">
                  <Trash2 size={15} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                  清空对话记录
                </span>
                <span className="agent-settings-row-meta">按账号保存在服务器</span>
              </span>
            </button>
          </section>
        ) : null}

        {page === "account" ? (
          <>
            <section className="inset-group agent-settings-list" aria-label="账号资料">
              <div className="agent-settings-row agent-settings-row-static">
                <span className="agent-settings-row-main">
                  <span className="agent-settings-row-label">用户名</span>
                  <span className="agent-settings-row-meta">{user?.username || "—"}</span>
                </span>
              </div>
              <div className="agent-settings-row agent-settings-row-static">
                <span className="agent-settings-row-main">
                  <span className="agent-settings-row-label">角色</span>
                  <span className="agent-settings-row-meta">
                    {user?.role === "admin" ? "管理员" : "用户"}
                  </span>
                </span>
              </div>
              <div className="agent-settings-row agent-settings-row-static">
                <span className="agent-settings-row-main">
                  <span className="agent-settings-row-label">状态</span>
                  <span className="agent-settings-row-meta">
                    {user?.is_active === false ? "已禁用" : "正常"}
                  </span>
                </span>
              </div>
            </section>

            <p className="agent-settings-lead" style={{ marginTop: 14 }}>
              安全
            </p>
            <section className="inset-group agent-settings-list" aria-label="账号安全">
              <button
                type="button"
                className="agent-settings-row"
                onClick={() => {
                  haptics.tap();
                  setPwdCurrent("");
                  setPwdNew("");
                  setPwdConfirm("");
                  setShowPwd(false);
                  setPwdOpen(true);
                }}
              >
                <span className="agent-settings-row-main">
                  <span className="agent-settings-row-label">
                    <KeyRound size={15} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                    修改密码
                  </span>
                  <span className="agent-settings-row-meta">当前密码验证后更换</span>
                </span>
                <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
              </button>
              <button
                type="button"
                className="agent-settings-row agent-settings-row-danger"
                onClick={async () => {
                  haptics.tap();
                  try {
                    await api.logout();
                  } catch {
                    /* client clears anyway */
                  }
                  clearSession();
                  window.location.href = "/login";
                }}
              >
                <span className="agent-settings-row-main">
                  <span className="agent-settings-row-label">
                    <LogOut size={15} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                    退出登录
                  </span>
                </span>
              </button>
            </section>
          </>
        ) : null}

        {page === "identity" ? (
          <>
            <p className="agent-settings-lead">
              你是安崽的谁？点一项即可；对话会注入【关系】语气（全站仍一套对话预设）。
            </p>
            <section className="inset-group agent-settings-list" aria-label="身份选择">
              {roles.map((r) => {
                const selected =
                  Boolean(current?.configured) &&
                  (r.id === "custom"
                    ? current?.role === "custom"
                    : current?.role === r.id);
                return (
                  <button
                    key={r.id}
                    type="button"
                    className={`agent-settings-row${selected ? " is-selected" : ""}`}
                    disabled={saving}
                    onClick={() => {
                      haptics.tap();
                      if (r.id === "custom") {
                        setCustomLabel(current?.role === "custom" ? current.label : "");
                        setCustomOpen(true);
                        return;
                      }
                      void pickRole(r.id);
                    }}
                  >
                    <span className="agent-settings-row-main">
                      <span className="agent-settings-row-label">
                        <UserRound size={15} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        {r.label}
                      </span>
                      {selected ? (
                        <span className="agent-settings-row-meta">当前</span>
                      ) : null}
                    </span>
                    {selected ? (
                      <Check size={16} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
                    ) : (
                      <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                    )}
                  </button>
                );
              })}
            </section>
          </>
        ) : null}

        {page === "notify" ? (
          <>
            <p className="agent-settings-lead">
              绑定 Server酱 / PushPlus / WxPusher，交易日定时推送本账号仓库分析。各账号互不影响。
            </p>
            {!notify ? (
              <p className="agent-settings-lead">加载中…</p>
            ) : (
              <>
                <section className="inset-group agent-settings-list" aria-label="开关">
                  <button
                    type="button"
                    className="agent-settings-row"
                    disabled={notifyBusy}
                    onClick={() => {
                      haptics.tap();
                      void saveNotify({ enabled: !notify.enabled });
                    }}
                  >
                    <span className="agent-settings-row-main">
                      <span className="agent-settings-row-label">
                        <Bell size={15} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        开启日报
                      </span>
                      <span className="agent-settings-row-meta">
                        {notify.enabled ? "已开" : "已关"}
                      </span>
                    </span>
                    {notify.enabled ? (
                      <Check size={16} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
                    ) : (
                      <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                    )}
                  </button>
                </section>

                <p className="agent-settings-lead" style={{ marginTop: 14 }}>
                  通道
                </p>
                <section className="inset-group agent-settings-list" aria-label="通道">
                  {(notify.channels.length
                    ? notify.channels
                    : [
                        { id: "serverchan", label: "Server酱" },
                        { id: "pushplus", label: "PushPlus" },
                        { id: "wxpusher", label: "WxPusher" },
                      ]
                  ).map((c) => {
                    const selected = notify.channel === c.id;
                    return (
                      <button
                        key={c.id}
                        type="button"
                        className={`agent-settings-row${selected ? " is-selected" : ""}`}
                        disabled={notifyBusy}
                        onClick={() => {
                          haptics.tap();
                          void saveNotify({ channel: c.id });
                        }}
                      >
                        <span className="agent-settings-row-main">
                          <span className="agent-settings-row-label">{c.label}</span>
                          {selected ? (
                            <span className="agent-settings-row-meta">当前</span>
                          ) : null}
                        </span>
                        {selected ? (
                          <Check size={16} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
                        ) : (
                          <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        )}
                      </button>
                    );
                  })}
                </section>

                <p className="agent-settings-lead" style={{ marginTop: 14 }}>
                  密钥（仅本账号）
                </p>
                <section className="inset-group agent-settings-form" aria-label="密钥">
                  <label className="agent-settings-field">
                    <span className="mute" style={{ fontSize: 13 }}>
                      {notify.channel === "serverchan"
                        ? "SendKey"
                        : notify.channel === "pushplus"
                          ? "Token"
                          : "AppToken / SPT"}
                      {notify.token_set ? ` · 已存 ${notify.token_preview}` : ""}
                    </span>
                    <span className="agent-pwd-field">
                      <input
                        type={showToken ? "text" : "password"}
                        value={notifyToken}
                        onChange={(e) => setNotifyToken(e.target.value)}
                        placeholder={notify.token_set ? "留空则保持原密钥" : "粘贴密钥"}
                        autoCapitalize="none"
                        autoCorrect="off"
                        autoComplete="off"
                        style={{ fontSize: 16 }}
                      />
                      <button
                        type="button"
                        className="agent-pwd-toggle"
                        aria-label={showToken ? "隐藏" : "显示"}
                        onClick={() => setShowToken((v) => !v)}
                      >
                        {showToken ? (
                          <EyeOff size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        ) : (
                          <Eye size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        )}
                      </button>
                    </span>
                  </label>
                  {notify.channel === "wxpusher" ? (
                    <label className="agent-settings-field">
                      <span className="mute" style={{ fontSize: 13 }}>
                        UID（AppToken 时必填；SPT 可空）
                      </span>
                      <input
                        type="text"
                        value={notify.wxpusher_uid}
                        onChange={(e) =>
                          setNotify((prev) =>
                            prev ? { ...prev, wxpusher_uid: e.target.value } : prev,
                          )
                        }
                        placeholder="UID_xxx"
                        autoCapitalize="none"
                        autoCorrect="off"
                        style={{ fontSize: 16 }}
                      />
                    </label>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-block"
                    style={{ marginTop: 8 }}
                    disabled={
                      notifyBusy ||
                      (!notifyToken.trim() &&
                        !(notify.channel === "wxpusher" && notify.token_set))
                    }
                    onClick={() => {
                      haptics.tap();
                      const patch: Parameters<typeof api.putNotifySettings>[0] = {};
                      if (notifyToken.trim()) patch.token = notifyToken.trim();
                      if (notify.channel === "wxpusher") {
                        patch.wxpusher_uid = notify.wxpusher_uid;
                      }
                      if (!patch.token && notify.channel !== "wxpusher") {
                        toast("请粘贴密钥", "warning");
                        return;
                      }
                      if (!patch.token && !notify.token_set) {
                        toast("请先填写密钥", "warning");
                        return;
                      }
                      void saveNotify(patch);
                    }}
                  >
                    {notifyBusy ? "保存中…" : "保存密钥"}
                  </button>
                </section>

                <p className="agent-settings-lead" style={{ marginTop: 14 }}>
                  时间（北京）
                </p>
                <section className="inset-group agent-settings-form" aria-label="时间">
                  <div className="agent-notify-time-row">
                    <label className="agent-settings-field">
                      <span className="mute" style={{ fontSize: 13 }}>
                        时
                      </span>
                      <input
                        type="number"
                        min={0}
                        max={23}
                        value={notify.hour}
                        onChange={(e) =>
                          setNotify((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  hour: Math.min(23, Math.max(0, Number(e.target.value) || 0)),
                                }
                              : prev,
                          )
                        }
                        style={{ fontSize: 16 }}
                      />
                    </label>
                    <label className="agent-settings-field">
                      <span className="mute" style={{ fontSize: 13 }}>
                        分
                      </span>
                      <input
                        type="number"
                        min={0}
                        max={59}
                        value={notify.minute}
                        onChange={(e) =>
                          setNotify((prev) =>
                            prev
                              ? {
                                  ...prev,
                                  minute: Math.min(59, Math.max(0, Number(e.target.value) || 0)),
                                }
                              : prev,
                          )
                        }
                        style={{ fontSize: 16 }}
                      />
                    </label>
                  </div>
                  <div className="agent-notify-weekdays" role="group" aria-label="星期">
                    {WEEKDAY_OPTS.map((d) => {
                      const on = parseWeekdays(notify.weekdays).has(d.id);
                      return (
                        <button
                          key={d.id}
                          type="button"
                          className={`agent-notify-day${on ? " is-on" : ""}`}
                          disabled={notifyBusy}
                          onClick={() => {
                            haptics.tap();
                            const set = parseWeekdays(notify.weekdays);
                            if (on) {
                              if (set.size <= 1) return;
                              set.delete(d.id);
                            } else {
                              set.add(d.id);
                            }
                            setNotify((prev) =>
                              prev ? { ...prev, weekdays: encodeWeekdays(set) } : prev,
                            );
                          }}
                        >
                          {d.label}
                        </button>
                      );
                    })}
                  </div>
                  <button
                    type="button"
                    className="btn btn-block"
                    style={{ marginTop: 8 }}
                    disabled={notifyBusy}
                    onClick={() => {
                      haptics.tap();
                      void saveNotify({
                        hour: notify.hour,
                        minute: notify.minute,
                        weekdays: notify.weekdays,
                      });
                    }}
                  >
                    保存时间
                  </button>
                </section>

                <p className="agent-settings-lead" style={{ marginTop: 14 }}>
                  分析档位
                </p>
                <section className="inset-group agent-settings-list" aria-label="档位">
                  {(notify.degrees.length
                    ? notify.degrees
                    : [
                        { id: "light", label: "轻量" },
                        { id: "standard", label: "标准" },
                        { id: "deep", label: "深度" },
                      ]
                  ).map((d) => {
                    const selected = notify.degree === d.id;
                    return (
                      <button
                        key={d.id}
                        type="button"
                        className={`agent-settings-row${selected ? " is-selected" : ""}`}
                        disabled={notifyBusy}
                        onClick={() => {
                          haptics.tap();
                          void saveNotify({ degree: d.id });
                        }}
                      >
                        <span className="agent-settings-row-main">
                          <span className="agent-settings-row-label">{d.label}</span>
                        </span>
                        {selected ? (
                          <Check size={16} strokeWidth={2.25} absoluteStrokeWidth aria-hidden />
                        ) : (
                          <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                        )}
                      </button>
                    );
                  })}
                </section>

                <p className="agent-settings-lead" style={{ marginTop: 14 }}>
                  试发
                </p>
                <section className="inset-group agent-settings-list" aria-label="试发">
                  <button
                    type="button"
                    className="agent-settings-row"
                    disabled={notifyBusy || !notify.configured}
                    onClick={async () => {
                      haptics.tap();
                      setNotifyBusy(true);
                      try {
                        const r = await api.testNotify();
                        if (r.ok) {
                          haptics.success();
                          toast("测试消息已发送", "success");
                        } else {
                          toast(r.reason || r.detail || "发送失败", "warning");
                        }
                      } catch (e) {
                        toast(errMsg(e, "发送失败"), "warning");
                      } finally {
                        setNotifyBusy(false);
                      }
                    }}
                  >
                    <span className="agent-settings-row-main">
                      <span className="agent-settings-row-label">发送测试消息</span>
                      <span className="agent-settings-row-meta">
                        {notify.configured ? "验证通道" : "先保存密钥"}
                      </span>
                    </span>
                    <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                  </button>
                  <button
                    type="button"
                    className="agent-settings-row"
                    disabled={notifyBusy || !notify.configured}
                    onClick={async () => {
                      haptics.tap();
                      if (
                        typeof window !== "undefined" &&
                        !window.confirm("立刻跑一遍本账号仓库分析并推送到微信？可能较慢。")
                      ) {
                        return;
                      }
                      setNotifyBusy(true);
                      try {
                        const r = await api.runNotifyDigest({ force: true });
                        if (r.ok && !r.skipped) {
                          haptics.success();
                          toast("日报已推送", "success");
                        } else if (r.skipped) {
                          toast(r.reason || "已跳过", "warning");
                        } else {
                          toast(r.reason || r.detail || "推送失败", "warning");
                        }
                      } catch (e) {
                        toast(errMsg(e, "推送失败"), "warning");
                      } finally {
                        setNotifyBusy(false);
                      }
                    }}
                  >
                    <span className="agent-settings-row-main">
                      <span className="agent-settings-row-label">立刻推送仓库日报</span>
                      <span className="agent-settings-row-meta">强制再发一次</span>
                    </span>
                    <ChevronRight size={16} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                  </button>
                </section>
              </>
            )}
          </>
        ) : null}
      </div>

      <CenterModal
        open={pwdOpen}
        title="修改密码"
        onClose={() => {
          if (!saving) setPwdOpen(false);
        }}
        footer={
          <button
            type="button"
            className="btn btn-block"
            disabled={saving || !pwdCurrent || !pwdNew.trim() || !pwdConfirm.trim()}
            onClick={() => void submitPassword()}
          >
            {saving ? "保存中…" : "确认修改"}
          </button>
        }
      >
        <div className="form-grid">
          <label className="full">
            <span className="mute" style={{ fontSize: 13 }}>
              当前密码
            </span>
            <span className="agent-pwd-field">
              <input
                type={showPwd ? "text" : "password"}
                value={pwdCurrent}
                onChange={(e) => setPwdCurrent(e.target.value)}
                autoComplete="current-password"
                data-autofocus
                style={{ fontSize: 16 }}
              />
            </span>
          </label>
          <label className="full">
            <span className="mute" style={{ fontSize: 13 }}>
              新密码（至少 4 位）
            </span>
            <span className="agent-pwd-field">
              <input
                type={showPwd ? "text" : "password"}
                value={pwdNew}
                onChange={(e) => setPwdNew(e.target.value)}
                autoComplete="new-password"
                style={{ fontSize: 16 }}
              />
            </span>
          </label>
          <label className="full">
            <span className="mute" style={{ fontSize: 13 }}>
              确认新密码
            </span>
            <span className="agent-pwd-field">
              <input
                type={showPwd ? "text" : "password"}
                value={pwdConfirm}
                onChange={(e) => setPwdConfirm(e.target.value)}
                autoComplete="new-password"
                style={{ fontSize: 16 }}
              />
              <button
                type="button"
                className="agent-pwd-toggle"
                aria-label={showPwd ? "隐藏密码" : "显示密码"}
                onClick={() => setShowPwd((v) => !v)}
              >
                {showPwd ? (
                  <EyeOff size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                ) : (
                  <Eye size={18} strokeWidth={2} absoluteStrokeWidth aria-hidden />
                )}
              </button>
            </span>
          </label>
        </div>
      </CenterModal>

      <CenterModal
        open={customOpen}
        title="自定义身份"
        onClose={() => setCustomOpen(false)}
        footer={
          <button
            type="button"
            className="btn btn-block"
            disabled={saving || !customLabel.trim()}
            onClick={() => void pickRole("custom", customLabel.trim())}
          >
            {saving ? "保存中…" : "确认"}
          </button>
        }
      >
        <label className="form-grid">
          <span className="mute" style={{ fontSize: 13 }}>
            你是安崽的…
          </span>
          <input
            value={customLabel}
            onChange={(e) => setCustomLabel(e.target.value)}
            placeholder="例如：舅舅、导师"
            maxLength={16}
            autoCapitalize="none"
            autoCorrect="off"
            data-autofocus
          />
        </label>
      </CenterModal>
    </div>
  );
}
