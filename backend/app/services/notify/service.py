"""Outbound notification dispatcher (docs/WECOM_CONTRACTS.md §7).

The ERP never talks to WeCom directly — it POSTs a rendered-template request to
the WeCom Gateway at `{wecom_gateway_url}/wecom/send`, authenticated with the
shared `X-Gateway-Key`.

This module must NEVER raise: a dead or misconfigured gateway may degrade
customer messaging but must never break order confirmation, delivery or
invoicing.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SystemSetting

logger = logging.getLogger("erp.notify")

_TIMEOUT_SECONDS = 5.0
_DEFAULT_LOCALE = "zh"
_VALID_LOCALES = ("zh", "en")


def _json_safe(value: Any) -> Any:
    """Coerce a payload into something httpx can json-encode (never raises)."""
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    if isinstance(value, (datetime, date, UUID, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _setting_value(db: Session, key: str) -> Any:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else None


def _customer_locale(db: Session, customer_id: str | None) -> str:
    """Pick `zh` (default) or `en` for a customer. Simple and non-throwing.

    Signal: the `default_notify_locale` system setting, optionally overridden
    per customer by a `notify_locale:<customer_id>` setting. Anything unknown
    falls back to `zh`.
    """
    try:
        if db is None or not customer_id:
            return _DEFAULT_LOCALE
        keys = ["default_notify_locale", f"notify_locale:{customer_id}"]
        rows = db.query(SystemSetting).filter(SystemSetting.key.in_(keys)).all()
        by_key = {r.key: r.value for r in rows}

        for key in (f"notify_locale:{customer_id}", "default_notify_locale"):
            val = by_key.get(key)
            if isinstance(val, str) and val.strip().lower() in _VALID_LOCALES:
                return val.strip().lower()
    except Exception:  # noqa: BLE001 — locale is cosmetic, never fatal
        logger.debug("Could not resolve notify locale for customer %s", customer_id)
    return _DEFAULT_LOCALE


def _summary_for(template: str, result: dict) -> str:
    """The one-line audit summary. Truncated, because the column is 500 chars.

    A WeCom refusal arrives with a hint id, the source IP and a documentation
    URL bolted on the end, so the raw string can be long. `AuditLog.summary` is
    `String(500)`, so an unclipped reason would fail the audit write — and this
    module swallows write failures by design, so the row would just vanish.
    The full text is still kept in `after["detail"]`.
    """
    status = str(result.get("status") or "unknown")
    detail = result.get("error") or result.get("reason")
    text = f"{template} -> {status}"
    if detail:
        text += ": " + str(detail)[:400]
    return text[:500]


def _record(
    *,
    template: str,
    customer_id: str | None,
    order_id: str | None,
    chat_id: str | None,
    result: dict,
) -> None:
    """Leave a trace of the attempt in the audit trail. Never raises.

    Every caller of `notify()` drops its return value, so a send that failed or
    was skipped leaves no evidence anywhere in the ERP — and when the gateway
    itself is down, the gateway's own log does not exist either. A row here is
    what turns "the customer never got a message" from a guess into a lookup:
    System → Audit, filtered on entity type `Notification`.

    Uses its own session on purpose. These handlers run from `emit()`, which the
    endpoints call *after* `db.commit()`, and the request session is then closed
    without another commit — a row added to `db` would be flushed and thrown away.
    """
    try:
        from app.core.audit import log_audit
        from app.core.database import SessionLocal

        status = str(result.get("status") or "unknown")
        detail = result.get("error") or result.get("reason")
        with SessionLocal() as audit_db:
            log_audit(
                audit_db,
                None,
                "Notification",
                order_id or customer_id or chat_id or "system",
                "send",
                after={
                    "template": template,
                    "status": status,
                    "order_id": order_id,
                    "customer_id": customer_id,
                    "chat_id": chat_id,
                    "detail": detail,
                },
                summary=_summary_for(template, result),
            )
            audit_db.commit()
    except Exception:  # noqa: BLE001 — a broken audit write must not break an order
        logger.debug("Could not record notify(%s) outcome", template, exc_info=True)


def notify(
    db: Session,
    *,
    template: str,
    customer_id: str | None,
    payload: dict,
    order_id: str | None = None,
    locale: str = "zh",
    chat_id: str | None = None,
) -> dict:
    """Ask the WeCom Gateway to send `template` to `customer_id`.

    `chat_id` targets an internal group instead of a customer — used for ops
    alerts ("an order is waiting for review"), which must reach the team rather
    than the person who placed the order.

    Returns one of:
      {"status": "disabled"}                       notify_enabled is false
      {"status": "skipped", "reason": ...}         nothing to send / no destination
      {"status": "sent",    "response": {...}}     gateway accepted
      {"status": "failed",  "error": "..."}        gateway unreachable or rejected

    Never raises. Every attempt — including a failure — is written to the audit
    trail by `_record`, because the caller always discards this return value.
    """
    result = _send(
        db,
        template=template,
        customer_id=customer_id,
        payload=payload,
        order_id=order_id,
        locale=locale,
        chat_id=chat_id,
    )
    _record(
        template=template,
        customer_id=customer_id,
        order_id=order_id,
        chat_id=chat_id,
        result=result,
    )
    return result


def _send(
    db: Session,
    *,
    template: str,
    customer_id: str | None,
    payload: dict,
    order_id: str | None = None,
    locale: str = "zh",
    chat_id: str | None = None,
) -> dict:
    """The actual gateway call. See `notify` for the contract."""
    try:
        if not settings.notify_enabled:
            return {"status": "disabled"}
        if not customer_id and not chat_id:
            return {"status": "skipped", "reason": "no destination"}

        url = f"{(settings.wecom_gateway_url or '').rstrip('/')}/wecom/send"
        body = {
            "template": template,
            "customer_id": customer_id,
            "chat_id": chat_id,
            "order_id": order_id,
            "locale": locale if locale in _VALID_LOCALES else _DEFAULT_LOCALE,
            "payload": _json_safe(payload or {}),
        }

        with httpx.Client(trust_env=False, timeout=_TIMEOUT_SECONDS) as client:
            resp = client.post(
                url,
                json=body,
                headers={"X-Gateway-Key": settings.wecom_gateway_key or ""},
            )

        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 — non-JSON body is not fatal
            data = {}

        if resp.status_code >= 400:
            logger.warning(
                "notify(%s) rejected by gateway: HTTP %s %s",
                template, resp.status_code, data,
            )
            return {"status": "failed", "error": f"HTTP {resp.status_code}: {data}"}

        out = {"status": str(data.get("status") or "sent"), "response": data}
        # A WeCom-level refusal arrives as HTTP 200 with the failure inside the
        # body — `{"status": "failed", "error": "appchat/send failed: 60020
        # ..."}`. Left nested, `notify()` reports the status and drops the
        # reason, so the audit row says "failed" with no detail and the one
        # fact that explains why the customer was not told is gone. Lift it.
        reason = data.get("error") or data.get("reason")
        if reason:
            out["error"] = reason
        return out
    except Exception as exc:  # noqa: BLE001 — a dead gateway must never break the ERP
        logger.warning("notify(%s) failed: %s", template, exc)
        return {"status": "failed", "error": str(exc)}
