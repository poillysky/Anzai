"""安崽 chat tools — on-demand quotes / portfolio / news (no hallucinated numbers)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Holding
from app.providers.intraday import format_intraday_summary
from app.providers.kline import format_kline_summary
from app.providers.leaders import format_leaders_summary
from app.providers.macro import format_macro_text, list_topics
from app.providers.news import get_holdings_news, get_market_news, get_interests_news
from app.providers.quote import get_quote, get_quotes, normalize_symbol
from app.providers.search import format_search_summary, resolve_best_symbol
from app.providers.sector import format_sector_summary
from app.services.portfolio import build_portfolio, consolidate_same_symbol

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 3

_MACRO_HINT = "、".join(list_topics())

# OpenAI-compatible tool schemas
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_indices",
            "description": "查询主要A股指数实时行情（上证/深成/创业）。问大盘涨跌、点位时必须先调此工具。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro",
            "description": (
                "查询黄金/白银/原油/铜/美元人民币等宏观商品行情。"
                f"用户问金价、白银、油价、铜、汇率时必须调此工具。支持主题：{_MACRO_HINT}。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "主题词，如 黄金、白银、原油、铜、美元、gold、oil",
                    },
                },
                "required": ["topic"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_quote",
            "description": "查询单只股票或 ETF 实时行情（现价、涨跌幅）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "代码，如 510300、600519；问上证/深成/创业请用 get_indices；问黄金现货/纽约金请用 get_macro",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ"],
                        "description": "市场 SH 或 SZ；不确定可省略，服务端按代码推断",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_intraday",
            "description": "查询单只股票/ETF 分时走势摘要（高低、现价位置、大致上行/下行/震荡）。问走势、分时、盘中形态时必须调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "代码，如 603078、510300"},
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ"],
                        "description": "市场 SH 或 SZ；可省略",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_kline",
            "description": "查询近 N 日 K 线摘要（OHLC、区间涨跌、MA5/10/20）。问历史走势、均线、近几日表现时必须调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "代码"},
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ"],
                        "description": "市场 SH 或 SZ；可省略",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "交易日数量，默认 30，最大 60",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector",
            "description": "查询个股所属行业/概念板块及板块涨跌。问板块、同业、概念时必须调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "代码"},
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ"],
                        "description": "市场 SH 或 SZ；可省略",
                    },
                },
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "查询用户仓库持仓摘要（总市值、盈亏、前若干持仓明细）。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "查询新闻：市场要闻、持仓相关、或关键词检索（如「黄金」、股票名）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["market", "holdings", "keyword"],
                        "description": "market=要闻；holdings=持仓相关；keyword=按词搜",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "kind=keyword 时的搜索词，如 半导体、白酒、黄金、江化微",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "条数，默认 5，最大 8",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_symbol",
            "description": "按名称或关键词搜股票/ETF 代码（如「茅台」「宁德」）。用户没报六位代码时先调此工具，再对命中代码查行情。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "名称或关键词"},
                    "limit": {"type": "integer", "description": "条数，默认 5"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_analysis_snapshot",
            "description": "读取用户「分析」页最近一次已完成报告摘要。问上次分析、报告结论、分析页怎么说时必须调；勿假装刚跑完专家。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leaders",
            "description": "查询涨跌榜/成交额榜（白话摘要）。问谁涨得猛、跌幅榜、成交活跃时调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "enum": ["sh-composite", "sz-component", "chinext"],
                        "description": "市场：沪市/深市/创业板，默认沪市口径",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["up", "down", "amount", "turnover", "etf"],
                        "description": "up涨幅 down跌幅 amount成交额 turnover换手 etf相关ETF",
                    },
                    "limit": {"type": "integer", "description": "条数默认 8"},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_LABELS: dict[str, str] = {
    "get_indices": "查指数行情",
    "get_macro": "查商品/宏观",
    "get_quote": "查个股行情",
    "get_intraday": "查分时走势",
    "get_kline": "查日K",
    "get_sector": "查板块",
    "get_portfolio": "查仓库持仓",
    "get_news": "查新闻",
    "search_symbol": "搜股票代码",
    "get_analysis_snapshot": "读分析报告",
    "get_leaders": "查涨跌榜",
}

_INDEX_HINTS = (
    "上证",
    "深成",
    "深证",
    "创业板",
    "大盘",
    "沪指",
    "指数",
    "点位",
    "涨了多少",
    "跌了多少",
)
# Casual "how's today" → light pulse (indices + portfolio)
# Pulse = 问整体盘面/账户，不含单独「怎么样」（避免「黄金怎么样」误拉仓库）
_PULSE_HINTS = (
    "今天咋样",
    "今天怎么样",
    "今天怎样",
    "大盘",
    "收盘",
    "开盘",
    "盘面",
    "亏了吗",
    "赚了吗",
    "账户",
    "咱们今天",
)
_PORTFOLIO_HINTS = (
    "持仓",
    "仓库",
    "账户",
    "盈亏",
    "市值",
    "咱们仓",
    "仓位",
    "亏在",
    "集中",
    "重仓",
    "风险",
)
_NEWS_HINTS = ("新闻", "资讯", "消息", "要闻")
_ANALYSIS_HINTS = ("分析报告", "上次分析", "分析页", "报告结论", "跑完分析", "最近分析")
_LEADERS_HINTS = ("涨幅榜", "跌幅榜", "谁涨", "涨停", "跌得多", "成交额榜", "龙头", "领涨")
_NAME_STRIP = (
    "怎么样",
    "怎样",
    "怎么看",
    "情况",
    "分析",
    "走势",
    "分时",
    "看看",
    "值得买吗",
    "值得吗",
    "咋样",
    "如何",
    "帮我看",
    "帮我看看",
    "一下",
    "预估",
    "什么",
    "行情",
    "今天",
    "吗",
    "呢",
    "啊",
    "的",
    # 仓位闲聊，勿当股票名
    "持仓",
    "仓库",
    "账户",
    "盈亏",
    "市值",
    "仓位",
    "集中",
    "重仓",
    "风险",
    "分散",
    "散不散",
    "匀一点",
    "咱们仓",
)
_CHAT_BLOCK = frozenset(
    {
        "吃了",
        "吃了吗",
        "在吗",
        "你好",
        "嗨",
        "谢谢",
        "早",
        "晚安",
        "哈哈",
        "嗯",
        "哦",
        "好的",
        "行",
        "收到",
        "谁猛",
        "谁涨",
        "涨幅榜",
        "跌幅榜",
        "结论",
        "上次",
        "报告",
    }
)
_DEEP_ASK = (
    "情况",
    "怎么样",
    "怎样",
    "分析",
    "走势",
    "分时",
    "日K",
    "日k",
    "板块",
    "值得",
    "怎么看",
    "看看",
    "研判",
    "预估",
)


def is_macro_only_turn(text: str) -> bool:
    """Compat: macro scene without warehouse. Prefer agent_scene.detect_turn_scene."""
    from app.services.agent_scene import detect_turn_scene

    scene = detect_turn_scene(text)
    return scene.primary == "macro" and not scene.include_portfolio


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:,.0f}"


def tool_label(name: str) -> str:
    return TOOL_LABELS.get(name, name)


def last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages or []):
        if (m.get("role") or "") == "user":
            return str(m.get("content") or "").strip()
    return ""


def _resolve_sym_mkt(symbol: str, market: str = "") -> tuple[str, str]:
    mkt = (market or "").strip().upper()
    if mkt not in {"SH", "SZ", "HK", "US"}:
        mkt = ""
    try:
        return normalize_symbol(symbol.strip().upper(), mkt or None)
    except Exception:
        return symbol.strip().upper(), mkt or "SH"


def _extract_name_query(text: str) -> str:
    """Strip chatter words; leftover may be a stock name."""
    import re

    q = (text or "").strip()
    for w in _NAME_STRIP:
        q = q.replace(w, " ")
    q = re.sub(r"\s+", " ", q).strip()
    q = re.sub(r"(?<!\d)\d{6}(?!\d)", " ", q).strip()
    # drop pure pulse/macro words
    if q in {"今天", "行情", "大盘", "市场", "黄金", "白银", "原油"}:
        return ""
    if len(q) < 2:
        return ""
    return q[:32]


def _add_symbol_deep(
    add: Any,
    symbol: str,
    market: str,
    *,
    deep: bool,
) -> None:
    _s, mkt = _resolve_sym_mkt(symbol, market)
    add("get_quote", {"symbol": _s, "market": mkt})
    if deep:
        add("get_intraday", {"symbol": _s, "market": mkt})
        add("get_kline", {"symbol": _s, "market": mkt, "limit": 30})
        add("get_sector", {"symbol": _s, "market": mkt})
        q = get_quote(_s, mkt)
        kw = (q.name if q and q.name else _s).strip()
        add("get_news", {"kind": "keyword", "keyword": kw, "limit": 5})


def plan_prefetch(
    user_text: str,
    *,
    db: Session | None = None,
    user_id: int | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Heuristic tiers: pulse / topic / name→code / symbol-deep / news."""
    import re

    text = (user_text or "").strip()
    if not text:
        return []
    plan: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()

    def add(name: str, args: dict[str, Any]) -> None:
        key = name + ":" + json.dumps(args, ensure_ascii=False, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        plan.append((name, args))

    from app.providers.macro import topics_mentioned

    macro_topics = topics_mentioned(text)
    for t in macro_topics:
        add("get_macro", {"topic": t["id"]})

    symbols = re.findall(r"(?<!\d)(\d{6})(?!\d)", text)
    resolved: list[tuple[str, str, str]] = []  # symbol, market, name
    for sym in symbols:
        s, m = _resolve_sym_mkt(sym)
        resolved.append((s, m, ""))

    # Name → code；宏观 / 纯仓位闲聊 / 榜单等不按名搜股票
    name_q = ""
    want_portfolio = any(h in text for h in _PORTFOLIO_HINTS)
    skip_name = bool(macro_topics) or any(
        h in text for h in (_LEADERS_HINTS + _ANALYSIS_HINTS + _NEWS_HINTS)
    ) or (
        any(h in text for h in _PULSE_HINTS) and not any(k in text for k in _DEEP_ASK)
    ) or (
        # 「仓位散不散」类：只拉仓库，别把整句当股票名去搜
        want_portfolio
        and not re.search(r"(?<!\d)\d{6}(?!\d)", text)
        and not any(k in text for k in _DEEP_ASK)
    )

    if not resolved and not skip_name:
        name_q = _extract_name_query(text)
        if db is not None and user_id is not None and name_q and name_q not in _CHAT_BLOCK:
            rows = (
                db.query(Holding)
                .filter(Holding.user_id == user_id)
                .order_by(Holding.id.asc())
                .all()
            )
            for h in rows:
                nm = (h.name or "").strip()
                if nm and (nm in text or name_q in nm or nm in name_q):
                    resolved.append((h.symbol, h.market, nm))
                    break
        want_name = name_q and name_q not in _CHAT_BLOCK and (
            any(k in text for k in _DEEP_ASK) or text.strip() == name_q
        )
        if not resolved and want_name:
            hit = resolve_best_symbol(name_q)
            if hit:
                add("search_symbol", {"query": name_q, "limit": 5})
                resolved.append((hit.symbol, hit.market, hit.name))

    deep = bool(resolved) and (any(k in text for k in _DEEP_ASK) or len(text) <= 24)

    pulse = any(h in text for h in _PULSE_HINTS) or any(h in text for h in _INDEX_HINTS)

    if deep or resolved:
        add("get_indices", {})

    # 问黄金等宏观时：只拉宏观，别顺带塞仓库（否则模型必复读账户推销分散）
    if macro_topics and not resolved and not want_portfolio:
        pass
    elif pulse and not resolved:
        add("get_indices", {})
        add("get_portfolio", {})
    elif want_portfolio:
        add("get_portfolio", {})

    for sym, mkt, _nm in resolved[:2]:
        _add_symbol_deep(add, sym, mkt, deep=deep or len(resolved) == 1)

    if any(h in text for h in _ANALYSIS_HINTS):
        add("get_analysis_snapshot", {})

    if any(h in text for h in _LEADERS_HINTS):
        kind = "up"
        if any(k in text for k in ("跌", "跌幅")):
            kind = "down"
        elif "成交" in text:
            kind = "amount"
        elif "换手" in text:
            kind = "turnover"
        elif "ETF" in text.upper() or "etf" in text:
            kind = "etf"
        add("get_leaders", {"board": "sh-composite", "kind": kind, "limit": 8})

    if any(h in text for h in _NEWS_HINTS) and not resolved:
        topics = topics_mentioned(text)
        if topics:
            add("get_news", {"kind": "keyword", "keyword": topics[0]["id"], "limit": 5})
        else:
            add("get_news", {"kind": "market", "limit": 5})

    return plan


def prefetch_for_turn(
    db: Session,
    user_id: int,
    user_text: str,
) -> list[dict[str, str]]:
    """Run heuristic tools; return [{name, label, text}]."""
    out: list[dict[str, str]] = []
    for name, args in plan_prefetch(user_text, db=db, user_id=user_id):
        text = execute_tool(db, user_id, name, args)
        out.append({"name": name, "label": tool_label(name), "text": text})
    return out


def format_prefetch_block(
    items: list[dict[str, str]],
    *,
    scene_primary: str | None = None,
) -> str:
    if not items:
        return ""
    from app.providers.macro import calendar_clock_line

    parts = [
        "【本轮实时查询】刚拉取的真实数据；优先引用，没有再说没有。"
        "嵌进口语，别列清单，引用数字时不要加粗。",
        calendar_clock_line(),
    ]
    # 场景说明已在 assemble_turn；这里只补与数据强相关的一句
    if scene_primary == "macro" or (
        scene_primary is None and all(it["name"] == "get_macro" for it in items)
    ):
        parts.append("宏观数据：优先讲标了「今日」的品种；非今日=昨收，说清即可。")
    for it in items:
        parts.append(it["text"])
    return "\n\n".join(parts)


def execute_tool(
    db: Session,
    user_id: int,
    name: str,
    arguments: dict[str, Any] | None,
) -> str:
    """Run one tool; return compact Chinese text for the model."""
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "get_indices":
            return _tool_indices()
        if name == "get_macro":
            return format_macro_text(str(args.get("topic") or ""))
        if name == "get_quote":
            return _tool_quote(str(args.get("symbol") or ""), str(args.get("market") or ""))
        if name == "get_intraday":
            return _tool_intraday(str(args.get("symbol") or ""), str(args.get("market") or ""))
        if name == "get_kline":
            return _tool_kline(
                str(args.get("symbol") or ""),
                str(args.get("market") or ""),
                int(args.get("limit") or 30),
            )
        if name == "get_sector":
            return _tool_sector(str(args.get("symbol") or ""), str(args.get("market") or ""))
        if name == "get_portfolio":
            return _tool_portfolio(db, user_id)
        if name == "get_news":
            return _tool_news(
                db,
                user_id,
                kind=str(args.get("kind") or "market"),
                keyword=str(args.get("keyword") or ""),
                limit=int(args.get("limit") or 5),
            )
        if name == "search_symbol":
            return format_search_summary(
                str(args.get("query") or ""),
                limit=int(args.get("limit") or 5),
            )
        if name == "get_analysis_snapshot":
            return _tool_analysis_snapshot(db, user_id)
        if name == "get_leaders":
            return format_leaders_summary(
                key=str(args.get("board") or "sh-composite"),
                kind=str(args.get("kind") or "up"),
                limit=int(args.get("limit") or 8),
            )
        return json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return json.dumps({"error": f"{name} 失败：{type(exc).__name__}"}, ensure_ascii=False)


def _as_of_note(q: Any) -> str:
    as_of = getattr(q, "as_of", None) or ""
    if as_of and as_of != "mock":
        return f" · 行情时间 {as_of}（新浪）"
    if getattr(q, "live", False):
        return " · 来源新浪"
    return ""


def _tool_indices() -> str:
    from app.providers.macro import calendar_clock_line, freshness_label

    specs = [
        ("000001", "SH", "上证指数"),
        ("399001", "SZ", "深证成指"),
        ("399006", "SZ", "创业板指"),
    ]
    quotes = get_quotes([(s, m) for s, m, _ in specs])
    lines = [
        "【指数行情】来源新浪；无 live 报价时禁止编造。",
        calendar_clock_line(),
    ]
    any_live = False
    for sym, _m, name in specs:
        q = quotes.get(sym)
        if not q or not getattr(q, "live", True) or not q.price:
            lines.append(f"- {name}：暂无真实报价（勿编造）")
            continue
        any_live = True
        pts = "—"
        if q.prev_close and q.prev_close > 0:
            pts = f"{q.price - q.prev_close:+.2f}"
        tag = freshness_label(q.as_of, venue="a_share")
        line = (
            f"- {name}（{sym}）现价 {q.price:.2f} · 涨跌 {pts} 点 · {_fmt_pct(q.change_pct)}"
            f" · [{tag}]"
        )
        if q.as_of:
            line += f" · {q.as_of}"
        lines.append(line)
    if not any_live:
        lines.append("（接口失败，请让用户看「市场」页；禁止猜测点位）")
    return "\n".join(lines)


def _tool_quote(symbol: str, market: str) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    norm_sym, norm_mkt = _resolve_sym_mkt(symbol, market)
    q = get_quote(norm_sym, norm_mkt or "SH")
    if not q or not getattr(q, "live", True) or not q.price:
        return f"{norm_sym} 暂无真实报价（勿编造）"
    return (
        f"【行情】{q.name or norm_sym}（{q.symbol} {q.market}）"
        f"现价 {q.price} · 涨跌幅 {_fmt_pct(q.change_pct)}"
        + (f" · 昨收 {q.prev_close}" if q.prev_close else "")
        + _as_of_note(q)
    )


def _tool_intraday(symbol: str, market: str) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    sym, mkt = _resolve_sym_mkt(symbol, market)
    return format_intraday_summary(sym, mkt)


def _tool_kline(symbol: str, market: str, limit: int) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    sym, mkt = _resolve_sym_mkt(symbol, market)
    return format_kline_summary(sym, mkt, limit=max(5, min(int(limit or 30), 60)))


def _tool_sector(symbol: str, market: str) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    sym, mkt = _resolve_sym_mkt(symbol, market)
    return format_sector_summary(sym, mkt)


def _tool_portfolio(db: Session, user_id: int) -> str:
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    lines = [
        "【仓库】",
        (
            f"总市值 {_fmt_money(pf.total_market_value)} · "
            f"总成本 {_fmt_money(pf.total_cost)} · "
            f"累计 {_fmt_money(pf.total_pnl)}（{_fmt_pct(pf.total_pnl_pct)}） · "
            f"今日 {_fmt_money(pf.day_pnl)}（{_fmt_pct(pf.day_pnl_pct)}）"
        ),
    ]
    if not pf.holdings:
        lines.append("（暂无持仓）")
        return "\n".join(lines)
    ranked = sorted(pf.holdings, key=lambda h: float(h.market_value or 0), reverse=True)
    for h in ranked[:15]:
        lines.append(
            f"- {h.symbol} {h.name or ''} 市值{_fmt_money(h.market_value)} "
            f"盈亏{_fmt_money(h.pnl)}（{_fmt_pct(h.pnl_pct)}）今日{_fmt_pct(h.change_pct)}"
        )
    if len(ranked) > 15:
        lines.append(f"…另有 {len(ranked) - 15} 只")
    return "\n".join(lines)


def _tool_analysis_snapshot(db: Session, user_id: int) -> str:
    from app.services import analysis as analysis_svc
    from app.services.agent_context import _summarize_report

    job = analysis_svc.latest_job(db, user_id)
    if job is None:
        return "【分析报告】还没有跑完的分析。可先去「分析」页跑一趟；别编造报告内容。"
    report: dict[str, Any] | None = None
    if job.report_json:
        try:
            raw = json.loads(job.report_json)
            if isinstance(raw, dict):
                report = raw
        except json.JSONDecodeError:
            report = None
    lines = [
        "【分析报告】来自「分析」页最近一次完成结果（只读，不是刚跑的专家会）。",
        f"范围 {job.scope} · 档位 {job.degree} · 配方 {job.recipe_id}",
    ]
    lines.extend(_summarize_report(report))
    return "\n".join(lines)


def _tool_news(db: Session, user_id: int, *, kind: str, keyword: str, limit: int) -> str:
    lim = max(1, min(int(limit or 5), 8))
    kind = (kind or "market").strip().lower()
    items: list[Any] = []
    title = "新闻"

    if kind == "holdings":
        rows = (
            db.query(Holding.symbol)
            .filter(Holding.user_id == user_id)
            .order_by(Holding.id.asc())
            .all()
        )
        symbols = [str(r[0]) for r in rows if r and r[0]]
        items = get_holdings_news(symbols, limit=lim)
        title = "持仓相关新闻"
    elif kind == "keyword":
        kw = (keyword or "").strip()
        if not kw:
            return "keyword 搜索需要提供 keyword"
        items = get_interests_news([kw], limit=lim)
        title = f"关键词「{kw}」新闻"
    else:
        _t, items = get_market_news(limit=lim, board="headline")
        title = "市场要闻"

    lines = [f"【{title}】"]
    if not items:
        lines.append("（暂无条目）")
        return "\n".join(lines)
    for it in items[:lim]:
        src = getattr(it, "source", "") or ""
        t = getattr(it, "title", "") or ""
        summary = (getattr(it, "summary", "") or "")[:80]
        lines.append(f"- {t}" + (f" · {src}" if src else "") + (f" — {summary}" if summary else ""))
    return "\n".join(lines)


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}
