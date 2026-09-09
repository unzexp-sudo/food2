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
- When ready: `git push` to `unzexp-sudo/food2` per the established push path
  (keychain stores the unzexp-sudo PAT; do NOT use the foreign `htjani` token).
