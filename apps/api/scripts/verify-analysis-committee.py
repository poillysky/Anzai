"""Regression fixtures for analysis committee P0/P1 (no LLM).

Run: PYTHONPATH=apps/api python apps/api/scripts/verify-analysis-committee.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.services.analysis_orchestra import (  # noqa: E402
    build_risk_seat_memo,
    compute_structure_facts,
    format_evidence_text,
    _assemble_report,
    _watch_evidence_ref,
)


def _snap(**over: object) -> dict:
    base = {
        "scope": "portfolio",
        "evidence_tier": "standard",
        "captured_at": "2026-08-06T11:00:00+08:00",
        "calendar": {
            "shanghai_date": "2026-08-06",
            "session_label": "交易中",
            "session_detail": "午市",
            "today_means": "标签今日=盘中相对昨收",
        },
        "portfolio": {
            "total_market_value": 100000,
            "total_pnl": -500,
            "total_pnl_pct": -0.5,
            "day_pnl": -750,
            "day_pnl_pct": -1.43,
            "holding_count": 2,
        },
        "indices": [
            {
                "symbol": "000001",
                "name": "上证",
                "price": 3000,
                "change_pct": -0.5,
                "fresh_today": True,
                "day_label": "今日",
            }
        ],
        "quotes": [
            {
                "symbol": "688825",
                "name": "长鑫科技",
                "market": "SH",
                "price": 51.8,
                "prev_close": 54.2,
                "open": 53.0,
                "change_pct": -4.43,
                "day_label": "今日",
                "fresh_today": True,
                "in_portfolio": True,
                "weight": 43.2,
                "day_pnl": -750,
                "day_pnl_pct": -1.43,
                "pnl_pct": -1.43,
                "cost": 52.55,
                "bought_at": "2026-08-06",
            },
            {
                "symbol": "510300",
                "name": "沪深300",
                "market": "SH",
                "price": 4.0,
                "prev_close": 4.01,
                "open": 4.0,
                "change_pct": -0.25,
                "day_label": "今日",
                "fresh_today": True,
                "in_portfolio": True,
                "weight": 56.8,
                "day_pnl": 10,
                "day_pnl_pct": 0.1,
                "pnl_pct": 1.0,
            },
        ],
        "news": [],
        "intraday": [
            {"symbol": "688825", "open": 53.0, "last": 51.8, "prev_close": 54.2, "point_count": 10}
        ],
    }
    base.update(over)
    return base


def main() -> None:
    snap = _snap()
    structure = compute_structure_facts(snap)
    snap["structure"] = structure

    assert structure["head_name"] == "沪深300" or structure["head_weight"] == 56.8
    assert structure["portfolio_day_pnl_pct"] == -1.43
    assert structure["vs_sh_pnl"] is not None
    assert abs(float(structure["vs_sh_pnl"]) - (-1.43 - (-0.5))) < 1e-6

    evidence = format_evidence_text(snap)
    assert "今开" in evidence
    assert "昨收" in evidence
    assert "今日盈亏" in evidence
    assert "组合今日盈亏" in evidence or "组合今日盈亏" in evidence.replace(" ", "")
    assert "行情今日" in evidence or "行情今日" in evidence

    # Stale quote must not be labeled as live today move in risk seat
    stale = _snap()
    stale["quotes"][0]["fresh_today"] = False
    stale["quotes"][0]["day_label"] = "非今日"
    stale["quotes"][0]["last_session_change_pct"] = 9.5
    stale["quotes"][0]["change_pct"] = 0.0
    stale["quotes"][1]["fresh_today"] = False
    stale["structure"] = compute_structure_facts(stale)
    risk = build_risk_seat_memo(stale)
    assert risk["id"] == "risk"
    assert risk["status"] == "done"
    assert any("仓位" in b or "非今日" in b or "新闻" in b for b in risk["bullets"])

    # Head ≥35% inject via assemble
    heavy = _snap()
    heavy["quotes"][0]["weight"] = 60.0
    heavy["quotes"][1]["weight"] = 40.0
    heavy["structure"] = compute_structure_facts(heavy)
    judge = {
        "summary": "整体观望",
        "stance": "中性",
        "confidence": 0.6,
        "bullets": [],
        "_raw": {
            "verdict": "两只都先看着",
            "stance": "中性",
            "confidence": 0.6,
            "watch": [],
            "items": [],
        },
    }
    report = _assemble_report(
        heavy,
        agents=[{"id": "trend", "status": "done"}, {"id": "news", "status": "done"}],
        debate=[{"round": 1, "open_questions": ["头部是不是太重？"]}],
        judge=judge,
        open_questions=["头部是不是太重？"],
    )
    assert any("仓位" in w for w in report["watch"]), report["watch"]
    assert report.get("unresolved") or report.get("open_resolutions") is not None
    # empty news → watch mentions 消息
    assert any("消息" in w for w in report["watch"])

    ref = _watch_evidence_ref("重点看长鑫科技：仓位偏重", heavy)
    assert "仓位" in ref or "今日盈亏" in ref

    print("verify-analysis-committee: OK")


if __name__ == "__main__":
    main()
