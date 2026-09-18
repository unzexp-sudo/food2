# Why the OCR is not reading the images — analysis and the automation path

Date: 2026-09-18. Status of the code: fixed (`6144971`). Status of the deployment: **needs two
config changes**, listed at the end.

## The short answer

**The OCR is not broken. It was never switched on.**

There is no bug in the OCR plumbing. The attachment reaches the ERP, is written to disk
correctly, and is handed to the extractor. The extractor is a **mock provider**, which does not
read images at all — it returns three canned lines. The live ERP has been reporting this about
itself on `/api/health` the whole time.

And even once real OCR is switched on, an image will still not become an order on its own. That
is a separate, deliberate gate. So there are **two blockers**, with two different fixes.

## The evidence

### 1. The live health endpoint says it

    GET https://food2-production-7c23.up.railway.app/api/health

    "ai_provider": "mock",
    "image_extraction_is_simulated": true

`image_extraction_is_simulated` exists precisely to report this, and it reads `true`.

### 2. The stored extraction says it

    GET /api/v1/intake/extractions/{job_id}    (both WeCom image jobs)

Both images produced **byte-identical** line sets:

    土豆 50斤 / 大白菜 30斤 / 五花肉 20斤

with `parser_notes: "image OCR not available in mock provider"` and
`ocr_overall_confidence: null`.

Those three lines are verbatim `mock_ocr_lines()`'s `default_lines`
(`backend/app/ai/adapters.py:323`). **Two different customer photos produced the same three
products.** No real reader can do that — this is the conclusive tell.

### 3. The plumbing is genuinely fine

- The gateway hands off the bytes inline (`file_b64`), because `file_path` and `file_url` both
  fail silently across separate containers.
- `_store_original()` writes them under `files_dir` and returns a real path, which is stored on
  both the document and the job — so `file_path` is populated for the extractor.
- The ERP's `file_hash` for the image is **byte-identical** to the gateway's sha256, so the bytes
  on disk are correct and complete.

Nothing is missing except a provider that looks at pixels.

### 4. What the pipeline looks like

    gateway  →  ERP stores file  →  extractor  →  review gate  →  human confirms  →  draft order
      OK            OK              MOCK          PARKS           the only exit

Live counts confirm the shape: **19 documents = 3 text with draft orders, 14 text + 2 image
parked** (`GET /api/v1/intake/review-count` → `pending_review: 16`). The 3 that produced orders
did so because someone confirmed them in the review UI.

## Blocker 1 — the provider is a mock (config, not code)

`get_extractor()` (`adapters.py:906`) returns `MockExtractor` unless `ai_provider` is a real
provider. `AliyunQwenExtractor` is fully implemented — Aliyun `RecognizeAdvanced` for text +
confidence, then Qwen-VL for structure, QA and cancellation detection, then `apply_review_gate`.
It is selected the moment `ai_provider == "aliyun_qwen"` and all three credentials are present;
otherwise it raises rather than silently degrading.

Under `mock`, `MockExtractor` does not read an image at all: `mock_ocr_lines()` returns canned
lines regardless of the file's contents. Typed text, Excel and text-layer PDFs *are* genuinely
parsed under `mock` — only images and scanned PDFs are faked.

**Fix:** set on the ERP service, then confirm `/api/health` reads
`image_extraction_is_simulated: false`:

    ERP_AI_PROVIDER=aliyun_qwen
    ERP_ALIYUN_ACCESS_KEY_ID=...
    ERP_ALIYUN_ACCESS_KEY_SECRET=...
    ERP_QWEN_API_KEY=...

Until then, every photo yields the same three canned lines, and "the OCR is not doing its job"
will remain literally true.

## Blocker 2 — nothing auto-creates an order, by design (a decision, not a defect)

`backend/app/ai/pipeline.py:168`:

    requires_review = bool(getattr(extraction, "requires_human_review", False)) \
        or settings.intake_require_human_review

`intake_require_human_review` defaults **True** (`config.py:105`) and
`tests/test_mandatory_review.py:84` asserts that it must. When it is set, Step 8 returns early:
the job is parked in `needs_review`, no order is created, and the **only** way forward is
`POST /api/v1/intake/jobs/{id}/confirm-review`, i.e. a person.

Two further gates sit behind it, also defaulting True: `orders_require_human_confirmation`
(no order is settled without a person) and `require_delivery_confirmation` (no order is
confirmed without a person confirming where it goes).

This is the "0 assumptions" policy — *a wrong order shipping is far more costly than the few
seconds a human spends confirming* — and `apply_review_gate()` reinforces it independently:
a detected handwritten note is routed to a human **regardless of how high the confidence is**.

