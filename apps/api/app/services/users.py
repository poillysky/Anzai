"""User account helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.database.migrate import claim_orphan_rows
from app.database.session import engine
from app.models import (
    AnalysisJob,
    AnalysisProfile,
    AgentConversation,
    AgentMessage,
    Holding,
    NewsInterest,
    User,
    UserPreference,
    WatchlistItem,
)


def user_count(db: Session) -> int:
    return db.query(User).count()


def get_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username.strip()).first()


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str = "user",
    claim_orphans: bool = False,
    identity_role: str = "",
    identity_label: str = "",
) -> User:
    name = username.strip()
    if len(name) < 2:
        raise ValueError("用户名至少 2 个字符")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    if role not in {"admin", "user"}:
        raise ValueError("角色无效")
    if get_by_username(db, name):
        raise ValueError("用户名已存在")

    row = User(
        username=name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    if claim_orphans:
        claim_orphan_rows(engine, row.id)

    # Ensure profile row
    if db.query(AnalysisProfile).filter(AnalysisProfile.user_id == row.id).first() is None:
        db.add(AnalysisProfile(user_id=row.id, degree="standard"))
        db.commit()

    # 注册/引导传入时写入；Admin 建号可后填
    rid = (identity_role or "").strip()
    if rid:
        from app.services import preferences as prefs_svc

        prefs_svc.set_identity(db, row.id, rid, identity_label or "")

    return row


def authenticate(db: Session, username: str, password: str) -> User:
    row = get_by_username(db, username)
    if row is None or not row.is_active:
        raise ValueError("用户名或密码错误")
    if not verify_password(password, row.password_hash):
        raise ValueError("用户名或密码错误")
    return row


def set_password(db: Session, user: User, password: str) -> None:
    if len(password) < 4:
        raise ValueError("密码至少 4 位")
    user.password_hash = hash_password(password)
    db.commit()


def get_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def change_password(
    db: Session,
    user_id: int,
    *,
    current_password: str,
    new_password: str,
) -> None:
    row = get_by_id(db, user_id)
    if row is None or not row.is_active:
        raise ValueError("账号不可用")
    if not verify_password(current_password, row.password_hash):
        raise ValueError("当前密码不正确")
    if current_password == new_password:
        raise ValueError("新密码不能与当前密码相同")
    set_password(db, row, new_password)


def delete_user_cascade(db: Session, user: User) -> None:
    uid = user.id
    db.query(Holding).filter(Holding.user_id == uid).delete()
    db.query(WatchlistItem).filter(WatchlistItem.user_id == uid).delete()
    db.query(NewsInterest).filter(NewsInterest.user_id == uid).delete()
    db.query(UserPreference).filter(UserPreference.user_id == uid).delete()
    db.query(AnalysisProfile).filter(AnalysisProfile.user_id == uid).delete()
    db.query(AnalysisJob).filter(AnalysisJob.user_id == uid).delete()
    db.query(AgentMessage).filter(AgentMessage.user_id == uid).delete()
    db.query(AgentConversation).filter(AgentConversation.user_id == uid).delete()
    db.delete(user)
    db.commit()


def active_admin_count(db: Session) -> int:
    return (
        db.query(User)
        .filter(User.role == "admin", User.is_active.is_(True))
        .count()
    )
