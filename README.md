# FoodSupply ERP

A B2B food-supply ERP with an AI intake layer. Customers (schools, restaurants, government canteens) send orders in **any format** — text, handwritten notes, photos, PDFs, spreadsheets. An **AI intake layer** extracts structured order data; the ERP handles confirmation, procurement merge, warehouse, delivery, and billing with full **line-level lineage** traceability.

Bilingual: **English + 中文** with a one-click language toggle.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     INTAKE LAYER                        │
│  Upload API │ OCR/PDF/Excel extract │ LLM Extractor     │
│              │ SKU fuzzy match + aliases │ Confidence    │
└──────────────────────────┬──────────────────────────────┘
                           │ structured draft orders
┌──────────────────────────▼──────────────────────────────┐
│                      ERP CORE                           │
│  Orders │ Procurement │ Warehouse │ Delivery │ Finance  │
└──────────────────────────┬──────────────────────────────┘
                           │
                    SQLite (dev) / PostgreSQL (prod)
```

**Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.x · Pydantic v2 · React 18 + TypeScript · Vite · Ant Design 5 · react-i18next. Modular monolith — every business module is an independent package under `app/api/v1` + `app/services/<module>`.

## The Lineage Model (non-negotiable)

```
customer_order_line
  → consolidation_batch_line
    → purchase_order_line
      → inbound_receipt_line
        → pick_line
          → delivery_line
            → invoice_line
```

`GET /api/v1/orders/{id}/lineage` walks the full chain per line.

## Repo layout

```
backend/
  app/
    api/v1/      routers per module (auth, system, customers, catalog,
                 wholesalers, contracts, intake, orders, procurement,
                 warehouse, delivery, finance)
    services/    business logic per module
    ai/          intake adapters (Mock + OpenAI-compatible) + pipeline + matching
    models/      the full schema (FIXED contract — see docs/AGENT_CONTRACTS.md)
    core/        config, db, security, deps, events, audit, numbers, pagination
  seed.py        demo data (5 users, 4 categories, ~16 bilingual products,
                 3 customers w/ aliases, 2 wholesalers, mappings, rules,
                 contract prices, 1 standing template)
  tests/         pytest suite (226 tests)
frontend/
  src/
    api/         axios client + hooks
    i18n/        en.ts + zh.ts (toggle persisted in localStorage)
    layouts/     AdminLayout (role-filtered sidebar, lang switch)
    pages/       one dir per module; bilingual screens
docs/
  EXECUTIVE_SUMMARY.md   the original build plan
  AGENT_CONTRACTS.md     the binding contract every module codes against
  WECOM_CONTRACTS.md     the binding contract for the WeCom gateway
wecom-gateway/           standalone WeCom (企业微信) ↔ ERP gateway (port 8100)
docker-compose.yml        postgres + backend + nginx frontend
```

## Quick start (dev)

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env          # SQLite default — works out of the box
uvicorn app.main:app --reload --port 8000
```

OpenAPI docs: http://localhost:8000/docs · Health: http://localhost:8000/api/health

### Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173  (proxies /api → :8000)
```

Log in with any seeded account (password `erp123`):
`admin@erp.local` · `ops@erp.local` · `warehouse@erp.local` · `finance@erp.local` · `driver@erp.local`

### Tests

```bash
cd backend && python -m pytest tests/ -q        # 263 tests
cd wecom-gateway && python -m pytest -q         # 245 tests, hermetic (temp SQLite)
cd frontend && npm run build                    # 0 TypeScript errors
```

### WeCom gateway (optional, mock mode by default)

```bash
cd wecom-gateway
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8100     # health: /wecom/health → {"mode":"mock"}

# in another shell: produce a fake WeCom message stream, then pull it
python -m simulator.producer --scenario all
python -c "from app.core.database import SessionLocal; from app.services.archive import pull_once; \
db=SessionLocal(); print(pull_once(db)); db.close()"

