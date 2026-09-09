# Guanmai MVP — 9-Agent Parallel Build Plan

> Status: **Foundation laid, theme live, typecheck green.** This document is the
> contracts-first spec that lets 9 module-agents run **in parallel without
> colliding on shared files**. Per the user directive: **do NOT push to git until
> the full build is complete and verified.**

---

## 1. Why this plan exists

The app (`FoodSupply ERP`, a Guanmai-style B2B fresh-produce SaaS) already has a
comprehensive, working module set. The user's "Guanmai MVP + 9 agents" directive
is executed as:

1. A **contracts-first foundation** (this doc + the shared files below) written
   by a single orchestrator so module agents own **disjoint files**.
2. **9 module agents**, each owning exactly one module's frontend + backend files.
3. A single final verification (`tsc -b` + `py_compile`) and user approval
   **before any `git push`**.

### Critical lesson (enforced by tooling)
Multiple edits to the **same file in one message are unreliable** — only some
apply. Therefore the foundation pre-registers every shared touch-point
(route, menu entry, status domain, i18n key) so agents never need to edit shared
files. Each agent edits **only its own module files**.

---

## 2. The 9 Guanmai MVP modules

| # | Module | Route | Status this session |
|---|--------|-------|---------------------|
| 1 | Sales Quotations (报价) | `/sales/quotations` | ✅ Built prior session; 4-col card grid + batch modal |
| 2 | Customers (客户) | `/master/customers` | ✅ Implemented (651 lines) |
| 3 | Products / SKU (商品) | `/master/products` | ✅ Implemented (646 lines) |
| 4 | Wholesalers / Suppliers (供应商) | `/master/wholesalers` | ✅ Implemented |
| 5 | Purchase Orders (采购单) | `/purchase-orders` | ✅ Implemented |
| 6 | Sales Orders (销售订单) | `/orders` | ✅ Implemented |
| 7 | Inventory (库存) | `/warehouse/inventory` | ✅ Implemented |
| 8 | Delivery (配送) | `/delivery` | ✅ Implemented (635 lines) |
| 9 | Finance / Settlement (财务结算) | `/finance/invoices` + `/finance/statements` + `/finance/margin` | ✅ Implemented |
| + | Dashboard (工作台) | `/dashboard` | ✅ **Built this session** (was a stub) |

All 9 modules are present and operational. The modern/sleek **theme** is applied
globally via `ConfigProvider` and a **light/dark toggle** sits next to the
**EN / 中文 language toggle** in the header (dual-language preserved at top).

---

## 3. Contracts-first foundation (ORCHESTRATOR-OWNED — agents MUST NOT edit)

These files are the shared seams. They are already written/registered; agents
only consume them.

| File | Role |
|------|------|
| `frontend/src/theme.ts` | `buildTheme(mode)` — modern/sleek tokens (indigo `#5B5BD6`, 10px radii, soft shadows, light/dark). |
| `frontend/src/theme-context.tsx` | `ThemeModeProvider` + `useThemeMode()` — persisted light/dark state. |
| `frontend/src/App.tsx` | Wires `ConfigProvider theme={buildTheme(mode)}` + `ThemeModeProvider`. |
| `frontend/src/layouts/AdminLayout.tsx` | Menu groups + header toggles (theme Switch + language Segmented). **Agents add menu items ONLY via the documented `MENU_GROUPS` pattern here, coordinated by orchestrator.** |
| `frontend/src/router.tsx` | Route table. Routes already registered for all 9 modules. |
| `frontend/src/components/StatusTag.tsx` | `StatusDomain` union + color map. Agents add a domain by PR to this file only. |
| `frontend/src/i18n/en.ts`, `zh.ts` | Translation registry. Agents add keys inside their module's `pages.<module>` namespace (append-only). |
| `backend/app/main.py` | Router `include_router` registry. |
| `backend/app/models/__init__.py` | Model exports. |

