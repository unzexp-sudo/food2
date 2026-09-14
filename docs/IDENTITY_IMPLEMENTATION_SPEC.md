# Implementation spec — conversation binding, delivery readiness, company pre-fill

Read `docs/CUSTOMER_MATCHING_PLAN.md` first for the *why*. This file is the
*what*: the contracts that let several agents work in parallel without
colliding.

## 0. Non-negotiables

- **Nothing auto-binds.** Every binding traces back to a recorded human
  decision.
- **Nothing is confirmed without a human.** Not a customer, not an address, not
  a delivery.
- **Extraction only proposes.** It never writes to `customers`.
- **Do not `git commit` or `git push`.** The orchestrator commits.
- **Do not edit files you do not own** (§5). Note the need in your report
  instead.
- Run tests with the sandbox bypass if the sandbox blocks writes. Note:
  `pytest tmp_path` fixture fails in this sandbox (EEXIST on basetemp) — use
  `tempfile.TemporaryDirectory()`.

---

## 1. Shared data contract

### 1.1 New table `customer_identities` (Agent 1 owns)

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | follow `UUIDMixin` |
| `customer_id` | String(36) FK customers.id, indexed | |
| `kind` | String(30) | `wecom_external_userid` \| `wecom_chat_id` |
| `value` | String(200) | the stable WeCom identifier |
| `status` | String(20) | `proposed` \| `confirmed` \| `rejected` |
| `confirmed_by` | String(36) FK users.id, nullable | |
| `confirmed_at` | DateTime nullable | |
| `evidence` | JSON | snapshot: contact name, alias, corp name, phone, msgid, document_id |
| `created_at` / `updated_at` | | `TimestampMixin` |

**Partial unique index (the whole point):**
`UNIQUE (kind, value) WHERE status = 'confirmed'`

SQLite and Postgres both support partial indexes. In SQLAlchemy use
`Index("uq_identity_confirmed", "kind", "value", unique=True,
sqlite_where=..., postgresql_where=...)` or equivalent — and **write a test
that proves inserting a second confirmed identity for the same
`(kind, value)` fails.**

### 1.2 Additive columns (Agent 1 owns)

On `customers`:
- `address_confirmed_at` DateTime nullable
- `address_confirmed_by` String(36) nullable
- `address_source` String(30) nullable — `manual` \| `pdf_extract` \| `ocr_extract` \| `wecom`

On `orders`:
- `delivery_address` Text nullable
- `delivery_contact_name` String(100) nullable
- `delivery_contact_phone` String(50) nullable
- `delivery_confirmed_at` DateTime nullable
- `delivery_confirmed_by` String(36) nullable

`init_db()` applies additive columns via ALTER TABLE (this pattern already
exists in `app/core/database.py` — follow it so existing DBs pick columns up
without a migration tool).

### 1.3 Company draft (Agent 2 owns — this is the interface Agent 1 consumes)

```python
# backend/app/ai/company_extract.py
@dataclass
class FieldDraft:
    value: str | None
    confidence: float          # 0.0–1.0
    evidence: str | None       # the exact source line that produced it
    method: str                # regex_name | regex_address | regex_phone
                               # | regex_contact | tax_id | none

@dataclass
class CompanyDraft:
    name: FieldDraft
    address: FieldDraft
    phone: FieldDraft
    contact: FieldDraft
    tax_id: FieldDraft
    source_kind: str           # pdf_text | ocr_text | plain_text
    raw_excerpt: str           # first 300 chars of the text used

def extract_company_info(text: str, *, source_kind: str = "plain_text") -> CompanyDraft
def pdf_text(file_path: str) -> str
```

Rules: **deterministic regex only.** If a field is not found, return
`FieldDraft(None, 0.0, None, "none")` — never guess, never fuzzy. Useful
anchors for Chinese purchase docs: `统一社会信用代码` (18-char
`[0-9A-HJ-NPQRTUWXY]{18}`), `名称|客户名称|收货单位|单位名称|购货单位`,
`地址|收货地址|送货地址|详细地址`, `电话|联系电话|手机|联系方式`,
`联系人`. Values are stripped of the label and surrounding punctuation.
Take the **first** match per field; record the matched line as evidence.

Agent 1 imports this module; Agent 2 creates it. Do not edit each other's file.

---

## 2. Behaviour (Agent 1)

### 2.1 Resolve on ingest
In `services/intake/wecom_intake.py:ingest_wecom_message`, after the document
exists:
1. Build the chat key: prefer `external_userid`, else `chat_id`.
2. Look up a **confirmed** identity. If found → set `doc.customer_id`, record
   `document_meta["identity"] = {"status": "bound", "identity_id": ...,
   "method": ..., "confirmed_by": ...}`.
3. If not found → `document_meta["identity"] = {"status": "unbound",
   "chat_key": ..., "kind": ...}` and **do not** let it proceed to an order
   (see 2.3).

### 2.2 Company proposal
After text is available (raw text / PDF text / OCR output), call
`extract_company_info` and store the result — as a proposal only — at
`document_meta["company_proposal"]`. Never write to `customers`.

