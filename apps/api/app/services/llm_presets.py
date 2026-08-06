"""Conversation preset (L1) — single catalog for 安崽真人对话; no API secrets.

Identity is NOT separate presets: injected as 【关系】 prompt via compose_system_prompt.
"""

from __future__ import annotations

import json
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.preset_seeds import BUILTIN_PRESETS, SINGLE_PRESET_ID

logger = logging.getLogger(__name__)

PRESETS_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_presets.json"
AGENT_CHAT_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_chat.json"

DEFAULT_SYSTEM_PROMPT = BUILTIN_PRESETS[0]["system_prompt"]
DEFAULT_REPLY_STYLE = BUILTIN_PRESETS[0]["reply_style"]
DEFAULT_CHIPS = list(BUILTIN_PRESETS[0]["suggested_chips"])


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_preset_id() -> str:
    return f"pr_{secrets.token_hex(4)}"


def _clamp_float(raw: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _clamp_int(raw: Any, default: int, lo: int, hi: int) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _parse_chips(raw: Any) -> list[str]:
    chips: list[str] = []
    if isinstance(raw, list):
        seq = raw
    elif isinstance(raw, str):
        seq = raw.replace("，", ",").split(",")
    else:
        seq = []
    for c in seq:
        s = str(c).strip()
        if s and s not in chips:
            chips.append(s)
        if len(chips) >= 8:
            break
    return chips or list(DEFAULT_CHIPS)


def _blank_preset(
    *,
    preset_id: str,
    name: str,
    system_prompt: str = "",
    reply_style: str = "",
    temperature: float = 0.75,
    top_p: float = 0.95,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    max_tokens: int = 1000,
    history_messages: int = 24,
    suggested_chips: Any = None,
    model_override: str = "",
) -> dict[str, Any]:
    prompt = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    style = (reply_style or "").strip()
    return {
        "id": preset_id,
        "name": (name or "").strip() or "安崽真人对话",
        "system_prompt": prompt,
        "reply_style": style,
        "temperature": _clamp_float(temperature, 1.0, 0.0, 2.0),
        "top_p": _clamp_float(top_p, 1.0, 0.0, 1.0),
        "frequency_penalty": _clamp_float(frequency_penalty, 0.0, -2.0, 2.0),
        "presence_penalty": _clamp_float(presence_penalty, 0.0, -2.0, 2.0),
        "max_tokens": _clamp_int(max_tokens, 2048, 256, 32768),
        "history_messages": _clamp_int(history_messages, 24, 2, 100),
        "suggested_chips": _parse_chips(suggested_chips),
        "model_override": str(model_override or "").strip(),
        "updatedAt": _now(),
    }


def compose_system_prompt(
    preset: dict[str, Any],
    *,
    identity_role: str = "",
    identity_label: str = "",
    include_analyst_skill: bool = False,
) -> str:
    """薄人设 + 关系；分析师 Skill / 场景禁令不在这里堆，由 agent_scene 按轮注入。"""
    from app.services import agent_analyst_skill as skill_svc
    from app.services import identity as identity_svc

    main = str(preset.get("system_prompt") or DEFAULT_SYSTEM_PROMPT).strip()
    style = str(preset.get("reply_style") or "").strip()
    parts = [main]
    if style:
        parts.append(f"【口吻】{style}")
    relation = identity_svc.relation_prompt_block(identity_role, identity_label)
    if relation:
        parts.append(relation)
    else:
        parts.append(
            "【关系】用户尚未选择身份。用中性口语助手语气；可提醒对方在 App 里设置「你是安崽的谁」。"
        )
    if include_analyst_skill:
        parts.append(skill_svc.analyst_skill_block())
    parts.append(
        "【数据纪律】精确数字只引用【本轮实时查询】/工具/本轮附带的仓库或分析；"
        "没有的就说没有，禁止编造。"
        "「今天」以【日历】或行情时间标签为准；标了非今日的是昨收，必须说清。"
    )
    parts.append(
        "【表达】心里专业、嘴上白话。"
        "禁止用一对星号做 Markdown 加粗（尤其包数字）；"
        "数字写进口语句子，有判断、有依据，但不念术语清单。"
    )
    return "\n\n".join(parts)


def _normalize_preset(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return _blank_preset(
        preset_id=str(raw.get("id") or "").strip() or new_preset_id(),
        name=str(raw.get("name") or "安崽真人对话"),
        system_prompt=str(raw.get("system_prompt") or ""),
        reply_style=str(raw.get("reply_style") or ""),
        temperature=raw.get("temperature", 0.75),
        top_p=raw.get("top_p", 0.95),
        frequency_penalty=raw.get("frequency_penalty", 0.0),
        presence_penalty=raw.get("presence_penalty", 0.0),
        max_tokens=raw.get("max_tokens", 1000),
        history_messages=raw.get("history_messages", 24),
        suggested_chips=raw.get("suggested_chips"),
        model_override=str(raw.get("model_override") or ""),
    )


def _builtin_preset() -> dict[str, Any]:
    p = _normalize_preset(dict(BUILTIN_PRESETS[0]))
    assert p is not None
    return p


def _slim_agent_chat() -> None:
    if not AGENT_CHAT_PATH.exists():
        return
    try:
        raw = json.loads(AGENT_CHAT_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        slim = {
            "enabled": bool(raw.get("enabled", True)),
            "preset_id": SINGLE_PRESET_ID,
        }
        AGENT_CHAT_PATH.write_text(
            json.dumps(slim, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        logger.exception("failed to slim %s", AGENT_CHAT_PATH)


def save_presets(store: dict[str, Any]) -> Path:
    """Persist — product uses exactly one preset (the active / only row)."""
    presets_raw = store.get("presets") if isinstance(store, dict) else None
    presets: list[dict[str, Any]] = []
    if isinstance(presets_raw, list):
        for item in presets_raw:
            p = _normalize_preset(item if isinstance(item, dict) else None)
            if p:
                presets.append(p)
                break  # only keep first / single
    if not presets:
        presets = [_builtin_preset()]

    # Always pin stable id for the single catalog entry
    presets[0]["id"] = SINGLE_PRESET_ID
    out = {"activePresetId": SINGLE_PRESET_ID, "presets": presets}
    PRESETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return PRESETS_PATH


def load_presets() -> dict[str, Any]:
    """Load single preset; refresh builtin shell if file missing / empty."""
    data: dict[str, Any] | None = None
    if PRESETS_PATH.exists():
        try:
            raw = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            logger.exception("failed to read %s", PRESETS_PATH)

    if data and isinstance(data.get("presets"), list) and data["presets"]:
        builtin = _builtin_preset()
        chosen = None
        for item in data["presets"]:
            if isinstance(item, dict) and item.get("id") == SINGLE_PRESET_ID:
                chosen = _normalize_preset(item)
                break
        if chosen is None:
            # Drop legacy multi-preset catalog → single builtin (admin can re-edit)
            chosen = builtin
        else:
            # Ensure core contract text present; if old stub, refresh shell from builtin
            prompt = str(chosen.get("system_prompt") or "")
            stale = (
                "本轮场景" not in prompt
                or "倾向性建议" not in prompt
                or "禁止用 Markdown 加粗" not in prompt
                or "别只回两三个字" not in prompt
                or float(chosen.get("temperature") or 0) < 0.95
                or int(chosen.get("max_tokens") or 0) < 2048
                or "禁死板套路" in prompt
                or "不给买卖指令" in prompt
                or "【行情快照】" in prompt
                or "只能引用上下文里已给出的" in prompt
                or "主动同步" in prompt
                or "播报员" in prompt
            )
            if stale:
                kept_name = chosen.get("name")
                # Take full builtin prompt + sampling (真人对话参数)；仅保留自定义名称
                chosen = builtin
                if kept_name and kept_name not in ("对话安崽", "真人对话·默认", "安崽真人对话"):
                    chosen["name"] = kept_name
            elif "禁止固定" not in str(chosen.get("reply_style") or ""):
                chosen["frequency_penalty"] = builtin["frequency_penalty"]
                chosen["presence_penalty"] = builtin["presence_penalty"]
                chosen["temperature"] = builtin["temperature"]
                chosen["max_tokens"] = builtin["max_tokens"]
                chosen["reply_style"] = builtin["reply_style"]
                chosen["suggested_chips"] = builtin["suggested_chips"]
                chosen["system_prompt"] = builtin["system_prompt"]
        store = {"activePresetId": SINGLE_PRESET_ID, "presets": [chosen]}
    else:
        store = {"activePresetId": SINGLE_PRESET_ID, "presets": [_builtin_preset()]}

    save_presets(store)
    _slim_agent_chat()
    return store


def get_preset(preset_id: str | None = None, store: dict[str, Any] | None = None) -> dict[str, Any] | None:
    st = store or load_presets()
    if not st["presets"]:
        return None
    return deepcopy(st["presets"][0])


def get_active_preset(store: dict[str, Any] | None = None) -> dict[str, Any] | None:
    return get_preset(None, store)


def get_the_preset(store: dict[str, Any] | None = None) -> dict[str, Any]:
    p = get_active_preset(store)
    return p if p else _builtin_preset()


def set_active_preset(preset_id: str | None) -> dict[str, Any]:
    """No-op toggle semantics collapsed: always the single preset."""
    store = load_presets()
    store["activePresetId"] = SINGLE_PRESET_ID
    save_presets(store)
    return store


def update_preset(preset_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    store = load_presets()
    cur = store["presets"][0]
    store["presets"][0] = _blank_preset(
        preset_id=SINGLE_PRESET_ID,
        name=str(fields.get("name") or cur["name"]),
        system_prompt=str(
            fields.get("system_prompt") if "system_prompt" in fields else cur["system_prompt"]
        ),
        reply_style=str(
            fields.get("reply_style") if "reply_style" in fields else cur.get("reply_style") or ""
        ),
        temperature=fields.get("temperature", cur["temperature"]),
        top_p=fields.get("top_p", cur.get("top_p", 0.95)),
        frequency_penalty=fields.get("frequency_penalty", cur.get("frequency_penalty", 0.0)),
        presence_penalty=fields.get("presence_penalty", cur.get("presence_penalty", 0.0)),
        max_tokens=fields.get("max_tokens", cur["max_tokens"]),
        history_messages=fields.get("history_messages", cur.get("history_messages", 24)),
        suggested_chips=fields.get("suggested_chips", cur.get("suggested_chips")),
        model_override=str(
            fields.get("model_override")
            if "model_override" in fields
            else cur.get("model_override") or ""
        ),
    )
    save_presets(store)
    return store


def create_preset(fields: dict[str, Any]) -> dict[str, Any]:
    """Product is single-preset: create = overwrite the one preset."""
    return update_preset(SINGLE_PRESET_ID, fields)


def delete_preset(preset_id: str) -> dict[str, Any]:
    raise ValueError("对话预设仅保留一套，不可删除；可编辑内容")


def parse_preset_form(form: dict[str, str]) -> dict[str, Any]:
    return {
        "name": form.get("name") or "",
        "system_prompt": form.get("system_prompt") or "",
        "reply_style": form.get("reply_style") or "",
        "temperature": form.get("temperature") or "1",
        "top_p": form.get("top_p") or "1",
        "frequency_penalty": form.get("frequency_penalty") or "0",
        "presence_penalty": form.get("presence_penalty") or "0",
        "max_tokens": form.get("max_tokens") or "2048",
        "history_messages": form.get("history_messages") or "24",
        "suggested_chips": form.get("suggested_chips") or "",
        "model_override": form.get("model_override") or "",
    }
