"""Analysis committee: trend ∥ news ∥ flow → risk(det) → dialectic×N → judge."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any, Callable

from app.services import analysis_llm as llm
from app.services import analysis_prompts as prompts
from app.services.analysis_tiers import AGENT_LABELS, get_tier

logger = logging.getLogger(__name__)

EventCb = Callable[[dict[str, Any]], None]


def dialectic_rounds_for(scope: str, degree: str) -> int:
    """Dialectic rounds by degree（个股轻量可跳过；仓库固定 standard 时为 1）。"""
    del scope
    return {"light": 0, "standard": 1, "deep": 2}.get(degree, 1)


def _fmt_pct(v: Any) -> str | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f"{float(v):+.2f}%"
    return None


def holding_fact_bits(q: dict[str, Any]) -> list[str]:
    """Factual fragments for one quote/holding row (no name prefix)."""
    bits: list[str] = []
    if q.get("weight") is not None:
        try:
            bits.append(f"仓位{float(q['weight']):.1f}%")
        except (TypeError, ValueError):
            pass
    # 持仓：今日盈亏用现金流转；行情涨跌单独标，勿混用
    if q.get("in_portfolio") and q.get("day_pnl_pct") is not None:
        day_pnl = _fmt_pct(q.get("day_pnl_pct"))
        if day_pnl is not None:
            bits.append(f"今日盈亏{day_pnl}")
        quote_move = _fmt_pct(q.get("change_pct"))
        day_label = str(q.get("day_label") or "今日")
        if quote_move is not None:
            bits.append(f"行情{day_label}{quote_move}")
        elif not q.get("fresh_today", True):
            last = _fmt_pct(q.get("last_session_change_pct"))
            if last is not None:
                bits.append(f"昨盘{last}")
    else:
        day_label = str(q.get("day_label") or "今日")
        day_move = _fmt_pct(q.get("change_pct"))
        if day_move is not None:
            bits.append(f"{day_label}{day_move}")
        elif not q.get("fresh_today", True):
            last = _fmt_pct(q.get("last_session_change_pct"))
            if last is not None:
                bits.append(f"昨盘{last}")
    if q.get("prev_close") is not None and q.get("in_portfolio"):
        try:
            bits.append(f"昨收{float(q['prev_close']):.2f}")
        except (TypeError, ValueError):
            pass
    periods = q.get("periods") if isinstance(q.get("periods"), dict) else {}
    d5 = _fmt_pct(periods.get("d5"))
    if d5:
        bits.append(f"近5交易日{d5}")
    if q.get("pnl_pct") is not None and q.get("in_portfolio"):
        try:
            pnl = float(q["pnl_pct"])
            bits.append("成本附近" if abs(pnl) < 2 else f"相对成本{pnl:+.1f}%")
        except (TypeError, ValueError):
            pass
    if not bits:
        bits.append("数据有限")
    return bits


def holding_fact_line(q: dict[str, Any], *, with_name: bool = True) -> str:
    name = str(q.get("name") or q.get("symbol") or "").strip()
    kind = str(q.get("asset_kind") or "").strip()
    body = " · ".join(holding_fact_bits(q))
    if with_name and name:
        prefix = f"{name}[{kind}]" if kind else name
        return f"{prefix}：{body}"
    return body


def build_holding_lines(snapshot: dict[str, Any], *, limit: int = 6) -> list[str]:
    """Deterministic one-liners for current holdings / targets (evidence only)."""
    quotes = list(snapshot.get("quotes") or [])
    if not quotes:
        return []
    in_port = [q for q in quotes if q.get("in_portfolio")]
    src = in_port or quotes
    ranked = sorted(
        src,
        key=lambda x: float(x.get("weight") or 0),
        reverse=True,
    )
    # 仓库巡检：尽量覆盖全部持仓（股票/基金/黄金）
    if str(snapshot.get("scope") or "") == "portfolio":
        cap = max(limit, len(ranked))
    else:
        cap = limit
    return [holding_fact_line(q) for q in ranked[:cap]]


_CLOUDY_VERDICT = (
    "动能",
    "格局",
    "青睐",
    "维持看好",
    "整体偏多",
    "整体偏空",
    "震荡上行",
    "谨慎乐观",
    "走势稳健",
    "做多情绪",
)


def _verdict_too_cloudy(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 8:
        return True
    return any(w in t for w in _CLOUDY_VERDICT)


def _looks_like_data_dump(text: str) -> bool:
    """True if the line is mostly % / 仓位 / 涨跌 enumeration."""
    t = (text or "").strip()
    if not t:
        return True
    if "%" in t and any(ch.isdigit() for ch in t):
        return True
    if "仓位" in t and any(ch.isdigit() for ch in t):
        return True
    if t.count("·") >= 2 and any(ch.isdigit() for ch in t):
        return True
    if "龙虎榜" in t or "净买入" in t:
        return True
    return False


def _action_tail(stance: str, llm_summary: str) -> str:
    s = (llm_summary or "").strip()
    if s and len(s) <= 36 and not _looks_like_data_dump(s) and any(
        k in s for k in ("宜减", "观望", "持有", "轻仓", "兑现", "加仓", "减仓")
    ):
        return s
    return {
        "偏多": "可以考虑持有观望",
        "偏空": "可以考虑宜减不宜加",
        "数据不足": "先看清再动",
    }.get(stance, "观望为主")


def _plain_item_summary(name: str, stance: str, llm_summary: str) -> str:
    s = (llm_summary or "").strip()
    if s and not _looks_like_data_dump(s):
        # already named?
        if name and name in s:
            return s
        return f"{name}：{s}" if name else s
    return f"{name}：{_action_tail(stance, '')}" if name else _action_tail(stance, "")


def _plain_fallback_verdict(snapshot: dict[str, Any], stance: str) -> str:
    quotes = list(snapshot.get("quotes") or [])
    ranked = sorted(quotes, key=lambda x: float(x.get("weight") or 0), reverse=True)
    names = [
        str(q.get("name") or q.get("symbol") or "").strip()
        for q in ranked[:2]
        if str(q.get("name") or q.get("symbol") or "").strip()
    ]
    action = _action_tail(stance, "")
    if len(names) >= 2:
        return f"{names[0]}、{names[1]}是当前主仓，整体【{stance}】，{action}。"
    if names:
        return f"{names[0]}是当前主仓，整体【{stance}】，{action}。"
    return f"整体【{stance}】，{action}。"



def compute_structure_facts(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Deterministic portfolio/market facts for seats (not LLM)."""
    quotes = list(snapshot.get("quotes") or [])
    indices = list(snapshot.get("indices") or [])
    news = list(snapshot.get("news") or [])
    port = snapshot.get("portfolio") if isinstance(snapshot.get("portfolio"), dict) else {}

    ranked = sorted(
        [q for q in quotes if q.get("weight") is not None],
        key=lambda x: float(x.get("weight") or 0),
        reverse=True,
    )
    head_w = float(ranked[0]["weight"]) if ranked else None
    head_name = str((ranked[0].get("name") or ranked[0].get("symbol") or "")) if ranked else ""
    top3 = round(sum(float(q.get("weight") or 0) for q in ranked[:3]), 2) if ranked else None

    def _day_move(q: dict[str, Any]) -> float | None:
        if q.get("fresh_today", True) and isinstance(q.get("change_pct"), (int, float)):
            return float(q["change_pct"])
        if isinstance(q.get("last_session_change_pct"), (int, float)):
            return float(q["last_session_change_pct"])
        if isinstance(q.get("change_pct"), (int, float)):
            return float(q["change_pct"])
        return None

    holds = [q for q in quotes if q.get("in_portfolio")] or quotes
    hold_moves = [m for q in holds if (m := _day_move(q)) is not None]
    avg_hold = round(sum(hold_moves) / len(hold_moves), 2) if hold_moves else None
    sh = next((i for i in indices if i.get("symbol") == "000001"), None)
    sh_move = _day_move(sh) if isinstance(sh, dict) else None
    vs_sh = None
    if avg_hold is not None and sh_move is not None:
        vs_sh = round(avg_hold - sh_move, 2)

    port_day_pct: float | None = None
    try:
        if port.get("day_pnl_pct") is not None:
            port_day_pct = float(port["day_pnl_pct"])
    except (TypeError, ValueError):
        port_day_pct = None
    vs_sh_pnl: float | None = None
    if port_day_pct is not None and sh_move is not None:
        vs_sh_pnl = round(port_day_pct - sh_move, 2)

    return {
        "head_weight": head_w,
        "head_name": head_name,
        "top3_weight": top3,
        "avg_hold_move": avg_hold,
        "sh_move": sh_move,
        "vs_sh": vs_sh,
        "portfolio_day_pnl_pct": port_day_pct,
        "vs_sh_pnl": vs_sh_pnl,
        "news_count": len(news),
        "quote_count": len(quotes),
        "fresh_share": (
            round(
                sum(1 for q in quotes if q.get("fresh_today", True)) / max(len(quotes), 1),
                2,
            )
            if quotes
            else 0.0
        ),
    }


