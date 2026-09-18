# Intake → binding: closing the dead end

**Phase 2 UX plan — integrating the binding flow that Phase 1 already built.**

Status: **S0 and S2 are implemented** (`f6181d6`, `57b7bc0`); S1 was done as the
mechanical means to S2. **S3 and S4 are not.** This document is kept as the record of the
diagnosis and the reasoning, not as an open proposal.

Companion to `IDENTITY_IMPLEMENTATION_SPEC.md` (Phase 1, 2026-09-14), which designed and
shipped the binding *capability*. This plan covers the *integration* into the intake flow,
plus four defects found while analysing it.

> Verification note: S0/S2/S3 are verified by the backend suite (468 passed, 2 skipped) and
> `npx tsc -b --force` (exit 0). The frontend has **no test runner**, so the end-to-end
> flow in §5 has **not** been clicked through in a browser — that remains to be done.

---

## 0. Diagnosis — the capability exists; the integration does not

The screenshot shows the whole problem in one frame: **17 orders waiting for review**, a
red banner reading *"This conversation is not bound to a customer yet — bind it first
(chat_key=…)"*, and **no control anywhere that performs a bind**. The instruction is
present; the affordance is missing.

Phase 1 deliberately built binding as **standalone pages**:

```
UnboundChatsPage  →  BindChatPage  →  POST /identity/bind  →  release_held()
   (queue)            (?kind=&value=&chat_key=)                (re-points held docs)
```

That flow is complete and correct. What was never built is the path *from the intake
inbox into it*. The operator is told to bind, and then has to guess that a separate
"Identity" item in the WeCom sidebar group is the answer.

### Three verified defects

**D1 — The Customer column can never show a name, even once bound.**
`IntakePage.tsx:360` renders:

```tsx
r.customer_id ? pickName(lang, r.customer_name_en, r.customer_name_zh) : "—"
```

but `_doc_out` (`backend/app/services/intake/service.py:67-82`) returns **only**
`customer_id` — never `customer_name_en` / `customer_name_zh`. So `pickName` receives two
`undefined`s. Fixing binding alone would still leave this column blank.

**D2 — Confirm is gated by role, not by binding.**
`IntakeReviewDrawer.tsx:167` is `disabled={!canConfirm}`, and `IntakePage.tsx:705` passes
`canConfirm={canMutate}` — a role check. An unbound order therefore presents a **fully
enabled, confident-looking primary button** whose only purpose is to fail. The business
rule is correct and must stay; it is enforced at the wrong layer for UX.

**D3 — The failure is a transient toast, and the drawer contradicts it.**
`handleConfirm` (`IntakeReviewDrawer.tsx:92-103`) goes through `useMutate`, which on
failure calls `message.error(getApiError(err))` — an AntD toast at the top of the screen.
The drawer's own `<Alert type="error">` (line 177) is bound to the *load* error only, so
it stays silent. The result: a red flash, while the drawer continues to display *"No order
exists yet. Compare the lines below against the original, then confirm."* with an enabled
Confirm button. Click, flash, click, flash.

### The precedent that proves the fix is cheap

`POST /orders/{id}/confirm` refuses while `delivery_confirmed_at is None`
(`IDENTITY_IMPLEMENTATION_SPEC.md` §2.3) — **structurally the same gate** as the binding
one. That gate *was* integrated: `OrderDetailPage.tsx:520` renders `DeliveryConfirmPanel`
inline, with the pre-filled-but-unverified values, a warning `Alert`, and an explicit
confirm. It is a good component and it works.

Binding is the one gate that never got this treatment. The pattern to copy is already in
the codebase.

---

## 1. Principles

**P1 — Never a dead end.** Every blocked action offers the action that unblocks it, at
the point of blocking. No instruction without a control next to it.

**P2 — A blocking business rule is persistent and inline, never a toast.** A toast is for
something that happened. A rule that prevents something must stay on screen, with its own
button. (Contrast: D3.)

**P3 — Show the gap, pre-fill it, ask only for what is missing.** Extracted values are
displayed and tagged *extracted — not verified*; the human is asked only for the fields
that are actually absent. `CreateCustomerFromProposal.tsx` already does exactly this — its
`extractedLabel()` / `humanLabel()` distinction is the house style.

**P4 — Bind the conversation once, and say what that releases.** Binding is per-chat, not
per-order. `release_held` re-points every held document for that chat key. So the UI must
say *"this will release 17 held orders"* — turning 17 chores into one decision. This is the
single biggest UX win available here.