### Frontend shared utilities (use, don't reinvent)
- `api` client: `src/api/client.ts` (`api.get/post/patch/put/delete`, `Page<T>`).
- Hooks: `src/api/hooks.ts` (`useList`, `useDetail`, `useMutate`).
- `useLanguage()` from `src/i18n` → `{ t, lang, setLang }`; `pickName(lang, en, zh)` from `src/utils/format`.
- `StatusTag` for every status value (`domain` + `value`).

### Backend shared pattern (every module follows this exactly)
```
backend/app/
  models/<module>.py          # SQLAlchemy 2.0 models
  schemas/<module>.py         # Pydantic v2
  services/<module>/<module>.py  # business logic
  api/v1/<module>.py          # router: GET list (filters+pagination), POST, GET/{id}, PATCH/{id}, DELETE/{id}
```
Register the router in `main.py`; export models in `models/__init__.py`. Use
`next_number(db, Model, "code", "PREFIX")` for human-readable codes and
`log_audit(...)` for mutations. Auth via `require_roles([...])`.

---

## 4. File-ownership contract (per agent)

Each agent owns **only** its module files. It may read shared files but must not
write them. Adding a route/menu/status/i18n entry is done by the orchestrator in
a single coordinated edit, or via a documented append to `en.ts`/`zh.ts` within
the agent's `pages.<module>` namespace.

| Agent | Owns (frontend) | Owns (backend) |
|-------|-----------------|----------------|
| A · Quotations | `pages/sales/*` | `models/quotation.py`, `schemas/quotations.py`, `services/quotations/*`, `api/v1/quotations.py` |
| B · Customers | `pages/master/CustomersPage.tsx` (+ components) | `models/customer.py`, `schemas/customers.py`, `services/customers/*`, `api/v1/customers.py` |
| C · Products | `pages/master/ProductsPage.tsx` (+ components) | `models/product.py`, `schemas/products.py`, `services/products/*`, `api/v1/products.py` |
| D · Wholesalers | `pages/master/WholesalersPage.tsx` | `models/wholesaler.py`, `schemas/wholesalers.py`, `services/wholesalers/*`, `api/v1/wholesalers.py` |
| E · Purchase Orders | `pages/purchase-orders/*` | `models/purchase_order.py`, `schemas/purchase_orders.py`, `services/purchase_orders/*`, `api/v1/purchase_orders.py` |
| F · Sales Orders | `pages/orders/*` | `models/order.py`, `schemas/orders.py`, `services/orders/*`, `api/v1/orders.py` |
| G · Inventory | `pages/warehouse/InventoryPage.tsx` | `models/inventory.py`, `schemas/inventory.py`, `services/inventory/*`, `api/v1/inventory.py` |
| H · Delivery | `pages/delivery/DeliveryPage.tsx` | `models/delivery.py`, `schemas/delivery.py`, `services/delivery/*`, `api/v1/delivery.py` |
| I · Finance | `pages/finance/*` | `models/invoice.py`, `schemas/invoices.py`, `services/invoices/*`, `api/v1/invoices.py` |

---

## 5. Parallel execution protocol

1. **Orchestrator** writes the foundation (done) + this plan. Pre-registers all
   routes, menu entries, status domains, and base i18n namespaces.
2. **Spawn 9 agents in parallel** — one per module — each with:
   - its ownership table row above,
   - the contracts in §3,
   - the explicit rule: *edit only your module files; do not edit shared files;
     do not run `git push`; do not run project-wide builds*.
3. Each agent delivers its module's backend + frontend + dual-language strings,
   following the existing `QuotationsPage` / `orders` modules as the reference.
4. **Orchestrator** runs the single consolidated verification (below) once all
   agents report done.
5. **No `git push`** until: `tsc -b` is green, `py_compile` is green, and the
   user approves. (User directive: *DO NOT PUSH TO GIT until completion.*)

---

## 6. Verification (run once, by orchestrator)

```bash
# Frontend typecheck (use a larger heap — full project graph can OOM)
cd frontend && NODE_OPTIONS=--max-old-space-size=4096 npx tsc -b

# Backend syntax check (FastAPI can't boot in this sandbox — no Docker/Postgres)
cd backend && python -m compileall -q app

# Optional: full Vite build to a fresh outDir (safe-delete guard blocks dist/)
cd frontend && npm run build -- --outDir dist_verify && rm -rf dist_verify
```

