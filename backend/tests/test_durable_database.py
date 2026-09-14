"""The durability guard (docs/CUSTOMER_MATCHING_PLAN.md §4).

A hosted container gets an ephemeral filesystem. The default SQLite database
lives inside it, so every redeploy deletes the entire order book — and, since
2026-09-14, every WeCom conversation binding that decides which customer an
order belongs to.

The guard converts that silent, delayed disaster into a refusal to start. These
tests exist because a guard nobody tests is a guard that quietly stops working.
"""
from __future__ import annotations

import pytest

from app.core.database import assert_durable_database, is_deployed

SQLITE = "sqlite:///./erp.db"
POSTGRES = "postgresql+psycopg2://user:pw@host:5432/erp"

_ALL_MARKERS = (
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_PROJECT_ID",
    "DYNO",
    "ENVIRONMENT",
    "APP_ENV",
    "ERP_ENVIRONMENT",
)


@pytest.fixture
def not_deployed(monkeypatch):
    for name in _ALL_MARKERS:
        monkeypatch.delenv(name, raising=False)


def test_sqlite_in_a_container_refuses_to_start(monkeypatch):
    """The whole point: fail loudly now rather than lose everything later."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")

    with pytest.raises(RuntimeError) as exc:
        assert_durable_database(SQLITE, allow_ephemeral=False)

    message = str(exc.value)
    # The message has to be actionable — a refusal without a fix is just an
    # outage with extra steps.
    assert "ERP_DATABASE_URL" in message
    assert "ERP_ALLOW_EPHEMERAL_DATABASE" in message
    assert "redeploy" in message


def test_postgres_in_a_container_starts(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert_durable_database(POSTGRES, allow_ephemeral=False)  # does not raise


def test_local_sqlite_is_unaffected(not_deployed):
    """Development and the test suite run on SQLite and must keep working."""
    assert is_deployed() is False
    assert_durable_database(SQLITE, allow_ephemeral=False)  # does not raise


def test_app_env_production_counts_as_deployed(monkeypatch):
    """Railway is not the only host; a generic production marker works too."""
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert is_deployed() is True
    with pytest.raises(RuntimeError):
        assert_durable_database(SQLITE, allow_ephemeral=False)


def test_disposable_data_can_be_accepted_explicitly(monkeypatch):
    """A demo or smoke test may genuinely not care. It just has to say so."""
    monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
    assert_durable_database(SQLITE, allow_ephemeral=True)  # does not raise
