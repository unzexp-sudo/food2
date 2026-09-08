# AGENT_CONTRACTS.md — FoodSupply ERP Build Contracts

This document is the **binding contract** for every module. Read it fully before writing code.
Also read `docs/EXECUTIVE_SUMMARY.md` for business context.

---

## 1. Repository layout & file ownership

```
Foshan1/
├── backend/
│   ├── requirements.txt            # FIXED — do not modify
│   ├── seed.py                     # FIXED — do not modify
│   ├── app/
│   │   ├── main.py                 # FIXED — do not modify (all routers pre-registered)
│   │   ├── core/                   # FIXED — config, database, security, deps, events, audit, numbers, pagination
│   │   ├── models/                 # FIXED — the complete schema. DO NOT EDIT. Never add/remove fields.
│   │   ├── schemas/common.py       # FIXED — shared Pydantic helpers
│   │   ├── api/v1/                 # ONE OWNER PER FILE (see table below)
│   │   ├── services/               # one subpackage per module, owned accordingly
│   │   └── ai/                     # owned by intake agent
│   └── tests/                      # test_<module>.py per agent; conftest.py FIXED
├── frontend/                       # owned by frontend agents (see §7)
├── docs/                           # FIXED
└── docker-compose.yml, Dockerfiles # FIXED
```

| File / area | Owner |
|---|---|
| `app/api/v1/auth.py`, `app/api/v1/system.py`, `app/services/system/` | **auth/system agent** |
| `app/api/v1/customers.py`, `catalog.py`, `wholesalers.py`, `contracts.py`, `app/services/masterdata/` | **master data agent** |
| `app/api/v1/intake.py`, `app/ai/`, `app/services/intake/` | **intake agent** |
| `app/api/v1/orders.py`, `app/api/v1/procurement.py`, `app/services/orders/` | **orders/procurement agent** |
| `app/api/v1/warehouse.py`, `app/api/v1/delivery.py`, `app/services/warehouse/` | **warehouse/delivery agent** |
| `app/api/v1/finance.py`, `app/services/finance/` | **finance agent** |
| `frontend/src/pages/intake/`, `orders/`, `consolidation/`, `purchase-orders/` | **FE screens A agent** |
| `frontend/src/pages/warehouse/`, `delivery/`, `finance/`, `master/`, `system/` | **FE screens B agent** |

**Hard rules:**
- Never edit files outside your ownership. Never edit `models/`, `core/`, `main.py`, `seed.py`, `conftest.py`, or another module's files.
- If a model is missing something you need, **work around it** (compute in a service/serializer) and note it in your final report.
- Each stub router file contains a spec of exactly which endpoints to implement. Replace the stub body; keep `router` name and prefix.
- Only use libraries in `requirements.txt` (backend) or already installed by the foundation (frontend). **Never run pip/npm install.**

---

## 2. Tech & runtime

- **Backend**: Python 3.13, FastAPI, SQLAlchemy 2.x (declarative), Pydantic v2. No other frameworks.
- **DB**: SQLAlchemy URL from settings; SQLite for dev/test (already works with all model types used), PostgreSQL in production (via docker-compose).
- **Jobs**: FastAPI `BackgroundTasks` only (no Celery/Redis in this build).
- **Files**: local disk under `settings.files_dir` via path helpers in `core/config.py`.
- **Python to use**: `/Users/harshjani/.workbuddy-ai/binaries/python/envs/default/bin/python` (deps pre-installed).
- **Node/npm for frontend agents**: `/Users/harshjani/.workbuddy-ai/binaries/node/versions/22.22.2-2/bin/node` and `.../bin/npm` (same dir).

### Backend conventions

- Every router file: `router = APIRouter(prefix="/api/v1/<resource>", tags=["<resource>"])` — prefixes are already set in the stubs; keep them.
- Path params use snake_case; response models are Pydantic schemas you define in `app/schemas/<module>.py` (create the file; you own it).
- **Auth**: `from app.core.deps import get_current_user, require_roles` — `Depends(require_roles("admin", "ops"))`. Role names: `admin`, `ops`, `warehouse`, `finance`, `driver`.
- **Pagination** (all list endpoints): query params `page: int = 1, page_size: int = 20` (max 100), response:
  ```json
  {"items": [...], "total": 42, "page": 1, "page_size": 20}
  ```
  Use `app.core.pagination.paginate(items, total, page, page_size)`.
