"""Deterministic rebalance draft from warehouse weights + day_pnl (no LLM)."""

from __future__ import annotations

from typing import Any


def draft_rebalance_from_rows(
    holdings: list[dict[str, Any]],
    *,
    day_pnl_pct: float | None = None,
) -> dict[str, Any]:
    """Structured 调仓草案 — 倾向性，非下单。

    Each holding may include: symbol, name, weight, day_pnl, day_pnl_pct, pnl_pct.
    """
    holds = [h for h in holdings if h]
    if not holds:
        return {
            "kind": "rebalance",
            "empty": True,
            "stance": "仓库空仓，没什么可调",
            "day_pnl_pct": None,
            "head": None,
            "notes": [],
        }

    ranked = sorted(holds, key=lambda h: float(h.get("weight") or 0), reverse=True)
    notes: list[str] = []
    head = ranked[0]
    hw = float(head.get("weight") or 0)
    head_name = str(head.get("name") or head.get("symbol") or "").strip() or "头仓"
    stance = "观望为主"
    if hw >= 35:
        stance = "宜减不宜加"
        notes.append(f"{head_name} 约 {hw:.1f}% 偏重")
    elif hw >= 25:
        stance = "观望，冲高再议轻减"
        notes.append(f"{head_name} 约 {hw:.1f}% 偏集中")

    top3 = sum(float(h.get("weight") or 0) for h in ranked[:3])
    if top3 >= 70:
        notes.append(f"前三合计约 {top3:.0f}%，分散偏弱")

    by_day = sorted(
        holds,
        key=lambda h: float(h["day_pnl"] if h.get("day_pnl") is not None else 0),
    )
    drag = by_day[0]
    if drag.get("day_pnl") is not None and float(drag["day_pnl"]) < 0:
        dn = drag.get("day_pnl_pct")
        dn_s = f"{float(dn):+.2f}%" if isinstance(dn, (int, float)) else "—"
        notes.append(
            f"今日拖累 {drag.get('name') or drag.get('symbol')} day_pnl {dn_s}"
        )

    near = [
        h
        for h in ranked
        if h.get("pnl_pct") is not None and abs(float(h["pnl_pct"])) < 2
    ]
    if near:
        n0 = near[0]
        notes.append(f"{n0.get('name') or n0.get('symbol')} 贴近成本，加减都别急")

    return {
        "kind": "rebalance",
        "empty": False,
        "stance": stance,
        "day_pnl_pct": (
            round(float(day_pnl_pct), 2) if isinstance(day_pnl_pct, (int, float)) else None
        ),
        "head": {
            "symbol": str(head.get("symbol") or ""),
            "name": head_name,
            "weight": round(hw, 1),
        },
        "notes": notes[:4],
    }


def draft_rebalance_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Attach to portfolio analysis report; None when not a warehouse job."""
    if str(snapshot.get("scope") or "") != "portfolio":
        return None
    quotes = list(snapshot.get("quotes") or [])
    holds = [q for q in quotes if q.get("in_portfolio") or q.get("weight") is not None]
    if not holds:
        holds = quotes
    port = snapshot.get("portfolio") if isinstance(snapshot.get("portfolio"), dict) else {}
    day_pct = port.get("day_pnl_pct")
    return draft_rebalance_from_rows(holds, day_pnl_pct=day_pct)
