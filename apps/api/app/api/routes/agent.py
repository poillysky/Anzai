"""安崽 Agent chat API — multi-conversation + SSE stream."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import SessionLocal, get_db
from app.services import agent_chat as chat_svc
from app.services import agent_history as history_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class ChatMessageIn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessageIn] = Field(default_factory=list)
    conversation_id: int | None = None


class NewConversationBody(BaseModel):
    close_current: bool = True


@router.get("/session")
def agent_session(
    conversation_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    return chat_svc.session_payload(db, user.id, conversation_id=conversation_id)


@router.get("/conversations")
def list_conversations(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    return {"items": history_svc.list_conversations(db, user.id)}


@router.post("/conversations")
def create_conversation(
    body: NewConversationBody | None = None,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    close_current = True if body is None else bool(body.close_current)
    conv = history_svc.create_conversation(db, user.id, close_current=close_current)
    return {"conversation": history_svc.conversation_dict(conv), "status": "ok"}


@router.post("/conversations/{conversation_id}/close")
def close_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    conv = history_svc.close_conversation(db, user.id, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="对话不存在")
    # 关闭当前后保证还有一个可聊的 open 会话
    active = history_svc.get_or_create_active(db, user.id)
    return {
        "status": "ok",
        "conversation": history_svc.conversation_dict(conv),
        "active": history_svc.conversation_dict(active),
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    ok = history_svc.delete_conversation(db, user.id, conversation_id)
    if not ok:
        raise HTTPException(status_code=404, detail="对话不存在")
    active = history_svc.get_or_create_active(db, user.id)
    return {"status": "ok", "active": history_svc.conversation_dict(active)}


@router.delete("/messages")
def clear_agent_messages(
    conversation_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, Any]:
    n = history_svc.clear_messages(db, user.id, conversation_id)
    return {"status": "ok", "deleted": n}


@router.post("/chat")
async def agent_chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> StreamingResponse:
    msgs = [{"role": m.role, "content": m.content} for m in payload.messages]
    user_id = user.id
    conv = history_svc.resolve_conversation(db, user_id, payload.conversation_id)
    # 已关闭的会话不允许继续写，自动落到新开对话
    if (conv.status or "") == "closed":
        conv = history_svc.create_conversation(db, user_id, close_current=False)
    conversation_id = conv.id
    gen, openai_msgs, meta = chat_svc.prepare_chat(db, user_id, msgs)
    meta = {**meta, "conversation_id": conversation_id}

    latest_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user" and (m.get("content") or "").strip():
            latest_user = (m.get("content") or "").strip()
            break

    # 提问立刻落库：换页 / abort 时至少保留用户句，避免整轮消失
    if latest_user:
        try:
            history_svc.append_user_message(
                db, user_id, latest_user, conversation_id=conversation_id
            )
        except Exception:
            logger.exception("failed to persist user message for user %s", user_id)

    async def event_gen():
        assistant_parts: list[str] = []
        had_error = False
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "meta",
                    "model": meta.get("model"),
                    "preset_id": meta.get("preset_id"),
                    "preset_name": meta.get("preset_name"),
                    "profile_name": meta.get("profile_name"),
                    "tools": meta.get("tools") or [],
                    "conversation_id": conversation_id,
                    "scene": meta.get("scene"),
                },
                ensure_ascii=False,
            )
            + "\n\n"
        )
        try:
            async for piece in chat_svc.stream_chat_completion(
                gen,
                openai_msgs,
                db=db,
                user_id=user_id,
            ):
                try:
                    ev = json.loads(piece)
                except json.JSONDecodeError:
                    ev = {}
                if ev.get("type") == "token" and ev.get("text"):
                    assistant_parts.append(str(ev["text"]))
                elif ev.get("type") == "error":
                    had_error = True
                    if not assistant_parts and ev.get("message"):
                        assistant_parts.append(f"（出错）{ev.get('message')}")
                yield f"data: {piece}\n\n"
        finally:
            reply = "".join(assistant_parts).strip()
            # 有正文或错误文案都落库；中断且零字则仅保留已写入的用户句
            if latest_user and (reply or had_error):
                try:
                    with SessionLocal() as persist_db:
                        history_svc.append_assistant_message(
                            persist_db,
                            user_id,
                            reply or "（已中断）",
                            conversation_id=conversation_id,
                        )
                except Exception:
                    logger.exception("failed to persist assistant reply for user %s", user_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