- **Errors**: raise `HTTPException(status_code, detail)`. 400 validation/business, 401 unauthenticated, 403 wrong role, 404 missing, 409 state conflict (e.g. confirming a rejected order).
- **Audit**: call `app.core.audit.log_audit(db, actor, entity_type, entity_id, action, before=..., after=...)` on every state-changing business action (create/update/confirm/send/receive/complete). Wrap entity names in English; FE translates.
- **Numbers**: business numbers via `app.core.numbers.next_number(db, Model, prefix)` → e.g. `ORD-20260907-0001`. Prefixes: `ORD` orders, `CON` consolidation batches, `PO` purchase orders, `RCP` inbound receipts, `PCK` pick lists, `DLV` deliveries, `INV` invoices, `PAY` payments.
- **Cross-module triggers**: use `app.core.events` (tiny in-process pub/sub):
  - `events.emit("order.draft_created", db=db, order=order)` — emitted by intake after creating a draft order. **Orders agent registers a handler** that runs auto-confirm evaluation.
  - `events.emit("delivery.completed", db=db, delivery=delivery)` — emitted by the delivery endpoint. **Finance agent registers a handler** that auto-generates the invoice.
  - Handlers are registered at module import time (top of your router or service module): `events.on("order.draft_created")(my_handler)`. Handlers receive keyword args, must never raise (catch and log), and must do their own `db.commit()` on the passed session.
- **Transactions**: endpoints commit their own session; event handlers commit too. Never commit inside services unless the caller passes `commit=True`.
- **IDs**: string UUIDs (models default them). Expose as plain strings.
- **Bilingual master data**: entities have `name_en` / `name_zh` (+ optional `name` free fields elsewhere). APIs return both fields; the FE picks per language.

### Testing (backend agents)

Copy the pattern from `tests/conftest.py` (FIXED). Write `tests/test_<yourmodule>.py`:

```python
from tests.conftest import client, admin_headers

def test_something(client, admin_headers):
    r = client.get("/api/v1/your-resource", headers=admin_headers)
    assert r.status_code == 200
```

`conftest.py` boots the full app against a fresh temp SQLite DB, seeds it (via `seed.py`), and provides `admin_headers` (role admin) and `ops_headers` (role ops). Run with:

```
cd /Users/harshjani/Documents/Foshan1/backend
/Users/harshjani/.workbuddy-ai/binaries/python/envs/default/bin/python -m pytest tests/test_<yourmodule>.py -x -q
```

Do NOT start uvicorn servers or long-running processes. TestClient only. Your tests must pass before you finish.

---

## 3. Status enums (exact strings — never invent new ones without noting it)

| Domain | Values |
|---|---|
| User roles | `admin`, `ops`, `warehouse`, `finance`, `driver` |
| OrderStatus | `draft`, `pending_confirmation`, `needs_clarification`, `confirmed`, `consolidated`, `fulfilled`, `invoiced`, `rejected` |
| IntakeJobStatus | `queued`, `processing`, `completed`, `failed` |
| IntakeSourceType | `text`, `image`, `pdf`, `excel`, `email_body` |
| ConsolidationStatus | `open`, `closed` |
| POStatus | `draft`, `sent`, `partially_received`, `received`, `closed`, `cancelled` |
| PickListStatus | `open`, `picking`, `picked`, `cancelled` |
| PickLineStatus | `open`, `picked`, `short` |
| DeliveryStatus | `scheduled`, `picked`, `out_for_delivery`, `delivered`, `failed`, `partial` |
| InvoiceStatus | `draft`, `issued`, `partial`, `paid`, `void` |
| PaymentDirection | `inbound` (customer pays us), `outbound` (we pay wholesaler) |
| CustomerType | `school`, `restaurant`, `canteen`, `other` |
| Master data status | `active`, `inactive` |

