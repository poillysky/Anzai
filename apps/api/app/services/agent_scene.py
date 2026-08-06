"""Turn scene router — intent → what context/tools to feed 安崽.

Always-on persona stays thin; each turn gets a scene packet instead of
one fixed mega-template (warehouse + bans + skill every time).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.orm import Session

SceneId = Literal[
    "chat",
    "macro",
    "market",
    "stock",
    "portfolio",
    "leaders",
    "analysis",
    "news",
]

_PITCH_MARKS = ("市值", "雪人", "分散", "满仓", "黄金ETF", "您说呢", "压舱", "跑得")

# 用户在要倾向性买卖/调仓建议（不是闲聊）
_ADVICE_HINTS = (
    "该不该买",
    "该不该卖",
    "要不要买",
    "要不要卖",
    "值得买",
    "值不值得",
    "买点",
    "卖点",
    "加仓",
    "减仓",
    "清仓",
    "调仓",
    "怎么买",
    "怎么卖",
    "出手",
    "建议买",
    "建议卖",
    "买卖建议",
    "操作建议",
    "能买吗",
    "能卖吗",
    "要不要减",
    "要不要加",
)

_CHAT_ONLY = frozenset(
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
    }
)


@dataclass(frozen=True)
class TurnScene:
    """Detected intent for one user turn."""

    primary: SceneId
    flags: frozenset[str]
    text: str

    @property
    def include_portfolio(self) -> bool:
        # 宏观-only 绝不带仓；盘面/仓位/个股+明确问仓才带
        if "macro" in self.flags and self.flags.isdisjoint(
            {"portfolio", "stock", "market"}
        ):
            return False
        if "portfolio" in self.flags:
            return True
        if "market" in self.flags and "macro" not in self.flags:
            return True
        return False

    @property
    def include_analysis(self) -> bool:
        return "analysis" in self.flags or (
            "portfolio" in self.flags and "macro" not in self.flags
        )

    @property
    def wants_advice(self) -> bool:
        return any(h in self.text for h in _ADVICE_HINTS)

    @property
    def include_analyst_skill(self) -> bool:
        if self.wants_advice and self.flags & {
            "stock",
            "analysis",
            "portfolio",
            "market",
            "macro",
        }:
            return True
        return bool(self.flags & {"stock", "analysis", "portfolio"})

    @property
    def history_limit(self) -> int | None:
        if self.primary == "chat":
            return 8
        if self.primary == "macro":
            return 4
        if self.primary in {"leaders", "news"}:
            return 6
        return None  # use preset default

    @property
    def scrub_pitch_history(self) -> bool:
        return self.primary in {"macro", "chat", "leaders", "news"}

    @property
    def max_tokens(self) -> int:
        """Per-turn ceiling — 够说清；闲聊短一点，个股/分析放宽。"""
        # 对齐 BrewStory openai_max_tokens≈2048 量级；场景略分档
        base = {
            "chat": 1024,
            "macro": 2048,
            "news": 1536,
            "leaders": 2048,
            "market": 2048,
            "portfolio": 2048,
            "analysis": 3072,
            "stock": 3072,
        }.get(self.primary, 2048)

        n = len(self.text)
        if n >= 36:
            base = int(base * 1.1)
        if n >= 72:
            base = int(base * 1.1)
        deep = ("分析", "怎么看", "详细", "走势", "分时", "对比", "为什么", "值不值得")
        if any(k in self.text for k in deep):
            base += 512
        if self.wants_advice:
            base += 512
        if len(self.flags) >= 2:
            base += 256
        cap = {
            "chat": 2048,
            "macro": 4096,
            "news": 3072,
            "leaders": 4096,
            "market": 4096,
            "portfolio": 4096,
            "analysis": 6144,
            "stock": 6144,
        }.get(self.primary, 4096)
        return max(1024, min(base, cap))

    @property
    def length_guidance(self) -> str:
        """Tell the model how long to write — not a fixed script."""
        if self.primary == "chat":
            return "篇幅：两三句人话即可，别只回两三个字。"
        if self.primary == "macro":
            return "篇幅：把今天金价/商品说清楚（可三四句），别只丢一个短词就停。"
        if self.primary == "news":
            return "篇幅：一两句要点，说清就行。"
        if self.primary == "leaders":
            return "篇幅：点几只领涨/领跌并带一句体感，别写成榜单全文。"
        if self.primary == "market":
            return "篇幅：三四句；指数为主，账户最多一句。"
        if self.primary == "portfolio":
            return "篇幅：说清盈亏/集中度，三四句到五六句都可以。"
        if self.primary == "analysis":
            return "篇幅：结论 + 一两句依据，别复述整份报告。"
        if self.primary == "stock":
            if self.wants_advice or any(
                k in self.text for k in ("详细", "分析", "怎么看", "分时", "走势")
            ):
                return "篇幅：把该说的点说清（可稍长），含一句建议与风险，别灌水也别只回半句。"
            return "篇幅：三四句说清现价、强弱和体感，别只回几个字。"
        return "篇幅：问什么答什么，说清楚再停，别只回两三个字。"


def detect_turn_scene(user_text: str) -> TurnScene:
    """Rule-based intent (no extra LLM). Shared by prefetch + context packing."""
    # Local imports avoid circular import at module load
    from app.providers.macro import topics_mentioned
    from app.services import agent_tools as tools

    text = (user_text or "").strip()
    flags: set[str] = set()

    if not text:
        return TurnScene(primary="chat", flags=frozenset({"chat"}), text=text)

    compact = re.sub(r"\s+", "", text)
    if compact in _CHAT_ONLY or text in _CHAT_ONLY:
        return TurnScene(primary="chat", flags=frozenset({"chat"}), text=text)

    macro_topics = topics_mentioned(text)
    if macro_topics:
        flags.add("macro")

    if any(h in text for h in tools._PORTFOLIO_HINTS):
        flags.add("portfolio")
    if any(h in text for h in tools._PULSE_HINTS) or any(
        h in text for h in tools._INDEX_HINTS
    ):
        flags.add("market")
    if any(h in text for h in tools._LEADERS_HINTS):
        flags.add("leaders")
    if any(h in text for h in tools._ANALYSIS_HINTS):
        flags.add("analysis")
    if any(h in text for h in tools._NEWS_HINTS):
        flags.add("news")

    has_code = bool(re.search(r"(?<!\d)\d{6}(?!\d)", text))
    # 榜单/报告/新闻/宏观优先，勿把整句当票名
    block_name = bool(
        flags & {"macro", "leaders", "analysis", "news"}
    ) or (
        "portfolio" in flags and not has_code
    )
    name_q = ""
    if not has_code and not block_name:
        name_q = tools._extract_name_query(text)

    if has_code:
        flags.add("stock")
    elif (
        name_q
        and name_q not in tools._CHAT_BLOCK
        and (
            any(k in text for k in tools._DEEP_ASK)
            or text.strip() == name_q
            or len(text) <= 24
        )
    ):
        flags.add("stock")

    if not flags:
        flags.add("chat")

    primary = _pick_primary(flags)
    return TurnScene(primary=primary, flags=frozenset(flags), text=text)


def _pick_primary(flags: set[str]) -> SceneId:
    # 更具体的意图优先；stock 放中后，避免盖住榜单/报告
    order: list[SceneId] = [
        "macro",
        "leaders",
        "analysis",
        "portfolio",
        "stock",
        "market",
        "news",
        "chat",
    ]
    for sid in order:
        if sid in flags:
            return sid
    return "chat"


def scene_hint(scene: TurnScene) -> str:
    """Short per-scene instruction — not a global ban essay."""
    from app.providers.macro import calendar_clock_line

    bits = [
        f"【本轮场景 · {scene.primary}】",
        "表达：口语、有判断；数字禁止 Markdown 加粗（不要成对星号包数字）。",
    ]
    if scene.primary == "chat":
        bits.append("闲聊：正常人回话，几乎不提行情和仓库。")
    elif scene.primary == "macro":
        bits.append(
            "只答商品/宏观一两句人话；零提账户、持仓、分散、ETF配置。"
            + calendar_clock_line()
        )
    elif scene.primary == "market":
        bits.append(
            "盘面：指数为主，账户最多一句体感；别念持仓清单。"
            + calendar_clock_line()
        )
    elif scene.primary == "stock":
        bits.append(
            "个股：心里想深一点，嘴上白话；数字嵌进句子；没问仓位就别提仓库和分散。"
            + calendar_clock_line()
        )
    elif scene.primary == "portfolio":
        bits.append("仓库：可谈盈亏/集中度，别推销具体产品，别「您说呢」。")
    elif scene.primary == "leaders":
        bits.append("榜单：用查询结果白话说谁强谁弱，别扯个人仓位。")
    elif scene.primary == "analysis":
        bits.append("分析结论：引用报告摘要，别假装刚开完专家会。")
    elif scene.primary == "news":
        bits.append("新闻：一两句要点，别借机推销调仓。")

    if "macro" in scene.flags and "portfolio" in scene.flags:
        bits.append("同时问了宏观和仓：先行情，仓位一句带过即可。")
    if scene.wants_advice:
        bits.append(
            "【建议】对方在问买卖/操作：给倾向性建议（观望/可轻仓/宜减不宜加等）"
            "+一句依据+一句风险；用「可以考虑」；"
            "禁止保证赚钱、立刻全仓、必须卖掉、假装已下单。"
        )
    elif scene.primary in {"stock", "portfolio", "analysis", "market"}:
        bits.append(
            "若顺口给操作看法，用温和建议口吻；没问买卖就不要硬推调仓。"
        )
    bits.append(scene.length_guidance)
    return "".join(bits)


def tool_hint_for_scene(scene: TurnScene) -> str:
    """Slim tool reminder — only name tools relevant to this scene."""
    base = "【工具】数字只引自【本轮实时查询】或工具；没有就说没有，禁止编造。"
    by: dict[str, str] = {
        "chat": "闲聊一般不用工具。",
        "macro": "宏观用 get_macro；看清日期标签。",
        "market": "盘面用 get_indices；账户用 get_portfolio。",
        "stock": "没代码先 search_symbol；再 get_quote / get_intraday / get_kline / get_sector / get_news。",
        "portfolio": "仓库用 get_portfolio。",
        "leaders": "榜单用 get_leaders。",
        "analysis": "报告用 get_analysis_snapshot。",
        "news": "新闻用 get_news。",
    }
    extra = by.get(scene.primary, "")
    # multi-flag extras
    more: list[str] = []
    if "leaders" in scene.flags and scene.primary != "leaders":
        more.append("get_leaders")
    if "analysis" in scene.flags and scene.primary != "analysis":
        more.append("get_analysis_snapshot")
    if "news" in scene.flags and scene.primary != "news":
        more.append("get_news")
    if more:
        extra += "还可：" + " / ".join(more) + "。"
    return f"{base}{extra}"


def build_scene_context(db: Session, user_id: int, scene: TurnScene) -> str:
    from app.services.agent_context import (
        analysis_context,
        silent_portfolio_context,
    )

    parts = [scene_hint(scene)]
    if scene.include_portfolio:
        parts.append(silent_portfolio_context(db, user_id))
    if scene.include_analysis:
        parts.append(analysis_context(db, user_id))
    if not scene.include_portfolio and not scene.include_analysis:
        parts.append(
            "【数据说明】本轮以【本轮实时查询】为准；未附仓库/报告，勿自行编造账户数字。"
        )
    return "\n\n".join(parts)


def filter_history(
    messages: list[dict[str, Any]],
    scene: TurnScene,
    *,
    default_limit: int,
) -> list[dict[str, Any]]:
    limit = scene.history_limit if scene.history_limit is not None else default_limit
    cleaned: list[dict[str, Any]] = []
    for m in messages:
        role_m = (m.get("role") or "").strip()
        content = (m.get("content") or "").strip()
        if role_m not in {"user", "assistant"} or not content:
            continue
        if (
            scene.scrub_pitch_history
            and role_m == "assistant"
            and any(k in content for k in _PITCH_MARKS)
        ):
            continue
        cleaned.append({"role": role_m, "content": content[:8000]})
    return cleaned[-limit:]


def assemble_turn(
    db: Session,
    user_id: int,
    messages: list[dict[str, Any]],
    *,
    system_prompt: str,
    history_messages: int,
) -> tuple[TurnScene, list[dict[str, Any]]]:
    """Build openai message list for this turn (sans prefetch block)."""
    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            last_user = (m.get("content") or "").strip()
            break

    scene = detect_turn_scene(last_user)
    hist = filter_history(messages, scene, default_limit=history_messages)
    openai_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": tool_hint_for_scene(scene)},
        {"role": "system", "content": build_scene_context(db, user_id, scene)},
    ]
    openai_msgs.extend(hist)
    return scene, openai_msgs
