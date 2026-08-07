"use client";

import {
  FormEvent,
  ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { ActionSheet } from "@/components/overlay/ActionSheet";
import { ChevronLeft, Eye, EyeOff } from "@/components/ui/icons";
import { api } from "@/lib/api";
import { getAccessToken, setSession } from "@/lib/auth";
import { haptics } from "@/lib/haptics";
import "./login.css";

const PUSH_MS = 360;
const HERO_SRC = "/brand/anzai-login-hero.png";

/** 与后端 identity.IDENTITY_ROLES 对齐（注册页无 token，用本地目录） */
const IDENTITY_ROLES: { id: string; label: string }[] = [
  { id: "dad", label: "爸爸" },
  { id: "mom", label: "妈妈" },
  { id: "grandpa", label: "爷爷" },
  { id: "grandma", label: "奶奶" },
  { id: "brother", label: "哥哥" },
  { id: "sister", label: "姐姐" },
  { id: "friend", label: "朋友" },
  { id: "wife", label: "老婆" },
  { id: "husband", label: "老公" },
  { id: "self", label: "就是我自己" },
  { id: "custom", label: "自定义" },
];

type Phase = "gate" | "login" | "register" | "bootstrap";
type NavDir = "push" | "pop";

function parseApiError(err: unknown, fallback: string): string {
  const msg = err instanceof Error ? err.message : fallback;
  try {
    const parsed = JSON.parse(msg) as { detail?: string };
    return parsed.detail || msg;
  } catch {
    return msg.replace(/^"|"$/g, "").slice(0, 120) || fallback;
  }
}

export default function LoginScreen() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("gate");
  const [prevPhase, setPrevPhase] = useState<Phase | null>(null);
  const [navDir, setNavDir] = useState<NavDir | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showPassword2, setShowPassword2] = useState(false);
  const [identityRole, setIdentityRole] = useState("");
  const [identityLabel, setIdentityLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [forgotOpen, setForgotOpen] = useState(false);
  /** Hero painted first; CTAs/form only after image + auth status */
  const [heroReady, setHeroReady] = useState(false);
  const [statusReady, setStatusReady] = useState(false);
  const [gateActionsSettled, setGateActionsSettled] = useState(false);
  const pendingPhaseRef = useRef<"gate" | "bootstrap">("gate");

  const animatingRef = useRef(false);
  const timerRef = useRef<number | null>(null);

  const isForm = phase !== "gate";
  const needsConfirm = phase === "register" || phase === "bootstrap";
  const showBack =
    (isForm && phase !== "bootstrap") ||
    prevPhase === "login" ||
    prevPhase === "register";
  const revealUi = heroReady && statusReady;

  useEffect(() => {
    if (getAccessToken()) {
      router.replace("/");
      return;
    }

    let cancelled = false;
    const img = new Image();
    const markHero = () => {
      if (!cancelled) setHeroReady(true);
    };
    img.onload = () => {
      if (typeof img.decode === "function") {
        img.decode().then(markHero).catch(markHero);
      } else {
        markHero();
      }
    };
    img.onerror = markHero;
    img.src = HERO_SRC;
    if (img.complete) markHero();
    // Don't block CTAs forever if decode stalls
    const failSafe = window.setTimeout(markHero, 1600);

    return () => {
      cancelled = true;
      window.clearTimeout(failSafe);
    };
  }, [router]);

  useEffect(() => {
    if (getAccessToken()) return;

    let cancelled = false;
    const ctrl = new AbortController();
    const timer = window.setTimeout(() => ctrl.abort(), 4000);

    (async () => {
      try {
        const status = await api.authStatus({ signal: ctrl.signal });
        if (cancelled) return;
        pendingPhaseRef.current = status.has_users ? "gate" : "bootstrap";
        setError("");
      } catch (err) {
        if (cancelled) return;
        pendingPhaseRef.current = "gate";
        if (err instanceof DOMException && err.name === "AbortError") {
          setError("服务器响应超时，可先尝试登录");
        } else {
          const raw = err instanceof Error ? err.message : "";
          if (raw.includes("Failed to fetch") || raw.includes("NetworkError") || !raw) {
            setError("无法连接服务器，请确认 API 已启动（默认 8515）");
          } else {
            setError(parseApiError(err, "登录服务暂不可用"));
          }
        }
      } finally {
        window.clearTimeout(timer);
        if (!cancelled) setStatusReady(true);
      }
    })();

    return () => {
      cancelled = true;
      ctrl.abort();
      window.clearTimeout(timer);
    };
  }, []);

  /** After hero + status: show gate CTAs or jump to bootstrap form */
  useEffect(() => {
    if (!revealUi) return;
    if (pendingPhaseRef.current === "bootstrap" && phase === "gate") {
      setPhase("bootstrap");
    }
  }, [revealUi, phase]);

  useEffect(() => {
    if (!revealUi || phase !== "gate" || gateActionsSettled) return;
    const id = window.setTimeout(() => setGateActionsSettled(true), 800);
    return () => window.clearTimeout(id);
  }, [revealUi, phase, gateActionsSettled]);

  useEffect(() => {
    return () => {
      if (timerRef.current != null) window.clearTimeout(timerRef.current);
    };
  }, []);

  function navigate(next: Phase, direction: NavDir) {
    if (animatingRef.current || next === phase) return;
    if (phase === "gate") setGateActionsSettled(true);
    animatingRef.current = true;
    setPrevPhase(phase);
    setNavDir(direction);
    setPhase(next);
    if (timerRef.current != null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      setPrevPhase(null);
      setNavDir(null);
      animatingRef.current = false;
      timerRef.current = null;
    }, PUSH_MS);
  }

  function openForm(next: "login" | "register") {
    haptics.tap();
    setError("");
    setShowPassword(false);
    setShowPassword2(false);
    if (next === "register") {
      setPassword2("");
      setIdentityRole("");
      setIdentityLabel("");
    }
    navigate(next, "push");
  }

  function goBack() {
    if (phase === "bootstrap" || !isForm) return;
    haptics.tap();
    setError("");
    navigate("gate", "pop");
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (needsConfirm && password !== password2) {
      setError("两次密码不一致");
      haptics.warning();
      return;
    }
    if (phase === "register" || phase === "bootstrap") {
      if (!identityRole.trim()) {
        setError("请选择你是安崽的谁");
        haptics.warning();
        return;
      }
      if (identityRole === "custom" && !identityLabel.trim()) {
        setError("自定义请填写称呼，例如「舅舅」");
        haptics.warning();
        return;
      }
    }
    setBusy(true);
    try {
      const res =
        phase === "bootstrap"
          ? await api.bootstrap(
              username.trim(),
              password,
              identityRole.trim(),
              identityRole === "custom" ? identityLabel.trim() : "",
            )
          : phase === "register"
            ? await api.register(
                username.trim(),
                password,
                identityRole.trim(),
                identityRole === "custom" ? identityLabel.trim() : "",
              )
            : await api.login(username.trim(), password);
      setSession(res.access_token, res.user);
      haptics.success();
      router.replace("/");
    } catch (err) {
      haptics.warning();
      setError(parseApiError(err, "登录失败"));
      setBusy(false);
    }
  }

  function gateActionsClass() {
    if (!revealUi) return "login-gate-actions login-gate-actions--pending";
    if (gateActionsSettled) return "login-gate-actions login-gate-actions--settled";
    return "login-gate-actions";
  }

  function renderGate() {
    return (
      <div className="login-gate">
        <div className="login-gate-spacer" aria-hidden />
        <div className={gateActionsClass()}>
          {error ? (
            <p className="login-error" role="alert">
              {error}
            </p>
          ) : (
            <p className="login-error login-error--placeholder" aria-hidden>
              &nbsp;
            </p>
          )}
          <button
            type="button"
            className="login-btn login-btn--primary"
            disabled={busy || !revealUi}
            onClick={() => openForm("login")}
          >
            账号登录
          </button>
          <button
            type="button"
            className="login-btn login-btn--secondary"
            disabled={busy || !revealUi}
            onClick={() => openForm("register")}
          >
            注册账号
          </button>
        </div>
      </div>
    );
  }

  function renderForm(formPhase: "login" | "register" | "bootstrap") {
    const title =
      formPhase === "bootstrap"
        ? "创建管理员"
        : formPhase === "register"
          ? "注册账号"
          : "账号登录";
    const submitLabel = busy
      ? "请稍候…"
      : formPhase === "bootstrap"
        ? "创建并进入"
        : formPhase === "register"
          ? "注册并登录"
          : "登录";
    const formEnter = formPhase === "bootstrap" ? " login-form-page--enter" : "";

    return (
      <div className={`login-form-page${formEnter}`}>
        <div className="login-form-head">
          <h1 className="login-title">{title}</h1>
          {formPhase === "bootstrap" ? (
            <p className="login-lead">首位账号为管理员；请同时选择你是安崽的谁</p>
          ) : formPhase === "register" ? (
            <p className="login-lead">选好身份后注册，安崽说话语气会跟着变</p>
          ) : null}
        </div>

        <div className="login-scroll">
          <form className="login-form" onSubmit={onSubmit}>
            <label className="login-field">
              <span className="login-label">用户名</span>
              <input
                className="login-input"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="请输入用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                minLength={2}
                maxLength={64}
              />
            </label>

            <label className="login-field">
              <span className="login-label">密码</span>
              <span className="login-password">
                <input
                  className="login-input"
                  type={showPassword ? "text" : "password"}
                  name="password"
                  autoComplete={formPhase === "login" ? "current-password" : "new-password"}
                  placeholder={formPhase === "login" ? "请输入密码" : "至少 4 位"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={4}
                  maxLength={128}
                />
                <button
                  type="button"
                  className="login-eye"
                  onClick={() => setShowPassword((v) => !v)}
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                >
                  {showPassword ? (
                    <EyeOff size={18} strokeWidth={1.75} absoluteStrokeWidth />
                  ) : (
                    <Eye size={18} strokeWidth={1.75} absoluteStrokeWidth />
                  )}
                </button>
              </span>
            </label>

            {needsConfirm ? (
              <label className="login-field">
                <span className="login-label">确认密码</span>
                <span className="login-password">
                  <input
                    className="login-input"
                    type={showPassword2 ? "text" : "password"}
                    name="password2"
                    autoComplete="new-password"
                    placeholder="再次输入"
                    value={password2}
                    onChange={(e) => setPassword2(e.target.value)}
                    required
                    minLength={4}
                    maxLength={128}
                  />
                  <button
                    type="button"
                    className="login-eye"
                    onClick={() => setShowPassword2((v) => !v)}
                    aria-label={showPassword2 ? "隐藏确认密码" : "显示确认密码"}
                  >
                    {showPassword2 ? (
                      <EyeOff size={18} strokeWidth={1.75} absoluteStrokeWidth />
                    ) : (
                      <Eye size={18} strokeWidth={1.75} absoluteStrokeWidth />
                    )}
                  </button>
                </span>
              </label>
            ) : null}

            {formPhase === "register" || formPhase === "bootstrap" ? (
              <div className="login-identity">
                <div className="login-identity-head">
                  <span className="login-label">你是安崽的谁</span>
                  <span className="login-identity-hint">必选 · 决定对话语气</span>
                </div>
                <div className="login-identity-chips" role="group" aria-label="身份">
                  {IDENTITY_ROLES.map((r) => (
                    <button
                      key={r.id}
                      type="button"
                      className={`login-identity-chip${identityRole === r.id ? " is-on" : ""}`}
                      onClick={() => {
                        haptics.tap();
                        setIdentityRole(r.id);
                        if (r.id !== "custom") setIdentityLabel("");
                      }}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
                {identityRole === "custom" ? (
                  <label className="login-field login-field--stack">
                    <span className="login-label">怎么称呼</span>
                    <input
                      className="login-input"
                      value={identityLabel}
                      onChange={(e) => setIdentityLabel(e.target.value)}
                      placeholder="例如 舅舅"
                      maxLength={16}
                      required
                    />
                  </label>
                ) : null}
              </div>
            ) : null}

            {error && phase === formPhase ? (
              <p className="login-error" role="alert">
                {error}
              </p>
            ) : null}

            <button
              type="submit"
              className="login-btn login-btn--primary login-btn--submit"
              disabled={busy}
            >
              {submitLabel}
            </button>
          </form>

          {formPhase === "login" || formPhase === "register" ? (
            <p className="login-switch">
              {formPhase === "login" ? (
                <>
                  还没有账号？
                  <button
                    type="button"
                    onClick={() => {
                      haptics.tap();
                      setError("");
                      setPassword2("");
                      setShowPassword2(false);
                      setIdentityRole("");
                      setIdentityLabel("");
                      setPhase("register");
                    }}
                  >
                    立即注册
                  </button>
                  <span className="login-switch-sep" aria-hidden>
                    ·
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      haptics.tap();
                      setForgotOpen(true);
                    }}
                  >
                    忘记密码
                  </button>
                </>
              ) : (
                <>
                  已有账号？
                  <button
                    type="button"
                    onClick={() => {
                      haptics.tap();
                      setError("");
                      setPhase("login");
                    }}
                  >
                    去登录
                  </button>
                </>
              )}
            </p>
          ) : null}
        </div>
      </div>
    );
  }

  function renderPhase(p: Phase): ReactNode {
    return p === "gate" ? renderGate() : renderForm(p);
  }

  function layerClass(role: "current" | "prev"): string {
    if (!navDir) return "login-layer";
    return role === "current"
      ? `login-layer login-layer--enter-${navDir}`
      : `login-layer login-layer--exit-${navDir}`;
  }

  const currentKey = isForm ? "form" : "gate";
  const prevKey =
    prevPhase == null ? null : prevPhase === "gate" ? "gate" : "form";

  return (
    <div className="login-root">
      <header className="login-top">
        {showBack ? (
          <button type="button" className="login-back" onClick={goBack} aria-label="返回">
            <ChevronLeft size={26} strokeWidth={1.6} absoluteStrokeWidth />
          </button>
        ) : (
          <span className="login-top-spacer" />
        )}
      </header>

      <div className="login-stack">
        {prevPhase != null && prevKey != null ? (
          <div className={layerClass("prev")} key={`prev-${prevKey}`} aria-hidden>
            {renderPhase(prevPhase)}
          </div>
        ) : null}
        <div className={layerClass("current")} key={`cur-${currentKey}`}>
          {phase === "bootstrap" && !revealUi ? renderGate() : renderPhase(phase)}
        </div>
      </div>

      <ActionSheet
        open={forgotOpen}
        title="忘记密码请联系管理员，在 /admin/accounts 中重置密码。"
        onClose={() => setForgotOpen(false)}
        actions={[{ label: "知道了", onClick: () => undefined }]}
      />
    </div>
  );
}
