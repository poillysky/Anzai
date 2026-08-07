"""分析委员会 LLM 连接（与安崽对话 LLM 配置分离）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, reload_settings
from app.services.settings_store import write_env_updates

logger = logging.getLogger(__name__)

CONN_PATH = Path(__file__).resolve().parents[2] / "data" / "analysis_connection.json"

ANALYSIS_SOURCES: list[dict[str, str]] = [
    {"value": "openai", "label": "OpenAI", "baseUrl": "https://api.openai.com/v1"},
    {"value": "deepseek", "label": "DeepSeek", "baseUrl": "https://api.deepseek.com/v1"},
    {
        "value": "dashscope",
        "label": "通义 · DashScope 兼容",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    {"value": "custom", "label": "自定义 OpenAI 兼容", "baseUrl": ""},
]

SOURCE_BASE: dict[str, str] = {s["value"]: s["baseUrl"] for s in ANALYSIS_SOURCES}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def infer_source(base_url: str) -> str:
    base = (base_url or "").rstrip("/").lower()
    for src in ANALYSIS_SOURCES:
        if src["value"] == "custom":
            continue
        known = (src["baseUrl"] or "").rstrip("/").lower()
        if known and (base == known or base.startswith(known)):
            return src["value"]
    return "custom"


def _blank(
    *,
    source: str = "openai",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    available_models: list[str] | None = None,
) -> dict[str, Any]:
    src = source if source in SOURCE_BASE else "custom"
    if not base_url:
        base_url = SOURCE_BASE.get(src) or "https://api.openai.com/v1"
    if not model:
        model = "gpt-4o-mini"
    models = [str(x).strip() for x in (available_models or []) if str(x).strip()][:80]
    return {
        "source": src,
        "baseUrl": (base_url or "").rstrip("/"),
        "apiKey": api_key or "",
        "model": model or "gpt-4o-mini",
        "availableModels": models,
        "updatedAt": _now(),
    }


def _seed_from_chat_or_env() -> dict[str, Any]:
    """First boot: copy chat LLM so现网不中断；之后各自改互不影响。"""
    s = get_settings()
    base = (getattr(s, "llm_analysis_base_url", None) or "").strip()
    key = (getattr(s, "llm_analysis_api_key", None) or "").strip()
    model = (getattr(s, "llm_analysis_model", None) or "").strip()
    if base or key or model:
        return _blank(
            source=infer_source(base) if base else "openai",
            base_url=base or (s.llm_base_url or ""),
            api_key=key or (s.llm_api_key or ""),
            model=model or (s.llm_model or "gpt-4o-mini"),
        )
    try:
        from app.services import llm_profiles as profiles_svc

        active = profiles_svc.get_active_profile()
        return _blank(
            source=str(active.get("source") or infer_source(str(active.get("baseUrl") or ""))),
            base_url=str(active.get("baseUrl") or s.llm_base_url or ""),
            api_key=str(active.get("apiKey") or s.llm_api_key or ""),
            model=str(active.get("model") or s.llm_model or "gpt-4o-mini"),
        )
    except Exception:
        logger.exception("seed analysis connection from chat failed")
        return _blank(
            source=infer_source(s.llm_base_url or ""),
            base_url=s.llm_base_url or "",
            api_key=s.llm_api_key or "",
            model=s.llm_model or "gpt-4o-mini",
        )


def load_connection() -> dict[str, Any]:
    if not CONN_PATH.is_file():
        conn = _seed_from_chat_or_env()
        save_connection(conn)
        return conn
    try:
        raw = json.loads(CONN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("read analysis_connection failed")
        return _seed_from_chat_or_env()
    if not isinstance(raw, dict):
        return _seed_from_chat_or_env()
    models = raw.get("availableModels") if isinstance(raw.get("availableModels"), list) else []
    return _blank(
        source=str(raw.get("source") or "custom"),
        base_url=str(raw.get("baseUrl") or ""),
        api_key=str(raw.get("apiKey") or ""),
        model=str(raw.get("model") or ""),
        available_models=[str(x) for x in models],
    )


def save_connection(conn: dict[str, Any]) -> Path:
    CONN_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONN_PATH.write_text(
        json.dumps(conn, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return CONN_PATH


def public_connection(conn: dict[str, Any] | None = None) -> dict[str, Any]:
    c = conn or load_connection()
    models = c.get("availableModels") if isinstance(c.get("availableModels"), list) else []
    return {
        "source": c.get("source") or "custom",
        "baseUrl": c.get("baseUrl") or "",
        "model": c.get("model") or "",
        "apiKeySet": bool(str(c.get("apiKey") or "").strip()),
        "updatedAt": c.get("updatedAt") or "",
        "availableModels": [str(x) for x in models if str(x).strip()][:80],
    }


def remember_model_list(ids: list[str]) -> None:
    """Persist model ids on disk — Session cookie 装不下长列表。"""
    conn = load_connection()
    cleaned = [str(x).strip() for x in ids if str(x).strip()][:80]
    conn["availableModels"] = cleaned
    conn["updatedAt"] = _now()
    save_connection(conn)


def saved_model_list() -> list[str]:
    conn = load_connection()
    raw = conn.get("availableModels")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()][:80]


def apply_to_env(conn: dict[str, Any]) -> None:
    write_env_updates(
        {
            "LLM_ANALYSIS_BASE_URL": str(conn.get("baseUrl") or ""),
            "LLM_ANALYSIS_MODEL": str(conn.get("model") or "gpt-4o-mini"),
            "LLM_ANALYSIS_API_KEY": str(conn.get("apiKey") or ""),
        }
    )
    reload_settings()


def update_connection(
    *,
    source: str,
    base_url: str,
    model: str,
    api_key: str | None,
    clear_key: bool = False,
) -> dict[str, Any]:
    cur = load_connection()
    src = source if source in SOURCE_BASE else "custom"
    base = (base_url or "").strip() or SOURCE_BASE.get(src) or cur.get("baseUrl") or ""
    key = cur.get("apiKey") or ""
    if clear_key:
        key = ""
    elif api_key is not None and str(api_key).strip():
        key = str(api_key).strip()
    conn = _blank(
        source=src,
        base_url=base,
        api_key=key,
        model=(model or "").strip() or cur.get("model") or "gpt-4o-mini",
        available_models=list(cur.get("availableModels") or []),
    )
    save_connection(conn)
    apply_to_env(conn)
    return conn


def resolve_creds() -> dict[str, str]:
    """Return api_key / base_url / model for analysis committee — never chat profiles."""
    conn = load_connection()
    base = str(conn.get("baseUrl") or "").strip().rstrip("/")
    key = str(conn.get("apiKey") or "").strip()
    model = str(conn.get("model") or "").strip()
    if not base or not key:
        s = get_settings()
        base = (getattr(s, "llm_analysis_base_url", None) or "").strip().rstrip("/") or base
        key = (getattr(s, "llm_analysis_api_key", None) or "").strip() or key
        model = (getattr(s, "llm_analysis_model", None) or "").strip() or model
    if not base:
        base = "https://api.openai.com/v1"
    return {
        "api_key": key,
        "base_url": base.rstrip("/") + "/",
        "model": model or "gpt-4o-mini",
    }
