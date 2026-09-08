# WeCom ↔ ERP Integration — AGENT CONTRACT (binding)

This document is the single source of truth for the WeCom Gateway build.
**If a detail is not in this file, stop and ask — do not invent it.**

Source of business intent: `docs/EXECUTIVE_SUMMARY.md`, `docs/AGENT_CONTRACTS.md`.
Source of integration intent: the WeCom ↔ ERP Integration Plan supplied by the user.

---

## §0 Ground rules

1. **One new component:** the WeCom Gateway — a separate FastAPI service at
   `/WeCom1/`, port **8100**, own database, own process.
2. **The Gateway does NOT parse orders.** No OCR, no LLM, no SKU matching.
   The ERP intake pipeline owns all of that. The Gateway's only jobs are:
   receive → dedupe → identify customer → normalize to a file/text → hand off.
3. **No secrets are available yet.** Every credential is read from an env var
   that is currently empty/placeholder. Code must start, import, and run in
   `mock` mode with zero credentials. Never crash at import because a secret
   is missing.
4. **Mock mode is first-class.** `WECOM_MODE=mock` must let the full chain
   (WeCom in → ERP → WeCom out) run locally with no WeCom account. Real and
   mock implementations sit behind identical interfaces and are selected by
   `get_*` factories in `app/adapters/`.
5. **Bilingual.** All customer-facing outbound text has `en` and `zh` renderings.
   UI strings use the existing `en.ts` / `zh.ts` i18n files.
6. **Idempotent everywhere.** A WeCom retry/replay of the same `msgid` must never
   create a second order.

---

## §1 Repository layout & file ownership

```
WeCom1/
  requirements.txt
  .env.example
  README.md
  app/
    main.py                      [ORCH]  FastAPI app, router registration, lifespan
    core/
      config.py                  [ORCH]  Settings (env prefix WECOM_ / ERP_)
      database.py                [ORCH]  engine, SessionLocal, get_db, init_db
      logging_cfg.py             [ORCH]  simple logging setup
      callback_crypto.py         [ORCH]  WeCom callback URL verify + AES-256-CBC decrypt
      security.py                [ORCH]  service-key guard for ERP→gateway calls
    models/
      base.py                    [ORCH]  UUIDMixin / TimestampMixin (inherit Base!)
      wecom.py                   [ORCH]  5 tables (see §3)
    schemas/
      wecom.py                   [ORCH]  Pydantic v2 request/response models
    adapters/
      decrypt.py                 [ORCH]  PurePythonDecryptor | SdkDecryptor | get_decryptor
      wecom_api.py               [ORCH]  RealWeComApi | MockWeComApi | get_wecom_api
      storage.py                 [ORCH]  LocalStorage | S3Storage | get_storage
      erp_client.py              [ORCH]  HttpErpClient | MockErpClient | get_erp_client
    services/
      identity.py                [A]     contact/group upsert + auto-bind cascade
      ingestor.py                [A]     route a normalized message → storage → handoff
      archive.py                 [A]     pull loop, decrypt, dedupe, dispatch
      handoff.py                 [A]     POST ERP /api/v1/intake/wecom (+ /reply)
      outbound.py                [B]     template render + send to WeCom
    api/
      callback.py                [B]     GET/POST /wecom/callback, POST /wecom/archive/callback
      messages.py                [B]     admin CRUD/list for wecom_message_log
      contacts.py                [B]     admin CRUD + manual bind
      groups.py                  [B]     admin CRUD for groups
      send.py                    [B]     POST /wecom/send
      health.py                  [B]     GET /wecom/health
    templates/
      messages.py                [B]     6 bilingual templates
  tests/                         [D]
  simulator/                     [D]     fake WeCom producer + fixtures

backend/                          (existing ERP — minimal, additive changes)
  app/api/v1/wecom_intake.py     [ORCH]  POST /api/v1/intake/wecom, /reply, GET /wecom-messages
  app/services/notify/
    service.py                   [C]     notify() dispatcher → gateway POST /wecom/send
    wecom_notify.py              [C]     event handlers → notify()
  app/ai/pipeline.py             [C]     add emit("intake.job_failed", ...)
  app/api/v1/orders.py           [C]     add emit("order.confirmed", ...)
  app/services/finance/auto_invoice.py [C] add emit("invoice.created", ...)

frontend/
  src/pages/wecom/*              [D]     gateway console pages + i18n keys
```

