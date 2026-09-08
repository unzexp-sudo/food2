# WeCom ↔ ERP Integration — Phase 2 Verification

Status: **complete and verified end-to-end** (mock mode, no WeCom credentials required)

Date: 2026-09-08
Scope: `wecom-gateway/` (port 8100) + the WeCom half of `backend/` (port 8000)
Contract of record: [`docs/WECOM_CONTRACTS.md`](./WECOM_CONTRACTS.md)

---

## 1. What was built

A **separate gateway service** (`wecom-gateway/`) that sits between WeCom and the ERP.

```
WeCom ──► Session Archive / app callback ──► Gateway ──► ERP intake pipeline
             (or the bundled simulator)        │            │
                                               │◄───────────┘
                                          order_confirmed,
                                     needs_customer_confirm, …
                                               │
                                               ▼
                                    WeCom outbound message
```

The gateway *never* parses an order. It dedupes, identifies the customer, downloads
attachments, and hands a normalised document to the ERP. Everything downstream
(parse → match → price → confirm) is existing ERP behaviour.

Delivered pieces:

| Area | Location |
|---|---|
| Ingestion, identity, archive cursor, handoff | `wecom-gateway/app/services/` |
| Callback / ingest / archive / messages / contacts / groups / send routers | `wecom-gateway/app/api/` |
| Bilingual outbound templates (en + zh) | `wecom-gateway/app/templates/messages.py` |
| WeCom API, archive-decrypt, ERP, storage adapters | `wecom-gateway/app/adapters/` |
| Full simulator (12 scenarios, no credentials needed) | `wecom-gateway/simulator/` |
| ERP-side intake endpoint + outbound notify hooks | `backend/app/api/v1/wecom_intake.py`, `backend/app/services/notify/` |
| WeCom console pages in the frontend | `frontend/src/pages/` |

---

## 2. Definition of Done — verified

| # | Requirement | Result |
|---|---|---|
| 1 | Simulator posts a text order | 12 archive entries staged → 11 messages ingested |
| 2 | Gateway dedupes | Replay of `wmMsgText0001` → `status=duplicate`, ERP document count unchanged (9) |
| 3 | Gateway binds customer | C001/C002/C003 resolved; unknown sender + plain group left `unresolved` |
| 4 | Gateway hands off | 9 handed off, 2 ignored (internal staff + ops chat) |
| 5 | ERP creates draft → auto-confirms | 7 orders created, 1 auto-confirmed |
| 6 | ERP → gateway `order_confirmed` | outbound row `status=mock`, delivered to `wmExtCanteen001` |
| 7 | Reply-to-parent linking | `wmMsgReply0001` → `parent=wmMsgText0001`, inherited customer C003 |
| 8 | Media (png/pdf/xlsx) | downloaded into the shared dir `backend/data/files/wecom` |

---

## 3. Defects found and fixed

The build passed 194 + 243 unit tests and still shipped these. They were found by
(a) auditing both services against the contract and (b) running the two services
live against each other. Every one now has a regression test.

### Critical

| Defect | Impact | Fix |
|---|---|---|
| **App-callback messages were dropped.** WeCom app callbacks send PascalCase XML (`MsgType`, `MsgId`); the ingestor read lowercase JSON keys. Unrecognised entries were classed `other` → ignored, and got a fabricated `nomsgid-<uuid4>` that broke dedupe. | **100% of messages arriving by app callback were silently lost.** | `_is_app_callback()` / `_from_app_callback()` in `app/api/callback.py` map the envelope onto the archive shape, preserving `MsgId` and converting `CreateTime` seconds → ms. |
| **ERP idempotency never matched.** `cast(document_meta["wecom"]["msgid"], String)` compiles on SQLite to `CAST(JSON_QUOTE(JSON_EXTRACT(…)))`; `JSON_QUOTE` wraps the value in literal quotes so equality never held. | **Every gateway retry created a second order.** Also broke reply→parent linking. | `.as_string()` instead of `cast(...)` in `backend/app/services/intake/wecom_intake.py`. |
| **Archive cursor advanced past failures.** The cursor moved to `max(seq)` regardless of outcome. | An ERP outage looked like a clean batch and **silently discarded every order**. | `app/services/archive.py` holds the cursor at the first failed `seq` and reports a `failed` count. `duplicate`/`ignored` remain terminal and do not block. |
| **Resolved customer bindings were never persisted.** §5 resolved a customer per message but only wrote it to the message row. §7 resolves outbound destinations by querying contacts with a matching `customer_id`. | The gateway could **take orders from a customer forever and never be able to reply** — every notification logged `skipped / NO_DESTINATION`. | New `identity.persist_binding()` writes the binding back (contact-derived evidence only, never overwriting a manual bind). Plus a `resolve_destination` fallback to the last inbound sender for that customer. |

