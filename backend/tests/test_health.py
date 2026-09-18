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
    ignored, and then it protects nothing."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "ai_provider", "aliyun_qwen")
    body = client.get("/api/health").json()

    assert body["ai_provider"] == "aliyun_qwen"
    assert body["image_extraction_is_simulated"] is False


def test_openai_counts_as_simulated_because_it_is_a_stub():
    """`OpenAIExtractor` delegates to `MockExtractor` and appends a note, so
    selecting it changes nothing. Reporting it as "real" would be a lie in the
    one place an operator goes to find out."""
    from app.core.config import Settings

    assert Settings(ai_provider="openai").image_extraction_is_simulated is True
    assert Settings(ai_provider="").image_extraction_is_simulated is True
    assert Settings(ai_provider="aliyun_qwen").image_extraction_is_simulated is False
    assert Settings(ai_provider="MOCK").image_extraction_is_simulated is True


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
