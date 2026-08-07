"""Admin console at /admin — not exposed via Next PWA rewrite.

Pages: /admin/accounts · /admin/llm · /admin/analysis-llm · /admin/embedding · /admin/knowledge · /admin/presets · /admin/agent
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database import get_db
from app.models import User
from app.services import analysis_connection as analysis_conn_svc
from app.services import embedding_connection as embedding_svc
from app.services import llm_presets as presets_svc
from app.services import llm_profiles as profiles_svc
from app.services import users as users_svc
from app.services.agent_chat_config import (
    load_agent_chat,
    parse_agent_chat_form,
    resolve_generation,
    save_agent_chat,
)
from app.services.analysis_tiers import (
    AGENT_IDS,
    AGENT_LABELS,
    EVIDENCE_BLURBS,
    SEAT_META,
    TIER_IDS,
    TIER_META,
    DEFAULT_TIERS,
    load_tiers,
    parse_tier_form,
    save_tiers,
)
from app.services.settings_store import public_settings_view, write_env_updates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))

_PAGES = frozenset(
    {"accounts", "llm", "analysis-llm", "embedding", "knowledge", "presets", "agent"}
)


def _flash(request: Request) -> dict | None:
    raw = request.session.pop("flash", None)
    if isinstance(raw, dict) and "message" in raw:
        return raw
    return None


def _set_flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _authed(request: Request) -> bool:
    return bool(request.session.get("admin_ok"))


def _admin_user_id(request: Request) -> int | None:
    raw = request.session.get("admin_user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _redirect(page: str = "") -> RedirectResponse:
    if page in _PAGES:
        return RedirectResponse(f"/admin/{page}", status_code=303)
    return RedirectResponse("/admin", status_code=303)


def _gate(request: Request, db: Session) -> RedirectResponse | None:
    """Return redirect if not allowed to view admin pages."""
    has_users = users_svc.user_count(db) > 0
    if not has_users:
        request.session.clear()
        return _redirect()
    if not _authed(request):
        return _redirect()
    return None


def _shell_ctx(request: Request, page: str, **extra: object) -> dict:
    return {
        "flash": _flash(request),
        "nav": True,
        "page": page,
        **extra,
    }


def _model_list(request: Request, *, key: str = "llm_model_list") -> list[str]:
    raw = request.session.get(key)
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()][:80]
    return []


def _parse_openai_model_ids(data: object) -> list[str]:
    ids: list[str] = []
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        for item in data["data"]:
            if isinstance(item, dict) and item.get("id"):
                ids.append(str(item["id"]))
            if len(ids) >= 80:
                break
    return ids


async def _fetch_openai_model_ids(base: str, key: str) -> tuple[list[str], str | None]:
    """Try GET {base}models then {base}v1/models. Returns (ids, error_message)."""
    root = (base or "").strip().rstrip("/") + "/"
    headers = {"Authorization": f"Bearer {key}"}
    candidates = [urljoin(root, "models")]
    # If base already ends with /v1/, first URL is enough; else also try …/v1/models
    if "/v1/" not in root.lower():
        candidates.append(urljoin(root, "v1/models"))
    last_err: str | None = None
    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in candidates:
            try:
                res = await client.get(url, headers=headers)
            except Exception as exc:
                last_err = f"{type(exc).__name__}"
                continue
            if res.status_code >= 400:
                detail = (res.text or "")[:120].replace("\n", " ")
                last_err = f"HTTP {res.status_code}" + (f"：{detail}" if detail else "")
                continue
            try:
                data = res.json() if res.content else {}
            except Exception:
                last_err = "响应不是 JSON"
                continue
            ids = _parse_openai_model_ids(data)
            if ids:
                return ids, None
            last_err = "验通但未返回模型列表"
    return [], last_err or "连接失败"


@router.get("/admin", response_class=HTMLResponse, response_model=None)
def admin_home(request: Request, db: Session = Depends(get_db)):
    has_users = users_svc.user_count(db) > 0
    if has_users and _authed(request):
        return _redirect("accounts")
    if not has_users:
        request.session.clear()
    return TEMPLATES.TemplateResponse(
        request,
        "admin_login.html",
        {
            "flash": _flash(request),
            "nav": False,
            "page": "",
            "bootstrap": not has_users,
        },
    )


@router.get("/admin/accounts", response_class=HTMLResponse, response_model=None)
def admin_accounts_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    users = db.query(User).order_by(User.id.asc()).all()
    return TEMPLATES.TemplateResponse(
        request,
        "admin_accounts.html",
        _shell_ctx(request, "accounts", users=users),
    )


@router.get("/admin/llm", response_class=HTMLResponse, response_model=None)
def admin_llm_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    store = profiles_svc.load_profiles()
    active = profiles_svc.get_active_profile(store)
    models = profiles_svc.saved_model_list(store) or _model_list(request)
    return TEMPLATES.TemplateResponse(
        request,
        "admin_llm.html",
        _shell_ctx(
            request,
            "llm",
            settings=public_settings_view(),
            profile_store=profiles_svc.public_store(store),
            active=profiles_svc.public_profile(active),
            chat_sources=profiles_svc.CHAT_SOURCES,
            model_list=models,
        ),
    )


@router.get("/admin/analysis-llm", response_class=HTMLResponse, response_model=None)
def admin_analysis_llm_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    conn = analysis_conn_svc.load_connection()
    # Prefer disk list — Session cookie 装不下长模型表
    models = analysis_conn_svc.saved_model_list() or _model_list(
        request, key="analysis_model_list"
    )
    return TEMPLATES.TemplateResponse(
        request,
        "admin_analysis_llm.html",
        _shell_ctx(
            request,
            "analysis-llm",
            active=analysis_conn_svc.public_connection(conn),
            analysis_sources=analysis_conn_svc.ANALYSIS_SOURCES,
            model_list=models,
        ),
    )


@router.get("/admin/embedding", response_class=HTMLResponse, response_model=None)
def admin_embedding_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    conn = embedding_svc.load_connection()
    from app.core.config import get_settings
    from app.services import knowledge_pg as knowledge_pg_svc

    settings = get_settings()
    kb_url = (settings.knowledge_database_url or "").strip()
    kb_fields = knowledge_pg_svc.parse_knowledge_db_url(kb_url)
    kb_status: dict[str, object] = {
        "configured": bool(kb_url),
        "ok": False,
        "total": 0,
        "with_embedding": 0,
        "error": "",
    }
    if kb_url:
        try:
            knowledge_pg_svc.ensure_schema()
            total, with_emb = knowledge_pg_svc.count_cards()
            kb_status.update({"ok": True, "total": total, "with_embedding": with_emb})
        except Exception as exc:
            kb_status["error"] = f"{type(exc).__name__}: {exc}"[:160]
    return TEMPLATES.TemplateResponse(
        request,
        "admin_embedding.html",
        _shell_ctx(
            request,
            "embedding",
            active=embedding_svc.public_connection(conn),
            embed_sources=embedding_svc.EMBEDDING_SOURCES,
            model_list=_model_list(request, key="embedding_model_list"),
            kb_fields=kb_fields,
            kb_status=kb_status,
        ),
    )


@router.get("/admin/knowledge", response_class=HTMLResponse, response_model=None)
def admin_knowledge_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    from app.services import knowledge as knowledge_svc
    from app.services import knowledge_pg as knowledge_pg_svc

    db_ok = False
    db_error = ""
    cards_view: list[dict] = []
    total = 0
    with_embedding = 0
    edit: dict | None = None
    if knowledge_pg_svc.knowledge_db_configured():
        try:
            knowledge_pg_svc.ensure_schema()
            total, with_embedding = knowledge_pg_svc.count_cards()
            rows = knowledge_pg_svc.list_cards()
            for row in rows:
                tags = row.get("tags") or []
                if not isinstance(tags, list):
                    tags = list(tags)
                cards_view.append(
                    {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or ""),
                        "tags": [str(t) for t in tags],
                        "source": str(row.get("source") or ""),
                        "date": str(row.get("card_date") or ""),
                        "body": str(row.get("body") or ""),
                        "has_embedding": bool(row.get("has_embedding")),
                    }
                )
            db_ok = True
            edit_id = (request.query_params.get("id") or "").strip()
            if edit_id:
                row = knowledge_pg_svc.get_card(edit_id)
                if row:
                    tags = row.get("tags") or []
                    if not isinstance(tags, list):
                        tags = list(tags)
                    edit = {
                        "id": str(row.get("id") or ""),
                        "title": str(row.get("title") or ""),
                        "tags": [str(t) for t in tags],
                        "source": str(row.get("source") or ""),
                        "date": str(row.get("card_date") or ""),
                        "body": str(row.get("body") or ""),
                    }
        except Exception as exc:
            db_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning("admin knowledge page: %s", db_error)
    else:
        db_error = "未配置 KNOWLEDGE_DATABASE_URL"

    return TEMPLATES.TemplateResponse(
        request,
        "admin_knowledge.html",
        _shell_ctx(
            request,
            "knowledge",
            db_ok=db_ok,
            db_error=db_error,
            cards=cards_view,
            total=total,
            with_embedding=with_embedding,
            edit=edit,
            md_count=len(knowledge_svc.load_cards()),
        ),
    )


@router.post("/admin/knowledge/save")
def admin_knowledge_save(
    request: Request,
    card_id: str = Form(""),
    title: str = Form(""),
    tags: str = Form(""),
    source: str = Form(""),
    card_date: str = Form(""),
    body: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.services import knowledge as knowledge_svc

    try:
        result = knowledge_svc.save_card(
            card_id=card_id,
            title=title,
            tags=tags,
            source=source,
            card_date=card_date,
            body=body,
            reembed=True,
        )
        emb = "已向量化" if result.get("embedded") else "未向量化（检查 Embedding 配置）"
        _set_flash(request, f"已保存 {result.get('id')} · {emb}", "ok")
    except Exception as exc:
        logger.warning("knowledge save failed: %s", exc)
        _set_flash(request, f"保存失败：{exc}"[:200], "err")
    return _redirect("knowledge")


@router.post("/admin/knowledge/delete")
def admin_knowledge_delete(
    request: Request,
    card_id: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.services import knowledge as knowledge_svc

    try:
        ok = knowledge_svc.delete_card(card_id)
        _set_flash(request, "已删除" if ok else "未找到该卡", "ok" if ok else "err")
    except Exception as exc:
        _set_flash(request, f"删除失败：{exc}"[:200], "err")
    return _redirect("knowledge")


@router.post("/admin/knowledge/reembed")
def admin_knowledge_reembed(
    request: Request,
    card_id: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.services import knowledge as knowledge_svc

    try:
        result = knowledge_svc.reembed_card(card_id)
        if result.get("embedded"):
            _set_flash(request, f"已重嵌 {result.get('id')}", "ok")
        else:
            _set_flash(request, "重嵌失败：检查 Embedding API", "err")
    except Exception as exc:
        _set_flash(request, f"重嵌失败：{exc}"[:200], "err")
    return _redirect("knowledge")


@router.post("/admin/knowledge/import-md")
def admin_knowledge_import_md(request: Request) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.services import knowledge as knowledge_svc

    try:
        knowledge_svc.reset_embed_cooldown()
        result = knowledge_svc.sync_markdown_to_postgres()
        if not result.get("ok"):
            _set_flash(request, f"导入失败：{result.get('reason') or 'unknown'}", "err")
        else:
            _set_flash(
                request,
                f"Markdown 导入完成 · 更新 {result.get('upserted')} · "
                f"库内 {result.get('total')} · 已向量化 {result.get('with_embedding')}",
                "ok",
            )
    except Exception as exc:
        logger.exception("knowledge import-md failed")
        _set_flash(request, f"导入失败：{exc}"[:200], "err")
    return _redirect("knowledge")


@router.get("/admin/presets", response_class=HTMLResponse, response_model=None)
def admin_presets_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    store = presets_svc.load_presets()
    preset = (store.get("presets") or [None])[0] or {}
    return TEMPLATES.TemplateResponse(
        request,
        "admin_presets.html",
        _shell_ctx(
            request,
            "presets",
            preset_store=store,
            prompt_rows=presets_svc.list_editable_prompt_rows(preset),
        ),
    )


@router.get("/admin/agent", response_class=HTMLResponse, response_model=None)
def admin_agent_page(request: Request, db: Session = Depends(get_db)):
    blocked = _gate(request, db)
    if blocked:
        return blocked
    presets = presets_svc.load_presets()
    agent_chat = load_agent_chat()
    from app.services.agent_tools import TOOL_LABELS

    return TEMPLATES.TemplateResponse(
        request,
        "admin_agent.html",
        _shell_ctx(
            request,
            "agent",
            tiers=load_tiers(),
            agent_chat=agent_chat,
            generation=resolve_generation(agent_chat),
            preset_store=presets,
            agent_ids=AGENT_IDS,
            agent_labels=AGENT_LABELS,
            tier_ids=TIER_IDS,
            tier_meta=TIER_META,
            seat_meta=SEAT_META,
            evidence_blurbs=EVIDENCE_BLURBS,
            chat_tools=list(TOOL_LABELS.values()),
        ),
    )


@router.post("/admin/login")
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if users_svc.user_count(db) == 0:
        _set_flash(request, "请先在 PWA 完成首位管理员注册", "err")
        return _redirect()
    try:
        user = users_svc.authenticate(db, username, password)
    except ValueError:
        _set_flash(request, "用户名或密码错误", "err")
        return _redirect()
    if user.role != "admin":
        _set_flash(request, "需要管理员账号", "err")
        return _redirect()
    request.session["admin_ok"] = True
    request.session["admin_user_id"] = user.id
    _set_flash(request, "已登录", "ok")
    return _redirect("accounts")


@router.get("/admin/logout")
def admin_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return _redirect()


@router.post("/admin/settings")
def admin_save(
    request: Request,
    LLM_SOURCE: str = Form("custom"),
    LLM_API_KEY: str = Form(""),
    LLM_BASE_URL: str = Form(""),
    LLM_MODEL: str = Form(""),
    clear_api_key: str = Form(""),
    QUOTE_PROVIDER: str = Form("sina"),
    CORS_ORIGINS: str = Form(""),
    DATABASE_URL: str = Form(""),
    JWT_SECRET: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()

    if QUOTE_PROVIDER not in {"sina", "mock"}:
        _set_flash(request, "QUOTE_PROVIDER 仅支持 sina / mock", "err")
        return _redirect("llm")

    clear = clear_api_key in ("1", "on", "true", "yes")
    key_in = LLM_API_KEY.strip()
    profiles_svc.sync_active_from_fields(
        source=LLM_SOURCE.strip() or "custom",
        base_url=LLM_BASE_URL.strip(),
        model=LLM_MODEL.strip() or "gpt-4o-mini",
        api_key=key_in if key_in else None,
        clear_key=clear,
    )

    env_extra: dict[str, str] = {
        "QUOTE_PROVIDER": QUOTE_PROVIDER.strip(),
        "CORS_ORIGINS": CORS_ORIGINS.strip(),
        "DATABASE_URL": DATABASE_URL.strip() or "sqlite:///./anzai.db",
    }
    if JWT_SECRET.strip():
        env_extra["JWT_SECRET"] = JWT_SECRET.strip()
    write_env_updates(env_extra)

    request.session["admin_ok"] = True
    _set_flash(request, "聊天模型与运行环境已保存", "ok")
    return _redirect("llm")


@router.post("/admin/embedding/settings")
def admin_embedding_save(
    request: Request,
    EMBED_SOURCE: str = Form("custom"),
    EMBED_BASE_URL: str = Form(""),
    EMBED_API_KEY: str = Form(""),
    EMBED_MODEL: str = Form(""),
    clear_api_key: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    clear = clear_api_key in ("1", "on", "true", "yes")
    key_in = EMBED_API_KEY.strip()
    embedding_svc.update_connection(
        source=EMBED_SOURCE.strip() or "custom",
        base_url=EMBED_BASE_URL.strip(),
        model=EMBED_MODEL.strip() or "text-embedding-v4",
        api_key=key_in if key_in else None,
        clear_key=clear,
    )
    request.session["admin_ok"] = True
    _set_flash(request, "向量模型连接已保存（与聊天分离）", "ok")
    return _redirect("embedding")


@router.post("/admin/embedding/knowledge-db")
def admin_knowledge_db_save(
    request: Request,
    KB_HOST: str = Form(""),
    KB_PORT: str = Form("5437"),
    KB_USER: str = Form("postgres"),
    KB_PASSWORD: str = Form("postgres"),
    KB_DBNAME: str = Form("anzai_knowledge"),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.services import knowledge_pg as knowledge_pg_svc

    url = knowledge_pg_svc.build_knowledge_db_url(
        host=KB_HOST,
        port=KB_PORT,
        user=KB_USER,
        password=KB_PASSWORD,
        dbname=KB_DBNAME,
    )
    write_env_updates({"KNOWLEDGE_DATABASE_URL": url})
    request.session["admin_ok"] = True
    if url:
        _set_flash(request, "知识库 Postgres 已保存", "ok")
    else:
        _set_flash(request, "主机为空，已清空知识库地址（回退本地索引）", "ok")
    return _redirect("embedding")


@router.post("/admin/embedding/knowledge-db/test")
def admin_knowledge_db_test(
    request: Request,
    KB_HOST: str = Form(""),
    KB_PORT: str = Form("5437"),
    KB_USER: str = Form("postgres"),
    KB_PASSWORD: str = Form("postgres"),
    KB_DBNAME: str = Form("anzai_knowledge"),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    from app.core.config import reload_settings
    from app.services import knowledge_pg as knowledge_pg_svc

    host = (KB_HOST or "").strip()
    if not host:
        _set_flash(request, "请先填写主机 IP", "err")
        return _redirect("embedding")
    url = knowledge_pg_svc.build_knowledge_db_url(
        host=host,
        port=KB_PORT,
        user=KB_USER,
        password=KB_PASSWORD,
        dbname=KB_DBNAME,
    )
    write_env_updates({"KNOWLEDGE_DATABASE_URL": url})
    reload_settings()
    try:
        knowledge_pg_svc.ensure_schema()
        total, with_emb = knowledge_pg_svc.count_cards()
        _set_flash(
            request,
            f"知识库连接成功 · 卡片 {total} · 已向量化 {with_emb}",
            "ok",
        )
    except Exception as exc:
        logger.warning("knowledge db test failed: %s", exc)
        _set_flash(request, f"知识库连接失败：{type(exc).__name__}: {exc}"[:200], "err")
    return _redirect("embedding")


@router.post("/admin/embedding/test")
async def admin_embedding_test(
    request: Request,
    EMBED_BASE_URL: str = Form(""),
    EMBED_API_KEY: str = Form(""),
    EMBED_MODEL: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()

    saved = embedding_svc.load_connection()
    base = (EMBED_BASE_URL.strip() or saved.get("baseUrl") or "").rstrip("/")
    key = (EMBED_API_KEY.strip() or saved.get("apiKey") or "").strip()
    model = (EMBED_MODEL.strip() or saved.get("model") or "text-embedding-v4").strip()
    if not key:
        _set_flash(request, "请先填写密钥（或保存后再测）", "err")
        return _redirect("embedding")
    if not base:
        _set_flash(request, "请填写向量端点 Base URL", "err")
        return _redirect("embedding")

    emb_url = f"{base}/embeddings"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                emb_url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "input": "安崽向量连接测试"},
            )
            # Optional: list models for picker
            models_res = await client.get(
                urljoin(base.rstrip("/") + "/", "models"),
                headers={"Authorization": f"Bearer {key}"},
            )
        if res.status_code >= 400:
            detail = (res.text or "")[:160].replace("\n", " ")
            request.session.pop("embedding_model_list", None)
            _set_flash(
                request,
                f"向量接口失败 HTTP {res.status_code}"
                + (f"：{detail}" if detail else "")
                + f"（POST {emb_url}）",
                "err",
            )
            return _redirect("embedding")
        data = res.json() if res.content else {}
        rows = data.get("data") if isinstance(data, dict) else None
        dim = 0
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            emb = rows[0].get("embedding")
            if isinstance(emb, list):
                dim = len(emb)
        ids: list[str] = []
        if models_res.status_code < 400:
            mdata = models_res.json() if models_res.content else {}
            if isinstance(mdata, dict) and isinstance(mdata.get("data"), list):
                for item in mdata["data"]:
                    if isinstance(item, dict) and item.get("id"):
                        ids.append(str(item["id"]))
                    if len(ids) >= 80:
                        break
        request.session["embedding_model_list"] = ids
        msg = f"向量连接成功 · 模型 {model}"
        if dim:
            msg += f" · 维度 {dim}"
        if ids:
            msg += f" · 列表 {len(ids)} 个"
        _set_flash(request, msg, "ok")
    except Exception as exc:
        logger.warning("embedding test failed: %s", type(exc).__name__)
        request.session.pop("embedding_model_list", None)
        _set_flash(request, f"向量连接失败：{type(exc).__name__}", "err")
    return _redirect("embedding")


@router.post("/admin/llm/profiles")
def admin_llm_profiles(
    request: Request,
    action: str = Form(...),
    profile_id: str = Form(""),
    name: str = Form(""),
    copy_active: str = Form("1"),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    try:
        if action == "switch":
            profiles_svc.switch_profile(profile_id.strip())
            _set_flash(request, "已切换连接配置档", "ok")
        elif action == "create":
            profiles_svc.create_profile(
                name=name.strip() or "新配置",
                copy_active=copy_active in ("1", "on", "true", "yes"),
            )
            _set_flash(request, "已新建配置档", "ok")
        elif action == "rename":
            profiles_svc.rename_profile(profile_id.strip(), name.strip())
            _set_flash(request, "已重命名配置档", "ok")
        elif action == "delete":
            profiles_svc.delete_profile(profile_id.strip())
            _set_flash(request, "已删除配置档", "ok")
        else:
            _set_flash(request, "未知操作", "err")
    except ValueError as exc:
        _set_flash(request, str(exc), "err")
    except Exception as exc:
        logger.exception("profile action failed")
        _set_flash(request, f"失败：{exc}", "err")
    return _redirect("llm")


@router.post("/admin/llm/test")
async def admin_llm_test(
    request: Request,
    LLM_BASE_URL: str = Form(""),
    LLM_API_KEY: str = Form(""),
    LLM_MODEL: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()

    active = profiles_svc.get_active_profile()
    base = (LLM_BASE_URL.strip() or active.get("baseUrl") or "").rstrip("/") + "/"
    key = (LLM_API_KEY.strip() or active.get("apiKey") or "").strip()
    model = (LLM_MODEL.strip() or active.get("model") or "—").strip()
    if not key:
        _set_flash(request, "请先填写密钥（或保存后再测）", "err")
        return _redirect("llm")

    ids, err = await _fetch_openai_model_ids(base, key)
    if ids:
        profiles_svc.remember_model_list(ids)
        request.session.pop("llm_model_list", None)  # 避免撑爆 cookie
        msg = f"连接成功 · 模型 {model or '—'} · 可用 {len(ids)} 个（见下拉）"
        _set_flash(request, msg, "ok")
    else:
        _set_flash(
            request,
            f"连接失败或未返回模型列表" + (f"：{err}" if err else "") + "。可手填模型名后保存。",
            "err",
        )
    return _redirect("llm")


@router.post("/admin/analysis-llm/settings")
def admin_analysis_llm_save(
    request: Request,
    ANALYSIS_SOURCE: str = Form("custom"),
    ANALYSIS_BASE_URL: str = Form(""),
    ANALYSIS_API_KEY: str = Form(""),
    ANALYSIS_MODEL: str = Form(""),
    clear_api_key: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    clear = clear_api_key in ("1", "on", "true", "yes")
    key_in = ANALYSIS_API_KEY.strip()
    analysis_conn_svc.update_connection(
        source=ANALYSIS_SOURCE.strip() or "custom",
        base_url=ANALYSIS_BASE_URL.strip(),
        model=ANALYSIS_MODEL.strip() or "gpt-4o-mini",
        api_key=key_in if key_in else None,
        clear_key=clear,
    )
    _set_flash(request, "分析模型已保存", "ok")
    return _redirect("analysis-llm")


@router.post("/admin/analysis-llm/test")
async def admin_analysis_llm_test(
    request: Request,
    ANALYSIS_BASE_URL: str = Form(""),
    ANALYSIS_API_KEY: str = Form(""),
    ANALYSIS_MODEL: str = Form(""),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()

    saved = analysis_conn_svc.load_connection()
    base = (ANALYSIS_BASE_URL.strip() or saved.get("baseUrl") or "").rstrip("/") + "/"
    key = (ANALYSIS_API_KEY.strip() or saved.get("apiKey") or "").strip()
    model = (ANALYSIS_MODEL.strip() or saved.get("model") or "—").strip()
    if not key:
        _set_flash(request, "请先填写密钥（或保存后再测）", "err")
        return _redirect("analysis-llm")

    ids, err = await _fetch_openai_model_ids(base, key)
    if ids:
        analysis_conn_svc.remember_model_list(ids)
        request.session.pop("analysis_model_list", None)  # 避免撑爆 cookie
        msg = f"连接成功 · 模型 {model or '—'} · 可用 {len(ids)} 个（见下拉）"
        _set_flash(request, msg, "ok")
    else:
        _set_flash(
            request,
            f"连接失败或未返回模型列表" + (f"：{err}" if err else "") + "。可手填模型名后保存。",
            "err",
        )
    return _redirect("analysis-llm")


@router.post("/admin/presets")
async def admin_presets_action(request: Request) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    form = await request.form()
    raw = {k: str(v) for k, v in form.items()}
    try:
        presets_svc.update_preset(
            raw.get("preset_id") or "",
            presets_svc.parse_preset_form(raw),
        )
        _set_flash(request, "对话预设已保存（全站仅此一套）", "ok")
    except ValueError as exc:
        _set_flash(request, str(exc), "err")
    except Exception as exc:
        logger.exception("preset action failed")
        _set_flash(request, f"失败：{exc}", "err")
    return _redirect("presets")


@router.post("/admin/users/create")
def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("user"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    try:
        users_svc.create_user(db, username=username, password=password, role=role)
        _set_flash(request, f"已创建用户 {username.strip()}", "ok")
    except ValueError as exc:
        _set_flash(request, str(exc), "err")
    return _redirect("accounts")


@router.post("/admin/users/{user_id}/password")
def admin_reset_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    row = db.get(User, user_id)
    if row is None:
        _set_flash(request, "用户不存在", "err")
        return _redirect("accounts")
    try:
        users_svc.set_password(db, row, password)
        _set_flash(request, f"已重置 {row.username} 的密码", "ok")
    except ValueError as exc:
        _set_flash(request, str(exc), "err")
    return _redirect("accounts")


@router.post("/admin/users/{user_id}/role")
def admin_set_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    role = role.strip()
    if role not in {"admin", "user"}:
        _set_flash(request, "角色无效", "err")
        return _redirect("accounts")
    row = db.get(User, user_id)
    if row is None:
        _set_flash(request, "用户不存在", "err")
        return _redirect("accounts")
    if (
        row.role == "admin"
        and role != "admin"
        and row.is_active
        and users_svc.active_admin_count(db) <= 1
    ):
        _set_flash(request, "至少保留一名启用中的管理员", "err")
        return _redirect("accounts")
    row.role = role
    db.commit()
    _set_flash(request, f"已将 {row.username} 设为 {role}", "ok")
    return _redirect("accounts")


@router.post("/admin/users/{user_id}/toggle")
def admin_toggle_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    row = db.get(User, user_id)
    if row is None:
        _set_flash(request, "用户不存在", "err")
        return _redirect("accounts")
    if row.is_active and row.role == "admin" and users_svc.active_admin_count(db) <= 1:
        _set_flash(request, "至少保留一名启用中的管理员", "err")
        return _redirect("accounts")
    row.is_active = not row.is_active
    db.commit()
    state = "启用" if row.is_active else "禁用"
    _set_flash(request, f"已{state} {row.username}", "ok")
    return _redirect("accounts")


@router.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    row = db.get(User, user_id)
    if row is None:
        _set_flash(request, "用户不存在", "err")
        return _redirect("accounts")
    me = _admin_user_id(request)
    if me is not None and me == row.id:
        _set_flash(request, "不能删除当前登录的管理员", "err")
        return _redirect("accounts")
    if row.role == "admin" and users_svc.active_admin_count(db) <= 1 and row.is_active:
        _set_flash(request, "至少保留一名启用中的管理员", "err")
        return _redirect("accounts")
    name = row.username
    users_svc.delete_user_cascade(db, row)
    _set_flash(request, f"已删除用户 {name} 及其业务数据", "ok")
    return _redirect("accounts")


@router.post("/admin/analysis-tiers")
async def admin_save_tiers(request: Request) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    form = await request.form()
    raw = {k: str(v) for k, v in form.items()}
    try:
        if raw.get("reset_defaults") in ("1", "on", "true", "yes"):
            path = save_tiers(DEFAULT_TIERS)
            _set_flash(request, f"已恢复默认三档（{path.name}）", "ok")
            return _redirect("agent")
        tiers = parse_tier_form(raw)
        for tid in TIER_IDS:
            if not tiers[tid]["agents"]:
                label = tiers[tid]["label"]
                _set_flash(request, f"{label}：至少勾选一个专家", "err")
                return _redirect("agent")
        path = save_tiers(tiers)
        _set_flash(request, f"分析三档已保存（{path.name}）", "ok")
    except Exception as exc:
        _set_flash(request, f"保存失败：{exc}", "err")
    return _redirect("agent")


@router.post("/admin/agent-chat")
async def admin_save_agent_chat(request: Request) -> RedirectResponse:
    if not _authed(request):
        _set_flash(request, "请先登录", "err")
        return _redirect()
    form = await request.form()
    raw = {k: str(v) for k, v in form.items()}
    try:
        cfg = parse_agent_chat_form(raw)
        preset_id = cfg.get("preset_id") or ""
        if preset_id and not presets_svc.get_preset(preset_id):
            _set_flash(request, "所选预设不存在", "err")
            return _redirect("agent")
        path = save_agent_chat(cfg)
        _set_flash(request, f"对话安崽配置已保存（{path.name}）", "ok")
    except Exception as exc:
        _set_flash(request, f"保存失败：{exc}", "err")
    return _redirect("agent")