# before going live: check credentials + whether your IP is allow-listed
python scripts/preflight.py
```

The gateway is **not** required to run the ERP. It defaults to `WECOM_MODE=mock`,
so it never contacts WeCom and needs no credentials. The WeCom console pages
appear in the frontend sidebar for `admin` and `ops` once it is running.
Full details — env vars, the identity cascade, the go-live checklist — are in
[`wecom-gateway/README.md`](wecom-gateway/README.md).

> Credentials have been validated against a real WeCom corp (`gettoken` →
> `errcode=0`), but **no message has ever been sent to a real customer**.
> Business APIs currently return `errcode 60020` because the caller's IP is not
> in the corp's 可信IP allow-list. Run `python scripts/preflight.py` to see the
> current state; it exits `0` only when a live send would actually work.

### Docker (production-style)

```bash
docker compose up --build        # postgres :5432, backend :8000, frontend :8080
```

## Demo flow (Definition of Done)

1. Log in as **ops**, go to **Intake inbox → New intake**, paste:
   ```
   土豆 50斤
   大白菜 30斤
   五花肉 20斤
   大米 2袋
   ```
2. Watch the AI extract 4 matched lines (confidence 1.0, `alias_exact`) → draft order `ORD-…`.
3. Open the order → **Confirm** (or it auto-confirms before 18:00 if confidence ≥ 95%).
4. **Consolidation → Run** for the delivery date → generates wholesaler POs `PO-…` with full traceability.
5. As **warehouse**: receive each PO → pick lists auto-generate → pick all lines.
6. **Deliveries → Generate** → mark picked → out → **Complete** with POD (photo/GPS/received-by).
7. As **finance**: an invoice auto-generated on delivery (`INV-…`); open the order's **Lineage** tab to trace every line from intake document → PO → inbound → pick → delivery → invoice; check the **customer statement** and **margin report**.

## Automated end-to-end demo

`scripts/demo_order_flow.py` exercises the entire lifecycle with **zero credentials**
— it boots the ERP + WeCom gateway on fresh temp databases, injects a WeCom-style
order through the simulator, waits for AI intake to create a draft order, then
fast-forwards the whole chain with made-up numbers:

```
confirm → consolidate → PO send → receive → pick → deliver → invoice
```

It prints each step and where to look in the UI, and dumps the mock WeCom outbox
(the exact messages a real customer would receive). Runs in ~25 seconds.

```bash
python scripts/demo_order_flow.py
```

## AI intake — processing rules

| Input | Pipeline |
|---|---|
| Handwritten photo | (mock OCR) → line extract → SKU match |
| PDF (typed) | pypdf text extract → line parser → SKU match (scanned PDFs fall back to mock OCR) |
| Excel / CSV | openpyxl/stdlib csv with header detection → direct import |
| Plain text | regex line extraction (`土豆50斤`, `tomato 5kg`, …) → SKU match |
| Repeat phrasing | (future) lookup last confirmed order for customer |

SKU matching cascade: `alias_exact` (1.0) → `catalog_exact` (0.95) → `fuzzy ≥0.85` (0.8) → `unmatched` (0.3). Every intake retains the **original file** (immutable), the **raw AI output** (immutable), and the **confirmed order** (editable, audited). Low-confidence lines are flagged in the admin; ops corrects before confirm.

## Automation defaults

| Process | Default |
|---|---|
| AI parsing | automatic on upload |
| Order confirm | auto if confidence ≥ 95% + all lines matched + before 18:00 cutoff; else manual |
| Consolidation | manual `POST /consolidation/run` at cutoff |
| PO generation | automatic after consolidation |
| PO send | manual approve (auto-send once stable) |
| Pick list | automatic on inbound complete |
| Invoice | automatic on delivery confirmed (`delivery.completed` event) |
| Shortages / substitutions | manual exception |

## Cross-module triggers (in-process events)

- `order.draft_created` → orders module runs **auto-confirm** evaluation
- `delivery.completed` → finance module **auto-generates the invoice**

Swappable for Celery/Redis later without touching module logic.

## License

Proprietary — internal build.
