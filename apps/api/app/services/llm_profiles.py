"""LLM connection profiles (L0) — editable via /admin/llm; secrets stay server-side."""

from __future__ import annotations

import json
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.services.settings_store import write_env_updates

logger = logging.getLogger(__name__)

PROFILES_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_profiles.json"

CHAT_SOURCES: list[dict[str, str]] = [
    {"value": "openai", "label": "OpenAI", "baseUrl": "https://api.openai.com/v1"},
    {"value": "deepseek", "label": "DeepSeek", "baseUrl": "https://api.deepseek.com/v1"},
    {
        "value": "dashscope",
        "label": "通义 · DashScope 兼容",
        "baseUrl": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    },
    {"value": "custom", "label": "自定义 OpenAI 兼容", "baseUrl": ""},
]

SOURCE_BASE: dict[str, str] = {s["value"]: s["baseUrl"] for s in CHAT_SOURCES}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_profile_id() -> str:
    return f"cp_{secrets.token_hex(4)}"


def infer_source(base_url: str) -> str:
    base = (base_url or "").rstrip("/").lower()
    for src in CHAT_SOURCES:
        if src["value"] == "custom":
            continue
        known = (src["baseUrl"] or "").rstrip("/").lower()
        if known and (base == known or base.startswith(known)):
            return src["value"]
    return "custom"


def _blank_profile(
    *,
    profile_id: str,
    name: str,
    source: str = "openai",
    base_url: str = "",
    api_key: str = "",
    model: str = "gpt-4o-mini",
    available_models: list[str] | None = None,
) -> dict[str, Any]:
    if not base_url:
        base_url = SOURCE_BASE.get(source) or "https://api.openai.com/v1"
    models = [str(x).strip() for x in (available_models or []) if str(x).strip()][:80]
    return {
        "id": profile_id,
        "name": name,
        "source": source if source in SOURCE_BASE else "custom",
        "baseUrl": base_url.rstrip("/") or "https://api.openai.com/v1",
        "apiKey": api_key or "",
        "model": model or "gpt-4o-mini",
        "availableModels": models,
        "updatedAt": _now(),
    }


def _normalize_profile(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    pid = str(raw.get("id") or "").strip() or new_profile_id()
    name = str(raw.get("name") or "未命名").strip() or "未命名"
    source = str(raw.get("source") or "").strip() or infer_source(str(raw.get("baseUrl") or ""))
    if source not in SOURCE_BASE:
        source = "custom"
    base = str(raw.get("baseUrl") or SOURCE_BASE.get(source) or "").strip()
    models = raw.get("availableModels") if isinstance(raw.get("availableModels"), list) else []
    return _blank_profile(
        profile_id=pid,
        name=name,
        source=source,
        base_url=base,
        api_key=str(raw.get("apiKey") or ""),
        model=str(raw.get("model") or "gpt-4o-mini"),
        available_models=[str(x) for x in models],
    )


def _seed_from_env() -> dict[str, Any]:
    s = get_settings()
    pid = new_profile_id()
    profile = _blank_profile(
        profile_id=pid,
        name="默认",
        source=infer_source(s.llm_base_url),
        base_url=s.llm_base_url or "https://api.openai.com/v1",
        api_key=s.llm_api_key or "",
        model=s.llm_model or "gpt-4o-mini",
    )
    return {"activeProfileId": pid, "profiles": [profile]}


def _normalize_store(raw: dict[str, Any] | None) -> dict[str, Any]:
    if not raw or not isinstance(raw.get("profiles"), list) or not raw["profiles"]:
        return _seed_from_env()

    profiles: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw["profiles"]:
        p = _normalize_profile(item if isinstance(item, dict) else None)
        if not p or p["id"] in seen:
            continue
        seen.add(p["id"])
        profiles.append(p)
    if not profiles:
        return _seed_from_env()

    active = str(raw.get("activeProfileId") or "").strip()
    if active not in {p["id"] for p in profiles}:
        active = profiles[0]["id"]
    return {"activeProfileId": active, "profiles": profiles}


def load_profiles() -> dict[str, Any]:
    data: dict[str, Any] | None = None
    if PROFILES_PATH.exists():
        try:
            raw = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            logger.exception("failed to read %s; seeding from env", PROFILES_PATH)
    store = _normalize_store(data)
    if data is None:
        save_profiles(store)
    return store


def save_profiles(store: dict[str, Any]) -> Path:
    out = _normalize_store(store)
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return PROFILES_PATH


def get_active_profile(store: dict[str, Any] | None = None) -> dict[str, Any]:
    st = store or load_profiles()
    active = st["activeProfileId"]
    for p in st["profiles"]:
        if p["id"] == active:
            return deepcopy(p)
    return deepcopy(st["profiles"][0])


def public_profile(p: dict[str, Any]) -> dict[str, Any]:
    """Template-safe profile (no raw apiKey)."""
    models = p.get("availableModels") if isinstance(p.get("availableModels"), list) else []
    return {
        "id": p["id"],
        "name": p["name"],
        "source": p["source"],
        "baseUrl": p["baseUrl"],
        "model": p["model"],
        "apiKeySet": bool(str(p.get("apiKey") or "").strip()),
        "updatedAt": p.get("updatedAt") or "",
        "availableModels": [str(x) for x in models if str(x).strip()][:80],
    }


def public_store(store: dict[str, Any] | None = None) -> dict[str, Any]:
    st = store or load_profiles()
    return {
        "activeProfileId": st["activeProfileId"],
        "profiles": [public_profile(p) for p in st["profiles"]],
    }


def saved_model_list(store: dict[str, Any] | None = None) -> list[str]:
    p = get_active_profile(store)
    raw = p.get("availableModels")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()][:80]


