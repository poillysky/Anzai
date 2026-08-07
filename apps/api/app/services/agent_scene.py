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
    "能买吧",
    "能卖吗",
    "买吗",
    "卖吗",
    "追买",
    "追吗",
    "今天买",
    "现在买",
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
        # 仅明确仓位语义才塞仓库；纯盘面/指数不再默默注入持仓
        if "macro" in self.flags and self.flags.isdisjoint(
            {"portfolio", "stock", "market"}
        ):
            return False
        if "portfolio" in self.flags:
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
            if self.wants_advice:
                return (
                    "篇幅：先一句买卖倾向，再用工具里的价嵌进两三句人话，收一句风险；"
                    "禁止小标题、分点清单、编造未查品种。"
                )
            return "篇幅：把今天金价/商品说清楚（可三四句），别只丢一个短词就停，也别写成行情看板。"
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

    if any(h in text for h in tools._PORTFOLIO_HINTS) or any(
        h in text
        for h in (
            "亏了吗",
            "赚了吗",
            "账户",
            "咱们今天",
            "今天咋样",
            "今天怎么样",
            "今天怎样",
        )
    ):
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
            "只答商品/宏观；零提账户、持仓、分散、ETF配置。"
            "数字只引用本轮宏观工具里的品种；"
            "黄金：没点名时默认积存金（工行/浙商/民生），不要反问 ETF/AU9999；"
            "已点名积存金/ETF/现货则只答点名的；不要默认念 AU9999 整板；"
            "禁止补编美元指数、美债、COMEX、沪金连续等未查项；别列行情看板。"
            "两三句说完；若本轮有相关新闻：优先「今日」，旧闻只当背景一句带过。"
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
        bits.append(
            "分析：先分清进行中还是已完成；进行中别编结论；"
            "已完成可带一句报告摘要，别假装这轮刚开完专家会。"
        )
    elif scene.primary == "news":
        bits.append(
            "新闻：一两句要点；点明新旧（今日/几天前）；别借机推销调仓。"
        )

    if "macro" in scene.flags and "portfolio" in scene.flags:
        bits.append("同时问了宏观和仓：先行情，仓位一句带过即可。")
    if scene.wants_advice:
        bits.append(
            "【建议】对方在问买卖/操作：先一句倾向（观望/可轻仓/宜减不宜加/别追），"
            "依据必须来自本轮工具数字，再用一两句人话带出，最后一句风险；用「可以考虑」。"
            "禁止保证赚钱、立刻全仓、必须卖掉、假装已下单；"
            "禁止「操作建议」编号清单和研报四段体。"
        )
    elif scene.primary in {"stock", "portfolio", "analysis", "market"}:
        bits.append(
            "若顺口给操作看法，用温和建议口吻；没问买卖就不要硬推调仓。"
        )
    bits.append(scene.length_guidance)
    return "".join(bits)