---

## 4. Entity field dictionaries (what the API returns — FE codes against this)

Common on almost everything: `id`, `created_at`, `updated_at` (ISO 8601). Money & quantities are JSON numbers (floats).

**User**: `id, email, name, role, is_active, created_at`
**Customer**: `id, code, name_en, name_zh, type, contact_name, contact_phone, address, delivery_zone, notes, status, created_at`
**CustomerContact**: `id, customer_id, name, phone, role`
**CustomerAlias**: `id, customer_id, alias, product_id`
**Category**: `id, name_en, name_zh, is_active`
**Unit**: `id, code, name_en, name_zh` (codes seeded: `jin`, `kg`, `box`, `bag`, `piece`)
**Product**: `id, sku, name_en, name_zh, category_id, category_name_en, category_name_zh, default_unit_id, default_unit_code, shelf_life_days, is_active`
**Wholesaler**: `id, code, name_en, name_zh, contact_name, contact_phone, is_active`
**ProductWholesalerMapping**: `id, product_id, wholesaler_id, supplier_sku, cost_price`
**SupplierRule**: `id, category_id (nullable), product_id (nullable), wholesaler_id, priority, moq, lead_time_days, is_default`
**ContractPrice**: `id, customer_id, product_id, unit_id, price, valid_from, valid_until`
**StandingOrderTemplate**: `id, customer_id, name, delivery_days` (list like `["mon","thu"]`), `is_active`, `lines: [{id, product_id, quantity, unit_id}]`
**IntakeDocument**: `id, customer_id (nullable), source_type, original_filename, file_url, file_hash, uploaded_by, created_at`
**IntakeJob**: `id, document_id, status, error, retry_count, draft_order_id (nullable), created_at, finished_at`
**Order**: `id, order_number, customer_id, customer_name_en, customer_name_zh, status, delivery_date, source_type, intake_document_id, standing_template_id, overall_confidence, confirmed_by, confirmed_at, notes, line_count, created_at`
**OrderLine**: `id, order_id, line_no, raw_text, product_id (nullable), product_display (name to show when unmatched), quantity, unit_id (nullable), unit_code, unit_price (nullable), confidence (0–1), match_method` (values: `alias_exact`, `catalog_exact`, `fuzzy`, `manual`, `unmatched`)
**ConsolidationBatch**: `id, batch_number, delivery_date, cutoff_at, status, order_count, exception_count, created_by, created_at`
**PurchaseOrder**: `id, po_number, wholesaler_id, wholesaler_name_en, wholesaler_name_zh, category_id, category_name_en, category_name_zh, batch_id, status, total_amount, sent_at, notes, created_at`
**PurchaseOrderLine**: `id, po_id, product_id, product_name_en, product_name_zh, quantity_ordered, unit_id, unit_code, cost_price, quantity_received, source_order_count`
**InboundReceipt**: `id, receipt_number, po_id, po_number, received_by, received_at, status, notes` + lines `[{id, po_line_id, product_name_en, product_name_zh, quantity_ordered, quantity_received, quantity_damaged, discrepancy, notes}]`
**PickList**: `id, pick_number, delivery_date, status` + lines `[{id, order_id, order_number, customer_name_en, customer_name_zh, product_id, product_name_en, product_name_zh, quantity, picked_quantity, status}]`
**Delivery**: `id, delivery_number, order_id, order_number, customer_id, customer_name_en, customer_name_zh, route, driver_id, driver_name, status, scheduled_date, picked_at, out_at, delivered_at, pod` (nullable `{photo_url, signature_url, received_by, gps_lat, gps_lng, delivered_at}`) + lines `[{id, order_line_id, product_name_en, product_name_zh, quantity, delivered_quantity}]`
**Invoice**: `id, invoice_number, customer_id, customer_name_en, customer_name_zh, order_id, order_number, status, total_amount, paid_amount, issued_at, created_at` + lines `[{id, order_line_id, description, quantity, unit_price, amount}]`
**Payment**: `id, number, invoice_id (nullable), direction, counterparty, amount, method, paid_at, note`
**AuditLog**: `id, entity_type, entity_id, action, actor_name, summary, created_at`
**Settings**: key/value map, e.g. `{"auto_confirm": {"enabled": true, "min_confidence": 0.95}, "cutoff_time": "18:00"}`