---

## 7. What changed this session (foundation + gaps)

- **Theme applied:** `theme.ts` was written earlier but never wired in. Now
  `App.tsx` applies `buildTheme(mode)` via `ConfigProvider`, and
  `theme-context.tsx` persists the mode.
- **Light/Dark toggle:** added a `Switch` (sun/moon) in the `AdminLayout`
  header, next to the EN/中文 `Segmented`. Header text is now theme-aware
  (removed hardcoded white that broke light mode).
- **Dashboard built:** replaced the 5-line `PagePlaceholder` stub with a
  Guanmai-style overview (KPI statistic cards, recent-orders table, quick
  navigation), fully dual-language.
- **i18n:** added `theme.*` and `common.{code,createdAt,recentOrders,quickActions}`.
- **Typecheck:** `tsc -b` clean.

---

## 8. Open items / next steps

- Confirm whether to rename the product from "FoodSupply ERP" to "Guanmai"
  branding (not requested; left as-is to preserve functionality).
- Live end-to-end test requires the user's Docker/`docker compose` stack
  (sandbox has no Docker/Postgres/FastAPI runtime).
- Push path correction: the macOS keychain holds a GitHub token for account
  **`htjani`**, **not** `unzexp-sudo`. `git push` to `unzexp-sudo/*` 403s with the
  htjani token. For `unzexp-sudo/food2` and `unzexp-sudo/WeCom1` you must either
  push with the `unzexp-sudo` account's PAT or add that PAT to the keychain first.
  See §9.9 for the ordered checklist.

---

## 9. Deploy Guide — Namecheap domain + Railway (food2 ERP **and** WeCom1 gateway)

> Both apps run as **separate Railway projects** and get **separate subdomains**
> on one Namecheap domain (e.g. `erp.yourdomain.com` and `wecom.yourdomain.com`).
> They talk to each other over HTTPS using two shared secrets.

### 9.1 What was already fixed for deploy (read this first)
Both apps were failing Railway's healthcheck with the generic
*"Application failed to respond"* page. Root cause was **not** missing env vars —
it was a missing **Postgres driver** in the production image while the DB URL was
a `postgresql+psycopg2://` string. `sqlalchemy.create_engine(...)` resolves that
dialect **at import time**, so without the driver the app crashed before uvicorn
bound to `PORT`.

- **food2 ERP** — fixed + pushed in `8965ce9`: `psycopg2-binary>=2.9` added to
  `backend/requirements.txt`; `lifespan` in `backend/app/main.py` wrapped in
  `try/except` so a transient DB error can't take the container down.
- **WeCom1 gateway** — fixed + committed **locally** as `1b26900` (same two
  changes). ⚠️ Not yet pushed: `git push` to `unzexp-sudo/WeCom1` 403s because the
  only cached GitHub token is for account `htjani`, which lacks push access. Push
  with the `unzexp-sudo` account (or add its PAT to the keychain) before Railway
  can rebuild it.

### 9.2 Prerequisites
- A Namecheap domain you control (e.g. `yourdomain.com`).
- A Railway account; GitHub repos `unzexp-sudo/food2` (the `Foshan1` tree) and
  `unzexp-sudo/WeCom1` connected to Railway.
- `openssl` available locally (for `ERP_SECRET_KEY`).
- The two shared secrets agreed between the apps (see §9.5).

### 9.3 Railway project setup

**food2 ERP** (Dockerfile builder):
1. New Project → Deploy from GitHub repo `unzexp-sudo/food2` (root, not a subdir).
2. Railway reads `railway.toml` → builder `DOCKERFILE`. The `Dockerfile` builds the
   React SPA in stage 1 and runs FastAPI (API **+** SPA) in stage 2 on
   `${PORT:-8000}`. No extra build settings needed.
