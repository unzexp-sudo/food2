from tests.conftest import client


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_reports_the_published_default_secrets(client):
    """Both shared secrets ship with defaults that are committed to this repo
    (which is public), and both are accepted as valid credentials. A deployment
    that never overrode them behaves perfectly normally, so the only signal is
    this flag — assert it exists and is honest."""
    from app.core.config import settings

    body = client.get("/api/health").json()
    assert "service_key_is_default" in body
    assert "wecom_gateway_key_is_default" in body

    field_default = type(settings).model_fields["service_key"].default
    assert body["service_key_is_default"] is (
        (settings.service_key or "").strip() == (field_default or "").strip()
    )


def test_health_clears_the_flag_once_a_secret_is_rotated(client, monkeypatch):
    """Guards against the check firing forever on a correctly-configured
    deployment — a warning that cannot be silenced gets ignored."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "service_key", "rotated-intake-secret")
    monkeypatch.setattr(settings, "wecom_gateway_key", "rotated-gateway-secret")

    body = client.get("/api/health").json()
    assert body["service_key_is_default"] is False
    assert body["wecom_gateway_key_is_default"] is False


def test_default_check_reads_the_default_off_the_model():
    """Binds the check to Settings itself, so changing the placeholder in
    config.py cannot leave a stale literal behind in the check."""
    from app.core.config import Settings

    default = Settings.model_fields["service_key"].default
    assert Settings(service_key=default).service_key_is_default is True
    assert Settings(service_key="something-else").service_key_is_default is False


# ---------------------------------------------------------------------------
# Outbound notification routing
# ---------------------------------------------------------------------------
#
# `WECOM_GATEWAY_URL` defaults to the GATEWAY's loopback port. On a laptop
# running both services that works, so the default is invisible in development
# and fatal in production: the ERP is its own container, nothing listens on
# 8100 there, and every customer message dies with a connection refused that
# `notify()` deliberately swallows. The only symptom is "the customer says
# nobody texted them", which is why the value has to be readable from outside.


def test_health_reports_where_outbound_notifications_actually_go(client):
    body = client.get("/api/health").json()
    from app.core.config import settings

    assert body["wecom_gateway_url"] == settings.wecom_gateway_url
    assert body["notify_enabled"] == settings.notify_enabled
    assert "wecom_gateway_url_is_loopback" in body


def test_health_flags_the_loopback_default_as_a_misconfiguration():
    """The shipped default must read as broken, not as fine."""
    from app.core.config import Settings

    default = Settings.model_fields["wecom_gateway_url"].default
    assert Settings(wecom_gateway_url=default).wecom_gateway_url_is_loopback is True


def test_the_loopback_flag_clears_for_every_real_host():
    """A flag that cannot be cleared on a correct deployment gets ignored.

    Also covers the shapes that look like a real URL but are not one.
    """
    from app.core.config import Settings

    for host in (
        "https://wecom1-production-4bc1.up.railway.app",
        "http://gateway.internal:8100",
        "https://wecom.example.com/gateway",
    ):
        assert Settings(wecom_gateway_url=host).wecom_gateway_url_is_loopback is False

    for loopback in (
        "http://127.0.0.1:8100",
        "http://localhost:8100",
        "http://LOCALHOST:8100",
        "http://[::1]:8100",
    ):
        assert Settings(wecom_gateway_url=loopback).wecom_gateway_url_is_loopback is True


def test_health_reports_that_photos_are_not_really_being_read(client, monkeypatch):
    """The `mock` provider does not read an image — it returns three canned
    lines that happen to be plausible products for this business.

    A deployment that never set `ai_provider` therefore looks completely healthy
    while every customer photo produces fabricated line items. Nothing else in
    the system says so, which is exactly why this has to be on /api/health.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "mock")
    body = client.get("/api/health").json()

    assert body["ai_provider"] == "mock"
    assert body["image_extraction_is_simulated"] is True


def test_health_clears_the_flag_once_a_real_vision_provider_is_set(client, monkeypatch):
    """Guards against a warning that can never be silenced — that kind gets
    ignored, and then it protects nothing.

    Note this requires the credentials to be present as well as the provider
    name. A real provider that is *selected but unkeyed* reads no photos either,
    so it does not clear the flag — see the test below.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "aliyun_qwen")
    monkeypatch.setattr(settings, "aliyun_access_key_id", "key-id")
    monkeypatch.setattr(settings, "aliyun_access_key_secret", "key-secret")
    monkeypatch.setattr(settings, "qwen_api_key", "qwen-key")
    body = client.get("/api/health").json()

    assert body["ai_provider"] == "aliyun_qwen"
    assert body["image_extraction_is_simulated"] is False


def test_health_keeps_warning_when_a_real_provider_has_no_credentials(client, monkeypatch):
    """The flag answers "is a photo actually being read?", not "is the provider
    name a real one?".

    A provider selected without credentials is the most likely misconfiguration
    there is — it is the exact state between "I chose Mistral" and "I pasted the
    key". Reporting it as healthy would put the lie in the one place an operator
    goes to find out.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "mistral")
    monkeypatch.setattr(settings, "mistral_api_key", "")
    assert client.get("/api/health").json()["image_extraction_is_simulated"] is True

    monkeypatch.setattr(settings, "mistral_api_key", "sk-live")
    assert client.get("/api/health").json()["image_extraction_is_simulated"] is False


