"""Delete high-confidence redundant knowledge cards (MD + Postgres) after domain seeds landed."""

from __future__ import annotations

from pathlib import Path

from app.core.config import reload_settings
from app.services import knowledge as k
from app.services import knowledge_pg as pg

DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge"

# Older generics / cross-domain twins superseded by gold/stock/fund 专题 (2026-08-07)
DELETE_IDS: list[str] = [
    # market mechanics
    "term-t-plus-one",
    "mkt-trading-hours",
    "mkt-lot-size-ashare",
    "term-auction",
    "term-limit-up-down",
    "term-boards-ashare",
    "mkt-settlement-funds",
    # leverage
    "term-margin-short",
    "no-leverage-default",
    "mkt-no-borrowed-money",
    "compliance-illegal-margin",
    # trading practice
    "mkt-trading-fees",
    "mkt-overtrading",
    "mkt-limit-vs-market-order",
    "mkt-averaging-down-caution",
    # valuation / dividend / index / ETF
    "valuation-pe-simple",
    "term-pb-simple",
    "term-ex-rights",
    "dividend-mindset",
    "mkt-dividend-reinvest",
    "etf-vs-stock",
    "kb-openend-vs-etf",
    "mkt-etf-tracking-error",
    "term-etf-premium",
    "mkt-qdii-etf-risk",
    "mkt-max-drawdown",
    "mkt-money-fund-vs-stock",
    "mkt-index-what-is",
    # ST / IPO / disclaimer pile-up
    "compliance-st-risk",
    "compliance-ipo-lottery",
    "compliance-no-guaranteed-return",
    "compliance-risk-disclosure",
    # macro / gold loose
    "mkt-real-return",
    "precious-metals-run",
    "dca-vs-lump",
    # wrong-domain twin (基金定投挂在股票前缀)
    "stock-qa-dca-safe",
    # within-domain thin clone (covered by fund-buy-ac-logic + fund-qa-ac)
    "fund-ac-class-myth",
]


def main() -> None:
    reload_settings()
    removed_md = 0
    removed_db = 0
    missing: list[str] = []
    for cid in DELETE_IDS:
        path = DIR / f"{cid}.md"
        if path.is_file():
            path.unlink()
            removed_md += 1
            print("rm md", cid)
        else:
            missing.append(cid)
        if pg.knowledge_db_configured():
            if k.delete_card(cid):
                removed_db += 1
                print("rm db", cid)

    # re-upsert touched keepers
    result = k.sync_markdown_to_postgres()
    print("sync", result)
    print(
        f"done md_removed={removed_md} db_removed={removed_db} "
        f"already_absent={len(missing)} delete_list={len(DELETE_IDS)}"
    )
    if missing:
        print("absent md:", ", ".join(missing))

    # smoke: old ids should not top-hit
    for q in ("T+1是什么", "融资融券", "场外基金和场内ETF", "定投是不是不会亏"):
        hits = k.search_cards(q, limit=3)
        print("Q:", q)
        for card, score, ch in hits:
            print(f"  {score:.3f} [{ch}] {card.id} · {card.title}")


if __name__ == "__main__":
    main()
