"""Per-user WeChat digest API (settings live under each account)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.providers.notify import send_wechat_notify
from app.services.notify_digest import run_portfolio_digest
from app.services.notify_settings import (
    CHANNELS,
    DEGREES,
    get_notify_cfg,
    public_notify,
    set_notify_cfg,
)

router = APIRouter(prefix="/notify", tags=["notify"], dependencies=[Depends(require_user)])


class NotifySettingsOut(BaseModel):
    enabled: bool = False
    channel: str = "serverchan"
    token_set: bool = False
    token_preview: str = ""
    wxpusher_uid: str = ""
    hour: int = 15
    minute: int = 10
    weekdays: str = "0,1,2,3,4"
    degree: str = "light"
    configured: bool = False
    channels: list[dict[str, str]] = Field(default_factory=list)
    degrees: list[dict[str, str]] = Field(default_factory=list)


class NotifySettingsIn(BaseModel):
    enabled: bool | None = None
    channel: str | None = Field(default=None, max_length=32)
    token: str | None = Field(default=None, max_length=256)
    wxpusher_uid: str | None = Field(default=None, max_length=64)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    weekdays: str | None = Field(default=None, max_length=32)
    degree: str | None = Field(default=None, max_length=16)


class NotifyRunOut(BaseModel):
    ok: bool
    skipped: bool = False
    reason: str = ""
    channel: str = ""
    detail: str = ""
    job_id: int | None = None
    title: str = ""
    content: str = ""
    content_preview: str = ""
    dry_run: bool = False


def _catalog() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    channels = [
        {"id": "serverchan", "label": "Server酱"},
        {"id": "pushplus", "label": "PushPlus"},
        {"id": "wxpusher", "label": "WxPusher"},
    ]
    degrees = [
        {"id": "light", "label": "轻量"},
        {"id": "standard", "label": "标准"},
        {"id": "deep", "label": "深度"},
    ]
    return channels, degrees


@router.get("/settings", response_model=NotifySettingsOut)
def get_settings(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NotifySettingsOut:
    cfg = get_notify_cfg(db, user.id)
    pub = public_notify(cfg)
    channels, degrees = _catalog()
    return NotifySettingsOut(**pub, channels=channels, degrees=degrees)


@router.put("/settings", response_model=NotifySettingsOut)
def put_settings(
    payload: NotifySettingsIn,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NotifySettingsOut:
    patch = payload.model_dump(exclude_unset=True)
    if "channel" in patch and patch["channel"]:
        ch = str(patch["channel"]).strip().lower()
        if ch not in CHANNELS:
            raise HTTPException(status_code=400, detail="通道无效")
        patch["channel"] = ch
    if "degree" in patch and patch["degree"]:
        deg = str(patch["degree"]).strip().lower()
        if deg not in DEGREES:
            raise HTTPException(status_code=400, detail="分析档位无效")
        patch["degree"] = deg
    cfg = set_notify_cfg(db, user.id, patch)
    pub = public_notify(cfg)
    channels, degrees = _catalog()
    return NotifySettingsOut(**pub, channels=channels, degrees=degrees)


@router.post("/test", response_model=NotifyRunOut)
def notify_test(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NotifyRunOut:
    cfg = get_notify_cfg(db, user.id)
    result = send_wechat_notify(
        channel=str(cfg.get("channel") or ""),
        token=str(cfg.get("token") or ""),
        title="安崽通知测试",
        content="通道已接通。之后会按你设定的时间推送仓库日报。\n\n仅供参考，不构成投资建议。",
        wxpusher_uid=str(cfg.get("wxpusher_uid") or ""),
    )
    return NotifyRunOut(
        ok=result.ok,
        channel=result.channel,
        detail=result.detail,
        reason="" if result.ok else result.detail,
    )


@router.post("/run-digest", response_model=NotifyRunOut)
def notify_run_digest(
    force: bool = Query(default=False),
    dry_run: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> NotifyRunOut:
    out = run_portfolio_digest(db, user.id, force=force or dry_run, dry_run=dry_run)
    return NotifyRunOut(
        ok=bool(out.get("ok")),
        skipped=bool(out.get("skipped")),
        reason=str(out.get("reason") or ""),
        channel=str(out.get("channel") or ""),
        detail=str(out.get("detail") or ""),
        job_id=out.get("job_id"),
        title=str(out.get("title") or ""),
        content=str(out.get("content") or ""),
        content_preview=str(out.get("content_preview") or ""),
        dry_run=bool(out.get("dry_run")),
    )
