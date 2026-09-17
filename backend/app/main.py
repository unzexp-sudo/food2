"""FoodSupply ERP — FastAPI application entrypoint.

All routers are pre-registered. Module agents replace stub router bodies only.
Run:  cd backend && python -m uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from seed import seed as run_seed
from app.api.v1 import (
    auth,
    catalog,
    contracts,
    customers,
    delivery,
    finance,
    identity,
    intake,
    orders,
    procurement,
    quotations,
    system,
    warehouse,
    wholesalers,
    wecom_intake,
)
from app.core.config import settings
from app.core.database import SessionLocal, init_db, is_deployed
from app.core.security import hash_password
from app.models import User

_log = logging.getLogger("erp.main")


def _should_seed_demo_data() -> bool:
    """Whether to create the demo dataset on boot.

    Unset (the default) is environment-derived: seed on a laptop, never inside a
    container. `seed.py` creates users whose password is a well-known constant,
    so on a durable production database the seed stops being something the next
    deploy wipes and becomes a permanent, publicly reachable admin account.
    """
    if settings.seed_demo_data is not None:
        return settings.seed_demo_data
    return not is_deployed()


def _bootstrap_admin(db) -> None:
    """Create the first admin on an empty database, without any demo data.

    This is the way into a fresh production database once the demo seed is off:
    with no users at all nobody can log in, and the ERP has no other bootstrap
    path. Deliberately a no-op unless BOTH settings are supplied.
    """
    email = (settings.bootstrap_admin_email or "").strip()
    password = settings.bootstrap_admin_password or ""
    if not email or not password:
        return
    if db.query(User).count() > 0:
        return

    db.add(
        User(
            email=email,
            name="Admin",
            role="admin",
            password_hash=hash_password(password),
        )
    )
    db.commit()
    _log.warning(
        "startup: created bootstrap admin %s — change this password now", email
    )


def _seed_or_bootstrap() -> None:
    """Populate a brand-new database, or deliberately leave it alone."""
    try:
        with SessionLocal() as db:
            if _should_seed_demo_data():
                run_seed(db)
                return
            _bootstrap_admin(db)
    except Exception:  # noqa: BLE001
        _log.exception("startup: seeding FAILED — continuing without seed data")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Boot the schema, but NEVER let a startup error take the whole container
    # down. If the DB is briefly unreachable (e.g. a Railway Postgres plugin
    # still spinning up, or a wrong ERP_DATABASE_URL), the process must still
    # bind to PORT so /api/health answers 200 — otherwise Railway shows
    # "Application failed to respond" and you can't even see the real error in
    # the deploy logs. The exception is logged at ERROR; endpoints that need the
    # DB will return 500 until the next restart / once the DB is reachable.
    #
    # NOTE: that same leniency is why a green /api/health proves nothing about
    # the database. Verify with a DB-backed endpoint, not the healthcheck.
    _log.info("startup: init_db (database_url=%s)", _safe_db_url(settings.database_url))
    try:
        init_db()
    except Exception:  # noqa: BLE001
        _log.exception("startup: init_db FAILED — app will start but DB-backed endpoints will 500")
    else:
        _seed_or_bootstrap()
    yield


def _safe_db_url(url: str) -> str:
    """Mask the password in a database URL for safe logging."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    creds, host = rest.split("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        return f"{scheme}://{user}:***@{host}"
    return url


app = FastAPI(
    title="FoodSupply ERP 食材供应链 ERP",
    version="0.1.0",
    description="B2B food supply ERP with AI order intake (see docs/EXECUTIVE_SUMMARY.md)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (
    auth, system, customers, catalog, wholesalers, contracts,
    identity, intake, orders, procurement, quotations, warehouse, delivery,
    finance, wecom_intake,
):
    app.include_router(module.router)

# Outbound WeCom notification handlers (docs/WECOM_CONTRACTS.md §7).
# Imported LAST so its `order.draft_created` handler runs after the orders
# module's auto-confirm handler and can read the settled order status.
# `from package import submodule` imports the wecom_notify module for its side
# effects (registering @on handlers) WITHOUT rebinding the `app` name.
from app.services.notify import wecom_notify  # noqa: E402,F401


@app.get("/api/health", tags=["system"])
def health():
    # Railway uses this path as the healthcheck, so everything here must stay
    # cheap and free of I/O — these are pure string comparisons.
    #
    # Both secrets default to values published in this repo (it is public), and
    # both are accepted as valid credentials: `service_key` authenticates the
    # /api/v1/intake/* endpoints as a system actor, `wecom_gateway_key` is the
    # ERP's half of the gateway pair. A deployment that never overrode them has
    # no symptom at all, so surface it rather than let it stay invisible.
    return {
        "status": "ok",
        "app": settings.app_name,
        "service_key_is_default": settings.service_key_is_default,
        "wecom_gateway_key_is_default": settings.wecom_gateway_key_is_default,
        # Not a secret, but the same class of invisible config: under the
        # default `mock` provider a photo or a scanned PDF is not read at all —
        # it yields canned demo lines. See `image_extraction_is_simulated`.
        "ai_provider": settings.ai_provider,
        "image_extraction_is_simulated": settings.image_extraction_is_simulated,
    }


# Undefined /api/* paths must return a JSON 404 — NOT the SPA HTML. The
# catch-all route below would otherwise serve index.html (text/html, 200) for
# any /api/... path the backend doesn't define, which makes the frontend's
# `fetch(...).json()` throw a SyntaxError on boot and leaves the page
# completely blank. Registering this BEFORE the catch-all ensures the SPA
# always gets a real JSON 404 it can handle. Defined /api routes (registered
# above via the v1 routers and /api/health) still take precedence and win.
@app.get("/api/{path:path}", include_in_schema=False)
def api_not_found(path: str) -> JSONResponse:  # noqa: ARG001
    return JSONResponse({"detail": "Not Found"}, status_code=404)


# ---- Serve the compiled React SPA (frontend/dist copied to /app/static) ----
# In the production Docker image /app/static is always present (built in
# stage 1 of the root Dockerfile). In local dev without a frontend build
# this block is skipped and only the API is served.
STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if STATIC_DIR.is_dir():
    # Vite emits hashed assets under /assets/...; serve them as static files.
    # Guard BOTH the assets dir and index.html: a missing/partial frontend
    # build must NOT crash the whole app — the API must stay up. (A bare
    # StaticFiles() mount raises at import time if its directory is absent,
    # which would hard-crash uvicorn in the container.)
    import logging

    _log = logging.getLogger("erp.main")
    _assets_dir = STATIC_DIR / "assets"
    _index_html = STATIC_DIR / "index.html"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")
    else:
        _log.warning(
            "SPA assets dir not found at %s — serving API only (no static UI).",
            _assets_dir,
        )
    if _index_html.is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):  # noqa: ARG001
            """SPA fallback: serve index.html for any non-API route."""
            return FileResponse(_index_html)