### Important

| Defect | Impact | Fix |
|---|---|---|
| `is_staff` never persisted → the DB half of `is_internal_sender` was dead code. | Internal staff chatter became real customer orders. | Passed through `upsert_contact`; messages from staff are ignored. |
| Attachment captions discarded (`请按PDF下单`). | Customer instructions lost. | Caption preserved; only voice has no usable text. |
| `parse_failed` notified customers about jobs that never came from WeCom. | Customers messaged about UI uploads with no chat to reply into. | Skip when `document_meta.wecom.msgid` is absent. |
| `.env.example` shipped a blank `WECOM_ERP_API_KEY`. | Every handoff 401s on a fresh install. | Defaults to `dev-service-key`, documented as must-match the ERP. |
| Simulator used customer codes/phones absent from the ERP seed. | Demo never resolves a customer. | Now `C001/C002/C003` and `13800000001–3`. |

### Customer-facing message rendering

| Defect | Impact | Fix |
|---|---|---|
| Template read `name`/`unit`; the ERP emits `product_display`/`unit_code`. | **Every line rendered as `- x 0.0`** — a confirmation with no products in it. | Key lists extended; `raw_text` used as a last resort so an unmatched line still shows what the customer wrote. |
| Quantity rendered as `20.0`. | Reads wrong to a customer. | Whole floats render as `20`; `20.5` unchanged. |
| Unit code shown (`jin`). | Machine code in a human message. | Locale-aware display name (`斤` / `jin`), falling back to the code. |
| Unpriced order sent `total: 0.0`. | Told the customer their order was free. | `None` → renders `-`. |
| **`delivered` reported the ordered quantity, not the delivered one.** A delivery line carries both `quantity` and `delivered_quantity`; the renderer read `quantity`. | **A partial delivery was confirmed to the customer as complete** — the single worst message to get wrong. | `delivered_quantity` now wins when present. |
| A delivered line of 0 was hidden. | "We still owe you this" became a blank. | `0` is treated as a real answer for delivered quantities (but stays hidden for order lines, where 0 means "the parser found nothing"). |
| `delivered` had no unit display name; `invoice_ready` could send `total: 0.0`. | Machine codes in human text; a zero-value invoice. | Locale-aware unit display; `None` for a missing invoice total. |
| **CORS only allowed `http://localhost:5173`.** The WeCom console calls the gateway straight from the browser (it is not behind the Vite `/api` proxy), and the frontend's own gateway client defaults to `127.0.0.1`. | Opening the app on `127.0.0.1:5173` (or a fallback port) blocked every gateway call at preflight; the UI reports this only as "gateway unreachable". | Allow-list covers both loopback spellings on 5173/5174. Unknown origins are still rejected — verified live that `evil.example.com` gets no header. |
| `page_response` was copy-pasted verbatim into four routers. | A pagination fix would silently apply to one endpoint and not the others. | Extracted to `app/core/pagination.py`; all four routers import it. |

---

## 4. Live verification

Both services run for real (uvicorn, separate SQLite files) and talk to each other.

```
=== gateway outbound log ===                 (before the binding fix)
  needs_customer_confirm | skipped | NO_DESTINATION   ×7

=== gateway outbound log ===                 (after)
  needs_customer_confirm | mock | to=user:wmExtPhone003
  needs_customer_confirm | mock | to=user:wmExtCanteen001
  needs_customer_confirm | mock | to=user:wmExtHotel002
  order_confirmed        | mock | to=user:wmExtCanteen001
  ...                                                7/7 delivered
```

Contact bindings after ingest:

```
wmExtCanteen001  cust=C003  via=remark_tag  conf=1.0
wmExtHotel002    cust=C002  via=remark_tag  conf=1.0
wmExtPhone003    cust=C001  via=phone       conf=0.9
wmExtUnknown999  cust=None                        (correctly unresolved)
LiSi             staff=True  cust=None            (internal, never bound)
```

Rendered message actually sent to the customer:

```
订单 ORD-20260908-0002 已确认。

配送日期：2026-09-09
商品明细：
- 土豆 x 50斤
- 大白菜 x 30斤
- 五花肉 x 20斤
合计：-

感谢您的下单，发货后我们会第一时间通知您。
```

### Test counts

| Suite | Result |
|---|---|
| `wecom-gateway` | **245 passed**, 0 xfail |
| `backend` | **263 passed** |
| `frontend` | build clean — 3211 modules, no TS errors |

New regression tests: `wecom-gateway/tests/test_integration_fixes.py` (21 tests,
one per defect above), `backend/tests/test_notify_payload.py` (6 tests),
`wecom-gateway/tests/test_preflight.py` (7 tests) and the
`WECOM_SEND_ALLOWLIST` block in `tests/test_outbound.py` (6 tests) plus
`tests/test_config.py` (2 tests).

