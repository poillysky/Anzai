"""安崽 chat — OpenAI-compatible streaming + on-demand tools."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings, reload_settings
from app.services import agent_chat_config as cfg_svc
from app.services import agent_history as history_svc
from app.services import agent_scene as scene_svc
from app.services import agent_tools as tools_svc
from app.services import identity as identity_svc
from app.services import llm_presets as presets_svc
from app.services import llm_profiles as profiles_svc
from app.services import preferences as prefs_svc

logger = logging.getLogger(__name__)


def resolve_llm_connection(gen: dict[str, Any] | None = None) -> dict[str, str]:
    """Active L0 profile first, then .env settings."""
    try:
        profile = profiles_svc.get_active_profile()
    except Exception:
        logger.exception("load llm profile failed")
        profile = {}
    settings = get_settings()
    key = (profile.get("apiKey") or settings.llm_api_key or "").strip()
    base = (
        profile.get("baseUrl") or settings.llm_base_url or "https://api.openai.com/v1"
    ).rstrip("/") + "/"
    model = (
        ((gen or {}).get("model_override") or "").strip()
        or (profile.get("model") or "").strip()
        or (settings.llm_model or "").strip()
        or "gpt-4o-mini"
    )
    name = (profile.get("name") or "").strip() or "默认"
    return {"api_key": key, "base_url": base, "model": model, "profile_name": name}


def prepare_chat(
    db: Session,
    user_id: int,
    messages: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return (generation, openai_messages, meta)."""
    identity = prefs_svc.get_identity(db, user_id)
    chat = cfg_svc.load_agent_chat()
    if not chat.get("enabled"):
        chat = {**chat, "enabled": True}
        cfg_svc.save_agent_chat(chat)

    role = identity.get("role") or ""
    label = identity.get("label") or ""

    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            last_user = (m.get("content") or "").strip()
            break
    scene = scene_svc.detect_turn_scene(last_user)

    gen = cfg_svc.resolve_generation(
        chat,
        identity_role=role,
        identity_label=label if role == "custom" else "",
        include_analyst_skill=scene.include_analyst_skill,
    )
    # 按场景动态篇幅：覆盖预设固定 max_tokens（预设只作后台默认上限参考）
    gen = {**gen, "max_tokens": scene.max_tokens}

    _, openai_msgs = scene_svc.assemble_turn(
        db,
        user_id,
        messages,
        system_prompt=gen["system_prompt"],
        history_messages=int(gen.get("history_messages") or 20),
    )

    conn = resolve_llm_connection(gen)
    meta = {
        "model": conn["model"],
        "preset_id": gen["preset_id"],
        "preset_name": gen.get("preset_name") or "",
        "identity": identity,
        "profile_name": conn["profile_name"],
        "tools": list(tools_svc.TOOL_LABELS.keys()),
        "scene": scene.primary,
        "scene_flags": sorted(scene.flags),
        "max_tokens": gen["max_tokens"],
    }
    return gen, openai_msgs, meta


def _is_gemini_model(model: str) -> bool:
    return "gemini" in (model or "").lower()


def _sampling_body(gen: dict[str, Any], model: str) -> dict[str, Any]:
    """Build chat/completions sampling fields — 默认对齐 BrewStory 放开采样。

    Gemini 3.x：thinking + 正文共用 max_tokens，过小会半句截断，故抬高额度；
    reasoning_effort 用 medium（比 low 更放开，比 high 更稳）。
    """
    max_tok = int(gen.get("max_tokens") or 2048)
    body: dict[str, Any] = {
        "model": model,
        "temperature": float(gen.get("temperature") if gen.get("temperature") is not None else 1.0),
        "top_p": float(gen.get("top_p") if gen.get("top_p") is not None else 1.0),
        "max_tokens": max_tok,
    }
    if _is_gemini_model(model):
        body["max_tokens"] = max(max_tok, 16384)
        body["reasoning_effort"] = "medium"
        return body
    # BrewStory 默认惩罚为 0；有自定义时再带上
    fp = float(gen.get("frequency_penalty") or 0)
    pp = float(gen.get("presence_penalty") or 0)
    if fp:
        body["frequency_penalty"] = fp
    if pp:
        body["presence_penalty"] = pp
    return body


