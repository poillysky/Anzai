"""Ambiguous-intent clarify-first — ask before answering (human chat feel)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Subtypes that make「黄金」specific enough to answer directly
_GOLD_SPECIFIC = (
    "积存金",
    "积存",
    "纸黄金",
    "黄金etf",
    "黄金ETF",
    "etf",
    "ETF",
    "au9999",
    "AU9999",
    "现货",
    "上金所",
    "门店",
    "金店",
    "首饰",
    "金条",
    "投资金条",
    "回收",
    "纽约金",
    "伦敦金",
    "国际金",
    "美元金",
)

_GOLD_BARE = (
    "黄金",
    "金价",
    "买金",
    "炒金",
    "金怎么样",
    "金咋样",
    "金如何",
)

_PORTFOLIO_BARE = (
    "仓",
    "仓库",
    "持仓",
    "我的仓",
    "咱们仓",
    "看看仓",
    "翻翻仓",
)

_PORTFOLIO_SPECIFIC = (
    "盈亏",
    "亏了",
    "赚了",
    "浮盈",
    "浮亏",
    "调仓",
    "分散",
    "重仓",
    "今天",
    "市值",
    "分析",
    "该不该",
    "要不要",
)

_STOCK_DEEP = (
    "分析",
    "怎么看",
    "该不该",
    "要不要",
    "买",
    "卖",
    "追",
    "加仓",
    "减仓",
    "现价",
    "多少钱",
    "涨跌",
    "分时",
    "走势",
    "新闻",
    "详",
)

_CHAT_SHORT = frozenset(
    {
        "你好",
        "您好",
        "在吗",
        "早上好",
        "晚安",
        "谢谢",
        "哈哈",
        "好的",
        "收到",
        "今天",
        "明天",
        "怎么样",
        "怎样",
        "啥意思",
    }
)


@dataclass(frozen=True)
class ClarifyNeed:
    kind: str
    """Short id: gold | portfolio | stock"""
    ask: str
    """One WeChat-style clarifying question for the model to ask."""


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _recent_assistant_asked_clarify(history: list[dict[str, Any]] | None) -> bool:
    """If we just asked a fork question, treat short replies as answers not new ambiguity."""
    if not history:
        return False
    for m in reversed(history):
        if (m.get("role") or "").strip() != "assistant":
            continue
        content = m.get("content") or ""
        if not isinstance(content, str):
            continue
        # Our clarify style: 「还是」+ 选项词
        if "还是" in content and any(
            k in content for k in ("积存", "ETF", "现货", "现价", "分析", "盈亏", "调仓")
        ):
            return True
        return False
    return False


def _gold_ambiguous(text: str) -> bool:
    t = text.strip()
    c = _compact(t).lower()
    if not c:
        return False
    if any(s.lower() in c for s in _GOLD_SPECIFIC):
        return False
    # Bare / near-bare gold asks
    if c in {"黄金", "金价", "金", "买金", "炒金", "黄金呢", "金价呢", "黄金啊", "黄金呀"}:
        return True
    if len(c) <= 12 and any(b in t for b in ("黄金", "金价")):
        # 「黄金怎么样」「今天金价」仍可能多义
        if any(x in t for x in ("油", "股", "仓", "大盘", "指数")):
            return False
        return True
    if any(b in t for b in _GOLD_BARE) and len(c) <= 16:
        return not any(s.lower() in c for s in _GOLD_SPECIFIC)
    return False


def _portfolio_ambiguous(text: str) -> bool:
    c = _compact(text)
    if c not in _PORTFOLIO_BARE and c not in {"看看仓库", "翻翻仓库", "我的仓库", "咱们仓库"}:
        return False
    return not any(s in text for s in _PORTFOLIO_SPECIFIC)


def _stock_name_ambiguous(text: str) -> bool:
    """Bare short Chinese name with no ask intent — e.g. 「茅台」."""
    t = (text or "").strip()
    c = _compact(t)
    if not t or len(c) > 8:
        return False
    if re.search(r"\d", c):
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", c):
        return False
    if c in _CHAT_SHORT:
        return False
    if any(k in t for k in _STOCK_DEEP):
        return False
    if any(k in t for k in ("黄金", "金价", "仓", "大盘", "指数", "新闻", "榜", "基金", "债")):
        return False
    return True


def detect_clarify_need(
    user_text: str,
    *,
    history: list[dict[str, Any]] | None = None,
) -> ClarifyNeed | None:
    """Return clarify need when the user turn is too vague to answer well."""
    text = (user_text or "").strip()
    if not text:
        return None
    if _recent_assistant_asked_clarify(history):
        # User is likely picking an option — answer this turn
        return None

    if _gold_ambiguous(text):
        return ClarifyNeed(
            kind="gold",
            ask=(
                "黄金啊——你是想看积存金、黄金 ETF，还是上金所现货价（AU9999）？"
                "说一下你关心哪头，安崽再按那个答。"
            ),
        )
    if _portfolio_ambiguous(text):
        return ClarifyNeed(
            kind="portfolio",
            ask="仓库呀——你是想看今天盈亏，还是想聊要不要调仓、散不散？",
        )
    if _stock_name_ambiguous(text):
        name = text.strip()
        return ClarifyNeed(
            kind="stock",
            ask=f"{name}啊——你是想先看现价走势，还是想听该不该买、怎么看？",
        )
    return None


def format_clarify_block(need: ClarifyNeed) -> str:
    return (
        "【本轮·先问清楚】用户这句太笼统，不要直接给完整行情或建议。\n"
        f"用一两句微信口语反问即可，参考：「{need.ask}」\n"
        "可以轻微改写，但必须只问一个分叉，禁止念看板、禁止堆价格、禁止工具复读。\n"
        "本轮不要调用查询工具；等对方点明后再查再答。"
    )


def should_skip_prefetch(need: ClarifyNeed | None) -> bool:
    return need is not None
