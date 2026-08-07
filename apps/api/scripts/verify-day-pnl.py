# scripts/verify-day-pnl.py — scenario matrix for broker cash-flow day P&L
"""Run: python scripts/verify-day-pnl.py (from apps/api with PYTHONPATH=.)"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.services.holding_day_lots import (
    day_pnl_cashflow,
    ensure_day_session,
    record_day_buy,
    record_day_sell,
)


def _h(**kw):
    base = dict(
        shares=0,
        cost=0,
        bought_at="2026-08-01",
        sod_shares=0,
        sod_asof="",
        day_buy_amount=0,
        day_sell_amount=0,
        day_buy_shares=0,
        day_buy_cost=0,
        day_buy_asof="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def check(name: str, got: float, expect: float, tol: float = 0.02) -> None:
    ok = abs(got - expect) <= tol
    print(("OK " if ok else "FAIL "), name, f"got={got} expect={expect}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    today = date(2026, 8, 6)
    with patch("app.services.holding_day_lots.shanghai_today", return_value=today):
        # A overnight
        check("A overnight", day_pnl_cashflow(
            shares=1000, price=5.5, prev_close=4.2, sod_shares=1000,
            day_buy_amount=0, day_sell_amount=0,
        )[0], 1300.0)

        # B first buy today
        check("B first buy", day_pnl_cashflow(
            shares=1000, price=4.5, prev_close=3.0, sod_shares=0,
            day_buy_amount=4000, day_sell_amount=0,
        )[0], 500.0)

        # C top-up
        check("C top-up", day_pnl_cashflow(
            shares=1500, price=5.5, prev_close=4.2, sod_shares=1000,
            day_buy_amount=2500, day_sell_amount=0,
        )[0], 1550.0)

        # E sell
        check("E sell", day_pnl_cashflow(
            shares=700, price=5.2, prev_close=4.2, sod_shares=1000,
            day_buy_amount=0, day_sell_amount=1500,
        )[0], 940.0)

        # F day trade (Longbridge case 2)
        check("F longbridge", day_pnl_cashflow(
            shares=100, price=200, prev_close=190, sod_shares=100,
            day_buy_amount=9900, day_sell_amount=10100,
        )[0], 1200.0)

        # Guard: uninitialized → vs prev_close not full MV
        pnl, _ = day_pnl_cashflow(
            shares=2000, price=69.21, prev_close=66.0, sod_shares=0,
            day_buy_amount=0, day_sell_amount=0,
        )
        check("guard not MV", pnl, 6420.0)
        assert abs(pnl - 2000 * 69.21) > 1

        # Roll-before-buy: mutate shares then record must not put buy into SOD
        h = _h(shares=1000, cost=4.0, sod_asof="2026-08-05")
        ensure_day_session(h)  # sod=1000
        assert h.sod_shares == 1000
        h.shares = 1500
        record_day_buy(h, 500, 5.0, "2026-08-06")
        check("roll-order sod stays", h.sod_shares, 1000.0)
        check("roll-order buy amt", h.day_buy_amount, 2500.0)
        check(
            "roll-order pnl",
            day_pnl_cashflow(
                shares=h.shares, price=5.5, prev_close=4.2,
                sod_shares=h.sod_shares,
                day_buy_amount=h.day_buy_amount,
                day_sell_amount=h.day_sell_amount,
            )[0],
            1550.0,
        )

        # Last-resort buy after shares already bumped without ensure
        h2 = _h(shares=1500, cost=4.33, sod_asof="")  # not rolled
        record_day_buy(h2, 500, 5.0, "2026-08-06")
        check("last-resort sod", h2.sod_shares, 1000.0)

        # Sell last-resort after shares reduced
        h3 = _h(shares=700, cost=4.0, sod_asof="")
        record_day_sell(h3, 300, 5.0)
        check("sell last-resort sod", h3.sod_shares, 1000.0)
        check("sell last-resort amt", h3.day_sell_amount, 1500.0)

        # Heal: day_buy_asof set, sod empty
        h4 = _h(shares=2000, cost=69.49, bought_at="2026-08-01", sod_asof="", day_buy_asof="2026-08-06")
        ensure_day_session(h4)
        check("heal overnight sod", h4.sod_shares, 2000.0)

        h5 = _h(shares=1000, cost=4.0, bought_at="2026-08-06", sod_asof="2026-08-06", sod_shares=0)
        ensure_day_session(h5)
        check("heal today buy amt", h5.day_buy_amount, 4000.0)

    print("all day-pnl scenarios passed")


if __name__ == "__main__":
    main()
