"""App core: settings & auth. Prefer `from app.core.config` / `from app.core.auth`."""

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
