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

import mimetypes
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.audit import log_audit
from app.models import Customer, IntakeDocument, IntakeJob, User
from app.services.intake.service import submit_intake

# msgtype -> ERP source_type fallback
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


def resolve_attachment(
    *,
    file_path: str | None,
    file_url: str | None,
) -> tuple[bytes | None, str | None, str | None]:
    """Return (bytes, filename, mime) for a WeCom attachment, or (None, None, None).

    Prefers the on-disk path (the Gateway writes into the shared files dir);
    falls back to fetching file_url.
    """
    if file_path:
        p = Path(file_path)
        if p.exists() and p.is_file():
            return p.read_bytes(), p.name, mimetypes.guess_type(p.name)[0]
    if file_url:
        data = _download(file_url)
        if data is not None:
            name = Path(file_url).name or "attachment"
            return data, name, mimetypes.guess_type(name)[0]
    return None, None, None


def _meta_of(doc: IntakeDocument) -> dict:
    return dict(doc.document_meta or {})


def _set_meta(db: Session, doc: IntakeDocument, extra: dict) -> None:
    meta = _meta_of(doc)
    meta.update(extra)
    doc.document_meta = meta
    flag_modified(doc, "document_meta")
    db.flush()


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
    )

    if filename is None and payload.get("file_mime"):
        mime = payload["file_mime"]

    source_type = payload.get("source_type") or source_type_for(msgtype, filename, content)

    if file_bytes is None and not content:
        # Voice-only or an attachment we could not fetch — still record it so
        # nothing silently disappears; the pipeline will flag it as failed.
        content = payload.get("content") or "[WeCom attachment could not be retrieved]"

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

    _set_meta(db, doc, {"wecom": wecom_block})

    log_audit(
        db,
        actor,
        "IntakeDocument",
        doc.id,
        "wecom_ingest",
        before=None,
        after={"msgid": msgid, "msgtype": msgtype, "customer_id": doc.customer_id},
        summary=(
            f"WeCom message {msgid} ingested from "
            f"{payload.get('external_userid') or payload.get('chat_id') or 'unknown'}"
        ),
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
