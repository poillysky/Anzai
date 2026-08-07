"""Per-user WeChat digest settings (stored on preferences.notify_json)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import UserPreference
from app.providers.notify import channel_configured
from app.services.preferences import get_or_create_preference

logger = logging.getLogger(__name__)

CHANNELS = ("serverchan", "pushplus", "wxpusher")
DEGREES = ("light", "standard", "deep")

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "channel": "serverchan",
    "token": "",
    "wxpusher_uid": "",
    "hour": 15,
    "minute": 10,
    "weekdays": "0,1,2,3,4",
    "degree": "light",
}


def _parse(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_notify(data: dict[str, Any] | None, *, keep_token: str = "") -> dict[str, Any]:
    src = data or {}
    ch = str(src.get("channel") or _DEFAULTS["channel"]).strip().lower()
    if ch not in CHANNELS:
        ch = "serverchan"
    degree = str(src.get("degree") or _DEFAULTS["degree"]).strip().lower()
    if degree not in DEGREES:
        degree = "light"
    try:
        hour = int(src.get("hour", _DEFAULTS["hour"]))
    except (TypeError, ValueError):
        hour = 15
    try:
        minute = int(src.get("minute", _DEFAULTS["minute"]))
    except (TypeError, ValueError):
        minute = 10
    hour = min(23, max(0, hour))
    minute = min(59, max(0, minute))
    token = str(src.get("token") or "").strip()
    if not token:
        token = keep_token
    weekdays = str(src.get("weekdays") or _DEFAULTS["weekdays"]).strip()
    if not weekdays:
        weekdays = "0,1,2,3,4"
    enabled = bool(src.get("enabled"))
    return {
        "enabled": enabled,
        "channel": ch,
        "token": token,
        "wxpusher_uid": str(src.get("wxpusher_uid") or "").strip()[:64],
        "hour": hour,
        "minute": minute,
        "weekdays": weekdays,
        "degree": degree,
    }


def mask_token(token: str) -> str:
    t = (token or "").strip()
    if not t:
        return ""
    if len(t) <= 6:
        return "***"
    return f"{t[:3]}…{t[-4:]}"


def public_notify(cfg: dict[str, Any]) -> dict[str, Any]:
    token = str(cfg.get("token") or "")
    return {
        "enabled": bool(cfg.get("enabled")),
        "channel": cfg.get("channel") or "serverchan",
        "token_set": bool(token),
        "token_preview": mask_token(token),
        "wxpusher_uid": cfg.get("wxpusher_uid") or "",
        "hour": int(cfg.get("hour") or 15),
        "minute": int(cfg.get("minute") or 10),
        "weekdays": cfg.get("weekdays") or "0,1,2,3,4",
        "degree": cfg.get("degree") or "light",
        "configured": channel_configured(
            str(cfg.get("channel") or ""),
            token,
            wxpusher_uid=str(cfg.get("wxpusher_uid") or ""),
        ),
    }


def get_notify_cfg(db: Session, user_id: int) -> dict[str, Any]:
    row = get_or_create_preference(db, user_id)
    raw = getattr(row, "notify_json", None) or "{}"
    return normalize_notify(_parse(raw))


def set_notify_cfg(db: Session, user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    row = get_or_create_preference(db, user_id)
    current = normalize_notify(_parse(getattr(row, "notify_json", None) or "{}"))
    merged = {**current, **{k: v for k, v in patch.items() if v is not None}}
    # Empty token in patch → keep previous
    if "token" in patch and not str(patch.get("token") or "").strip():
        merged["token"] = current.get("token") or ""
    cfg = normalize_notify(merged, keep_token=current.get("token") or "")
    row.notify_json = json.dumps(cfg, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return cfg


def list_enabled_notify_users(db: Session) -> list[tuple[int, dict[str, Any]]]:
    """Users with notify enabled + channel configured."""
    rows = db.query(UserPreference).all()
    out: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        cfg = normalize_notify(_parse(getattr(row, "notify_json", None) or "{}"))
        if not cfg.get("enabled"):
            continue
        if not channel_configured(
            cfg["channel"],
            cfg["token"],
            wxpusher_uid=cfg.get("wxpusher_uid") or "",
        ):
            continue
        out.append((int(row.user_id), cfg))
    return out
