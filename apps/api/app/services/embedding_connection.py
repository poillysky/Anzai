"""向量 / Embedding 连接（与聊天 LLM 配置分离）。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings, reload_settings
from app.services.settings_store import write_env_updates

logger = logging.getLogger(__name__)

CONN_PATH = Path(__file__).resolve().parents[2] / "data" / "embedding_connection.json"

EMBEDDING_SOURCES: list[dict[str, str]] = [
    {
        "value": "openai",
        "label": "OpenAI",
        "baseUrl": "https://api.openai.com/v1",
        "defaultModel": "text-embedding-3-small",
    },
    {
        "value": "dashscope",
        "label": "通义 · DashScope 兼容",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "defaultModel": "text-embedding-v4",
    },
    {
        "value": "custom",
        "label": "自定义 OpenAI 兼容",
        "baseUrl": "",
        "defaultModel": "text-embedding-v4",
    },
]

SOURCE_BASE: dict[str, str] = {s["value"]: s["baseUrl"] for s in EMBEDDING_SOURCES}
SOURCE_MODEL: dict[str, str] = {s["value"]: s["defaultModel"] for s in EMBEDDING_SOURCES}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def infer_source(base_url: str) -> str:
    base = (base_url or "").rstrip("/").lower()
    for src in EMBEDDING_SOURCES:
        if src["value"] == "custom":
            continue
        known = (src["baseUrl"] or "").rstrip("/").lower()
        if known and (base == known or base.startswith(known)):
            return src["value"]
    if "maas.aliyuncs.com" in base or "dashscope" in base:
        return "custom"
    return "custom"


def _blank(
    *,
    source: str = "dashscope",
    base_url: str = "",
    api_key: str = "",
    model: str = "",
) -> dict[str, Any]:
    src = source if source in SOURCE_BASE else "custom"
    if not base_url:
        base_url = SOURCE_BASE.get(src) or ""
    if not model:
        model = SOURCE_MODEL.get(src) or "text-embedding-v4"
    return {
        "source": src,
        "baseUrl": (base_url or "").rstrip("/"),
        "apiKey": api_key or "",
        "model": model or "text-embedding-v4",
        "updatedAt": _now(),
    }


def _seed_from_env() -> dict[str, Any]:
    s = get_settings()
    base = (getattr(s, "llm_embedding_base_url", None) or "").strip()
    key = (getattr(s, "llm_embedding_api_key", None) or "").strip()
    model = (s.llm_embedding_model or "").strip() or "text-embedding-v4"
    # Legacy: if only chat env set and embedding empty, leave blank (don't steal chat key)
    return _blank(
        source=infer_source(base) if base else "dashscope",
        base_url=base,
        api_key=key,
        model=model,
    )


def load_connection() -> dict[str, Any]:
    if not CONN_PATH.is_file():
        conn = _seed_from_env()
        save_connection(conn)
        return conn
    try:
        raw = json.loads(CONN_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("read embedding_connection failed")
        return _seed_from_env()
    if not isinstance(raw, dict):
        return _seed_from_env()
    return _blank(
        source=str(raw.get("source") or "custom"),
        base_url=str(raw.get("baseUrl") or ""),
        api_key=str(raw.get("apiKey") or ""),
        model=str(raw.get("model") or ""),
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
    return {
        "source": c.get("source") or "custom",
        "baseUrl": c.get("baseUrl") or "",
        "model": c.get("model") or "",
        "apiKeySet": bool(str(c.get("apiKey") or "").strip()),
        "updatedAt": c.get("updatedAt") or "",
    }


def apply_to_env(conn: dict[str, Any]) -> None:
    write_env_updates(
        {
            "LLM_EMBEDDING_BASE_URL": str(conn.get("baseUrl") or ""),
            "LLM_EMBEDDING_MODEL": str(conn.get("model") or "text-embedding-v4"),
            "LLM_EMBEDDING_API_KEY": str(conn.get("apiKey") or ""),
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
        model=(model or "").strip() or cur.get("model") or SOURCE_MODEL.get(src, ""),
    )
    save_connection(conn)
    apply_to_env(conn)
    # Allow knowledge to retry embeddings after config change
    try:
        from app.services import knowledge as knowledge_svc

        knowledge_svc.reset_embed_cooldown()
    except Exception:
        pass
    return conn


def resolve_creds() -> tuple[str, str, str]:
    """Return (base_url, api_key, model) for embedding calls — never chat LLM."""
    conn = load_connection()
    base = str(conn.get("baseUrl") or "").strip().rstrip("/")
    key = str(conn.get("apiKey") or "").strip()
    model = str(conn.get("model") or "").strip()
    if not base or not key:
        s = get_settings()
        base = (getattr(s, "llm_embedding_base_url", None) or "").strip().rstrip("/") or base
        key = (getattr(s, "llm_embedding_api_key", None) or "").strip() or key
        model = (s.llm_embedding_model or "").strip() or model
    return base, key, model or "text-embedding-v4"