def _sampling_body_tools(gen: dict[str, Any], model: str) -> dict[str, Any]:
    """Tool-calling round: smaller budget, low reasoning — decide which tool, not write essay."""
    body = _sampling_body(gen, model)
    if _is_gemini_model(model):
        body["max_tokens"] = min(int(body.get("max_tokens") or 4096), 4096)
        body["reasoning_effort"] = "low"
    else:
        body["max_tokens"] = min(int(body.get("max_tokens") or 2048), 2048)
    return body


async def _chat_once(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    res = await client.post(url, headers=headers, json=body)
    if res.status_code >= 400:
        detail = (res.text or "")[:240]
        raise RuntimeError(f"HTTP {res.status_code}: {detail}")
    data = res.json()
    if not isinstance(data, dict):
        raise RuntimeError("模型返回格式异常")
    return data


def _message_from_completion(data: dict[str, Any]) -> dict[str, Any]:
    choices = data.get("choices") or []
    if not choices:
        return {"role": "assistant", "content": ""}
    msg = (choices[0] or {}).get("message") or {}
    return msg if isinstance(msg, dict) else {"role": "assistant", "content": ""}


async def stream_chat_completion(
    gen: dict[str, Any],
    openai_msgs: list[dict[str, Any]],
    *,
    db: Session,
    user_id: int,
) -> AsyncIterator[str]:
    """Tool rounds (non-stream) then stream the final answer. Yields SSE JSON payloads."""
    reload_settings()
    conn = resolve_llm_connection(gen)
    key = conn["api_key"]
    base = conn["base_url"]
    model = conn["model"]
    if not key:
        yield json.dumps(
            {
                "type": "error",
                "message": (
                    f"当前连接「{conn['profile_name']}」未填写 API 密钥"
                    f"（模型 {model} 已选）。请打开 /admin/llm 在密钥框输入 sk-… 后点保存"
                ),
            },
            ensure_ascii=False,
        )
        yield json.dumps({"type": "done"}, ensure_ascii=False)
        return

    url = urljoin(base, "chat/completions")
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    messages: list[dict[str, Any]] = list(openai_msgs)

    # Prefetch by user intent (works even when gateway has no tool calling)
    user_text = tools_svc.last_user_text(messages)
    turn_scene = scene_svc.detect_turn_scene(user_text)
    prefetch_items = tools_svc.prefetch_for_turn(db, user_id, user_text)
    prefetch_block = tools_svc.format_prefetch_block(
        prefetch_items, scene_primary=turn_scene.primary
    )
    for it in prefetch_items:
        yield json.dumps(
            {
                "type": "tool_start",
                "name": it["name"],
                "label": it["label"],
                "id": f"pre_{it['name']}",
            },
            ensure_ascii=False,
        )
        yield json.dumps(
            {
                "type": "tool_result",
                "name": it["name"],
                "label": it["label"],
                "id": f"pre_{it['name']}",
                "preview": it["text"][:160],
            },
            ensure_ascii=False,
        )
    if prefetch_block:
        messages.append({"role": "system", "content": prefetch_block})

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=15.0)) as client:
            # 预取先塞【本轮实时查询】；再用 tools 让模型补缺（含 Gemini，schema 已无空 enum）
            # tools 轮失败则回退流式，预取数据仍在，不会没数
            for _round in range(tools_svc.MAX_TOOL_ROUNDS):
                body = {
                    **_sampling_body_tools(gen, model),
                    "messages": messages,
                    "tools": tools_svc.TOOL_DEFINITIONS,
                    "tool_choice": "auto",
                    "stream": False,
                }
                try:
                    data = await _chat_once(client, url=url, headers=headers, body=body)
                except RuntimeError as exc:
                    logger.warning("tool round failed, fallback stream: %s", exc)
                    if not prefetch_block:
                        try:
                            snap = tools_svc.execute_tool(db, user_id, "get_indices", {})
                            messages.append(
                                {
                                    "role": "system",
                                    "content": (
                                        "【强制行情】以下为刚才拉取的真实数据，只能引用这里：\n"
                                        + snap
                                    ),
                                }
                            )
                        except Exception:
                            logger.exception("fallback index snapshot failed")
                    break

                msg = _message_from_completion(data)
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    content = (msg.get("content") or "").strip()
                    if content and not prefetch_block:
                        step = 24
                        for i in range(0, len(content), step):
                            yield json.dumps(
                                {"type": "token", "text": content[i : i + step]},
                                ensure_ascii=False,
                            )
                        yield json.dumps({"type": "done"}, ensure_ascii=False)
                        return
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": msg.get("content") or None,
                        "tool_calls": tool_calls,
                    }
                )

                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = str(fn.get("name") or "").strip()
                    tid = str(tc.get("id") or name or "call")
                    args = tools_svc.parse_tool_arguments(fn.get("arguments"))
                    label = tools_svc.tool_label(name)
                    yield json.dumps(
                        {
                            "type": "tool_start",
                            "name": name,
                            "label": label,
                            "id": tid,
                        },
                        ensure_ascii=False,
                    )
                    result = tools_svc.execute_tool(db, user_id, name, args)
                    yield json.dumps(
                        {
                            "type": "tool_result",
                            "name": name,
                            "label": label,
                            "id": tid,
                            "preview": result[:160],
                        },
                        ensure_ascii=False,
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": result[:6000],
                        }
                    )

            # --- final streamed answer (no tools, force prose) ---
            yield json.dumps({"type": "tool_status", "label": "整理回答"}, ensure_ascii=False)
            stream_body = {
                **_sampling_body(gen, model),
                "messages": messages,
                "stream": True,
            }
            async with client.stream("POST", url, headers=headers, json=stream_body) as res:
                if res.status_code >= 400:
                    detail = (await res.aread()).decode("utf-8", errors="replace")[:240]
                    yield json.dumps(
                        {"type": "error", "message": f"模型错误 HTTP {res.status_code}: {detail}"},
                        ensure_ascii=False,
                    )
                    yield json.dumps({"type": "done"}, ensure_ascii=False)
                    return
                async for line in res.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        payload = line[5:].strip()
                    else:
                        continue
                    if payload == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0] or {}).get("delta") or {}
                    token = delta.get("content") or ""
                    if not token:
                        msg = (choices[0] or {}).get("message") or {}
                        token = msg.get("content") or delta.get("text") or ""
                    if isinstance(token, list):
                        token = "".join(
                            (p.get("text") if isinstance(p, dict) else str(p)) or ""
                            for p in token
                        )
                    if token:
                        yield json.dumps({"type": "token", "text": token}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("chat stream failed")
        yield json.dumps(
            {"type": "error", "message": f"连接失败：{type(exc).__name__}: {exc}"},
            ensure_ascii=False,
        )

    yield json.dumps({"type": "done"}, ensure_ascii=False)


def session_payload(
    db: Session,
    user_id: int,
    *,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    identity = prefs_svc.get_identity(db, user_id)
    preset = presets_svc.get_the_preset()
    role = identity.get("role") or ""
    if role == "wife":
        greeting = "嘿嘿，安崽来啦～刚瞄了眼咱们仓库，想听安崽聊哪块呀？"
    elif role == "dad":
        greeting = "爸好呀！安崽在呢～今天想听大盘，还是家里这点仓，跟安崽说说？"
    elif role == "mom":
        greeting = "妈！安崽来陪你啦～不着急，想聊啥慢慢跟安崽讲哦。"
    elif role == "husband":
        greeting = "嗨嗨，安崽报到～仓库安崽瞄过一眼啦，想对哪块，跟安崽说就行。"
    elif role in {"grandpa", "grandma"}:
        greeting = "安崽来啦～今天想听行情还是仓位，跟安崽唠两句呗。"
    elif role in {"brother", "sister", "friend"}:
        greeting = "嘿，安崽来了！行情也好仓位也好，随便问安崽～"
    elif role in {"partner"}:
        greeting = "安崽来啦～想聊行情还是仓位，安崽都在呢。"
    elif role == "self":
        greeting = "安崽在这儿呢～今天想听点啥，直接跟安崽说就好。"
    elif identity.get("configured"):
        greeting = "安崽来啦～想聊行情还是仓位，安崽都听着哦。"
    else:
        greeting = "嗨嗨，安崽来啦～先去设置里选一下「你是安崽的谁」，安崽好用对的语气陪你聊呀。"

    conv = history_svc.resolve_conversation(db, user_id, conversation_id)
    return {
        "enabled": True,
        "identity": {**identity, "roles": identity_svc.list_roles()},
        "preset_id": preset["id"],
        "preset_name": preset.get("name") or "",
        "suggested_chips": list(preset.get("suggested_chips") or presets_svc.DEFAULT_CHIPS),
        "greeting": greeting,
        "conversation_id": conv.id,
        "conversation": history_svc.conversation_dict(conv),
        "messages": history_svc.list_messages(db, user_id, conv.id),
    }