The last two `xfail` markers were removed when the gaps they described turned out
to be already fixed — the 16-byte callback prefix and `reply_to_msgid`
propagation. Leaving them as `xfail` would have hidden a future regression
behind a "expected to fail" that silently passes either way.

All six outbound templates were audited for the same key-mismatch class that
produced `- x 0.0`; `order_confirmed`, `needs_customer_confirm` and `delivered`
all carried it. The frontend↔gateway seam was audited too: the paginator shape
(`{items, total, page, page_size}`) matches what `useList` expects, and CORS was
verified live rather than by inspection.

### Re-verified after the live-credential work

The whole loop was re-run after the allowlist, signature and config changes,
against fresh temp databases with both services up:

```
messages: 11   contacts: 5
bound contacts:
   wmExtPhone003    -> <uuid>  (phone)
   wmExtHotel002    -> <uuid>  (remark_tag)
   wmExtCanteen001  -> <uuid>  (remark_tag)
dedupe:  re-post identical entry -> duplicate
outbound: order_confirmed  mock  -> user:wmExtPhone003
  | 订单 ORD-E2E-1 已确认。
  | 配送日期：2026-09-09
  | 商品明细：
  | - 土豆 x 50斤
  | 合计：125.0
```

Staff and ops-chat messages were ignored, the other nine were handed off, and
destination resolution returned a real `user:` target — the path that was 100%
broken before `persist_binding`.

> Testing note: `/wecom/send` takes the ERP customer **UUID**, not the customer
> code (`C001`). Sending the code yields `skipped / NO_DESTINATION`, which looks
> like the old bug but is not.

### Paginator verified live across all four routers

```
messages page_size=2, page=1     -> items= 2 total=11 page=1 page_size=2
messages page_size=2, page=2     -> items= 2 total=11 page=2 page_size=2
messages page_size=9999          -> items=11 total=11 page=1 page_size=500  (clamped)
contacts page_size=2             -> items= 2 total= 5
outbound page_size=2             -> items= 2 total= 7
groups   page_size=2             -> items= 2 total= 2
```

---

## 5. Running it

```bash
# ERP
cd backend && ERP_SERVICE_KEY=dev-service-key \
  ERP_WECOM_GATEWAY_URL=http://127.0.0.1:8100 \
  ERP_WECOM_GATEWAY_KEY=dev-gateway-key \
  python -m uvicorn app.main:app --port 8000

# Gateway
cd wecom-gateway && WECOM_ERP_API_KEY=dev-service-key \
  python -m uvicorn app.main:app --port 8100

# Stage demo traffic and pull it
cd wecom-gateway && python -m simulator.producer all
curl -X POST http://127.0.0.1:8100/wecom/archive/callback -d '{}'
```

`WECOM_MODE=mock` (the default) mocks **WeCom only** — the mock archive, the mock
WeCom API and the outbox. It deliberately does **not** mock the ERP: the whole point
of mock mode is to exercise the real gateway→ERP path without WeCom credentials.

---

## 6. Inbound callback signature — bug found and fixed

While waiting on the IP allow-list, the inbound path was exercised against a
request built to WeCom's published crypto spec
(`developer.work.weixin.qq.com/document/path/90968`) instead of against our own
assumptions. That found a defect that would have broken **100% of inbound
orders**:

```
dev_msg_signature = sha1(sort(token, timestamp, nonce, msg_encrypt))
```

The implementation hashed only `token, timestamp, nonce` — **three** values.
The encrypted payload is part of the signature. The failure mode is the worst
kind: a three-value hash is self-consistent, so it verifies fine against any
request you build yourself, and is rejected by every real WeCom callback. The
console would also have refused the callback URL at save time with no useful
error. Nothing in the unit suite could catch it, because the tests were built
the same wrong way.

Fixed in `app/core/callback_crypto.py`:

- `make_signature(timestamp, nonce, encrypt, token)` — the four-value hash.
- `verify_signature(..., encrypt=...)` — the payload is now required.
- `GET /wecom/callback` passes `encrypt=echostr`; `POST /wecom/callback`
  unwraps `<Encrypt>` from the body *before* verifying (the signature covers
  it, so it has to be read first), then decrypts.
- `decrypt()` now bounds-checks `msg_len` and compares the trailing
  `receiveid` against `WECOM_CORP_ID`. A mismatch is **logged, not rejected**:
  an attacker would already need our EncodingAESKey to produce a decodable
  ciphertext, so the security gain is negligible while the cost of being wrong
  about live traffic is total inbound failure.

Two independent proofs that the four-value scheme is correct:

1. `test_make_signature_matches_the_official_worked_example` — reproduces
   WeCom's own published token/timestamp/nonce/ciphertext and asserts the
   signature is `477715d11cdb4164915debcba66cb864d751f3e6`.
