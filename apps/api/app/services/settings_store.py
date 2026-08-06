"""Persist settings to apps/api/.env and reload cached Settings."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import get_settings, reload_settings

# apps/api/.env (cwd when running uvicorn is usually apps/api)
ENV_PATH = Path(__file__).resolve().parents[2] / ".env"

EDITABLE_KEYS = (
    "APP_PASSWORD",
    "DEMO_USERNAME",
    "DEMO_PASSWORD",
    "JWT_SECRET",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_MODEL",
    "QUOTE_PROVIDER",
    "CORS_ORIGINS",
    "DATABASE_URL",
)


def _escape_env_value(value: str) -> str:
    if re.search(r'[\s#"\']', value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def read_env_map() -> dict[str, str]:
    data: dict[str, str] = {}
    if not ENV_PATH.exists():
        return data
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        data[key] = val
    return data


def write_env_updates(updates: dict[str, str]) -> Path:
    """Merge updates into .env (create if missing). Only EDITABLE_KEYS."""
    clean = {k: (v if v is not None else "") for k, v in updates.items() if k in EDITABLE_KEYS}
    current = read_env_map()
    current.update(clean)

    # Preserve unknown keys from existing file order when possible
    lines: list[str] = [
        "# 安崽ETF — managed by /admin (do not commit secrets)",
        "",
    ]
    written: set[str] = set()
    if ENV_PATH.exists():
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in EDITABLE_KEYS:
                lines.append(f"{key}={_escape_env_value(current.get(key, ''))}")
                written.add(key)
            else:
                lines.append(raw.rstrip())
                written.add(key)

    for key in EDITABLE_KEYS:
        if key not in written:
            lines.append(f"{key}={_escape_env_value(current.get(key, ''))}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    reload_settings()
    return ENV_PATH


def public_settings_view() -> dict[str, str | bool]:
    """Values for admin form. LLM key never echoed — only llmApiKeySet."""
    s = get_settings()
    return {
        "APP_PASSWORD": s.app_password,
        "DEMO_USERNAME": s.demo_username,
        "DEMO_PASSWORD": s.demo_password,
        "JWT_SECRET": "",  # never echo; leave blank = keep existing on save
        "JWT_SECRET_SET": bool((s.jwt_secret or "").strip()),
        "LLM_API_KEY_SET": bool((s.llm_api_key or "").strip()),
        "LLM_BASE_URL": s.llm_base_url,
        "LLM_MODEL": s.llm_model,
        "QUOTE_PROVIDER": s.quote_provider,
        "CORS_ORIGINS": s.cors_origins,
        "DATABASE_URL": s.database_url,
    }


def mask_secret(value: str, show: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= show:
        return "*" * len(value)
    return "*" * (len(value) - show) + value[-show:]