**So "place the order automatically" is not a missing feature. It is three rules that were
deliberately switched on.** Turning them off is a business decision, not a bug fix — see the
options below.

## The one genuine code defect — found and fixed (`6144971`)

Under `mock`, an unread image yields **plausible products for this business**, and the test suite
runs with `intake_require_human_review=False` (fixture `_legacy_auto_approve`). So the
configuration a site would choose **to automate intake** was exactly the configuration in which
fabricated lines auto-created a real order for a real customer.

`apply_review_gate()` already flags such a document, but `MockExtractor` never calls it — so the
mock's image result carried `requires_human_review=False` and sailed straight through.

Fixed by making the **producer** assert it, so it survives the setting:

- `MockExtractor.extract` sets `requires_human_review=True` whenever it fell back to canned
  lines — the image branch, **and** the `scanned_pdf_no_text → mock_ocr_lines` branch, which had
  the identical hole under a different `source_type`.
- Cleared again when a structured text payload genuinely parsed, so real readings are not parked.

`test_image_submit_mock_ocr` asserted the old, unsafe contract (job `completed` plus a draft
order built from canned lines). It now asserts the safe one: the job parks in `needs_review`,
`draft_order_id` is `None`, the payload is flagged, the canned lines remain visible so a reviewer
can see what *would* be ordered, and a human `confirm-review` is what creates the order.

**Full suite: 485 passed, 2 skipped** — identical to baseline.

## What to do, in order

1. **Set the three OCR credentials + `ERP_AI_PROVIDER=aliyun_qwen`** on the ERP service. Verify
   with `/api/health` → `image_extraction_is_simulated: false`. This is the change that makes
   OCR actually read the images.
2. **Re-send one photo** and read the extraction: `parser_notes` should now say
   `aliyun RecognizeAdvanced lines=N conf=…` and `ocr_overall_confidence` should be a number,
   not `null`. That is the proof the pixels were read.
3. **Clear the current backlog.** 16 documents are parked. The 2 images in particular hold
   *fabricated* lines — do not confirm them; retry them after step 1
   (`POST /api/v1/intake/jobs/{id}/retry`) so they are re-extracted by the real provider.
4. **Then decide the gate.** Options, in increasing order of automation:
   - *Keep everything as is.* Every document waits for a person. Safest; the queue is the cost.
   - *Keep the gate, make review cheap.* One-click / bulk confirm, and let the extractor's
     per-line `review_reasons` highlight only the weak cells so a reviewer skims rather than
     re-reads. **Recommended** — it removes the labour without removing the check.
   - *Auto-approve above a threshold.* Set `intake_require_human_review=False`. High-confidence
     OCR then creates draft orders directly. Note a handwritten note is still parked by
     `apply_review_gate`, and `orders_require_human_confirmation` still stops it becoming a
     *confirmed* order.
   - *Fully hands-off.* Also clear `orders_require_human_confirmation` and
     `require_delivery_confirmation`. This is what allows an OCR misread to ship a truck to the
     wrong address with nobody having looked. Only take this with a measured accuracy number
     from the eval set (`tests/test_handwritten_eval.py`, run with keys).

5. **Unrelated but still open, and both break the loop after the order exists:**
   - `WECOM_GATEWAY_URL` on the ERP is still `http://127.0.0.1:8100`
     (`wecom_gateway_url_is_loopback: true`) — so **no customer notification has ever left the
     ERP**, even for the 3 orders that exist. Set it to
     `https://wecom1-production-4bc1.up.railway.app`.
   - `WECOM_ORDER_GROUP_IDS` is empty, so handoffs carry `customer_id=null` and land unbound.

## Known gaps

- **Fixed since this was written** (`bbaf6cf`, WeCom1): a PNG was stored and served as `.jpg`,
  because `normalize_entry` synthesised `f"{msgid}.jpg"` for every image. `_store_media` now
  takes the name from the bytes (`sniff_media`), correcting only the extension and only for
  signatures that are unambiguous — a `.docx` is a ZIP, so `PK\x03\x04` is deliberately not
  sniffed. Tests: `WeCom1/tests/test_media_sniffing.py`.
- **No PDF has ever reached the archive** (message types: image 2 / text 18 / other 4 — zero
  `file`), so the PDF intake path, including the scanned-PDF fallback fixed in `6144971`, is
  unverified against real traffic. Send one real PDF to exercise it.
- The 2 held images currently carry **fabricated** lines. Do not confirm them — retry them after
  switching on real OCR, so they are re-extracted by the actual provider.
