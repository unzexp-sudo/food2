"""Database engine, session, and declarative base. SQLite-compatible."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


# Environment variables that mean "this is a deployed container, not a laptop".
_PRODUCTION_MARKERS = ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "DYNO")


def is_deployed() -> bool:
    """True when we look like we are running in a hosted container."""
    if any(os.environ.get(name) for name in _PRODUCTION_MARKERS):
        return True
    for name in ("ENVIRONMENT", "APP_ENV", "ERP_ENVIRONMENT"):
        if (os.environ.get(name) or "").strip().lower() in ("production", "prod"):
            return True
    return False


def assert_durable_database(url: str, *, allow_ephemeral: bool) -> None:
    """Refuse to start on a database that a redeploy will destroy.

    A hosted container gets an ephemeral filesystem. The default SQLite file
    lives *inside* that filesystem, so every deploy silently deletes the whole
    database — orders, customers, and the conversation bindings that decide
    which customer an order belongs to.

    Failing to boot is a bad afternoon. Silently losing the order book is a bad
    year, and it is the kind of loss nobody notices until the goods have already
    gone somewhere. So this is deliberately a hard stop: point
    `ERP_DATABASE_URL` at the Postgres service and it goes away.

    Local runs and the test suite are unaffected — nothing sets these markers.
    """
    if allow_ephemeral or not url.startswith("sqlite"):
        return
    if not is_deployed():
        return

    raise RuntimeError(
        "\n"
        "REFUSING TO START: the database is SQLite inside an ephemeral "
        "container filesystem.\n"
        "\n"
        "Every redeploy of this service destroys the filesystem, and with it "
        "the entire database — every order, every customer, and every "
        "WeCom conversation binding.\n"
        "\n"
        "Fix: add the Postgres service to this Railway project and set\n"
        "    ERP_DATABASE_URL=${{Postgres.DATABASE_URL}}\n"
        "in the service variables, then redeploy.\n"
        "\n"
        "If the data really is disposable (a demo or a smoke test), set\n"
        "    ERP_ALLOW_EPHEMERAL_DATABASE=true\n"
        "to accept the loss explicitly.\n"
    )


def _make_engine(url: str):
    kwargs: dict = {}
    if url.startswith("sqlite"):
        # FastAPI may touch the DB from different threads.
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, pool_pre_ping=True, **kwargs)


assert_durable_database(
    settings.database_url, allow_ephemeral=settings.allow_ephemeral_database
)

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
    # Delivery confirmation (docs/IDENTITY_IMPLEMENTATION_SPEC.md §1.2).
    _add_column_if_missing("customers", "address_confirmed_at", "DATETIME")
    _add_column_if_missing("customers", "address_confirmed_by", "VARCHAR(36)")
    _add_column_if_missing("customers", "address_source", "VARCHAR(30)")
    _add_column_if_missing("orders", "delivery_address", "TEXT")
    _add_column_if_missing("orders", "delivery_contact_name", "VARCHAR(100)")
    _add_column_if_missing("orders", "delivery_contact_phone", "VARCHAR(50)")
    _add_column_if_missing("orders", "delivery_confirmed_at", "DATETIME")
    _add_column_if_missing("orders", "delivery_confirmed_by", "VARCHAR(36)")