**P5 — One decision per surface.** The inbox answers "what needs attention?". The review
drawer answers "is this draft right?". The binding pop-up answers "who is this?". Do not
merge them; do link them.

**P6 — Imitate what is already here.** `DeliveryConfirmPanel`, `CustomerPicker`,
`CreateCustomerFromProposal`, and `IntakeReviewDrawer` between them already contain every
pattern this plan needs. Build no new primitives.

---

## 2. Target flow

### 2.1 Inbox — make the blocked state visible and clickable

Today an unbound row shows `—` in the Customer column: a dead cell that looks like missing
data rather than a task.

- Replace `—` with a **`Needs customer`** tag that is itself a button, opening the binding
  pop-up for that row's conversation. The column becomes the affordance.
- Add an **`Unbound only`** filter and make the banner specific:
  *"17 waiting for review · 17 need a customer"* — because "17 waiting" hides that none of
  them *can* proceed.
- Once bound, the same cell shows the customer name (requires **D1** fixed).

### 2.2 Review drawer — prevent the error instead of reporting it

The drawer is where intent forms, so this is where the gate belongs.

- Add a **binding status strip** above the draft:
  - bound → green, *"Bound to 广州第X中学"*;
  - unbound → warning, *"Not bound to a customer — this order can't be created yet"*, with
    a primary **`Choose customer`** button.
- **Change what Confirm does when unbound.** Do not disable it into a dead end (P1).
  Relabel it *`Bind customer to continue`* and have it open the binding pop-up. On success,
  **resume the original confirm automatically** — the operator asked to create the order,
  so finish that job rather than making them click again. (P4 + P1.)
- Move the confirm failure out of the toast and into the drawer's `Alert` (P2, **D3**).

### 2.3 Binding pop-up — one decision, two branches

A controlled `Drawer` (width 840, matching `IntakeReviewDrawer`) titled
**"Which customer is this order for?"**. Controlled props only — no routing, no
`useSearchParams` (see §3, S1).

**Context header** (so the operator knows who they are binding): contact display name,
corp name, the WeCom chat id, *"releases N held orders"*, and a preview of the original
note — the same evidence `ChatEvidencePanel` already renders.

**Branch A — existing customer (default).**
`CustomerPicker` with server search. Before the list, a **suggestions** block: resolve any
`[CUST:…]` tag or phone from the evidence via `GET /intake/wecom/lookup-customer`, and show
the hit first with its reason — *"matched by remark tag CUST:C003"*. Showing *why* a
customer is suggested is what makes the suggestion trustworthy, and the confidence values
already exist server-side (`BIND_CONFIDENCE`: remark 1.0, phone 0.9, group 0.7).

**Branch B — new customer.**
`CreateCustomerFromProposal` pre-filled from `GET /intake/documents/{id}/company-proposal`,
each value tagged *extracted — not verified*, submit gated by the existing checkbox.

**Gap-filling (P3).** `code`, `name_en`, `name_zh` are required by the backend
(`CustomerCreate`). Today `CustomersPage.tsx` does **not** mark `name_zh` required, so the
form can submit and the user gets a raw 422. In the pop-up, any missing required field is
asked for explicitly and inline, with the reason shown. **D4** below.

**Confirmation.** `Popconfirm` before the write, as `BindChatPage` already does — binding
re-points documents, so it deserves the same "are you sure" as the existing screen.

---

## 3. Slices

Ordered so each is independently shippable and testable. S0 unblocks the rest.

### S0 — Backend contract (small, unblocks everything)
- Extend `_doc_out` to include `customer_name_en`, `customer_name_zh` (**D1**), and an
  `identity` block: `{status, chat_key, kind, value}`. Without `identity.status` the UI
  cannot tell bound from unbound at all.
- Tests: a bound document serialises its customer name; an unbound document serialises
  `identity.status == "unbound"` and a `chat_key`; the existing key set does not regress.

### S1 — `BindCustomerDrawer` (extract, don't rewrite)
- Extract the body of `BindChatPage.tsx` into
  `frontend/src/components/identity/BindCustomerDrawer.tsx` with controlled props:
  `{ open, chat: {kind, value, chat_key, display_name, …}, onClose, onBound }`.
- Replace its `useSearchParams` read and `navigate("/identity/chats")` with those props.
  This is the only real blocker to reuse — the page is otherwise already a component.
- `BindChatPage` becomes a thin wrapper passing route params, so the existing deep link and
  the sidebar badge keep working.
- `CustomerPicker`, `CompanyProposalPanel`, `CreateCustomerFromProposal` drop in unchanged.
- `npx tsc -b --force` must exit 0.

