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


# --- Unparseable URLs ---------------------------------------------------------
#
# The durability check above only ever asked "is this SQLite?", so a *broken*
# URL sailed past it and died later inside `create_engine()` with "Could not
# parse SQLAlchemy URL from given URL string" — a message that names SQLAlchemy
# and never mentions the variable at fault. On 2026-09-15 that cost a day and a
# half of downtime: both Railway services crash-looped while their healthchecks
# looked fine and the logs pointed at the wrong thing.
#
# These tests pin the two shapes that actually occurred.

UNRESOLVED_REFERENCE = "${{Postgres.DATABASE_URL}}"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", UNRESOLVED_REFERENCE, "postgresql://"],
)
def test_unparseable_url_refuses_to_start(bad, not_deployed):
    """Not deployed, not SQLite — and it must STILL refuse.

    The old guard returned early for anything that was not SQLite, so none of
    these were caught.
    """
    with pytest.raises(RuntimeError):
        assert_durable_database(bad, allow_ephemeral=False)


def test_empty_url_is_the_regression_we_actually_hit(not_deployed):
    """Railway showed the variable as `<empty string>`; that is what crashed."""
    with pytest.raises(RuntimeError) as exc:
        assert_durable_database("", allow_ephemeral=False)

    message = str(exc.value)
    assert "ERP_DATABASE_URL" in message
    # The two real causes must both be named, because both look identical from
    # inside the app and neither is guessable from a stack trace.
    assert "EMPTY" in message
    assert "UNRESOLVED" in message
    assert "checkmark" in message


def test_unresolved_reference_is_recognised(not_deployed):
    """A `${{...}}` that never resolved is passed through as literal text."""
    with pytest.raises(RuntimeError) as exc:
        assert_durable_database(UNRESOLVED_REFERENCE, allow_ephemeral=False)
    assert UNRESOLVED_REFERENCE in str(exc.value)


def test_whitespace_only_url_is_treated_as_empty(not_deployed):
    with pytest.raises(RuntimeError) as exc:
        assert_durable_database("   ", allow_ephemeral=False)
    assert "<empty string>" in str(exc.value)


def test_error_message_never_leaks_the_password(not_deployed):
    """A broken URL still must not put a credential into the deploy logs."""
    with pytest.raises(RuntimeError) as exc:
        assert_durable_database(
            "postgresql://admin:hunter2@host:notaport/db", allow_ephemeral=False
        )
    message = str(exc.value)
    assert "hunter2" not in message
    assert "***" in message


@pytest.mark.parametrize(
    "good",
    [SQLITE, POSTGRES, "postgresql://postgres:pw@postgres.railway.internal:5432/railway"],
)
def test_parseable_urls_are_still_accepted(good, not_deployed):
    """The new check must not start rejecting working configurations."""
    assert_durable_database(good, allow_ephemeral=False)  # does not raise
