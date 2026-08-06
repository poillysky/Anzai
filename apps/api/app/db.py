"""DEPRECATED shim — do not add logic. Use app.database."""

from app.database.session import Base, SessionLocal, engine, get_db, init_db

__all__ = ["Base", "SessionLocal", "engine", "get_db", "init_db"]
