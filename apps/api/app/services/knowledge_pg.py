"""Knowledge cards in Postgres + pgvector (business data stays on SQLite)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, unquote, urlparse

import psycopg
from psycopg.rows import dict_row

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# text-embedding-v4 / qwen3.7-text-embedding default dim on Bailian
EMBED_DIM = 1024

# Admin form defaults (host left blank for user)
KB_DEFAULTS = {
    "host": "",
    "port": "5437",
    "user": "postgres",
    "password": "postgres",
    "dbname": "anzai_knowledge",
}


def knowledge_db_url() -> str:
    return (get_settings().knowledge_database_url or "").strip()


def knowledge_db_configured() -> bool:
    return bool(knowledge_db_url())


def parse_knowledge_db_url(url: str | None = None) -> dict[str, str]:
    """Split connection URL into admin form fields; fill defaults for missing parts."""
    out = dict(KB_DEFAULTS)
    raw = (url if url is not None else knowledge_db_url()).strip()
    if not raw:
        return out
    try:
        u = urlparse(raw)
    except Exception:
        return out
    if u.hostname:
        out["host"] = u.hostname
    if u.port:
        out["port"] = str(u.port)
    if u.username:
        out["user"] = unquote(u.username)
    if u.password is not None:
        out["password"] = unquote(u.password)
    path = (u.path or "").lstrip("/")
    if path:
        out["dbname"] = path.split("/")[0]
    return out


def build_knowledge_db_url(
    *,
    host: str,
    port: str = "",
    user: str = "",
    password: str = "",
    dbname: str = "",
) -> str:
    """Build postgresql:// URL. Empty host → empty URL (disable PG knowledge)."""
    h = (host or "").strip()
    if not h:
        return ""
    p = (port or "").strip() or KB_DEFAULTS["port"]
    u = (user or "").strip() or KB_DEFAULTS["user"]
    pw = password if password is not None else KB_DEFAULTS["password"]
    db = (dbname or "").strip() or KB_DEFAULTS["dbname"]
    return (
        f"postgresql://{quote(u, safe='')}:{quote(pw, safe='')}@{h}:{p}/{db}"
    )


def _connect() -> psycopg.Connection:
    url = knowledge_db_url()
    if not url:
        raise RuntimeError("KNOWLEDGE_DATABASE_URL 未配置")
    # Accept SQLAlchemy-style prefix
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://") :]
    elif url.startswith("postgres+psycopg://"):
        url = "postgresql://" + url[len("postgres+psycopg://") :]
    return psycopg.connect(url, connect_timeout=8, row_factory=dict_row)


def ensure_schema() -> None:
    with _connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS knowledge_cards (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    tags TEXT[] NOT NULL DEFAULT '{{}}',
                    source TEXT NOT NULL DEFAULT '',
                    card_date TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    path TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL,
                    embedding vector({EMBED_DIM}),
                    embedding_model TEXT NOT NULL DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # HNSW for cosine distance; safe on small corpora
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS knowledge_cards_embedding_hnsw
                ON knowledge_cards
                USING hnsw (embedding vector_cosine_ops)
                """
            )


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{float(x):.8g}" for x in vec) + "]"


def fetch_card_meta() -> dict[str, dict[str, Any]]:
    """id -> {content_hash, embedding_model, has_embedding}."""
    ensure_schema()
    out: dict[str, dict[str, Any]] = {}
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, content_hash, embedding_model,
                       (embedding IS NOT NULL) AS has_embedding
                FROM knowledge_cards
                """
            )
            for row in cur.fetchall():
                out[str(row["id"])] = {
                    "content_hash": str(row["content_hash"] or ""),
                    "embedding_model": str(row["embedding_model"] or ""),
                    "has_embedding": bool(row["has_embedding"]),
                }
    return out