**Owners:** `[ORCH]` = written by the orchestrator before agents launch (do not modify).
`[A]` `[B]` `[C]` `[D]` = agent-owned. **Never edit a file you do not own.**

---

## §2 Runtime & conventions

| Item | Value |
|---|---|
| Gateway port | `8100` |
| Gateway env prefix | `WECOM_` (plus `ERP_*` for ERP-side settings) |
| Gateway DB | SQLite `WeCom1/data/wecom.db` (dev), Postgres via `WECOM_DATABASE_URL` |
| HTTP client | `httpx` |
| Config | `pydantic-settings` `BaseSettings`, `extra="ignore"` |
| Datetime | timezone-aware UTC internally; ISO 8601 strings on the wire |
| Pagination | `{items, total, page, page_size}` (same as ERP `page_response`) |
| Errors | `{"detail": "..."}`; handlers never raise out of event/background code |
| Logging | `logging.getLogger("wecom.<module>")` |

**Sandbox note:** an HTTP proxy is set in this environment and blocks localhost.
All outbound calls must go through `httpx` configured with `trust_env=False`
(see `app/adapters/erp_client.py` and `app/adapters/wecom_api.py` for the pattern).
Never use bare `requests`/`urllib` defaults.

---

## §3 Gateway database — 5 tables (exact)

Common: `id` (String(36) PK, UUID default), `created_at`, `updated_at`
(use `app.models.base.TimestampMixin`; it already inherits `Base`).

### `wecom_contacts`
| column | type | notes |
|---|---|---|
| external_userid | String(128), unique, index, not null | WeCom external contact ID |
| name | String(200) | display name / remark |
| alias | String(200), nullable | |
| avatar_url | String(500), nullable | |
| corp_name | String(200), nullable | external corp |
| is_staff | bool, default False | True = internal employee (skip ingestion) |
| staff_userid | String(128), nullable | set when is_staff |
| customer_id | String(36), nullable, index | **ERP** customer id (logical FK, no DB constraint) |
| bind_method | String(30), nullable | `manual`\|`remark_tag`\|`phone`\|`group`\|`auto` |
| bind_confidence | float, nullable | 1.0 manual/remark, 0.9 phone, 0.7 group |
| meta | JSON, default dict | |
| last_seen_at | DateTime, nullable | |

### `wecom_groups`
| column | type | notes |
|---|---|---|
| chat_id | String(128), unique, index, not null | WeCom `roomid` / `chatid` |
| name | String(200), nullable | |
| customer_id | String(36), nullable, index | resolved ERP customer |
| member_userids | JSON, default list | |
| member_count | int, default 0 | |
| is_order_group | bool, default False | listed in `WECOM_ORDER_GROUP_IDS` |
| is_internal_ops | bool, default False | internal ops chat — never ingest as order |
| meta | JSON, default dict | |

### `wecom_message_log`
| column | type | notes |
|---|---|---|
| msgid | String(200), unique, index, not null | WeCom `msgid` — the dedupe key |
| seq | int, nullable, index | archive sequence number |
| direction | String(10), default `in` | `in`\|`out` |
| external_userid | String(128), nullable, index | |
| chat_id | String(128), nullable, index | |
| sender_userid | String(128), nullable | |
| msgtype | String(20) | `text`\|`image`\|`file`\|`voice`\|`mixed`\|`other` |
| content_text | Text, nullable | |
| file_path | String(1000), nullable | local absolute path |
| file_url | String(1000), nullable | gateway-served URL |
| file_mime | String(200), nullable | |
| source_type | String(20), nullable | ERP source_type: `text`\|`image`\|`pdf`\|`excel` |
| customer_id | String(36), nullable, index | resolved at ingest |
| bind_status | String(20), default `unresolved` | `bound`\|`unresolved` |
| status | String(20), default `received` | `received`\|`handed_off`\|`ignored`\|`failed`\|`duplicate` |
| intake_job_id | String(36), nullable | ERP job id returned by handoff |
| document_id | String(36), nullable | ERP document id |
| reply_to_msgid | String(200), nullable | for reply-loop messages |
| error | Text, nullable | |
| raw | JSON, default dict | full decrypted WeCom payload |
| received_at | DateTime, nullable | |

