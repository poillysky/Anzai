"""Conversation preset (L1) — single catalog for 安崽真人对话; no API secrets.

BrewStory-lite shape: prompts[] + prompt_order.
Identity is NOT separate presets: relation marker filled in compose_system_prompt.
"""

from __future__ import annotations

import json
import logging
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.preset_seeds import (
    BUILTIN_PRESETS,
    BUILTIN_PROMPTS,
    DEFAULT_PROMPT_ORDER,
    PROMPT_ANALYST_SKILL,
    PROMPT_DATA_DISCIPLINE,
    PROMPT_EXPRESSION,
    PROMPT_MAIN,
    PROMPT_RELATION,
    PROMPT_REPLY_STYLE,
    SINGLE_PRESET_ID,
)

logger = logging.getLogger(__name__)

PRESETS_PATH = Path(__file__).resolve().parents[2] / "data" / "llm_presets.json"
AGENT_CHAT_PATH = Path(__file__).resolve().parents[2] / "data" / "agent_chat.json"

DEFAULT_SYSTEM_PROMPT = BUILTIN_PRESETS[0]["system_prompt"]
DEFAULT_REPLY_STYLE = BUILTIN_PRESETS[0]["reply_style"]
DEFAULT_CHIPS = list(BUILTIN_PRESETS[0]["suggested_chips"])

# Markers filled at compose time (not edited as free text in admin content)
MARKER_IDS = frozenset({PROMPT_RELATION, PROMPT_ANALYST_SKILL})


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


def _builtin_prompt_map() -> dict[str, dict[str, Any]]:
    return {str(p["identifier"]): dict(p) for p in BUILTIN_PROMPTS}


def _normalize_prompt_entry(raw: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    base = dict(fallback)
    if not isinstance(raw, dict):
        return base
    ident = str(raw.get("identifier") or base["identifier"]).strip() or base["identifier"]
    base["identifier"] = ident
    if raw.get("name"):
        base["name"] = str(raw["name"]).strip()
    if "role" in raw and raw["role"] in {"system", "user", "assistant"}:
        base["role"] = raw["role"]
    if "marker" in raw:
        base["marker"] = bool(raw["marker"])
    if "enabled" in raw:
        base["enabled"] = bool(raw["enabled"])
    if "injection_position" in raw:
        try:
            base["injection_position"] = int(raw["injection_position"])
        except (TypeError, ValueError):
            pass
    if "wrap" in raw:
        base["wrap"] = str(raw.get("wrap") or "") or base.get("wrap")
    # Markers keep empty content; editable entries take content from raw when present
    if not base.get("marker"):
        if "content" in raw:
            base["content"] = str(raw.get("content") or "")
    else:
        base["content"] = ""
    return base


def _normalize_prompt_order(raw: Any) -> list[dict[str, Any]]:
    builtin_ids = [str(o["identifier"]) for o in DEFAULT_PROMPT_ORDER]
    enabled_map = {str(o["identifier"]): bool(o.get("enabled", True)) for o in DEFAULT_PROMPT_ORDER}
    if isinstance(raw, list) and raw:
        seen: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("identifier") or "").strip()
            if not ident or ident in seen:
                continue
            seen.append(ident)
            if "enabled" in item:
                enabled_map[ident] = bool(item["enabled"])
        # Keep known order first, then any extras
        ordered = [i for i in seen if i in builtin_ids] or list(builtin_ids)
        for i in builtin_ids:
            if i not in ordered:
                ordered.append(i)
        for i in seen:
            if i not in ordered:
                ordered.append(i)
        return [{"identifier": i, "enabled": enabled_map.get(i, True)} for i in ordered]
    return [dict(o) for o in DEFAULT_PROMPT_ORDER]


