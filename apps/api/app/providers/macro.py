"""Macro / commodity quotes via Sina (gold, silver, oil, FX, …)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

_SH = ZoneInfo("Asia/Shanghai")
_WEEKDAY = "一二三四五六日"


@dataclass
class MacroQuote:
    key: str
    name: str
    price: float
    unit: str
    change_pct: float | None = None
    prev: float | None = None
    as_of: str | None = None
    live: bool = True
    venue: str = ""  # a_share | cn_future | spot | us | fx


def shanghai_now() -> datetime:
    return datetime.now(_SH)


def calendar_clock_line() -> str:
    """Tell the model what 「今天」 means — wall clock, not last print time."""
    now = shanghai_now()
    wd = _WEEKDAY[now.weekday()]
    return (
        f"【日历】此刻上海时间 {now.strftime('%Y-%m-%d %H:%M')}（星期{wd}）。"
        f"用户说的「今天/今日」= 这个日历日 {now.strftime('%Y-%m-%d')}。"
        f"每条行情自带时间标签；写了「非今日」的是昨收或旧点，"
        f"嘴上必须说清是哪天的数，禁止把昨收说成「今天盘中」。"
    )


def _parse_as_of_date(as_of: str | None) -> date | None:
    if not as_of:
        return None
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", as_of)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def freshness_label(as_of: str | None, *, venue: str = "") -> str:
    """Human tag so the model doesn't call yesterday's close 「今天」."""
    d = _parse_as_of_date(as_of)
    today = shanghai_now().date()
    if d is None:
        return "时间未知"
    if d == today:
        if venue == "a_share":
            return "今日A股"
        if venue in {"cn_future", "spot"}:
            return "今日（含夜盘/早盘）"
        return "今日"
    # older than calendar today
    if venue == "a_share":
        return f"A股收盘·{d.isoformat()}（非今日盘中）"
    return f"{d.isoformat()}（非今日）"


# topic_id -> aliases + sina specs (code, kind, display, unit)
# kind: hf=外盘期货 | gds=贵金属现货 | nf=国内期货连续 | a=A股(走 quote.get_quotes)
_TOPIC_CATALOG: list[dict] = [
    {
        "id": "gold",
        "aliases": ("黄金", "金价", "沪金", "金饰", "gold", "xau", "au9999"),
        "items": (
            ("gds_AU9999", "gds", "沪金AU9999", "元/克"),
            ("nf_AU0", "nf", "沪金连续", "元/克"),
            ("hf_GC", "hf", "纽约黄金", "美元/盎司"),
            ("518880", "a", "黄金ETF华安", "元"),
        ),
    },
    {
        "id": "silver",
        "aliases": ("白银", "银价", "沪银", "silver", "xag"),
        "items": (
            ("nf_AG0", "nf", "沪银连续", "元/千克"),
            ("hf_SI", "hf", "纽约白银", "美元/盎司"),
        ),
    },
    {
        "id": "oil",
        "aliases": ("原油", "石油", "油价", "布油", "美油", "oil", "wti", "brent"),
        "items": (
            ("hf_CL", "hf", "纽约原油WTI", "美元/桶"),
            ("nf_SC0", "nf", "上海原油连续", "元/桶"),
        ),
    },
    {
        "id": "copper",
        "aliases": ("铜", "沪铜", "铜价", "copper", "cu"),
        "items": (
            ("nf_CU0", "nf", "沪铜连续", "元/吨"),
            ("hf_HG", "hf", "美铜", "美分/磅"),
        ),
    },
    {
        "id": "usd_cny",
        "aliases": ("美元", "人民币", "汇率", "美元兑人民币", "离岸", "在岸", "usdcny", "usd/cny"),
        "items": (("fx_susdcny", "fx", "在岸人民币 USD/CNY", "元"),),
    },
]


def list_topics() -> list[str]:
    return [t["id"] for t in _TOPIC_CATALOG]


def topics_mentioned(text: str) -> list[dict]:
    """Return all catalog topics whose aliases appear in text (alias length ≥ 2)."""
    q = (text or "").strip().lower()
    if not q:
        return []
    found: list[dict] = []
    for t in _TOPIC_CATALOG:
        hit = False
        for a in t["aliases"]:
            al = str(a).lower()
            if len(al) < 2:
                continue
            if al in q:
                hit = True
                break
        if hit:
            found.append(t)
    return found


def resolve_topic(query: str) -> dict | None:
    mentioned = topics_mentioned(query)
    if mentioned:
        # Prefer longest alias match among mentioned
        q = (query or "").strip().lower()
        best = mentioned[0]
        best_len = 0
        for t in mentioned:
            for a in t["aliases"]:
                al = str(a).lower()
                if len(al) >= 2 and al in q and len(al) > best_len:
                    best = t
                    best_len = len(al)
        return best
    q = (query or "").strip().lower()
    if not q:
        return None
    for t in _TOPIC_CATALOG:
        if q == t["id"]:
            return t
    return None


def _parse_as_of(parts: list[str]) -> str | None:
    date = ""
    tod = ""
    for p in parts:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
            date = p
        elif re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", p):
            tod = p
    if date and tod:
        return f"{date} {tod}"
    return date or tod or None


def _pct(price: float, prev: float | None) -> float | None:
    if prev and prev > 0 and price > 0:
        return round((price - prev) / prev * 100, 2)
    return None


def _parse_hf(code: str, raw: str, name: str, unit: str) -> MacroQuote | None:
    # 最新,,,最高,最低,时间,昨结算,开盘,...,日期,名称
    parts = raw.split(",")
    if len(parts) < 8:
        return None
    try:
        price = float(parts[0] or 0)
        prev = float(parts[7] or 0) or None
        disp = (parts[13] if len(parts) > 13 else "") or name
        if price <= 0:
            return None
        return MacroQuote(
            key=code,
            name=disp,
            price=price,
            unit=unit,
            prev=prev,
            change_pct=_pct(price, prev),
            as_of=_parse_as_of(parts),
            live=True,
        )
    except ValueError:
        return None


def _parse_gds(code: str, raw: str, name: str, unit: str) -> MacroQuote | None:
    # 最新,...,时间,昨收,开盘,...,日期,名称
    parts = raw.split(",")
    if len(parts) < 9:
        return None
    try:
        price = float(parts[0] or 0)
        prev = float(parts[7] or 0) or None
        disp = (parts[13] if len(parts) > 13 else "") or name
        if price <= 0:
            return None
        return MacroQuote(
            key=code,
            name=disp,
            price=price,
            unit=unit,
            prev=prev,
            change_pct=_pct(price, prev),
            as_of=_parse_as_of(parts),
            live=True,
        )
    except ValueError:
        return None


def _parse_nf(code: str, raw: str, name: str, unit: str) -> MacroQuote | None:
    # 名称,时间,开盘,最高,最低,?,最新,买,卖,?,昨结算,...,日期
    parts = raw.split(",")
    if len(parts) < 11:
        return None
    try:
        price = float(parts[6] or 0)
        prev = float(parts[10] or 0) or None
        disp = parts[0] or name
        if price <= 0:
            return None
        # time often HHMMSS in parts[1]
        tod = parts[1]
        if re.fullmatch(r"\d{6}", tod):
            tod = f"{tod[0:2]}:{tod[2:4]}:{tod[4:6]}"
        date = next((p for p in parts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)), "")
        as_of = f"{date} {tod}".strip() if date or tod else None
        return MacroQuote(
            key=code,
            name=disp,
            price=price,
            unit=unit,
            prev=prev,
            change_pct=_pct(price, prev),
            as_of=as_of,
            live=True,
        )
    except ValueError:
        return None


def _parse_fx(code: str, raw: str, name: str, unit: str) -> MacroQuote | None:
    # time,bid,ask,...,昨收?,...,名称,涨跌额,涨跌幅,...
    parts = raw.split(",")
    if len(parts) < 10:
        return None
    try:
        # mid from bid/ask
        bid = float(parts[1] or 0)
        ask = float(parts[2] or 0)
        price = round((bid + ask) / 2, 4) if bid and ask else bid or ask
        # change_pct often parts[11]
        chg = None
        try:
            chg = float(parts[11]) if parts[11] else None
        except (ValueError, IndexError):
            chg = None
        disp = parts[9] if len(parts) > 9 and parts[9] else name
        as_of = None
        if parts[0] and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", parts[0]):
            date = next((p for p in parts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)), "")
            as_of = f"{date} {parts[0]}".strip() if date else parts[0]
        if price <= 0:
            return None
        return MacroQuote(
            key=code,
            name=disp,
            price=price,
            unit=unit,
            change_pct=round(chg, 4) if chg is not None else None,
            as_of=as_of,
            live=True,
        )
    except ValueError:
        return None


def _fetch_raw(codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    url = f"https://hq.sinajs.cn/list={','.join(codes)}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=8.0, headers=headers) as client:
        resp = client.get(url)
        resp.raise_for_status()
        text = resp.content.decode("gbk", errors="ignore")
    out: dict[str, str] = {}
    for line in text.strip().splitlines():
        if "hq_str_" not in line or '="' not in line:
            continue
        try:
            left, payload = line.split("=", 1)
            code = left.split("hq_str_")[-1]
            raw = payload.strip().strip(";").strip('"')
            if raw:
                out[code] = raw
        except ValueError:
            continue
    return out


def get_macro_quotes(topic_query: str) -> tuple[str | None, list[MacroQuote], str]:
    """Resolve topic and fetch quotes. Returns (topic_id, quotes, error_or_hint)."""
    topic = resolve_topic(topic_query)
    if topic is None:
        known = "、".join(t["id"] + f"（{'/'.join(t['aliases'][:3])}）" for t in _TOPIC_CATALOG)
        return None, [], f"暂不支持「{topic_query}」。已支持：{known}"

    sina_items = [(c, k, n, u) for c, k, n, u in topic["items"] if k != "a"]
    a_items = [(c, n, u) for c, k, n, u in topic["items"] if k == "a"]

    quotes: list[MacroQuote] = []
    try:
        raw_map = _fetch_raw([c for c, *_ in sina_items])
    except Exception:
        logger.exception("macro fetch failed")
        raw_map = {}

    venue_by_kind = {
        "hf": "us",
        "gds": "spot",
        "nf": "cn_future",
        "fx": "fx",
    }
    parsers = {"hf": _parse_hf, "gds": _parse_gds, "nf": _parse_nf, "fx": _parse_fx}
    for code, kind, name, unit in sina_items:
        raw = raw_map.get(code)
        if not raw:
            continue
        fn = parsers.get(kind)
        if not fn:
            continue
        q = fn(code, raw, name, unit)
        if q:
            q.venue = venue_by_kind.get(kind, "")
            quotes.append(q)

    if a_items:
        from app.providers.quote import get_quotes

        pairs = []
        for sym, _n, _u in a_items:
            # A-share gold ETF
            mkt = "SH" if sym.startswith(("5", "6", "9")) else "SZ"
            pairs.append((sym, mkt))
        aq = get_quotes(pairs)
        for sym, name, unit in a_items:
            q = aq.get(sym)
            if not q or not q.live or not q.price:
                continue
            quotes.append(
                MacroQuote(
                    key=sym,
                    name=q.name or name,
                    price=q.price,
                    unit=unit,
                    change_pct=q.change_pct,
                    prev=q.prev_close,
                    as_of=q.as_of,
                    live=True,
                    venue="a_share",
                )
            )

    if not quotes:
        return topic["id"], [], "行情暂时拉不到（勿编造）"
    return topic["id"], quotes, ""


def format_macro_text(topic_query: str) -> str:
    topic_id, quotes, err = get_macro_quotes(topic_query)
    if err and not quotes:
        return err
    lines = [
        f"【宏观/商品 · {topic_id}】来源新浪；无报价禁止编造。",
        calendar_clock_line(),
    ]
    stale = 0
    for q in quotes:
        chg = f"{q.change_pct:+.2f}%" if q.change_pct is not None else "—"
        tag = freshness_label(q.as_of, venue=q.venue)
        if "非今日" in tag:
            stale += 1
        line = f"- {q.name}：{q.price} {q.unit} · 涨跌 {chg} · [{tag}]"
        if q.as_of:
            line += f" · {q.as_of}"
        lines.append(line)
    if stale:
        lines.append(
            f"说明：其中 {stale} 条不是今天盘中（多为A股昨收）。"
            f"用户问「今天」时，优先讲标了「今日」的品种；昨收要单独说清。"
        )
    return "\n".join(lines)