def remember_model_list(ids: list[str]) -> None:
    """Persist model ids on active profile — Session cookie 装不下长列表。"""
    store = load_profiles()
    active_id = store["activeProfileId"]
    cleaned = [str(x).strip() for x in ids if str(x).strip()][:80]
    for i, p in enumerate(store["profiles"]):
        if p["id"] != active_id:
            continue
        store["profiles"][i] = _blank_profile(
            profile_id=p["id"],
            name=p["name"],
            source=p.get("source") or "custom",
            base_url=p.get("baseUrl") or "",
            api_key=p.get("apiKey") or "",
            model=p.get("model") or "gpt-4o-mini",
            available_models=cleaned,
        )
        break
    save_profiles(store)


def apply_profile_to_env(profile: dict[str, Any]) -> None:
    write_env_updates(
        {
            "LLM_BASE_URL": str(profile.get("baseUrl") or "https://api.openai.com/v1"),
            "LLM_MODEL": str(profile.get("model") or "gpt-4o-mini"),
            "LLM_API_KEY": str(profile.get("apiKey") or ""),
        }
    )


def sync_active_from_fields(
    *,
    source: str,
    base_url: str,
    model: str,
    api_key: str | None,
    clear_key: bool = False,
) -> dict[str, Any]:
    """Update active profile flat fields and push to .env."""
    store = load_profiles()
    active_id = store["activeProfileId"]
    for i, p in enumerate(store["profiles"]):
        if p["id"] != active_id:
            continue
        src = source if source in SOURCE_BASE else "custom"
        base = (base_url or "").strip() or SOURCE_BASE.get(src) or p["baseUrl"]
        key = p.get("apiKey") or ""
        if clear_key:
            key = ""
        elif api_key is not None and str(api_key).strip():
            key = str(api_key).strip()
        store["profiles"][i] = _blank_profile(
            profile_id=p["id"],
            name=p["name"],
            source=src,
            base_url=base,
            api_key=key,
            model=(model or "").strip() or p["model"],
            available_models=list(p.get("availableModels") or []),
        )
        break
    save_profiles(store)
    apply_profile_to_env(get_active_profile(store))
    return store


def switch_profile(profile_id: str) -> dict[str, Any]:
    store = load_profiles()
    if profile_id not in {p["id"] for p in store["profiles"]}:
        raise ValueError("配置档不存在")
    store["activeProfileId"] = profile_id
    save_profiles(store)
    apply_profile_to_env(get_active_profile(store))
    return store


def create_profile(*, name: str, copy_active: bool = True) -> dict[str, Any]:
    store = load_profiles()
    pid = new_profile_id()
    label = (name or "").strip() or "新配置"
    if copy_active:
        cur = get_active_profile(store)
        profile = _blank_profile(
            profile_id=pid,
            name=label,
            source=cur["source"],
            base_url=cur["baseUrl"],
            api_key=cur.get("apiKey") or "",
            model=cur["model"],
            available_models=list(cur.get("availableModels") or []),
        )
    else:
        profile = _blank_profile(profile_id=pid, name=label)
    store["profiles"].append(profile)
    store["activeProfileId"] = pid
    save_profiles(store)
    apply_profile_to_env(profile)
    return store


def rename_profile(profile_id: str, name: str) -> dict[str, Any]:
    store = load_profiles()
    label = (name or "").strip()
    if not label:
        raise ValueError("名称不能为空")
    found = False
    for p in store["profiles"]:
        if p["id"] == profile_id:
            p["name"] = label
            p["updatedAt"] = _now()
            found = True
            break
    if not found:
        raise ValueError("配置档不存在")
    save_profiles(store)
    return store


def delete_profile(profile_id: str) -> dict[str, Any]:
    store = load_profiles()
    if len(store["profiles"]) <= 1:
        raise ValueError("至少保留一个配置档")
    if profile_id not in {p["id"] for p in store["profiles"]}:
        raise ValueError("配置档不存在")
    store["profiles"] = [p for p in store["profiles"] if p["id"] != profile_id]
    if store["activeProfileId"] == profile_id:
        store["activeProfileId"] = store["profiles"][0]["id"]
        apply_profile_to_env(store["profiles"][0])
    save_profiles(store)
    return store
