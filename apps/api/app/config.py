"""DEPRECATED shim — do not add logic. Use app.core.config."""

from app.core.config import Settings, get_settings

__all__ = ["Settings", "get_settings"]
