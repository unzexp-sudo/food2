"""ERP side of the WeCom handoff (docs/WECOM_CONTRACTS.md §6).

The Gateway does zero parsing — it posts a normalized message here and the
existing AI intake pipeline takes over from that point on.

Incoming WeCom messages are recorded as ordinary IntakeDocuments so every
downstream screen (intake inbox, lineage, audit) works unchanged. The WeCom
provenance lives in `document_meta["wecom"]`:

    {
      "msgid", "external_userid", "chat_id", "sender_userid",
      "msgtype", "received_at", "reply_to_msgid",
      "parent_document_id", "parent_job_id"
    }
"""
from __future__ import annotations

import base64
import binascii
import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import log_audit
from app.core.config import settings
from app.models import Customer, IntakeDocument, IntakeJob, User
from app.services.identity import resolve_for_document
from app.services.intake.service import submit_intake
from app.services.intake.triage import classify_message

# msgtype -> ERP source_type fallback
logger = logging.getLogger("erp.intake.wecom")

_MSGTYPE_SOURCE = {
    "text": "text",
    "voice": "text",
    "image": "image",
    "file": "pdf",
    "mixed": "text",
    "other": "text",
}

_EXT_SOURCE = {
    ".pdf": "pdf",
    ".xlsx": "excel",
    ".xls": "excel",
    ".csv": "excel",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".bmp": "image",
    ".webp": "image",
}


def _msgid_expr():
    """SQL expression for document_meta['wecom']['msgid'] (SQLite + Postgres).

    MUST be `.as_string()`, not `cast(..., String)`. On SQLite the cast form
    compiles to `CAST(JSON_QUOTE(JSON_EXTRACT(...)) AS VARCHAR)` — `JSON_QUOTE`
    wraps the extracted value in literal double quotes, so the comparison is
    `"MSG1" = 'MSG1'` and never matches. Postgres behaves the same way. The
    result was that the idempotency anchor silently never matched, so a
    gateway retry created a SECOND order for the same WeCom message, and
    reply-to-parent linking never resolved.
    """
    return IntakeDocument.document_meta["wecom"]["msgid"].as_string()


def find_document_by_msgid(db: Session, msgid: str) -> IntakeDocument | None:
    """The idempotency anchor: one ERP document per WeCom msgid."""
    if not msgid:
        return None
    return (
        db.query(IntakeDocument)
        .filter(_msgid_expr() == msgid)
        .order_by(IntakeDocument.created_at)
        .first()
    )


def source_type_for(msgtype: str | None, filename: str | None, content: str | None) -> str:
    """Resolve the ERP source_type from msgtype + filename."""
    if filename:
        ext = Path(filename).suffix.lower()
        if ext in _EXT_SOURCE:
            return _EXT_SOURCE[ext]
    if msgtype == "text" and content:
        return "text"
    return _MSGTYPE_SOURCE.get(msgtype or "", "text")


def _download(url: str) -> bytes | None:
    # trust_env=False — the sandbox proxy cannot reach loopback services.
    try:
        with httpx.Client(trust_env=False, timeout=30.0) as c:
            r = c.get(url)
        if r.status_code == 200:
            return r.content
    except httpx.HTTPError:
        return None
    return None


