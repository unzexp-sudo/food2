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


def notify(
    db: Session,
    *,
    template: str,
    customer_id: str | None,
    payload: dict,
    order_id: str | None = None,
    locale: str = "zh",
) -> dict:
    """Ask the WeCom Gateway to send `template` to `customer_id`.

    Returns one of:
      {"status": "disabled"}                       notify_enabled is false
      {"status": "skipped", "reason": ...}         nothing to send / no customer
      {"status": "sent",    "response": {...}}     gateway accepted
      {"status": "failed",  "error": "..."}        gateway unreachable or rejected

    Never raises.
    """
    try:
        if not settings.notify_enabled:
            return {"status": "disabled"}
        if not customer_id:
            return {"status": "skipped", "reason": "no customer"}

        url = f"{(settings.wecom_gateway_url or '').rstrip('/')}/wecom/send"
        body = {
            "template": template,
            "customer_id": customer_id,
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

        return {"status": str(data.get("status") or "sent"), "response": data}
    except Exception as exc:  # noqa: BLE001 — a dead gateway must never break the ERP
        logger.warning("notify(%s) failed: %s", template, exc)
        return {"status": "failed", "error": str(exc)}
