"""Per-user 安崽 chat history — multi-conversation (open / closed / new)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import AgentConversation, AgentMessage

logger = logging.getLogger(__name__)

STORE_LIMIT = 400
LOAD_LIMIT = 120
CONV_LIST_LIMIT = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _title_from_text(text: str) -> str:
    raw = (text or "").strip().replace("\n", " ")
    if not raw:
        return "新对话"
    return (raw[:24] + "…") if len(raw) > 24 else raw


def conversation_dict(c: AgentConversation, *, preview: str = "") -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title or "新对话",
        "status": c.status or "open",
        "preview": preview,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "closed_at": c.closed_at.isoformat() if c.closed_at else None,
    }


def _owned_conversation(
    db: Session, user_id: int, conversation_id: int
) -> AgentConversation | None:
    return (
        db.query(AgentConversation)
        .filter(
            AgentConversation.id == conversation_id,
            AgentConversation.user_id == user_id,
        )
        .first()
    )


def list_conversations(db: Session, user_id: int, *, limit: int = CONV_LIST_LIMIT) -> list[dict[str, Any]]:
    lim = max(1, min(int(limit or CONV_LIST_LIMIT), 100))
    rows = (
        db.query(AgentConversation)
        .filter(AgentConversation.user_id == user_id)
        .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
        .limit(lim)
        .all()
    )
    out: list[dict[str, Any]] = []
    for c in rows:
        last = (
            db.query(AgentMessage)
            .filter(
                AgentMessage.conversation_id == c.id,
                AgentMessage.user_id == user_id,
            )
            .order_by(AgentMessage.id.desc())
            .first()
        )
        preview = ""
        if last and last.content:
            preview = (last.content or "").strip().replace("\n", " ")[:48]
        out.append(conversation_dict(c, preview=preview))
    return out


def get_or_create_active(db: Session, user_id: int) -> AgentConversation:
    row = (
        db.query(AgentConversation)
        .filter(
            AgentConversation.user_id == user_id,
            AgentConversation.status == "open",
        )
        .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
        .first()
    )
    if row:
        return row
    row = AgentConversation(
        user_id=user_id,
        title="新对话",
        status="open",
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def create_conversation(
    db: Session,
    user_id: int,
    *,
    close_current: bool = True,
) -> AgentConversation:
    if close_current:
        opens = (
            db.query(AgentConversation)
            .filter(
                AgentConversation.user_id == user_id,
                AgentConversation.status == "open",
            )
            .all()
        )
        now = _now()
        for c in opens:
            c.status = "closed"
            c.closed_at = now
            c.updated_at = now
    row = AgentConversation(
        user_id=user_id,
        title="新对话",
        status="open",
        updated_at=_now(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def close_conversation(db: Session, user_id: int, conversation_id: int) -> AgentConversation | None:
    row = _owned_conversation(db, user_id, conversation_id)
    if row is None:
        return None
    if row.status != "closed":
        row.status = "closed"
        row.closed_at = _now()
        row.updated_at = _now()
        db.commit()
        db.refresh(row)
    return row


def delete_conversation(db: Session, user_id: int, conversation_id: int) -> bool:
    row = _owned_conversation(db, user_id, conversation_id)
    if row is None:
        return False
    db.query(AgentMessage).filter(
        AgentMessage.user_id == user_id,
        AgentMessage.conversation_id == conversation_id,
    ).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return True


def resolve_conversation(
    db: Session,
    user_id: int,
    conversation_id: int | None,
) -> AgentConversation:
    if conversation_id:
        row = _owned_conversation(db, user_id, conversation_id)
        if row is not None:
            return row
    return get_or_create_active(db, user_id)


def list_messages(
    db: Session,
    user_id: int,
    conversation_id: int | None = None,
    *,
    limit: int = LOAD_LIMIT,
) -> list[dict[str, Any]]:
    conv = resolve_conversation(db, user_id, conversation_id)
    lim = max(1, min(int(limit or LOAD_LIMIT), STORE_LIMIT))
    rows = (
        db.query(AgentMessage)
        .filter(
            AgentMessage.user_id == user_id,
            AgentMessage.conversation_id == conv.id,
        )
        .order_by(AgentMessage.id.desc())
        .limit(lim)
        .all()
    )
    rows.reverse()
    return [
        {
            "id": f"m-{r.id}",
            "role": r.role,
            "content": r.content or "",
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
        if r.role in {"user", "assistant"} and (r.content or "").strip()
    ]


def append_turn(
    db: Session,
    user_id: int,
    *,
    user_content: str,
    assistant_content: str,
    conversation_id: int | None = None,
) -> None:
    u = (user_content or "").strip()
    a = (assistant_content or "").strip()
    if not u:
        return
    conv = append_user_message(db, user_id, u, conversation_id=conversation_id)
    if a and conv is not None:
        append_assistant_message(db, user_id, a, conversation_id=conv.id)


def append_user_message(
    db: Session,
    user_id: int,
    user_content: str,
    *,
    conversation_id: int | None = None,
) -> AgentConversation | None:
    """Persist user turn immediately (before / during stream)."""
    u = (user_content or "").strip()
    if not u:
        return None
    conv = resolve_conversation(db, user_id, conversation_id)
    if conv.status == "closed":
        conv = create_conversation(db, user_id, close_current=False)
    if (conv.title or "新对话") == "新对话":
        conv.title = _title_from_text(u)
    conv.updated_at = _now()
    db.add(
        AgentMessage(
            user_id=user_id,
            conversation_id=conv.id,
            role="user",
            content=u[:8000],
        )
    )
    db.commit()
    _prune(db, user_id, conv.id)
    return conv


def append_assistant_message(
    db: Session,
    user_id: int,
    assistant_content: str,
    *,
    conversation_id: int | None = None,
) -> None:
    """Persist assistant reply (full, partial, or error text) after stream ends."""
    a = (assistant_content or "").strip()
    if not a:
        return
    conv = resolve_conversation(db, user_id, conversation_id)
    if conv.status == "closed":
        return
    conv.updated_at = _now()
    db.add(
        AgentMessage(
            user_id=user_id,
            conversation_id=conv.id,
            role="assistant",
            content=a[:16000],
        )
    )
    db.commit()
    _prune(db, user_id, conv.id)


def clear_messages(db: Session, user_id: int, conversation_id: int | None = None) -> int:
    """Clear one conversation's messages, or wipe all history when conversation_id is None."""
    if conversation_id is not None:
        n = (
            db.query(AgentMessage)
            .filter(
                AgentMessage.user_id == user_id,
                AgentMessage.conversation_id == conversation_id,
            )
            .delete(synchronize_session=False)
        )
        conv = _owned_conversation(db, user_id, conversation_id)
        if conv:
            conv.title = "新对话"
            conv.updated_at = _now()
            conv.memory_summary = None
            conv.memory_until_message_id = None
        db.commit()
        return int(n or 0)

    n = db.query(AgentMessage).filter(AgentMessage.user_id == user_id).delete()
    db.query(AgentConversation).filter(AgentConversation.user_id == user_id).delete()
    db.commit()
    return int(n or 0)


def _prune(db: Session, user_id: int, conversation_id: int) -> None:
    excess = (
        db.query(AgentMessage.id)
        .filter(
            AgentMessage.user_id == user_id,
            AgentMessage.conversation_id == conversation_id,
        )
        .order_by(AgentMessage.id.desc())
        .offset(STORE_LIMIT)
        .all()
    )
    ids = [row[0] for row in excess]
    if not ids:
        return
    db.query(AgentMessage).filter(AgentMessage.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
