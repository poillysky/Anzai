"use client";

import { useEffect, useMemo, useState } from "react";
import {
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
import type { AnzaiIdentity, AnzaiIdentityRole } from "@/lib/types";
import type { AuthUser } from "@/lib/auth";

export type AgentSettingsPage = "settings" | "account" | "identity";

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
};

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
