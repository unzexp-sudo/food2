"""Startup seeding: who gets demo data, and who must not.

`seed.py` creates five users whose password is the constant "erp123" — one of
them an admin — plus a demo catalog, customers and contracts. On ephemeral
SQLite that was harmless: every redeploy wiped it. On the durable Postgres
introduced on 2026-09-14 it persists, which turns a convenience into a
publicly-known admin password on a publicly reachable ERP.

The seed is therefore environment-derived by default: seed on a laptop, never
inside a container. These tests pin that, and pin the bootstrap-admin escape
hatch that stops "no seed" meaning "nobody can log in".
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401  (registers every table on Base)
from app.core.config import settings
from app.core.database import Base, _ddl_type_for
from app.main import _bootstrap_admin, _should_seed_demo_data
from app.models import User

_DEPLOY_MARKERS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_PROJECT_ID",
    "DYNO",
    "ENVIRONMENT",
    "APP_ENV",
    "ERP_ENVIRONMENT",
)


@pytest.fixture
def not_deployed(monkeypatch):
    for name in _DEPLOY_MARKERS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def deployed(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")


@pytest.fixture
def empty_db():
    """A fresh, fully-migrated database with no rows in it."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        yield db


@pytest.fixture
def unset_seed_flag(monkeypatch):
    monkeypatch.setattr(settings, "seed_demo_data", None)


# --- Who gets the demo data ---------------------------------------------------


def test_seeds_on_a_laptop(not_deployed, unset_seed_flag):
    """Local development must keep working exactly as before."""
    assert _should_seed_demo_data() is True


def test_does_not_seed_in_a_container(deployed, unset_seed_flag):
    """The whole point: a production database never gets demo users."""
    assert _should_seed_demo_data() is False


def test_explicit_true_overrides_the_container_default(deployed, monkeypatch):
    """A throwaway environment may still want the demo dataset."""
    monkeypatch.setattr(settings, "seed_demo_data", True)
    assert _should_seed_demo_data() is True


def test_explicit_false_overrides_a_laptop(not_deployed, monkeypatch):
    monkeypatch.setattr(settings, "seed_demo_data", False)
    assert _should_seed_demo_data() is False


# --- Bootstrap admin: the way in without demo data ----------------------------


def test_bootstrap_admin_creates_the_first_user(empty_db, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_email", "owner@example.com")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "s3cret-not-erp123")

    _bootstrap_admin(empty_db)

    user = empty_db.query(User).one()
    assert user.email == "owner@example.com"
    assert user.role == "admin"
    assert user.is_active is True
    # The password must be the configured one, and must be hashed — never the
    # demo constant, never stored in the clear.
    assert user.password_hash != "s3cret-not-erp123"
    assert "erp123" not in user.password_hash


def test_bootstrap_admin_does_not_touch_a_populated_database(empty_db, monkeypatch):
    """Never overwrite real users, and never run twice."""
    empty_db.add(
        User(email="existing@example.com", name="Existing", role="admin",
             password_hash="x")
    )
    empty_db.commit()

    monkeypatch.setattr(settings, "bootstrap_admin_email", "owner@example.com")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "pw")

    _bootstrap_admin(empty_db)

    assert empty_db.query(User).count() == 1
    assert empty_db.query(User).one().email == "existing@example.com"


def test_bootstrap_admin_is_a_noop_when_unconfigured(empty_db, monkeypatch):
    """Both values are required — a half-set config must not invent a user."""
    monkeypatch.setattr(settings, "bootstrap_admin_email", "")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "")

    _bootstrap_admin(empty_db)

    assert empty_db.query(User).count() == 0


def test_bootstrap_admin_is_a_noop_with_only_a_password(empty_db, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_admin_email", "")
    monkeypatch.setattr(settings, "bootstrap_admin_password", "pw")

    _bootstrap_admin(empty_db)

    assert empty_db.query(User).count() == 0


# --- Dialect-correct ALTER TABLE ---------------------------------------------


def test_datetime_becomes_timestamp_on_postgres():
    """Postgres has no DATETIME type — the raw SQLite name would be a syntax
    error, and the failure used to be swallowed silently."""
    assert _ddl_type_for("postgresql", "DATETIME") == "TIMESTAMP"


def test_sqlite_keeps_its_own_type():
    assert _ddl_type_for("sqlite", "DATETIME") == "DATETIME"


@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
def test_parameterised_types_keep_their_size(dialect):
    assert _ddl_type_for(dialect, "VARCHAR(36)") == "VARCHAR(36)"
