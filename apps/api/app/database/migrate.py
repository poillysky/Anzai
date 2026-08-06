"""Idempotent SQLite schema patches for multi-user (user_id columns)."""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_USER_SCOPED_TABLES = (
    "holdings",
    "watchlist",
    "news_interests",
    "preferences",
    "analysis_profile",
    "analysis_jobs",
    "agent_messages",
)


def _table_columns(engine: Engine, table: str) -> set[str]:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def ensure_user_id_columns(engine: Engine) -> None:
    """ADD COLUMN user_id INTEGER DEFAULT 0 when missing (SQLite-safe)."""
    with engine.begin() as conn:
        for table in _USER_SCOPED_TABLES:
            cols = _table_columns(engine, table)
            if not cols:
                continue
            if "user_id" in cols:
                continue
            logger.info("migrate: adding user_id to %s", table)
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN user_id INTEGER DEFAULT 0"))


def claim_orphan_rows(engine: Engine, user_id: int) -> int:
    """Assign rows with user_id NULL/0 to the given user (first bootstrap)."""
    total = 0
    with engine.begin() as conn:
        for table in _USER_SCOPED_TABLES:
            cols = _table_columns(engine, table)
            if "user_id" not in cols:
                continue
            result = conn.execute(
                text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL OR user_id = 0"),
                {"uid": user_id},
            )
            total += result.rowcount or 0
    if total:
        logger.info("migrate: claimed %s orphan rows for user_id=%s", total, user_id)
    return total


def ensure_preference_identity_columns(engine: Engine) -> None:
    """ADD identity_role / identity_label on preferences when missing."""
    cols = _table_columns(engine, "preferences")
    if not cols:
        return
    with engine.begin() as conn:
        if "identity_role" not in cols:
            logger.info("migrate: adding identity_role to preferences")
            conn.execute(text("ALTER TABLE preferences ADD COLUMN identity_role VARCHAR(32) DEFAULT ''"))
        if "identity_label" not in cols:
            logger.info("migrate: adding identity_label to preferences")
            conn.execute(text("ALTER TABLE preferences ADD COLUMN identity_label VARCHAR(32) DEFAULT ''"))


def ensure_holdings_bought_at(engine: Engine) -> None:
    """ADD holdings.bought_at; backfill from created_at date (or leave empty)."""
    cols = _table_columns(engine, "holdings")
    if not cols:
        return
    with engine.begin() as conn:
        if "bought_at" not in cols:
            logger.info("migrate: adding bought_at to holdings")
            conn.execute(text("ALTER TABLE holdings ADD COLUMN bought_at VARCHAR(10) DEFAULT ''"))
        # Fill empty from created_at calendar day when present
        try:
            conn.execute(
                text(
                    """
                    UPDATE holdings
                    SET bought_at = substr(created_at, 1, 10)
                    WHERE (bought_at IS NULL OR bought_at = '')
                      AND created_at IS NOT NULL
                    """
                )
            )
        except Exception as exc:
            logger.warning("migrate: bought_at backfill skipped: %s", exc)


def dedupe_preferences(engine: Engine) -> int:
    """Keep one preferences row per user_id; drop extras (legacy duplicates)."""
    cols = _table_columns(engine, "preferences")
    if not cols or "user_id" not in cols:
        return 0
    deleted = 0
    with engine.begin() as conn:
        dup_ids = conn.execute(
            text(
                """
                SELECT user_id FROM preferences
                GROUP BY user_id
                HAVING COUNT(*) > 1
                """
            )
        ).fetchall()
        for (uid,) in dup_ids:
            rows = conn.execute(
                text(
                    """
                    SELECT id, COALESCE(identity_role, '') AS identity_role,
                           COALESCE(identity_label, '') AS identity_label
                    FROM preferences
                    WHERE user_id = :uid
                    ORDER BY id ASC
                    """
                ),
                {"uid": uid},
            ).fetchall()
            if len(rows) < 2:
                continue
            keep_id = rows[0][0]
            keep_role = rows[0][1] or ""
            keep_label = rows[0][2] or ""
            for rid, role, label in rows[1:]:
                if not keep_role.strip() and (role or "").strip():
                    keep_role = role or ""
                    keep_label = label or ""
                    conn.execute(
                        text(
                            """
                            UPDATE preferences
                            SET identity_role = :role, identity_label = :label
                            WHERE id = :kid
                            """
                        ),
                        {"role": keep_role, "label": keep_label, "kid": keep_id},
                    )
                conn.execute(text("DELETE FROM preferences WHERE id = :id"), {"id": rid})
                deleted += 1
    if deleted:
        logger.info("migrate: removed %s duplicate preferences rows", deleted)
    return deleted


def recreate_unique_if_needed(engine: Engine) -> None:
    """
    Best-effort: if legacy watchlist/news_interests have single-column UNIQUE,
    rebuild tables with (user_id, key) unique. Safe when empty or already migrated.
    """
    insp = inspect(engine)
    names = set(insp.get_table_names())

    if "watchlist" in names:
        _maybe_rebuild_watchlist(engine)
    if "news_interests" in names:
        _maybe_rebuild_news_interests(engine)


def _unique_cols(engine: Engine, table: str) -> list[list[str]]:
    insp = inspect(engine)
    out: list[list[str]] = []
    for uq in insp.get_unique_constraints(table):
        out.append(list(uq.get("column_names") or []))
    # SQLite may expose unique indexes instead
    for ix in insp.get_indexes(table):
        if ix.get("unique"):
            out.append(list(ix.get("column_names") or []))
    return out


