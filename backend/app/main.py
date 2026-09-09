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
from app.core.database import SessionLocal, init_db

_log = logging.getLogger("erp.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Boot the schema + demo seed, but NEVER let a startup error take the
    # whole container down. If the DB is briefly unreachable (e.g. a Railway
    # Postgres plugin still spinning up, or a wrong ERP_DATABASE_URL), the
    # process must still bind to PORT so /api/health answers 200 — otherwise
    # Railway shows "Application failed to respond" and you can't even see
    # the real error in the deploy logs. The exception is logged at ERROR;
    # endpoints that need the DB will return 500 until the next restart /
    # once the DB is reachable.
    _log.info("startup: init_db (database_url=%s)", _safe_db_url(settings.database_url))
    try:
        init_db()
    except Exception:  # noqa: BLE001
        _log.exception("startup: init_db FAILED — app will start but DB-backed endpoints will 500")
    else:
        try:
            with SessionLocal() as db:
                run_seed(db)
        except Exception:  # noqa: BLE001
            _log.exception("startup: seed FAILED — continuing without demo data")
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
    intake, orders, procurement, quotations, warehouse, delivery, finance,
    wecom_intake,
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
    return {"status": "ok", "app": settings.app_name}


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
