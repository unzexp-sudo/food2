"""Application settings. Environment variables are prefixed ERP_ (see .env.example)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout: backend/app/core/config.py -> repo root is three levels up from backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


# Substrings that mark a variable as a placeholder someone still has to fill in.
# Deliberately narrow, because this decides whether a credential is reported as
# configured — a false positive would hide a working deployment, which is worse
# than the false negative it is guarding against. A real API key never contains
# any of these.
_PLACEHOLDER_MARKERS = ("replace_me", "paste_", "changeme", "your_api_key", "todo")


def _is_placeholder(value: str | None) -> bool:
    lowered = (value or "").strip().lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ERP_",
        env_file=(".env", str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FoodSupply ERP"
    app_name_zh: str = "食材供应链 ERP"
    debug: bool = True

    # Database (SQLite for dev/demo, PostgreSQL in production)
    database_url: str = f"sqlite:///{BACKEND_DIR / 'erp.db'}"

    # Durability escape hatch. A container filesystem is ephemeral, so the
    # default SQLite file is destroyed on every redeploy — taking every order,
    # customer and conversation binding with it. In production we therefore
    # refuse to start on SQLite unless this is explicitly acknowledged. Set it
    # to true only if you have deliberately accepted that the data is
    # disposable (a demo, a smoke test, a throwaway environment).
    allow_ephemeral_database: bool = False

    # --- Demo seed -----------------------------------------------------------
    # `seed.py` creates five demo users — all with the password "erp123", one of
    # them an admin — plus a demo catalog, customers, wholesalers and contracts.
    # That is exactly what you want on a laptop, and emphatically not what you
    # want on a production database: a publicly-known admin password on a
    # publicly reachable ERP. On ephemeral SQLite the seed was wiped on every
    # deploy; on a durable Postgres it persists indefinitely.
    #
    # None (the default) means "decide from the environment": seed outside a
    # container, never inside one. Set true/false to override explicitly.
    seed_demo_data: bool | None = None

    # Bootstrap the FIRST admin on an otherwise empty database, without seeding
    # any demo data. Both values must be set for this to do anything. Use it to
    # get into a fresh production database, then change the password.
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    # Auth
    secret_key: str = "dev-secret-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 720

    # File storage
    files_dir: str = str(BACKEND_DIR / "data" / "files")

    # AI intake: "mock" (deterministic, offline), "mistral", "aliyun_qwen" or
    # "openai" (a stub — selecting it changes nothing, see OpenAIExtractor).
    ai_provider: str = "mock"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    # --- Aliyun OCR (handwritten Chinese text + table recognition) -------------
    # Used by the AliyunHandwritingExtractor. Recognizes full-page handwriting
    # with per-word confidence + cell coordinates. Get keys from the Aliyun
    # console (OCR API product). https://ocr.console.aliyun.com/
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_region_id: str = "cn-hangzhou"
    aliyun_ocr_endpoint: str = "ocr-api.cn-hangzhou.aliyuncs.com"

    # --- Qwen-VL (Alibaba multimodal) — form-type classifier + structurer -----
    # Used by QwenVLFormTypeClassifier + AliyunQwenExtractor. OpenAI-compatible
    # endpoint (DashScope). https://dashscope.console.aliyun.com/
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_model: str = "qwen-vl-max"
    # Cheaper/faster model used only for the cheap form-type classifier call.
    qwen_form_type_model: str = "qwen-vl-plus"

    # --- Mistral OCR (document AI) — photos + scanned PDFs ---------------------
    # Used by MistralOcrExtractor. One call reads a page and returns markdown,
    # which is then parsed by the same line parser the typed-text path uses.
    # https://docs.mistral.ai/capabilities/document_ai/basic_ocr/
    # Create a key at https://console.mistral.ai/ -> API keys.
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    # The documented alias that tracks the newest OCR model. Pin a dated
    # snapshot (e.g. "mistral-ocr-2512") once the output has been validated.
    mistral_ocr_model: str = "mistral-ocr-latest"

    # --- OCR quality-assurance gates ------------------------------------------
    # A per-field confidence below this is treated as a hard flag (the field is
    # surfaced for human correction). The whole document is routed to human
    # review when ANY field is below this, or when the overall (quantity-
    # weighted) confidence is below `ocr_review_threshold`.
    ocr_field_confidence_floor: float = 0.80
    # Below this overall confidence the document is ALWAYS flagged for review,
    # even if no individual field tripped the floor. Tuned against the eval set
    # in tests/fixtures/handwritten (see tests/test_handwritten_eval.py).
    ocr_review_threshold: float = 0.95
    # Tolerance (absolute, in currency units) for line-amount and order-total
    # math checks. Handwritten decimals are noisy, so leave some slack.
    ocr_math_abs_tol: float = 1.0

    # Require a human to confirm EVERY extraction before a draft order is
    # created — regardless of how confident the extractor is. The business rule
    # is "a wrong order shipping is a disaster", so confidence is never a
    # substitute for a person checking. Set to False only to restore
    # auto-approval of high-confidence documents.
    intake_require_human_review: bool = True

    # HARD RULE: no order is ever processed without a person confirming it.
    # Gate 2 above makes a human approve the *extraction*; this makes a human
    # approve the *order*. Without it, auto-confirm would flip a high-
    # confidence draft straight to "confirmed" with confirmed_by=None —
    # locking contract prices and notifying the customer with nobody's
    # sign-off, including in the middle of the night. Set to False only to
    # restore confidence-based auto-confirmation.
    orders_require_human_confirmation: bool = True

    # HARD RULE: no order is confirmed without a person confirming WHERE it is
    # going. A customer row can carry an address for years without anyone ever
    # checking it, and an order inherits that string silently — the wrong
    # address on a confirmed order is a truck at the wrong gate. Pre-filling
    # `delivery_address` from the customer is a proposal; it is not a
    # confirmation, and it must never satisfy this gate. Set to False only to
    # restore confirming an order with no delivery confirmation at all.
    require_delivery_confirmation: bool = True

    # Gate 1: keep non-orders ("你好", "收到", "谢谢") out of the intake inbox.
    #   "off"     — no classification at all, behave exactly as before
    #   "shadow"  — classify and record the verdict, change NO behaviour
    #   "enforce" — non-orders are parked, only orders create an intake job
    # Default is "shadow" so the verdicts can be reviewed in production before
    # anything starts being hidden from the team.
    intake_triage_mode: str = "shadow"

    # CORS
    cors_origins: str = "http://localhost:5173"

    # --- WeCom Gateway integration (docs/WECOM_CONTRACTS.md §9) ---------------
    # Shared secret accepted as X-ERP-Service-Key on the WeCom intake endpoints.
    service_key: str = "dev-service-key"
    # Where the WeCom Gateway lives; outbound notifications POST here.
    wecom_gateway_url: str = "http://127.0.0.1:8100"
    wecom_gateway_key: str = "dev-gateway-key"
    # Master switch for outbound customer notifications.
    notify_enabled: bool = True
    # Internal WeCom group chat that receives "an order is waiting for review"
    # alerts. Empty means no push is sent — the in-app review queue remains the
    # source of truth, so the order is never lost, just not announced. Empty is
    # the ACCEPTED production state: the operator's own WeCom client surfaces the
    # arrival, so this is not a misconfiguration to repair. See
    # `wecom_ops_chat_id_is_set` for why the flag is still reported.
    wecom_ops_chat_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def service_key_is_default(self) -> bool:
        """True while the intake service key is still the published placeholder.

        `service_key` is accepted as `X-ERP-Service-Key` by every
        `/api/v1/intake/*` endpoint and authenticates as a *system actor* — no
        login, no role check. Its default is committed to this repo, so a
        deployment that never overrode it is guarded by a value anyone who can
        read the repo already knows. Comparing against the field's own default
        avoids a second literal to keep in sync.
        """
        default = type(self).model_fields["service_key"].default
        return (self.service_key or "").strip() == (default or "").strip()

    @property
    def wecom_gateway_key_is_default(self) -> bool:
        """True while the ERP→gateway shared secret is still the placeholder.

        This is the ERP's half of the pair; the gateway's half is
        `WECOM_GATEWAY_SERVICE_KEY`. They must match, so rotating one alone
        401s every outbound notification.
        """
        default = type(self).model_fields["wecom_gateway_key"].default
        return (self.wecom_gateway_key or "").strip() == (default or "").strip()

    @property
    def wecom_gateway_url_is_loopback(self) -> bool:
        """True while outbound notifications are being posted into our OWN container.

        The default is `http://127.0.0.1:8100` — which is the *gateway's* port,
        not ours. On a laptop running both services that happens to work, so the
        default is invisible in development and fatal in production: the ERP is
        its own container, nothing listens on 8100 there, and every customer
        message dies with a connection refused.

        That failure is completely silent from the ERP's side. `notify()` catches
        it, logs a warning nobody reads, and returns — so the order confirms
        normally, the customer hears nothing, and there is no error anywhere in
        the product to explain it. Worse, it is the *default*, so a deployment
        that simply never set the variable looks identical to one that set it
        wrong.

        A loopback host is never a valid production destination for a service in
        another container, so this is a hard tell rather than a heuristic.
        """
        host = (self.wecom_gateway_url or "").strip().lower()
        return (
            host.startswith("http://127.0.0.1")
            or host.startswith("http://localhost")
            or host.startswith("http://[::1]")
        )

    @property
    def wecom_ops_chat_id_is_set(self) -> bool:
        """True when the internal "an order is waiting for review" ping can go out.

        `intake.needs_review` is the ONE notification with no customer on it — it
        is addressed to the ops group, not to a buyer. With `wecom_ops_chat_id`
        empty the handler logs one line at INFO and returns: the job is still
        queued, so nothing is lost, but nothing is pushed either. That mechanism
        is unchanged and is pinned by `test_mandatory_review.py`.

        **Unset is the accepted production state, not a defect.** The operator
        receives an arriving order through their own WeCom client, so no
        server-side ops group is configured, and the in-app review list remains
        the source of truth for what is parked. The banner therefore does not
        list this — a warning that fires on a deliberate setting teaches the
        reader to ignore the banner.

        The field stays on `/api/health` on purpose, and that is the difference
        between dropping an alarm and dropping the instrument: the server-side
        ping really is off, and that fact should stay readable from outside. If
        the client-side notification ever stops arriving, this is the field that
        tells you the ERP was never covering for it.
        """
        return bool((self.wecom_ops_chat_id or "").strip())

    @property
    def mistral_ocr_is_configured(self) -> bool:
        """True when a Mistral key is present AND is not an obvious placeholder.

        Railway rejects an empty variable value, so the only way to "create the
        variable, ready for the key" is to seed it with a placeholder. That
        placeholder must not read as a working configuration: a non-empty string
        is enough for the API to be attempted, but it is not enough for a photo
        to be read — and /api/health is the one place an operator looks to find
        out which of the two they have.
        """
        key = (self.mistral_api_key or "").strip()
        return bool(key) and not _is_placeholder(key)

    @property
    def image_extraction_is_simulated(self) -> bool:
        """True when photos and scanned PDFs would NOT be really read.

        The `mock` provider is deterministic and offline, which is right for
        tests — but for an `image` or a scanned PDF it does not parse anything.
        It returns three canned lines (土豆 50斤 / 大白菜 30斤 / 五花肉 20斤).
        Those are plausible products for this business, so the output looks like
        a real reading of the customer's note.

        The review gate does flag such a document ("line confidence unknown"),
        so it cannot auto-submit — but a reviewer skimming plausible line items
        is the failure mode this flag exists to make visible. Typed text, Excel
        and text-layer PDFs are genuinely parsed under `mock` and are unaffected.

        `openai` counts as simulated too: `OpenAIExtractor` is a stub that
        delegates to the mock extractor and appends a note, so selecting it
        changes nothing.

        A REAL provider that is selected but not configured also counts. The
        distinction matters: `mistral` with no API key does not silently become
        the mock (the factory refuses to build the extractor), but from the
        outside the effect is the same — no photo is being read. Reporting
        `false` there would be a lie in exactly the situation the flag exists
        for, so this checks credentials rather than trusting the provider name.
        It stays a pure string comparison: this runs on the healthcheck path,
        which must not do I/O.
        """
        provider = (self.ai_provider or "mock").strip().lower()
        if provider in ("", "mock", "openai"):
            return True
        if provider == "mistral":
            return not self.mistral_ocr_is_configured
        if provider == "aliyun_qwen":
            return not (
                (self.aliyun_access_key_id or "").strip()
                and (self.aliyun_access_key_secret or "").strip()
                and (self.qwen_api_key or "").strip()
            )
        # An unrecognised provider name falls through to MockExtractor in
        # `get_extractor()`, so it is simulated by definition.
        return True

    def files_path(self, *parts: str) -> Path:
        """Absolute path under the files dir; ensures directories exist."""
        base = Path(self.files_dir)
        target = base.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


settings = Settings()
