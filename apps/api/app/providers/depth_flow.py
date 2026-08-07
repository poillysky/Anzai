"""Order book (五档) + money flow (资金流向) for A-shares.

Primary:
  - 五档：新浪 hq（与 quote 同源，收盘后档位常为 0）
  - 资金：东财 fflow/daykline（push2his）

Fallback:
  - 五档：东财 stock/get（盘中更全，delay 常缺档）
  - 资金：AKShare stock_individual_fund_flow（底层仍多为东财）

「主力」= 成交额分档统计，不是庄家身份。文案禁止写「庄家入场」。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx

from app.providers.eastmoney import EM_HEADERS, em_float, host_label, stock_get_urls
from app.providers.quote import _sina_code, normalize_symbol
from app.providers.session import cn_session

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, "DepthFlowSnapshot"]] = {}
_CACHE_TTL = 20.0

_HIS = "https://push2his.eastmoney.com"
_DELAY = "https://push2delay.eastmoney.com"
_PUSH2 = "https://push2.eastmoney.com"

_DEPTH_FIELDS = (
    "f57,f58,f43,"
    "f11,f12,f13,f14,f15,f16,f17,f18,f19,f20,"
    "f31,f32,f33,f34,f35,f36,f37,f38,f39,f40"
)


@dataclass
class BookLevel:
    price: float
    volume: float  # 手数（股/100），与常见盘口一致


@dataclass
class OrderBook:
    symbol: str
    market: str
    name: str
    bids: list[BookLevel] = field(default_factory=list)  # 买1→买5
    asks: list[BookLevel] = field(default_factory=list)  # 卖1→卖5
    as_of: str | None = None
    source: str = ""
    live: bool = False  # 连续竞价中且有挂单量


@dataclass
class MoneyFlowDay:
    date: str
    main_net: float  # 主力净流入（元）
    super_net: float
    large_net: float
    mid_net: float
    small_net: float
    main_pct: float | None = None  # 主力净占比 %


@dataclass
class DepthFlowSnapshot:
    symbol: str
    market: str
    name: str
    book: OrderBook | None
    flow_days: list[MoneyFlowDay]
    flow_bias: str  # in | out | flat | na
    flow_label: str
    session_state: str = "closed"  # trading | lunch | pre | closed | weekend
    book_live: bool = False
    note: str = "主力按成交额分档，非庄家身份；非投资建议"


def _book_has_size(book: OrderBook | None) -> bool:
    if not book:
        return False
    return any(x.volume > 0 for x in book.bids + book.asks)


def _fflow_urls() -> tuple[str, ...]:
    return (
        f"{_HIS}/api/qt/stock/fflow/daykline/get",
        f"{_DELAY}/api/qt/stock/fflow/daykline/get",
        f"{_PUSH2}/api/qt/stock/fflow/daykline/get",
    )


def _secid(symbol: str, market: str) -> str:
    mkt = market.upper()
    sym = symbol.strip()
    if mkt == "HK":
        code = sym.zfill(5) if sym.isdigit() else sym
        return f"116.{code}"
    return f"{'1' if mkt == 'SH' else '0'}.{sym}"


def _fmt_yi(amount: float) -> str:
    """元 → 亿，带符号。"""
    yi = amount / 1e8
    if abs(yi) >= 0.01:
        return f"{yi:+.2f}亿"
    wan = amount / 1e4
    return f"{wan:+.0f}万"


def _bias_from_main(main_net: float) -> tuple[str, str]:
    # ~500万死区
    if main_net > 5e6:
        return "in", "资金偏流入"
    if main_net < -5e6:
        return "out", "资金偏流出"
    return "flat", "资金大致平衡"


def _fetch_sina_book(symbol: str, market: str) -> OrderBook | None:
    code = _sina_code(symbol, market)
    url = f"https://hq.sinajs.cn/list={code}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=8.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw_bytes = resp.content
            for enc in ("gbk", "utf-8"):
                try:
                    text = raw_bytes.decode(enc)
                    break
                except UnicodeDecodeError:
                    text = raw_bytes.decode("gbk", errors="ignore")
    except Exception as exc:
        logger.warning("sina book miss %s: %s", code, type(exc).__name__)
        return None

    for line in text.strip().splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            payload = line.split("=", 1)[1].strip().strip(";").strip('"')
            parts = payload.split(",")
            if len(parts) < 30:
                continue
            name = parts[0] or symbol
            bids: list[BookLevel] = []
            asks: list[BookLevel] = []
            for i in range(5):
                bv = float(parts[10 + i * 2] or 0)
                bp = float(parts[11 + i * 2] or 0)
                av = float(parts[20 + i * 2] or 0)
                ap = float(parts[21 + i * 2] or 0)
                # 股→手
                bids.append(BookLevel(price=bp, volume=round(bv / 100, 2) if bv else 0.0))
                asks.append(BookLevel(price=ap, volume=round(av / 100, 2) if av else 0.0))
            as_of = None
            if len(parts) > 31:
                as_of = f"{parts[30]} {parts[31]}".strip()
            return OrderBook(
                symbol=symbol,
                market=market,
                name=name,
                bids=bids,
                asks=asks,
                as_of=as_of,
                source="sina",
                live=any(x.volume > 0 for x in bids + asks),
            )
        except Exception:
            logger.exception("parse sina book failed %s", code)
    return None


def _fetch_em_book(symbol: str, market: str) -> OrderBook | None:
    secid = _secid(symbol, market)
    params = {"secid": secid, "fltt": "2", "invt": "2", "fields": _DEPTH_FIELDS}
    with httpx.Client(timeout=8.0, headers=EM_HEADERS, follow_redirects=True) as client:
        for url in stock_get_urls():
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                if not data:
                    continue
                # bid: f19/f20 … f11/f12 ; ask: f39/f40 … f31/f32
                bid_pairs = [(19, 20), (17, 18), (15, 16), (13, 14), (11, 12)]
                ask_pairs = [(39, 40), (37, 38), (35, 36), (33, 34), (31, 32)]
                bids: list[BookLevel] = []
                asks: list[BookLevel] = []
                for pk, vk in bid_pairs:
                    p = em_float(data.get(f"f{pk}"))
                    v = em_float(data.get(f"f{vk}"))
                    if p is None and v is None:
                        continue
                    bids.append(BookLevel(price=float(p or 0), volume=float(v or 0)))
                for pk, vk in ask_pairs:
                    p = em_float(data.get(f"f{pk}"))
                    v = em_float(data.get(f"f{vk}"))
                    if p is None and v is None:
                        continue
                    asks.append(BookLevel(price=float(p or 0), volume=float(v or 0)))
                if not bids and not asks:
                    logger.info("EM book empty fields from %s", host_label(url))
                    continue
                return OrderBook(
                    symbol=symbol,
                    market=market,
                    name=str(data.get("f58") or symbol),
                    bids=bids,
                    asks=asks,
                    as_of=None,
                    source=f"em:{host_label(url)}",
                    live=any(x.volume > 0 for x in bids + asks),
                )
            except Exception as exc:
                logger.warning(
                    "EM book miss %s %s: %s",
                    host_label(url),
                    secid,
                    type(exc).__name__,
                )
    return None


def get_order_book(symbol: str, market: str = "SH") -> OrderBook | None:
    sym, mkt = normalize_symbol(symbol, market)
    book = _fetch_sina_book(sym, mkt)
    if book and any(x.price > 0 or x.volume > 0 for x in book.bids + book.asks):
        return book
    em = _fetch_em_book(sym, mkt)
    if em:
        return em
    return book  # may be flat zeros after close


def _parse_fflow_line(row: str) -> MoneyFlowDay | None:
    parts = str(row).split(",")
    if len(parts) < 6:
        return None
    date = parts[0].strip()
    main = em_float(parts[1])
    small = em_float(parts[2])
    mid = em_float(parts[3])
    large = em_float(parts[4])
    super_n = em_float(parts[5])
    if main is None:
        return None
    pct = em_float(parts[6]) if len(parts) > 6 else None
    return MoneyFlowDay(
        date=date,
        main_net=float(main),
        small_net=float(small or 0),
        mid_net=float(mid or 0),
        large_net=float(large or 0),
        super_net=float(super_n or 0),
        main_pct=pct,
    )


def _fetch_em_flow(symbol: str, market: str, *, limit: int = 5) -> list[MoneyFlowDay]:
    secid = _secid(symbol, market)
    params = {
        "lmt": str(max(1, min(limit, 30))),
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }
    with httpx.Client(timeout=8.0, headers=EM_HEADERS, follow_redirects=True) as client:
        for url in _fflow_urls():
            try:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                raw = data.get("klines") or []
                days: list[MoneyFlowDay] = []
                for row in raw:
                    d = _parse_fflow_line(str(row))
                    if d:
                        days.append(d)
                if days:
                    return days
                logger.info("EM fflow empty from %s for %s", host_label(url), secid)
            except Exception as exc:
                logger.warning(
                    "EM fflow miss %s %s: %s",
                    host_label(url),
                    secid,
                    type(exc).__name__,
                )
    return []


def _fetch_ak_flow(symbol: str, market: str, *, limit: int = 5) -> list[MoneyFlowDay]:
    """AKShare fallback — same EM family; use only if direct EM failed."""
    try:
        import akshare as ak
    except Exception:
        return []
    mkt = "sh" if market.upper() == "SH" else "sz"
    try:
        df = ak.stock_individual_fund_flow(stock=symbol, market=mkt)
    except Exception as exc:
        logger.warning("akshare fund flow failed %s:%s: %s", market, symbol, type(exc).__name__)
        return []
    if df is None or getattr(df, "empty", True):
        return []
    cols = {str(c): c for c in df.columns}
    date_c = cols.get("日期")
    main_c = cols.get("主力净流入-净额") or cols.get("主力净流入净额")
    super_c = cols.get("超大单净流入-净额") or cols.get("超大单净流入净额")
    large_c = cols.get("大单净流入-净额") or cols.get("大单净流入净额")
    mid_c = cols.get("中单净流入-净额") or cols.get("中单净流入净额")
    small_c = cols.get("小单净流入-净额") or cols.get("小单净流入净额")
    pct_c = cols.get("主力净流入-净占比") or cols.get("主力净流入净占比")
    if not date_c or not main_c:
        return []
    days: list[MoneyFlowDay] = []
    try:
        tail = df.tail(limit)
    except Exception:
        tail = df
    for _, row in tail.iterrows():
        try:
            main = float(row[main_c])
        except (TypeError, ValueError):
            continue
        def _f(col: object | None) -> float:
            if col is None:
                return 0.0
            try:
                return float(row[col])
            except (TypeError, ValueError):
                return 0.0

        pct = None
        if pct_c is not None:
            try:
                pct = float(row[pct_c])
            except (TypeError, ValueError):
                pct = None
        days.append(
            MoneyFlowDay(
                date=str(row[date_c]),
                main_net=main,
                super_net=_f(super_c),
                large_net=_f(large_c),
                mid_net=_f(mid_c),
                small_net=_f(small_c),
                main_pct=pct,
            )
        )
    return days


def get_money_flow(symbol: str, market: str = "SH", *, limit: int = 5) -> list[MoneyFlowDay]:
    sym, mkt = normalize_symbol(symbol, market)
    days = _fetch_em_flow(sym, mkt, limit=limit)
    if days:
        return days
    return _fetch_ak_flow(sym, mkt, limit=limit)


def get_depth_flow(symbol: str, market: str = "SH", *, flow_days: int = 5) -> DepthFlowSnapshot:
    sym, mkt = normalize_symbol(symbol, market)
    if mkt == "HK":
        from app.providers.quote import get_quote
        from app.providers.session import hk_session

        sess = hk_session()
        name = sym
        try:
            q = get_quote(sym, "HK")
            if q and q.name:
                name = q.name
        except Exception:
            pass
        return DepthFlowSnapshot(
            symbol=sym,
            market="HK",
            name=name,
            book=None,
            flow_days=[],
            flow_bias="na",
            flow_label="港股暂无A股式资金分档",
            session_state=sess.state,
            book_live=False,
            note="港股聊天可查报价/分时/日K；无A股式五档主力分档。勿说庄家。",
        )

    if mkt == "JD":
        from app.providers.gold import jd_name_by_symbol
        from app.providers.quote import get_quote

        name = jd_name_by_symbol(sym)
        try:
            q = get_quote(sym, "JD")
            if q and q.name:
                name = q.name
        except Exception:
            pass
        return DepthFlowSnapshot(
            symbol=sym,
            market="JD",
            name=name,
            book=None,
            flow_days=[],
            flow_bias="na",
            flow_label="积存金无场内资金",
            session_state="na",
            book_live=False,
            note=(
                "浙商/民生积存金为场外金价（京东），无交易所五档与主力净流入；"
                "请看实时金价与仓位盈亏。勿编造盘口或庄家。"
            ),
        )

    sess = cn_session()
    trading = sess.state == "trading"
    cache_key = f"{mkt}:{sym}:{flow_days}:{sess.state}"
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    # 非连续竞价：不返回五档（收盘后新浪仍可能残留昨价，易误导）
    raw_book = get_order_book(sym, mkt) if trading else None
    book_live = bool(trading and raw_book and _book_has_size(raw_book))
    book = raw_book if book_live else None

    days = get_money_flow(sym, mkt, limit=flow_days)
    name = (raw_book.name if raw_book else "") or (book.name if book else "") or sym
    if not trading and not raw_book:
        # still try sina once for name only when closed
        named = _fetch_sina_book(sym, mkt)
        if named:
            name = named.name or name

    if days:
        bias, label = _bias_from_main(days[-1].main_net)
    else:
        bias, label = "na", "资金暂无"

    snap = DepthFlowSnapshot(
        symbol=sym,
        market=mkt,
        name=name,
        book=book,
        flow_days=days,
        flow_bias=bias,
        flow_label=label,
        session_state=sess.state,
        book_live=book_live,
        note=(
            "主力按成交额分档，非庄家身份；非投资建议"
            if trading
            else "已收盘/非连续竞价，无实时五档；资金为日频统计"
        ),
    )
    _CACHE[cache_key] = (now, snap)
    return snap


def format_depth_flow_summary(symbol: str, market: str = "SH") -> str:
    snap = get_depth_flow(symbol, market)
    if snap.market == "HK":
        return (
            f"【盘口·资金 · {snap.name}（{snap.symbol} HK）】\n"
            f"{snap.flow_label}。港股请用 get_quote / get_intraday / get_kline 查行情；"
            "勿编造A股式主力净流入或庄家。"
        )
    if snap.market == "JD":
        return (
            f"【盘口·资金 · {snap.name}（{snap.symbol} 积存金）】\n"
            f"{snap.flow_label}。积存金无交易所五档/主力分档；"
            "用 get_quote 看实时金价；勿编造盘口或庄家。"
        )
    lines = [f"【盘口·资金 · {snap.name}（{snap.symbol} {snap.market}）】"]
    book = snap.book
    if snap.book_live and book and (book.bids or book.asks):
        lines.append(
            "买卖五档（盘中）"
            + (f" · {book.as_of}" if book.as_of else "")
        )
        for i, (b, a) in enumerate(zip(book.bids, book.asks), start=1):
            lines.append(
                f"  买{i} {b.price:.2f}/{b.volume:.0f}手 · 卖{i} {a.price:.2f}/{a.volume:.0f}手"
            )
    elif snap.session_state == "trading":
        lines.append("五档暂无挂单量（勿编造盘口）。")
    else:
        label = {
            "closed": "已收盘",
            "weekend": "周末休市",
            "lunch": "午间休市",
            "pre": "未开盘",
        }.get(snap.session_state, "非交易时段")
        lines.append(f"{label}，无实时买卖五档（勿把昨收残留价当挂单）。")

    if snap.flow_days:
        last = snap.flow_days[-1]
        pct = f" · 占比 {last.main_pct:+.2f}%" if last.main_pct is not None else ""
        lines.append(
            f"资金（{last.date}）{snap.flow_label}：主力净 {_fmt_yi(last.main_net)}{pct}"
        )
        lines.append(
            f"  超大 {_fmt_yi(last.super_net)} · 大 {_fmt_yi(last.large_net)} · "
            f"中 {_fmt_yi(last.mid_net)} · 小 {_fmt_yi(last.small_net)}"
        )
        if len(snap.flow_days) > 1:
            recent = "；".join(
                f"{d.date[5:]} {_fmt_yi(d.main_net)}" for d in snap.flow_days[-3:]
            )
            lines.append(f"近几日主力净：{recent}")
        lines.append("说明：主力=成交额分档，不是庄家；勿说「庄家入场」。")
    else:
        lines.append("资金流向暂无（勿编造）。")
    return "\n".join(lines)
