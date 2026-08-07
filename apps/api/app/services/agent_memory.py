"""Rolling conversation summary — inject compact memory across long chats."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import httpx
from sqlalchemy.orm import Session

from app.models import AgentConversation, AgentMessage

logger = logging.getLogger(__name__)

SUMMARY_INTERVAL = 10
SUMMARY_MAX_CHARS = 280
_SUMMARY_SYSTEM = (
    "你在为个人理财助手「安崽」压缩对话记忆。"
    "只保留对后续回答有用的事实：用户偏好/风险态度、讨论过的标的与仓位倾向、未决问题。"
    "不要写行情数字（会过期）；不要建议买卖；不要客套。"
    f"摘要不超过 {SUMMARY_MAX_CHARS} 字。只输出摘要正文。"
)


def get_conversation_memory(db: Session, user_id: int, conversation_id: int) -> str:
    row = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return ""
    return (getattr(row, "memory_summary", None) or "").strip()


def format_memory_block(summary: str) -> str:
    s = (summary or "").strip()
    if not s:
        return ""
    return f"【会话记忆】（滚动摘要，数字以本轮实时查询为准）\n{s}"


def messages_since_summary(db: Session, user_id: int, conversation_id: int) -> int:
    row = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return 0
    marker_id = int(getattr(row, "memory_until_message_id", 0) or 0)
    q = db.query(AgentMessage).filter(
        AgentMessage.user_id == user_id,
        AgentMessage.conversation_id == conversation_id,
        AgentMessage.role.in_(("user", "assistant")),
    )
    if marker_id > 0:
        q = q.filter(AgentMessage.id > marker_id)
    return int(q.count() or 0)


def should_auto_summarize(db: Session, user_id: int, conversation_id: int) -> bool:
    total = (
        db.query(AgentMessage)
        .filter(
            AgentMessage.user_id == user_id,
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.role.in_(("user", "assistant")),
        )
        .count()
    )
    if total < SUMMARY_INTERVAL:
        return False
    return messages_since_summary(db, user_id, conversation_id) >= SUMMARY_INTERVAL


def _transcript_for_summary(
    db: Session, user_id: int, conversation_id: int
) -> tuple[str, int]:
    """Return (user content for LLM, last_message_id used)."""
    row = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        .first()
    )
    prev = (getattr(row, "memory_summary", None) or "").strip() if row else ""
    marker_id = int(getattr(row, "memory_until_message_id", 0) or 0) if row else 0

    q = (
        db.query(AgentMessage)
        .filter(
            AgentMessage.user_id == user_id,
            AgentMessage.conversation_id == conversation_id,
            AgentMessage.role.in_(("user", "assistant")),
        )
        .order_by(AgentMessage.id.asc())
    )
    if marker_id > 0:
        q = q.filter(AgentMessage.id > marker_id)
    rows = q.limit(80).all()
    if not rows:
        return "", -1

    parts: list[str] = []
    if prev:
        parts.append(f"已有摘要：\n{prev}")
    buf: list[str] = []
    last_id = -1
    for m in rows:
        role = "用户" if m.role == "user" else "安崽"
        content = (m.content or "").strip()[:600]
        if not content:
            continue
        buf.append(f"{role}：{content}")
        last_id = int(m.id)
    if not buf:
        return "", -1
    parts.append("\n".join(buf))
    return "\n\n".join(parts), last_id


def _extractive_fallback(transcript: str) -> str:
    lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]
    user_bits = [ln[3:] for ln in lines if ln.startswith("用户：")][-5:]
    if not user_bits:
        return "近期有过理财相关对话，具体以最近几轮为准。"
    joined = "；".join(b[:36] for b in user_bits)
    return f"近期聊过：{joined}"[:SUMMARY_MAX_CHARS]


def _call_summary_llm(gen: dict[str, Any], content: str) -> str:
    from app.services.agent_chat import resolve_llm_connection, _sampling_body

    conn = resolve_llm_connection(gen)
    key = conn.get("api_key") or ""
    base = conn.get("base_url") or ""
    model = conn.get("model") or ""
    if not key or not base or not model:
        return ""
    url = urljoin(base, "chat/completions")
    body = {
        **_sampling_body({**gen, "max_tokens": 400, "temperature": 0.3}, model),
        "messages": [
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": content},
        ],
        "stream": False,
    }
    # Cap reply tokens for summary
    body["max_tokens"] = min(int(body.get("max_tokens") or 400), 512)
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0)) as client:
        res = client.post(url, headers=headers, json=body)
        if res.status_code >= 400:
            logger.warning("summary llm HTTP %s: %s", res.status_code, (res.text or "")[:160])
            return ""
        data = res.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    text = (msg.get("content") or "").strip()
    return text[:SUMMARY_MAX_CHARS]


def maybe_update_conversation_summary(
    db: Session,
    user_id: int,
    conversation_id: int,
    *,
    gen: dict[str, Any] | None = None,
) -> str | None:
    """If enough new turns, refresh rolling summary. Returns new summary or None."""
    if not should_auto_summarize(db, user_id, conversation_id):
        return None
    transcript, last_id = _transcript_for_summary(db, user_id, conversation_id)
    if not transcript or last_id < 0:
        return None

    summary = ""
    if gen:
        try:
            summary = _call_summary_llm(gen, transcript)
        except Exception:
            logger.exception("conversation summary llm failed")
    if not summary:
        summary = _extractive_fallback(transcript)

    row = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        .first()
    )
    if row is None:
        return None
    row.memory_summary = summary[:1200]
    row.memory_until_message_id = last_id
    db.commit()
    return summary
