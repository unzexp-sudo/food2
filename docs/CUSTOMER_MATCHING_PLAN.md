# Customer Matching Plan — WeChat / WeCom conversation → ERP customer

> **Status: PROPOSAL v2** (supersedes the earlier version, which wrongly assumed
> each line in a document could belong to a different customer).
>
> Governing rule: **automation and speed must never become false assumptions.**
> The asymmetry that drives every decision below: *an unbound chat is a small
> delay; a mis-bound chat is goods and an invoice going to the wrong account.*
>
> **Corrected model (from the user, 2026-09-14):**
> - A WeChat / WeCom conversation is **always** associated with one customer.
> - Every order in that chat or group is for that same customer.
> - Different lines / breakdown labels (e.g. `（夜用台）`, `（日班）`) are
>   **sub-locations or counters within the same customer**, not different
>   accounts, and not different legal entities.

---

## 1. What this means for the data we already parse

`ai/structured_parser.py` extracts `customer_name` + `customer_code` per
breakdown row. Under the corrected model those are **drop-point labels inside
one account**, not accounts.

- Carry them through to the order line as a **delivery note / drop point**
  (they are genuinely useful — the picker needs to know night counter vs day
  shift).
- **Never use them to resolve or override the customer.** The account comes
  from the conversation, full stop.

This removes the single largest risk from v1 of this plan (fuzzy-matching
`佛山市政府（夜用台）` onto the wrong account). That risk is now structurally
impossible rather than merely guarded.

---

## 2. Why the first bind must be human — and why that is correct, not a cop-out

There is **no shared key** between a WeCom conversation and an ERP customer
until a human creates one:

| WeCom gives us | Exists in ERP? |
|---|---|
| `external_userid` | no |
| `chat_id` | no |
| display name / alias / remark (user-editable, changes freely) | no |
| corp name (free text) | no |

So the first order from any chat cannot be attributed automatically by any
honest mechanism. Anything that "works" without a human is guessing.

**This is a one-time cost per chat, not a per-order tax.** The design goal is to
make that one decision take about ten seconds and never need repeating.

---

## 3. The flow you described, mapped onto what actually exists

```
order arrives from an unbound chat
  → human picks the customer
  → verified
  → every later order from that chat auto-binds
```

| Step | Exists today? | Where |
|---|---|---|
| 1. Order arrives, chat unknown → resolve fails | ✅ yes | `WeCom1/app/services/identity.py:resolve_customer` returns `(None, None, None)` |
| 2. Gateway hands off with `customer_id=null` | ✅ yes | `handoff.py:33`; gateway even replies "无法识别客户…请手动绑定" (`ingestor.py:249`) |
| 3. ERP records it without a customer | ✅ yes | `ingest_wecom_message` drops to unresolved rather than losing the message |
| 4. **Unbound orders are held, not processed** | ❌ **missing** | see §5 — today they flow on and can become an order with no account |
| 5. **A screen where a human binds the chat** | ❌ **missing** | gateway has the API (`POST /wecom/contacts/{id}/bind`, `PATCH /wecom/contacts/{id}`) but no UI; ERP review has no customer picker |
| 6. Binding is remembered next time | ⚠️ **partial** | `resolve_customer` step 1 returns a pre-bound `customer_id` — but only if the binding was written to the **gateway's** table. A bind made inside the ERP never reaches it. |
| 7. Audit / reversal | ❌ missing | no record of who bound what, or what they saw |

So: **the pipeline is ~70% built. The missing 30% is exactly the part that makes
it safe** — holding unbound orders, the bind screen, and the round trip that
makes the binding stick.

---

## 4. Where the binding should live: in the ERP, not the gateway

Recommendation: a `customer_identities` table **in the ERP**, and the gateway
degrades to a pure forwarder of `external_userid` / `chat_id`.

| Column | Purpose |
|---|---|
| `customer_id` | FK to `customers` |
| `kind` | `wecom_external_userid` \| `wecom_chat_id` |
| `value` | the stable WeCom identifier |
| `status` | `proposed` \| `confirmed` \| `rejected` |
| `confirmed_by` / `confirmed_at` | who accepted it, when |
| `evidence` | JSON snapshot: contact name, alias, corp name, phone, msgid, document_id — what the human actually saw |
| `first_seen_at` / `last_seen_at` | |

**Invariant, enforced by the database:**

```sql
UNIQUE (kind, value) WHERE status = 'confirmed'
```

Many chats may point at one customer (a buyer and their colleague). One chat
may **never** point at two customers. The database enforces it; the application
is not trusted to.

### Why the ERP and not the gateway

The gateway already has `wecom_contacts.customer_id`, but:

- the ERP owns `customers` — a second copy of the truth in another service
  cannot be validated and will drift;
- the gateway would need a write-back path from the ERP for every bind made in
  the UI, which is one more thing to fail silently;
- **durability (see below).**

### Durability — resolved with a hard boot guard (2026-09-14)

Both services default to SQLite **inside the container**:
`WeCom1` → `sqlite:///<repo>/data/wecom.db`, `food2` → `sqlite:///<repo>/erp.db`,
and neither `railway.toml` declares a volume. A container filesystem is
ephemeral, so without Postgres a redeploy deletes every order, every customer
and every conversation binding.

Rather than rely on someone remembering to set an environment variable, **both
apps now refuse to start** when they detect a deployed container
(`RAILWAY_ENVIRONMENT`, `RAILWAY_PROJECT_ID`, `DYNO`, or
`ENVIRONMENT`/`APP_ENV=production`) *and* the database URL is SQLite:

```
REFUSING TO START: the database is SQLite inside an ephemeral container
filesystem.
Fix: add the Postgres service to this Railway project and set
    ERP_DATABASE_URL=${{Postgres.DATABASE_URL}}
```

- Escape hatch for a deliberate throwaway environment:
  `ERP_ALLOW_EPHEMERAL_DATABASE=true` / `WECOM_ALLOW_EPHEMERAL_DATABASE=true`.
- Local runs and the test suite are unaffected — nothing sets those markers.
- Tests: `backend/tests/test_durable_database.py`,
  `WeCom1/tests/test_durable_database.py`.

Failing to boot is a bad afternoon. Silently losing the order book is a bad
year, and it is the kind of loss nobody notices until the goods have already
gone somewhere.

**Still open (not covered by this guard):** `ERP_FILES_DIR` and the gateway's
`data/wecom` media directory are also on the ephemeral filesystem, so stored
order images and attachments are lost on redeploy even with Postgres. Those
need a Railway volume or object storage — see §9 open questions.

---

## 5. Cold start: unbound orders must be held, not processed

Today an unbound message becomes a normal intake document with
`customer_id = null` and can be carried all the way to a draft order. That is
the hole.

New behaviour:

1. On ingest, if no confirmed identity exists for `external_userid` / `chat_id`,
   the document is marked **`unbound`** and lands in a dedicated
   **Unbound chats** queue.
2. It is **not** parsed into an order and **cannot** be confirmed. There is no
   code path from `unbound` to `confirmed` that does not pass through a bind.
3. The queue groups by conversation: "Chat X — 4 messages waiting — never
   bound". One row per chat, not per message.
4. Binding the chat **re-processes every held document** for that conversation
   automatically, now with a known customer.

Net effect: **an order cannot exist in the system without an account attached.**

---

## 6. The bind screen — the one decision that matters

Bind in the context of the first real order, not as an abstract dropdown. The
human should be looking at the actual order, the actual chat, and the actual
customer at the same moment.

**Left — who is talking (from WeCom):**
- contact name / alias / remark, corp name
- phone if known
- `external_userid` (small, for support)
- first seen date, message count
- the order image or text, and 2–3 recent messages

**Right — who you are binding to (from the ERP):**
- searchable picker by code / name / phone / delivery zone
- on selection, show: code, name, zone, status, **last 3 orders and their
  products** — a sanity check in both directions

**Rules:**
- Bind on `external_userid` / `chat_id` only. **Never** on display name — it is
  user-editable and changes.
- Optional **two-person check** (`CUSTOMER_BIND_REQUIRES_APPROVAL=true`):
  someone binds, someone else confirms. Recommended for the first weeks. A
  second pair of eyes on a bind is far cheaper than one wrong delivery.
- **Reject** must be a first-class action ("this is a new customer — create
  one" / "this is not a customer, it's a supplier"), so the same unknown chat
  doesn't get re-proposed forever.
- Every bind writes `confirmed_by`, `confirmed_at`, and the evidence snapshot.

**What is deliberately not there:** no "best guess" pre-selection, no auto-bind
on name similarity. Suggestions may be *shown* (top 3 by name/phone similarity,
clearly labelled as suggestions) but never pre-filled, and never auto-committed.

---

## 7. Zero-error rules (the ones that survive contact with reality)

1. An order cannot be confirmed while its chat is unbound.
2. One conversation maps to exactly one customer — enforced by a unique index,
   not by application code.
3. Nothing is ever auto-bound by inference. Every auto-bind traces back to a
   recorded human decision.
4. Bindings are made on stable identifiers only.
5. A bind is reversible, and reversing produces a **list of every order that
   used it** for re-checking. It never silently rewrites history.
6. Bindings apply to **future** documents and to held documents on release.
   Confirmed orders are never retroactively re-attributed.
7. Every resolution is visible on the intake record: "bound to X by Y on
   date" — never invisible, never taken for granted.

---

## 8. Rollout

| Phase | Behaviour |
|---|---|
| 0 — observe | Log which chats would arrive unbound. No behaviour change. Produces the list of chats you need to bind before launch. |
| 1 — hold | Unbound documents are held in the queue. Bind screen live. Nothing auto-processes yet. |
| 2 — auto | Confirmed identities auto-bind. Held documents release on bind. |
| 3 — optional | Two-person approval turned off once you trust the customer list, if ever. |

Phase 0 costs nothing and tells you exactly how many chats you are dealing
with. Do it before writing any of the rest.

---

## 9. Open questions

1. **Is the channel a 1:1 contact or a group chat?** Determines whether we bind
   on `external_userid` or `chat_id` (or both — a group whose members change is
   better bound on `chat_id`).
2. **Is `external_userid` stable per external contact across your whole corp**,
   or per staff member? If it is per-staff, the same customer messaging two of
   your people yields two IDs and two binds. Harmless (many→one is allowed) but
   worth knowing. Verify against two real contacts.
3. Do you want the two-person bind approval on by default?
4. When a chat is bound, should held documents auto-release, or wait for a
   human to press go?
5. Are `WECOM_DATABASE_URL` / `ERP_DATABASE_URL` set to Postgres in Railway
   today? (§4)

---

## 10. Build order

1. Confirm §9.5 (database durability) — if this is wrong, nothing else matters.
2. `customer_identities` table + unique index + tests proving a second
   conflicting bind **cannot** be inserted.
3. Resolve-on-ingest using the ERP ledger (gateway keeps forwarding IDs).
4. Unbound hold queue + release-on-bind.
5. Bind screen with evidence on both sides.
6. Blocking gate: no confirmation without a bound customer.