def test_openai_counts_as_simulated_because_it_is_a_stub():
    """`OpenAIExtractor` delegates to `MockExtractor` and appends a note, so
    selecting it changes nothing. Reporting it as "real" would be a lie in the
    one place an operator goes to find out."""
    from app.core.config import Settings

    assert Settings(ai_provider="openai").image_extraction_is_simulated is True
    assert Settings(ai_provider="").image_extraction_is_simulated is True
    assert Settings(ai_provider="MOCK").image_extraction_is_simulated is True
    # A real provider clears the flag only once it can actually read a photo.
    assert Settings(ai_provider="aliyun_qwen").image_extraction_is_simulated is True
    assert Settings(
        ai_provider="aliyun_qwen",
        aliyun_access_key_id="id",
        aliyun_access_key_secret="secret",
        qwen_api_key="key",
    ).image_extraction_is_simulated is False
    assert Settings(ai_provider="mistral").image_extraction_is_simulated is True
    assert Settings(
        ai_provider="mistral", mistral_api_key="sk-live"
    ).image_extraction_is_simulated is False
    # An unrecognised provider falls back to the mock extractor in the factory,
    # so it is simulated by definition rather than by accident.
    assert Settings(ai_provider="gpt5-turbo-max").image_extraction_is_simulated is True


def test_the_mock_image_path_really_does_return_canned_lines(tmp_path):
    """Pins the actual behaviour behind the flag, so the flag cannot drift away
    from what the extractor does.

    This is the test that would have caught the problem: it asserts the mock
    image path ignores the file's contents entirely.
    """
    from app.ai.adapters import MockExtractor

    blank = tmp_path / "order.png"
    blank.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    other = tmp_path / "different.png"
    other.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\xff" * 4096)

    first = MockExtractor().extract(source_type="image", file_path=str(blank))
    second = MockExtractor().extract(source_type="image", file_path=str(other))

    assert [l.product_name for l in first.lines] == ["土豆", "大白菜", "五花肉"]
    assert [(l.product_name, l.quantity) for l in first.lines] == [
        (l.product_name, l.quantity) for l in second.lines
    ], "two different images produced different output — the flag is stale"

    # And the review gate does catch it, so this is a hazard rather than a
    # silent auto-submit.
    from app.ai.adapters import apply_review_gate

    assert apply_review_gate(first).requires_human_review is True


def test_a_scanned_pdf_is_flagged_because_nobody_read_it(tmp_path, monkeypatch):
    """A scanned PDF has no text layer, so the mock provider fabricates lines for
    it exactly as it does for an image — same hazard, same guard.

    `parse_pdf_lines` is what detects the missing text layer, so it is the seam to
    stub: this asserts the flag, not pypdf's PDF handling.
    """
    from app.ai import adapters
    from app.ai.adapters import MockExtractor

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    monkeypatch.setattr(
        adapters, "parse_pdf_lines", lambda p: ([], "scanned_pdf_no_text")
    )

    res = MockExtractor().extract(source_type="pdf", file_path=str(pdf))

    assert res.requires_human_review is True, (
        "a scanned PDF was never read — it must not be able to auto-approve"
    )
    assert [l.product_name for l in res.lines] == ["土豆", "大白菜", "五花肉"]
    assert "mock OCR" in res.parser_notes


def test_a_real_parse_clears_the_fabricated_flag(tmp_path, monkeypatch):
    """The flag must clear once the lines come from a genuine parse, or real
    documents get parked as unread — which is its own kind of failure, and the
    reason the flag is scoped rather than blanket."""
    import sys
    import types

    from app.ai import adapters
    from app.ai.adapters import MockExtractor

    pdf = tmp_path / "order.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    # The text layer is missing, so the canned fallback fires first...
    monkeypatch.setattr(
        adapters, "parse_pdf_lines", lambda p: ([], "scanned_pdf_no_text")
    )

    # ...but the document does carry an order table, so the structured parser
    # replaces the canned lines. pypdf is stubbed because all that is needed from
    # it is some text to hand to the structured parser.
    class _Page:
        def extract_text(self):
            return "任务数 1 广东誉元 采购单位"

    fake_pypdf = types.ModuleType("pypdf")
    fake_pypdf.PdfReader = lambda path: types.SimpleNamespace(pages=[_Page()])
    monkeypatch.setitem(sys.modules, "pypdf", fake_pypdf)

    monkeypatch.setattr(
        adapters,
        "_structured_for",
        lambda text: {
            "variant": "A",
            "lines": [{"product_name": "土豆", "total_quantity": 50, "total_unit": "斤"}],
        },
    )

    res = MockExtractor().extract(source_type="pdf", file_path=str(pdf))

    assert [l.product_name for l in res.lines] == ["土豆"], (
        "the structured parse should have replaced the canned lines"
    )
    assert res.requires_human_review is False, (
        "the document was genuinely parsed — it must not be parked as unread"
    )


