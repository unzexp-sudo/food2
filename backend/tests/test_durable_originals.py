"""An intake original has to outlive the container it arrived in.

A Railway container's filesystem is discarded on every deploy. Intake originals
used to be stored as nothing but a path inside that filesystem, so every document
uploaded before the last deploy became unreadable: the review screen rendered
"File not found on disk" where the photo should have been, and a re-parse was
impossible because the bytes no longer existed anywhere at all.

The bytes now live on the document row. These tests exist because the failure was
silent — the row still listed a filename and a hash, so the inbox looked perfectly
healthy right up until someone opened a document.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import inspect as sa_inspect

from app.core.database import SessionLocal
from app.models import IntakeDocument
from app.services.intake.service import materialize_original, submit_intake

JPEG = b"\xff\xd8\xff\xe0" + b"pretend-this-is-a-photo" * 40 + b"\xff\xd9"
TXT = "Potato 2 boxes\nCabbage 1 box".encode("utf-8")

# What a path looks like once the container that wrote it has been replaced.
DEAD_PATH = "/app/data/files/intake/gone-with-the-container.jpg"


def _submit(**kwargs) -> str:
    """Ingest an original and return its document id.

    No `background_tasks`, so the pipeline does not run: these tests are about
    storage, and a parse would only add time and a mock's canned lines.
    """
    with SessionLocal() as db:
        doc, _job = submit_intake(
            db,
            customer_id=None,
            delivery_date=None,
            **kwargs,
        )
        db.commit()
        return doc.id


@pytest.fixture(autouse=True)
def _schema(app):
    """`init_db()` + seed.

    Most tests here open a session directly instead of going through the HTTP
    client, so they do not otherwise pull in the app fixture — and without it
    the tables do not exist yet.
    """
    return app


# --- The bytes are in the row -------------------------------------------------

def test_an_uploaded_original_is_kept_on_the_row():
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        assert row.file_data == JPEG
        # The hash still describes the bytes, so a duplicate check that reads
        # `file_hash` keeps working unchanged.
        assert row.file_hash == hashlib.sha256(JPEG).hexdigest()


def test_a_typed_message_is_kept_as_its_bytes_too():
    doc_id = _submit(source_type="text", raw_text=TXT.decode("utf-8"))

    with SessionLocal() as db:
        assert db.get(IntakeDocument, doc_id).file_data == TXT


def test_listing_documents_does_not_pull_the_bytes_into_memory():
    """The inbox lists 50 documents a request; a photo is megabytes.

    `deferred=True` is the difference between rendering a list of filenames and
    loading every blob behind it, so it is pinned here rather than left to look
    like a harmless detail of the column definition.
    """
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = (
            db.query(IntakeDocument)
            .filter(IntakeDocument.id == doc_id)
            .one()
        )
        assert "file_data" in sa_inspect(row).unloaded
        # ... and it is still reachable on demand, which is what the download
        # endpoint and the extractor rely on.
        assert row.file_data == JPEG


# --- The regression: the container is replaced --------------------------------

def test_the_preview_still_serves_after_the_container_is_replaced(
    client, ops_headers
):
    """The whole point of this change.

    `file_path` is rewritten to a path that cannot exist — which is exactly what
    it is after a redeploy — and the preview must still render, because the bytes
    are served from the row.
    """
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        real_path = row.file_path
        row.file_path = DEAD_PATH
        db.commit()
    if real_path:
        Path(real_path).unlink(missing_ok=True)

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file?inline=1", headers=ops_headers
    )
    assert resp.status_code == 200
    assert resp.content == JPEG
    assert resp.headers["content-type"].startswith("image/jpeg")


def test_the_extractor_can_re_read_a_document_whose_disk_copy_is_gone():
    """A retry has to be possible, not just a preview.

    The extractors take a path, so the row's bytes are materialised for the
    duration of the call — and only for the duration.
    """
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        row.file_path = DEAD_PATH
        db.commit()

        with materialize_original(db, row) as path:
            assert Path(path).read_bytes() == JPEG
            assert Path(path).suffix == ".jpg"
            # It is a real file on disk, which is what PdfReader/openpyxl/
            # `open(..., "rb")` require.
            assert Path(path).is_file()
            temp_path = path

    assert not Path(temp_path).exists()


def test_the_materialised_file_is_removed_even_when_extraction_raises():
    """A minute-long OCR call must not leak a temp file per document."""
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        row.file_path = DEAD_PATH
        db.commit()

        with pytest.raises(RuntimeError):
            with materialize_original(db, row) as path:
                temp_path = path
                raise RuntimeError("the OCR provider blew up")

    assert not Path(temp_path).exists()


# --- Failing loudly, not silently --------------------------------------------

def test_a_document_with_no_original_anywhere_fails_loudly():
    """Better a named error than a reader handed an empty file.

    The two documents stranded by the old storage scheme are in exactly this
    state. They cannot be recovered, but they must not be mistaken for a
    document that legitimately has no attachment.
    """
    with SessionLocal() as db:
        doc = IntakeDocument(source_type="image", original_filename="lost.jpg")
        db.add(doc)
        db.commit()

        with pytest.raises(FileNotFoundError) as exc:
            with materialize_original(db, doc):
                pass

    assert "not available" in str(exc.value)


def test_the_endpoint_says_the_original_is_gone_rather_than_blaming_the_disk(
    client, ops_headers
):
    """The old message was "File not found on disk" — true, and useless.

    It named the one thing the reader could not act on, and made a storage bug
    look like a missing file on a machine they could go and check.
    """
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")
    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        row.file_data = None
        row.file_path = DEAD_PATH
        db.commit()

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file?inline=1", headers=ops_headers
    )
    assert resp.status_code == 404
    assert "no longer available" in resp.json()["detail"]


def test_an_ingest_survives_a_disk_it_cannot_write_to(monkeypatch):
    """The disk is a cache. A read-only one must not fail an ingest."""
    from app.core.config import Settings

    def _refuse(*_parts: str):
        raise OSError(30, "Read-only file system")

    # On the class, not the instance: `Settings` is a pydantic model and
    # `files_path` is a method, so setattr on the singleton is rejected.
    monkeypatch.setattr(Settings, "files_path", _refuse)

    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        assert row.file_path is None
        assert row.file_data == JPEG


# --- Rows written before the column existed -----------------------------------

def test_a_legacy_row_on_disk_is_still_served(client, ops_headers, tmp_path):
    """`file_data` is NULL for anything ingested before this change.

    Those rows are only readable while their container survives, which is not
    long — but the fallback keeps them working until it does, and keeps local
    development identical to how it was.
    """
    legacy = tmp_path / "legacy.jpg"
    legacy.write_bytes(JPEG)

    with SessionLocal() as db:
        doc = IntakeDocument(
            source_type="image",
            original_filename="legacy.jpg",
            file_path=str(legacy),
            file_data=None,
        )
        db.add(doc)
        db.commit()
        doc_id = doc.id

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file?inline=1", headers=ops_headers
    )
    assert resp.status_code == 200
    assert resp.content == JPEG


# --- The download header ------------------------------------------------------

def test_a_chinese_filename_arrives_intact(client, ops_headers):
    """RFC 5987, because the header is built by hand now.

    FileResponse used to own this header. Serving the bytes directly took it out
    of the path, and a hand-built header is exactly where a non-ASCII filename
    turns into mojibake or a 500.
    """
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="送货单.jpg")

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file?inline=1", headers=ops_headers
    )
    assert resp.status_code == 200
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("inline")
    assert "filename*=utf-8''" in disposition


def test_a_plain_filename_keeps_the_simple_quoted_form(client, ops_headers):
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file", headers=ops_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == 'attachment; filename="slip.jpg"'


def test_the_media_type_does_not_depend_on_the_disk_path_any_more(
    client, ops_headers
):
    """The extension used to be read off `file_path`, which is the field that
    survives least well. `original_filename` is the durable source of it."""
    doc_id = _submit(source_type="image", file_bytes=JPEG, filename="slip.jpg")
    with SessionLocal() as db:
        row = db.get(IntakeDocument, doc_id)
        row.file_path = None
        db.commit()

    resp = client.get(
        f"/api/v1/intake/documents/{doc_id}/file?inline=1", headers=ops_headers
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")
