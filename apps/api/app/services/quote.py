"""DEPRECATED shim — do not add logic. Use app.providers.quote."""

from app.providers.quote import (
    Quote,
    get_quote,
    get_quotes,
    normalize_symbol,
    provider_name,
)

__all__ = [
    "Quote",
    "get_quote",
    "get_quotes",
    "normalize_symbol",
    "provider_name",
]
