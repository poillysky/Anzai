"""Compatibility shim — tiers are the source of truth (see analysis_tiers)."""

from __future__ import annotations

from typing import Any

from app.services.analysis_tiers import (
    AGENT_LABELS,
    get_recipe,
    get_tier,
    list_degrees,
    recipe_for_degree,
    resolve_evidence_tier,
)

__all__ = [
    "AGENT_LABELS",
    "get_recipe",
    "get_tier",
    "list_degrees",
    "list_modes",
    "list_recipes",
    "recipe_for_degree",
    "resolve_evidence_tier",
]


def list_modes() -> list[dict[str, Any]]:
    """Deprecated — UI no longer uses modes; kept empty for catalog compat."""
    return []


def list_recipes(mode: str | None = None) -> list[dict[str, Any]]:
    """Expose tiers as recipes for catalog / admin preview."""
    del mode
    items = []
    for d in list_degrees():
        items.append(
            {
                "id": d["id"],
                "label": d["label"],
                "agents": list(d["agents"]),
                "weights": dict(d["weights"]),
                "evidence_tier": d["evidence_tier"],
                "modes": [],
                "agent_labels": dict(d.get("agent_labels") or {}),
            }
        )
    return items
