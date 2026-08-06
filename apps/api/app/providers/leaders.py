"""Board / leaderboard stocks via East Money + curated related ETFs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.providers.quote import get_quotes

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, list["LeaderStock"]]] = {}
_CACHE_TTL = 20.0

# push2 often disconnects; push2delay keeps board lists available after hours.
_CLIST_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://push2delay.eastmoney.com/api/qt/clist/get",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}

BOARD_FS: dict[str, tuple[str, str]] = {
    "sh-composite": ("m:1+t:2", "沪市"),
    "sz-component": ("m:0+t:6", "深市"),
    "chinext": ("m:0+t:80", "创业板"),
    "hk-hsi": ("m:116", "港股"),
    "us-nasdaq": ("m:105", "美股"),
}

BOARD_KINDS: dict[str, tuple[str, str, str]] = {
    "up": ("f3", "1", "涨幅榜"),
    "down": ("f3", "0", "跌幅榜"),
    "amount": ("f6", "1", "成交额"),
    "turnover": ("f8", "1", "换手率"),
}

# Curated ETFs mapped to index tabs (产品重心)
RELATED_ETFS: dict[str, list[tuple[str, str, str]]] = {
    "sh-composite": [
        ("510300", "SH", "沪深300ETF"),
        ("510050", "SH", "上证50ETF"),
        ("510500", "SH", "中证500ETF"),
        ("588000", "SH", "科创50ETF"),
        ("512100", "SH", "中证1000ETF"),
        ("512890", "SH", "红利低波ETF"),
    ],
    "sz-component": [
        ("159919", "SZ", "沪深300ETF"),
        ("159901", "SZ", "深100ETF"),
        ("159922", "SZ", "中证500ETF"),
        ("159915", "SZ", "创业板ETF"),
        ("159605", "SZ", "中概互联ETF"),
    ],
    "chinext": [
        ("159915", "SZ", "创业板ETF"),
        ("159992", "SZ", "创新药ETF"),
        ("159995", "SZ", "芯片ETF"),
        ("159819", "SZ", "人工智能ETF"),
        ("159611", "SZ", "电力ETF"),
    ],
    "hk-hsi": [
        ("159920", "SZ", "恒生ETF"),
        ("513600", "SH", "恒生ETF"),
        ("513990", "SH", "港股通ETF"),
        ("513130", "SH", "恒生科技ETF"),
        ("513180", "SH", "恒生科技ETF"),
    ],
    "us-nasdaq": [
        ("513100", "SH", "纳指ETF"),
        ("159941", "SZ", "纳指ETF"),
        ("513500", "SH", "标普500ETF"),
        ("513300", "SH", "纳斯达克ETF"),
        ("513030", "SH", "德国ETF"),
    ],
}


@dataclass
class LeaderStock:
    symbol: str
    name: str
    market: str
    price: float
    change_pct: float | None
    amount: float | None = None
    turnover: float | None = None


def _market_from_f13(f13: object, symbol: str) -> str:
    try:
        n = int(f13)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        n = -1
    if n == 116:
        return "HK"
    if n in (1, 17):
        return "SH"
    if n in (0, 2):
        return "SZ"
    if n in (105, 106, 107) or n >= 100:
        return "US"
    if symbol and symbol[0].isalpha():
        return "US"
    # 5-digit HK codes (e.g. 00700)
    if symbol.isdigit() and len(symbol) == 5:
        return "HK"
    if symbol.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _mock_leaders(key: str, kind: str) -> list[LeaderStock]:
    base = {
        "sh-composite": [
            ("600519", "贵州茅台", "SH", 1680.0, 1.2, 8e9, 0.4),
            ("601318", "中国平安", "SH", 48.5, 0.8, 5e9, 0.6),
            ("600036", "招商银行", "SH", 36.2, -0.3, 4e9, 0.5),
            ("601166", "兴业银行", "SH", 18.0, -1.1, 3e9, 0.8),
        ],
        "sz-component": [
            ("000001", "平安银行", "SZ", 11.2, 0.5, 2e9, 0.7),
            ("000858", "五粮液", "SZ", 140.0, 1.1, 3e9, 0.5),
            ("002594", "比亚迪", "SZ", 260.0, -0.6, 6e9, 1.2),
            ("000002", "万科A", "SZ", 7.5, -1.5, 1e9, 1.0),
        ],
        "chinext": [
            ("300750", "宁德时代", "SZ", 190.0, 2.1, 7e9, 1.5),
            ("300059", "东方财富", "SZ", 18.5, 1.4, 4e9, 2.0),
            ("300760", "迈瑞医疗", "SZ", 250.0, -0.4, 2e9, 0.6),
            ("300015", "爱尔眼科", "SZ", 12.0, -1.8, 1.5e9, 0.9),
        ],
        "us-nasdaq": [
            ("NVDA", "英伟达", "US", 120.0, 1.5, 5e10, None),
            ("AAPL", "苹果", "US", 220.0, 0.6, 4e10, None),
            ("MSFT", "微软", "US", 420.0, 0.4, 3e10, None),
            ("TSLA", "特斯拉", "US", 250.0, -1.2, 2e10, None),
        ],
    }
    rows = list(base.get(key, base["sh-composite"]))
    if kind == "down":
        rows.sort(key=lambda r: r[4])
    elif kind == "amount":
        rows.sort(key=lambda r: r[5], reverse=True)
    elif kind == "turnover":
        rows.sort(key=lambda r: (r[6] or 0), reverse=True)
    else:
        rows.sort(key=lambda r: r[4], reverse=True)
    return [
        LeaderStock(
            symbol=s,
            name=n,
            market=m,
            price=p,
            change_pct=c,
            amount=a,
            turnover=t,
        )
        for s, n, m, p, c, a, t in rows
    ]


def _get_related_etfs(key: str) -> list[LeaderStock]:
    specs = RELATED_ETFS.get(key) or RELATED_ETFS["sh-composite"]
    quotes = get_quotes([(sym, mkt) for sym, mkt, _ in specs])
    out: list[LeaderStock] = []
    for sym, mkt, fallback in specs:
        q = quotes.get(sym)
        out.append(
            LeaderStock(
                symbol=sym,
                name=fallback,
                market=mkt,
                price=q.price if q else 0.0,
                change_pct=q.change_pct if q else None,
                amount=None,
                turnover=None,
            )
        )
    return out


def format_leaders_summary(
    key: str = "sh-composite",
    kind: str = "up",
    limit: int = 8,
) -> str:
    """Plain-language leaderboard for agent tools."""
    title, kind_used, stocks = get_leaders(key, kind, limit=max(3, min(int(limit or 8), 15)))
    kind_zh = {
        "up": "涨得多的",
        "down": "跌得多的",
        "amount": "成交额大的",
        "turnover": "换手活跃的",
        "etf": "相关ETF",
    }.get(kind_used, kind_used)
    lines = [f"【榜单 · {title}】今天{kind_zh}（前 {min(len(stocks), limit)} 只，给人话概括用）"]
    if not stocks:
        lines.append("（暂无榜单数据，勿编造）")
        return "\n".join(lines)
    for s in stocks[:limit]:
        chg = f"{s.change_pct:+.2f}%" if s.change_pct is not None else "—"
        lines.append(f"- {s.name}（{s.symbol}）现价 {s.price} · {chg}")
    return "\n".join(lines)


def get_leaders(
    key: str,
    kind: str = "up",
    limit: int = 100,
) -> tuple[str, str, list[LeaderStock]]:
    """Return (board_title, kind, stocks)."""
    fs, region = BOARD_FS.get(key) or BOARD_FS["sh-composite"]
    if kind == "etf":
        title = f"{region}相关ETF"
        cache_key = f"{key}:etf:{limit}"
        now = time.time()
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            return title, "etf", cached[1]
        rows = _get_related_etfs(key)[:limit]
        _CACHE[cache_key] = (now, rows)
        return title, "etf", rows

    kind = kind if kind in BOARD_KINDS else "up"
    fid, po, label = BOARD_KINDS[kind]
    title = f"{region}{label}"
    cache_key = f"{key}:{kind}:{limit}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return title, kind, cached[1]

    out = _fetch_clist(fs=fs, fid=fid, po=po, limit=limit)
    if not out:
        logger.warning("Leaderboard empty for %s/%s; using mock", key, kind)
        out = _mock_leaders(key, kind)

    _CACHE[cache_key] = (now, out)
    return title, kind, out


def _parse_clist_row(row: dict) -> LeaderStock | None:
    symbol = str(row.get("f12") or "").strip()
    name = str(row.get("f14") or "").strip() or symbol
    if not symbol:
        return None
    try:
        price = float(row.get("f2") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        return None
    try:
        chg = float(row.get("f3")) if row.get("f3") is not None else None
    except (TypeError, ValueError):
        chg = None
    try:
        amount = float(row.get("f6")) if row.get("f6") is not None else None
    except (TypeError, ValueError):
        amount = None
    try:
        turnover = float(row.get("f8")) if row.get("f8") is not None else None
    except (TypeError, ValueError):
        turnover = None
    return LeaderStock(
        symbol=symbol,
        name=name,
        market=_market_from_f13(row.get("f13"), symbol),
        price=price,
        change_pct=round(chg, 2) if chg is not None else None,
        amount=amount,
        turnover=round(turnover, 2) if turnover is not None else None,
    )


def _fetch_clist(*, fs: str, fid: str, po: str, limit: int) -> list[LeaderStock]:
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": po,
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": fs,
        "fields": "f12,f13,f14,f2,f3,f6,f8",
    }
    with httpx.Client(timeout=8.0, headers=_HEADERS, follow_redirects=True) as client:
        for url in _CLIST_URLS:
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                rows = data.get("diff") or []
                if not rows:
                    logger.info("Leaders empty from %s fs=%s", url.split("/")[2], fs)
                    continue
                out: list[LeaderStock] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    item = _parse_clist_row(row)
                    if item is not None:
                        out.append(item)
                if out:
                    return out
            except Exception:
                logger.warning(
                    "Leaders fetch miss %s fs=%s",
                    url.split("/")[2],
                    fs,
                    exc_info=True,
                )
    return []
