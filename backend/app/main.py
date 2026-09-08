"""FoodSupply ERP — FastAPI application entrypoint.

All routers are pre-registered. Module agents replace stub router bodies only.
Run:  cd backend && python -m uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        run_seed(db)
    yield


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
