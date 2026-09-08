"""Tiny in-process pub/sub for cross-module triggers.

Emitted events (see docs/AGENT_CONTRACTS.md §2):
  - "order.draft_created"   (db=..., order=...)     handled by orders module (auto-confirm)
  - "delivery.completed"    (db=..., delivery=...)  handled by finance module (auto-invoice)

Handlers register at import time so main.py's router imports wire everything up.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("erp.events")

_handlers: dict[str, list[Callable[..., Any]]] = {}


def on(event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        _handlers.setdefault(event, []).append(fn)
        return fn

    return register


def emit(event: str, **kwargs: Any) -> list[Any]:
    """Call all handlers for `event`. Handler exceptions are logged, never raised."""
    results = []
    for fn in _handlers.get(event, []):
        try:
            results.append(fn(**kwargs))
        except Exception:  # noqa: BLE001 — handlers must never break the emitter
            logger.exception("Event handler %s failed for event %s", fn, event)
    return results
