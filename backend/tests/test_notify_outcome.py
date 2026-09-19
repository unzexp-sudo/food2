"""What the ERP remembers about a send that did not reach the customer.

The gateway answers HTTP 200 and puts a WeCom refusal in the body, so a failed
send looks like a successful HTTP call. If the reason stays nested inside
`response`, the ERP audit row says `order_confirmed -> failed` and nothing else
— the one fact that explains why the customer was never told is dropped.

These tests use the refusal strings production actually returned.
"""
from __future__ import annotations

import pytest

from app.services.notify import service as notify_service

# Verbatim from the gateway outbound log, 2026-09-19.
_ERR_60020 = (
    "appchat/send failed: 60020 not allow to access from your ip, "
    "hint: [1789802062025433068461577], from ip: 152.55.184.241, "
    "more info at https://open.work.weixin.qq.com/devtool/query?e=60020"
)
_ERR_86008 = (
    "appchat/send failed: 86008 you has no privilege to access this chat, "
    "which is created by other agent., hint: [1789804441445414233172574], "
    "from ip: 152.55.184.241, "
    "more info at https://open.work.weixin.qq.com/devtool/query?e=86008"
)


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, resp: _Resp):
        self._resp = resp
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def post(self, url, *, json=None, headers=None):  # noqa: A002 - httpx's kwarg
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._resp


@pytest.fixture
def gateway(monkeypatch):
    """Point notify() at a stub gateway and return the stub's call log."""
    monkeypatch.setattr(notify_service.settings, "notify_enabled", True)
    monkeypatch.setattr(
        notify_service.settings, "wecom_gateway_url", "http://gw.test"
    )
    monkeypatch.setattr(notify_service.settings, "wecom_gateway_key", "k")

    holder: dict[str, _Client] = {}

    def _factory(*_a, **_k):
        return holder["client"]

    monkeypatch.setattr(notify_service.httpx, "Client", _factory)

    def _arm(payload: dict, status_code: int = 200) -> _Client:
        holder["client"] = _Client(_Resp(status_code, payload))
        return holder["client"]

    return _arm


# --- the reason must survive -------------------------------------------------


def test_a_wecom_refusal_keeps_its_reason(gateway):
    gateway({"status": "failed", "error": _ERR_60020})
    out = notify_service._send(None, template="order_confirmed",
                               customer_id="c1", payload={})
    assert out["status"] == "failed"
    assert out["error"] == _ERR_60020


def test_every_refusal_keeps_its_reason_not_just_the_first_one(gateway):
    """60020 (IP not trusted) and 86008 (chat owned by another app) both bite.

    Same shape, different cause — the point of keeping the reason is that the
    next blocker is legible without re-reading the gateway's own log.
    """
    gateway({"status": "failed", "error": _ERR_86008})
    out = notify_service._send(None, template="order_confirmed",
                               customer_id="c1", payload={})
    assert "86008" in out["error"]


def test_a_skipped_send_keeps_its_reason(gateway):
    gateway({"status": "skipped", "error": "no WeCom destination for customer"})
    out = notify_service._send(None, template="order_confirmed",
                               customer_id="c1", payload={})
    assert out["status"] == "skipped"
    assert out["error"] == "no WeCom destination for customer"


def test_a_successful_send_carries_no_error(gateway):
    gateway({"status": "sent", "response": {"errcode": 0}})
    out = notify_service._send(None, template="order_confirmed",
                               customer_id="c1", payload={})
    assert out["status"] == "sent"
    assert "error" not in out


def test_an_http_error_includes_the_status_and_body(gateway):
    gateway({"detail": "bad key"}, status_code=401)
    out = notify_service._send(None, template="order_confirmed",
                               customer_id="c1", payload={})
    assert out["status"] == "failed"
    assert "401" in out["error"]


# --- the audit summary -------------------------------------------------------


def test_the_summary_names_the_reason():
    out = notify_service._summary_for(
        "order_confirmed", {"status": "failed", "error": _ERR_60020}
    )
    assert out.startswith("order_confirmed -> failed: ")
    assert "60020" in out
    assert len(out) <= 500


def test_a_very_long_reason_cannot_overflow_the_summary_column():
    """`AuditLog.summary` is String(500); a long reason must not kill the row."""
    out = notify_service._summary_for(
        "order_confirmed", {"status": "failed", "error": "x" * 5000}
    )
    assert len(out) <= 500
    assert len(out) < 5000
    assert out.endswith("x")


def test_the_summary_says_unknown_when_the_result_is_empty():
    assert notify_service._summary_for("order_confirmed", {}) == (
        "order_confirmed -> unknown"
    )
