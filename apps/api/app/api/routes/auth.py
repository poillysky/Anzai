"""Auth API: status / bootstrap / register / login / me."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, require_user
from app.core.security import create_access_token
from app.database import get_db
from app.schemas import (
    AuthBootstrapIn,
    AuthLoginIn,
    AuthRegisterIn,
    AuthStatusOut,
    AuthTokenOut,
    UserOut,
)
from app.services import users as users_svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_out(user) -> AuthTokenOut:
    token = create_access_token(user_id=user.id, role=user.role, username=user.username)
    return AuthTokenOut(access_token=token, user=UserOut.model_validate(user))


@router.get("/status", response_model=AuthStatusOut)
def auth_status(db: Session = Depends(get_db)) -> AuthStatusOut:
    return AuthStatusOut(has_users=users_svc.user_count(db) > 0)


@router.post("/bootstrap", response_model=AuthTokenOut, status_code=status.HTTP_201_CREATED)
def bootstrap(payload: AuthBootstrapIn, db: Session = Depends(get_db)) -> AuthTokenOut:
    if users_svc.user_count(db) > 0:
        raise HTTPException(status_code=400, detail="已有账号，请直接登录或注册")
    try:
        user = users_svc.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role="admin",
            claim_orphans=True,
            identity_role=payload.identity_role,
            identity_label=payload.identity_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _token_out(user)


@router.post("/register", response_model=AuthTokenOut, status_code=status.HTTP_201_CREATED)
def register(payload: AuthRegisterIn, db: Session = Depends(get_db)) -> AuthTokenOut:
    """Self-serve signup as normal user. First account must use /bootstrap."""
    if users_svc.user_count(db) == 0:
        raise HTTPException(status_code=400, detail="请先创建首位管理员账号")
    try:
        user = users_svc.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role="user",
            identity_role=payload.identity_role,
            identity_label=payload.identity_label,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _token_out(user)


@router.post("/login", response_model=AuthTokenOut)
def login(payload: AuthLoginIn, db: Session = Depends(get_db)) -> AuthTokenOut:
    try:
        user = users_svc.authenticate(db, payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _token_out(user)


@router.get("/me", response_model=UserOut)
def me(user: AuthUser = Depends(require_user), db: Session = Depends(get_db)) -> UserOut:
    row = users_svc.get_by_username(db, user.username)
    if row is None:
        raise HTTPException(status_code=401, detail="账号不可用")
    return UserOut.model_validate(row)


@router.post("/logout")
def logout(_: AuthUser = Depends(require_user)) -> dict[str, str]:
    return {"status": "ok"}
