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
