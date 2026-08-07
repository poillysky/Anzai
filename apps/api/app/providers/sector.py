"""Stock sector / concept boards via East Money — open + close resilient."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.providers.eastmoney import EM_HEADERS, em_float, host_label, stock_get_urls

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list["SectorBoard"]]] = {}
_CACHE_TTL = 120.0


@dataclass
class SectorBoard:
    code: str  # BK1039
    name: str
    rank: int | None = None
    price: float | None = None
    change_pct: float | None = None


def _em_code(symbol: str, market: str) -> str:
    mkt = market.upper()
    prefix = "SH" if mkt == "SH" else "SZ"
    return f"{prefix}{symbol.strip()}"


def _fetch_boards(symbol: str, market: str) -> list[SectorBoard]:
    code = _em_code(symbol, market)
    url = "https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax"
    try:
        with httpx.Client(timeout=10.0, headers=EM_HEADERS, follow_redirects=True) as client:
            resp = client.get(url, params={"code": code})
            resp.raise_for_status()
            data = resp.json() or {}
    except Exception:
        logger.exception("sector boards fetch failed %s", code)
        return []

    rows = data.get("ssbk") or []
    out: list[SectorBoard] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        bc = str(row.get("BOARD_CODE") or "").strip()
        name = str(row.get("BOARD_NAME") or "").strip()
        if not bc or not name:
            continue
        bk = bc if bc.upper().startswith("BK") else f"BK{bc}"
        if bk in seen:
            continue
        seen.add(bk)
        rank = row.get("BOARD_RANK")
        try:
            rank_i = int(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank_i = None
        out.append(SectorBoard(code=bk, name=name, rank=rank_i))
    return out


def _quote_board(bk: str) -> tuple[float | None, float | None]:
    secid = f"90.{bk.upper()}"
    params = {
        "secid": secid,
        "invt": "2",
        "fltt": "2",
        # f43 最新 / f60 昨收 / f170 涨跌幅
        "fields": "f57,f58,f43,f60,f170",
    }
    with httpx.Client(timeout=8.0, headers=EM_HEADERS, follow_redirects=True) as client:
        for url in stock_get_urls():
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                if not data:
                    continue
                price = em_float(data.get("f43"))
                prev = em_float(data.get("f60"))
                chg = em_float(data.get("f170"))
                if price is None or price <= 0:
                    if prev is not None and prev > 0:
                        price = prev
                        if chg is None:
                            chg = 0.0
                    else:
                        continue
                return price, chg
            except Exception as exc:
                logger.warning(
                    "board quote miss %s %s: %s",
                    host_label(url),
                    bk,
                    type(exc).__name__,
                )
    return None, None


def get_stock_sectors(symbol: str, market: str = "SH") -> list[SectorBoard]:
    sym = symbol.strip()
    mkt = market.upper()
    cache_key = f"{mkt}:{sym}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    boards = _fetch_boards(sym, mkt)
    for b in boards[:6]:
        price, chg = _quote_board(b.code)
        b.price = price
        b.change_pct = chg

    if boards:
        _CACHE[cache_key] = (now, boards)
    return boards


def format_sector_summary(symbol: str, market: str = "SH") -> str:
    boards = get_stock_sectors(symbol, market)
    if not boards:
        return f"{symbol} 暂无板块归属（勿编造行业涨跌）"
    lines = [f"【它属于哪些方向 · {symbol} {market.upper()}】（给安崽对齐用，回答时用人话概括）"]
    ranked = sorted(boards, key=lambda b: (b.rank is None, b.rank or 99))
    for b in ranked[:8]:
        chg = f"{b.change_pct:+.2f}%" if b.change_pct is not None else "—"
        lines.append(f"- {b.name} 今天 {chg}")
    return "\n".join(lines)
