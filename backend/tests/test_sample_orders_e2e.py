"""End-to-end tests: real procurement photos → POST /api/v1/intake/submit
with `source_type=text` and the transcribed fixture as `raw_text` → poll the
IntakeJob until `completed` → GET the IntakeExtraction and assert the
breakdowns survived the pipeline into `raw_output.lines[i].breakdowns`.

Pattern mirrors `tests/test_intake.py`: same `client` + `admin_headers`
fixtures, same `BackgroundTasks` flow (the in-process TestClient runs
background tasks synchronously, so polling is a safety net).

Per-sample expected counts:
    A | 13 lines | 32 breakdowns  (image cropped; 任务数:20)
    B | 16 lines | 24 breakdowns
    C |  7 lines | 11 breakdowns
    D | 14 lines | 35 breakdowns
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.conftest import admin_headers, client


FIXTURES = Path(__file__).parent / "fixtures" / "sample_orders"
_JOB_TIMEOUT = 15  # seconds; pipeline runs synchronously in TestClient


def _read(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


def _wait_for_job(client, job_id: str, headers: dict) -> dict:
    deadline = time.time() + _JOB_TIMEOUT
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/v1/intake/jobs/{job_id}", headers=headers)
        assert r.status_code == 200, f"job get failed: {r.status_code} {r.text}"
        last = r.json()
        if last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.05)
    return last


def _get_customer_id(client, headers: dict, code: str = "C001") -> str:
    r = client.get("/api/v1/customers", headers=headers)
    assert r.status_code == 200, r.text
    for c in r.json()["items"]:
        if c["code"] == code:
            return c["id"]
    raise AssertionError(f"Customer {code} not found in seed data")


# Map: sample -> (expected_line_count, expected_min_breakdown_lines, expected_min_with_code, task_count_str)
_EXPECTATIONS = {
    "sample_a": (13, 13, 32, "20"),
    "sample_b": (16, 16, 20, "16"),
    "sample_c": (7, 7, 11, "7"),
    "sample_d": (14, 14, 30, "14"),
}


@pytest.mark.parametrize("sample", list(_EXPECTATIONS))
def test_sample_e2e_through_pipeline(client, admin_headers, sample):
    customer_id = _get_customer_id(client, admin_headers, "C001")
    text = _read(sample)
    expected_lines, expected_min_bd_lines, expected_min_with_code, expected_task = (
        _EXPECTATIONS[sample]
    )

    r = client.post(
        "/api/v1/intake/submit",
        json={
            "customer_id": customer_id,
            "source_type": "text",
            "raw_text": text,
            "delivery_date": "2026-09-08",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, f"{r.status_code} {r.text}"
    body = r.json()
    job_id = body["job_id"]

    job = _wait_for_job(client, job_id, admin_headers)
    assert job["status"] == "completed", (
        f"job did not complete: status={job.get('status')} error={job.get('error')}"
    )

    r2 = client.get(f"/api/v1/intake/extractions/{job_id}", headers=admin_headers)
    assert r2.status_code == 200, r2.text
    ext = r2.json()
    raw = ext.get("raw_output") or {}
    assert raw.get("doc_type") == "supplier_order_table", (
        f"expected doc_type=supplier_order_table, got {raw.get('doc_type')!r}"
    )

    lines = raw.get("lines") or []
    assert len(lines) == expected_lines, (
        f"{sample}: expected {expected_lines} lines, got {len(lines)}"
    )

    # Per-line shape sanity
    for ln in lines:
        assert "raw_text" in ln
        assert "breakdowns" in ln  # always present (possibly empty list)
        assert "matched_product_id" in ln
        assert "quantity" in ln
        assert "unit" in ln

    # At least N lines have a non-empty breakdowns list
    lines_with_bds = [ln for ln in lines if (ln.get("breakdowns") or [])]
    assert len(lines_with_bds) >= expected_min_bd_lines, (
        f"{sample}: expected >= {expected_min_bd_lines} lines with "
        f"non-empty breakdowns, got {len(lines_with_bds)}"
    )

    # At least one breakdown exposes a customer_code or menu_code
    flat_bds = [bd for ln in lines_with_bds for bd in ln["breakdowns"]]
    coded = [
        bd
        for bd in flat_bds
        if bd.get("customer_code") or bd.get("menu_code")
    ]
    assert len(coded) >= expected_min_with_code, (
        f"{sample}: expected >= {expected_min_with_code} breakdowns with a "
        f"customer_code/menu_code, got {len(coded)}"
    )

    # Header metadata from the structured parser should be surfaced on
    # the extraction so ERP staff can see the source document context.
    header = raw.get("header") or {}
    assert header.get("task_count") == expected_task, (
        f"{sample}: expected header.task_count={expected_task!r}, "
        f"got {header.get('task_count')!r}"
    )

    # The job should have produced a draft order.
    assert job.get("draft_order_id"), (
        f"{sample}: completed job has no draft_order_id"
    )


@pytest.mark.parametrize("sample", list(_EXPECTATIONS))
def test_sample_breakdowns_reconcile_to_line_total(
    client, admin_headers, sample
):
    """The per-customer breakdowns must add up to the row total.

    This is the single strongest correctness signal for a multi-drop
    procurement table: if the parser mis-splits the 明细 cell the sum drifts
    away from 采购总数, which is exactly the failure mode that would silently
    under- or over-deliver to a customer.
    """
    customer_id = _get_customer_id(client, admin_headers, "C001")
    r = client.post(
        "/api/v1/intake/submit",
        json={
            "customer_id": customer_id,
            "source_type": "text",
            "raw_text": _read(sample),
            "delivery_date": "2026-09-08",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)
    assert job["status"] == "completed", job.get("error")

    ext = client.get(
        f"/api/v1/intake/extractions/{job['id']}", headers=admin_headers
    ).json()
    lines = (ext.get("raw_output") or {}).get("lines") or []

    checked = 0
    for ln in lines:
        bds = ln.get("breakdowns") or []
        if not bds:
            continue
        total = ln.get("quantity")
        if total is None:
            continue
        bd_sum = sum(bd.get("quantity") or 0 for bd in bds)
        assert abs(bd_sum - total) < 0.01, (
            f"{sample} line {ln.get('line_no')}: breakdowns sum to "
            f"{bd_sum} but row total is {total}"
        )
        checked += 1

    assert checked >= _EXPECTATIONS[sample][1], (
        f"{sample}: only reconciled {checked} lines"
    )
