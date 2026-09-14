"""Shared test fixtures. FIXED — see docs/AGENT_CONTRACTS.md §2.

Usage in your test files:
    from tests.conftest import client, admin_headers, ops_headers, warehouse_headers, finance_headers
"""
from __future__ import annotations

import os
import tempfile

# Must be set BEFORE any app import so Settings picks it up.
_TEST_DIR = tempfile.mkdtemp(prefix="erp_test_")
os.environ["ERP_DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/test.db"
os.environ["ERP_AI_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal, init_db  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.models import User  # noqa: E402
import seed as seed_module  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from app.main import app as fastapi_app

    init_db()
    with SessionLocal() as db:
        seed_module.seed(db)
    return fastapi_app


@pytest.fixture(scope="session")
def client(app):
    return TestClient(app)


def _auth_headers(email: str) -> dict:
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email).one()
        return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.fixture(scope="session")
def admin_headers():
    return _auth_headers("admin@erp.local")


@pytest.fixture(scope="session")
def ops_headers():
    return _auth_headers("ops@erp.local")


@pytest.fixture(scope="session")
def warehouse_headers():
    return _auth_headers("warehouse@erp.local")


@pytest.fixture(scope="session")
def finance_headers():
    return _auth_headers("finance@erp.local")


@pytest.fixture
def pin_cutoff(monkeypatch):
    """Pin the auto-confirm cutoff decision so tests never depend on the clock.

    Auto-confirm only fires while the local wall-clock time is at or before
    the configured cutoff (default 18:00). Without this, every test that
    asserts `status == "draft"` silently fails in the morning and every test
    that asserts `status == "confirmed"` fails at night.

    Usage:
        def test_x(client, admin_headers, pin_cutoff):
            pin_cutoff(False)   # past cutoff  -> order stays "draft"
            pin_cutoff(True)    # before cutoff -> auto-confirm may fire
    """
    import app.services.orders.auto_confirm as auto_confirm

    def _pin(before_cutoff: bool) -> None:
        monkeypatch.setattr(
            auto_confirm, "_before_cutoff", lambda cutoff: before_cutoff
        )

    return _pin


@pytest.fixture(autouse=True)
def _legacy_auto_approve(monkeypatch):
    """Keep pre-existing intake tests on the auto-approve path.

    Production now requires a human to confirm EVERY extraction, whatever the
    extractor's confidence (`settings.intake_require_human_review`, default
    True). That policy is orthogonal to what most of this suite asserts —
    extraction, SKU matching, confidence scoring, math checks — so tests that
    are not about the review gate opt out here instead of each growing a
    confirm step.

    Tests that DO exercise the gate request `require_review` (below), which
    re-enables the production rule on top of this.
    """
    monkeypatch.setattr(settings, "intake_require_human_review", False)


@pytest.fixture
def require_review(monkeypatch):
    """Opt a test into the production rule: every extraction stops for review.

    Usage:
        def test_every_order_waits(client, admin_headers, require_review):
            ...  # job ends in "needs_review", no Order exists yet
    """
    monkeypatch.setattr(settings, "intake_require_human_review", True)


@pytest.fixture(autouse=True)
def _legacy_auto_confirm(monkeypatch):
    """Keep pre-existing order tests on the auto-confirm path.

    Production now refuses to settle an order without a person confirming it
    (`settings.orders_require_human_confirmation`, default True). Most of this
    suite is about lines, pricing, lineage and consolidation — it needs
    confirmed orders to exist, not an extra manual-confirm step in every test.

    Tests that DO exercise the rule request `require_human_confirmation`.
    """
    monkeypatch.setattr(settings, "orders_require_human_confirmation", False)


@pytest.fixture
def require_human_confirmation(monkeypatch):
    """Opt into the production rule: no order settles without a person.

    Usage:
        def test_no_silent_confirm(client, admin_headers, require_human_confirmation):
            ...  # order stays "draft" until POST /orders/{id}/confirm
    """
    monkeypatch.setattr(settings, "orders_require_human_confirmation", True)
