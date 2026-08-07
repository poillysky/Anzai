"""安崽经验知识库 — Markdown 种子 + Postgres/pgvector（优先）/ 本地 JSON 兜底。"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
INDEX_PATH = KNOWLEDGE_DIR / ".index.json"
_INDEX_VERSION = 1
_STALE_DAYS = 180
@dataclass
class KnowledgeCard:
    id: str
    title: str
    tags: list[str]
    source: str
    date: str
    body: str
    path: str
    content_hash: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    text = (raw or "").replace("\r\n", "\n")
    if not text.startswith("---"):
        return {}, text.strip()
    rest = text[3:].lstrip("\n")
    end = rest.find("\n---")
    if end < 0:
        return {}, text.strip()
    fm = rest[:end]
    body = rest[end + 4 :].lstrip("\n").strip()
    meta: dict[str, str] = {}
    for line in fm.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip().strip("\"'")
    return meta, body


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def load_cards() -> list[KnowledgeCard]:
    if not KNOWLEDGE_DIR.is_dir():
        return []
    cards: list[KnowledgeCard] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("read knowledge card failed: %s", path)
            continue
        meta, body = _parse_frontmatter(raw)
        cid = (meta.get("id") or path.stem).strip()
        title = (meta.get("title") or cid).strip()
        tags_raw = meta.get("tags") or ""
        tags = [t.strip() for t in re.split(r"[,，]", tags_raw) if t.strip()]
        source = (meta.get("source") or "经验库").strip()
        dt = (meta.get("date") or "").strip()
        blob = f"{cid}\n{title}\n{','.join(tags)}\n{body}"
        cards.append(
            KnowledgeCard(
                id=cid,
                title=title,
                tags=tags,
                source=source,
                date=dt,
                body=body,
                path=str(path.name),
                content_hash=_hash_text(blob),
            )
        )
    return cards


def _bigrams(text: str) -> dict[str, float]:
    s = re.sub(r"\s+", "", (text or "").lower())
    if not s:
        return {}
    grams: dict[str, float] = {}
    if len(s) == 1:
        grams[s] = 1.0
        return grams
    for i in range(len(s) - 1):
        g = s[i : i + 2]
        grams[g] = grams.get(g, 0.0) + 1.0
    # L2 normalize
    norm = math.sqrt(sum(v * v for v in grams.values())) or 1.0
    return {k: v / norm for k, v in grams.items()}


def _cosine_sparse(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(v * b.get(k, 0.0) for k, v in a.items())


def _cosine_dense(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _parse_card_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _age_factor(card_date: str) -> float:
    d = _parse_card_date(card_date)
    if d is None:
        return 0.95
    delta = (date.today() - d).days
    if delta <= _STALE_DAYS:
        return 1.0
    # soft demote older strategy notes
    return 0.7


# After hard failures, skip further embedding calls until process restart / model change
_embed_disabled_reason: str | None = None


def reset_embed_cooldown() -> None:
    """Call after embedding connection settings change."""
    global _embed_disabled_reason
    _embed_disabled_reason = None


def _embed_texts(texts: list[str]) -> list[list[float] | None]:
    """OpenAI-compatible embeddings via dedicated embedding connection (not chat LLM)."""
    global _embed_disabled_reason
    if not texts:
        return []
    if _embed_disabled_reason:
        return [None] * len(texts)
    from app.services.embedding_connection import resolve_creds

    base, key, model = resolve_creds()
    if not key or not model or not base:
        return [None] * len(texts)

    url = f"{base}/embeddings"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    out: list[list[float] | None] = [None] * len(texts)
    batch_size = 16
    got_any = False
    try:
        with httpx.Client(timeout=30.0, headers=headers) as client:
            for start in range(0, len(texts), batch_size):
                chunk = texts[start : start + batch_size]
                resp = client.post(url, json={"model": model, "input": chunk})
                if resp.status_code >= 400:
                    logger.warning(
                        "embedding HTTP %s — fallback to keyword (disable further tries)",
                        resp.status_code,
                    )
                    _embed_disabled_reason = f"http_{resp.status_code}"
                    break
                data = resp.json()
                rows = data.get("data") if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    _embed_disabled_reason = "bad_payload"
                    break
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    idx = row.get("index")
                    emb = row.get("embedding")
                    if isinstance(idx, int) and isinstance(emb, list) and 0 <= idx < len(chunk):
                        out[start + idx] = [float(x) for x in emb]
                        got_any = True
    except Exception:
        logger.exception("embedding request failed — keyword fallback")
        _embed_disabled_reason = "exception"
    if not got_any and _embed_disabled_reason is None:
        _embed_disabled_reason = "empty"
    return out


def _load_index() -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {"version": _INDEX_VERSION, "model": "", "cards": {}}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("cards"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        logger.exception("read knowledge index failed")
    return {"version": _INDEX_VERSION, "model": "", "cards": {}}


def _save_index(index: dict[str, Any]) -> None:
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def sync_markdown_to_postgres(cards: list[KnowledgeCard] | None = None) -> dict[str, Any]:
    """Upsert Markdown seeds into Postgres + embed stale rows."""
    from app.services import knowledge_pg as pg
    from app.services.embedding_connection import resolve_creds

    if not pg.knowledge_db_configured():
        return {"ok": False, "reason": "no_db"}
    cards = cards if cards is not None else load_cards()
    pg.ensure_schema()
    meta = pg.fetch_card_meta()
    _base, _key, model = resolve_creds()
    if not _base or not _key:
        model = ""

    need: list[KnowledgeCard] = []
    for c in cards:
        row = meta.get(c.id)
        stale = (
            row is None
            or row.get("content_hash") != c.content_hash
            or (bool(model) and row.get("embedding_model") != model)
            or (bool(model) and not row.get("has_embedding"))
        )
        if stale:
            need.append(c)

    vectors: list[list[float] | None] = [None] * len(need)
    if need and model and not _embed_disabled_reason:
        texts = [f"{c.title}\n{' '.join(c.tags)}\n{c.body}" for c in need]
        vectors = _embed_texts(texts)

    upserted = 0
    for c, vec in zip(need, vectors):
        pg.upsert_card(
            card_id=c.id,
            title=c.title,
            tags=c.tags,
            source=c.source,
            card_date=c.date,
            body=c.body,
            path=c.path,
            content_hash=c.content_hash,
            embedding=vec if isinstance(vec, list) else None,
            embedding_model=model if isinstance(vec, list) else "",
        )
        upserted += 1

    # Cards unchanged still ensure row exists (first run without re-embed)
    for c in cards:
        if c.id in {x.id for x in need}:
            continue
        if c.id not in meta:
            pg.upsert_card(
                card_id=c.id,
                title=c.title,
                tags=c.tags,
                source=c.source,
                card_date=c.date,
                body=c.body,
                path=c.path,
                content_hash=c.content_hash,
                embedding=None,
                embedding_model="",
            )
            upserted += 1

    # 不 delete_missing：后台新建卡以 DB 为准，MD 只作导入/补种
    total, with_emb = pg.count_cards()
    return {
        "ok": True,
        "upserted": upserted,
        "deleted": 0,
        "total": total,
        "with_embedding": with_emb,
        "model": model,
    }


def _slug_card_id(title: str, explicit: str = "") -> str:
    raw = (explicit or "").strip() or (title or "").strip()
    slug = re.sub(r"[^\w\-]+", "-", raw, flags=re.UNICODE).strip("-").lower()
    if slug and len(slug) <= 64:
        return slug
    return "kb-" + _hash_text(raw or "card")


def save_card(
    *,
    card_id: str = "",
    title: str,
    tags: list[str] | str | None = None,
    source: str = "",
    card_date: str = "",
    body: str,
    path: str = "admin",
    reembed: bool = True,
) -> dict[str, Any]:
    """Create/update a card in Postgres and optionally refresh embedding."""
    from app.services import knowledge_pg as pg
    from app.services.embedding_connection import resolve_creds

    if not pg.knowledge_db_configured():
        raise RuntimeError("未配置知识库 Postgres")
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise ValueError("标题和正文不能为空")
    if isinstance(tags, str):
        tag_list = [t.strip() for t in re.split(r"[,，]", tags) if t.strip()]
    else:
        tag_list = [str(t).strip() for t in (tags or []) if str(t).strip()]
    cid = _slug_card_id(title, card_id)
    source = (source or "安崽经验库").strip() or "安崽经验库"
    card_date = (card_date or "").strip()
    path = (path or "admin").strip() or "admin"
    blob = f"{cid}\n{title}\n{','.join(tag_list)}\n{body}"
    content_hash = _hash_text(blob)

    emb: list[float] | None = None
    model = ""
    if reembed:
        _b, _k, model = resolve_creds()
        if _b and _k and model:
            reset_embed_cooldown()
            vectors = _embed_texts([f"{title}\n{' '.join(tag_list)}\n{body}"])
            if vectors and isinstance(vectors[0], list):
                emb = vectors[0]
            else:
                model = ""

    pg.upsert_card(
        card_id=cid,
        title=title,
        tags=tag_list,
        source=source,
        card_date=card_date,
        body=body,
        path=path,
        content_hash=content_hash,
        embedding=emb,
        embedding_model=model if emb else "",
    )
    return {
        "id": cid,
        "embedded": emb is not None,
        "model": model if emb else "",
    }


def delete_card(card_id: str) -> bool:
    from app.services import knowledge_pg as pg

    if not pg.knowledge_db_configured():
        raise RuntimeError("未配置知识库 Postgres")
    return pg.delete_card(card_id)


def reembed_card(card_id: str) -> dict[str, Any]:
    from app.services import knowledge_pg as pg

    row = pg.get_card(card_id)
    if not row:
        raise ValueError("卡片不存在")
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        tags = list(tags)
    return save_card(
        card_id=str(row["id"]),
        title=str(row.get("title") or ""),
        tags=[str(t) for t in tags],
        source=str(row.get("source") or ""),
        card_date=str(row.get("card_date") or ""),
        body=str(row.get("body") or ""),
        path=str(row.get("path") or "admin"),
        reembed=True,
    )


def ensure_index(cards: list[KnowledgeCard] | None = None) -> dict[str, Any]:
    """Sync embeddings: Postgres when configured, else local .index.json."""
    global _embed_disabled_reason
    cards = cards if cards is not None else load_cards()
    from app.services import knowledge_pg as pg

    if pg.knowledge_db_configured():
        try:
            return sync_markdown_to_postgres(cards)
        except Exception:
            logger.exception("postgres knowledge sync failed; fallback local index")

    index = _load_index()
    from app.services.embedding_connection import resolve_creds

    _base, _key, model = resolve_creds()
    if not _base or not _key:
        model = ""
    stored_model = str(index.get("model") or "")
    card_map: dict[str, Any] = dict(index.get("cards") or {})

    live_ids = {c.id for c in cards}
    for cid in list(card_map.keys()):
        if cid not in live_ids:
            del card_map[cid]

    need_embed: list[KnowledgeCard] = []
    model_changed = bool(model) and stored_model != model
    if model_changed:
        _embed_disabled_reason = None
    for c in cards:
        entry = card_map.get(c.id) if isinstance(card_map.get(c.id), dict) else None
        has_emb = bool(entry and isinstance(entry.get("embedding"), list) and entry.get("embedding"))
        skipped = bool(entry and entry.get("embed_skip") and entry.get("hash") == c.content_hash)
        stale = (
            entry is None
            or entry.get("hash") != c.content_hash
            or model_changed
            or (bool(model) and not has_emb and not skipped)
        )
        if stale:
            need_embed.append(c)
        elif entry is not None:
            card_map[c.id] = entry

    if need_embed and model and not _embed_disabled_reason:
        texts = [f"{c.title}\n{' '.join(c.tags)}\n{c.body}" for c in need_embed]
        vectors = _embed_texts(texts)
        for c, vec in zip(need_embed, vectors):
            card_map[c.id] = {
                "hash": c.content_hash,
                "embedding": vec,
                "embed_skip": vec is None,
            }
    else:
        for c in need_embed:
            prev = card_map.get(c.id) if isinstance(card_map.get(c.id), dict) else {}
            card_map[c.id] = {
                "hash": c.content_hash,
                "embedding": prev.get("embedding") if prev.get("hash") == c.content_hash else None,
                "embed_skip": True,
            }

    index = {
        "version": _INDEX_VERSION,
        "model": model,
        "cards": card_map,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        _save_index(index)
    except OSError:
        logger.exception("write knowledge index failed")
    return index


def _lex_boost(query: str, card: KnowledgeCard) -> tuple[float, float]:
    """Return (lex_score, boost)."""
    q = query
    ql = q.lower()
    blob = f"{card.title} {' '.join(card.tags)} {card.body}"
    lex = _cosine_sparse(_bigrams(q), _bigrams(blob))
    boost = 0.0
    for t in card.tags:
        tl = t.lower()
        if not tl:
            continue
        if tl in ql:
            boost += 0.16
        elif len(tl) >= 2 and any(tl[i : i + 2] in ql for i in range(len(tl) - 1)):
            boost += 0.06
    for needle in ("追", "减仓", "加仓", "分散", "定投", "新闻", "盈亏", "宏观"):
        if needle in ql and needle in blob:
            boost += 0.1
            break
    if card.title and any(part in card.title for part in re.findall(r"[\u4e00-\u9fff]{2,}", q)):
        boost += 0.05
    return lex, boost


def _row_to_card(row: dict[str, Any]) -> KnowledgeCard:
    tags = row.get("tags") or []
    if not isinstance(tags, list):
        tags = list(tags) if tags else []
    body = str(row.get("body") or "")
    title = str(row.get("title") or "")
    cid = str(row.get("id") or "")
    blob = f"{cid}\n{title}\n{','.join(str(t) for t in tags)}\n{body}"
    return KnowledgeCard(
        id=cid,
        title=title,
        tags=[str(t) for t in tags],
        source=str(row.get("source") or "经验库"),
        date=str(row.get("card_date") or ""),
        body=body,
        path=str(row.get("path") or ""),
        content_hash=str(row.get("content_hash") or _hash_text(blob)),
    )


def search_cards(query: str, *, limit: int = 5) -> list[tuple[KnowledgeCard, float, str]]:
    """Return (card, score, channel) sorted by score desc."""
    q = (query or "").strip()
    if not q:
        return []
    lim = max(1, min(int(limit or 5), 8))
    from app.services import knowledge_pg as pg

    if pg.knowledge_db_configured():
        try:
            return _search_postgres(q, limit=lim)
        except Exception:
            logger.exception("postgres knowledge search failed; fallback local")

    cards = load_cards()
    if not cards:
        return []
    index = ensure_index(cards)

    q_vec: list[float] | None = None
    from app.services.embedding_connection import resolve_creds

    _b, _k, _m = resolve_creds()
    if _b and _k and _m:
        embedded = _embed_texts([q])
        if embedded and embedded[0]:
            q_vec = embedded[0]

    scored: list[tuple[KnowledgeCard, float, str]] = []
    for c in cards:
        entry = (index.get("cards") or {}).get(c.id) or {}
        emb = entry.get("embedding") if isinstance(entry, dict) else None
        lex, boost = _lex_boost(q, c)
        channel = "关键词"
        if q_vec and isinstance(emb, list) and emb:
            vec_score = _cosine_dense(q_vec, [float(x) for x in emb])
            channel = "向量+关键词"
            score = 0.65 * vec_score + 0.35 * lex + boost
        else:
            score = lex + boost
        score *= _age_factor(c.date)
        if score > 0.05:
            scored.append((c, score, channel))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:lim]


def _search_postgres(query: str, *, limit: int) -> list[tuple[KnowledgeCard, float, str]]:
    from app.services import knowledge_pg as pg
    from app.services.embedding_connection import resolve_creds

    sync_markdown_to_postgres()
    _b, _k, _m = resolve_creds()
    q_vec: list[float] | None = None
    if _b and _k and _m:
        embedded = _embed_texts([query])
        if embedded and embedded[0]:
            q_vec = embedded[0]

    scored: list[tuple[KnowledgeCard, float, str]] = []
    if q_vec:
        rows = pg.search_by_vector(q_vec, limit=max(limit * 3, 12))
        for row in rows:
            c = _row_to_card(row)
            lex, boost = _lex_boost(query, c)
            vec_score = float(row.get("vec_score") or 0.0)
            score = (0.65 * vec_score + 0.35 * lex + boost) * _age_factor(c.date)
            if score > 0.05:
                scored.append((c, score, "pgvector+关键词"))
    else:
        # keyword-only over PG rows
        for row in pg.load_all_cards():
            c = _row_to_card(row)
            lex, boost = _lex_boost(query, c)
            score = (lex + boost) * _age_factor(c.date)
            if score > 0.05:
                scored.append((c, score, "关键词"))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def format_search_text(query: str, *, limit: int = 5) -> str:
    hits = search_cards(query, limit=limit)
    from app.services import knowledge_pg as pg

    backend = "Postgres/pgvector" if pg.knowledge_db_configured() else "本地索引"
    lines = [
        f"【经验库·非实时 · {backend}】方法论/纪律参考，不是行情也不是新闻。",
        "引用规则：先讲本轮 Tools 里的数字；经验只嵌一两句框架；勿把库内叙述当今日点位。",
    ]
    if not hits:
        lines.append("（未命中相关条目；别编造经验。）")
        return "\n".join(lines)
    for i, (c, score, channel) in enumerate(hits, 1):
        tags = "、".join(c.tags[:4]) if c.tags else "—"
        age = c.date or "日期未知"
        body = re.sub(r"\s+", " ", c.body).strip()
        if len(body) > 220:
            body = body[:220] + "…"
        lines.append(
            f"{i}. {c.title}（{c.source} · {age} · {channel} · 相关度 {score:.2f}）"
            f" 标签:{tags} — {body}"
        )
    return "\n".join(lines)