### `wecom_message_cursor`
| column | type | notes |
|---|---|---|
| cursor_key | String(50), unique, not null | e.g. `archive` |
| last_seq | int, default 0 | |
| last_run_at | DateTime, nullable | |

### `wecom_outbound_log`
| column | type | notes |
|---|---|---|
| template | String(50), not null | see §6 |
| to_type | String(10) | `user`\|`group` |
| to_id | String(128) | |
| customer_id | String(36), nullable, index | |
| order_id | String(36), nullable, index | |
| locale | String(5), default `zh` | |
| rendered_text | Text, nullable | |
| payload | JSON, default dict | |
| status | String(20), default `pending` | `sent`\|`mock`\|`skipped`\|`failed` |
| response | JSON, default dict | |
| error | Text, nullable | |

---

## §4 Message ingestion rules

1. Pull from Session Archive using `seq` cursor, `limit=1000`, `timeout` configurable.
2. **Dedupe on `msgid`** — if `wecom_message_log.msgid` exists, mark `duplicate` and skip.
3. **Skip internal staff** — sender in `WECOM_STAFF_USERIDS` or contact `is_staff` → `ignored`.
4. **Skip internal ops chats** — `chat_id == WECOM_INTERNAL_OPS_CHAT_ID` → `ignored`.
5. Route by `msgtype`:
   | msgtype | action |
   |---|---|
   | `text` | hand off `content_text` directly (`source_type=text`) |
   | `image` | download via `sdkfileid` → store → `source_type=image` |
   | `file` | download → map extension → `pdf`\|`excel`\|`image`\|default `pdf` |
   | `voice` | download → store; `source_type=text`, hand off with a note that transcription is not enabled yet |
   | `mixed` | concatenate text parts + download first attachment |
   | other | status `ignored`, logged |
6. Extension → source_type map:
   `.pdf → pdf`, `.xlsx/.xls/.csv → excel`, `.png/.jpg/.jpeg/.gif/.bmp/.webp → image`, else `pdf`.
7. Media is stored under `WECOM_MEDIA_DIR` (default `<repo>/backend/data/files/wecom`)
   so the ERP can read it from the same disk. `get_storage()` returns the object.
8. Store the row **before** handoff, then update with `intake_job_id` / `status`.
   Handoff failures leave `status=failed` with `error` set — never lose the message.

---

## §5 Customer identity — auto-bind cascade

Run in this order; first hit wins. Set `bind_method` + `bind_confidence`.

1. **pre-bound** — `wecom_contacts.customer_id` already set → `manual`, 1.0
2. **remark tag** — the contact's remark/alias/name contains `[CUST:<customer_code>]`
   → look up ERP `Customer.code` → `remark_tag`, 1.0
3. **phone match** — contact phone (from WeCom contact payload, if present) matches
   `Customer.contact_phone` or `CustomerContact.phone` → `phone`, 0.9
4. **group chat** — `chat_id` resolves in `wecom_groups` to a `customer_id` → `group`, 0.7
5. **unresolved** — `customer_id = None`, `bind_status = unresolved`.
   The message is **still handed off** to the ERP (the ERP accepts `customer_id: null`);
   the gateway additionally posts an internal alert to `WECOM_INTERNAL_OPS_CHAT_ID`
   (or logs it in mock mode) so staff can bind it manually.

Contact resolution order for *phone*: WeCom archive payloads do not carry a phone
number. The phone rule therefore only applies when the Gateway has already
fetched the contact profile (`externalcontact/get?external_userid=`) and it
returned a phone — in mock mode this comes from the simulator payload.

---

## §6 ERP handoff contract

### `POST {ERP_BASE_URL}/api/v1/intake/wecom`
Auth (either):
- header `X-ERP-Service-Key: <ERP_API_KEY>`, **or**
- `Authorization: Bearer <jwt>` with role `ops` or `admin`

Headers: `Idempotency-Key: <msgid>` (the gateway also sends `msgid` in the body;
either is sufficient).