---

## 5. API surface (binding)

All paths prefixed `/api/v1`. `R` = required roles. Pagination params apply to all GET lists.

### auth (auth agent)
```
POST /auth/login            {email, password} → {token, user}        (public)
GET  /auth/me               → User                                     (any)
POST /auth/change-password  {old_password, new_password}              (any)
```

### system (auth agent)
```
GET/POST /users, PATCH/DELETE /users/{id}            R: admin
GET      /audit-logs?entity_type=&entity_id=         R: admin, ops
GET/PUT  /settings                                    R: admin
GET      /settings/public                             (any logged-in; returns non-sensitive subset: cutoff_time, auto_confirm)
```

### customers (master data agent)
```
GET/POST /customers, GET/PATCH/DELETE /customers/{id}                 R: ops/admin (GET also finance)
GET  /customers/{id}/contacts, POST /customers/{id}/contacts,
DELETE /customers/contacts/{contact_id}
GET  /customers/{id}/aliases, POST /customers/{id}/aliases,
DELETE /customers/aliases/{alias_id}
```

### catalog (master data agent)
```
GET/POST /products, GET/PATCH/DELETE /products/{id}          R: ops/admin (GET any role)
GET/POST /product-categories, PATCH/DELETE /product-categories/{id}
GET/POST /units, PATCH/DELETE /units/{id}
GET  /products/{id}/wholesalers  → mappings for the product
```

### wholesalers (master data agent)
```
GET/POST /wholesalers, GET/PATCH/DELETE /wholesalers/{id}
GET/POST /product-wholesaler-mappings, DELETE /product-wholesaler-mappings/{id}
GET/POST /supplier-rules, PATCH/DELETE /supplier-rules/{id}
```

### contracts (master data agent)
```
GET/POST /contract-prices?customer_id=&product_id=, DELETE /contract-prices/{id}
GET/POST /standing-order-templates, GET/PATCH/DELETE /standing-order-templates/{id}
POST /standing-order-templates/{id}/create-order   → creates a draft order (no AI)
```

### intake (intake agent)
```
POST /intake/submit                               R: ops/admin
  multipart/form-data OR json:
    customer_id?: string, delivery_date?: date,
    source_type: text|image|pdf|excel|email_body,
    raw_text?: string, file?: UploadFile
  → {document_id, job_id}   (processing runs as background task)
GET  /intake/documents?customer_id=               R: ops/admin/finance
GET  /intake/documents/{id}                       → IntakeDocument
GET  /intake/documents/{id}/file                  → FileResponse (original)
GET  /intake/jobs?status=                         R: ops/admin
GET  /intake/jobs/{id}                            → IntakeJob (poll until completed)
POST /intake/jobs/{id}/retry                      R: ops/admin
GET  /intake/extractions/{job_id}                 → raw AI output (immutable audit trail)
```

### orders (orders agent)
```
GET    /orders?status=&customer_id=&delivery_date=&q=      R: ops/admin/finance (finance read-only)
POST   /orders                    manual create {customer_id, delivery_date, notes, lines:[{product_id?, product_display, quantity, unit_id?, unit_price?}]}  R: ops/admin
GET    /orders/{id}               → Order + lines
PATCH  /orders/{id}/lines         bulk replace lines (same shape as create)  R: ops/admin
POST   /orders/{id}/confirm       {notes?}  → sets status confirmed; locks contract prices onto lines  R: ops/admin
POST   /orders/{id}/reject        {reason}   R: ops/admin
POST   /orders/{id}/request-clarification  {note}  → status needs_clarification
POST   /orders/{id}/resubmit      from needs_clarification back to pending_confirmation
GET    /orders/{id}/lineage       → per order line: batch, PO line, received, picked, delivered, invoice (see §6)
```

