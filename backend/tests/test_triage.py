"""Gate 1 triage: "is this message an order at all?"

The bar these tests set: a real order must never be dropped (a false negative
is lost revenue), and the everyday chatter that currently floods the inbox
must be caught (a false positive costs a reviewer one glance).

Deliberately included: greetings that *open* an order ("你好，明天要 50斤土豆")
must still be orders. That is the trap a naive keyword filter falls into.
"""
from __future__ import annotations

import pytest

from app.services.intake.triage import (
    NOT_ORDER,
    ORDER,
    UNCLEAR,
    classify_message,
)


def classify(text, **kw):
    return classify_message(text=text, msgtype=kw.pop("msgtype", "text"), **kw)


# --- Chatter that must NOT become an order -----------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "您好",
        "早上好",
        "在吗",
        "老板在吗",
        "好的",
        "收到",
        "收到了",
        "谢谢",
        "谢谢老板",
        "辛苦了",
        "好的收到",
        "嗯嗯",
        "ok",
        "OK",
        "不好意思",
        "稍等",
        "   ",
        "",
        None,
        "\U0001f44d",  # thumbs up only
        "\U0001f600\U0001f600\U0001f600",
        "明天几点送？",
        "多少钱一斤？",
        "货到了吗",
        "你们什么时候上班",
        "早上好老板",
        "你好，请问土豆多少钱一斤",
        "稍等，我问下厨房",
    ],
)
def test_chatter_is_not_an_order(text):
    v = classify(text)
    assert v.decision == NOT_ORDER, f"{text!r} → {v.decision} (score {v.score}: {v.reasons})"


# --- Real orders that must survive -------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "土豆 50斤\n大白菜 30斤\n五花肉 20斤\n大米 2袋",
        "土豆50斤",
        "大白菜30斤，土豆20斤",
        "明天要 50斤土豆",
        "你好，明天要 土豆50斤 大白菜30斤",
        "麻烦明天早上送 100斤白菜",
        "老板，今天下午补 10箱矿泉水",
        "需要：鸡蛋 5箱，油 2桶",
        "下单：五花肉 20斤",
        "加 5袋大米",
        "請送 30斤土豆",
        "50斤土豆",
        "土豆 50",
        "明天几点能送到？要 50斤土豆",  # question, but carries a quantity
    ],
)
def test_real_orders_are_orders(text):
    v = classify(text)
    assert v.decision in (ORDER, UNCLEAR), (
        f"{text!r} → {v.decision} (score {v.score}: {v.reasons}) — a real order "
        "must never be dropped"
    )


# --- A greeting that merely opens an order ------------------------------------

def test_greeting_prefix_does_not_disqualify_an_order():
    """The classic false negative: matched on the whole body, not as substring."""
    assert classify("你好").decision == NOT_ORDER
    assert classify("你好，明天要 50斤土豆").decision in (ORDER, UNCLEAR)
    assert classify("收到，另外加 10斤排骨").decision in (ORDER, UNCLEAR)


# --- Attachments are orders ---------------------------------------------------

def test_attachment_is_an_order_even_without_text():
    v = classify_message(text=None, msgtype="image", has_attachment=True, filename="order.jpg")
    assert v.decision == ORDER
    assert v.tier == "tier0"
    assert v.signals["has_attachment"] is True


def test_attachment_beats_empty_text():
    """A photo of a handwritten list has no body text — that is normal."""
    v = classify_message(text="", msgtype="file", has_attachment=True, filename="清单.xlsx")
    assert v.decision == ORDER


def test_unfetchable_attachment_with_no_text_is_not_silently_an_order():
    """No bytes resolved → we only have text to judge, so judge the text."""
    v = classify_message(text=None, msgtype="image", has_attachment=False)
    assert v.decision == NOT_ORDER


# --- Scoring behaviour --------------------------------------------------------

def test_quantity_unit_is_the_strongest_signal():
    v = classify("土豆 50斤")
    assert v.signals.get("qty_units"), "quantity+unit was not detected"
    assert v.score >= 4, v.reasons


def test_multiple_lines_add_weight():
    one = classify("土豆")
    many = classify("土豆 50斤\n大白菜 30斤\n五花肉 20斤")
    assert many.score > one.score


def test_question_penalty_applies_only_without_quantity():
    assert classify("几点送？").decision == NOT_ORDER
    # Same question, but it carries an order → must survive.
    assert classify("几点送？要 50斤土豆").decision in (ORDER, UNCLEAR)


def test_full_width_digits_are_normalized():
    """ Customers type ５０ on Chinese IMEs; "５０斤" must still count."""
    v = classify("土豆 ５０斤")
    assert v.decision in (ORDER, UNCLEAR), f"full-width digits missed: {v.reasons}"


# --- Robustness ---------------------------------------------------------------

def test_classifier_never_raises_and_defaults_to_order():
    """A triage bug must never lose a message — unclear is treated as an order."""
    v = classify_message(text=object())  # type: ignore[arg-type]
    assert v.is_order is True, "on failure triage must default to treating it as an order"


def test_verdict_serializes_for_storage():
    d = classify("土豆 50斤").as_dict()
    assert set(d) == {"decision", "score", "tier", "reasons", "signals"}
    assert isinstance(d["score"], int)
    assert isinstance(d["reasons"], list)


# --- Shadow mode: the ingest path records but does not change behaviour -------

SERVICE_HEADERS = {"X-ERP-Service-Key": "dev-service-key"}


def _post_wecom(client, msgid: str, content: str) -> dict:
    r = client.post(
        "/api/v1/intake/wecom",
        json={"msgid": msgid, "msgtype": "text", "content": content},
        headers=SERVICE_HEADERS,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_shadow_mode_records_verdict_but_still_creates_a_job(client):  # noqa: F811
    """Shadow must be invisible: the job is created exactly as before."""
    body = _post_wecom(client, "wm-triage-shadow-1", "你好")
    assert body["job_id"], "shadow mode must not change behaviour"

    r = client.get(
        "/api/v1/intake/wecom/triage-report", headers={"X-ERP-Service-Key": "dev-service-key"}
    )
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["mode"] == "shadow"
    assert report["counts"].get("not_order", 0) >= 1


def test_shadow_report_lists_what_would_be_parked(client):  # noqa: F811
    _post_wecom(client, "wm-triage-shadow-2", "谢谢老板")
    report = client.get(
        "/api/v1/intake/wecom/triage-report", headers=SERVICE_HEADERS
    ).json()

    excerpts = [p["excerpt"] for p in report["would_park"]]
    assert "谢谢老板" in excerpts, f"chatter not listed as would-park: {excerpts}"


def test_shadow_report_does_not_list_real_orders(client):  # noqa: F811
    _post_wecom(client, "wm-triage-shadow-3", "土豆 50斤\n大白菜 30斤")
    report = client.get(
        "/api/v1/intake/wecom/triage-report", headers=SERVICE_HEADERS
    ).json()

    excerpts = [p["excerpt"] for p in report["would_park"]]
    assert "土豆 50斤\n大白菜 30斤" not in excerpts, "a real order must never be parked"