```json
{
  "msgid": "wmAbCdEf123",
  "external_userid": "wmEfGh...",
  "chat_id": "wrChatId...",
  "sender_userid": "ZhangSan",
  "customer_id": "<erp customer id or null>",
  "msgtype": "text",
  "content": "明天要 20斤 土豆 ...",
  "file_url": null,
  "file_path": null,
  "file_mime": null,
  "source_type": "text",
  "received_at": "2026-09-07T16:00:00+08:00",
  "reply_to_msgid": null
}
```

Response `201`:
```json
{"document_id": "...", "job_id": "...", "customer_id": "...", "status": "queued", "duplicate": false}
```
If `msgid` was already processed → `200` with the original ids and `"duplicate": true`.

### `POST {ERP_BASE_URL}/api/v1/intake/wecom/reply`
Same body, with `reply_to_msgid` set to the original `msgid`. The ERP resolves the
parent document by `msgid` and links the reply to the same customer + order context.

### `GET /api/v1/intake/wecom-messages`
Filters: `customer_id`, `status`, `page`, `page_size`. Returns ERP intake documents
whose `document_meta.wecom` is set, newest first.

---

## §7 Outbound — `POST /wecom/send`

Request:
```json
{
  "template": "order_confirmed",
  "customer_id": "<erp customer id>",
  "external_userid": null,
  "chat_id": null,
  "order_id": "<erp order id>",
  "locale": "zh",
  "payload": { "order_number": "ORD-20260908-0001", "delivery_date": "2026-09-08", "lines": [], "total": 631.0 }
}
```

Resolution: `external_userid` → else contacts bound to `customer_id` → else
`chat_id` from that customer's order group → else `status=skipped`
(`error="no WeCom destination for customer"`).

Response:
```json
{"outbound_id": "...", "status": "sent|mock|skipped|failed", "to_type": "...", "to_id": "...", "rendered_text": "..."}
```

### Templates (each must render in `en` and `zh`)
| template | trigger | payload fields |
|---|---|---|
| `order_confirmed` | order status → `confirmed` | order_number, delivery_date, lines[], total |
| `needs_customer_confirm` | order status → `pending_confirmation` | order_number, lines[], reason |
| `parse_failed` | intake job → `failed` | msgid, error |
| `out_for_delivery` | delivery → `delivering` | order_number, driver, eta |
| `delivered` | delivery → `delivered`/`partial` | order_number, delivered_lines[] |
| `invoice_ready` | invoice created | invoice_number, order_number, total |

Transport:
- `to_type=user` → `externalcontact/message/send` (needs `WECOM_AGENT_ID`)
- `to_type=group` → `appchat/send` with `chatid`
- mock mode → append to `wecom_outbound_log` with `status=mock` and write to
  `data/outbox/*.txt` so a human can read what would have been sent.

---

## §8 Callback endpoints

| endpoint | purpose |
|---|---|
| `GET /wecom/callback` | WeCom URL verification — echo `msg_signature`, `timestamp`, `nonce`, `echostr` decrypted |
| `POST /wecom/callback` | app message callback — verify signature, decrypt, dispatch to ingestor |
| `POST /wecom/archive/callback` | `msgaudit_notify` ping → triggers an immediate archive pull |
| `GET /wecom/health` | `{status, mode, erp_reachable, archive_enabled}` |
| `GET /wecom/media/{filename}` | serve downloaded media (mock/real) |

Signature verify (app callback): sort `[token, timestamp, nonce]` → sha1 hex.
Decrypt: AES-256-CBC, key = base64decode(`EncodingAESKey` + "="), IV = key[:16],
PKCS7 unpad, then strip the 16-byte network-order length prefix and 4-byte
receiver suffix. Implement in `app/core/callback_crypto.py`.

In **mock mode** signature verification is skipped and the body is taken as
already-decrypted JSON.

---

## §9 Config keys (all `WECOM_` prefixed unless noted)

