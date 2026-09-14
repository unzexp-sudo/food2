"""Delivery confirmation gate (docs/IDENTITY_IMPLEMENTATION_SPEC.md §2.3).

The rule: a customer row can carry an address for years without anyone ever
checking it. Copying it onto an order is a convenience for the person
confirming; it is not a confirmation. Only a person pressing "confirm delivery"
is, and until they do, the order cannot be confirmed at all.
"""
from __future__ import annotations

import uuid

from app.core.database import SessionLocal
from app.models import Customer, Order

from tests.conftest import admin_headers, client, warehouse_headers  # noqa: F401
from tests.test_mandatory_review import (
    _get_customer_id,
    _submit_and_confirm_review,
)
from tests.test_orders import (
    _disable_auto_confirm,
    _first_customer_id,
    _make_manual_order,
    _restore_default_settings,
)


def _get(client, order_id: str, headers: dict) -> dict:
    r = client.get(f"/api/v1/orders/{order_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _customer_with_address(code: str = "C001") -> Customer:
    with SessionLocal() as db:
        c = db.query(Customer).filter(Customer.code == code).one()
        if not c.address:
            c.address = "佛山市禅城区季华路 1 号"
            db.commit()
        return c


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_an_order_cannot_be_confirmed_without_a_confirmed_destination(
    client, admin_headers, require_delivery_confirmation  # noqa: F811
):
    """The whole point: a wrong address on a confirmed order is a disaster."""
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        assert order["status"] == "draft"

        r = client.post(
            f"/api/v1/orders/{order['id']}/confirm", json={}, headers=admin_headers
        )
        assert r.status_code == 409, r.text

        still = _get(client, order["id"], admin_headers)
        assert still["status"] == "draft", "the order settled without a destination"
        assert still["confirmed_at"] is None
    finally:
        _restore_default_settings(client, admin_headers)


def test_prefilled_address_is_a_proposal_not_a_confirmation(
    client, admin_headers, require_delivery_confirmation  # noqa: F811
):
    """Pre-fill is allowed to exist; it is never allowed to satisfy the gate."""
    _disable_auto_confirm(client, admin_headers)
    try:
        customer = _customer_with_address()
        order = _make_manual_order(client, admin_headers, customer_id=customer.id)
        assert order["delivery_address"] == customer.address, (
            "the order was not pre-filled from the customer"
        )
        assert order["delivery_confirmed_at"] is None, (
            "pre-filling silently counted as confirming"
        )

        r = client.post(
            f"/api/v1/orders/{order['id']}/confirm", json={}, headers=admin_headers
        )
        assert r.status_code == 409, r.text
    finally:
        _restore_default_settings(client, admin_headers)


def test_confirming_delivery_then_the_order_settles(
    client, admin_headers, require_delivery_confirmation  # noqa: F811
):
    """The one path through the gate: a person confirms, then the order."""
    _disable_auto_confirm(client, admin_headers)
    try:
        order = _make_manual_order(client, admin_headers)
        address = f"佛山市南海区桂城街道 {uuid.uuid4().hex[:6]} 号"

        r = client.post(
            f"/api/v1/orders/{order['id']}/confirm-delivery",
            json={
                "delivery_address": address,
                "contact_name": "陈师傅",
                "contact_phone": "13800000000",
            },
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["delivery_address"] == address
        assert body["delivery_contact_name"] == "陈师傅"
        assert body["delivery_confirmed_at"], "no confirmation timestamp"
        assert body["delivery_confirmed_by"], "nobody recorded as the confirmer"

        r2 = client.post(
            f"/api/v1/orders/{order['id']}/confirm", json={}, headers=admin_headers
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "confirmed"

        with SessionLocal() as db:
            row = db.get(Order, order["id"])
            assert row.delivery_confirmed_at is not None
            assert row.delivery_address == address
    finally:
        _restore_default_settings(client, admin_headers)


def test_confirm_delivery_requires_an_address(
    client, admin_headers, require_delivery_confirmation  # noqa: F811
):
    """An order can only be confirmed *to* somewhere."""
    _disable_auto_confirm(client, admin_headers)
    try:
        customer_id = _first_customer_id(client, admin_headers)
        with SessionLocal() as db:
            c = db.get(Customer, customer_id)
            c.address = None
            db.commit()
        order = _make_manual_order(client, admin_headers, customer_id=customer_id)
        assert order["delivery_address"] is None

        r = client.post(
            f"/api/v1/orders/{order['id']}/confirm-delivery",
            json={"contact_name": "陈师傅"},
            headers=admin_headers,
        )
        assert r.status_code == 409, r.text
    finally:
        _restore_default_settings(client, admin_headers)


# ---------------------------------------------------------------------------
# Customer address verification
# ---------------------------------------------------------------------------

def test_verify_address_records_who_checked_it(client, admin_headers):  # noqa: F811
    """A checked address is a fact with a name on it; an unchecked one is text."""
    customer_id = _first_customer_id(client, admin_headers)
    address = f"佛山市顺德区 {uuid.uuid4().hex[:6]} 路 8 号"

    r = client.post(
        f"/api/v1/customers/{customer_id}/verify-address",
        json={"address": address, "contact_name": "李经理"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["address"] == address
    assert body["address_confirmed_at"], "no verification timestamp"
    assert body["address_confirmed_by"], "nobody recorded as verifier"
    assert body["address_source"] == "manual"

    with SessionLocal() as db:
        row = db.get(Customer, customer_id)
        assert row.address_confirmed_at is not None
        assert row.address_confirmed_by == body["address_confirmed_by"]


def test_delivery_endpoints_require_ops_or_admin(
    client, warehouse_headers  # noqa: F811
):
    r = client.post(
        "/api/v1/orders/does-not-exist/confirm-delivery",
        json={"delivery_address": "x"},
        headers=warehouse_headers,
    )
    assert r.status_code == 403, f"{r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# The unattended path must not be able to get around the gate
# ---------------------------------------------------------------------------

def test_auto_confirm_cannot_settle_an_order_with_no_confirmed_destination(
    client,  # noqa: F811
    admin_headers,
    require_review,  # noqa: F811
    require_delivery_confirmation,  # noqa: F811
    pin_cutoff,
):
    """The regression this guards: auto-confirm walked straight past the gate.

    `_legacy_auto_confirm` is autouse and turns
    `orders_require_human_confirmation` OFF for this test, so the auto-confirm
    branch really is reachable here — which is precisely the configuration in
    which the delivery rule used to be bypassable. Auto-confirm runs with
    nobody watching, so with the delivery rule ON it must never fire.
    """
    pin_cutoff(True)
    r = client.put(
        "/api/v1/settings",
        json={
            "auto_confirm": {"enabled": True, "min_confidence": 0.95},
            "cutoff_time": "23:59",
        },
        headers=admin_headers,
    )
    # Guard against this test quietly going vacuous: if the write fails we are
    # no longer proving that the delivery gate overrides auto-confirm.
    assert r.status_code == 200, f"could not enable auto_confirm: {r.text}"

    try:
        customer_id = _get_customer_id(client, admin_headers)
        order_id = _submit_and_confirm_review(client, admin_headers, customer_id)

        order = _get(client, order_id, admin_headers)
        assert order["status"] == "draft", (
            f"auto-confirm settled an order with no confirmed destination: "
            f"{order['status']}"
        )
        assert order["delivery_confirmed_at"] is None
        assert not order.get("confirmed_at"), "nothing unattended may confirm this"
    finally:
        _restore_default_settings(client, admin_headers)
