"""A retried job must be shown with its NEWEST extraction.

Every run appends an IntakeExtraction row instead of replacing one, because the
raw AI output is an immutable audit trail. `get_extraction_for_job` used to
order by `id` — a random UUID — so with two rows it returned whichever sorted
first, and a reviewer re-processing a document to get a better read could be
shown the superseded one.

Found live on 2026-09-19: a job retried right after the figure-only fix still
served the old five-line read, `tbl-0.md` included, which is a line the fixed
code cannot produce. The re-run looked like it had done nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sqlalchemy import update

from app.core.database import SessionLocal
from app.models import IntakeExtraction
from app.models.base import gen_uuid
from app.services.intake.service import get_extraction_for_job


@pytest.fixture(autouse=True)
def _schema(app):
    """Tables only exist once the app fixture has run `init_db()`."""
    return app


def _row(job_id: str, marker: str, created_at=None, row_id: str | None = None) -> IntakeExtraction:
    """`row_id` is set explicitly so the ORDER is deterministic.

    Ids are random UUIDs, so a test that lets them be generated cannot pin an
    ordering: `order_by(id)` picks whichever UUID happens to sort first, and the
    assertion passes by luck roughly half the time. Verified — with the old
    `order_by(id)` restored, ids generated at random let all four of these pass.
    """
    return IntakeExtraction(
        id=row_id or gen_uuid(),
        job_id=job_id,
        raw_output={"marker": marker},
        overall_confidence=None,
        parser_notes=None,
        created_at=created_at,
    )


def _pair(n: int) -> tuple[str, str]:
    """(sorts-first, sorts-last) row ids, unique per test.

    Deliberately ordered so that sorting by id ASCENDING returns the STALE row
    first — the choice the bug made. Unique because the test database is
    session-scoped and a repeated primary key fails the insert.
    """
    return (f"00000000-0000-0000-0000-{n:012d}", f"ffff0000-0000-0000-0000-{n:012d}")


def _add(*rows: IntakeExtraction) -> None:
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()


def test_a_retried_job_is_shown_with_its_newest_extraction():
    _add(
        _row("job-currency-1", "superseded",
             datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc), row_id=_pair(1)[0]),
        _row("job-currency-1", "current",
             datetime(2026, 9, 19, 3, 5, tzinfo=timezone.utc), row_id=_pair(1)[1]),
    )

    with SessionLocal() as db:
        got = get_extraction_for_job(db, "job-currency-1")

    assert got is not None
    assert got.raw_output["marker"] == "current"


def test_a_row_written_before_the_column_existed_loses_to_a_real_timestamp():
    """The backfill is best-effort, so a NULL must lose rather than win.

    Postgres sorts NULLs FIRST on a descending sort, which without `nulls_last`
    would hand back the oldest rows — precisely the ones the column was added to
    outrank.
    """
    with SessionLocal() as db:
        stale = _row("job-currency-2", "pre-migration",
                     datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc), row_id=_pair(2)[0])
        fresh = _row("job-currency-2", "current",
                     datetime(2026, 9, 19, 3, 5, tzinfo=timezone.utc), row_id=_pair(2)[1])
        db.add_all([stale, fresh])
        db.commit()
        # The ORM fills `created_at` from its Python-side default even when the
        # caller passes None, so a genuinely pre-migration row has to be put
        # back to NULL explicitly.
        db.execute(
            update(IntakeExtraction)
            .where(IntakeExtraction.id == stale.id)
            .values(created_at=None)
        )
        db.commit()

        got = get_extraction_for_job(db, "job-currency-2")

    assert got is not None
    assert got.raw_output["marker"] == "current"


def test_a_job_with_one_extraction_is_unaffected():
    _add(_row("job-currency-3", "only",
              datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc)))

    with SessionLocal() as db:
        got = get_extraction_for_job(db, "job-currency-3")

    assert got.raw_output["marker"] == "only"


def test_rows_for_another_job_are_not_returned():
    _add(
        _row("job-currency-4", "mine",
             datetime(2026, 9, 19, 3, 0, tzinfo=timezone.utc), row_id=_pair(4)[1]),
        # The other job's row is NEWER and sorts FIRST by id, so returning it
        # would prove the filter is missing rather than the ordering.
        _row("job-currency-other", "theirs",
             datetime(2026, 9, 19, 4, 0, tzinfo=timezone.utc), row_id=_pair(4)[0]),
    )

    with SessionLocal() as db:
        got = get_extraction_for_job(db, "job-currency-4")

    assert got.raw_output["marker"] == "mine"
