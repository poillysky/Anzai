"""Current user preferences — identity + account self-service."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.database import get_db
from app.services import identity as identity_svc
from app.services import preferences as prefs_svc
from app.services import users as users_svc

router = APIRouter(prefix="/me", tags=["me"])


class IdentityOut(BaseModel):
    role: str = ""
    label: str = ""
    call_as: str = ""
    configured: bool = False
    relation_prompt: str = ""
    roles: list[dict[str, str]] = Field(default_factory=list)


class IdentityUpdate(BaseModel):
    role: str = Field(..., max_length=32)
    label: str = Field(default="", max_length=16)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=4, max_length=128)


@router.get("/identity", response_model=IdentityOut)
def get_identity(
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> IdentityOut:
    data = prefs_svc.get_identity(db, user.id)
    return IdentityOut(**data, roles=identity_svc.list_roles())


@router.put("/identity", response_model=IdentityOut)
def put_identity(
    payload: IdentityUpdate,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> IdentityOut:
    try:
        data = prefs_svc.set_identity(db, user.id, payload.role, payload.label)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return IdentityOut(**data, roles=identity_svc.list_roles())


@router.post("/password")
def change_password(
    payload: ChangePasswordIn,
    db: Session = Depends(get_db),
    user: AuthUser = Depends(require_user),
) -> dict[str, str]:
    try:
        users_svc.change_password(
            db,
            user.id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}