def _decode_inline(file_b64: str | None) -> bytes | None:
    """Decode the Gateway's inline attachment, or None when it is unusable.

    Returns None rather than raising: a malformed inline field must degrade to the
    URL fallback, never fail the intake. The message is still an order.
    """
    if not file_b64:
        return None
    try:
        return base64.b64decode(file_b64, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("Inline attachment is not valid base64 — falling back to the URL")
        return None


def resolve_attachment(
    *,
    file_path: str | None,
    file_url: str | None,
    file_b64: str | None = None,
) -> tuple[bytes | None, str | None, str | None]:
    """Return (bytes, filename, mime) for a WeCom attachment, or (None, None, None).

    Order: **inline bytes → on-disk path → file_url.**

    Inline first, and not as a nicety. The Gateway is a separate service with its
    own filesystem, so the `file_path` branch below is dead in production — the
    path names a file in the Gateway's container and `exists()` is always False,
    silently. `file_url` then depends on `WECOM_MEDIA_URL_BASE` naming the
    Gateway's *public* origin, and its default is the Gateway's own loopback.
    Only the inline field has no external dependency.

    The filename still comes from the URL or path, because those are the only
    places the original extension survives — and the extension is what decides
    whether this is a PDF to parse or a photo to look at.
    """
    name = (Path(file_url).name if file_url else "") or (
        Path(file_path).name if file_path else ""
    )
    name = name or "attachment"

    data = _decode_inline(file_b64)
    if data is not None:
        return data, name, mimetypes.guess_type(name)[0]

    if file_path:
        p = Path(file_path)
        if p.exists() and p.is_file():
            return p.read_bytes(), p.name, mimetypes.guess_type(p.name)[0]
    if file_url:
        fetched = _download(file_url)
        if fetched is not None:
            return fetched, name, mimetypes.guess_type(name)[0]
    return None, None, None


def _meta_of(doc: IntakeDocument) -> dict:
    return dict(doc.document_meta or {})


def _set_meta(db: Session, doc: IntakeDocument, extra: dict) -> None:
    meta = _meta_of(doc)
    meta.update(extra)
    doc.document_meta = meta
    flag_modified(doc, "document_meta")
    db.flush()


def _resolve_identity(db: Session, doc: IntakeDocument, payload: dict[str, Any]) -> dict:
    """§2.1 — resolve the conversation to a customer, or hold the document.

    A WeCom chat has no key in common with an ERP customer until a human makes
    one, so: a confirmed binding wins; otherwise an explicit customer asserted
    upstream is carried (it is an upstream human decision, not an inference);
    otherwise the document is `unbound` and cannot become an order.
    """
    block = resolve_for_document(
        db,
        doc,
        external_userid=payload.get("external_userid"),
        chat_id=payload.get("chat_id"),
        upstream_customer_id=payload.get("customer_id"),
    )
    if block.get("status") == "bound" and block.get("customer_id"):
        # An ERP binding outranks the gateway: the ERP owns the ledger and
        # "one conversation, one customer" is enforced here.
        doc.customer_id = block["customer_id"]
        db.flush()
    # What the bind screen shows the human. Never used to resolve anything —
    # a display name is user-editable and is not evidence of identity.
    block["display_name"] = (
        payload.get("display_name")
        or payload.get("contact_name")
        or payload.get("name")
    )
    block["corp_name"] = payload.get("corp_name")
    block["alias"] = payload.get("contact_alias")
    return block


def ingest_wecom_message(
    db: Session,
    payload: dict[str, Any],
    *,
    actor: User | None = None,
    background_tasks: BackgroundTasks | None = None,
) -> tuple[IntakeDocument, IntakeJob, bool]:
    """Create (or reuse) the intake document + job for one WeCom message.

    Returns (document, job, duplicate). Idempotent on `msgid`.
    """
    msgid = (payload.get("msgid") or "").strip()
    if not msgid:
        raise ValueError("msgid is required")

    existing = find_document_by_msgid(db, msgid)
    if existing is not None:
        job = (
            db.query(IntakeJob)
            .filter(IntakeJob.document_id == existing.id)
            .order_by(IntakeJob.retry_count.desc(), IntakeJob.created_at.desc())
            .first()
        )
        return existing, job, True

    customer_id = payload.get("customer_id") or None
    if customer_id is not None:
        if db.get(Customer, customer_id) is None:
            # Never lose the message over a stale binding — drop to unresolved.
            customer_id = None

    msgtype = payload.get("msgtype") or "text"
    content = payload.get("content") or None
    file_bytes, filename, mime = resolve_attachment(
        file_path=payload.get("file_path"),
        file_url=payload.get("file_url"),
        file_b64=payload.get("file_b64"),
    )

    if filename is None and payload.get("file_mime"):
        mime = payload["file_mime"]

    source_type = payload.get("source_type") or source_type_for(msgtype, filename, content)

    if file_bytes is None and not content:
        # Voice-only or an attachment we could not fetch — still record it so
        # nothing silently disappears; the pipeline will flag it as failed.
        content = payload.get("content") or "[WeCom attachment could not be retrieved]"

    # --- Gate 1: is this an order at all? ----------------------------------
    # In "shadow" mode this only records the verdict — every message still
    # becomes an intake document exactly as before, so the team can inspect
    # what triage *would* have parked before anything is actually hidden.
    # "Declared" = the message said it had a file, even if we could not fetch
    # it. Triage must treat that as an attachment: otherwise a download failure
    # would leave us with only placeholder text, get classified as chatter and
    # be parked — losing a real order because our fetch broke.
    declared_attachment = bool(
        payload.get("file_url") or payload.get("file_path") or payload.get("file_mime")
    ) or msgtype in ("image", "file", "video", "mixed")

    verdict = classify_message(
        text=content,
        msgtype=msgtype,
        has_attachment=file_bytes is not None or declared_attachment,
        filename=filename,
    )
    mode = (settings.intake_triage_mode or "shadow").lower()
    logger.info(
        "triage msgid=%s decision=%s score=%s tier=%s mode=%s reasons=%s",
        msgid, verdict.decision, verdict.score, verdict.tier, mode, verdict.reasons,
    )
    # Only Tier 0 verdicts are ever parked. Tier 0 is deterministic — empty,
    # emoji-only, or a message that is nothing but a greeting / ack / system
    # event — so the odds of a real order hiding in there are negligible.
    # Tier 1 "not_order" is a heuristic score (a bare product name with no
    # quantity scores 0) and stays ingested: dropping a real order costs far
    # more than one extra row in the inbox, which is the same asymmetry that
    # made "unclear" count as an order in the first place.
    should_park = (
        mode == "enforce"
        and verdict.decision == "not_order"
        and verdict.tier == "tier0"
    )
    if should_park:
        logger.info(
            "triage PARKED msgid=%s tier=%s reasons=%s", msgid, verdict.tier, verdict.reasons
        )

    doc, job = submit_intake(
        db,
        customer_id=customer_id,
        delivery_date=None,
        source_type=source_type,
        raw_text=content if file_bytes is None else None,
        file_bytes=file_bytes,
        filename=filename,
        actor=actor,
        background_tasks=background_tasks,
        parked=should_park,
    )

    wecom_block: dict[str, Any] = {
        "msgid": msgid,
        "external_userid": payload.get("external_userid"),
        "chat_id": payload.get("chat_id"),
        "sender_userid": payload.get("sender_userid"),
        "msgtype": msgtype,
        "received_at": payload.get("received_at"),
        "reply_to_msgid": payload.get("reply_to_msgid"),
        "file_url": payload.get("file_url"),
        # Gate 1 verdict, so shadow-mode decisions can be audited before
        # enforcement is switched on. The excerpt makes the report readable
        # without re-opening every document.
        "triage": {
            **verdict.as_dict(),
            "excerpt": (content or "")[:120],
            # Lets the report answer "what did we actually park?" rather than
            # only "what would we have parked?".
            "parked": should_park,
        },
    }

    reply_to = payload.get("reply_to_msgid")
    if reply_to:
        parent = find_document_by_msgid(db, reply_to)
        if parent is not None:
            wecom_block["parent_document_id"] = parent.id
            parent_job = (
                db.query(IntakeJob)
                .filter(IntakeJob.document_id == parent.id)
                .order_by(IntakeJob.retry_count.desc(), IntakeJob.created_at.desc())
                .first()
            )
            wecom_block["parent_job_id"] = parent_job.id if parent_job else None
            # Re-use the parent's customer so a reply lands in the right context.
            if customer_id is None and parent.customer_id:
                doc.customer_id = parent.customer_id
                db.flush()

    # Resolve AFTER the parent-inheritance above, so `doc.customer_id` is final
    # and a reply that inherited its parent's customer is not held for nothing.
    identity_block = _resolve_identity(db, doc, payload)

    _set_meta(db, doc, {"wecom": wecom_block, "identity": identity_block})

    if identity_block.get("status") == "unbound":
        logger.info(
            "identity UNBOUND msgid=%s chat_key=%s — document held, it cannot "
            "become an order until a human binds the conversation",
            msgid, identity_block.get("chat_key"),
        )

    log_audit(
        db,
        actor,
        "IntakeDocument",
        doc.id,
        "wecom_ingest",
        before=None,
        after={"msgid": msgid, "msgtype": msgtype, "customer_id": doc.customer_id},
        summary=(
            f"WeCom message {msgid} parked by triage (not an order) from "
            if should_park
            else f"WeCom message {msgid} ingested from "
        )
        + f"{payload.get('external_userid') or payload.get('chat_id') or 'unknown'}",
    )

    return doc, job, False


def list_wecom_documents(
    db: Session,
    *,
    customer_id: str | None = None,
) -> tuple[list[IntakeDocument], int]:
    """All WeCom-sourced intake documents, newest first."""
    q = db.query(IntakeDocument)
    if customer_id:
        q = q.filter(IntakeDocument.customer_id == customer_id)
    docs = q.order_by(IntakeDocument.created_at.desc()).all()
    docs = [d for d in docs if isinstance(d.document_meta or {}, dict) and "wecom" in (d.document_meta or {})]
    return docs, len(docs)
