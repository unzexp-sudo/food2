"""The two WeCom messages a customer gets while an order is in flight.

`order_confirmed` and `out_for_delivery` are the whole of the customer-facing
automation between "we took your order" and "here is your invoice". Until this
module existed, only the *payload builders* were tested — nothing asserted that
the handlers are reached at all, so the wiring could have been dead and the
suite would still have been green.

These tests drive the real endpoints and capture what the ERP hands the
gateway. They deliberately stop at the ERP boundary: the gateway resolves the
destination (and logs `skipped` when a customer has no WeCom binding), so a
test here can only prove the request was made, not that it was delivered.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tests.conftest import admin_headers, client, ops_headers, warehouse_headers  # noqa: F401


@pytest.fixture
def sent(monkeypatch):
    """Capture every notify() the WeCom event handlers make.

    Patches the name the handlers resolve at call time
    (`app.services.notify.wecom_notify.notify`), so the real dispatcher — and
    the HTTP call to the gateway — never runs.
    """
    from app.services.notify import wecom_notify

    calls: list[dict] = []

    def _fake_notify(
        db, *, template, customer_id, payload,
        order_id=None, locale="zh", chat_id=None,
    ):
        calls.append({
            "template": template,
            "customer_id": customer_id,
            "order_id": order_id,
            "locale": locale,
            "payload": payload,
        })
        return {"status": "sent"}

    monkeypatch.setattr(wecom_notify, "notify", _fake_notify)
    return calls


def _templates(calls: list[dict]) -> list[str]:
    return [c["template"] for c in calls]


def _make_pending_order(*, status: str = "pending_confirmation") -> str:
    """An order sitting in a confirmable state, built straight in the DB.

    Not created through `POST /orders` on purpose: that endpoint emits
    `order.draft_created`, and the auto-confirm handler would race this test to
    the `confirmed` status.
    """
    from app.core.database import SessionLocal
    from app.models import Customer, Order, OrderLine, Product, Unit

    stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
    with SessionLocal() as db:
        customer = db.query(Customer).filter(Customer.code == "C001").one()
        product = db.query(Product).filter(Product.sku == "VG001").one()
        jin = db.query(Unit).filter(Unit.code == "jin").one()
        order = Order(
            order_number=f"ORD-NOTIF-{stamp}",
            customer_id=customer.id,
            status=status,
            delivery_date=date(2027, 6, 1),
            source_type="manual",
            overall_confidence=1.0,
            delivery_address="1 Test Road",
            delivery_contact_name="Test Receiver",
            delivery_contact_phone="13800000000",
        )
        db.add(order)
        db.flush()
        db.add(OrderLine(
            order_id=order.id, line_no=1, raw_text="土豆20斤",
            product_id=product.id, product_display="Potato / 土豆",
            quantity=20.0, unit_id=jin.id, unit_price=2.2,
            confidence=1.0, match_method="manual",
        ))
        db.commit()
        return order.id


# ---------------------------------------------------------------------------
# Order confirmed
# ---------------------------------------------------------------------------

def test_confirming_an_order_texts_the_customer(client, ops_headers, sent):
    """The single most important message: a person approved the order."""
    order_id = _make_pending_order()
    r = client.post(f"/api/v1/orders/{order_id}/confirm", headers=ops_headers, json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"

    assert _templates(sent) == ["order_confirmed"]
    call = sent[0]
    assert call["order_id"] == order_id
    assert call["customer_id"] == r.json()["customer_id"]
    # What the gateway template renders. A missing order_number would send the
    # customer "订单 - 已确认".
    assert call["payload"]["order_number"] == r.json()["order_number"]
    assert call["payload"]["delivery_date"] == "2027-06-01"
    assert [ln["product_display"] for ln in call["payload"]["lines"]] == ["Potato / 土豆"]


def test_confirming_twice_does_not_text_twice(client, ops_headers, sent):
    """A second confirm is refused, so the customer hears once."""
    order_id = _make_pending_order()
    client.post(f"/api/v1/orders/{order_id}/confirm", headers=ops_headers, json={})
    r = client.post(f"/api/v1/orders/{order_id}/confirm", headers=ops_headers, json={})
    assert r.status_code == 409
    assert _templates(sent) == ["order_confirmed"]


# ---------------------------------------------------------------------------
# Out for delivery
# ---------------------------------------------------------------------------

def _delivery_out_for_delivery(client, headers) -> dict:
    """Build a delivery through the real chain and dispatch it. Returns it."""
    from tests.test_delivery import _build_chain_to_picked

    data = _build_chain_to_picked(client, headers)
    # The final pick created the delivery; `/deliveries/generate` is a backfill
    # and would return nothing here.
    delivery_id = data["delivery_id"]
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/status", headers=headers,
        json={"status": "out_for_delivery"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "out_for_delivery"
    return r.json()


def test_dispatching_a_delivery_texts_the_customer(client, warehouse_headers, sent):
    delivery = _delivery_out_for_delivery(client, warehouse_headers)

    assert _templates(sent) == ["out_for_delivery"]
    call = sent[0]
    # `order_id` is the delivery's *order*, not the delivery — the gateway logs
    # the outbound row against the order, and the template prints the order
    # number the customer recognises.
    assert call["order_id"] == delivery["order_id"]
    assert call["payload"]["order_number"], "the customer needs the order number"


def test_picking_a_delivery_does_not_text_the_customer(client, warehouse_headers, sent):
    """"Picked" is an internal state. The customer hears at dispatch, not before."""
    from tests.test_delivery import _build_chain_to_picked

    data = _build_chain_to_picked(client, warehouse_headers)
    delivery_id = data["delivery_id"]
    r = client.post(
        f"/api/v1/deliveries/{delivery_id}/status", headers=warehouse_headers,
        json={"status": "picked"},
    )
    assert r.status_code == 200, r.text
    assert sent == []


def test_dispatching_twice_texts_once(client, warehouse_headers, sent):
    """The driver's button is idempotent in the DB but must be in WeCom too.

    Re-sending "your order is on its way" reads as a second dispatch.
    """
    delivery = _delivery_out_for_delivery(client, warehouse_headers)
    r = client.post(
        f"/api/v1/deliveries/{delivery['id']}/status", headers=warehouse_headers,
        json={"status": "out_for_delivery"},
    )
    assert r.status_code == 200, r.text
    assert _templates(sent) == ["out_for_delivery"]


# ---------------------------------------------------------------------------
# The order payload carries the delivery leg
# ---------------------------------------------------------------------------

def test_order_detail_exposes_the_delivery_leg(client, warehouse_headers, ops_headers):
    """The timeline cannot show "out for delivery" without this.

    `out_for_delivery` is a Delivery status, not an Order status, so the order
    payload has to hand it over — otherwise the ERP jumps
    `consolidated -> fulfilled` and the customer's message has no counterpart
    on screen.
    """
    delivery = _delivery_out_for_delivery(client, warehouse_headers)
    order = client.get(
        f"/api/v1/orders/{delivery['order_id']}", headers=ops_headers
    ).json()

    assert order["delivery"] is not None
    assert order["delivery"]["id"] == delivery["id"]
    assert order["delivery"]["status"] == "out_for_delivery"
    assert order["delivery"]["out_at"] is not None


def test_order_list_does_not_pay_for_the_delivery_leg(client, ops_headers):
    """The list view renders no delivery, so it must not query one per row."""
    body = client.get("/api/v1/orders?page_size=5", headers=ops_headers).json()
    assert body["items"], "seed should provide at least one order"
    assert all(item["delivery"] is None for item in body["items"])


# ---------------------------------------------------------------------------
# A send that does not land must leave a trace
# ---------------------------------------------------------------------------

def _notification_rows() -> list[dict]:
    """Every `Notification` audit row, newest first."""
    from app.core.database import SessionLocal
    from app.models import AuditLog

    with SessionLocal() as db:
        rows = (
            db.query(AuditLog)
            .filter(AuditLog.entity_type == "Notification")
            .order_by(AuditLog.created_at.desc())
            .all()
        )
        return [
            {"entity_id": r.entity_id, "after": r.after or {}, "summary": r.summary}
            for r in rows
        ]


def test_a_dead_gateway_is_recorded_and_does_not_block_the_confirm(
    client, ops_headers, monkeypatch
):
    """The two halves of the contract, together.

    A customer message that cannot be sent must never fail the order — and it
    must not vanish either. Before this, `notify()` caught the connection error,
    logged a warning and returned a dict the caller dropped, so "the customer
    was never texted" was invisible from inside the ERP.
    """
    from app.core.config import settings

    # Discard port: connection refused, immediately.
    monkeypatch.setattr(settings, "wecom_gateway_url", "http://127.0.0.1:9")

    order_id = _make_pending_order()
    r = client.post(f"/api/v1/orders/{order_id}/confirm", headers=ops_headers, json={})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"

    rows = [row for row in _notification_rows() if row["entity_id"] == order_id]
    assert rows, "the failed send left no trace"
    assert rows[0]["after"]["template"] == "order_confirmed"
    assert rows[0]["after"]["status"] == "failed"
    assert rows[0]["after"]["detail"], "a failure with no reason is not diagnosable"


def test_a_disabled_notifier_is_recorded(client, ops_headers, monkeypatch):
    """`notify_enabled=False` is the quietest possible failure: nothing happens.

    It is also the one most likely to be left on by accident, so it has to be
    visible in the same place as a failure.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "notify_enabled", False)

    order_id = _make_pending_order()
    r = client.post(f"/api/v1/orders/{order_id}/confirm", headers=ops_headers, json={})
    assert r.status_code == 200, r.text

    rows = [row for row in _notification_rows() if row["entity_id"] == order_id]
    assert rows, "a disabled notifier is indistinguishable from a working one"
    assert rows[0]["after"]["status"] == "disabled"
