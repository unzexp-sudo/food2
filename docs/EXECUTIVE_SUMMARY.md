# Executive Summary: Food Delivery ERP — Build Plan

## Purpose

Build a B2B food supply ERP from scratch. Customers (schools, restaurants, government canteens) send orders in **any format** — text, handwritten notes, photos, PDFs, spreadsheets. An **AI intake layer** extracts structured order data; the ERP handles confirmation, procurement merge, warehouse, delivery, and billing with full traceability.

WeCom and other channels are **intake sources only**. This document covers the ERP core.

---

## Core Business Loop

```
Order Intake (any format)
  → AI Parse & Normalize
  → Pending Order Pool
  → Confirm & Consolidate
  → Generate Purchase Orders (wholesaler + category)
  → Inbound Receipt
  → Pick & Deliver
  → Reconcile & Invoice
```

Every step links back to the original customer order line.

---

## System Modules

| Module | Responsibility |
|--------|----------------|
| **Intake & AI Parser** | Accept files/text; OCR + document understanding; output structured draft orders |
| **Customer & Contract** | Customer profiles, contract prices, standing order templates, buyer aliases |
| **Order Management** | Pending pool, confirm/reject, consolidation batches, order lifecycle |
| **Procurement** | Merge confirmed demand into POs by wholesaler + category; supplier rules |
| **Warehouse** | Inbound against PO, inventory ledger, pick lists, loss/shrinkage |
| **Delivery** | Sort by customer order, routes, proof of delivery |
| **Finance** | Customer AR, wholesaler AP, invoicing, payment status |
| **Master Data** | Products, categories, units, wholesaler catalog mapping, SKU aliases |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     INTAKE LAYER                        │
│  Upload API │ OCR │ PDF Parser │ LLM Extractor │ Queue  │
└──────────────────────────┬──────────────────────────────┘
                           │ structured draft orders
┌──────────────────────────▼──────────────────────────────┐
│                      ERP CORE                           │
│  Orders │ Procurement │ Warehouse │ Delivery │ Finance  │
└──────────────────────────┬──────────────────────────────┘
                           │
                    PostgreSQL + Redis + Job Worker
```

**Stack (implemented):**
- Backend: Python (FastAPI), modular monolith
- Database: PostgreSQL in production; SQLite for dev/demo (same SQLAlchemy models)
- Background jobs: FastAPI BackgroundTasks behind a job abstraction (upgrade path to Celery/Redis)
- File storage: local disk behind a storage abstraction (upgrade path to S3)
- AI: pluggable adapter interface — Mock provider (deterministic, for dev/demo) + OpenAI-compatible provider
- Admin UI: React + Vite + TypeScript + Ant Design 5, bilingual EN/中文

Start as a **modular monolith**. Split services only when volume demands it.

---

## The Lineage Model (non-negotiable)

All fulfillment is tracked at **order line** level:

```
customer_order_line
  → consolidation_batch_line
    → purchase_order_line
      → inbound_receipt_line
        → pick_line
          → delivery_line
            → invoice_line
```

One customer order can split across multiple POs (by wholesaler/category). One PO can serve many customers. Lineage IDs make both directions queryable.

---

## Phase 1 — Foundation

- Project scaffold, auth, RBAC (admin, ops, warehouse, finance, driver)
- PostgreSQL schema: customers, products, contracts, wholesalers
- Product catalog with units, categories, customer-specific aliases
- Basic admin UI: customer CRUD, product CRUD, contract pricing

## Phase 2 — AI Intake Layer

### Intake API
```
POST /intake/submit
  - customer_id (or match later)
  - source_type: text | image | pdf | excel | email_body
  - file(s) or raw_text
  - delivery_date (optional)
```

Store original file + metadata. Enqueue for processing.

### Processing pipeline (async job)

| Step | Action |
|------|--------|
| 1. Classify | Document type: handwritten note, typed list, invoice-style PDF, spreadsheet |
| 2. Extract | OCR for images/PDFs; table extraction for PDFs/Excel; LLM for messy text |
| 3. Normalize | `{ product_name, quantity, unit, notes }` per line |
| 4. Match SKUs | Fuzzy match against catalog + customer aliases |
| 5. Score | Confidence per line and per order (0–100) |
| 6. Draft | Create `draft_order` + `draft_order_lines` in pending pool |

### AI output schema (every intake produces this)

```json
{
  "customer_id": "uuid | null",
  "delivery_date": "2026-09-08",
  "lines": [
    {
      "raw_text": "土豆50斤",
      "matched_product_id": "uuid",
      "matched_product_name": "Potato",
      "quantity": 50,
      "unit": "jin",
      "confidence": 0.94,
      "match_method": "alias_exact"
    }
  ],
  "overall_confidence": 0.91,
  "parser_notes": "Handwritten note, 3 lines detected"
}
```

### Intake storage
- `intake_documents` — original file, hash, source, upload time
- `intake_jobs` — processing status, errors, retry count
- `intake_extractions` — raw AI output (audit trail)

## Phase 3 — Order Management

### Order states
```
draft → pending_confirmation → confirmed → consolidated → fulfilled → invoiced
         ↘ rejected
         ↘ needs_clarification