# ---------------------------------------------------------------------------
# The review ping is the one notification with no customer on it
# ---------------------------------------------------------------------------
#
# `intake.needs_review` is the only thing that would announce a parked job to
# WeCom, and with `WECOM_OPS_CHAT_ID` unset the handler logs one INFO line and
# returns — the job is still queued, nothing is pushed, and (because the return
# happens before `notify()`) no outbound-log row is written either. That
# behaviour is pinned by
# `test_mandatory_review.py::test_review_push_skipped_when_unconfigured`.
#
# Unset is the ACCEPTED production state, not a defect: the operator's own WeCom
# client surfaces the arrival, and the in-app review list is the source of truth.
# So the banner does not warn about it. What these tests add is that the state
# stays *visible from outside* — dropping the alarm must not mean dropping the
# instrument, because a log line nobody reads is indistinguishable from a feature
# that works, and this field is what tells you the ERP was never covering for a
# client-side notification that stopped arriving.


def test_health_reports_whether_the_review_ping_can_go_out(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "wecom_ops_chat_id", "ops-chat-123")
    assert client.get("/api/health").json()["wecom_ops_chat_id_is_set"] is True


def test_the_review_ping_reads_as_off_on_the_shipped_default(client):
    """The default is empty, so a deployment that never set the variable — the
    state production is actually in — must read as "cannot announce"."""
    from app.core.config import Settings

    default = Settings.model_fields["wecom_ops_chat_id"].default
    assert default == "", "the default changed; this flag's meaning moved with it"
    assert Settings(wecom_ops_chat_id=default).wecom_ops_chat_id_is_set is False
    assert client.get("/api/health").json()["wecom_ops_chat_id_is_set"] is False


def test_the_review_ping_flag_is_not_an_unsilenceable_warning():
    """A flag that cannot be cleared gets ignored, and then it protects nothing.
    Whitespace is not a chat id either — it would be sent as an empty target."""
    from app.core.config import Settings

    assert Settings(wecom_ops_chat_id="ops-chat-123").wecom_ops_chat_id_is_set is True
    assert Settings(wecom_ops_chat_id="   ").wecom_ops_chat_id_is_set is False


def test_the_flag_agrees_with_what_the_handler_does(client, admin_headers, require_review, monkeypatch):
    """Ties the reported flag to the real behaviour rather than to the setting.

    Sets the chat id and asserts BOTH that health reports it and that a parked
    job actually produces a push — so the flag cannot drift away from the code
    the way a hand-maintained status field does.
    """
    import app.services.notify.wecom_notify as wn
    from tests.test_mandatory_review import DEMO_TEXT, _get_customer_id, _wait_for_job

    calls = []
    monkeypatch.setattr(wn, "notify", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(wn.settings, "wecom_ops_chat_id", "ops-chat-123")

    assert client.get("/api/health").json()["wecom_ops_chat_id_is_set"] is True

    r = client.post(
        "/api/v1/intake/submit",
        json={
            "customer_id": _get_customer_id(client, admin_headers),
            "source_type": "text",
            "raw_text": DEMO_TEXT,
        },
        headers=admin_headers,
    )
    job = _wait_for_job(client, r.json()["job_id"], admin_headers)

    assert job["status"] == "needs_review"
    assert [c["template"] for c in calls] == ["intake_needs_review"], (
        "health said the ping was configured, but no ping went out"
    )


# ---------------------------------------------------------------------------
# Gate 1 (intake triage) mode
# ---------------------------------------------------------------------------
#
# The gate shipped with a default of "shadow", which classifies every message
# and then hides nothing. That is indistinguishable from a broken filter from
# the outside: chatter keeps arriving, the code looks correct, and no surface in
# the product said which mode was running. It was read as "the filter does not
# work" for exactly that reason. The mode is now reported, so the next person
# can read the answer instead of inferring it.


def test_health_reports_the_triage_mode(client):
    body = client.get("/api/health").json()
    from app.core.config import settings

    assert body["intake_triage_mode"] == (settings.intake_triage_mode or "").lower()


def test_the_triage_mode_default_is_enforce():
    """The gate is product behaviour, not an experiment left switched off.

    A default of "shadow" is what let non-orders reach the inbox while every
    test stayed green, so the default itself is the thing worth pinning.
    """
    from app.core.config import Settings

    assert Settings().intake_triage_mode == "enforce"


def test_health_follows_the_mode_when_it_is_changed(client, monkeypatch):
    """A field that cannot change is a constant, not a reading."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "intake_triage_mode", "shadow")
    assert client.get("/api/health").json()["intake_triage_mode"] == "shadow"

    monkeypatch.setattr(settings, "intake_triage_mode", "off")
    assert client.get("/api/health").json()["intake_triage_mode"] == "off"