### S2 — Wire into intake (the ask)
- Inbox: `Needs customer` tag-as-button in the Customer column; `Unbound only` filter;
  specific banner counts.
- Drawer: binding status strip; Confirm → *"Bind customer to continue"* when unbound;
  resume-confirm-after-bind; confirm errors into the inline `Alert` (**D3**).
- `CustomerPicker` (read-only) or a `CustomerSummary` chip to show the bound customer.

### S3 — Show who is talking, and start the search from it — SHIPPED
- **The contact evidence is now on the inbox row.** `_doc_out`'s identity block gained
  `display_name`, `corp_name`, `alias`, so a held row reads *"Needs customer · 陈记饭店"*
  instead of an action next to a `chat_key` the operator cannot act on. A conversation
  WeCom told us nothing about says *"Sender unknown"* rather than leaving a gap.
- **The picker is pre-filled from the conversation**, and each result names the field it
  matched (*"matched on Phone"*). `CustomerPicker` takes a `hint`; the drawer passes
  `alias || corp_name || display_name`.
- **The pre-fill is a suggestion, never a selection.** `value` stays null until a click,
  the box is clearable, and an Alert says where the name came from. `searchHint` was
  reworded, because "nothing is pre-selected" would otherwise have become false.

**Correction to the first draft of this plan.** It proposed sourcing the suggestion from
the gateway, on the finding that `wecom_block` carries no `remark`/`phone`. That finding
was accurate but pointed at the wrong block: `_resolve_identity` already writes
`display_name` / `corp_name` / `alias` into `document_meta["identity"]`
(`wecom_intake.py:166-172`), and `/identity/unbound` has always returned them. **The data
was already in the ERP; only the intake serialiser dropped it, so no WeCom1 change is
needed.** Lesson: check the *identity* block, not only `wecom_block`.
- i18n keys added to **both** `extra.ts` and `extraZh.ts` (there is still no parity test).

### S4 — ERP-wide audit of the same anti-pattern
The intake gate is unlikely to be the only one. Audit every server-side refusal for the D2/D3
shape — *does the UI show an enabled action that can only fail, and report it as a toast?*
Known candidates, each needing verification:
- `POST /orders/{id}/confirm` (delivery gate) — **already integrated** via
  `DeliveryConfirmPanel`; use as the reference implementation.
- `POST /identity/{id}/unbind` → affects existing orders; is the consequence shown before
  the action?
- Customer duplicate `code` → `HTTP 400 {"detail": "Customer code already exists: …"}`
  (`customers.py:115`); is that a field-level error or a toast?
- Quotation / PO-receive validation refusals.

Deliverable: a short list of confirmed instances, each with the fix, so the principle is
applied consistently rather than one page at a time.

---

## 4. Additional defect to fix in S2

**D4 — `name_zh` is required by the API but not by the form.**
`CustomerCreate` requires `code`, `name_en`, `name_zh`. `CustomersPage.tsx` marks only
`code` (line 484) and `name_en` (line 491) as required, so the form submits and the backend
returns a 422 with no field mapping. Either mark it required in the form or make it
optional in the schema — but the two must agree. The binding pop-up must not inherit this.

---

## 5. Tests

Backend (`backend/tests/`):
- bound vs unbound `_doc_out` shape, including `identity` and the customer name;
- `confirm-review` on an unbound document still raises (the gate must **not** be weakened);
- binding releases held documents and they carry the customer (`release_held`).

Frontend: no test runner is configured (`package.json` has no test script, no vitest), so
the frontend gate is `npx tsc -b --force` plus manual verification. Adding vitest is a
separate decision and is not assumed here.

Manual acceptance — the screenshot scenario, end to end:
1. Inbox shows 17 waiting, each row offering `Needs customer`.
2. Open one → review drawer shows the draft **and** an unbound warning with
   `Choose customer`.
3. Bind to an existing customer → the drawer resumes and the order is created, with no
   manual re-click.
4. The Customer column shows the name for all 17 released rows.
5. Attempting to confirm an unbound order produces an inline message, never a bare toast.

---

## 6. Non-goals

- Weakening or removing any server-side gate. `assert_document_bound` stays exactly as it
  is; this plan only moves the *discovery* of the requirement earlier.
- Auto-binding. `identity/service.py` states plainly that **nothing auto-binds**, and the
  Phase 1 decision to keep a human in the loop stands. Suggestions are shown; the human
  decides.
- Reworking `UnboundChatsPage` / `BindChatPage`. They keep working; they become one of two
  entry points instead of the only one.
- Postgres, auth on the gateway endpoints, secret rotation — tracked separately.
