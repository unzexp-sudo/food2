"""The work queue — what is waiting on a person, per section of the app.

One read model for the nav badges, rather than one endpoint per section. The
shell wants all of these at the same moment on every tick, so asking section by
section meant N requests per tick *plus* a 403 for every section the role cannot
open. Here the server decides what the caller may know and answers in one round
trip.

The rule every number obeys, because a badge that counts something you cannot
act on teaches people to ignore badges — and then a real order stops getting
noticed:

- **Count work waiting on us, not on someone else.** An order in
  `needs_clarification` is parked on the customer's reply, but ops still has a
  move (`resubmit`), so it counts. A purchase order in `sent` is in the
  wholesaler's hands, yet the receipt it will need is ours to post, so that
  counts too.
- **Count only sections the role can act on.** A warehouse user has no intake
  queue; handing them that number would be a number they cannot clear.
- **One number per section, meaning one thing**, and the section's page must
  carry the action that clears it.

Each count mirrors the predicate of the page it badges. Where a service already
owns that predicate, this module calls the service rather than re-writing the
query — a badge that disagrees with its page is worse than no badge.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Delivery, Invoice, Order, PurchaseOrder, User
from app.services.identity import service as identity_svc
from app.services.intake import service as intake_svc
from app.services.orders import orders as orders_svc
from app.services.warehouse import inbound as inbound_svc
from app.services.warehouse import picklists as pick_svc


# Which role can act on which section. Mirrors the `roles` list on each nav item
# in `AdminLayout.MENU_GROUPS` — if the two ever disagree, someone gets a badge
# for a page they cannot open, which is the failure this map exists to prevent.
# A test asserts the two lists match.
ROLE_SECTIONS: dict[str, set[str]] = {
    "intake": {"admin", "ops"},
    "orders": {"admin", "ops", "finance"},
    "consolidation": {"admin", "ops"},
    "purchase_orders": {"admin", "ops"},
    "identity_chats": {"admin", "ops"},
    "inbound": {"admin", "finance"},
    "pick_lists": {"admin", "warehouse"},
    "delivery": {"admin", "warehouse", "driver"},
    "invoices": {"admin", "finance"},
}


def collect(db: Session, *, user: User) -> dict[str, int]:
    """Every section this user can act on, and how much is waiting in it.

    Sections the role cannot act on are omitted rather than returned as zero:
    the client badges what it is given, and a key present with 0 would invite a
    badge that is permanently empty for that role.
    """
    role = user.role
    sections = {name for name, roles in ROLE_SECTIONS.items() if role in roles}
    out: dict[str, int] = {}

    if "intake" in sections:
        # Gate 1/2 — has a human read the extraction? `parked` is excluded on
        # purpose; see `count_pending_review`.
        out["intake"] = intake_svc.count_pending_review(db)

    if "orders" in sections:
        # Gate 3 — has a human approved the order?
        out["orders"] = orders_svc.count_awaiting_confirmation(db)

    if "identity_chats" in sections:
        # Gate 0 — which customer is this conversation? Counted in chats, not
        # messages, because one chat is one decision.
        out["identity_chats"] = identity_svc.count_unbound_chats(db)

    if "consolidation" in sections:
        # `run_consolidation` consumes exactly the confirmed orders for a date;
        # anything else is already in a batch or not yet confirmed. The action
        # is "run consolidation for that date".
        out["consolidation"] = (
            db.query(Order).filter(Order.status == "confirmed").count()
        )

    if "purchase_orders" in sections:
        # Drafted and never sent. The action is "send it to the wholesaler" —
        # until then nothing is on order and nobody is expecting a truck.
        out["purchase_orders"] = (
            db.query(PurchaseOrder).filter(PurchaseOrder.status == "draft").count()
        )

    if "inbound" in sections:
        # Sent or partly received: goods we are owed, and the receipt is ours to
        # post. `received`/`closed` are done; `draft` is not ordered yet and is
        # counted on the Purchase Orders item instead. Counting `sent` is
        # deliberate even though it is partly waiting on the wholesaler — the
        # ERP has no "the truck has arrived" signal, so this is the only place
        # that obligation can be visible.
        #
        # The count comes from the service that renders the page, NOT from a
        # query written here. It used to be a hand-written status filter in this
        # function, next to an Inbound page that listed a different table
        # entirely: the badge read 2 and the screen was empty, and nothing tied
        # the two together so nothing could notice.
        out["inbound"] = inbound_svc.count_awaiting_receipts(db)

    if "pick_lists" in sections:
        # Items still to pick. Exactly the rows on the Pick Lists screen that
        # carry a Pick button, so it falls to zero when the work is done.
        out["pick_lists"] = pick_svc.count_pick_tasks(db)["open_lines"]

    if "delivery" in sections:
        # Anything not finished successfully: scheduled and picked still need
        # dispatch, out_for_delivery needs the completion step (the human
        # verification), and failed needs a human to recover it.
        q = db.query(Delivery).filter(
            Delivery.status.notin_(("delivered", "partial"))
        )
        if role == "driver":
            # A driver may only see their own, so badging the depot's whole
            # board would show a number they have no way to clear.
            q = q.filter(Delivery.driver_id == user.id)
        out["delivery"] = q.count()

    if "invoices" in sections:
        # Delivered and verified, but no invoice exists. Normally zero, because
        # `delivery.completed` auto-invoices — non-zero means auto-invoice is
        # switched off or the handler failed, which is exactly when a human has
        # to notice. The action is "generate the invoice".
        #
        # `order_id.is_not(None)` is not decoration: `invoices.order_id` is
        # nullable, and a NOT IN over a set containing NULL matches nothing in
        # SQL. Without it this count is silently always 0.
        invoiced_order_ids = db.query(Invoice.order_id).filter(
            Invoice.status != "void",
            Invoice.order_id.is_not(None),
        )
        out["invoices"] = (
            db.query(Order)
            .filter(
                Order.status == "fulfilled",
                ~Order.id.in_(invoiced_order_ids),
            )
            .count()
        )

    return out
