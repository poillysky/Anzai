"""Keyword-activated finance knowledge packs (BrewStory WI-lite, not RAG)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.agent_tokens import estimate_tokens

# Soft budget for all activated packs combined.
PACK_TOKEN_BUDGET = 700


@dataclass(frozen=True)
class KnowledgePack:
    id: str
    keys: tuple[str, ...]
    content: str
    priority: int = 50  # lower = earlier / more important


_PACKS: tuple[KnowledgePack, ...] = (
    KnowledgePack(
        id="compliance",
        priority=10,
        keys=(
            "该不该买",
            "要不要买",
            "能不能买",
            "全仓",
            "梭哈",
            "稳赚",
            "保证赚钱",
            "必涨",
            "荐股",
            "跟单",
            "内幕",
            "马上卖掉",
            "立刻买入",
        ),
        content=(
            "合规口径：给倾向可以，禁止保证收益/全仓梭哈/假装已下单；"
            "不编造未查到的价与仓位；结尾留一句风险即可，勿恐吓也不要鸡汤。"
        ),
    ),
    KnowledgePack(
        id="gold_discipline",
        priority=20,
        keys=(
            "黄金",
            "金价",
            "金条",
            "积存金",
            "纸黄金",
            "AU9999",
            "黄金ETF",
            "首饰金",
        ),
        content=(
            "黄金口径：只说「黄金/金价」时先问积存金、ETF 还是现货 AU9999，再答；"
            "已点名则只说点名品种；整段最多 1～2 个价格；别念看板；"
            "页面没有的叫法（沪金99、黄金连续等）禁止使用。"
        ),
    ),
    KnowledgePack(
        id="portfolio_words",
        priority=30,
        keys=("仓位", "仓库", "持仓", "今天盈亏", "浮盈", "浮亏", "重仓", "分散", "调仓"),
        content=(
            "仓库口径：分清「今日盈亏 day_pnl」与「行情涨跌 quote_chg」，勿混说「今天涨了」；"
            "短线标签是动量倾向不是预测；没查到仓位就说没查到，别估。"
        ),
    ),
    KnowledgePack(
        id="hk_us_limits",
        priority=40,
        keys=("港股", "美股", "纳斯达克", "恒生", "夜盘", "盘前", "盘后", "ADR"),
        content=(
            "港美口径：时区与交易时段说清楚；指数用 App 五指数口径；"
            "缺报价就老实说，别用过时印象价。"
        ),
    ),
    KnowledgePack(
        id="fund_basics",
        priority=45,
        keys=("基金", "定投", "货币基金", "纯债", "A类", "C类", "QDII", "跟踪误差", "场外"),
        content=(
            "基金口径：费率/A·C 类差异用人话点到即可；定投不是稳赚；"
            "清盘/暂停申赎等以工具与知识库为准，勿编规则细节。"
        ),
    ),
    KnowledgePack(
        id="convertible_bond",
        priority=48,
        keys=("可转债", "转债", "强赎", "下修", "转股价"),
        content=(
            "转债口径：强赎/下修是规则事件不是保证赚钱；波动可大于正股；"
            "没查到条款就别假装记得。"
        ),
    ),
    KnowledgePack(
        id="anti_fraud",
        priority=15,
        keys=("加微信", "带单", "稳赚不赔", "内幕消息", "老师带你", "私钥", "助记词", "转账验证"),
        content=(
            "反诈：凡「加群带单/交保证金/要验证码或助记词」一律劝停；"
            "安崽不会要转账，也不会承诺稳赚。"
        ),
    ),
)


def scan_knowledge_packs(
    text: str,
    *,
    budget_tokens: int = PACK_TOKEN_BUDGET,
) -> list[KnowledgePack]:
    """Activate packs whose keys hit user text; fill by priority until budget."""
    hay = (text or "").strip()
    if not hay:
        return []
    hits = [p for p in _PACKS if any(k in hay for k in p.keys)]
    hits.sort(key=lambda p: (p.priority, p.id))
    chosen: list[KnowledgePack] = []
    used = 0
    for p in hits:
        cost = estimate_tokens(p.content) + 8
        if chosen and used + cost > budget_tokens:
            break
        if not chosen and cost > budget_tokens:
            chosen.append(p)
            break
        chosen.append(p)
        used += cost
    return chosen


def format_packs_block(packs: list[KnowledgePack]) -> str:
    if not packs:
        return ""
    lines = ["【口径提示】本轮按关键词启用，请遵守："]
    for p in packs:
        lines.append(f"- {p.content}")
    return "\n".join(lines)


def packs_debug(packs: list[KnowledgePack]) -> list[dict[str, Any]]:
    return [{"id": p.id, "priority": p.priority, "keys_hit": True} for p in packs]
