"""Deterministic news relevance ranking — trim before LLM context."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from app.providers.cn_calendar import parse_as_of_date, shanghai_today
from app.providers.news import NewsItem, news_age_label

# asset_kind → theme tokens (CN + common EN for RSS)
_KIND_THEMES: dict[str, tuple[str, ...]] = {
    "黄金积存": ("黄金", "金价", "金市", "美联储", "美元", "非农", "gold", "fed", "dollar"),
    "黄金ETF": ("黄金", "金价", "金市", "美联储", "美元", "非农", "gold", "fed", "dollar"),
    "场内ETF": ("ETF", "指数", "板块", "基金"),
    "场外基金": ("基金", "净值", "定投", "仓位"),
    "股票": ("A股", "沪深", "涨停", "业绩", "回购", "港股", "美股", "恒生", "纳斯达克"),
}

_MACRO_WORLD = (
    "美联储",
    "美股",
    "港股",
    "恒生",
    "纳斯达克",
    "非农",
    "美元",
    "原油",
    "油价",
    "国债",
    "降息",
    "加息",
    "fed",
    "powell",
    "treasury",
    "oil",
    "nasdaq",
    "s&p",
    "dow",
    "hang seng",
)

_ANN_HINTS = ("公告", "东方财富公告", "F10", "东方财富F10")


def _as_item_dict(item: NewsItem | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    if is_dataclass(item):
        return asdict(item)
    return {
        "id": getattr(item, "id", ""),
        "title": getattr(item, "title", "") or "",
        "summary": getattr(item, "summary", "") or "",
        "source": getattr(item, "source", "") or "",
        "published_at": getattr(item, "published_at", "") or "",
        "url": getattr(item, "url", "") or "",
        "symbols": list(getattr(item, "symbols", None) or []),
        "region": getattr(item, "region", "cn") or "cn",
        "board": getattr(item, "board", "") or "",
    }


def _days_ago(published_at: object) -> int | None:
    raw = str(published_at or "").strip()
    if not raw:
        return None
    d = parse_as_of_date(raw)
    if d is None:
        return None
    return (shanghai_today() - d).days


def _themes_for_kinds(asset_kinds: list[str] | None) -> set[str]:
    out: set[str] = set()
    for k in asset_kinds or []:
        for t in _KIND_THEMES.get(str(k), ()):
            out.add(t.lower())
        if "黄金" in str(k):
            out.update(t.lower() for t in _KIND_THEMES["黄金ETF"])
    return out


def _has_macro_exposure(asset_kinds: list[str] | None, interest_terms: list[str] | None) -> bool:
    kinds = [str(k) for k in (asset_kinds or [])]
    if any("黄金" in k for k in kinds):
        return True
    blob = " ".join(interest_terms or []).lower()
    return any(m.lower() in blob for m in _MACRO_WORLD)


def score_news_item(
    item: NewsItem | dict[str, Any],
    *,
    symbols: list[str] | None = None,
    names: list[str] | None = None,
    asset_kinds: list[str] | None = None,
    interest_terms: list[str] | None = None,
) -> tuple[float, str]:
    """Return (0..1-ish raw score before normalize, short why)."""
    row = _as_item_dict(item)
    title = str(row.get("title") or "")
    summary = str(row.get("summary") or "")
    text = f"{title} {summary}".lower()
    text_raw = f"{title} {summary}"
    source = str(row.get("source") or "")
    board = str(row.get("board") or "")
    region = str(row.get("region") or "cn").lower()
    item_syms = {str(s).strip() for s in (row.get("symbols") or []) if str(s).strip()}

    score = 0.0
    why_bits: list[str] = []

    sym_set = {str(s).strip() for s in (symbols or []) if str(s).strip()}
    name_list = [str(n).strip() for n in (names or []) if str(n).strip() and len(str(n).strip()) >= 2]

    # Symbol / name hits
    hit_sym = None
    for s in sym_set:
        if s in item_syms or s in text_raw or s.lower() in text:
            hit_sym = s
            break
    if hit_sym:
        score += 4.0
        why_bits.append(f"命中:{hit_sym}")
    else:
        for nm in name_list:
            if nm in text_raw or nm.lower() in text:
                score += 3.5
                why_bits.append(f"命中:{nm}")
                break

    # Board prior
    if board == "holding" or (item_syms & sym_set):
        score += 1.2
        if "持仓" not in "".join(why_bits):
            why_bits.append("持仓相关")
    if board == "headline":
        score += 0.35
    if board == "world" or region == "world":
        score += 0.2

    # Theme lexicon from asset kinds
    themes = _themes_for_kinds(asset_kinds)
    for term in (interest_terms or []):
        t = str(term).strip()
        if len(t) >= 2:
            themes.add(t.lower())
    theme_hit = None
    for th in themes:
        if th and th in text:
            theme_hit = th
            score += 1.5
            break
    if theme_hit:
        why_bits.append(f"主题:{theme_hit}")

    # Macro / world preference when portfolio has gold or macro interests
    macro_exp = _has_macro_exposure(asset_kinds, interest_terms)
    macro_in_text = any(m.lower() in text for m in _MACRO_WORLD)
    if macro_exp and (region == "world" or board == "world" or macro_in_text):
        score += 1.8
        why_bits.append("宏观相关")
    elif region == "world" or board == "world":
        # Pure A-share book: weak international background only
        if macro_in_text:
            score += 0.6
            why_bits.append("国际背景")
        else:
            score -= 0.4

    # Announcement / F10 boost for holdings
    if any(h in source for h in _ANN_HINTS) or "公告" in title:
        if hit_sym or (item_syms & sym_set) or board == "holding":
            score += 1.5
            why_bits.append("公告")

    # Recency
    days = _days_ago(row.get("published_at"))
    if days is None:
        score -= 0.3
    elif days <= 0:
        score += 1.2
        why_bits.append("今日")
    elif days == 1:
        score += 0.6
    elif days == 2:
        score += 0.2
    elif days >= 3:
        score -= min(2.0, 0.5 * days)
        if days >= 5:
            why_bits.append(news_age_label(row.get("published_at")))

    if not why_bits:
        why_bits.append("弱相关")

    return score, "·".join(why_bits[:3])


def rank_and_trim_news(
    items: list[NewsItem | dict[str, Any]],
    *,
    limit: int,
    symbols: list[str] | None = None,
    names: list[str] | None = None,
    asset_kinds: list[str] | None = None,
    interest_terms: list[str] | None = None,
    min_score: float = 0.35,
) -> list[dict[str, Any]]:
    """Score, apply holding/world quotas, return trimmed dicts with relevance fields."""
    lim = max(1, int(limit or 1))
    if not items:
        return []

    scored: list[tuple[float, str, dict[str, Any]]] = []
    raw_scores: list[float] = []
    for it in items:
        sc, why = score_news_item(
            it,
            symbols=symbols,
            names=names,
            asset_kinds=asset_kinds,
            interest_terms=interest_terms,
        )
        row = _as_item_dict(it)
        raw_scores.append(sc)
        scored.append((sc, why, row))

    # Normalize to ~0..1 for evidence display
    mx = max(raw_scores) if raw_scores else 1.0
    mn = min(raw_scores) if raw_scores else 0.0
    span = (mx - mn) or 1.0

    enriched: list[dict[str, Any]] = []
    for sc, why, row in scored:
        rel = round(max(0.0, min(1.0, (sc - mn) / span)), 3)
        # Absolute floor: very negative raw → drop later
        row = dict(row)
        row["relevance"] = rel
        row["relevance_why"] = why
        row["_raw_score"] = sc
        enriched.append(row)

    enriched.sort(key=lambda r: float(r.get("_raw_score") or 0), reverse=True)

    # Drop weak noise unless pool is tiny
    kept = [r for r in enriched if float(r.get("_raw_score") or 0) >= min_score]
    if len(kept) < min(3, lim) and enriched:
        kept = enriched[: max(lim, 3)]

    # Quotas: holdings-related at least ~half; world/macro cap
    holding_min = max(1, lim // 2) if symbols else 0
    world_max = 3 if lim >= 12 else (2 if lim >= 6 else 1)
    if _has_macro_exposure(asset_kinds, interest_terms):
        world_max = min(lim, world_max + 1)

    def _is_holding(r: dict[str, Any]) -> bool:
        if str(r.get("board") or "") == "holding":
            return True
        why = str(r.get("relevance_why") or "")
        if "命中:" in why or "持仓" in why or "公告" in why:
            return True
        item_syms = {str(s) for s in (r.get("symbols") or [])}
        return bool(item_syms & {str(s) for s in (symbols or [])})

    def _is_world(r: dict[str, Any]) -> bool:
        return str(r.get("region") or "") == "world" or str(r.get("board") or "") == "world"

    selected: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _key(r: dict[str, Any]) -> str:
        return str(r.get("url") or r.get("id") or r.get("title") or "")

    def _take(pred, n: int) -> None:
        nonlocal selected
        if n <= 0:
            return
        got = 0
        for r in kept:
            if got >= n:
                break
            k = _key(r)
            if not k or k in seen_keys:
                continue
            if not pred(r):
                continue
            seen_keys.add(k)
            selected.append(r)
            got += 1

    # 1) fill holding quota
    if holding_min:
        _take(_is_holding, holding_min)
    # 2) world quota
    _take(_is_world, world_max)
    # 3) fill remainder by score
    for r in kept:
        if len(selected) >= lim:
            break
        k = _key(r)
        if not k or k in seen_keys:
            continue
        # Cap extra world beyond world_max
        if _is_world(r) and sum(1 for x in selected if _is_world(x)) >= world_max:
            continue
        seen_keys.add(k)
        selected.append(r)

    # If still short, relax world cap
    if len(selected) < lim:
        for r in kept:
            if len(selected) >= lim:
                break
            k = _key(r)
            if not k or k in seen_keys:
                continue
            seen_keys.add(k)
            selected.append(r)

    out: list[dict[str, Any]] = []
    for r in selected[:lim]:
        r = dict(r)
        r.pop("_raw_score", None)
        # Trim summary for evidence
        sm = str(r.get("summary") or "")
        if len(sm) > 280:
            r["summary"] = sm[:280]
        out.append(r)
    return out


def news_items_to_dicts(items: list[NewsItem], *, board: str = "") -> list[dict[str, Any]]:
    rows = []
    for i in items:
        d = asdict(i) if is_dataclass(i) else _as_item_dict(i)
        if board:
            d["board"] = board
        rows.append(d)
    return rows
