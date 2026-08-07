"""安崽 chat — OpenAI-compatible streaming + on-demand tools."""

from __future__ import annotations

import json
import logging
import random
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

# 开场白按身份多套（语气对齐 identity.tone），session / 新对话随机一条
_GREETINGS: dict[str, tuple[str, ...]] = {
    "dad": (
        "爸好呀！安崽在呢～今天想听大盘，还是家里这点仓，跟安崽说说？",
        "安崽在呢爸～刚醒，今天想听点啥，直接说就行。",
        "嘿爸，安崽报到。有想问的票或仓位，扔过来就行。",
        "回来啦～安崽候着。行情还是仓，你想唠哪头？",
        "安崽待命中。想盯指数、翻翻仓库，还是随便唠两句？",
        "爸，安崽来了。不端着，有啥想问的直接开口。",
        "安崽在～刚瞄了眼盘。想听涨跌还是盘盘家里仓？",
        "嘿，安崽报到。今天复盘也好，问票也好，你说。",
        "安崽来啦～像回家唠嗑那样就行，想听啥点一下。",
        "在呢～大盘、仓位、某只票，扔过来安崽接。",
    ),
    "mom": (
        "妈！安崽来陪你啦～不着急，想聊啥慢慢跟安崽讲哦。",
        "妈好呀～安崽在呢，行情也好仓位也好，慢慢说。",
        "安崽来啦妈～今天想听轻松一点的，还是看家里仓？",
        "安崽候着你～有啥担心的也行，先跟安崽唠两句。",
        "嗨妈～安崽来了。想看大盘还是看看咱们仓，你说。",
        "安崽在呢～不赶时间，想听涨跌还是安心看看仓？",
        "来啦～安崽陪你。今天情绪也好、数也好，慢慢讲就行。",
        "安崽报到～想先听人话版行情，还是翻翻家里仓？",
        "在呢妈～有放心不下的，跟安崽说一声就好。",
        "安崽来啦～先接住你想聊的，再慢慢对上数。",
    ),
    "wife": (
        "嘿嘿，安崽来啦～刚瞄了眼咱们仓库，想听安崽聊哪块呀？",
        "安崽报到～今天想听行情还是仓位，你点题就行。",
        "嗨嗨～安崽在呢。咱们仓安崽瞄过，想对哪块跟安崽说。",
        "安崽来啦～不忙的话，大盘还是家里仓，聊哪个？",
        "嘿，安崽在～想听涨跌还是盘盘仓，随便开口。",
        "安崽候着～一起理财那档事，想聊哪头你定。",
        "来啦～刚看了眼咱们仓。想听倾向也行，不硬下单。",
        "安崽在呢～行情、仓位、某只票，咱们慢慢对。",
        "嘿嘿报到～今天复盘还是随便唠两句涨跌？",
        "安崽来啦～务实聊钱就行，想听啥你开口。",
    ),
    "husband": (
        "嗨嗨，安崽报到～仓库安崽瞄过一眼啦，想对哪块，跟安崽说就行。",
        "安崽来啦～行情或仓位，你想听哪头？",
        "嘿，安崽在呢。今天想看大盘还是翻仓库？",
        "安崽待命～有票想问、仓想聊，直接说。",
        "报到～安崽刚醒，大盘还是仓位，你定。",
        "安崽在～咱们仓瞄过了，想听倾向或复盘都行。",
        "来啦～一起把账理清楚，想聊涨跌还是仓？",
        "安崽候着～务实说，不装专家，你点题。",
        "嘿报到～今天看盘还是盘盘咱们仓？",
        "安崽来啦～有想法扔过来，安崽接住再说清楚。",
    ),
    "grandpa": (
        "安崽来啦～今天想听行情还是仓位，跟安崽唠两句呗。",
        "爷爷好～安崽在呢，慢慢说，行情仓位都行。",
        "安崽陪您唠唠～想听大盘还是看看仓？",
        "安崽来啦爷爷～不着急，想问啥跟安崽讲。",
        "安崽候着～今天用大白话听行情，还是翻翻仓？",
        "在呢～安崽把涨跌说成人话，您想听哪头？",
        "安崽来陪您坐会儿～大盘、仓位，慢慢点。",
        "报到～不赶，想听指数还是家里仓，您说。",
        "安崽在～有听不懂的数，安崽用比喻讲给您听。",
        "来啦爷爷～想唠行情或看看仓，开口就行。",
    ),
    "grandma": (
        "安崽来啦～今天想听行情还是仓位，跟安崽唠两句呗。",
        "奶奶好～安崽在呢，慢慢说就行。",
        "安崽陪您坐会儿～想听大盘还是家里仓？",
        "安崽来啦～有想问的，跟安崽慢慢讲哦。",
        "在呢～安崽把行情说成听得懂的话，您想听哪块？",
        "安崽候着～不着急，涨跌也好仓也好，慢慢聊。",
        "来啦奶奶～想先听轻松版行情，还是看看仓？",
        "安崽报到～有放心不下的，跟安崽说一声。",
        "安崽在～数字绕的地方，安崽用人话跟您对。",
        "陪您唠唠～大盘还是家里仓，您点一下就好。",
    ),
    "brother": (
        "嘿，安崽来了！行情也好仓位也好，随便问安崽～",
        "安崽在～今天想听啥，大盘还是仓？",
        "嘿，安崽报到。有票想聊直接扔过来。",
        "安崽来啦～闲聊行情或盘仓，开麦就行。",
        "哥，安崽在～不端着，想听涨跌还是翻仓？",
        "报到～有想吐槽的票也行，安崽接着聊。",
        "安崽候着～复盘、问票、看仓，你挑。",
        "嘿来啦～今天轻松唠两句盘，还是认真盘仓？",
        "安崽在呢～风险安崽会讲清，你先开口。",
        "来了～大盘指数或家里仓，扔个话题就行。",
    ),
    "sister": (
        "嘿，安崽来了！行情也好仓位也好，随便问安崽～",
        "安崽在～今天想听大盘还是翻翻仓？",
        "嗨，安崽报到～有想问的票或仓位说一声。",
        "安崽来啦～行情仓位都行，随便聊。",
        "姐，安崽在～别端着，想听啥直接说。",
        "报到～涨跌也好、仓也好，开麦就行。",
        "安崽候着～轻松唠盘还是认真看看仓？",
        "嗨来啦～有想问的票扔过来，安崽接。",
        "安崽在呢～今天复盘还是随便聊聊行情？",
        "来啦～大盘、仓位、某只票，你点题。",
    ),
    "friend": (
        "嘿，安崽来了！行情也好仓位也好，随便问安崽～",
        "安崽在呢～今天聊大盘还是仓位？",
        "嘿，安崽报到。有想法直接说。",
        "安崽来啦～闲聊涨跌或家里仓，都行。",
        "在～不装权威，想听啥扔过来。",
        "报到～吐槽行情也行，问仓也行。",
        "安崽候着～复盘、问票、看指数，你挑。",
        "嘿来了～今天轻松唠两句还是认真对仓？",
        "安崽在～有想法直接开麦，安崽接住。",
        "来啦～大盘涨跌或仓位结构，随便开口。",
    ),
    "self": (
        "安崽在这儿呢～今天想听点啥，直接跟安崽说就好。",
        "安崽待命～大盘还是仓位，自己点题。",
        "嘿，安崽在。想复盘还是看盘，说一声。",
        "安崽来啦～今天聊涨跌还是翻翻仓？",
        "安崽在呢～有想法直接扔过来就行。",
        "报到～平等聊，专业但不端着。想听哪头？",
        "安崽候着～指数、仓位、单票，你定。",
        "在～今天复盘、看盘还是随便问一句？",
        "安崽来啦～不硬加称呼，想聊啥开口就行。",
        "待命中～行情或仓库，点一下安崽接着讲。",
    ),
}
# 自定义身份 / 未知 role：中性口语
_GREETINGS_CONFIGURED = (
    "安崽来啦～想聊行情还是仓位，安崽都听着哦。",
    "嗨，安崽在呢～今天想听大盘还是家里仓？",
    "安崽报到～行情仓位都行，你开口就行。",
    "安崽来啦～不着急，想聊啥跟安崽说。",
    "嘿，安崽在～刚醒，大盘还是仓，你点题。",
    "安崽候着～复盘、问票、看涨跌，随便开口。",
    "来啦～想听人话版行情还是翻翻仓？",
    "安崽在呢～有想法扔过来就行。",
)
_GREETINGS_SETUP = (
    "嗨嗨，安崽来啦～先去设置里选一下「你是安崽的谁」，安崽好用对的语气陪你聊呀。",
    "安崽报到～先到设置里选好身份，安崽才能用对的口气陪你聊哦。",
    "嘿，安崽在～设置里选一下「你是安崽的谁」，选完安崽就好开口啦。",
    "安崽来啦～去设置里定一下身份，选完安崽就按对的语气陪你聊。",
    "报到～先选「你是安崽的谁」，安崽好开口，别用错口气～",
    "在呢～设置里点一下身份，安崽就能用对的语气候着你啦。",
)


