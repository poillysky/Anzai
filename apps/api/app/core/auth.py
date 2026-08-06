"""Auth dependencies: JWT Bearer (primary)."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database import get_db
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class AuthUser:
    id: int
    username: str
    role: str


def require_user(
    creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: Session = Depends(get_db),
) -> AuthUser:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(creds.credentials)
        user_id = int(payload.get("sub") or 0)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    row = db.get(User, user_id)
    if row is None or not row.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号不可用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthUser(id=row.id, username=row.username, role=row.role)


def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


# Back-compat alias used by older imports
def require_password() -> None:
    """Deprecated — use require_user. Kept so stray imports fail closed at runtime if called."""
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="请使用 Bearer Token 登录",
    )