def upsert_card(
    *,
    card_id: str,
    title: str,
    tags: list[str],
    source: str,
    card_date: str,
    body: str,
    path: str,
    content_hash: str,
    embedding: list[float] | None,
    embedding_model: str,
) -> None:
    ensure_schema()
    if embedding is not None and len(embedding) != EMBED_DIM:
        raise ValueError(
            f"embedding dim {len(embedding)} != {EMBED_DIM} "
            f"(模型需与表结构一致，当前按 text-embedding-v4=1024)"
        )
    params = {
        "id": card_id,
        "title": title,
        "tags": tags,
        "source": source,
        "card_date": card_date,
        "body": body,
        "path": path,
        "content_hash": content_hash,
        "embedding_model": embedding_model or "",
    }
    with _connect() as conn:
        with conn.cursor() as cur:
            if embedding is None:
                cur.execute(
                    """
                    INSERT INTO knowledge_cards (
                        id, title, tags, source, card_date, body, path,
                        content_hash, embedding, embedding_model, updated_at
                    ) VALUES (
                        %(id)s, %(title)s, %(tags)s, %(source)s, %(card_date)s, %(body)s, %(path)s,
                        %(content_hash)s, NULL, %(embedding_model)s, now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        tags = EXCLUDED.tags,
                        source = EXCLUDED.source,
                        card_date = EXCLUDED.card_date,
                        body = EXCLUDED.body,
                        path = EXCLUDED.path,
                        content_hash = EXCLUDED.content_hash,
                        updated_at = now()
                    """,
                    params,
                )
            else:
                params["emb"] = _vec_literal(embedding)
                cur.execute(
                    """
                    INSERT INTO knowledge_cards (
                        id, title, tags, source, card_date, body, path,
                        content_hash, embedding, embedding_model, updated_at
                    ) VALUES (
                        %(id)s, %(title)s, %(tags)s, %(source)s, %(card_date)s, %(body)s, %(path)s,
                        %(content_hash)s, (%(emb)s)::vector, %(embedding_model)s, now()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        tags = EXCLUDED.tags,
                        source = EXCLUDED.source,
                        card_date = EXCLUDED.card_date,
                        body = EXCLUDED.body,
                        path = EXCLUDED.path,
                        content_hash = EXCLUDED.content_hash,
                        embedding = EXCLUDED.embedding,
                        embedding_model = EXCLUDED.embedding_model,
                        updated_at = now()
                    """,
                    params,
                )
        conn.commit()


def delete_missing(live_ids: set[str]) -> int:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            if not live_ids:
                cur.execute("DELETE FROM knowledge_cards")
                n = cur.rowcount
            else:
                cur.execute(
                    "DELETE FROM knowledge_cards WHERE NOT (id = ANY(%s))",
                    (list(live_ids),),
                )
                n = cur.rowcount
        conn.commit()
    return int(n or 0)


def count_cards() -> tuple[int, int]:
    """Return (total, with_embedding)."""
    if not knowledge_db_configured():
        return 0, 0
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM knowledge_cards")
            total = int(cur.fetchone()["n"])
            cur.execute(
                "SELECT count(*) AS n FROM knowledge_cards WHERE embedding IS NOT NULL"
            )
            with_emb = int(cur.fetchone()["n"])
    return total, with_emb


def search_by_vector(
    query_vec: list[float],
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Cosine distance search; returns rows with vec_score in [0,1] approx."""
    if len(query_vec) != EMBED_DIM:
        raise ValueError(f"query dim {len(query_vec)} != {EMBED_DIM}")
    ensure_schema()
    lim = max(1, min(int(limit), 20))
    lit = _vec_literal(query_vec)
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, tags, source, card_date, body, path, content_hash,
                       (1 - (embedding <=> %(q)s::vector)) AS vec_score
                FROM knowledge_cards
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %(q)s::vector
                LIMIT %(lim)s
                """,
                {"q": lit, "lim": lim},
            )
            return list(cur.fetchall())


def load_all_cards() -> list[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, tags, source, card_date, body, path, content_hash,
                       embedding_model,
                       (embedding IS NOT NULL) AS has_embedding,
                       updated_at
                FROM knowledge_cards
                ORDER BY updated_at DESC NULLS LAST, id
                """
            )
            return list(cur.fetchall())


def list_cards() -> list[dict[str, Any]]:
    """Admin list (same columns as load_all_cards)."""
    return load_all_cards()


def get_card(card_id: str) -> dict[str, Any] | None:
    cid = (card_id or "").strip()
    if not cid:
        return None
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, tags, source, card_date, body, path, content_hash,
                       embedding_model,
                       (embedding IS NOT NULL) AS has_embedding,
                       updated_at
                FROM knowledge_cards
                WHERE id = %s
                """,
                (cid,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_card(card_id: str) -> bool:
    cid = (card_id or "").strip()
    if not cid:
        return False
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM knowledge_cards WHERE id = %s", (cid,))
            n = cur.rowcount
        conn.commit()
    return int(n or 0) > 0
