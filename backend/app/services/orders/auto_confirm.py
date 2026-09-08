"""Auto-confirm handler for the "order.draft_created" event.

Registered at module import time so that any module which imports the orders
router (main.py does this) wires the handler up.

Rules (per docs/AGENT_CONTRACTS.md §2 and docs/EXECUTIVE_SUMMARY.md Phase 3):
  - Read settings "auto_confirm" {"enabled": true, "min_confidence": 0.95}
  - Read settings "cutoff_time" "18:00"
  - If auto_confirm is disabled → leave status "draft" for manual review.
  - If current local time > cutoff on delivery_date's day → leave as "draft"
    for manual review (cutoff passed).
  - If order.overall_confidence >= min_confidence AND every line has a
    product_id (matched) AND all quantities > 0 → auto-confirm:
      status "confirmed", confirmed_by=None, confirmed_at=now,
      lock contract prices onto lines (same as /confirm), audit "auto_confirm".
  - Otherwise → move to "pending_confirmation" so it lands in the manual pool.

Catches its own exceptions; never raises. Commits on the passed db session.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone

from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.events import emit, on
from app.models import Order, OrderLine, SystemSetting
from app.services.orders.orders import lock_contract_prices

logger = logging.getLogger("erp.orders.auto_confirm")

_DEFAULT_MIN_CONFIDENCE = 0.95
_DEFAULT_CUTOFF = "18:00"


def _get_setting(db: Session, key: str) -> dict | str | None:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else None


def _auto_confirm_settings(db: Session) -> tuple[bool, float]:
    """Returns (enabled, min_confidence) with defaults if missing."""
    val = _get_setting(db, "auto_confirm")
    if isinstance(val, dict):
        enabled = bool(val.get("enabled", True))
        min_conf = float(val.get("min_confidence", _DEFAULT_MIN_CONFIDENCE))
        return enabled, min_conf
    return True, _DEFAULT_MIN_CONFIDENCE


def _cutoff_setting(db: Session) -> time:
    val = _get_setting(db, "cutoff_time")
    if isinstance(val, str):
        try:
            hh, mm = val.split(":", 1)
            return time(hour=int(hh), minute=int(mm))
        except (ValueError, IndexError):
            pass
    return time.fromisoformat(_DEFAULT_CUTOFF)


def _before_cutoff(cutoff: time) -> bool:
    """True if the current local time is at or before cutoff.

    `datetime.now()` returns local wall-clock time; we compare only the
    time-of-day component against the configured cutoff.
    """
    now_local = datetime.now().time()
    return now_local <= cutoff


@on("order.draft_created")
def handle_draft_created(*, db: Session, order: Order) -> None:
    """Auto-confirm evaluation triggered when a draft order is created.

    The handler commits on the passed db session. It must never raise —
    `events.emit` also wraps handlers in try/except, but we catch here so we
    can log a friendlier message and leave the order in a sane state.
    """
    try:
        # Reload the order from the passed session in case the caller's
        # identity map differs (intake pipeline commits first, then emits).
        o = db.get(Order, order.id) if order is not None else None
        if o is None:
            logger.warning("order.draft_created: order not found (id=%s)", order.id)
            return
        if o.status != "draft":
            # Already moved on (e.g. an earlier handler ran). Do nothing.
            return

        # Ensure lines are loaded.
        lines = (
            db.query(OrderLine)
            .filter(OrderLine.order_id == o.id)
            .order_by(OrderLine.line_no)
            .all()
        )

        enabled, min_conf = _auto_confirm_settings(db)
        if not enabled:
            # Leave as draft — manual review required.
            return

        cutoff = _cutoff_setting(db)
        if not _before_cutoff(cutoff):
            # Past today's cutoff — leave for manual review.
            return

        all_matched = all(ln.product_id is not None for ln in lines) and len(lines) > 0
        all_qty_positive = all((ln.quantity or 0) > 0 for ln in lines) and len(lines) > 0
        confidence_ok = (o.overall_confidence or 0.0) >= min_conf

        before = {"status": o.status, "overall_confidence": o.overall_confidence}
        if confidence_ok and all_matched and all_qty_positive:
            # Rules pass — auto-confirm.
            lock_contract_prices(db, o)
            o.status = "confirmed"
            o.confirmed_by = None  # system user
            o.confirmed_at = datetime.now(timezone.utc)
            db.flush()
            log_audit(
                db, None, "Order", o.id, "auto_confirm",
                before=before,
                after={
                    "status": o.status,
                    "confirmed_by": None,
                    "confirmed_at": o.confirmed_at.isoformat() if o.confirmed_at else None,
                },
                summary=(
                    f"Order {o.order_number} auto-confirmed "
                    f"(confidence={o.overall_confidence}, threshold={min_conf})"
                ),
            )
            db.commit()
            # Outbound WeCom notification (docs/WECOM_CONTRACTS.md §11).
            emit("order.confirmed", db=db, order=o)
        else:
            # Rules fail — move to the manual pending pool.
            o.status = "pending_confirmation"
            db.flush()
            log_audit(
                db, None, "Order", o.id, "auto_confirm_to_pending",
                before=before,
                after={"status": o.status},
                summary=(
                    f"Order {o.order_number} moved to pending_confirmation "
                    f"(auto-confirm rules failed)"
                ),
            )
            db.commit()
    except Exception:  # noqa: BLE001 — handler must never break the emitter
        logger.exception("Auto-confirm handler failed for order %s", getattr(order, "id", "?"))
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
