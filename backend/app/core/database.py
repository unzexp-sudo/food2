"""Database engine, session, and declarative base. SQLite-compatible."""
from __future__ import annotations

import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger("erp.core.database")


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


def _redact(url: str) -> str:
    """Mask any password before a URL reaches a log line or an error message."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, host = rest.partition("@")
    return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"


def assert_valid_database_url(url: str, *, env_var: str) -> None:
    """Refuse to start on a URL SQLAlchemy cannot parse.

    This exists because of a real outage. The durability check below only ever
    asks "is this SQLite?", so an **empty** value — or a Railway reference such
    as the one shown below that never resolved because no service of that name
    existed — sailed straight through and blew up later inside `create_engine()`
    with "Could not parse SQLAlchemy URL from given URL string". That message
    names SQLAlchemy, not the variable at fault, and it took a day and a half
    of downtime to trace back to a variable nobody had saved.

    Both failure modes have the same two causes, so the message names them.
    """
    if not (url and url.strip()):
        detail = "  Got: <empty string>"
    else:
        try:
            parsed = make_url(url)
        except Exception:  # noqa: BLE001 - this function exists to explain it
            detail = f"  Got: {_redact(url)[:120]!r}  (not a URL SQLAlchemy can parse)"
        else:
            if parsed.get_backend_name() != "sqlite" and not parsed.host:
                # e.g. "postgresql://" — parses cleanly, but there is nothing to
                # dial. SQLite is exempt: it has no host by design.
                detail = f"  Got: {_redact(url)[:120]!r}  (no host to connect to)"
            else:
                return

    raise RuntimeError(
        "\n"
        f"REFUSING TO START: {env_var} is not a usable database URL.\n"
        "\n"
        f"{detail}\n"
        "\n"
        "SQLAlchemy cannot parse it, so the app would otherwise boot far enough\n"
        "to pass a healthcheck and then crash with an error that never mentions\n"
        "this variable. The two usual causes:\n"
        "\n"
        "  1. The value is EMPTY. In Railway an inline variable edit is only saved\n"
        "     when you click the checkmark; navigating away discards it and leaves\n"
        "     the previous (empty) value in place.\n"
        "\n"
        "  2. It is an UNRESOLVED reference. ${{Service.VAR}} resolves only when a\n"
        "     service with that EXACT name exists in the same project AND the same\n"
        "     environment. Otherwise Railway passes the literal text straight\n"
        "     through, and it arrives here looking like a URL.\n"
        "\n"
        "Fix: open the Postgres service, copy its DATABASE_URL and paste it here —\n"
        "or use the variable-reference picker so the name cannot be mistyped.\n"
    )


def assert_durable_database(
    url: str, *, allow_ephemeral: bool, env_var: str = "ERP_DATABASE_URL"
) -> None:
    """Refuse to start on a database a redeploy will destroy — or cannot parse.

    A hosted container gets an ephemeral filesystem. The default SQLite file
    lives *inside* that filesystem, so every deploy silently deletes the whole
    database — orders, customers, and the conversation bindings that decide
    which customer an order belongs to.

    Failing to boot is a bad afternoon. Silently losing the order book is a bad
    year, and it is the kind of loss nobody notices until the goods have already
    gone somewhere. So this is deliberately a hard stop: point
    `ERP_DATABASE_URL` at the Postgres service and it goes away.

    A URL that cannot be parsed is checked first, because that failure is
    silent in a different way — see `assert_valid_database_url`.

    Local runs and the test suite are unaffected — nothing sets these markers.
    """
    assert_valid_database_url(url, env_var=env_var)

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


# `ALTER TABLE ... ADD COLUMN` takes a literal SQL type, and the dialects spell
# some of them differently. Postgres has no `DATETIME` — it answers "type
# datetime does not exist" — so the SQLite name the callers pass below has to be
# translated before the statement reaches the server.
_PG_TYPE_ALIASES = {
    "DATETIME": "TIMESTAMP",
    "BLOB": "BYTEA",
    "DOUBLE": "DOUBLE PRECISION",
}


def _ddl_type_for(dialect_name: str, ddl_type: str) -> str:
    """Translate a SQLite type name into the given dialect's equivalent."""
    if not dialect_name.startswith("postgres"):
        return ddl_type
    base, paren, size = ddl_type.partition("(")
    mapped = _PG_TYPE_ALIASES.get(base.strip().upper(), base.strip())
    return f"{mapped}({size}" if paren else mapped


def _add_column_if_missing(table: str, column: str, ddl_type: str) -> None:
    """Additive column guard.

    `create_all` only creates *missing tables* — it never ALTERs an existing
    one. Without this, a database created before a column was introduced
    would start failing with "no such column" on first insert. This keeps
    additive schema changes safe without pulling in a migration tool.

    A failure here must not stop the service booting, so it is logged rather
    than raised — but it is never swallowed silently. A hidden exception looks
    exactly like a successful migration until the first query touches the
    missing column, which is a much worse place to find out.
    """
    from sqlalchemy import inspect, text

    try:
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return
        if any(c["name"] == column for c in insp.get_columns(table)):
            return
        resolved = _ddl_type_for(engine.dialect.name, ddl_type)
        with engine.begin() as conn:
            conn.execute(
                text(f'ALTER TABLE {table} ADD COLUMN {column} {resolved}')
            )
    except Exception:  # pragma: no cover - best effort, never block startup
        logger.warning(
            "init_db: could NOT add column %s.%s (%s) — add it manually; any "
            "query touching it will fail",
            table,
            column,
            ddl_type,
            exc_info=True,
        )


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
    # The intake original itself, not just a path to it. A path into the
    # container is lost on the next deploy; see `models/intake.py`.
    _add_column_if_missing("intake_documents", "file_data", "BLOB")