2. `scripts/callback_smoke.py` — starts the gateway in live mode with a
   throwaway token/EncodingAESKey on a scratch port and temp DB, then performs
   the real three-way handshake: URL verification echo, an encrypted+signed
   text message, and two rejected requests (tampered signature, signature for a
   different payload). Exits non-zero on any failure.

Both new guards were verified **not to be vacuous**: reverting `verify_signature`
to the three-value hash makes
`test_live_url_verification_rejects_the_three_value_signature` and
`test_verify_signature_rejects_the_three_value_hash` fail.

```bash
python scripts/callback_smoke.py
```

Also corrected: `MockWeComApi` had no way to observe what it was handed, so a
gate could only be checked via its return status. It now records every message
in `self.sent`.

---

## 7. Live credential status

Real credentials were supplied on 2026-09-08 and validated against the live API.

| Check | Endpoint | Result |
|---|---|---|
| corp ID + secret valid | `GET /cgi-bin/gettoken` | **PASS** — `errcode=0`, token issued, 7200 s |
| business APIs reachable | `GET /cgi-bin/agent/get` | **FAIL** — `errcode=60020` |

`60020` is `not allow to access from your ip`. WeCom issues access tokens from
any address but only lets **allow-listed** addresses call business APIs. The
caller's public IP at the time of the check was **14.212.114.52**.

**This is a console setting, not a code defect — nothing in the repository can
work around it.** To clear it:

1. WeCom admin → 我的企业 → 企业信息 → **可信IP** (or the app's own
   企业可信IP setting, if it overrides the corp list).
2. Add `14.212.114.52` (or the current public IP — re-check if on a
   consumer line, it rotates).
3. Re-run the preflight:

```bash
cd wecom-gateway && python scripts/preflight.py
```

It exits `0` only when `agent/get` succeeds. Until then, **stay in
`WECOM_MODE=mock`** — a live send would fail with `60020` on every message.

### Still to be supplied

| Variable | Needed for | Status |
|---|---|---|
| `WECOM_TOKEN` | inbound callbacks (app callback URL) | empty |
| `WECOM_ENCODING_AES_KEY` | inbound callbacks (app callback URL) | empty |
| `WECOM_ARCHIVE_PRIVATE_KEY_PATH` | session-archive decryption | empty |

`WECOM_TOKEN` / `WECOM_ENCODING_AES_KEY` come from the app's 接收消息
configuration page and are **not** the same as the Secret. Outbound sending can
go live without them; inbound message receipt cannot.

### Blast-radius control for the first live send

`WECOM_SEND_ALLOWLIST` (comma-separated userids / chat_ids, empty by default).
When non-empty, a **live** send to any other destination is refused and logged
as `status=blocked` — it never reaches WeCom. Mock mode is exempt, so the
end-to-end mock flow is unaffected.

Destination resolution is a five-step cascade (§5/§7) running against contact
data we have never seen from a real corp. If it resolves to the wrong person,
the first thing that customer ever receives from us is a wrong order
confirmation. Recommended first-live sequence:

1. Set `WECOM_SEND_ALLOWLIST=<your own WeCom userid>`.
2. `WECOM_MODE=live`, trigger one notification.
3. Check `GET /wecom/outbound` — expect one row, `status=sent`, your userid.
4. Prove destination resolution on real data, then clear the allowlist.

Refusals are visible in the console: `blocked` renders as a red **Blocked** /
**已拦截** tag.

---

## 8. Before production

Nothing here needs WeCom credentials, but these do:

1. **WeCom credentials** — `WECOM_CORP_ID`, `WECOM_SECRET`, `WECOM_AGENT_ID`,
   `WECOM_TOKEN`, `WECOM_ENCODING_AES_KEY`.
2. **Session Archive** — `WECOM_ARCHIVE_ENABLED=true` plus the RSA private key, and
   either the official C SDK (via the ctypes adapter already wired in) or the
   pure-Python path. Set `WECOM_ARCHIVE_SDK=python|c-sdk`.
3. **Change both service keys together** — `ERP_SERVICE_KEY` and
   `WECOM_ERP_API_KEY` must match, as must `ERP_WECOM_GATEWAY_KEY` and
   `WECOM_GATEWAY_API_KEY`. A mismatch 401s every handoff.
4. **Media storage** — currently a shared local directory
   (`backend/data/files/wecom`). Move to S3/MinIO before scaling out.
5. **Review the auto-bind cascade** in production for the first week: `phone` binds
   at 0.9 confidence and will bind any contact sharing a phone number with a
   customer contact. All auto-bindings are recorded with `bind_method` and
   `bind_confidence` so they can be audited and corrected from the WeCom console.
