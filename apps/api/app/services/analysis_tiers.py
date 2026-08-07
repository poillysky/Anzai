"""Analysis tiers (light / standard / deep) — editable via /admin.

App：个股三档可选；仓库巡检固定 standard（见 analysis.create job）。
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
    "trend": "走势席",
    "news": "新闻席",
    "flow": "资金情绪",
    "risk": "结构风险",
    "dialectic": "辩证席",
    "judge": "首席综合",
}

# Admin UI + blurb migration helpers
SEAT_META: dict[str, dict[str, str | bool]] = {
    "trend": {"kind": "LLM", "deferred": False},
    "news": {"kind": "LLM", "deferred": False},
    "flow": {"kind": "未接入", "deferred": True},
    "risk": {"kind": "确定性", "deferred": False},
}

TIER_META: dict[str, dict[str, str]] = {
    "light": {
        "pipeline": "走势∥新闻 → 首席",
        "note": "跳过辩证与结构风险。适合单票快看。",
        "evidence_hint": "证据偏薄：报价+指数+少量新闻；可不拉分时。",
    },
    "standard": {
        "pipeline": "走势∥新闻 → 结构风险 → 辩证×1 → 首席",
        "note": "仓库持仓巡检固定本档。结构风险看集中度 / 今日盈亏相对上证 / 空新闻。",
        "evidence_hint": "标准证据：分时（昨收·今开·最新）+ 日K + 持仓今日盈亏分列。",
    },
    "deep": {
        "pipeline": "走势∥新闻 → 结构风险 → 辩证×2 → 首席",
        "note": "证据加厚、辩证两回合；open_questions 必须由首席回应。",
        "evidence_hint": "深度证据：更长K线、更多新闻；仍禁止编造未纳入宏观。",
    },
}

EVIDENCE_BLURBS: dict[str, str] = {
    "light": "轻量证据",
    "standard": "分时+K+新闻",
    "deep": "加厚K/新闻",
}

_LEGACY_BLURBS: dict[str, frozenset[str]] = {
    "standard": frozenset(
        {
            "走势∥新闻 → 辩证 → 首席",
            "走势 || 新闻 -> 辩证 -> 首席",
            "走势∥新闻 → 辩证 → 首席",
        }
    ),
    "deep": frozenset(
        {
            "四席委员会 · 辩证多回合 · 证据加厚",
        }
    ),
    "light": frozenset(),
}

TIER_IDS = ("light", "standard", "deep")

DEFAULT_TIERS: dict[str, dict[str, Any]] = {
    "light": {
        "id": "light",
        "label": "轻量",
        "blurb": "走势∥新闻 → 首席（跳过辩证）",
        "agents": ["trend", "news"],
        "weights": {"trend": 0.5, "news": 0.5},
        "evidence_tier": "light",
    },
    "standard": {
        "id": "standard",
        "label": "标准",
        "blurb": "走势∥新闻 → 结构风险 → 辩证 → 首席",
        "agents": ["trend", "news", "risk"],
        "weights": {"trend": 0.4, "news": 0.35, "risk": 0.25},
        "evidence_tier": "standard",
    },
    "deep": {
        "id": "deep",
        "label": "深度",
        "blurb": "走势∥新闻 → 结构风险 → 辩证×2 → 首席",
        "agents": ["trend", "news", "risk"],
        "weights": {"trend": 0.4, "news": 0.3, "risk": 0.3},
        "evidence_tier": "deep",
    },
}

# apps/api/data/analysis_tiers.json
TIERS_PATH = Path(__file__).resolve().parents[2] / "data" / "analysis_tiers.json"


def normalize_degree(degree: str | None) -> str:
    if degree in TIER_IDS:
        return str(degree)
    return "standard"


def _normalize_tier(tid: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    base = deepcopy(DEFAULT_TIERS[tid])
    if not raw:
        return base
    label = str(raw.get("label") or base["label"]).strip() or base["label"]
    blurb = str(raw.get("blurb") or base["blurb"]).strip() or base["blurb"]
    # Migrate stale App blurbs saved before 结构风险席
    legacy = _LEGACY_BLURBS.get(tid, frozenset())
    if blurb in legacy or (
        tid in {"standard", "deep"}
        and "结构风险" not in blurb
        and ("辩证" in blurb or "四席" in blurb)
    ):
        blurb = base["blurb"]
    evidence = str(raw.get("evidence_tier") or base["evidence_tier"]).strip()
    if evidence not in TIER_IDS:
        evidence = base["evidence_tier"]

    agents_in = raw.get("agents")
    agents: list[str] = []
    if isinstance(agents_in, list):
        for a in agents_in:
            aid = str(a).strip()
            # 资金情绪未接入，忽略历史勾选
            if aid == "flow":
                continue
            if aid in AGENT_IDS and aid not in agents:
                agents.append(aid)
    if not agents:
        agents = list(base["agents"])
    # 标准/深度若仍是旧的 trend+news，自动补上结构风险
    if tid in {"standard", "deep"} and agents == ["trend", "news"]:
        agents = list(base["agents"])

    weights_in = raw.get("weights") if isinstance(raw.get("weights"), dict) else {}
    weights: dict[str, float] = {}
    for a in agents:
        try:
            w = float(weights_in.get(a, base["weights"].get(a, 1.0 / len(agents))))
        except (TypeError, ValueError):
            w = float(base["weights"].get(a, 1.0 / len(agents)))
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
    tid = normalize_degree(degree)
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
    if degree:
        return str(get_tier(normalize_degree(degree))["evidence_tier"])
    try:
        return str(get_recipe(recipe_id).get("evidence_tier") or "standard")
    except KeyError:
        return "standard"


def parse_tier_form(form: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Build tiers dict from admin form fields."""
    out: dict[str, dict[str, Any]] = {}
    for tid in TIER_IDS:
        agents = [
            a
            for a in AGENT_IDS
            if a != "flow" and form.get(f"{tid}_agent_{a}") in ("1", "on", "true")
        ]
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