| key | default | meaning |
|---|---|---|
| `WECOM_MODE` | `mock` | `mock`\|`live` |
| `WECOM_DATABASE_URL` | sqlite `data/wecom.db` | |
| `WECOM_CORP_ID` | `""` | placeholder |
| `WECOM_AGENT_ID` | `""` | placeholder |
| `WECOM_SECRET` | `""` | placeholder |
| `WECOM_TOKEN` | `""` | app callback token |
| `WECOM_ENCODING_AES_KEY` | `""` | 43-char |
| `WECOM_ARCHIVE_PRIVATE_KEY_PATH` | `""` | PEM path |
| `WECOM_ARCHIVE_SDK_PATH` | `""` | optional C SDK |
| `WECOM_DECRYPT_PROVIDER` | `pure` | `pure`\|`sdk` |
| `WECOM_STAFF_USERIDS` | `""` | comma-separated |
| `WECOM_ORDER_GROUP_IDS` | `""` | comma-separated chat ids |
| `WECOM_INTERNAL_OPS_CHAT_ID` | `""` | |
| `WECOM_ARCHIVE_PULL_INTERVAL` | `30` | seconds |
| `WECOM_ARCHIVE_LIMIT` | `1000` | |
| `WECOM_ARCHIVE_TIMEOUT` | `5` | seconds |
| `WECOM_MEDIA_DIR` | `<repo>/backend/data/files/wecom` | |
| `WECOM_MEDIA_URL_BASE` | `http://127.0.0.1:8100/wecom/media` | |
| `WECOM_ERP_BASE_URL` | `http://127.0.0.1:8000` | |
| `WECOM_ERP_API_KEY` | `""` | placeholder |
| `WECOM_GATEWAY_SERVICE_KEY` | `dev-gateway-key` | guards `POST /wecom/send` |
| `WECOM_CORS_ORIGINS` | `http://localhost:5173` | |

ERP-side additions (`ERP_` prefix in `backend/app/core/config.py`):
| key | default |
|---|---|
| `ERP_WECOM_GATEWAY_URL` | `http://127.0.0.1:8100` |
| `ERP_WECOM_GATEWAY_KEY` | `dev-gateway-key` |
| `ERP_SERVICE_KEY` | `dev-service-key` (accepts `X-ERP-Service-Key` on intake endpoints) |
| `ERP_NOTIFY_ENABLED` | `true` |

---

## §11 Internal Python API (agents MUST implement these exact signatures)

### `app/services/identity.py` — owner [A]
```python
CUST_TAG_RE: re.Pattern                       # r"\[CUST:([^\]]+)\]"
def extract_cust_tag(*values: str | None) -> str | None
def upsert_contact(db, *, external_userid, name=None, alias=None, corp_name=None,
                   is_staff=None, staff_userid=None, meta=None) -> WeComContact
def upsert_group(db, *, chat_id, name=None, member_userids=None,
                 is_order_group=None, is_internal_ops=None) -> WeComGroup
def is_internal_sender(db, *, sender_userid=None, external_userid=None,
                       chat_id=None) -> bool
def resolve_customer(db, *, contact: WeComContact | None, group: WeComGroup | None = None,
                     phone: str | None = None,
                     erp=None) -> tuple[str | None, str | None, float | None]
    # -> (customer_id, bind_method, bind_confidence); cascade per §5
def bind_contact(db, external_userid: str, customer_id: str,
                 method: str = "manual") -> WeComContact
```

### `app/services/ingestor.py` — owner [A]
```python
EXT_SOURCE_TYPE: dict[str, str]               # ".pdf" -> "pdf", ...
def normalize_entry(entry: dict) -> dict
    # -> {"msgid","seq","msgtype","external_userid","chat_id","sender_userid",
    #     "text","sdkfileid","filename","md5","received_at","raw"}
def source_type_for(msgtype: str | None, filename: str | None) -> str
def ingest_entry(db, entry: dict, *, erp=None, api=None, storage=None) -> IngestResult
```

### `app/services/archive.py` — owner [A]
```python
def get_cursor(db, key: str = "archive") -> WeComMessageCursor
def set_cursor(db, cursor: WeComMessageCursor, last_seq: int) -> None
def pull_once(db, *, api=None, erp=None) -> dict
    # -> {"fetched": int, "ingested": int, "skipped": int, "last_seq": int}
def start_poller(interval: int | None = None) -> threading.Thread   # daemon
```

### `app/services/handoff.py` — owner [A]
```python
def build_payload(msg: WeComMessageLog) -> dict
def handoff(db, msg: WeComMessageLog, *, erp=None, as_reply: bool = False) -> dict
```