### consolidation & POs (orders agent)
```
POST /consolidation/run           {delivery_date} → {batch, purchase_orders, exceptions}  R: ops/admin
GET  /consolidation/batches?delivery_date=&status=
GET  /consolidation/batches/{id}  → batch + POs + exceptions (lines with no supplier mapping)
GET  /purchase-orders?status=&wholesaler_id=&delivery_date(batch delivery date)=
GET  /purchase-orders/{id}        → PO + lines (each line embeds source order numbers)
PATCH /purchase-orders/{id}/lines bulk edit lines before send (quantities/cost prices)  R: ops/admin
POST /purchase-orders/{id}/send   draft → sent  R: ops/admin
POST /purchase-orders/{id}/cancel R: ops/admin
```

### warehouse (warehouse agent)
```
GET  /purchase-orders/{id}/received   → summary of receipts vs PO (also embedded in PO detail)
POST /inbound-receipts          {po_id, lines: [{po_line_id, quantity_received, quantity_damaged?, notes?}]}  R: warehouse/admin
       → creates receipt, flags discrepancies, updates PO status (partially_received/received),
         emits pick list regeneration for the affected delivery date, records inventory movements
GET  /inbound-receipts?po_id=
GET  /pick-lists?delivery_date=&status=
GET  /pick-lists/{id}
POST /pick-lists/{id}/lines/{line_id}/pick   {picked_quantity}   R: warehouse/admin
POST /pick-lists/generate       {delivery_date}  manual regen    R: warehouse/admin
GET  /inventory?product_id=     → movements ledger  R: warehouse/admin
POST /inventory/loss            {product_id, quantity, reason}   R: warehouse/admin
```

### delivery (warehouse agent)
```
GET  /deliveries?date=&status=&driver_id=
GET  /deliveries/{id}
POST /deliveries/generate       {delivery_date} → creates one Delivery per order picked for that date  R: ops/admin/warehouse
POST /deliveries/{id}/assign    {driver_id}     R: ops/admin
POST /deliveries/{id}/status    {status}  (picked / out_for_delivery)  R: warehouse/admin/driver
POST /deliveries/{id}/complete  {lines: [{delivery_line_id, delivered_quantity}], received_by?, photo?: UploadFile, gps_lat?, gps_lng?}
       → status delivered (or partial if short, failed if zero), stores POD, emits "delivery.completed"  R: warehouse/admin/driver
```

### finance (finance agent)
```
GET  /statements/customer/{id}?from=&to=   → AR rows from delivered quantities: [{order_id, order_number, delivery_date, description, quantity, unit_price, amount}]
GET  /statements/wholesaler/{id}?from=&to= → AP rows from inbound receipts
POST /invoices/generate       {order_id}  → Invoice from delivered quantities × order line prices  R: finance/admin
GET  /invoices?status=&customer_id=
GET  /invoices/{id}
POST /invoices/{id}/payments  {amount, method, note}  → updates paid_amount/status, creates Payment  R: finance/admin
GET  /payments?direction=&invoice_id=
GET  /reports/margin?by=customer|category|product&from=&to= → [{dimension_id, dimension_name, revenue, cost, margin, margin_pct}]
```

---

## 6. Lineage response shape

`GET /orders/{id}/lineage`:

```json
{
  "order_id": "...", "order_number": "ORD-...",
  "lines": [
    {
      "order_line_id": "...", "raw_text": "土豆50斤",
      "product_name_en": "Potato", "product_name_zh": "土豆",
      "quantity": 50, "unit_code": "jin",
      "batch_number": "CON-...", "po_number": "PO-...",
      "po_quantity": 150, "received_quantity": 148,
      "picked_quantity": 50, "delivered_quantity": 50,
      "invoice_id": "...", "invoice_number": "INV-..."
    }
  ]
}
```

Build it by walking: `order_line → consolidation_batch_lines → purchase_order_lines → inbound_receipt_lines (via po_line) → pick_lines → delivery_lines → invoice_lines`.