```

### Pending order pool (admin screen)
- List draft/pending orders with source document thumbnail
- Side-by-side: **original document | AI extraction | editable fields**
- Ops actions: Confirm, Edit lines, Reject, Request clarification
- Audit log: who confirmed what, when

### Auto-confirm rules (configurable)
Auto-confirm when:
- Overall confidence ≥ threshold (e.g. 95%)
- All lines matched to catalog SKUs
- Quantities within normal range for customer
- Prices match contract
- Before cutoff time

Otherwise → stays in pending pool.

### Standing orders
- Per-customer templates (weekly recurring lists)
- "Reorder template" creates draft without AI

## Phase 4 — Consolidation & Procurement

### Consolidation
- Scheduled cutoff job (e.g. daily 18:00) gathers all **confirmed** orders for next delivery window
- Creates `consolidation_batch` linking all order lines

### PO merge engine
- Input: confirmed order lines
- Group by: **wholesaler_id + product_category**
- Output: `purchase_order` + `purchase_order_lines` with aggregated quantities
- Retain links: each PO line → source customer order lines

### Supplier rules (config table)
- Default wholesaler per category
- Product → wholesaler mapping
- MOQ, lead time, split rules when multiple suppliers available
- Manual override in admin before PO send

### PO lifecycle
```
draft → sent → partially_received → received → closed
```

## Phase 5 — Warehouse

- Inbound receipt against PO (scan or manual entry)
- Quantity discrepancy flags (ordered vs received)
- Inventory ledger (optional for cross-dock; required for stored goods)
- Pick list generation: split inbound stock back to customer order lines
- Loss/shrinkage recording

## Phase 6 — Delivery

- Delivery runs grouped by route/zone
- Driver assignment
- Pick confirmation (scan or checklist)
- Proof of delivery: photo, signature, timestamp, GPS
- Status: picked → out_for_delivery → delivered → failed/partial

On delivery confirmation → trigger billing eligibility.

## Phase 7 — Finance

- Customer statements (AR) from **delivered** quantities, not ordered
- Wholesaler statements (AP) from inbound vs wholesaler invoice
- Three-way match: PO ↔ inbound ↔ supplier invoice
- Invoice generation and payment status
- Margin view: sell price vs landed cost per order/customer/category

---

## Database — Core Tables

```
customers, customer_contacts, customer_product_aliases
products, product_categories, units
contract_prices, standing_order_templates

wholesalers, product_wholesaler_mapping, supplier_rules

intake_documents, intake_jobs, intake_extractions

orders, order_lines
consolidation_batches, consolidation_batch_lines

purchase_orders, purchase_order_lines
inbound_receipts, inbound_receipt_lines

pick_lists, pick_lines
deliveries, delivery_lines, proof_of_delivery

invoices, invoice_lines
payments

audit_logs
```

All transactional tables include: `created_at`, `updated_at`, `created_by`, `status`.

---

## AI Intake — Processing Rules

| Input | Pipeline |
|-------|----------|
| **Handwritten photo** | Image preprocess → OCR → LLM line extraction → SKU match |
| **PDF (typed)** | PDF text extract; if scanned, OCR first → table/line parser → SKU match |
| **PDF (multi-page invoice-style)** | Layout analysis → line items → SKU match |
| **Excel / CSV** | Column mapping template per customer → direct import |
| **Plain text** | LLM extraction → SKU match |
| **Repeat phrasing** ("same as last week") | Lookup last confirmed order for customer → pre-fill draft |

Every intake retains:
- Original file (immutable)
- Raw AI output (immutable)
- Final confirmed order (editable, audited)

Low-confidence lines are highlighted in admin; ops corrects before confirm. Corrections feed alias/matching improvements over time.

---

## API Surface (ERP only)

```
# Intake
POST   /intake/submit
GET    /intake/jobs/:id
GET    /intake/documents/:id

# Orders
GET    /orders?status=pending_confirmation
GET    /orders/:id
PATCH  /orders/:id/lines
POST   /orders/:id/confirm
POST   /orders/:id/reject

# Consolidation & Procurement
POST   /consolidation/run          (cutoff trigger)
GET    /purchase-orders
POST   /purchase-orders/:id/send

# Warehouse
POST   /inbound
GET    /pick-lists?date=

# Delivery
POST   /deliveries/:id/complete

# Finance
GET    /statements/customer/:id
POST   /invoices/generate

# Master data
CRUD   /customers, /products, /wholesalers, /contracts
```

---

## Admin UI — Priority Screens

1. **Intake inbox** — uploaded documents, processing status, failures
2. **Pending order pool** — original doc + parsed data side-by-side, confirm/edit
3. **Order detail** — full lineage trace
4. **Consolidation dashboard** — today's cutoff, order count, exceptions
5. **Purchase orders** — merge result, edit before send
6. **Inbound & pick lists** — warehouse ops
7. **Delivery board** — routes, status, POD
8. **Finance** — AR/AP, margin, invoices
9. **Master data** — customers, catalog, contracts, supplier rules

---

## Automation Defaults

| Process | Default |
|---------|---------|
| AI parsing | Automatic on upload |
| Order confirm | Auto if confidence + rules pass; else manual |
| Consolidation | Scheduled at cutoff |
| PO generation | Automatic after consolidation |
| PO send | Manual approve first; auto-send once stable |
| Pick list | Automatic on inbound complete |
| Invoice | Automatic on delivery confirmed |
| Substitutions / shortages | Manual exception |

---

## Definition of Done (MVP)

A user can:
1. Upload a handwritten note photo or PDF for a customer
2. See AI-extracted order lines with confidence scores
3. Confirm or edit the order
4. Run cutoff → system generates wholesaler POs
5. Receive goods → pick lists split by customer
6. Mark delivered → customer statement generated
7. Trace any order line from intake document through to invoice

That is the ERP. Channel integrations (WeCom, mini-program, email) plug into `POST /intake/submit` later without changing core logic.