def _maybe_rebuild_watchlist(engine: Engine) -> None:
    uniques = _unique_cols(engine, "watchlist")
    has_composite = any(set(u) == {"user_id", "symbol"} for u in uniques)
    has_symbol_only = any(u == ["symbol"] for u in uniques)
    if has_composite or not has_symbol_only:
        return
    logger.info("migrate: rebuilding watchlist for composite unique")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS watchlist_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    symbol VARCHAR(16) NOT NULL,
                    name VARCHAR(64) NOT NULL DEFAULT '',
                    market VARCHAR(8) NOT NULL DEFAULT 'SH',
                    created_at DATETIME,
                    CONSTRAINT uq_watchlist_user_symbol UNIQUE (user_id, symbol)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO watchlist_new (id, user_id, symbol, name, market, created_at)
                SELECT id, COALESCE(user_id, 0), symbol, name, market, created_at FROM watchlist
                """
            )
        )
        conn.execute(text("DROP TABLE watchlist"))
        conn.execute(text("ALTER TABLE watchlist_new RENAME TO watchlist"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_watchlist_user_id ON watchlist (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_watchlist_symbol ON watchlist (symbol)"))


def _maybe_rebuild_news_interests(engine: Engine) -> None:
    uniques = _unique_cols(engine, "news_interests")
    has_composite = any(set(u) == {"user_id", "keyword"} for u in uniques)
    has_kw_only = any(u == ["keyword"] for u in uniques)
    if has_composite or not has_kw_only:
        return
    logger.info("migrate: rebuilding news_interests for composite unique")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS news_interests_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    keyword VARCHAR(32) NOT NULL,
                    created_at DATETIME,
                    CONSTRAINT uq_interest_user_keyword UNIQUE (user_id, keyword)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO news_interests_new (id, user_id, keyword, created_at)
                SELECT id, COALESCE(user_id, 0), keyword, created_at FROM news_interests
                """
            )
        )
        conn.execute(text("DROP TABLE news_interests"))
        conn.execute(text("ALTER TABLE news_interests_new RENAME TO news_interests"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_news_interests_user_id ON news_interests (user_id)")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_news_interests_keyword ON news_interests (keyword)")
        )


def ensure_agent_conversation_schema(engine: Engine) -> None:
    """Add conversation_id on messages; backfill one open thread per user with orphans."""
    msg_cols = _table_columns(engine, "agent_messages")
    if not msg_cols:
        return

    with engine.begin() as conn:
        if "conversation_id" not in msg_cols:
            logger.info("migrate: adding conversation_id to agent_messages")
            conn.execute(text("ALTER TABLE agent_messages ADD COLUMN conversation_id INTEGER"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_agent_messages_conversation_id "
                    "ON agent_messages (conversation_id)"
                )
            )

        # Ensure conversations table exists (create_all should have made it)
        tables = inspect(engine).get_table_names()
        if "agent_conversations" not in tables:
            logger.info("migrate: creating agent_conversations")
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS agent_conversations (
                        id INTEGER NOT NULL PRIMARY KEY,
                        user_id INTEGER NOT NULL DEFAULT 0,
                        title VARCHAR(64) DEFAULT '新对话',
                        status VARCHAR(16) DEFAULT 'open',
                        created_at DATETIME,
                        updated_at DATETIME,
                        closed_at DATETIME
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_agent_conversations_user_id "
                    "ON agent_conversations (user_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_agent_conversations_status "
                    "ON agent_conversations (status)"
                )
            )

        # Users with messages missing conversation_id → one open conversation
        orphan_users = conn.execute(
            text(
                """
                SELECT DISTINCT user_id FROM agent_messages
                WHERE conversation_id IS NULL AND user_id IS NOT NULL AND user_id != 0
                """
            )
        ).fetchall()
        for (uid,) in orphan_users:
            if uid is None:
                continue
            existing = conn.execute(
                text(
                    "SELECT id FROM agent_conversations "
                    "WHERE user_id = :uid AND status = 'open' ORDER BY id DESC LIMIT 1"
                ),
                {"uid": uid},
            ).fetchone()
            if existing:
                cid = existing[0]
            else:
                # Title from first user message
                first = conn.execute(
                    text(
                        """
                        SELECT content FROM agent_messages
                        WHERE user_id = :uid AND role = 'user'
                        ORDER BY id ASC LIMIT 1
                        """
                    ),
                    {"uid": uid},
                ).fetchone()
                title = "对话记录"
                if first and first[0]:
                    raw = str(first[0]).strip().replace("\n", " ")
                    title = (raw[:24] + "…") if len(raw) > 24 else (raw or title)
                conn.execute(
                    text(
                        """
                        INSERT INTO agent_conversations
                        (user_id, title, status, created_at, updated_at)
                        VALUES (:uid, :title, 'open', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """
                    ),
                    {"uid": uid, "title": title},
                )
                row = conn.execute(
                    text(
                        "SELECT id FROM agent_conversations "
                        "WHERE user_id = :uid ORDER BY id DESC LIMIT 1"
                    ),
                    {"uid": uid},
                ).fetchone()
                cid = row[0] if row else None
            if cid is None:
                continue
            conn.execute(
                text(
                    """
                    UPDATE agent_messages SET conversation_id = :cid
                    WHERE user_id = :uid AND conversation_id IS NULL
                    """
                ),
                {"cid": cid, "uid": uid},
            )
            logger.info("migrate: backfilled conversation %s for user %s", cid, uid)