### 2.3 Gates — the hard part
- `confirm_intake_review` **must refuse** (raise `ValueError`) while
  `document_meta["identity"]["status"] == "unbound"`. No path from unbound to
  an order without a bind.
- `POST /orders/{id}/confirm` **must refuse** while
  `delivery_confirmed_at is None` and `settings.require_delivery_confirmation`
  (default **True**). Pre-fill `delivery_address` from
  `Customer.address` when it exists — but pre-filling is not confirming.
- Binding a chat releases everything held for that chat key.

### 2.4 Endpoints (Agent 1 owns)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/v1/identity/unbound` | — | `{items: [{chat_key, kind, value, display_name, corp_name, waiting_count, last_message_at}], total}` |
| POST | `/api/v1/identity/bind` | `{kind, value, customer_id}` | `{identity_id, released: n}` |
| GET | `/api/v1/identity/bindings` | `customer_id?` | `[{id, kind, value, status, confirmed_by, confirmed_at}]` |
| POST | `/api/v1/identity/{identity_id}/unbind` | `{reason}` | `{affected_orders: [order_id]}` |
| GET | `/api/v1/intake/documents/{id}/company-proposal` | — | `CompanyDraft` JSON or `null` |
| POST | `/api/v1/customers/{id}/verify-address` | `{address, contact_name?, contact_phone?, delivery_zone?}` | customer; sets `address_confirmed_at/by` |
| POST | `/api/v1/orders/{id}/confirm-delivery` | `{delivery_address, contact_name?, contact_phone?}` | order; sets `delivery_confirmed_at/by` |

All new endpoints require roles `ops` or `admin` (follow
`require_roles("ops", "admin")` used elsewhere).

### 2.5 Tests Agent 1 must write
- a second confirmed identity for the same `(kind, value)` **cannot** be
  inserted (commit must raise);
- an unbound document cannot be confirmed into an order;
- after binding, held documents are released and carry the customer;
- an order cannot be confirmed without `delivery_confirmed_at`;
- unbinding returns the affected order list.

---

## 3. Frontend (Agent 3)

New files only under `frontend/src/pages/identity/` and
`frontend/src/components/identity/`, plus i18n keys and route/nav registration.

1. **Unbound chats queue** — one row per chat: display name, corp name, waiting
   count, last message, and a "Bind" action opening the bind screen.
2. **Bind screen** — two columns:
   - left: the WeCom identity (name, alias, corp, phone, external_userid,
     first seen, recent message samples, the order image or text);
   - right: a searchable customer picker (code / name / phone / zone); on
     selection show code, name, zone, status and **the last 3 orders**, plus the
     company proposal from `GET /intake/documents/{id}/company-proposal` when
     present.
   - A "create new customer" branch that pre-fills from the proposal, with each
     pre-filled field marked as *extracted, unverified* and showing its
     evidence line. Nothing saves until the human submits.
3. **Delivery confirmation** — on the order review/confirm screen, show
   `delivery_address` (pre-filled from the customer, clearly marked), and
   require an explicit confirm action before the order can be confirmed.

Requirements: `npx tsc -b --force` must exit 0. Follow existing patterns in
`frontend/src/pages/intake/IntakePage.tsx`. Add i18n keys to
`frontend/src/i18n/extra.ts` and `extraZh.ts` under a new `pages.identity`
namespace. Register the route in `App.tsx` and the nav item in
`AdminLayout.tsx` (you are the only agent touching the frontend, so these are
yours).

---

## 4. Agent 2 scope

`backend/app/ai/company_extract.py` + `backend/tests/test_company_extract.py`.
Nothing else. Pure functions, no DB, no HTTP. Include tests for: a PDF-ish text
block with all fields, a block with only a name, a block with nothing
(every field `method == "none"`), and label/punctuation stripping.

---

## 5. File ownership — do not cross these lines

| Agent | Owns | Must not touch |
|---|---|---|
| 1 | `backend/app/models/identity.py`, `backend/app/services/identity/*`, `backend/app/api/v1/identity.py`, edits to `models/__init__.py`, `models/customer.py`, `models/order.py`, `core/config.py`, `services/intake/wecom_intake.py`, `services/intake/service.py`, `services/orders/orders.py`, `api/v1/orders.py`, `api/v1/intake.py`, `tests/test_identity*`, `tests/test_delivery_gate*` | `app/ai/company_extract.py`, anything in `frontend/` |
| 2 | `backend/app/ai/company_extract.py`, `backend/tests/test_company_extract.py` | everything else |
| 3 | `frontend/src/pages/identity/*`, `frontend/src/components/identity/*`, `frontend/src/i18n/extra.ts`, `frontend/src/i18n/extraZh.ts`, `frontend/src/App.tsx`, `frontend/src/layouts/AdminLayout.tsx` | everything in `backend/` |

---

## 6. Definition of done

- New backend tests pass **and** the existing suite still passes
  (baseline: 370 passed, 2 skipped).
- `npx tsc -b --force` exit 0.
- No agent has committed anything.
- Each agent reports: files changed, tests added, any contract it had to
  change, anything it could not finish.