def build_risk_seat_memo(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Deterministic「结构风险」席 — no LLM; deep/standard 名实相符。"""
    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    if not structure:
        structure = compute_structure_facts(snapshot)
        snapshot["structure"] = structure

    bullets: list[str] = []
    head_w = structure.get("head_weight")
    head_n = str(structure.get("head_name") or "").strip()
    top3 = structure.get("top3_weight")
    fresh = float(structure.get("fresh_share") or 1.0)
    vs_pnl = structure.get("vs_sh_pnl")
    vs_sh = structure.get("vs_sh")
    news_n = int(structure.get("news_count") or 0)

    stance = "中性"
    conf = 0.72

    if isinstance(head_w, (int, float)) and float(head_w) >= 35 and head_n:
        bullets.append(f"{head_n}仓位 {float(head_w):.1f}%，组合跟着它晃")
        stance = "偏空"
        conf = 0.78
    if isinstance(top3, (int, float)) and float(top3) >= 70:
        bullets.append(f"前三合计 {float(top3):.1f}%，分散度偏弱")
        if stance == "中性":
            stance = "偏空"
    if vs_pnl is not None:
        rel = "强于" if float(vs_pnl) >= 0.3 else ("弱于" if float(vs_pnl) <= -0.3 else "大致跟上")
        bullets.append(
            f"组合今日盈亏 {structure.get('portfolio_day_pnl_pct')}%，"
            f"{rel}上证（差 {float(vs_pnl):+.2f}%）"
        )
    elif vs_sh is not None:
        rel = "偏强" if float(vs_sh) >= 0.3 else ("偏弱" if float(vs_sh) <= -0.3 else "大致同步")
        bullets.append(f"持仓行情相对上证{rel}（差 {float(vs_sh):+.2f}%）")
    if fresh < 0.5:
        bullets.append("多只行情非今日，今日盈亏按归零理解，别当盘中大涨大跌")
        conf = min(conf, 0.5)
        stance = "数据不足"
    if news_n <= 0:
        bullets.append("本轮几乎无关联新闻，别把波动说成消息驱动")
        conf = min(conf, 0.55)

    if not bullets:
        bullets.append("集中度与相对大盘暂无明显红旗")

    summary = "；".join(bullets[:2])
    return {
        "id": "risk",
        "label": AGENT_LABELS.get("risk", "结构风险"),
        "status": "done",
        "summary": summary,
        "stance": stance,
        "confidence": conf,
        "bullets": bullets[:4],
        "weight": None,
    }


def _news_age_label(published_at: Any) -> str:
    from app.providers.news import news_age_label

    return news_age_label(published_at)


def build_flow_seat_memo(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """If no usable 资金 evidence, return deterministic 数据不足 memo (skip LLM)."""
    rows = list(snapshot.get("depth_flow") or [])
    usable = [r for r in rows if r.get("main_net") is not None or r.get("flow_label")]
    if usable and any(r.get("main_net") is not None for r in usable):
        return None
    return {
        "id": "flow",
        "label": AGENT_LABELS.get("flow", "资金情绪"),
        "status": "done",
        "summary": "本轮无可用资金流向，勿编造主力进出",
        "stance": "数据不足",
        "confidence": 0.35,
        "bullets": ["盘口资金暂缺或未覆盖头部持仓"],
        "weight": None,
    }


def format_evidence_text(snapshot: dict[str, Any]) -> str:
    """Compact Chinese brief for all seats (frozen numbers only)."""
    lines: list[str] = []
    lines.append(f"范围：{snapshot.get('scope')} · 证据档：{snapshot.get('evidence_tier')}")
    lines.append(f"采集：{snapshot.get('captured_at') or ''}")
    cal = snapshot.get("calendar") if isinstance(snapshot.get("calendar"), dict) else {}
    if cal:
        lines.append(
            "【日历】"
            f"上海 {cal.get('shanghai_time') or cal.get('shanghai_date')} · "
            f"{cal.get('session_label') or ''}（{cal.get('session_detail') or ''}）。"
            f"{cal.get('today_means') or ''}"
        )

    indices = snapshot.get("indices") or []
    if indices:
        bits = []
        for i in indices:
            label = str(i.get("day_label") or "今日")
            chg = i.get("change_pct")
            chg_s = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "—"
            bit = f"{i.get('name') or i.get('symbol')} {i.get('price')}（{label} {chg_s}"
            if not i.get("fresh_today", True):
                last = i.get("last_session_change_pct")
                if isinstance(last, (int, float)):
                    bit += f"；昨盘 {last:+.2f}%"
            bit += "）"
            bits.append(bit)
        lines.append("指数：" + "；".join(bits))

    port = snapshot.get("portfolio")
    if isinstance(port, dict) and port:
        # day_pnl 已在 build_portfolio 按日历归零；标签仍写「今日盈亏」= 日历今日
        lines.append(
            "组合："
            f"市值 {port.get('total_market_value')} · "
            f"总盈亏 {port.get('total_pnl')}（{port.get('total_pnl_pct')}%）· "
            f"今日盈亏 {port.get('day_pnl')}（{port.get('day_pnl_pct')}%）· "
            f"{port.get('holding_count')} 只"
        )

    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    if not structure:
        structure = compute_structure_facts(snapshot)
    struct_bits: list[str] = []
    if structure.get("head_weight") is not None and structure.get("head_name"):
        struct_bits.append(
            f"头部 {structure['head_name']} 仓位 {float(structure['head_weight']):.1f}%"
        )
    if structure.get("top3_weight") is not None:
        struct_bits.append(f"前三合计 {float(structure['top3_weight']):.1f}%")
    if structure.get("vs_sh") is not None:
        avg = structure.get("avg_hold_move")
        sh = structure.get("sh_move")
        vs = float(structure["vs_sh"])
        rel = "偏强" if vs >= 0.3 else ("偏弱" if vs <= -0.3 else "大致同步")
        struct_bits.append(
            f"持仓行情相对上证{rel}（持仓均 {avg}% · 上证 {sh}% · 差 {vs:+.2f}%）"
        )
    if structure.get("vs_sh_pnl") is not None and structure.get("portfolio_day_pnl_pct") is not None:
        vp = float(structure["vs_sh_pnl"])
        rel_p = "强于" if vp >= 0.3 else ("弱于" if vp <= -0.3 else "大致跟上")
        struct_bits.append(
            f"组合今日盈亏{structure.get('portfolio_day_pnl_pct')}%"
            f"{rel_p}上证（差 {vp:+.2f}%）"
        )
    if struct_bits:
        lines.append("【结构事实】" + "；".join(struct_bits))

    for q in snapshot.get("quotes") or []:
        label = str(q.get("day_label") or "今日")
        chg = q.get("change_pct")
        chg_s = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "—"
        bit = (
            f"{q.get('name') or q.get('symbol')}({q.get('symbol')}) "
            f"[{q.get('asset_kind') or '标的'}] "
            f"现价 {q.get('price')}"
        )
        if q.get("prev_close") is not None:
            bit += f" 昨收 {q.get('prev_close')}"
        if q.get("open") is not None:
            bit += f" 今开 {q.get('open')}"
        if q.get("in_portfolio") and q.get("day_pnl_pct") is not None:
            dp = q.get("day_pnl_pct")
            dn = q.get("day_pnl")
            bit += f" 今日盈亏 {dn}（{dp}%）"
            bit += f" 行情{label} {chg_s}"
        else:
            bit += f" {label} {chg_s}"
        if not q.get("fresh_today", True):
            last = q.get("last_session_change_pct")
            if isinstance(last, (int, float)):
                bit += f"（昨盘涨跌 {last:+.2f}%，勿说成今日）"
            as_of = q.get("as_of")
            if as_of:
                bit += f" as_of={as_of}"
        if q.get("weight") is not None:
            bit += f" 仓位 {q.get('weight')}%"
        if q.get("pnl_pct") is not None and q.get("in_portfolio"):
            bit += f" 相对成本 {q.get('pnl_pct')}%"
        if q.get("bought_at") and q.get("in_portfolio"):
            bit += f" 买入日 {q.get('bought_at')}"
        periods = q.get("periods") or {}
        if periods:
            ps = []
            for key, label_p in (("d5", "近5交易日"), ("m1", "近1月"), ("m3", "近3月")):
                v = periods.get(key)
                if isinstance(v, (int, float)):
                    ps.append(f"{label_p} {v:+.2f}%")
            as_of_p = periods.get("as_of")
            if ps:
                bit += " · " + " / ".join(ps)
                if as_of_p:
                    bit += f"（K截至 {as_of_p}）"
        lines.append("标的：" + bit)

    for row in snapshot.get("intraday") or []:
        lines.append(
            f"分时 {row.get('symbol')}：点位 {row.get('point_count')} · "
            f"开 {row.get('open')} → 最新 {row.get('last')} · 昨收 {row.get('prev_close')}"
        )

    for row in snapshot.get("depth_flow") or []:
        name = row.get("name") or row.get("symbol")
        bit = f"盘口资金 {name}({row.get('symbol')})：{row.get('flow_label') or '资金暂无'}"
        if row.get("main_net") is not None:
            yi = float(row["main_net"]) / 1e8
            bit += f" 主力净 {yi:+.2f}亿"
            if row.get("main_pct") is not None:
                bit += f"（{float(row['main_pct']):+.2f}%）"
            if row.get("flow_date"):
                bit += f" · {row.get('flow_date')}"
        if row.get("book_live") and row.get("bid1_price") is not None:
            bit += (
                f" · 买1 {row.get('bid1_price')}/{row.get('bid1_vol')}手"
                f" 卖1 {row.get('ask1_price')}/{row.get('ask1_vol')}手"
            )
        elif row.get("session_state") and row.get("session_state") != "trading":
            bit += " · 非交易时段无实时五档"
        bit += "（主力=成交额分档，非庄家）"
        lines.append(bit)

    macro = snapshot.get("macro") or []
    if macro:
        lines.append("宏观（仅下列可用，禁止外推）：")
        for m in macro:
            lines.append(str(m)[:800])
    else:
        lines.append("宏观：本轮未纳入（勿编造 DXY/美债/COMEX 等）。")

    news = snapshot.get("news") or []
    if news:
        lines.append(
            f"新闻 {len(news)} 条（已按持仓/主题相关性筛选，非全网原文；"
            "无高相关时可判数据不足，禁止编造）："
        )
        for n in news[:14]:
            syms = n.get("symbols") or []
            board = str(n.get("board") or "")
            region = str(n.get("region") or "cn")
            if board == "headline" and not syms:
                sym_s = "要闻"
            elif board == "world" or region == "world":
                sym_s = "国际"
            elif syms:
                sym_s = ",".join(str(s) for s in syms[:4])
            else:
                sym_s = "未标注"
            age = _news_age_label(n.get("published_at"))
            src = str(n.get("source") or "").strip()
            src_bit = f"｜{src}" if src else ""
            rel = n.get("relevance")
            rel_bit = ""
            if isinstance(rel, (int, float)):
                why = str(n.get("relevance_why") or "").strip()
                rel_bit = f"｜相关度{rel:.2f}"
                if why:
                    rel_bit += f"({why})"
            lines.append(
                f"- [{age}] 关联:{sym_s}{src_bit}{rel_bit}｜{n.get('title') or ''}｜{(n.get('summary') or '')[:100]}"
            )
    else:
        lines.append("新闻：暂无（新闻席应数据不足）。")

    knowledge = snapshot.get("knowledge") or []
    if knowledge:
        backend = str(knowledge[0].get("backend") or "经验库")
        lines.append(
            f"【经验库·非实时 · {backend}】方法论/纪律参考，不是行情也不是新闻；"
            "先讲本轮证据数字，经验只嵌一两句框架；勿把库内叙述当今日点位。"
        )
        for i, k in enumerate(knowledge[:6], 1):
            tags = "、".join(str(t) for t in (k.get("tags") or [])[:4]) or "—"
            age = k.get("date") or "日期未知"
            lines.append(
                f"- {i}. {k.get('title') or ''}（{k.get('source') or '经验库'} · {age} · "
                f"{k.get('channel') or ''} · 相关度 {k.get('score')}）"
                f" 标签:{tags} — {(k.get('body') or '')[:180]}"
            )
    else:
        lines.append("经验库：本轮未命中（勿编造经验条目）。")

    return "\n".join(lines)


def _memo_from_llm(aid: str, data: dict[str, Any]) -> dict[str, Any]:
    bullets = data.get("bullets") if isinstance(data.get("bullets"), list) else []
    bullets = [str(b).strip() for b in bullets if str(b).strip()][:3]
    summary = str(data.get("summary") or data.get("verdict") or "").strip() or "（无摘要）"
    return {
        "id": aid,
        "label": AGENT_LABELS.get(aid, aid),
        "status": "done",
        "summary": summary,
        "stance": llm.normalize_stance(data.get("stance")),
        "confidence": llm.clamp_confidence(data.get("confidence")),
        "bullets": bullets,
        "weight": None,
        "bull_points": [str(x) for x in (data.get("bull_points") or []) if str(x).strip()][:4],
        "bear_points": [str(x) for x in (data.get("bear_points") or []) if str(x).strip()][:4],
        "open_questions": [str(x) for x in (data.get("open_questions") or []) if str(x).strip()][:4],
        "_raw": data,
    }


def _failed_memo(aid: str, err: str) -> dict[str, Any]:
    reason = llm.friendly_llm_error(RuntimeError(err))
    return {
        "id": aid,
        "label": AGENT_LABELS.get(aid, aid),
        "status": "failed",
        "summary": f"本席暂时失败：{reason}",
        "stance": "数据不足",
        "confidence": 0.2,
        "bullets": [],
        "weight": None,
    }


def _run_seat(aid: str, system: str, user: str) -> dict[str, Any]:
    try:
        data = llm.chat_json(system=system, user=user, temperature=0.35, max_tokens=1400)
        return _memo_from_llm(aid, data)
    except Exception as exc:
        logger.exception("analysis seat %s failed", aid)
        return _failed_memo(aid, str(exc))


def run_committee(
    snapshot: dict[str, Any],
    *,
    scope: str,
    degree: str,
    on_event: EventCb | None = None,
) -> dict[str, Any]:
    """Run multi-agent committee; return report dict. Emits progress via on_event."""

    def emit(ev: dict[str, Any]) -> None:
        if on_event:
            on_event(ev)

    def emit_progress(pct: int, label: str, *, stage: str) -> None:
        emit(
            {
                "type": "progress",
                "pct": pct,
                "label": label,
                "stage": stage,
            }
        )
        emit({"type": "stage", "stage": stage, "label": label, "pct": pct})

    structure = compute_structure_facts(snapshot)
    snapshot["structure"] = structure
    evidence = format_evidence_text(snapshot)
    n_dial = dialectic_rounds_for(scope, degree)

    # Sequential seats — parallel burst often trips provider 429 rate limits
    emit_progress(50, "走势席分析中…", stage="experts")

    emit({"type": "agent_start", "id": "trend", "label": AGENT_LABELS["trend"], "pct": 52})
    emit_progress(52, f"{AGENT_LABELS['trend']}分析中…", stage="trend")
    trend_memo = _run_seat(
        "trend",
        prompts.TREND_SYSTEM,
        prompts.evidence_user_block(evidence, scope=scope),
    )
    emit({"type": "agent_done", "agent": trend_memo})

    emit({"type": "agent_start", "id": "news", "label": AGENT_LABELS["news"], "pct": 55})
    emit_progress(55, f"{AGENT_LABELS['news']}分析中…", stage="news")
    news_memo = _run_seat(
        "news",
        prompts.NEWS_SYSTEM,
        prompts.evidence_user_block(evidence, scope=scope),
    )
    emit({"type": "agent_done", "agent": news_memo})

    # 资金情绪席：默认开；无证据时确定性「数据不足」，有证据走 LLM
    flow_memo: dict[str, Any] | None = None
    tier_agents = list(get_tier(degree).get("agents") or [])
    if "flow" in tier_agents:
        emit({"type": "agent_start", "id": "flow", "label": AGENT_LABELS["flow"], "pct": 57})
        emit_progress(57, f"{AGENT_LABELS['flow']}…", stage="flow")
        flow_memo = build_flow_seat_memo(snapshot)
        if flow_memo is None:
            flow_memo = _run_seat(
                "flow",
                prompts.FLOW_SYSTEM,
                prompts.evidence_user_block(evidence, scope=scope),
            )
        emit({"type": "agent_done", "agent": flow_memo})

    agents: list[dict[str, Any]] = [trend_memo, news_memo]
    if flow_memo:
        agents.append(flow_memo)
    debate: list[dict[str, Any]] = []
    dialectic_memo: dict[str, Any] | None = None
    emit_progress(58, "专家席完成", stage="experts_done")

    # 结构风险席：确定性；由档位 agents 勾选控制（标准/深度默认开）
    risk_memo: dict[str, Any] | None = None
    if "risk" in tier_agents:
        emit({"type": "agent_start", "id": "risk", "label": AGENT_LABELS["risk"], "pct": 59})
        emit_progress(59, f"{AGENT_LABELS['risk']}…", stage="risk")
        risk_memo = build_risk_seat_memo(snapshot)
        emit({"type": "agent_done", "agent": risk_memo})
        agents.append(risk_memo)

    if n_dial > 0:
        emit_progress(62, f"辩证席 · 共 {n_dial} 回合", stage="dialectic")
        prev_extra = ""
        for i in range(1, n_dial + 1):
            dial_pct = 62 + int(12 * i / max(n_dial, 1))
            emit(
                {
                    "type": "agent_start",
                    "id": "dialectic",
                    "label": f"{AGENT_LABELS['dialectic']} · 第{i}回合",
                    "round": i,
                    "pct": dial_pct,
                }
            )
            emit_progress(
                dial_pct,
                f"{AGENT_LABELS['dialectic']} · 第{i}/{n_dial}回合…",
                stage="dialectic",
            )
            def _pub(m: dict[str, Any]) -> dict[str, Any]:
                return {k: v for k, v in m.items() if k != "_raw"}

            memos_block = (
                f"【走势席】{json.dumps(_pub(trend_memo), ensure_ascii=False)}\n"
                f"【新闻席】{json.dumps(_pub(news_memo), ensure_ascii=False)}\n"
            )
            if flow_memo:
                memos_block += f"【资金情绪席】{json.dumps(_pub(flow_memo), ensure_ascii=False)}\n"
            if risk_memo:
                memos_block += f"【结构风险席】{json.dumps(_pub(risk_memo), ensure_ascii=False)}\n"
            memos_block += (
                f"【辩证回合】第 {i}/{n_dial} 回合\n"
                f"{prev_extra}"
            )
            dialectic_memo = _run_seat(
                "dialectic",
                prompts.DIALECTIC_SYSTEM,
                prompts.evidence_user_block(evidence, scope=scope, extra=memos_block),
            )
            dialectic_memo["round"] = i
            emit({"type": "agent_done", "agent": dialectic_memo})
            debate.append(
                {
                    "round": i,
                    "summary": dialectic_memo.get("summary"),
                    "stance": dialectic_memo.get("stance"),
                    "bull_points": dialectic_memo.get("bull_points") or [],
                    "bear_points": dialectic_memo.get("bear_points") or [],
                    "open_questions": dialectic_memo.get("open_questions") or [],
                    "bullets": dialectic_memo.get("bullets") or [],
                }
            )
            prev_extra = (
                f"【上一回合辩证】{json.dumps(_pub(dialectic_memo), ensure_ascii=False)}\n"
                "请针对 open_questions 继续交锋。"
            )
        if dialectic_memo:
            agents.append(dialectic_memo)
    else:
        emit_progress(70, "辩证席跳过", stage="dialectic")

    emit_progress(82, f"{AGENT_LABELS['judge']}汇总中…", stage="judge")
    emit({"type": "agent_start", "id": "judge", "label": AGENT_LABELS["judge"], "pct": 82})
    # Strip internal _raw before sending to next seat context
    def public_memo(m: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in m.items() if k != "_raw"}

    holding_lines = build_holding_lines(snapshot)
    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    if not structure:
        structure = compute_structure_facts(snapshot)
        snapshot["structure"] = structure

    open_qs: list[str] = []
    for d in debate:
        for q in d.get("open_questions") or []:
            s = str(q).strip()
            if s and s not in open_qs:
                open_qs.append(s)
    open_qs = open_qs[:6]

    judge_extra = (
        f"【走势席】{json.dumps(public_memo(trend_memo), ensure_ascii=False)}\n"
        f"【新闻席】{json.dumps(public_memo(news_memo), ensure_ascii=False)}\n"
        f"【结构事实】{json.dumps(structure, ensure_ascii=False)}\n"
    )
    if flow_memo:
        judge_extra += f"【资金情绪席】{json.dumps(public_memo(flow_memo), ensure_ascii=False)}\n"
    if risk_memo:
        judge_extra += f"【结构风险席】{json.dumps(public_memo(risk_memo), ensure_ascii=False)}\n"
    if debate:
        judge_extra += f"【辩证轨迹】{json.dumps(debate, ensure_ascii=False)}\n"
    if open_qs:
        judge_extra += (
            "【必须回应的未决问题】"
            f"{json.dumps(open_qs, ensure_ascii=False)}\n"
            "对每条：能下结论的写进 open_resolutions（问题+结论）；"
            "仍不确定的写进 unresolved。禁止假装没看见。\n"
        )
    if holding_lines:
        judge_extra += (
            "【持仓事实·仅供心里有数，总结里不要陈列这些数字】\n"
            + "\n".join(f"- {line}" for line in holding_lines)
            + "\n"
        )
    judge_memo = _run_seat(
        "judge",
        prompts.JUDGE_SYSTEM,
        prompts.evidence_user_block(evidence, scope=scope, extra=judge_extra),
    )
    emit({"type": "agent_done", "agent": public_memo(judge_memo)})
    agents.append(judge_memo)
    emit_progress(92, "整理总结报告…", stage="report")

    report = _assemble_report(
        snapshot,
        scope=scope,
        agents=agents,
        debate=debate,
        judge=judge_memo,
        open_questions=open_qs,
    )
    emit({"type": "report", "report": report})
    return report


def _build_watch_fallback(
    snapshot: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    stance: str,
) -> list[str]:
    """Deterministic 重点注意 when judge omits watch."""
    watch: list[str] = []
    quotes = list(snapshot.get("quotes") or [])
    ranked = sorted(quotes, key=lambda x: float(x.get("weight") or 0), reverse=True)

    for q in ranked[:3]:
        name = str(q.get("name") or q.get("symbol") or "").strip()
        if not name:
            continue
        w = q.get("weight")
        try:
            wf = float(w) if w is not None else None
        except (TypeError, ValueError):
            wf = None
        last = q.get("last_session_change_pct")
        try:
            last_f = float(last) if last is not None else None
        except (TypeError, ValueError):
            last_f = None
        pnl = q.get("pnl_pct")
        try:
            pnl_f = float(pnl) if pnl is not None and q.get("in_portfolio") else None
        except (TypeError, ValueError):
            pnl_f = None

        if wf is not None and wf >= 35:
            watch.append(f"重点看{name}：仓位偏重，组合跟着它走，波动要心里有数")
        elif last_f is not None and abs(last_f) >= 7:
            if last_f > 0:
                watch.append(f"重点看{name}：昨盘冲得猛，留意能不能站住，别追高")
            else:
                watch.append(f"重点看{name}：昨盘掉得快，可以考虑宜减不宜加")
        elif pnl_f is not None and abs(pnl_f) < 2:
            watch.append(f"重点看{name}：就在成本附近，方向一变体感会明显")

    if not watch:
        for it in items[:2]:
            name = str(it.get("name") or "").strip()
            if not name:
                continue
            st = it.get("stance") or stance
            if st == "偏空":
                watch.append(f"重点看{name}：偏弱，可以考虑宜减不宜加")
            elif st == "偏多":
                watch.append(f"重点看{name}：短线偏强，冲高别盲目加")
            else:
                watch.append(f"重点看{name}：先盯住，有动静再动")

    out: list[str] = []
    seen: set[str] = set()
    for line in watch:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out[:3]


def _watch_evidence_ref(line: str, snapshot: dict[str, Any]) -> str:
    """Short evidence citation for a watch sentence (name → fact)."""
    quotes = list(snapshot.get("quotes") or [])
    for q in quotes:
        name = str(q.get("name") or "").strip()
        sym = str(q.get("symbol") or "").strip()
        if not name and not sym:
            continue
        if (name and name in line) or (sym and sym in line):
            bits: list[str] = []
            if q.get("weight") is not None:
                try:
                    bits.append(f"仓位{float(q['weight']):.1f}%")
                except (TypeError, ValueError):
                    pass
            if q.get("day_pnl_pct") is not None:
                bits.append(f"今日盈亏{_fmt_pct(q.get('day_pnl_pct'))}")
            if q.get("change_pct") is not None:
                lab = str(q.get("day_label") or "今日")
                bits.append(f"行情{lab}{_fmt_pct(q.get('change_pct'))}")
            if q.get("prev_close") is not None:
                bits.append(f"昨收{q.get('prev_close')}")
            if bits:
                return " · ".join(bits)
    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    if structure.get("head_name") and str(structure["head_name"]) in line:
        return f"头部仓位 {structure.get('head_weight')}%"
    return ""


def _assemble_report(
    snapshot: dict[str, Any],
    *,
    scope: str = "portfolio",
    agents: list[dict[str, Any]],
    debate: list[dict[str, Any]],
    judge: dict[str, Any],
    open_questions: list[str] | None = None,
) -> dict[str, Any]:
    del scope  # reserved for future scope-specific plain templates
    data = judge.get("_raw") if isinstance(judge.get("_raw"), dict) else {}
    if judge.get("status") == "failed":
        verdict = "委员会汇总暂未完成，请稍后再试"
        stance = "数据不足"
        confidence = 0.2
    else:
        verdict = str(data.get("verdict") or judge.get("summary") or "分析完成").strip()
        # Never surface raw LLM HTTP / JSON blobs in the hero
        if "LLM HTTP" in verdict or "rate_limit" in verdict.lower():
            verdict = llm.friendly_llm_error(RuntimeError(verdict))
        stance = llm.normalize_stance(data.get("stance") or judge.get("stance"))
        confidence = llm.clamp_confidence(
            data.get("confidence"), float(judge.get("confidence") or 0.5)
        )
    # Calibrate confidence from seat health + evidence thinness
    failed_seats = sum(
        1
        for a in agents
        if a.get("id") in {"trend", "news", "flow"} and a.get("status") == "failed"
    )
    thin_news = not (snapshot.get("news") or [])
    structure = snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    fresh_share = float(structure.get("fresh_share") or 1.0)
    if failed_seats >= 2:
        confidence = min(confidence, 0.35)
        if stance not in {"数据不足"}:
            stance = "数据不足"
    elif failed_seats == 1 or thin_news:
        confidence = min(confidence, 0.55)
    if fresh_share < 0.5:
        confidence = min(confidence, 0.5)
    hl = data.get("highlights") if isinstance(data.get("highlights"), list) else []
    highlights = [
        str(x).strip()
        for x in hl
        if str(x).strip() and not _looks_like_data_dump(str(x))
    ][:2]
    if not highlights:
        raw_bullets = list(judge.get("bullets") or [])
        highlights = [
            str(x).strip()
            for x in raw_bullets
            if str(x).strip() and not _looks_like_data_dump(str(x))
        ][:2]
    act = data.get("actions") if isinstance(data.get("actions"), list) else []
    actions = [
        str(x).strip()
        for x in act
        if str(x).strip() and not _looks_like_data_dump(str(x))
    ][:2]
    raw_watch = data.get("watch") if isinstance(data.get("watch"), list) else []
    watch = [
        str(x).strip()
        for x in raw_watch
        if str(x).strip() and not _looks_like_data_dump(str(x))
    ][:3]
    # Deterministic risk inject (TradingAgents-style: risk constrains judge output)
    structure = structure or (
        snapshot.get("structure") if isinstance(snapshot.get("structure"), dict) else {}
    )
    head_w = structure.get("head_weight")
    head_n = str(structure.get("head_name") or "").strip()
    if isinstance(head_w, (int, float)) and float(head_w) >= 35 and head_n:
        if not any(head_n in w or "仓位" in w for w in watch):
            watch = [f"{head_n}仓位偏重，别再加仓"] + watch
    if thin_news and not any("消息" in w or "新闻" in w for w in watch):
        watch = watch + ["消息面偏空，别把短期波动当新闻驱动"]
    watch = watch[:3]

    open_resolutions = [
        str(x).strip()
        for x in (data.get("open_resolutions") or [])
        if str(x).strip()
    ][:6]
    unresolved = [
        str(x).strip()
        for x in (data.get("unresolved") or [])
        if str(x).strip()
    ][:4]
    pending_qs = list(open_questions or [])
    if pending_qs and not open_resolutions and not unresolved:
        unresolved = pending_qs[:3]

    quotes_list = list(snapshot.get("quotes") or [])
    quotes = {str(q.get("symbol") or ""): q for q in quotes_list}
    name_index = {
        str(q.get("name") or "").strip(): q
        for q in quotes_list
        if str(q.get("name") or "").strip()
    }
    items: list[dict[str, Any]] = []
    raw_items = data.get("items") if isinstance(data.get("items"), list) else []
    for it in raw_items[:32]:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol") or "").strip()
        q = quotes.get(sym) or {}
        if not q:
            # try zero-pad 6-digit / name match
            if sym.isdigit() and len(sym) < 6:
                sym2 = sym.zfill(6)
                q = quotes.get(sym2) or {}
                if q:
                    sym = sym2
            if not q:
                nm = str(it.get("name") or "").strip()
                q = name_index.get(nm) or {}
                if q:
                    sym = str(q.get("symbol") or sym)
        if not q:
            continue
        item_stance = llm.normalize_stance(it.get("stance"))
        name = str(it.get("name") or q.get("name") or sym)
        items.append(
            {
                "symbol": sym or str(q.get("symbol") or ""),
                "name": name,
                "market": str(q.get("market") or "SH"),
                "stance": item_stance,
                "change_pct": q.get("change_pct"),
                "weight": q.get("weight"),
                "summary": _plain_item_summary(
                    name, item_stance, str(it.get("summary") or "")
                ),
            }
        )
    if not items:
        ranked = sorted(
            quotes_list,
            key=lambda x: float(x.get("weight") or 0),
            reverse=True,
        )
        item_cap = len(ranked) if str(snapshot.get("scope") or "") == "portfolio" else 8
        for q in ranked[:item_cap]:
            name = str(q.get("name") or q.get("symbol") or "")
            items.append(
                {
                    "symbol": q.get("symbol"),
                    "name": name,
                    "market": q.get("market") or "SH",
                    "stance": stance,
                    "change_pct": q.get("change_pct"),
                    "weight": q.get("weight"),
                    "summary": _plain_item_summary(name, stance, ""),
                }
            )

    if _verdict_too_cloudy(verdict) or _looks_like_data_dump(verdict):
        verdict = _plain_fallback_verdict(snapshot, stance)

    if not watch:
        watch = _build_watch_fallback(snapshot, items, stance=stance)

    watch_refs = [_watch_evidence_ref(w, snapshot) for w in watch]

    judge_failed = judge.get("status") == "failed"
    for a in agents:
        if a.get("id") == "judge":
            a["summary"] = verdict
            a["stance"] = stance
            a["confidence"] = confidence
            a["bullets"] = highlights
            a["status"] = "failed" if judge_failed else "done"

    failed_labels = [
        str(a.get("label") or a.get("id") or "席位")
        for a in agents
        if a.get("status") == "failed"
    ]
    degraded = bool(failed_labels) or failed_seats > 0
    quality_note = ""
    if judge_failed:
        quality_note = "首席汇总失败，结论可信度低，建议稍后重跑。"
    elif failed_seats >= 2:
        quality_note = f"{'、'.join(failed_labels[:3])}未谈成，以下为降级结论。"
    elif failed_seats == 1:
        quality_note = f"{failed_labels[0]}未谈成，其余席位仍供参考。"

    return {
        "verdict": verdict,
        "stance": stance,
        "confidence": confidence,
        "highlights": highlights,
        "watch": watch,
        "watch_refs": watch_refs,
        "open_resolutions": open_resolutions,
        "unresolved": unresolved,
        "holding_lines": [],
        "items": items,
        "bullets": highlights,
        "structure": [],
        "actions": actions,
        "agents": [
            {
                "id": a.get("id"),
                "label": a.get("label"),
                "status": a.get("status") or "done",
                "summary": a.get("summary") or "",
                "stance": a.get("stance") or "中性",
                "confidence": a.get("confidence") or 0.5,
                "bullets": a.get("bullets") or [],
                "weight": a.get("weight"),
            }
            for a in agents
        ],
        "debate": debate,
        "template": False,
        "degraded": degraded,
        "failed_seats": failed_labels,
        "quality_note": quality_note,
    }



def iter_committee_events(
    snapshot: dict[str, Any],
    *,
    scope: str,
    degree: str,
) -> Iterator[dict[str, Any]]:
    """Yield events as the committee runs (background thread + queue)."""
    q: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def on_event(ev: dict[str, Any]) -> None:
        # Don't put huge _raw on the wire
        if ev.get("type") == "agent_done" and isinstance(ev.get("agent"), dict):
            agent = {k: v for k, v in ev["agent"].items() if k != "_raw"}
            q.put({**ev, "agent": agent})
        else:
            q.put(ev)

    err_box: list[BaseException] = []

    def worker() -> None:
        try:
            run_committee(snapshot, scope=scope, degree=degree, on_event=on_event)
        except BaseException as exc:  # noqa: BLE001 — surface to SSE
            err_box.append(exc)
            logger.exception("committee worker failed")
            q.put({"type": "error", "message": str(exc)[:400]})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while True:
        ev = q.get()
        if ev is None:
            break
        yield ev
