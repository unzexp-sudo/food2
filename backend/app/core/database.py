"""Database engine, session, and declarative base. SQLite-compatible."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str):
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # FastAPI may touch the DB from different threads.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, pool_pre_ping=True, **kwargs)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency yielding a DB session."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> None:
    """Additive column guard.

    `create_all` only creates *missing tables* — it never ALTERs an existing
    one. Without this, a database created before a column was introduced
    would start failing with "no such column" on first insert. This keeps
    additive schema changes safe without pulling in a migration tool.
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return
        if any(c["name"] == column for c in insp.get_columns(table)):
            return
        with engine.begin() as conn:
            conn.execute(
                text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl_type}')
            )
    except Exception:  # pragma: no cover - best effort, never block startup
        pass


def init_db() -> None:
    """Create all tables (dev/demo path; use migrations in production)."""
    from app import models  # noqa: F401  (register all models)

    Base.metadata.create_all(bind=engine)
    # Additive columns introduced after the initial schema.
    _add_column_if_missing("inventory_movements", "created_at", "DATETIME")
    _add_column_if_missing("inventory_movements", "updated_at", "DATETIME")
