"""Payload shapes the WeCom Gateway renders into customer-facing messages.

The gateway templates read `product_display` / `quantity` / `unit`, so what
this module emits is literally what the customer sees in WeCom. These tests
guard against shipping a message that reads "- x 0.0" or "合计：0.0".
"""
from __future__ import annotations


class _Unit:
    def __init__(self, code: str, name_zh: str | None, name_en: str | None):
        self.code = code
        self.name_zh = name_zh
        self.name_en = name_en


class _Line:
    def __init__(self, unit=None, product_display=None, raw_text=None, quantity=0.0):
        self.unit = unit
        self.product_display = product_display
        self.raw_text = raw_text
        self.quantity = quantity


# ---------------------------------------------------------------------------
# Unit naming
# ---------------------------------------------------------------------------


def test_unit_display_prefers_the_readers_language():
    from app.services.notify.wecom_notify import _unit_display

    ln = _Line(unit=_Unit("jin", "斤", "jin"))
    assert _unit_display(ln, "zh") == "斤"
    assert _unit_display(ln, "en") == "jin"


def test_unit_display_falls_back_through_the_languages_to_the_code():
    from app.services.notify.wecom_notify import _unit_display

    # No English name → fall back to Chinese rather than the raw code.
    ln = _Line(unit=_Unit("jin", "斤", None))
    assert _unit_display(ln, "en") == "斤"
    # No names at all → the code is better than nothing.
    assert _unit_display(_Line(unit=_Unit("jin", None, None)), "zh") == "jin"


def test_unit_display_tolerates_a_missing_unit():
    from app.services.notify.wecom_notify import _unit_display

    assert _unit_display(_Line(), "zh") is None


# ---------------------------------------------------------------------------
# Line rendering contract
# ---------------------------------------------------------------------------


def test_product_display_falls_back_to_what_the_customer_wrote():
    """An unmatched line still has to show something the customer recognises."""
    from app.services.notify.wecom_notify import _product_display

    assert _product_display(_Line(raw_text="白菜 20斤。")) == "白菜 20斤。"
    assert _product_display(_Line(product_display="土豆")) == "土豆"


def test_order_lines_expose_the_keys_the_gateway_template_reads():
    """Regression: the gateway only knew `name`/`unit`, so every line rendered
    as a nameless "- x 0.0". `unit` must carry the display name and
    `unit_code` the machine value.
    """
    from app.services.notify.wecom_notify import _order_lines

    class _Query:
        def filter(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def all(self):
            return [_Line(unit=_Unit("jin", "斤", "jin"), product_display="土豆", quantity=50.0)]

    class _Db:
        def query(self, *a, **k):
            return _Query()

    fake_order = type("Order", (), {"id": "order-1"})()
    lines = _order_lines(_Db(), fake_order, "zh")
    assert lines == [
        {
            "product_display": "土豆",
            "quantity": 50.0,
            "unit": "斤",
            "unit_code": "jin",
        }
    ]


def test_unpriced_order_renders_no_total():
    """`0.0` would tell the customer their order is free; `None` renders `-`."""
    from app.services.notify.wecom_notify import _order_total

    class _Query:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [_Line(quantity=50.0)]  # quantity but no unit_price

    class _Db:
        def query(self, *a, **k):
            return _Query()

    total = _order_total(_Db(), object())
    assert total == 0.0
    # This is the expression the notify handler uses:
    assert (total or None) is None