---

## 7. Frontend contracts

- Stack: Vite + React + TypeScript + Ant Design 5 + react-router-dom v6 + axios + i18next/react-i18next + dayjs.
- **Bilingual**: language toggle in header (EN / 中文), persisted in localStorage, sets both i18n language and AntD `ConfigProvider` locale. Translation files `src/i18n/en.ts`, `src/i18n/zh.ts` — extend them; never hardcode UI strings.
- Routes (already registered by foundation; each has a placeholder page you replace):
  ```
  /login
  /intake                    Intake inbox
  /orders                    Order list + pending pool
  /orders/:id                Order detail + lineage
  /consolidation             Consolidation dashboard
  /purchase-orders           PO list
  /purchase-orders/:id       PO detail
  /warehouse/inbound         Inbound receipts
  /warehouse/pick-lists      Pick lists
  /warehouse/inventory       Inventory ledger
  /delivery                  Delivery board
  /finance/invoices          Invoices
  /finance/statements        AR/AP statements
  /finance/margin            Margin report
  /master/customers          Customers (+contacts/aliases via drawers)
  /master/products           Products + categories + units (tabs)
  /master/wholesalers        Wholesalers + mappings
  /master/contracts          Contract prices + supplier rules (tabs)
  /master/standing-orders    Standing order templates
  /system/users              User management (admin)
  /system/audit              Audit log
  /system/settings           Settings (admin)
  ```
- API client at `src/api/client.ts` (axios, `baseURL: "/api/v1"`, Bearer token, 401 → redirect to /login). Shared hooks in `src/api/hooks.ts`.
- Role-based UI: hide menu items the role can't use (roles: admin sees all; ops: intake/orders/consolidation/POs/master; warehouse: warehouse/delivery; finance: finance + read orders; driver: delivery).
- Dates: display `YYYY-MM-DD`; datetimes `YYYY-MM-DD HH:mm`.
- Confidence: API 0–1 → display as percent, color: ≥0.95 green, ≥0.7 orange, else red.
- Every page: AntD `Table` + filters in a card; detail pages/drawers for edit forms. Keep it clean and dense — this is an ops tool.
- **Verify with `npm run build`** — it must pass with zero TypeScript errors before you finish. Do not run `npm install` for new packages; use what's installed (antd, @ant-design/icons, react-router-dom, axios, i18next, react-i18next, dayjs).

---

## 8. Demo & seed data (already in `seed.py`)

- Users: `admin@erp.local` / `ops@erp.local` / `warehouse@erp.local` / `finance@erp.local` / `driver@erp.local`, all password `erp123`.
- Units: jin 斤, kg 公斤, box 箱, bag 袋, piece 个.
- Categories: Vegetables 蔬菜, Meat 肉类, Rice & Grain 米面粮油, Condiments 调味品.
- ~16 products (bilingual, e.g. Potato 土豆, Cabbage 大白菜, Pork 五花肉, Rice 大米), 3 customers (Foshan No.1 Primary School 佛山第一小学 / school, Golden Dragon Restaurant 金龙酒家 / restaurant, Nanhai District Canteen 南海区政府食堂 / canteen) with aliases (e.g. "土豆", "potato", "洋芋" → Potato), 2 wholesalers, product-wholesaler mappings with cost prices, default supplier rules per category, contract prices, one standing order template, default settings.
- Demo intake text (works with the mock AI): `"土豆 50斤\n大白菜 30斤\n五花肉 20斤\n大米 2袋"`.

---

## 9. Verification checklist for every agent

1. All your endpoints implemented exactly per §5 (paths, methods, roles, shapes).
2. Your tests pass: `python -m pytest tests/test_<module>.py -x -q` (backend) / `npm run build` (frontend).
3. No edits outside your owned files (`git diff --stat` if in doubt — ask by reading files, don't guess).
4. Bilingual fields present where §4 says so; statuses exactly per §3.
5. Final report: list endpoints/pages built, tests added, any workarounds or contract gaps found.
