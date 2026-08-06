"""Three analysis tiers (light / standard / deep) — editable via /admin.

App UI only picks a tier; agent mix & weights live here.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

AGENT_IDS = ("trend", "news", "flow", "risk")
AGENT_LABELS: dict[str, str] = {
    "trend": "走势",
    "news": "新闻",
    "flow": "资金情绪",
    "risk": "结构风险",
    "judge": "首席综合",
}

TIER_IDS = ("light", "standard", "deep")

DEFAULT_TIERS: dict[str, dict[str, Any]] = {
    "light": {
        "id": "light",
        "label": "轻量",
        "blurb": "走势 + 新闻（双席快评）",
        "agents": ["trend", "news"],
        "weights": {"trend": 0.5, "news": 0.5},
        "evidence_tier": "light",
    },
    "standard": {
        "id": "standard",
        "label": "标准",
        "blurb": "四专家均衡",
        "agents": ["trend", "news", "flow", "risk"],
        "weights": {"trend": 0.25, "news": 0.25, "flow": 0.25, "risk": 0.25},
        "evidence_tier": "standard",
    },
    "deep": {
        "id": "deep",
        "label": "深度",
        "blurb": "四席全开 · 新闻与风险加重",
        "agents": ["trend", "news", "flow", "risk"],
        "weights": {"trend": 0.20, "news": 0.30, "flow": 0.15, "risk": 0.35},
        "evidence_tier": "deep",
    },
}

# apps/api/data/analysis_tiers.json
TIERS_PATH = Path(__file__).resolve().parents[2] / "data" / "analysis_tiers.json"


def _normalize_tier(tid: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_TIERS[tid])
    if not raw:
        return base
    label = str(raw.get("label") or base["label"]).strip() or base["label"]
    blurb = str(raw.get("blurb") or base["blurb"]).strip() or base["blurb"]
    evidence = str(raw.get("evidence_tier") or base["evidence_tier"]).strip()
    if evidence not in {"light", "standard", "deep"}:
        evidence = base["evidence_tier"]

    agents_in = raw.get("agents")
    agents: list[str] = []
    if isinstance(agents_in, list):
        for a in agents_in:
            aid = str(a).strip()
            if aid in AGENT_IDS and aid not in agents:
                agents.append(aid)
    if not agents:
        agents = list(base["agents"])

    weights_in = raw.get("weights") if isinstance(raw.get("weights"), dict) else {}
    weights: dict[str, float] = {}
    for a in agents:
        try:
            w = float(weights_in.get(a, 1.0 / len(agents)))
        except (TypeError, ValueError):
            w = 1.0 / len(agents)
        weights[a] = max(0.0, w)
    total = sum(weights.values()) or 1.0
    weights = {k: round(v / total, 4) for k, v in weights.items()}

    return {
        "id": tid,
        "label": label,
        "blurb": blurb,
        "agents": agents,
        "weights": weights,
        "evidence_tier": evidence,
    }


def load_tiers() -> dict[str, dict[str, Any]]:
    data: dict[str, Any] = {}
    if TIERS_PATH.exists():
        try:
            raw = json.loads(TIERS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:
            logger.exception("failed to read %s; using defaults", TIERS_PATH)
    return {tid: _normalize_tier(tid, data.get(tid)) for tid in TIER_IDS}


def save_tiers(tiers: dict[str, dict[str, Any]]) -> Path:
    out = {tid: _normalize_tier(tid, tiers.get(tid)) for tid in TIER_IDS}
    TIERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TIERS_PATH.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return TIERS_PATH


def get_tier(degree: str) -> dict[str, Any]:
    tid = degree if degree in TIER_IDS else "standard"
    return load_tiers()[tid]


def list_degrees() -> list[dict[str, Any]]:
    tiers = load_tiers()
    return [
        {
            "id": tid,
            "label": t["label"],
            "default_recipe": tid,
            "evidence_tier": t["evidence_tier"],
            "blurb": t["blurb"],
            "agents": list(t["agents"]),
            "weights": dict(t["weights"]),
            "agent_labels": {a: AGENT_LABELS.get(a, a) for a in t["agents"]},
        }
        for tid, t in tiers.items()
    ]


def recipe_for_degree(degree: str) -> dict[str, Any]:
    """Recipe-shaped dict for the orchestrator (id = tier id)."""
    t = get_tier(degree)
    return {
        "id": t["id"],
        "label": t["label"],
        "agents": list(t["agents"]),
        "weights": dict(t["weights"]),
        "evidence_tier": t["evidence_tier"],
        "modes": [],
    }


def get_recipe(recipe_id: str) -> dict[str, Any]:
    """Resolve recipe: tier id first, else legacy aliases."""
    if recipe_id in TIER_IDS:
        return recipe_for_degree(recipe_id)
    # legacy aliases from earlier P0
    legacy = {
        "quick_two": "light",
        "balanced": "standard",
        "deep_full": "deep",
        "trend_heavy": "standard",
        "news_heavy": "deep",
        "risk_heavy": "deep",
    }
    if recipe_id in legacy:
        return recipe_for_degree(legacy[recipe_id])
    raise KeyError(recipe_id)


def resolve_evidence_tier(degree: str | None, recipe_id: str) -> str:
    if degree in TIER_IDS:
        return str(get_tier(degree)["evidence_tier"])
    try:
        return str(get_recipe(recipe_id).get("evidence_tier") or "standard")
    except KeyError:
        return "standard"


def parse_tier_form(form: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Build tiers dict from admin form fields."""
    out: dict[str, dict[str, Any]] = {}
    for tid in TIER_IDS:
        agents = [a for a in AGENT_IDS if form.get(f"{tid}_agent_{a}") in ("1", "on", "true")]
        weights: dict[str, float] = {}
        for a in agents:
            raw = form.get(f"{tid}_w_{a}", "")
            try:
                weights[a] = float(raw)
            except (TypeError, ValueError):
                weights[a] = 1.0
        evidence = form.get(f"{tid}_evidence", tid)
        out[tid] = {
            "id": tid,
            "label": form.get(f"{tid}_label", DEFAULT_TIERS[tid]["label"]),
            "blurb": form.get(f"{tid}_blurb", DEFAULT_TIERS[tid]["blurb"]),
            "agents": agents or list(DEFAULT_TIERS[tid]["agents"]),
            "weights": weights or dict(DEFAULT_TIERS[tid]["weights"]),
            "evidence_tier": evidence if evidence in TIER_IDS else tid,
        }
    return {tid: _normalize_tier(tid, out[tid]) for tid in TIER_IDS}
