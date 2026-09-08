"""Finance services package.

Importing this package registers the "delivery.completed" event handler
(auto_invoice.handle_delivery_completed) so main.py importing the finance
router wires it up — same pattern as app.services.orders.
"""
from app.services.finance import auto_invoice  # noqa: F401  (registers the handler at import)
