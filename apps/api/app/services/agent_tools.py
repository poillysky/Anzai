"""安崽 chat tools — on-demand quotes / portfolio / news (no hallucinated numbers)."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models import Holding
from app.providers.intraday import format_intraday_summary
from app.providers.kline import format_kline_summary
from app.providers.depth_flow import format_depth_flow_summary
from app.providers.leaders import format_leaders_summary
from app.providers.macro import format_macro_text, list_topics, news_keyword_for_topic
from app.providers.news import (
    MARKET_BOARDS,
    NewsItem,
    format_news_digest,
    get_holdings_news,
    get_interests_news,
    get_market_news,
)
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
            "description": (
                "查询 App「股票」页同款指数：上证/深成/创业/恒生/纳斯达克，并附开盘状态。"
                "问大盘、港股指数、美股指数、点位涨跌时必须先调此工具。"
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro",
            "description": (
                "查询商品/宏观参考行情。"
                "问黄金时返回与 App「股票→黄金」页一致的品种（AU9999、积存金、博时/工银黄金ETF、"
                "纽约金、伦敦金、门店零售），不是期货连续合约。"
                "白银/原油/铜/铁矿/螺纹/豆粕/天然气/汇率等为对话补充源（App 无独立页），"
                f"主题：{_MACRO_HINT}；总览传 overview。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": (
                            "主题词，如 黄金、原油、汇率、螺纹钢、铁矿、豆粕、天然气、overview；"
                            "也可传 gold/oil/usd_cny 等 id"
                        ),
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
            "description": "查询单只股票或 ETF 实时行情（现价、涨跌幅）。支持 A 股六位与港股五位（如 00700）；港股可查不可入仓。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": (
                            "代码：A 股六位如 510300、600519；港股五位如 00700、03690。"
                            "问上证/深成/创业请用 get_indices；问黄金现货/纽约金/积存金请用 get_macro"
                        ),
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "HK"],
                        "description": "SH/SZ/HK；五位代码默认港股；不确定可省略",
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
            "description": "查询单只股票/ETF 分时走势摘要（高低、现价位置、大致上行/下行/震荡）。问走势、分时、盘中形态时必须调。A 股与港股均可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "代码，A 股六位或港股五位如 00700",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "HK"],
                        "description": "SH/SZ/HK；可省略",
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
            "description": "查询近 N 日 K 线摘要（OHLC、区间涨跌、MA5/10/20）。问历史走势、均线、近几日表现时必须调。A 股与港股均可。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "代码，A 股六位或港股五位",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "HK"],
                        "description": "SH/SZ/HK；可省略",
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
            "name": "get_depth_flow",
            "description": (
                "查询买卖五档盘口 + 近几日资金流向（主力/超大/大/中/小单净流入）。"
                "问挂单、盘口、资金流入流出、主力时必须调。"
                "注意：主力是成交额分档，不是庄家；禁止说庄家入场。"
                "港股无 A 股式五档/主力分档，会说明改用报价/分时/日K。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "代码，A 股六位或港股五位",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "HK"],
                        "description": "SH/SZ/HK；可省略",
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
            "description": "查询个股所属行业/概念板块及板块涨跌（主要 A 股）。问板块、同业、概念时必须调。",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "代码"},
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "HK"],
                        "description": "SH/SZ；港股会提示暂无 A 股式板块",
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
            "description": (
                "查询新闻：市场版块（要闻/国际等）、持仓相关、或关键词检索；返回已按持仓相关性筛选的摘要。"
                "聊黄金/原油等宏观时用 keyword=中文主题（黄金、原油），或 board=world；勿用英文 id。"
                "聊科技/能源/金融等板块新闻时 kind=market 并指定 board。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["market", "holdings", "keyword"],
                        "description": "market=版块要闻；holdings=持仓相关；keyword=按词搜",
                    },
                    "keyword": {
                        "type": "string",
                        "description": "kind=keyword 时的搜索词，如 半导体、白酒、黄金、江化微",
                    },
                    "board": {
                        "type": "string",
                        "enum": [b["id"] for b in MARKET_BOARDS],
                        "description": (
                            "kind=market 时的版块：headline要闻/tech科技/energy能源/"
                            "finance金融/agri农业/auto汽车/estate地产/industry产经/company公司；默认 headline"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "条数，默认 5，最大 10",
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
            "description": (
                "按名称或关键词搜股票/ETF/港股代码（如「茅台」「腾讯」「宁德」）。"
                "用户没报代码时先调此工具，再对命中代码查行情。港股可查行情，不进仓库。"
            ),
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
            "description": (
                "读取分析状态与最近结论：若正在跑会说明进行中；若已完成则摘要报告。"
                "问上次分析、报告结论、分析好了吗、巡检进度时调。"
                "正在跑时勿假装已有新结论；跑完后可带一句结论。"
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_analysis",
            "description": (
                "仅当用户明确要求立刻开跑分析时调用。"
                "仓库：「帮我分析今天仓库」「分析一下仓库」「仓库巡检」→ scope=portfolio"
                "（含股票/场内ETF/场外基金/黄金积存，全仓）。"
                "单票：「帮我分析茅台」「分析黄金ETF」「分析某某基金」→ scope=symbol，"
                "填 symbol/market/name；支持 SH/SZ/OF/JD/GDS(AU9999)；固定标准档。"
                "港股勿调本工具：用 get_quote / get_intraday / get_kline 聊天查行情即可，不进仓库、不开标准分析。"
                "禁止：问进度/上次结论/仓位散不散/闲聊提分析 → 只用 get_analysis_snapshot。"
                "后台执行、立即返回；已有任务在跑则提示等待。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["portfolio", "symbol"],
                        "description": "portfolio=仓库全仓；symbol=单标的（股/ETF/基金/黄金）",
                    },
                    "symbol": {
                        "type": "string",
                        "description": "代码：A股/ETF/场外基金六位，或积存金 sku；scope=symbol 必填",
                    },
                    "market": {
                        "type": "string",
                        "enum": ["SH", "SZ", "OF", "JD", "GDS"],
                        "description": "SH/SZ 场内；OF 场外基金；JD 积存金；GDS=AU9999 上金所现货",
                    },
                    "name": {
                        "type": "string",
                        "description": "简称（可选，便于话术）",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_leaders",
            "description": (
                "查询涨跌榜/成交额榜（与 App「股票」榜单同口径）。"
                "问谁涨得猛、跌幅榜、成交活跃、港股/美股榜时调。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "board": {
                        "type": "string",
                        "enum": [
                            "sh-composite",
                            "sz-component",
                            "chinext",
                            "hk-hsi",
                            "us-nasdaq",
                        ],
                        "description": "与股票页指数 Tab 一致：沪/深/创业/恒生/纳斯达克",
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
    {
        "type": "function",
        "function": {
            "name": "draft_rebalance_plan",
            "description": (
                "根据当前仓库生成调仓草案：点名过重仓、今日拖累/贡献，给倾向性建议"
                "（观望/宜减不宜加/可轻仓）。禁止假装已下单。用户问调仓、分散、减一点时调。"
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": (
                "检索安崽经验知识库（纪律/口吻/怎么看盘的框架，含基础与专业层）。"
                "问该不该追、仓位、规则时用；问实际利率、杜邦、久期、Brinson、隐含波动率等进阶概念时"
                "把 mode 设为 advanced。"
                "返回非实时经验，不能当行情或新闻；点位仍须用其它行情工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "检索词，如 追高、仓位分散、实际利率、杜邦分析",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "条数，默认 3，最大 8",
                    },
                    "mode": {
                        "type": "string",
                        "description": (
                            "auto|basic|advanced。"
                            "普通用户问答用 basic 或 auto；专业名词/宏观估值归因用 advanced。"
                            "默认 auto（按问法自动切换）。"
                        ),
                    },
                },
                "required": ["query"],
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
    "get_depth_flow": "查盘口资金",
    "get_sector": "查板块",
    "get_portfolio": "查仓库持仓",
    "get_news": "查新闻",
    "search_symbol": "搜股票代码",
    "get_analysis_snapshot": "读分析报告",
    "start_analysis": "启动分析",
    "get_leaders": "查涨跌榜",
    "draft_rebalance_plan": "调仓草案",
    "search_knowledge": "查经验库",
}

_KNOWLEDGE_HINTS = (
    "该不该追",
    "还追吗",
    "要不要追",
    "追高",
    "仓位重",
    "押太重",
    "分散一点",
    "怎么看仓",
    "宜减不宜加",
    "可轻仓",
    "积存金",
    "投资金条",
    "纸黄金",
    "黄金ETF",
    "适合买黄金",
    "首饰金",
    "黄金回收",
    "黄金骗局",
    "打新",
    "T+1",
    "融资融券",
    "指数基金",
    "市盈率",
    "ST股票",
    "荐股",
    "除权除息",
    "集合竞价",
    "货币基金",
    "纯债基金",
    "A类C类",
    "A类和C类",
    "红利再投资",
    "QDII",
    "基金清盘",
    "跟踪误差",
    "场外基金",
    "定投会不会亏",
    "可转债",
    "强赎",
    "银行理财",
    "大额存单",
    "储蓄国债",
    "增额寿",
    "年金险",
    "闲钱",
    "资产配置",
    "杀猪盘",
    "保本高收益",
    "追涨杀跌",
    "个人养老金",
    "实际利率",
    "杜邦",
    "Brinson",
    "久期",
    "隐含波动率",
    "风格漂移",
    "有效前沿",
    "行业分析",
    "板块轮动",
    "港股通",
    "美股",
    "REITs",
    "公募REITs",
    "私募基金",
)

_FLOW_HINTS = (
    "资金",
    "流入",
    "流出",
    "盘口",
    "挂单",
    "五档",
    "主力",
    "大单",
    "超大单",
)

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
    "恒生",
    "港股",
    "纳斯达克",
    "纳指",
    "美股",
)
# 账户体感脉冲（才拉仓库）；纯大盘/指数走 _INDEX_HINTS，不塞持仓
_PULSE_HINTS = (
    "今天咋样",
    "今天怎么样",
    "今天怎样",
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
    "咱们仓",
    "仓位",
    "亏在",
    "集中",
    "重仓",
    "调仓",
    "分散",
    "减仓",
    "加仓",
)
_NEWS_HINTS = ("新闻", "资讯", "消息", "要闻")
_ANALYSIS_HINTS = (
    "分析报告",
    "上次分析",
    "分析页",
    "报告结论",
    "跑完分析",
    "最近分析",
    "分析好了",
    "分析进度",
    "巡检进度",
    "巡检好了",
)
# 仅明确「要求开跑」口令才预取 start_analysis（读结论不走这里）
_START_ANALYSIS_HINTS = (
    "帮我分析今天仓库",
    "帮我分析一下仓库",
    "帮我分析下仓库",
    "帮我分析仓库",
    "帮我分析今天持仓",
    "帮我分析一下持仓",
    "帮我分析下持仓",
    "帮我分析持仓",
    "分析一下今天仓库",
    "分析下今天仓库",
    "分析今天仓库",
    "分析一下仓库",
    "分析下仓库",
    "分析仓库情况",
    "分析一下持仓",
    "分析下持仓",
    "重新分析仓库",
    "重新分析持仓",
    "再分析一遍仓库",
    "再分析一遍持仓",
    "跑个仓库分析",
    "跑一下仓库分析",
    "仓库巡检",
    "帮我巡检仓库",
    "启动仓库分析",
)
_LEADERS_HINTS = (
    "涨幅榜",
    "跌幅榜",
    "谁涨",
    "涨停",
    "跌得多",
    "成交额榜",
    "龙头",
    "领涨",
    "榜单",
)
_NAME_STRIP = (
    "怎么样",
    "怎样",
    "怎么看",
    "情况",
    "分析",
    "走势",
    "分时",
    "看看",
    "看下",
    "值得买吗",
    "值得吗",
    "咋样",
    "如何",
    "帮我查",
    "帮我看看",
    "帮我看",
    "查一下",
    "查下",
    "查询",
    "一下",
    "预估",
    "什么",
    "行情",
    "今天",
    "港股的",
    "港股",
    "A股的",
    "A股",
    "股价",
    "现价",
    "最新价",
    "报价",
    "价格",
    "多少钱",
    "多少",
    "现在",
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
    "看下",
    "查一下",
    "查下",
    "查询",
    "股价",
    "现价",
    "价格",
    "报价",
    "多少钱",
    "多少",
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

    from app.providers.search import clean_search_query

    q = clean_search_query(text or "")
    if not q:
        q = (text or "").strip()
        for w in _NAME_STRIP:
            q = q.replace(w, " ")
        q = re.sub(r"\s+", " ", q).strip()
    q = re.sub(r"(?<!\d)\d{6}(?!\d)", " ", q).strip()
    q = re.sub(r"(?<!\d)\d{5}(?!\d)", " ", q).strip()
    # drop pure pulse/macro words
    if q in {"今天", "行情", "大盘", "市场", "黄金", "白银", "原油", "港股", "A股"}:
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
        if mkt in {"SH", "SZ"}:
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
    symbols5 = re.findall(r"(?<!\d)(\d{5})(?!\d)", text)
    resolved: list[tuple[str, str, str]] = []  # symbol, market, name
    for sym in symbols:
        s, m = _resolve_sym_mkt(sym)
        resolved.append((s, m, ""))
    for sym in symbols5:
        s, m = _resolve_sym_mkt(sym, "HK")
        if not any(r[0] == s and r[1] == m for r in resolved):
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
        and not re.search(r"(?<!\d)\d{5}(?!\d)", text)
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
        from app.providers.search import clean_search_query

        cleaned = clean_search_query(text)
        want_name = name_q and name_q not in _CHAT_BLOCK and (
            any(k in text for k in _DEEP_ASK)
            or text.strip() == name_q
            # 「港股小米」「小米」等：去掉口语后只剩简称，也按名搜
            or (cleaned == name_q and 2 <= len(name_q) <= 12)
        )
        if not resolved and want_name:
            # 带「港」时用原句解析，便于 prefer HK
            hit = resolve_best_symbol(text if "港" in text else name_q)
            if hit:
                add("search_symbol", {"query": name_q, "limit": 5})
                resolved.append((hit.symbol, hit.market, hit.name))

    deep = bool(resolved) and (any(k in text for k in _DEEP_ASK) or len(text) <= 24)

    want_index = any(h in text for h in _INDEX_HINTS)
    # 账户体感才算脉冲；「大盘/指数」单独走指数，不拉仓库
    account_pulse = any(h in text for h in _PULSE_HINTS)

    if deep or resolved:
        add("get_indices", {})

    # 问黄金等宏观时：只拉宏观，别顺带塞仓库（否则模型必复读账户推销分散）
    if macro_topics and not resolved and not want_portfolio:
        pass
    elif want_portfolio or account_pulse:
        if want_index or account_pulse or want_portfolio:
            add("get_indices", {})
        add("get_portfolio", {})
        if any(k in text for k in ("调仓", "分散", "减仓", "加仓", "重仓")):
            add("draft_rebalance_plan", {})
    elif want_index and not resolved:
        add("get_indices", {})

    for sym, mkt, _nm in resolved[:2]:
        _add_symbol_deep(add, sym, mkt, deep=deep or len(resolved) == 1)
        if mkt in {"SH", "SZ"} and any(h in text for h in _FLOW_HINTS):
            add("get_depth_flow", {"symbol": sym, "market": mkt})

    if any(h in text for h in _ANALYSIS_HINTS):
        add("get_analysis_snapshot", {})
    started_analysis = False
    if any(h in text for h in _START_ANALYSIS_HINTS):
        add("start_analysis", {"scope": "portfolio"})
        started_analysis = True
        # 不预取 snapshot：本轮会等委员会跑完再注入结论
    else:
        stock_job = _explicit_symbol_analysis_target(text, db=db, user_id=user_id)
        if stock_job:
            add("start_analysis", stock_job)
            started_analysis = True
            sym = str(stock_job.get("symbol") or "")
            mkt = str(stock_job.get("market") or "SH")
            if sym:
                _add_symbol_deep(add, sym, mkt, deep=True)
                if (mkt or "").upper() in {"SH", "SZ"}:
                    add("get_depth_flow", {"symbol": sym, "market": mkt})

    # 进行中 / 刚跑完：拉分析状态（本轮刚 start 的由 wait 注入，勿抢跑空结论）
    if db is not None and user_id is not None and not started_analysis:
        try:
            from app.services import analysis as analysis_svc
            from app.services import analysis_pending as pending_svc

            if analysis_svc.running_job(db, user_id) is not None:
                add("get_analysis_snapshot", {})
            elif pending_svc.peek_pending_job_id(user_id) is not None:
                add("get_analysis_snapshot", {})
        except Exception:
            pass

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
        add(
            "get_leaders",
            {"board": _infer_leaders_board(text), "kind": kind, "limit": 8},
        )

    # 宏观主题：顺带拉中文关键词新闻（gold→黄金），别只搜英文 id
    if macro_topics and not resolved:
        for t in macro_topics[:2]:
            add(
                "get_news",
                {
                    "kind": "keyword",
                    "keyword": news_keyword_for_topic(t["id"]),
                    "limit": 5,
                },
            )

    if any(h in text for h in _NEWS_HINTS) and not resolved and not macro_topics:
        board = _infer_news_board(text)
        add("get_news", {"kind": "market", "board": board, "limit": 6})

    # 经验库：追高/仓位纪律/买卖口吻；宏观追问也顺带一条
    want_knowledge = any(h in text for h in _KNOWLEDGE_HINTS) or any(
        k in text for k in ("还追", "该不该买", "要不要买", "要不要减", "怎么说")
    )
    if macro_topics and any(k in text for k in ("追", "买", "加仓", "减")):
        want_knowledge = True
    if want_knowledge:
        add("search_knowledge", {"query": text[:80], "limit": 3})

    return plan


def _infer_leaders_board(text: str) -> str:
    """Map spoken market hints → App index / leaders board key."""
    if any(k in text for k in ("港股", "恒生", "港股通", "恒科")):
        return "hk-hsi"
    if any(k in text for k in ("美股", "纳斯达克", "纳指", "标普")):
        return "us-nasdaq"
    if any(k in text for k in ("创业板", "创指", "创业")):
        return "chinext"
    if any(k in text for k in ("深市", "深成", "深证", "深圳")):
        return "sz-component"
    return "sh-composite"


def _infer_news_board(text: str) -> str:
    """Map spoken board hints → MARKET_BOARDS id."""
    pairs = (
        (("国际", "海外", "美联储", "美股", "非农", "纳斯达克", "标普", "华尔街"), "world"),
        (("科技", "芯片", "半导体", "人工智能", "AI"), "tech"),
        (("能源", "石油", "煤炭", "电力", "新能源"), "energy"),
        (("金融", "银行", "券商", "保险"), "finance"),
        (("农业", "种植", "养殖", "饲料"), "agri"),
        (("汽车", "新能源车", "车企"), "auto"),
        (("地产", "房产", "楼市"), "estate"),
        (("产经", "制造", "工业"), "industry"),
        (("公司", "公告", "业绩"), "company"),
    )
    for keys, bid in pairs:
        if any(k in text for k in keys):
            return bid
    return "headline"


def prefetch_for_turn(
    db: Session,
    user_id: int,
    user_text: str,
) -> list[dict[str, str]]:
    """Run heuristic tools; return [{name, label, text}]."""
    out: list[dict[str, str]] = []
    for name, args in plan_prefetch(user_text, db=db, user_id=user_id):
        text = execute_tool(db, user_id, name, args, user_text=user_text)
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
        (
            "【本轮实时查询】刚拉取的真实数据；结论与点位只许引用这里有的，没有再说没有。"
            "嵌进口语，别列清单，引用数字时不要加粗；禁止补编未出现的宏观指标。"
            "工具里若给了多条报价：回答时最多用 1～2 个，禁止一股脑全念。"
        ),
        calendar_clock_line(),
    ]
    # 场景说明已在 assemble_turn；这里只补与数据强相关的一句
    if scene_primary == "macro" or (
        scene_primary is None and all(it["name"] == "get_macro" for it in items)
    ):
        parts.append(
            "宏观/黄金：优先讲标了「今日」的品种；非今日=昨收，说清即可。"
            "黄金泛问默认积存金；用户没点名的 ETF/门店/纽约金别提。"
        )
    for it in items:
        parts.append(it["text"])
    return "\n\n".join(parts)


def execute_tool(
    db: Session,
    user_id: int,
    name: str,
    arguments: dict[str, Any] | None,
    *,
    user_text: str = "",
) -> str:
    """Run one tool; return compact Chinese text for the model."""
    args = arguments if isinstance(arguments, dict) else {}
    try:
        if name == "get_indices":
            return _tool_indices()
        if name == "get_macro":
            return format_macro_text(
                str(args.get("topic") or ""),
                user_text=user_text or str(args.get("hint") or ""),
            )
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
        if name == "get_depth_flow":
            return _tool_depth_flow(
                str(args.get("symbol") or ""),
                str(args.get("market") or ""),
            )
        if name == "get_sector":
            return _tool_sector(str(args.get("symbol") or ""), str(args.get("market") or ""))
        if name == "get_portfolio":
            return _tool_portfolio(db, user_id)
        if name == "draft_rebalance_plan":
            return _tool_draft_rebalance(db, user_id)
        if name == "get_news":
            return _tool_news(
                db,
                user_id,
                kind=str(args.get("kind") or "market"),
                keyword=str(args.get("keyword") or ""),
                board=str(args.get("board") or "headline"),
                limit=int(args.get("limit") or 5),
            )
        if name == "search_knowledge":
            from app.services.knowledge import format_search_text

            return format_search_text(
                str(args.get("query") or ""),
                limit=int(args.get("limit") or 3),
                mode=str(args.get("mode") or "auto"),
            )
        if name == "search_symbol":
            return format_search_summary(
                str(args.get("query") or ""),
                limit=int(args.get("limit") or 5),
            )
        if name == "get_analysis_snapshot":
            return _tool_analysis_snapshot(db, user_id)
        if name == "start_analysis":
            return _tool_start_analysis(
                db,
                user_id,
                scope=str(args.get("scope") or "portfolio"),
                symbol=str(args.get("symbol") or ""),
                market=str(args.get("market") or ""),
                stock_name=str(args.get("name") or ""),
            )
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
    """Same five indices as App「股票」页 + session labels."""
    from app.providers.macro import calendar_clock_line, freshness_label
    from app.providers.quote import Quote, fetch_sina_int
    from app.providers.session import cn_session, hk_session, us_session_bj

    cn_specs = [
        ("sh-composite", "000001", "SH", "上证指数"),
        ("sz-component", "399001", "SZ", "深证成指"),
        ("chinext", "399006", "SZ", "创业板指"),
    ]
    hk_specs = [("hk-hsi", "HSI", "int_hangseng", "恒生指数")]
    us_specs = [("us-nasdaq", "IXIC", "int_nasdaq", "纳斯达克")]

    quotes = get_quotes([(sym, mkt) for _, sym, mkt, _ in cn_specs])

    def _fill_int(codes: list[tuple[str, str, str]], market: str) -> dict[str, Quote]:
        fetched = fetch_sina_int(codes)
        for sym, _, name in codes:
            if sym not in fetched:
                fetched[sym] = Quote(
                    symbol=sym,
                    name=name,
                    market=market,
                    price=0.0,
                    change_pct=None,
                    prev_close=None,
                )
            else:
                fetched[sym].market = market
        return fetched

    hk_quotes = _fill_int([(s, sina, n) for _, s, sina, n in hk_specs], "HK")
    us_quotes = _fill_int([(s, sina, n) for _, s, sina, n in us_specs], "US")

    sess_cn = cn_session()
    sess_hk = hk_session()
    sess_us = us_session_bj()
    lines = [
        "【指数行情 · 与 App「股票」页一致】上证/深成/创业/恒生/纳斯达克；无 live 报价禁止编造。",
        calendar_clock_line(),
        (
            f"开盘状态：A股 {sess_cn.label}（{sess_cn.detail}）；"
            f"港股 {sess_hk.label}；美股 {sess_us.label}。"
        ),
    ]
    any_live = False

    def _append(name: str, sym: str, q: Any, venue: str) -> None:
        nonlocal any_live
        if not q or not getattr(q, "live", True) or not q.price:
            lines.append(f"- {name}：暂无真实报价（勿编造）")
            return
        any_live = True
        pts = "—"
        if q.prev_close and q.prev_close > 0:
            pts = f"{q.price - q.prev_close:+.2f}"
        tag = freshness_label(q.as_of, venue=venue)
        line = (
            f"- {name}（{sym}）现价 {q.price:.2f} · 涨跌 {pts} 点 · {_fmt_pct(q.change_pct)}"
            f" · [{tag}]"
        )
        if q.as_of:
            line += f" · {q.as_of}"
        lines.append(line)

    for _key, sym, _mkt, name in cn_specs:
        _append(name, sym, quotes.get(sym), "a_share")
    for _key, sym, _sina, name in hk_specs:
        _append(name, sym, hk_quotes.get(sym), "us")
    for _key, sym, _sina, name in us_specs:
        _append(name, sym, us_quotes.get(sym), "us")

    if not any_live:
        lines.append("（接口失败，请让用户看「股票」页；禁止猜测点位）")
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


def _tool_depth_flow(symbol: str, market: str) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    sym, mkt = _resolve_sym_mkt(symbol, market)
    return format_depth_flow_summary(sym, mkt)


def _tool_sector(symbol: str, market: str) -> str:
    if not (symbol or "").strip():
        return "缺少 symbol"
    sym, mkt = _resolve_sym_mkt(symbol, market)
    if mkt == "HK":
        return (
            f"【板块 · {sym} HK】港股暂无 A 股式行业/概念板块涨跌。"
            "请用 get_quote / get_intraday / get_kline 查行情；勿编造板块。"
        )
    return format_sector_summary(sym, mkt)


def _tool_portfolio(db: Session, user_id: int) -> str:
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    lines = [
        "【仓库 · 与 App「仓库」页同口径】"
        "day_pnl=今日盈亏(现金流转)；quote_chg=行情涨跌(对昨收)；勿混用。"
        "短线标签=App 行上芯片（动量倾向，非预测）。",
        (
            f"总市值 {_fmt_money(pf.total_market_value)} · "
            f"总成本 {_fmt_money(pf.total_cost)} · "
            f"累计 {_fmt_money(pf.total_pnl)}（{_fmt_pct(pf.total_pnl_pct)}） · "
            f"day_pnl {_fmt_money(pf.day_pnl)}（{_fmt_pct(pf.day_pnl_pct)}）"
        ),
    ]
    if not pf.holdings:
        lines.append("（暂无持仓）")
        return "\n".join(lines)
    ranked = sorted(pf.holdings, key=lambda h: float(h.market_value or 0), reverse=True)
    bias_map: dict[str, str] = {}
    try:
        from app.providers.short_bias import get_short_biases

        pairs = [(h.symbol, h.market) for h in ranked[:12]]
        for b in get_short_biases(pairs):
            bias_map[f"{b.market}:{b.symbol}"] = b.label
    except Exception:
        logger.exception("portfolio short_bias failed")

    for h in ranked[:15]:
        unit = "克" if (h.market or "").upper() == "JD" else "份"
        bias = bias_map.get(f"{h.market}:{h.symbol}", "")
        bias_bit = f" 短线={bias}" if bias else ""
        lines.append(
            f"- {h.symbol} {h.name or ''} {h.shares:g}{unit} · "
            f"成本{(h.cost if h.cost is not None else 0):.3g} · "
            f"现价{(h.last_price if h.last_price is not None else 0):.3g} · "
            f"市值{_fmt_money(h.market_value)} "
            f"累计{_fmt_money(h.pnl)}（{_fmt_pct(h.pnl_pct)}）"
            f" day_pnl={_fmt_money(h.day_pnl)}（{_fmt_pct(h.day_pnl_pct)}）"
            f" quote_chg={_fmt_pct(h.change_pct)}"
            f" 仓位{_fmt_pct(h.weight)}"
            f"{bias_bit}"
        )
    if len(ranked) > 15:
        lines.append(f"…另有 {len(ranked) - 15} 只")
    return "\n".join(lines)


def portfolio_card_payload(db: Session, user_id: int) -> dict[str, Any]:
    """Structured card for UI (not for model prose)."""
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    holds = list(pf.holdings or [])
    ranked = sorted(holds, key=lambda h: float(h.market_value or 0), reverse=True)
    rows: list[dict[str, Any]] = []
    for h in ranked[:5]:
        rows.append(
            {
                "symbol": h.symbol,
                "name": (h.name or "").strip() or h.symbol,
                "weight": round(float(h.weight), 1) if h.weight is not None else None,
                "day_pnl_pct": (
                    round(float(h.day_pnl_pct), 2) if h.day_pnl_pct is not None else None
                ),
                "quote_chg": (
                    round(float(h.change_pct), 2) if h.change_pct is not None else None
                ),
            }
        )
    return {
        "kind": "portfolio",
        "total_market_value": round(float(pf.total_market_value or 0), 0),
        "day_pnl_pct": (
            round(float(pf.day_pnl_pct), 2) if pf.day_pnl_pct is not None else None
        ),
        "total_pnl_pct": (
            round(float(pf.total_pnl_pct), 2) if pf.total_pnl_pct is not None else None
        ),
        "count": len(holds),
        "holdings": rows,
    }


def rebalance_card_payload(db: Session, user_id: int) -> dict[str, Any]:
    """Structured rebalance draft card for UI."""
    from app.services.rebalance import draft_rebalance_from_rows

    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    holds = list(pf.holdings or [])
    rows = [
        {
            "symbol": h.symbol,
            "name": h.name,
            "weight": h.weight,
            "day_pnl": h.day_pnl,
            "day_pnl_pct": h.day_pnl_pct,
            "pnl_pct": h.pnl_pct,
        }
        for h in holds
    ]
    return draft_rebalance_from_rows(rows, day_pnl_pct=pf.day_pnl_pct)


def analysis_card_payload(db: Session, user_id: int) -> dict[str, Any] | None:
    """Compact card after start_analysis — UI shows friendly wait ack immediately."""
    from app.services import analysis as analysis_svc
    from app.services.agent_analysis_wait import estimate_eta_minutes, friendly_wait_line

    job = analysis_svc.running_job(db, user_id)
    if job is None:
        return None
    out = analysis_svc.job_to_out(job)
    symbols = list(out.get("symbols") or [])
    s0 = symbols[0] if symbols else {}
    scope = str(job.scope or "portfolio")
    if scope == "portfolio":
        label = "仓库巡检"
        tip = "仓库"
    else:
        nm = str(s0.get("name") or s0.get("symbol") or "").strip()
        label = f"个股·{nm}" if nm else "个股分析"
        tip = nm or "这只"
    lo, hi = estimate_eta_minutes(str(job.degree or "standard"))
    ack = friendly_wait_line(label=tip, degree=str(job.degree or "standard"))
    return {
        "kind": "analysis",
        "job_id": int(job.id),
        "scope": scope,
        "status": "running",
        "title": "分析进行中",
        "label": label,
        "degree": str(job.degree or "standard"),
        "eta_minutes": [lo, hi],
        "symbol": str(s0.get("symbol") or ""),
        "name": str(s0.get("name") or ""),
        "ack": ack,
    }


def card_payload_for_tool(
    db: Session, user_id: int, name: str
) -> dict[str, Any] | None:
    if name == "get_portfolio":
        return portfolio_card_payload(db, user_id)
    if name == "draft_rebalance_plan":
        return rebalance_card_payload(db, user_id)
    if name == "start_analysis":
        return analysis_card_payload(db, user_id)
    return None


def _tool_draft_rebalance(db: Session, user_id: int) -> str:
    """Deterministic rebalance draft from warehouse weights + day_pnl (no LLM)."""
    consolidate_same_symbol(db, user_id)
    pf = build_portfolio(db, user_id)
    holds = list(pf.holdings or [])
    if not holds:
        return "【调仓草案】仓库空仓，没什么可调；先去加持仓再说。"

    ranked = sorted(holds, key=lambda h: float(h.weight or 0), reverse=True)
    lines = [
        "【调仓草案·倾向性·非下单】",
        (
            f"组合 day_pnl {_fmt_money(pf.day_pnl)}（{_fmt_pct(pf.day_pnl_pct)}）· "
            f"累计 {_fmt_pct(pf.total_pnl_pct)} · 共 {len(holds)} 只"
        ),
    ]
    head = ranked[0]
    hw = float(head.weight or 0)
    if hw >= 35:
        lines.append(
            f"过重：{head.name or head.symbol} 仓位 {hw:.1f}% —— 可以考虑宜减不宜加，别再加仓。"
        )
    elif hw >= 25:
        lines.append(
            f"偏集中：{head.name or head.symbol} 约 {hw:.1f}% —— 观望为主，冲高再议要不要轻减。"
        )

    by_day = sorted(
        holds,
        key=lambda h: float(h.day_pnl if h.day_pnl is not None else 0),
    )
    drag = by_day[0]
    lift = by_day[-1]
    if drag.day_pnl is not None and float(drag.day_pnl) < 0:
        lines.append(
            f"今日拖累：{drag.name or drag.symbol} day_pnl {_fmt_money(drag.day_pnl)}"
            f"（{_fmt_pct(drag.day_pnl_pct)}）—— 先分清是行情还是你的成本；"
            "别把 quote_chg 当成账户亏多少。"
        )
    if lift.day_pnl is not None and float(lift.day_pnl) > 0 and lift.symbol != drag.symbol:
        lines.append(
            f"今日贡献：{lift.name or lift.symbol} day_pnl {_fmt_money(lift.day_pnl)}"
            f"（{_fmt_pct(lift.day_pnl_pct)}）—— 冲高别盲目加。"
        )

    near_cost = [
        h
        for h in holds
        if h.pnl_pct is not None and abs(float(h.pnl_pct)) < 2
    ]
    if near_cost:
        names = "、".join((h.name or h.symbol) for h in near_cost[:3])
        lines.append(f"贴近成本：{names} —— 方向一变体感会明显，可以考虑先盯着。")

    top3 = sum(float(h.weight or 0) for h in ranked[:3])
    if top3 >= 70:
        lines.append(f"前三合计约 {top3:.0f}% —— 分散偏弱，新钱优先看别的方向，别继续堆头仓。")

    lines.append("话术约束：只给倾向，不承诺赚钱，不假装已经下单。")
    return "\n".join(lines)


def _tool_analysis_snapshot(db: Session, user_id: int) -> str:
    from app.services import analysis as analysis_svc
    from app.services import analysis_pending as pending_svc
    from app.services.agent_context import _summarize_report

    lines: list[str] = ["【分析状态】"]
    running = analysis_svc.running_job(db, user_id)
    just_ready = None if running is not None else pending_svc.consume_if_ready(db, user_id)

    if running is not None:
        lines.append(
            f"进行中：任务 #{running.id} · {running.scope} · {running.degree}。"
            "结论还没出来。话术：分析还在跑，你可以先聊别的；"
            "跑完后你再说一句或随便聊，我会主动把结论带上。"
            "禁止编造尚未完成的专家会结论。"
        )
    elif just_ready is not None:
        lines.append(
            "【刚跑完·必须主动播报】委员会已结束。"
            "回复第一句就必须用人话讲清结论（verdict + 倾向），"
            "不要等用户问「分析好了吗」；可提一句去「分析」页看全文。"
        )
    else:
        lines.append("当前没有进行中的分析任务。")

    job = just_ready or analysis_svc.latest_job(db, user_id)
    if job is None:
        lines.append("还没有已完成的分析报告。可去「分析」页跑，或让我 start_analysis 后台巡检。")
        return "\n".join(lines)

    report: dict[str, Any] | None = None
    if job.report_json:
        try:
            raw = json.loads(job.report_json)
            if isinstance(raw, dict):
                report = raw
        except json.JSONDecodeError:
            report = None

    if str(job.status or "") == "failed":
        lines.append(
            f"最近任务 #{job.id} 失败：{(job.error or '未知错误')[:200]}。"
            "话术：老实说没跑通，可请用户再试或去分析页看。"
        )
        return "\n".join(lines)

    lines.append(
        f"最近完成：任务 #{job.id} · {job.scope} · {job.degree} · 配方 {job.recipe_id}"
        + (
            "（刚出炉，请主动播报）"
            if just_ready is not None
            else ("（可在对话里带一句结论）" if not running else "（旧结论；新任务还在跑）")
        )
    )
    if report and report.get("template"):
        lines.append("质量：模板兜底（委员会未完整跑通），播报时要诚实说「简化版/兜底」。")
    elif report and report.get("degraded"):
        fails = report.get("failed_seats") or []
        bit = "、".join(str(x) for x in fails[:4]) if fails else "部分席位"
        lines.append(f"质量：部分席位异常（{bit}），播报时提一句「有的席没谈成」。")
    lines.extend(_summarize_report(report))
    return "\n".join(lines)


def _explicit_symbol_analysis_target(
    text: str,
    *,
    db: Session | None,
    user_id: int | None,
) -> dict[str, str] | None:
    """Parse「帮我分析茅台/600519」→ start_analysis args; None if not explicit stock run."""
    import re

    t = (text or "").strip()
    if not t:
        return None
    if any(h in t for h in _START_ANALYSIS_HINTS):
        return None
    m = re.search(r"(?:帮我分析|分析一下|分析下|重新分析)\s*(.+)$", t)
    if not m:
        return None
    raw = m.group(1).strip()
    for suf in (
        "这只股票",
        "这支股票",
        "股票",
        "这只票",
        "这只",
        "一下",
        "情况",
        "怎么样",
        "怎样",
        "吗",
        "呢",
        "吧",
        "呀",
    ):
        if raw.endswith(suf):
            raw = raw[: -len(suf)].strip()
    if not raw:
        return None
    if any(k in raw for k in ("仓库", "持仓", "组合", "账户", "大盘", "指数")):
        return None

    code_m = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
    if code_m:
        sym, mkt = normalize_symbol(code_m.group(1), "")
        if mkt == "HK":
            return None
        return {
            "scope": "symbol",
            "symbol": sym,
            "market": mkt,
            "name": raw if not raw.isdigit() else sym,
        }

    hk_m = re.search(r"(?<!\d)(\d{5})(?!\d)", raw)
    if hk_m:
        # 港股只聊天查行情，不开标准分析
        return None

    if db is not None and user_id is not None:
        rows = (
            db.query(Holding)
            .filter(Holding.user_id == user_id)
            .order_by(Holding.id.asc())
            .all()
        )
        for h in rows:
            nm = (h.name or "").strip()
            if nm and (nm in raw or raw in nm):
                return {
                    "scope": "symbol",
                    "symbol": h.symbol,
                    "market": h.market,
                    "name": nm or h.symbol,
                }

    hit = resolve_best_symbol(raw)
    if hit:
        if (hit.market or "").upper() == "HK":
            return None
        return {
            "scope": "symbol",
            "symbol": hit.symbol,
            "market": hit.market,
            "name": hit.name or hit.symbol,
        }
    return None


def _tool_start_analysis(
    db: Session,
    user_id: int,
    *,
    scope: str,
    symbol: str = "",
    market: str = "",
    stock_name: str = "",
) -> str:
    from app.services import analysis as analysis_svc

    scope = (scope or "portfolio").strip().lower() or "portfolio"
    if scope not in {"portfolio", "symbol"}:
        return "【启动分析】scope 只支持 portfolio 或 symbol。"

    from app.services import analysis_pending as pending_svc

    existing = analysis_svc.running_job(db, user_id)
    if existing is not None:
        pending_svc.mark_pending(user_id, int(existing.id))
        return (
            f"【启动分析】已有任务 #{existing.id}（{existing.scope}/{existing.degree}）在跑。"
            "本轮对话会等待该任务结束后再带结论给你，无需用户再说一句。"
            "在等待期间不要编造结论。"
        )

    symbols: list[dict[str, str]] | None = None
    label = "仓库巡检"
    if scope == "symbol":
        sym = (symbol or "").strip()
        mkt = (market or "").strip().upper()
        nm = (stock_name or "").strip()
        if not sym:
            # 尝试用名称再解析一次
            if nm:
                hit = resolve_best_symbol(nm)
                if hit:
                    sym, mkt, nm = hit.symbol, hit.market, hit.name or hit.symbol
            if not sym:
                return (
                    "【启动分析】个股分析需要股票代码。"
                    "请先 search_symbol 再 start_analysis(scope=symbol, symbol=…)。"
                )
        try:
            sym, mkt = normalize_symbol(sym, mkt or "SH")
        except Exception:
            pass
        if (mkt or "").upper() == "HK" or (len(sym) == 5 and sym.isdigit()):
            return (
                f"【启动分析】港股「{nm or sym}」不开标准分析、也不进仓库。"
                "聊天里用 get_quote / get_intraday / get_kline 查报价、分时、日K 即可。"
            )
        if (mkt or "").upper() not in {"SH", "SZ", "OF", "JD", "GDS"}:
            return (
                "【启动分析】单票分析支持 SH/SZ（股票·ETF）、OF（场外基金）、"
                "JD（积存金）、GDS（AU9999 上金所现货）。"
            )
        symbols = [{"symbol": sym, "market": mkt or "SH", "name": nm or sym}]
        label = f"单票标准分析「{nm or sym}」"

    try:
        job = analysis_svc.start_job_background(
            user_id=user_id,
            scope=scope,
            symbols=symbols,
            degree="standard",
        )
    except ValueError as exc:
        return f"【启动分析】开不了：{exc}"
    except Exception as exc:
        logger.exception("start_analysis failed")
        return f"【启动分析】失败：{type(exc).__name__}"

    pending_svc.mark_pending(user_id, int(job.id))

    target_note = ""
    if scope == "symbol" and symbols:
        s0 = symbols[0]
        target_note = f" · {s0.get('name') or s0['symbol']}（{s0['symbol']}）"

    return (
        f"【启动分析】{label}已在后台开始（任务 #{job.id} · 标准档{target_note}）。"
        "本轮会等待委员会跑完，再把结论注入后回答；"
        "先用友好等待话术安抚用户，禁止假装现在已有新结论。"
    )


def _tool_news(
    db: Session,
    user_id: int,
    *,
    kind: str,
    keyword: str,
    board: str = "headline",
    limit: int,
) -> str:
    from app.services.news_relevance import news_items_to_dicts, rank_and_trim_news

    lim = max(1, min(int(limit or 5), 10))
    kind = (kind or "market").strip().lower()
    items: list[Any] = []
    title = "新闻"
    filtered = False

    # Light portfolio context for relevance (names + crude asset kinds)
    hold_rows = (
        db.query(Holding.symbol, Holding.name, Holding.market)
        .filter(Holding.user_id == user_id)
        .order_by(Holding.id.asc())
        .all()
    )
    hold_symbols = [str(r[0]) for r in hold_rows if r and r[0]]
    hold_names = [str(r[1]).strip() for r in hold_rows if r and str(r[1] or "").strip()]
    hold_kinds: list[str] = []
    for r in hold_rows:
        mkt = str(r[2] or "SH").upper()
        nm = str(r[1] or "")
        if mkt == "JD" or "黄金" in nm:
            hold_kinds.append("黄金积存" if mkt == "JD" else "黄金ETF")
        elif mkt == "OF":
            hold_kinds.append("场外基金")
        else:
            hold_kinds.append("股票")

    def _trim(raw_items: list[Any], *, board_tag: str = "") -> list[NewsItem]:
        nonlocal filtered
        pool = news_items_to_dicts(raw_items, board=board_tag) if raw_items else []
        # Over-fetch already done by callers; still rank to lim
        ranked = rank_and_trim_news(
            pool,
            limit=lim,
            symbols=hold_symbols,
            names=hold_names,
            asset_kinds=hold_kinds,
            interest_terms=[keyword] if keyword else None,
        )
        filtered = True
        out: list[NewsItem] = []
        for d in ranked:
            out.append(
                NewsItem(
                    id=str(d.get("id") or ""),
                    title=str(d.get("title") or ""),
                    summary=str(d.get("summary") or ""),
                    source=str(d.get("source") or ""),
                    published_at=str(d.get("published_at") or ""),
                    url=str(d.get("url") or ""),
                    symbols=list(d.get("symbols") or []),
                    region=str(d.get("region") or "cn"),
                )
            )
        return out

    if kind == "holdings":
        pool_lim = max(lim * 3, 20)
        raw = get_holdings_news(hold_symbols, limit=pool_lim)
        items = _trim(raw, board_tag="holding")
        title = "持仓相关新闻"
    elif kind == "keyword":
        kw = (keyword or "").strip()
        if not kw:
            return "keyword 搜索需要提供 keyword"
        # 英文 topic id → 中文检索词（gold→黄金）
        if kw in set(list_topics()):
            kw = news_keyword_for_topic(kw)
        pool_lim = max(lim * 3, 24)
        raw = get_interests_news([kw], limit=pool_lim)
        items = _trim(raw, board_tag="keyword")
        title = f"关键词「{kw}」新闻"
    else:
        bid = (board or "headline").strip().lower()
        known = {b["id"] for b in MARKET_BOARDS}
        if bid not in known:
            bid = "headline"
        pool_lim = max(lim * 4, 28)
        board_title, raw = get_market_news(limit=pool_lim, board=bid)
        items = _trim(raw, board_tag=bid)
        title = f"市场新闻 · {board_title}"

    return format_news_digest(items, title=title, limit=lim, filtered=filtered)


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
