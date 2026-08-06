"""Per-user preferences (identity). Identity ≠ preset — only prompt injection."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import UserPreference
from app.services import identity as identity_svc


def get_or_create_preference(db: Session, user_id: int) -> UserPreference:
    rows = (
        db.query(UserPreference)
        .filter(UserPreference.user_id == user_id)
        .order_by(UserPreference.id.asc())
        .all()
    )
    if not rows:
        row = UserPreference(user_id=user_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    # Legacy DBs may have duplicate user_id rows (unique not enforced early on)
    keep = rows[0]
    for extra in rows[1:]:
        # Prefer a row that already has identity if keep is empty
        if not (keep.identity_role or "").strip() and (extra.identity_role or "").strip():
            keep.identity_role = extra.identity_role
            keep.identity_label = extra.identity_label or ""
        db.delete(extra)
    if len(rows) > 1:
        db.commit()
        db.refresh(keep)
    return keep


def get_identity(db: Session, user_id: int) -> dict:
    row = get_or_create_preference(db, user_id)
    role = getattr(row, "identity_role", "") or ""
    label = getattr(row, "identity_label", "") or ""
    return identity_svc.public_identity(role, label)


def set_identity(db: Session, user_id: int, role: str, label: str = "") -> dict:
    rid = identity_svc.normalize_role(role)
    if role and not rid:
        raise ValueError("身份无效")
    if rid == "custom":
        custom = (label or "").strip()
        if not custom:
            raise ValueError("自定义身份请填写称呼，例如「舅舅」")
        if len(custom) > 16:
            raise ValueError("自定义称呼最多 16 字")
    else:
        custom = ""

    row = get_or_create_preference(db, user_id)
    row.identity_role = rid
    row.identity_label = custom
    db.commit()
    db.refresh(row)
    return identity_svc.public_identity(rid, custom)