3. Add the **PostgreSQL** plugin (creates a `PostgreSQL` service in the project).
   Railway auto-injects `DATABASE_URL` into the web service — but food2 reads
   `ERP_DATABASE_URL`, so either (a) set `ERP_DATABASE_URL` to the plugin's
   connection string, or (b) leave it unset and food2 falls back to SQLite
   (ephemeral on Railway's filesystem — fine for a demo, loses data on restart).
4. Healthcheck is already configured: `healthcheckPath = "/api/health"`,
   `healthcheckTimeout = 30`, `restartPolicyType = "ON_FAILURE"`.
5. Deploy. Railway gives a default `https://food2-<env>.up.railway.app` domain.

**WeCom1 gateway** (Nixpacks builder):
1. New Project → Deploy from GitHub repo `unzexp-sudo/WeCom1`.
2. Railway reads `railway.toml` → builder `NIXPACKS`, `startCommand =
   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8100}`.
   `requirements.txt` now contains `psycopg2-binary>=2.9` so the
   `postgresql+psycopg2` dialect resolves.
3. Add the **PostgreSQL** plugin; set `WECOM_DATABASE_URL` to its connection
   string (or leave unset → SQLite fallback).
4. Healthcheck: `healthcheckPath = "/wecom/health"`, `healthcheckTimeout = 30`,
   `restartPolicyType = "ON_FAILURE"`.
5. Deploy after the `1b26900` push (see §9.1 warning).

### 9.4 Environment variables

**food2 ERP** (set in Railway → Service → Variables):
| Variable | Example / note |
|---|---|
| `ERP_DATABASE_URL` | Railway Postgres plugin connection string, **or** unset → SQLite fallback |
| `ERP_SECRET_KEY` | `openssl rand -hex 32` (generate once, keep it) |
| `ERP_AI_PROVIDER` | `mock` (default, no external calls) or `openai` |
| `ERP_OPENAI_API_KEY` | only if `ERP_AI_PROVIDER=openai` |
| `ERP_ALIYUN_ACCESS_KEY_ID` | live OCR only (Aliyun `RecognizeAdvanced`) |
| `ERP_ALIYUN_ACCESS_KEY_SECRET` | live OCR only |
| `ERP_QWEN_API_KEY` | live OCR only (Qwen-VL-Max) |
| `ERP_SERVICE_KEY` | shared secret the gateway presents; must equal WeCom1 `WECOM_ERP_API_KEY` |
| `ERP_WECOM_GATEWAY_URL` | `https://wecom.yourdomain.com` (or the WeCom1 Railway domain) |
| `ERP_WECOM_GATEWAY_KEY` | shared secret ERP presents to gateway; must equal `WECOM_GATEWAY_SERVICE_KEY` |
| `ERP_NOTIFY_ENABLED` | `true` / `false` |
| `ERP_CORS_ORIGINS` | `https://erp.yourdomain.com` (the SPA is same-origin, but set for safety) |
| `ERP_ACCESS_TOKEN_EXPIRE_MINUTES` | `720` |
| `ERP_FILES_DIR` | leave default (filesystem is ephemeral on Railway) |
| `VITE_WECOM_GATEWAY_URL` | **build** variable: `https://wecom.yourdomain.com` (consumed by Vite at build) |

**WeCom1 gateway** (set in Railway → Service → Variables):
| Variable | Example / note |
|---|---|
| `WECOM_DATABASE_URL` | Postgres plugin string, **or** unset → SQLite fallback |
| `WECOM_MODE` | `mock` (default) until going live |
| `WECOM_CORP_ID` / `WECOM_AGENT_ID` / `WECOM_SECRET` / `WECOM_TOKEN` / `WECOM_ENCODING_AES_KEY` | live WeCom only |
| `WECOM_DECRYPT_PROVIDER` | `pure` (RSA/AES via `cryptography`, no vendor binary) |
| `WECOM_STAFF_USERIDS` / `WECOM_ORDER_GROUP_IDS` / `WECOM_INTERNAL_OPS_CHAT_ID` | routing; set before live |
| `WECOM_SEND_ALLOWLIST` | empty in mock; set your own userid for the first live send |
| `WECOM_ERP_BASE_URL` | `https://erp.yourdomain.com` (or the food2 Railway domain) |
| `WECOM_ERP_API_KEY` | must equal food2 `ERP_SERVICE_KEY` |
| `WECOM_GATEWAY_SERVICE_KEY` | must equal food2 `ERP_WECOM_GATEWAY_KEY` |
| `WECOM_CORS_ORIGINS` | `https://erp.yourdomain.com` (the WeCom console calls the gateway directly from the browser) |
| `WECOM_MEDIA_URL_BASE` | `https://wecom.yourdomain.com/wecom/media` (so the ERP can fetch attachments) |

### 9.5 The two shared secrets (must match on both sides)
- **ERP ↔ gateway handshake (inbound orders):** food2 `ERP_SERVICE_KEY`
  **==** WeCom1 `WECOM_ERP_API_KEY`. A mismatch 401s every inbound order.
- **ERP → gateway outbound (notifications):** food2 `ERP_WECOM_GATEWAY_KEY`
  **==** WeCom1 `WECOM_GATEWAY_SERVICE_KEY`.
Generate each once (e.g. `openssl rand -hex 16`) and set on **both** services.

### 9.6 Namecheap custom domain (one domain, two subdomains)
1. In **Railway**, for each service → Settings → **Domains** → **Add Domain**:
   - food2 → `erp.yourdomain.com`
   - WeCom1 → `wecom.yourdomain.com`
   Railway returns a **DNS target** (e.g. `cname.railway.app` or the service's
   `*.up.railway.app` host). Copy it.
2. In **Namecheap** → Domain List → Manage → **Advanced DNS** → **Add New Record**:
   - Type **CNAME**, Host `erp`, Value `<food2 Railway target>`, TTL Auto.
   - Type **CNAME**, Host `wecom`, Value `<WeCom1 Railway target>`, TTL Auto.
   (For the root/apex domain use an **ALIAS** record pointing at the Railway
   target instead of CNAME.)
3. Wait for propagation (minutes to an hour). Railway auto-provisions a TLS
   certificate (Let's Encrypt) once the CNAME is verified — the domain shows a
   green check.
4. Set the cross-referencing variables from §9.4/§9.5 to the new `https://…`
   URLs and **Redeploy** both services.

### 9.7 Verify it's actually live
- food2: `curl -fsS https://erp.yourdomain.com/api/health` → `{"status":"ok",…}`.
- WeCom1: `curl -fsS https://wecom.yourdomain.com/wecom/health` → `{"status":"ok",…}`.
- In Railway, the service is **Healthy** (no "Application failed to respond").
- End-to-end smoke (mock mode): WeCom1 forwards a simulated message → food2
  creates an intake job → OCR gate flags handwritten notes for review.

### 9.8 Troubleshooting
- **"Application failed to respond" — wrong GitHub *source* repo (the cause we
  actually hit):** If the code is fixed (psycopg2 present, `lifespan` hardened)
  yet the link still errors, open Railway → Service → **Settings → Source** and
  confirm the connected repo is `unzexp-sudo/food2` / `unzexp-sudo/WeCom1` on
  branch `main`. A stale fork (`htjani/food2`) or a source the Railway GitHub
  app can't read will keep rebuilding the OLD commit no matter how many times you
  press **Redeploy** — "Redeploy" re-runs the *same pinned commit*, it never
  pulls a newer one. Fix = disconnect and reconnect the source while logged into
  GitHub as **`unzexp-sudo`** (see §9.10). This is the #1 recurring footgun here.
- **"Application failed to respond" — import-time crash:** Open the Railway deploy
  logs; look for `ModuleNotFoundError: No module named 'psycopg2'` or a dialect
  error. Fix = ensure the driver is in `requirements.txt` (done for both) and
  harden `lifespan` (done). A DB blip alone should no longer kill the container.
- **Healthcheck times out** → confirm the path matches the `railway.toml`
  `healthcheckPath` (`/api/health` for food2, `/wecom/health` for WeCom1) and that
  the app binds to `${PORT}` (Railway injects `PORT`).
- **401 on inbound orders** → the two `ERP_SERVICE_KEY` / `WECOM_ERP_API_KEY`
  values don't match.
- **CORS errors in the WeCom console** → `WECOM_CORS_ORIGINS` must include the
  browser origin the operator opens the console from.
- **Data disappears after restart** → you're on the SQLite fallback; attach the
  Postgres plugin and set the `*_DATABASE_URL` to its string.

### 9.9 Push checklist (do this in order)
1. `cd /Users/harshjani/Documents/WeCom1 && git push origin main`  ← needs the
   `unzexp-sudo` PAT (htjani token is denied). This triggers the WeCom1 rebuild.
2. Confirm food2 already rebuilt from `8965ce9` (it was pushed earlier).
3. Set all variables in §9.4, the two secrets in §9.5.
4. Wire Namecheap CNAMEs (§9.6), wait for TLS, redeploy both.
5. Run the §9.7 health curls.

### 9.10 Guard — Railway GitHub source MUST be `unzexp-sudo/*` (not `htjani`)

This is the single most common reason the links stay broken after a "successful"
push. The Railway GitHub integration was originally authorized under the `htjani`
identity, so it was reading `htjani/food2` (a stale fork that never received the
psycopg2 fixes) and could not fire webhooks for `unzexp-sudo/*`. Symptoms:
- Top Railway deployment commit is NOT `91a1983` (food2) / `1b26900` (WeCom1).
- Manual **Redeploy** keeps rebuilding the *older* version.
- `htjani/WeCom1` does not even exist, yet the WeCom1 deploy never updates.

**Fix (Railway dashboard, ~2 min per service — cannot be done from CI/CLI):**
1. Project → Service → **Settings → Source** (or "Connected Repository").
2. Disconnect / Change source.
3. Click **Deploy from GitHub** and **log in to GitHub as `unzexp-sudo`** (switch
   accounts if it defaults to `htjani`).
4. Select **`unzexp-sudo/food2`** and **`unzexp-sudo/WeCom1`**, branch **`main`**.
5. Save → Railway builds `91a1983` / `1b26900`. Future `git push` auto-deploys.

Verify the running source with:
```bash
curl -fsS https://food2-production.up.railway.app/api/health
curl -fsS https://wecom1-production.up.railway.app/wecom/health
```
Both must return `200 {"status":"ok",…}`. If they do, the reconnect worked.

### 9.11 Frontend build — keep ALL of `node_modules` in ONE vendor chunk

food2's React SPA is served by FastAPI from `backend/static`. After the API/routing
fixes, the page was still blank with TWO different boot-time crashes, both caused by
Vite **circular vendor-chunk imports**:

1. `Cannot read properties of undefined (reading 'version')` in the antd chunk →
   the antd chunk's React import resolved to `undefined` because antd and react
   were in separate chunks that cross-imported.
2. `Cannot access 'fo' before initialization` (TDZ) in the vendor chunk → the
   `react-vendor`↔`vendor` cycle persisted (antd's transitive deps like `dayjs`,
   `scroll-into-view-if-needed` landed in `vendor` and referenced back).

**Rule (see `frontend/vite.config.ts`):** `manualChunks` returns a single `"vendor"`
chunk for anything under `node_modules`. Do NOT split antd / @ant-design / rc-* /
react / scheduler into separate chunks — the resulting circular graph throws
undefined-binding / TDZ errors at boot and the SPA renders blank. One big cached
vendor chunk is the correct trade-off here.

### 9.12 Canonical `*.up.railway.app` domain returns "Application not found"

Railway serves each app at its **per-deployment** domain (e.g.
`food2-production-7c23.up.railway.app`, `wecom1-production-4bc1.up.railway.app`),
NOT the bare `food2-production.up.railway.app` / `wecom1-production.up.railway.app`
form — those return `{"status":"error","code":404,"message":"Application not found"}`.
So: either use the working per-deployment URL, or wire the **Namecheap custom
domain** (§9.6: CNAME `erp`/`wecom` → the Railway target) and use
`https://erp.yourdomain.com` / `https://wecom.yourdomain.com` as the real
endpoints. The cross-referencing env vars in §9.4/§9.5 should then point at those
custom domains.