def tool_hint_for_scene(scene: TurnScene) -> str:
    """Slim tool reminder — only name tools relevant to this scene."""
    from app.providers.macro import calendar_clock_line

    base = "【工具】数字只引自【本轮实时查询】或工具；没有就说没有，禁止编造。"
    by: dict[str, str] = {
        "chat": "闲聊一般不用工具。",
        "macro": (
            "宏观用 get_macro（总览可传 overview；黄金=App 黄金页同款品种）；"
            "相关消息用 get_news keyword=中文主题（黄金/原油等）；"
            "问追不追可用 search_knowledge；"
            "看清日期标签；结论只引用返回里的品种与数字，勿补 DXY/美债/沪金连续。"
        ),
        "market": (
            "盘面：指数用 get_indices（含恒生/纳斯达克与开盘状态）；"
            "榜单用 get_leaders（可指定港股/美股）；账户最多一句体感；别念持仓清单。"
            + calendar_clock_line()
        ),
        "stock": (
            "没代码先 search_symbol；再 get_quote / get_intraday / get_kline / get_sector / get_news；"
            "港股五位代码（如 00700）可查报价/分时/日K，不进仓库、不开 start_analysis；"
            "问盘口、挂单、资金流入流出、主力用 get_depth_flow（主力=金额分档，禁止说庄家；港股无此分档）；"
            "买卖纪律可 search_knowledge。"
        ),
        "portfolio": "仓库用 get_portfolio；分散/过重可 search_knowledge + draft_rebalance_plan。",
        "leaders": "榜单用 get_leaders（board=沪/深/创业/恒生/纳斯达克）。",
        "analysis": (
            "读结论用 get_analysis_snapshot；"
            "明确开跑：仓库→start_analysis(portfolio)；"
            "个股/基金/黄金「帮我分析XX」→start_analysis(symbol)；仓库巡检覆盖股票+基金+黄金。"
        ),
        "news": "新闻用 get_news（可指定 board=tech/energy/finance 等；keyword 用中文）。",
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


def build_scene_context(
    db: Session,
    user_id: int,
    scene: TurnScene,
    *,
    conversation_id: int | None = None,
) -> str:
    from app.services import analysis as analysis_svc
    from app.services.agent_context import (
        analysis_context,
        analysis_follow_context,
        silent_portfolio_context,
    )

    parts = [scene_hint(scene)]
    if scene.include_portfolio:
        parts.append(silent_portfolio_context(db, user_id))

    follow = None
    try:
        follow = analysis_follow_context(
            db, user_id, conversation_id=conversation_id
        )
    except Exception:
        follow = None

    if follow:
        # Within post-analysis window: carry detailed summary every turn
        parts.append(follow)
    elif scene.include_analysis:
        parts.append(analysis_context(db, user_id))
    elif scene.primary != "chat":
        # 非闲聊：若有进行中或刚完成的分析，后面对话自动带上（不阻塞）
        try:
            if (
                analysis_svc.running_job(db, user_id) is not None
                or analysis_svc.latest_job(db, user_id) is not None
            ):
                parts.append(analysis_context(db, user_id))
        except Exception:
            pass
    if not scene.include_portfolio and not scene.include_analysis and not follow:
        if len(parts) == 1:
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
    conversation_id: int | None = None,
    max_context: int | None = None,
    reserve_for_reply: int | None = None,
) -> tuple[TurnScene, list[dict[str, Any]], dict[str, Any]]:
    """Build openai message list for this turn (sans prefetch block).

    Returns (scene, messages, assemble_meta) — meta for preview/debug.
    """
    from app.services import agent_knowledge_packs as packs_svc
    from app.services import agent_memory as memory_svc
    from app.services import agent_tokens as tokens_svc

    last_user = ""
    for m in reversed(messages):
        if (m.get("role") or "").strip() == "user":
            last_user = (m.get("content") or "").strip()
            break

    scene = detect_turn_scene(last_user)
    hist = filter_history(messages, scene, default_limit=history_messages)

    from app.services import agent_clarify as clarify_svc

    clarify = clarify_svc.detect_clarify_need(last_user, history=hist)

    openai_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": tool_hint_for_scene(scene)},
        {
            "role": "system",
            "content": build_scene_context(
                db, user_id, scene, conversation_id=conversation_id
            ),
        },
    ]

    if clarify:
        openai_msgs.append(
            {"role": "system", "content": clarify_svc.format_clarify_block(clarify)}
        )
    elif clarify_svc.detect_gold_default_jicun(last_user):
        openai_msgs.append(
            {
                "role": "system",
                "content": (
                    "【本轮·黄金默认积存金】用户只说了黄金/金价，未点名品种。"
                    "按积存金（工行/浙商/民生）回答，不要反问 ETF 或 AU9999，不要念全板。"
                ),
            }
        )

    memory_summary = ""
    if conversation_id:
        memory_summary = memory_svc.get_conversation_memory(db, user_id, conversation_id)
        mem_block = memory_svc.format_memory_block(memory_summary)
        if mem_block:
            openai_msgs.append({"role": "system", "content": mem_block})

    # Clarify turn: skip knowledge packs (they push answering tips)
    packs: list[Any] = []
    if not clarify:
        packs = packs_svc.scan_knowledge_packs(last_user)
        packs_block = packs_svc.format_packs_block(packs)
        if packs_block:
            openai_msgs.append({"role": "system", "content": packs_block})

    openai_msgs.extend(hist)

    ctx = int(max_context or tokens_svc.DEFAULT_MAX_CONTEXT)
    reserve = int(reserve_for_reply if reserve_for_reply is not None else scene.max_tokens)
    if clarify:
        reserve = min(reserve, 512)
    before_tokens = tokens_svc.estimate_messages_tokens(openai_msgs)
    openai_msgs = tokens_svc.trim_messages_to_budget(openai_msgs, ctx, reserve)
    after_tokens = tokens_svc.estimate_messages_tokens(openai_msgs)

    meta = {
        "scene": scene.primary,
        "scene_flags": sorted(scene.flags),
        "clarify": {"kind": clarify.kind, "ask": clarify.ask} if clarify else None,
        "history_kept": sum(
            1 for m in openai_msgs if (m.get("role") or "") in {"user", "assistant"}
        ),
        "packs": packs_svc.packs_debug(packs),
        "has_memory": bool(memory_summary),
        "memory_chars": len(memory_summary),
        "has_analysis_follow": any(
            "【近期分析·详细摘要】" in str(m.get("content") or "")
            for m in openai_msgs
            if (m.get("role") or "") == "system"
        ),
        "tokens_before_trim": before_tokens,
        "tokens_after_trim": after_tokens,
        "max_context": ctx,
        "reserve_for_reply": reserve,
    }
    return scene, openai_msgs, meta
