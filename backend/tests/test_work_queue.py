"""The work queue: the numbers behind the nav badges.

Two things are pinned here, and the second is the one that actually breaks.

1. **The counts.** Every badge must move when its section's work moves, and go
   back to where it was when a person clears it. A badge that only ever shows
   zero is indistinguishable from a missing badge, which is how the reported
   problem presented: "I feel like I am currently having to refresh and double
   check and implement and not implemented, because during testing its not
   coming up".

2. **The contract between the shell and the server.** `AdminLayout.tsx` decides
   *which nav item* wears a badge and *which roles* may see it; `workqueue.py`
   decides *what is counted* and *for whom*. A typo on either side does not
   raise — it just goes quiet. So the two lists are compared literally, by
   reading the TSX. If you add a gated section and forget one half, this file
   fails instead of the badge silently not appearing.

The counts are asserted as deltas against a shared session-scoped database, so
these tests do not care what else has run before them — only that the number
moved by the amount the action earned.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.workqueue import ROLE_SECTIONS
from tests.conftest import _auth_headers
from tests.conftest import client, admin_headers, warehouse_headers, finance_headers  # noqa: F401


# ---------------------------------------------------------------------------
# The shell/server contract
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAYOUT_TSX = _REPO_ROOT / "frontend" / "src" / "layouts" / "AdminLayout.tsx"

# `{ key: "/intake", labelKey: "nav.intake", icon: <InboxOutlined />, roles: ["admin", "ops"] },`
# Anchored on the leading slash so the menu *group* keys ("operations",
# "warehouse", …) and the `MenuItemDef` interface's `key: string;` do not match.
_NAV_ITEM_RE = re.compile(r'key:\s*"(/[^"]+)"[^{}]*?roles:\s*\[([^\]]*)\]')
# Inside `WORK_BADGES`, each entry declares `nav` before `queue`.
_BADGE_RE = re.compile(r'nav:\s*"([^"]+)"\s*,\s*queue:\s*"([^"]+)"')


def _layout_source() -> str:
    if not _LAYOUT_TSX.exists():
        pytest.skip(f"{_LAYOUT_TSX} is not present — cannot check the shell/server contract")
    return _LAYOUT_TSX.read_text(encoding="utf-8")


def _badge_pairs() -> list[tuple[str, str]]:
    """`[(nav key, queue key), …]` from `AdminLayout.WORK_BADGES`."""
    src = _layout_source()
    start = src.index("const WORK_BADGES")
    end = src.index("\n];", start)
    return _BADGE_RE.findall(src[start:end])


def _nav_roles() -> dict[str, set[str]]:
    """`{nav key: {roles}}` from `AdminLayout.MENU_GROUPS`."""
    return {
        key: {r.strip().strip('"') for r in roles.split(",") if r.strip()}
        for key, roles in _NAV_ITEM_RE.findall(_layout_source())
    }


def test_the_shell_badges_exactly_the_sections_the_server_counts():
    """A badge with no count is dead; a count with no badge is invisible.

    Both directions matter. The first is the typo that makes a badge vanish
    silently; the second is a section someone added server-side and forgot to
    surface, which is the shape of the bug that started this.
    """
    badged = {queue for _, queue in _badge_pairs()}
    assert badged == set(ROLE_SECTIONS), (
        "WORK_BADGES in AdminLayout.tsx and ROLE_SECTIONS in "
        "app/services/workqueue.py disagree.\n"
        f"  badged but not counted: {sorted(badged - set(ROLE_SECTIONS))}\n"
        f"  counted but not badged: {sorted(set(ROLE_SECTIONS) - badged)}"
    )


def test_a_badge_is_only_worn_by_roles_that_can_open_the_page():
    """The nav item's roles and the server's roles for that section must match.

    If the shell is stricter than the server, the number is fetched and thrown
    away. If it is looser, a role gets a badge for a page it cannot open — a
    number it has no way to clear, which teaches people to ignore badges.
    """
    nav = _nav_roles()
    for nav_key, queue in _badge_pairs():
        assert nav_key in nav, f"WORK_BADGES points at {nav_key}, which is not a nav item"
        assert nav[nav_key] == ROLE_SECTIONS[queue], (
            f"{nav_key} is shown to {sorted(nav[nav_key])} but the server counts "
            f"'{queue}' for {sorted(ROLE_SECTIONS[queue])}"
        )


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

# Pinned literally rather than derived from ROLE_SECTIONS, so that a careless
# edit to the map has to be an intentional edit to this list too.
_ROLE_EXPECTATIONS = {
    "admin@erp.local": {
        "intake", "orders", "consolidation", "purchase_orders", "identity_chats",
        "inbound", "pick_lists", "delivery", "invoices",
    },
    "ops@erp.local": {
        "intake", "orders", "consolidation", "purchase_orders", "identity_chats",
    },
    "warehouse@erp.local": {"pick_lists", "delivery"},
    # `inbound` moved here from warehouse on 2026-09-19 with the page itself.
    "finance@erp.local": {"inbound", "orders", "invoices"},
    "driver@erp.local": {"delivery"},
}


def _queue(client, headers) -> dict[str, int]:  # noqa: F811
    r = client.get("/api/v1/work-queue", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict), body
    assert all(isinstance(v, int) for v in body.values()), body
    return body


@pytest.mark.parametrize("email", sorted(_ROLE_EXPECTATIONS))
def test_the_queue_carries_only_the_sections_the_role_can_act_on(client, email):
    assert set(_queue(client, _auth_headers(email))) == _ROLE_EXPECTATIONS[email]


def test_a_section_is_omitted_rather_than_reported_as_zero(client, warehouse_headers):
    """Absent, not zero.

    A key present with 0 would invite a permanently empty badge for a role that
    can never fill it, and the client badges whatever it is handed.
    """
    got = _queue(client, warehouse_headers)
    assert "intake" not in got
    assert "orders" not in got
    assert "invoices" not in got
    # Inbound left the warehouse on 2026-09-19: the section moved to finance, and
    # a badge for a page this role cannot open is a number it can never clear.
    assert "inbound" not in got
    assert "pick_lists" in got


def test_the_inbound_badge_is_a_finance_number_now(client, finance_headers):
    """The one section that changed owner, pinned explicitly.

    Not redundant with the parametrised test above: that one would still pass if
    someone moved the section to *any* other role, and this asserts where it
    actually belongs and that the warehouse lost it.
    """
    fin = _queue(client, finance_headers)
    assert "inbound" in fin
    assert "inbound" not in _queue(client, _auth_headers("warehouse@erp.local"))


def test_every_authenticated_role_may_read_its_own_queue(client):
    """No role is 403'd out of the endpoint — the response is already scoped.

    This is why there is one endpoint and not one per section: per-section
    polling produced a 403 per section the role cannot open, which is invisible
    in the UI and noise in the network log.
    """
    for email in _ROLE_EXPECTATIONS:
        r = client.get("/api/v1/work-queue", headers=_auth_headers(email))
        assert r.status_code == 200, f"{email} -> {r.status_code} {r.text}"


def test_the_queue_requires_a_token(client):
    assert client.get("/api/v1/work-queue").status_code in (401, 403)


# ---------------------------------------------------------------------------
# A badge must be countable from the page it points at
# ---------------------------------------------------------------------------

# Sections whose badge is the number of rows on a page, with the request that
# page makes. The badge and this request must return the same number — a badge
# that disagrees with its own page is worse than no badge, because it is
# indistinguishable from a broken one.
#
# This is the defect that was reported: the Inbound badge counted purchase
# orders owed a receipt while its page rendered *receipts*, so the badge read 2
# and the table was empty.
#
# Verified against production on 2026-09-19 before being written down here:
# intake 13/13, orders 2/2, identity 0/0, purchase_orders 2/2, pick_lists 9/9.
_BADGE_LIST_SOURCES: dict[str, str] = {
    "intake": "/api/v1/intake/documents?status=needs_review&page_size=100",
    "orders": "/api/v1/orders?statuses=draft,pending_confirmation,needs_clarification&page_size=100",
    "identity_chats": "/api/v1/identity/unbound",
    "purchase_orders": "/api/v1/purchase-orders?status=draft&page_size=100",
    "inbound": "/api/v1/inbound-receipts/awaiting?page_size=100",
}

# The rest, and why no list can be compared to them. Each one is a deliberate
# answer, not an omission — the point is that every badge has been thought
# about, so a new section cannot be added without deciding.
_BADGE_NOT_A_LIST: dict[str, str] = {
    # A date you run consolidation for, not a set of rows.
    "consolidation": "the action is 'run consolidation for a date'; the batch list is keyed differently",
    # Cleared from the Delivery board, which lists every delivery — the badge is
    # the not-yet-finished subset, and `/deliveries` has no multi-status filter.
    "delivery": "the badge is the not-delivered subset; /deliveries filters one status at a time",
    # Normally zero; cleared by generating an invoice, which is on the Invoices
    # page but keyed by order, not by "has no invoice".
    "invoices": "no filter expresses 'fulfilled with no invoice'",
    # `task-count` is not a `{items,total}` page response; its `open_lines` is
    # asserted directly in test_pick_lists_... below.
    "pick_lists": "the page's count endpoint is task-count, asserted separately",
}


def test_every_badged_section_has_a_decided_source():
    """No badge without a decision about how its page shows the same rows.

    Adding a section to `ROLE_SECTIONS` without saying which of these two lists
    it belongs to fails here, rather than shipping a number nobody can check.
    """
    assert set(_BADGE_LIST_SOURCES) | set(_BADGE_NOT_A_LIST) == set(ROLE_SECTIONS)
    assert not (set(_BADGE_LIST_SOURCES) & set(_BADGE_NOT_A_LIST))


@pytest.mark.parametrize("section", sorted(_BADGE_LIST_SOURCES))
def test_the_badge_count_matches_the_rows_the_page_can_show(client, admin_headers, section):
    """The number on the nav item is the number of rows behind it."""
    badge = _queue(client, admin_headers)[section]
    r = client.get(_BADGE_LIST_SOURCES[section], headers=admin_headers)
    assert r.status_code == 200, r.text
    listed = r.json()
    assert badge == listed["total"], (
        f"the {section} badge says {badge} but its page can show "
        f"{listed['total']} row(s) — one of them changed predicate without the other"
    )


def test_the_pick_list_badge_is_the_open_lines_the_page_can_pick(client, warehouse_headers):
    """`task-count.open_lines` is the badge, and it is the rows with a Pick button."""
    badge = _queue(client, warehouse_headers)["pick_lists"]
    body = client.get("/api/v1/pick-lists/task-count", headers=warehouse_headers).json()
    assert badge == body["open_lines"]


def test_a_partly_received_po_is_still_owed_and_still_counted(client, finance_headers):
    """The status a naive filter drops, and the reason this test exists.

    `RECEIVABLE_PO_STATUSES` is `{sent, partially_received}`. A list filtered on
    `sent` alone — the shape this nearly shipped with — hides every PO awaiting
    a second delivery while the badge keeps counting it. That is the same class
    of defect as the one reported, one filter clause smaller.

    Written after a probe: narrowing the list to `sent` left **all five**
    `test_the_badge_count_matches_the_rows_the_page_can_show` cases green,
    because the shared test database happened to contain no partly-received PO.
    A parity test over whatever state exists passes vacuously the moment the
    interesting state is absent. So the state is created here rather than hoped
    for.
    """
    from app.core.database import SessionLocal
    from tests.test_warehouse import _seed_chain_for_inbound

    # Ordered 60 of potato, 30 of cabbage; receive 50 → partly received.
    data = _seed_chain_for_inbound(SessionLocal(), partial=True)
    r = client.post(
        "/api/v1/inbound-receipts",
        headers=finance_headers,
        json={"po_id": data["po"].id, "lines": [
            {"po_line_id": data["pol1"].id, "quantity_received": 50.0},
        ]},
    )
    assert r.status_code == 201, r.text

    from app.models import PurchaseOrder
    with SessionLocal() as db:
        assert db.get(PurchaseOrder, data["po"].id).status == "partially_received"

    listed = client.get(
        "/api/v1/inbound-receipts/awaiting?page_size=100", headers=finance_headers
    ).json()
    row = next((x for x in listed["items"] if x["id"] == data["po"].id), None)
    assert row is not None, "a partly-received PO still owes goods and must stay listed"
    assert row["status"] == "partially_received"
    # Both lines: 60 ordered - 50 received = 10 of potato, plus all 30 of
    # cabbage, which has not arrived at all. Outstanding is per PO, not per
    # line — the first draft of this assertion said 10.0 and was wrong.
    assert row["quantity_outstanding"] == 40.0
    assert row["total_received"] == 50.0

    badge = _queue(client, finance_headers)["inbound"]
    assert badge == listed["total"], (
        "the badge counts partly-received POs, so the list must show them too"
    )


# ---------------------------------------------------------------------------
# The counts move when the work moves
# ---------------------------------------------------------------------------

def _seed_consolidated_order():
    """A confirmed→consolidated order with a sent PO, on its own delivery date."""
    from app.core.database import SessionLocal
    from tests.test_warehouse import _seed_chain_for_inbound

    return _seed_chain_for_inbound(SessionLocal())


def test_an_open_pick_line_is_counted_and_picking_it_clears_the_badge(
    client, warehouse_headers, finance_headers
):
    """The warehouse badge the user could not see.

    Note what the count tracks: the *pick lines*, not the order. The order sits
    at `consolidated` for the whole test — the badge has to fall to zero when
    the picking is done, not when the order changes status.

    The receipt is posted as finance: inbound receipts moved to finance on
    2026-09-19, and the warehouse is now 403 on `POST /inbound-receipts`.
    """
    data = _seed_consolidated_order()
    before = _queue(client, warehouse_headers)["pick_lists"]

    r = client.post(
        "/api/v1/inbound-receipts",
        headers=finance_headers,
        json={"po_id": data["po"].id, "lines": [
            {"po_line_id": data["pol1"].id, "quantity_received": 50.0},
            {"po_line_id": data["pol2"].id, "quantity_received": 30.0},
        ]},
    )
    assert r.status_code == 201, r.text

    gen = client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert gen.status_code == 201, gen.text
    pick_list = gen.json()["pick_list"]

    assert _queue(client, warehouse_headers)["pick_lists"] == before + 2, (
        "generating a pick list for two open lines must raise the badge by two"
    )

    for ln in pick_list["lines"]:
        rr = client.post(
            f"/api/v1/pick-lists/{pick_list['id']}/lines/{ln['id']}/pick",
            headers=warehouse_headers,
            json={"picked_quantity": ln["quantity"]},
        )
        assert rr.status_code == 200, rr.text

    assert _queue(client, warehouse_headers)["pick_lists"] == before, (
        "picking every line must return the badge to where it started"
    )


def test_task_count_says_how_many_orders_have_no_pick_list_yet(client, warehouse_headers):
    """`orders_awaiting_pick_list` — the gap the user hit while testing.

    Confirming an order does not generate a pick list, and neither does
    consolidating it. Until someone presses Generate, the warehouse has nothing
    to look at, so the Pick Lists screen has to be able to say so out loud.
    """
    data = _seed_consolidated_order()
    r = client.get("/api/v1/pick-lists/task-count", headers=warehouse_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["orders_awaiting_pick_list"] >= 1
    assert data["delivery_date"].isoformat() in body["awaiting_dates"]

    gen = client.post(
        "/api/v1/pick-lists/generate",
        headers=warehouse_headers,
        json={"delivery_date": data["delivery_date"].isoformat()},
    )
    assert gen.status_code == 201, gen.text

    after = client.get("/api/v1/pick-lists/task-count", headers=warehouse_headers).json()
    assert data["delivery_date"].isoformat() not in after["awaiting_dates"], (
        "a date that now has a pick list is no longer awaiting one"
    )
    assert after["orders_awaiting_pick_list"] == body["orders_awaiting_pick_list"] - 1


def test_a_draft_purchase_order_is_counted_and_hands_over_to_inbound_when_sent(
    client, admin_headers
):
    """Draft and sent are two different obligations, and neither may be dropped.

    A draft PO has been composed and told to nobody — nothing is on order and no
    truck is coming. Sending it is the action that clears the Purchase Orders
    badge and creates the Inbound one. Counting both in one section would hide
    the first behind the second; counting neither is the hole this closes.
    """
    from app.core.database import SessionLocal
    from app.models import Product, PurchaseOrder, User, Wholesaler

    before = _queue(client, admin_headers)

    with SessionLocal() as db:
        ops = db.query(User).filter(User.email == "ops@erp.local").one()
        po = PurchaseOrder(
            po_number=f"PO-QUEUE-{datetime.now(timezone.utc).strftime('%H%M%S%f')}",
            wholesaler_id=db.query(Wholesaler).first().id,
            category_id=db.query(Product).first().category_id,
            status="draft",
            total_amount=0.0,
            created_by=ops.id,
        )
        db.add(po)
        db.commit()
        po_id = po.id

    drafted = _queue(client, admin_headers)
    assert drafted["purchase_orders"] == before["purchase_orders"] + 1
    assert drafted["inbound"] == before["inbound"], "a draft is not yet inbound work"

    with SessionLocal() as db:
        db.get(PurchaseOrder, po_id).status = "sent"
        db.commit()

    after = _queue(client, admin_headers)
    assert after["purchase_orders"] == before["purchase_orders"]
    assert after["inbound"] == before["inbound"] + 1


def test_a_fulfilled_order_with_no_invoice_is_counted_even_when_a_blank_invoice_exists(
    client, admin_headers
):
    """The NULL trap, pinned.

    `invoices.order_id` is nullable, and `NOT IN (SELECT order_id FROM invoices)`
    matches **nothing** in SQL as soon as that subquery yields a single NULL. So
    one invoice not raised against an order — a statement-period invoice, say —
    would silently pin this badge to zero forever, and nobody would ever notice,
    because zero is the normal reading.

    Hence `Invoice.order_id.is_not(None)` in `workqueue.collect`. Delete that
    clause and this test goes red.
    """
    from app.core.database import SessionLocal
    from app.models import Customer, Invoice, Order, User

    before = _queue(client, admin_headers)["invoices"]

    with SessionLocal() as db:
        ops = db.query(User).filter(User.email == "ops@erp.local").one()
        customer_id = db.query(Customer).first().id
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        # An invoice that belongs to no order — this is what makes a naive
        # NOT IN collapse to "no rows".
        db.add(Invoice(
            invoice_number=f"INV-QUEUE-{stamp}",
            customer_id=customer_id,
            order_id=None,
            status="draft",
            total_amount=0.0,
        ))
        db.add(Order(
            order_number=f"ORD-QUEUE-{stamp}",
            customer_id=customer_id,
            status="fulfilled",
            delivery_date=date(2027, 6, 1),
            source_type="manual",
            created_by=ops.id,
        ))
        db.commit()

    assert _queue(client, admin_headers)["invoices"] == before + 1


def test_a_driver_is_badged_only_for_their_own_deliveries(client, admin_headers):
    """A driver cannot clear the depot's board, so it must not be shown to them.

    The delivery section is the one whose scope changes by role rather than only
    by presence: an unassigned delivery still needs a human, but not *this*
    human. So the driver's count moves by one and the depot's by two.
    """
    from app.core.database import SessionLocal
    from app.models import Delivery, User

    data = _seed_consolidated_order()
    driver_headers = _auth_headers("driver@erp.local")
    before_driver = _queue(client, driver_headers)["delivery"]
    before_admin = _queue(client, admin_headers)["delivery"]

    with SessionLocal() as db:
        driver_id = db.query(User).filter(User.email == "driver@erp.local").one().id
        stamp = datetime.now(timezone.utc).strftime("%H%M%S%f")
        db.add(Delivery(
            delivery_number=f"DLV-QUEUE-A-{stamp}",
            order_id=data["order"].id,
            driver_id=driver_id,
            status="scheduled",
            scheduled_date=data["delivery_date"] + timedelta(days=30),
        ))
        db.add(Delivery(
            delivery_number=f"DLV-QUEUE-B-{stamp}",
            order_id=data["order"].id,
            driver_id=None,
            status="scheduled",
            scheduled_date=data["delivery_date"] + timedelta(days=31),
        ))
        db.commit()

    assert _queue(client, driver_headers)["delivery"] == before_driver + 1, (
        "the driver is badged for the delivery assigned to them, and only that one"
    )
    assert _queue(client, admin_headers)["delivery"] == before_admin + 2, (
        "the depot is badged for both, including the unassigned one"
    )
