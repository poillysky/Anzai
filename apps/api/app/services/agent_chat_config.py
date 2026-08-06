"""Chat agent config — points at the single conversation preset."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services import llm_presets as presets_svc
from app.services.preset_seeds import SINGLE_PRESET_ID

logger = logging.getLogger(__name__)

AGENT_CHAT_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_chat.json"

DEFAULT_AGENT_CHAT: dict[str, Any] = {
    "enabled": True,
    "preset_id": SINGLE_PRESET_ID,
}


def _normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_AGENT_CHAT)
    if not raw:
        return base
    base["enabled"] = bool(raw.get("enabled", True))
    base["preset_id"] = SINGLE_PRESET_ID
    return base


def load_agent_chat() -> dict[str, Any]:
    presets_svc.load_presets()
    data: dict[str, Any] | None = None
    if AGENT_CHAT_PATH.exists():
        try:
            raw = json.loads(AGENT_CHAT_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            logger.exception("failed to read %s", AGENT_CHAT_PATH)
    return _normalize(data)


def save_agent_chat(raw: dict[str, Any]) -> Path:
    out = _normalize(raw)
    AGENT_CHAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    AGENT_CHAT_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return AGENT_CHAT_PATH


def parse_agent_chat_form(form: dict[str, str]) -> dict[str, Any]:
    return {
        "enabled": form.get("enabled") in ("1", "on", "true", "yes"),
        "preset_id": SINGLE_PRESET_ID,
    }


def resolve_generation(
    cfg: dict[str, Any] | None = None,
    *,
    identity_role: str = "",
    identity_label: str = "",
    include_analyst_skill: bool = False,
) -> dict[str, Any]:
    chat = cfg or load_agent_chat()
    preset = presets_svc.get_the_preset()
    return {
        "enabled": chat.get("enabled", True),
        "preset_id": preset["id"],
        "preset_name": preset.get("name") or "",
        "system_prompt": presets_svc.compose_system_prompt(
            preset,
            identity_role=identity_role,
            identity_label=identity_label,
            include_analyst_skill=include_analyst_skill,
        ),
        "reply_style": preset.get("reply_style") or "",
        "temperature": preset["temperature"],
        "top_p": preset.get("top_p", 0.95),
        "frequency_penalty": preset.get("frequency_penalty", 0.0),
        "presence_penalty": preset.get("presence_penalty", 0.0),
        "max_tokens": preset["max_tokens"],
        "history_messages": preset.get("history_messages", 24),
        "suggested_chips": list(preset.get("suggested_chips") or presets_svc.DEFAULT_CHIPS),
        "model_override": preset.get("model_override") or "",
        "identity_role": identity_role,
        "identity_label": identity_label,
    }
