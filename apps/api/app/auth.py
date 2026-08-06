"""DEPRECATED shim — do not add logic. Use app.core.auth."""

from app.core.auth import AuthUser, require_admin, require_user

__all__ = ["AuthUser", "require_admin", "require_user"]