def _pick_greeting(identity: dict[str, Any]) -> str:
    role = str(identity.get("role") or "")
    pool = _GREETINGS.get(role)
    if pool:
        return random.choice(pool)
    if identity.get("configured"):
        return random.choice(_GREETINGS_CONFIGURED)
    return random.choice(_GREETINGS_SETUP)


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
    *,
    conversation_id: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return (generation, openai_messages, meta)."""
    from app.services import agent_tokens as tokens_svc

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
    max_context = int(gen.get("max_context") or tokens_svc.DEFAULT_MAX_CONTEXT)
    gen = {**gen, "max_tokens": scene.max_tokens, "max_context": max_context}

    _, openai_msgs, assemble_meta = scene_svc.assemble_turn(
        db,
        user_id,
        messages,
        system_prompt=gen["system_prompt"],
        history_messages=int(gen.get("history_messages") or 20),
        conversation_id=conversation_id,
        max_context=max_context,
        reserve_for_reply=int(gen["max_tokens"]),
    )
    if assemble_meta.get("clarify"):
        gen = {**gen, "max_tokens": min(int(gen["max_tokens"]), 512)}

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
        "max_context": max_context,
        "assemble": assemble_meta,
    }
    return gen, openai_msgs, meta


def preview_chat(
    db: Session,
    user_id: int,
    messages: list[dict[str, str]],
    *,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    """Dry-run assemble for debugging — what the model would see this turn."""
    from app.services import agent_tokens as tokens_svc

    gen, openai_msgs, meta = prepare_chat(
        db, user_id, messages, conversation_id=conversation_id
    )
    user_text = tools_svc.last_user_text(openai_msgs) or ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            user_text = (m.get("content") or "").strip()
            break
    turn_scene = scene_svc.detect_turn_scene(user_text)
    from app.services import agent_clarify as clarify_svc

    clarify = clarify_svc.detect_clarify_need(user_text, history=messages)
    if clarify_svc.should_skip_prefetch(clarify):
        prefetch_items = []
    else:
        prefetch_items = tools_svc.prefetch_for_turn(db, user_id, user_text)
    prefetch_block = tools_svc.format_prefetch_block(
        prefetch_items, scene_primary=turn_scene.primary
    )
    msgs = list(openai_msgs)
    if prefetch_block:
        msgs.append({"role": "system", "content": prefetch_block})
    msgs = tokens_svc.trim_messages_to_budget(
        msgs,
        int(meta.get("max_context") or tokens_svc.DEFAULT_MAX_CONTEXT),
        int(gen.get("max_tokens") or 2048),
    )

    system_blocks: list[dict[str, Any]] = []
    for m in msgs:
        if (m.get("role") or "") != "system":
            continue
        content = str(m.get("content") or "")
        system_blocks.append(
            {
                "chars": len(content),
                "tokens": tokens_svc.estimate_tokens(content),
                "preview": content[:240] + ("…" if len(content) > 240 else ""),
            }
        )

    return {
        "scene": meta.get("scene"),
        "scene_flags": meta.get("scene_flags") or [],
        "clarify": meta.get("assemble", {}).get("clarify") if isinstance(meta.get("assemble"), dict) else meta.get("clarify"),
        "model": meta.get("model"),
        "preset_name": meta.get("preset_name"),
        "max_tokens": meta.get("max_tokens"),
        "max_context": meta.get("max_context"),
        "assemble": meta.get("assemble") or {},
        "prefetch": [
            {"name": it["name"], "label": it["label"], "chars": len(it.get("text") or "")}
            for it in prefetch_items
        ],
        "message_count": len(msgs),
        "tokens_estimate": tokens_svc.estimate_messages_tokens(msgs),
        "system_blocks": system_blocks,
        "history_tail": [
            {
                "role": m.get("role"),
                "chars": len(str(m.get("content") or "")),
                "preview": str(m.get("content") or "")[:120],
            }
            for m in msgs
            if (m.get("role") or "") in {"user", "assistant"}
        ][-8:],
    }


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


async def _yield_wait_analysis(
    *,
    user_id: int,
    job_id: int,
    card: dict[str, Any] | None,
    start_text: str,
) -> AsyncIterator[tuple[str, str | None]]:
    """Yield (sse_json, report_text_or_none). Last report_text is set on analysis_ready."""
    from app.services.agent_analysis_wait import (
        analysis_label_from_job,
        iter_wait_analysis,
        parse_job_id_from_start_text,
    )
    from app.database import SessionLocal
    from app.services import analysis as analysis_svc

    jid = job_id or parse_job_id_from_start_text(start_text) or 0
    if not jid and card:
        try:
            jid = int(card.get("job_id") or 0)
        except (TypeError, ValueError):
            jid = 0
    if not jid:
        return

    label = ""
    degree = None
    if card:
        label = str(card.get("name") or card.get("label") or "").strip()
        degree = str(card.get("degree") or "") or None
        if str(card.get("scope") or "") == "portfolio":
            label = "仓库"
    if not label or not degree:
        db2 = SessionLocal()
        try:
            job = analysis_svc.get_job(db2, jid, user_id)
            if job is not None:
                label = label or analysis_label_from_job(job)
                degree = degree or str(job.degree or "standard")
        finally:
            db2.close()

    async for ev in iter_wait_analysis(
        user_id=user_id,
        job_id=jid,
        label=label,
        degree=degree,
    ):
        et = ev.get("type")
        if et == "token":
            yield json.dumps({"type": "token", "text": ev.get("text") or ""}, ensure_ascii=False), None
        elif et == "tool_status":
            yield json.dumps(
                {
                    "type": "tool_status",
                    "label": ev.get("label") or "分析中",
                    "name": ev.get("name") or "start_analysis",
                },
                ensure_ascii=False,
            ), None
        elif et == "analysis_ready":
            text = str(ev.get("text") or "")
            yield json.dumps(
                {
                    "type": "tool_status",
                    "label": "整理结论中"
                    if ev.get("ok")
                    else "分析未完成",
                    "name": "start_analysis",
                },
                ensure_ascii=False,
            ), text
            return


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
    from app.services import agent_clarify as clarify_svc
    from app.services import agent_tokens as tokens_svc

    user_text = tools_svc.last_user_text(messages)
    turn_scene = scene_svc.detect_turn_scene(user_text)
    clarify = clarify_svc.detect_clarify_need(user_text, history=openai_msgs)
    if clarify_svc.should_skip_prefetch(clarify):
        prefetch_items = []
        # Ensure clarify block present even if assemble missed (e.g. old path)
        if not any(
            "【本轮·先问清楚】" in str(m.get("content") or "")
            for m in messages
            if (m.get("role") or "") == "system"
        ):
            messages.append(
                {"role": "system", "content": clarify_svc.format_clarify_block(clarify)}
            )
    else:
        prefetch_items = tools_svc.prefetch_for_turn(db, user_id, user_text)
    prefetch_block = tools_svc.format_prefetch_block(
        prefetch_items, scene_primary=turn_scene.primary
    )
    analysis_report_block: str | None = None
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
        card = tools_svc.card_payload_for_tool(db, user_id, it["name"])
        if card:
            yield json.dumps({"type": "card", "card": card}, ensure_ascii=False)
        if it["name"] == "start_analysis":
            async for sse, report in _yield_wait_analysis(
                user_id=user_id,
                job_id=int((card or {}).get("job_id") or 0) if isinstance(card, dict) else 0,
                card=card if isinstance(card, dict) else None,
                start_text=str(it.get("text") or ""),
            ):
                yield sse
                if report:
                    analysis_report_block = report
    if prefetch_block:
        messages.append({"role": "system", "content": prefetch_block})
    if analysis_report_block:
        messages.append(
            {
                "role": "system",
                "content": (
                    "【本轮分析已完成·请据此播报】\n"
                    + analysis_report_block
                    + "\n用户已经等过委员会；直接讲结论，不要再说请等待。"
                ),
            }
        )

    # Prefetch / report may bloat context — keep system prefix, drop older chat
    messages = tokens_svc.trim_messages_to_budget(
        messages,
        int(gen.get("max_context") or tokens_svc.DEFAULT_MAX_CONTEXT),
        int(gen.get("max_tokens") or turn_scene.max_tokens),
    )

    try:
        # 等分析时可能超过 90s；工具轮 + 最终流式分开放宽
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            # 本轮已等完委员会：跳过 tools 轮，直接据报告流式播报
            if analysis_report_block:
                yield json.dumps(
                    {"type": "tool_status", "label": "整理结论中"},
                    ensure_ascii=False,
                )
                stream_body = {
                    **_sampling_body(gen, model),
                    "messages": messages,
                    "stream": True,
                }
                async with client.stream(
                    "POST", url, headers=headers, json=stream_body
                ) as res:
                    if res.status_code >= 400:
                        detail = (await res.aread()).decode("utf-8", errors="replace")[
                            :240
                        ]
                        yield json.dumps(
                            {
                                "type": "error",
                                "message": f"模型错误 HTTP {res.status_code}: {detail}",
                            },
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
                                (p.get("text") if isinstance(p, dict) else str(p))
                                or ""
                                for p in token
                            )
                        if token:
                            yield json.dumps(
                                {"type": "token", "text": token},
                                ensure_ascii=False,
                            )
                yield json.dumps({"type": "done"}, ensure_ascii=False)
                return

            # 模糊意图：只反问，不开工具轮（避免对着空预取仍去查板）
            if clarify:
                stream_gen = {**gen, "max_tokens": min(int(gen.get("max_tokens") or 512), 512)}
                stream_body = {
                    **_sampling_body(stream_gen, model),
                    "messages": messages,
                    "stream": True,
                }
                async with client.stream(
                    "POST", url, headers=headers, json=stream_body
                ) as res:
                    if res.status_code >= 400:
                        detail = (await res.aread()).decode("utf-8", errors="replace")[
                            :240
                        ]
                        yield json.dumps(
                            {
                                "type": "error",
                                "message": f"模型错误 HTTP {res.status_code}: {detail}",
                            },
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
                                (p.get("text") if isinstance(p, dict) else str(p))
                                or ""
                                for p in token
                            )
                        if token:
                            yield json.dumps(
                                {"type": "token", "text": token},
                                ensure_ascii=False,
                            )
                yield json.dumps({"type": "done"}, ensure_ascii=False)
                return

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
                    result = tools_svc.execute_tool(
                        db, user_id, name, args, user_text=user_text
                    )
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
                    card = tools_svc.card_payload_for_tool(db, user_id, name)
                    if card:
                        yield json.dumps(
                            {"type": "card", "card": card}, ensure_ascii=False
                        )
                    if name == "start_analysis" and "开不了" not in result and "失败" not in result:
                        async for sse, report in _yield_wait_analysis(
                            user_id=user_id,
                            job_id=int((card or {}).get("job_id") or 0)
                            if isinstance(card, dict)
                            else 0,
                            card=card if isinstance(card, dict) else None,
                            start_text=result,
                        ):
                            yield sse
                            if report:
                                analysis_report_block = report
                                messages.append(
                                    {
                                        "role": "system",
                                        "content": (
                                            "【本轮分析已完成·请据此播报】\n"
                                            + report
                                            + "\n用户已经等过委员会；直接讲结论，不要再说请等待。"
                                        ),
                                    }
                                )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tid,
                            "content": result[:6000],
                        }
                    )

            # --- final streamed answer (no tools, force prose) ---
            yield json.dumps({"type": "tool_status", "label": "整理结论中"}, ensure_ascii=False)
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
    greeting = _pick_greeting(identity)

    conv = history_svc.resolve_conversation(db, user_id, conversation_id)
    return {
        "enabled": True,
        "identity": {**identity, "roles": identity_svc.list_roles()},
        "preset_id": preset["id"],
        "preset_name": preset.get("name") or "",
        "suggested_chips": [],
        "greeting": greeting,
        "conversation_id": conv.id,
        "conversation": history_svc.conversation_dict(conv),
        "messages": history_svc.list_messages(db, user_id, conv.id),
    }