def _migrate_flat_to_prompts(
    system_prompt: str,
    reply_style: str,
    prompts_raw: Any,
    order_raw: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build prompts stack; fill main/reply_style from flat fields when stack missing."""
    fmap = _builtin_prompt_map()
    by_id: dict[str, dict[str, Any]] = {k: dict(v) for k, v in fmap.items()}

    if isinstance(prompts_raw, list):
        for item in prompts_raw:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("identifier") or "").strip()
            if not ident:
                continue
            fallback = by_id.get(ident) or {
                "identifier": ident,
                "name": ident,
                "role": "system",
                "marker": False,
                "enabled": True,
                "injection_position": 0,
                "content": "",
            }
            by_id[ident] = _normalize_prompt_entry(item, fallback)

    # Flat → stack when prompts absent or main empty
    main = by_id.get(PROMPT_MAIN)
    if main is not None and not (main.get("content") or "").strip():
        main["content"] = (system_prompt or "").strip() or DEFAULT_SYSTEM_PROMPT
    if main is not None and (system_prompt or "").strip() and not isinstance(prompts_raw, list):
        main["content"] = (system_prompt or "").strip()

    style = by_id.get(PROMPT_REPLY_STYLE)
    if style is not None:
        if not isinstance(prompts_raw, list) or not (style.get("content") or "").strip():
            style["content"] = (reply_style or "").strip() or DEFAULT_REPLY_STYLE

    order = _normalize_prompt_order(order_raw)
    # Sync enabled onto prompt entries
    en = {o["identifier"]: bool(o.get("enabled", True)) for o in order}
    for ident, p in by_id.items():
        if ident in en:
            p["enabled"] = en[ident]

    prompts = []
    for o in order:
        ident = o["identifier"]
        if ident in by_id:
            prompts.append(by_id[ident])
        elif ident in fmap:
            prompts.append(dict(fmap[ident]))
    # Append any custom extras not in order
    ordered_ids = {o["identifier"] for o in order}
    for ident, p in by_id.items():
        if ident not in ordered_ids:
            prompts.append(p)
            order.append({"identifier": ident, "enabled": bool(p.get("enabled", True))})

    return prompts, order


def _flat_mirrors_from_prompts(prompts: list[dict[str, Any]]) -> tuple[str, str]:
    main = DEFAULT_SYSTEM_PROMPT
    style = DEFAULT_REPLY_STYLE
    for p in prompts:
        ident = str(p.get("identifier") or "")
        if ident == PROMPT_MAIN:
            main = str(p.get("content") or "").strip() or DEFAULT_SYSTEM_PROMPT
        elif ident == PROMPT_REPLY_STYLE:
            style = str(p.get("content") or "").strip()
    return main, style


def _blank_preset(
    *,
    preset_id: str,
    name: str,
    system_prompt: str = "",
    reply_style: str = "",
    prompts: Any = None,
    prompt_order: Any = None,
    temperature: float = 0.75,
    top_p: float = 0.95,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
    max_tokens: int = 1000,
    history_messages: int = 24,
    suggested_chips: Any = None,
    model_override: str = "",
) -> dict[str, Any]:
    prompt_list, order = _migrate_flat_to_prompts(
        system_prompt or "",
        reply_style or "",
        prompts,
        prompt_order,
    )
    main, style = _flat_mirrors_from_prompts(prompt_list)
    return {
        "id": preset_id,
        "name": (name or "").strip() or "安崽真人对话",
        "system_prompt": main,
        "reply_style": style,
        "prompts": prompt_list,
        "prompt_order": order,
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


def _wrap_block(entry: dict[str, Any], body: str) -> str:
    wrap = str(entry.get("wrap") or "").strip()
    text = (body or "").strip()
    if not text:
        return ""
    if wrap:
        return f"【{wrap}】{text}"
    return text


def _order_enabled(preset: dict[str, Any]) -> list[tuple[str, bool]]:
    order = preset.get("prompt_order") or DEFAULT_PROMPT_ORDER
    out: list[tuple[str, bool]] = []
    for item in order:
        if isinstance(item, dict):
            ident = str(item.get("identifier") or "").strip()
            if ident:
                out.append((ident, bool(item.get("enabled", True))))
    return out or [(str(o["identifier"]), True) for o in DEFAULT_PROMPT_ORDER]


def _prompt_map(preset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    m: dict[str, dict[str, Any]] = {}
    for p in preset.get("prompts") or []:
        if isinstance(p, dict) and p.get("identifier"):
            m[str(p["identifier"])] = p
    # Fill missing builtins
    for bp in BUILTIN_PROMPTS:
        m.setdefault(str(bp["identifier"]), dict(bp))
    return m


def compose_system_prompt(
    preset: dict[str, Any],
    *,
    identity_role: str = "",
    identity_label: str = "",
    include_analyst_skill: bool = False,
) -> str:
    """Assemble system stack from prompts + prompt_order (BrewStory-lite)."""
    from app.services import agent_analyst_skill as skill_svc
    from app.services import identity as identity_svc

    # Ensure stack exists even on partially migrated dicts
    if not preset.get("prompts"):
        preset = _blank_preset(
            preset_id=str(preset.get("id") or SINGLE_PRESET_ID),
            name=str(preset.get("name") or "安崽真人对话"),
            system_prompt=str(preset.get("system_prompt") or ""),
            reply_style=str(preset.get("reply_style") or ""),
            temperature=preset.get("temperature", 1.0),
            top_p=preset.get("top_p", 1.0),
            frequency_penalty=preset.get("frequency_penalty", 0.0),
            presence_penalty=preset.get("presence_penalty", 0.0),
            max_tokens=preset.get("max_tokens", 2048),
            history_messages=preset.get("history_messages", 32),
            suggested_chips=preset.get("suggested_chips"),
            model_override=str(preset.get("model_override") or ""),
        )

    pmap = _prompt_map(preset)
    parts: list[str] = []

    for ident, enabled in _order_enabled(preset):
        if not enabled:
            continue
        entry = pmap.get(ident)
        if entry is None:
            continue
        if not bool(entry.get("enabled", True)):
            continue

        if ident == PROMPT_RELATION:
            relation = identity_svc.relation_prompt_block(identity_role, identity_label)
            if relation:
                parts.append(relation)
            else:
                parts.append(
                    "【关系】用户尚未选择身份。用中性口语助手语气；"
                    "可提醒对方在 App 里设置「你是安崽的谁」。"
                )
            continue

        if ident == PROMPT_ANALYST_SKILL:
            if include_analyst_skill:
                parts.append(skill_svc.analyst_skill_block())
            continue

        if entry.get("marker"):
            continue

        block = _wrap_block(entry, str(entry.get("content") or ""))
        if block:
            parts.append(block)

    if not parts:
        # Absolute fallback
        parts.append(DEFAULT_SYSTEM_PROMPT)
    return "\n\n".join(parts)


def list_editable_prompt_rows(preset: dict[str, Any]) -> list[dict[str, Any]]:
    """Admin UI rows: order + editable content (markers shown read-only)."""
    pmap = _prompt_map(preset)
    rows: list[dict[str, Any]] = []
    for ident, enabled in _order_enabled(preset):
        entry = pmap.get(ident) or {}
        rows.append(
            {
                "identifier": ident,
                "name": str(entry.get("name") or ident),
                "enabled": enabled and bool(entry.get("enabled", True)),
                "marker": bool(entry.get("marker")) or ident in MARKER_IDS,
                "content": "" if ident in MARKER_IDS else str(entry.get("content") or ""),
                "wrap": str(entry.get("wrap") or ""),
                "hint": {
                    PROMPT_RELATION: "运行时按 PWA「你是安崽的谁」注入，此处不可编辑正文。",
                    PROMPT_ANALYST_SKILL: "仅个股/仓位/分析等场景注入，正文来自分析师 Skill 模块。",
                    PROMPT_MAIN: "核心人设；对应旧字段 system_prompt。",
                    PROMPT_REPLY_STYLE: "口吻短句；对应旧字段 reply_style。",
                    PROMPT_DATA_DISCIPLINE: "数字只能引用本轮工具/预取。",
                    PROMPT_EXPRESSION: "排版与口语表达硬约束。",
                }.get(ident, ""),
            }
        )
    return rows


def _normalize_preset(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    return _blank_preset(
        preset_id=str(raw.get("id") or "").strip() or new_preset_id(),
        name=str(raw.get("name") or "安崽真人对话"),
        system_prompt=str(raw.get("system_prompt") or ""),
        reply_style=str(raw.get("reply_style") or ""),
        prompts=raw.get("prompts"),
        prompt_order=raw.get("prompt_order"),
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


def _main_prompt_text(preset: dict[str, Any]) -> str:
    for p in preset.get("prompts") or []:
        if isinstance(p, dict) and p.get("identifier") == PROMPT_MAIN:
            return str(p.get("content") or "")
    return str(preset.get("system_prompt") or "")


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
            prompt = _main_prompt_text(chosen)
            stale = (
                "本轮场景" not in prompt
                or "倾向性建议" not in prompt
                or "禁止用 Markdown 加粗" not in prompt
                or "别只回两三个字" not in prompt
                or "禁研报体" not in prompt
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
            elif not chosen.get("prompts"):
                # Flat file → stack (content already in system_prompt / reply_style)
                chosen = _normalize_preset(chosen)
            # If file had flat-only, ensure prompts written next save
            if not any(
                isinstance(p, dict) and p.get("identifier") == PROMPT_DATA_DISCIPLINE
                for p in (chosen.get("prompts") or [])
            ):
                # Merge sampling from file but ensure stack completeness via normalize
                chosen = _normalize_preset(
                    {
                        **chosen,
                        "prompts": chosen.get("prompts") or builtin["prompts"],
                        "prompt_order": chosen.get("prompt_order") or builtin["prompt_order"],
                    }
                )
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
        prompts=fields.get("prompts") if "prompts" in fields else cur.get("prompts"),
        prompt_order=fields.get("prompt_order")
        if "prompt_order" in fields
        else cur.get("prompt_order"),
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
    """Parse admin form — supports prompt_<id> / prompt_enabled_<id> stack fields."""
    builtin = _builtin_preset()
    prompts: list[dict[str, Any]] = []
    order: list[dict[str, Any]] = []

    # Prefer explicit order from form keys matching builtin order
    for o in builtin.get("prompt_order") or DEFAULT_PROMPT_ORDER:
        ident = str(o["identifier"])
        enabled_key = f"prompt_enabled_{ident}"
        content_key = f"prompt_{ident}"
        # Checkbox: missing => disabled when any prompt_* field present
        has_stack_fields = any(k.startswith("prompt_") for k in form)
        if has_stack_fields:
            enabled = enabled_key in form and str(form.get(enabled_key) or "") not in {
                "",
                "0",
                "false",
                "off",
            }
        else:
            enabled = bool(o.get("enabled", True))

        entry = next(
            (dict(p) for p in (builtin.get("prompts") or []) if p.get("identifier") == ident),
            {
                "identifier": ident,
                "name": ident,
                "role": "system",
                "marker": ident in MARKER_IDS,
                "enabled": enabled,
                "injection_position": 0,
                "content": "",
            },
        )
        entry["enabled"] = enabled
        if ident not in MARKER_IDS and content_key in form:
            entry["content"] = str(form.get(content_key) or "")
            entry["marker"] = False
        prompts.append(entry)
        order.append({"identifier": ident, "enabled": enabled})

    main = next((p for p in prompts if p.get("identifier") == PROMPT_MAIN), None)
    style = next((p for p in prompts if p.get("identifier") == PROMPT_REPLY_STYLE), None)

    # Legacy flat fields (if stack not submitted)
    system_prompt = form.get("system_prompt")
    reply_style = form.get("reply_style")
    if system_prompt is not None and main is not None and f"prompt_{PROMPT_MAIN}" not in form:
        main["content"] = system_prompt
    if reply_style is not None and style is not None and f"prompt_{PROMPT_REPLY_STYLE}" not in form:
        style["content"] = reply_style

    return {
        "name": form.get("name") or "",
        "system_prompt": (main or {}).get("content") or form.get("system_prompt") or "",
        "reply_style": (style or {}).get("content") or form.get("reply_style") or "",
        "prompts": prompts,
        "prompt_order": order,
        "temperature": form.get("temperature") or "1",
        "top_p": form.get("top_p") or "1",
        "frequency_penalty": form.get("frequency_penalty") or "0",
        "presence_penalty": form.get("presence_penalty") or "0",
        "max_tokens": form.get("max_tokens") or "2048",
        "history_messages": form.get("history_messages") or "24",
        "suggested_chips": form.get("suggested_chips") or "",
        "model_override": form.get("model_override") or "",
    }
