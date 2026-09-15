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
