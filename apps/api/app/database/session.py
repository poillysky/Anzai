from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401
    from app.database.migrate import (
        dedupe_preferences,
        ensure_agent_conversation_memory_columns,
        ensure_agent_conversation_schema,
        ensure_holdings_bought_at,
        ensure_holdings_day_buy_lot,
        ensure_preference_identity_columns,
        ensure_user_id_columns,
        recreate_unique_if_needed,
    )

    Base.metadata.create_all(bind=engine)
    ensure_user_id_columns(engine)
    ensure_preference_identity_columns(engine)
    ensure_holdings_bought_at(engine)
    ensure_holdings_day_buy_lot(engine)
    ensure_agent_conversation_schema(engine)
    ensure_agent_conversation_memory_columns(engine)
    dedupe_preferences(engine)
    recreate_unique_if_needed(engine)