### `app/templates/messages.py` — owner [B]
```python
TEMPLATES: dict[str, dict[str, str]]   # {"order_confirmed": {"en": "...", "zh": "..."}, ...}
def render(template: str, locale: str, payload: dict) -> str
    # Missing keys must NOT raise — render them as "-" via a defaultdict formatter.
```

### `app/services/outbound.py` — owner [B]
```python
def resolve_destination(db, *, external_userid=None, customer_id=None,
                        chat_id=None) -> tuple[str | None, str | None]   # (to_type, to_id)
def send_message(db, req: SendRequest, *, api=None) -> SendResponse
def alert_ops(db, text: str, *, api=None) -> dict | None
```

### `app/api/*.py` routers — owner [B]  (all mounted in `app/main.py` already)
| file | prefix | endpoints |
|---|---|---|
| `health.py` | `/wecom` | `GET /health` |
| `callback.py` | `/wecom` | `GET /callback`, `POST /callback`, `POST /archive/callback`, `POST /ingest` |
| `messages.py` | `/wecom` | `GET /messages`, `GET /messages/{id}`, `POST /messages/{id}/rehand` |
| `contacts.py` | `/wecom` | `GET /contacts`, `PATCH /contacts/{external_userid}`, `POST /contacts/{external_userid}/bind` |
| `groups.py` | `/wecom` | `GET /groups`, `POST /groups`, `DELETE /groups/{chat_id}` |
| `send.py` | `/wecom` | `POST /send`, `GET /outbound` |

`POST /wecom/ingest` accepts `IngestRequest` (a raw decrypted archive entry) and
calls `ingest_entry` — this is the simulator's injection point.

### `backend/app/services/notify/` — owner [C]
```python
# service.py
def notify(db, *, template: str, customer_id: str | None, payload: dict,
           order_id: str | None = None, locale: str = "zh") -> dict
    # POSTs to {ERP_WECOM_GATEWAY_URL}/wecom/send with X-Gateway-Key.
    # Never raises: logs + returns {"status": "failed", ...} on error.
    # No-op (returns {"status":"disabled"}) when ERP_NOTIFY_ENABLED is false.
# wecom_notify.py — event handlers, each wrapped in try/except
@on("order.confirmed")      -> order_confirmed
@on("order.draft_created")  -> needs_customer_confirm  (only if status is still
                               "draft" or "pending_confirmation" after the
                               orders module's auto-confirm handler has run)
@on("intake.job_failed")    -> parse_failed
@on("delivery.dispatched")  -> out_for_delivery
@on("delivery.completed")   -> delivered
@on("invoice.created")      -> invoice_ready
```

### New events the ERP must emit — owner [C]
| event | where | kwargs |
|---|---|---|
| `order.confirmed` | `app/api/v1/orders.py` confirm endpoint AND `app/services/orders/auto_confirm.py` on success | `db=db, order=order` |
| `intake.job_failed` | `app/ai/pipeline.py` except block | `db=db, job=job, document=document` |
| `invoice.created` | `app/services/finance/auto_invoice.py` after the audit commit, and the manual generate invoice endpoint | `db=db, invoice=invoice` |
| `delivery.dispatched` | wherever a delivery moves to `delivering`/`in_transit` (add the emit if the transition exists) | `db=db, delivery=delivery` |

---

## §10 Definition of done

1. `cd WeCom1 && python -m pytest` — all tests pass.
2. `cd backend && python -m pytest` — the pre-existing **226** tests still pass.
3. Mock end-to-end: simulator posts a text order → gateway dedupes → binds customer
   → hands off → ERP creates draft → auto-confirms → gateway receives
   `order_confirmed` → outbound log row with `status=mock`.
4. Same for an image and a PDF (media downloaded + stored + handed off).
5. Unresolveable sender → handed off with `customer_id: null` + internal alert.
6. Internal staff message → `ignored`, no ERP call.
7. Duplicate `msgid` → `duplicate`, no second ERP call.
8. Reply loop: a reply with `reply_to_msgid` lands on the correct customer.
9. `GET /wecom/health` returns `mode=mock`, `erp_reachable=true`.
10. Frontend builds (`npm run build`, 0 TS errors) with the new WeCom pages.
