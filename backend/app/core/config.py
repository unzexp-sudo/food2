"""Application settings. Environment variables are prefixed ERP_ (see .env.example)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout: backend/app/core/config.py -> repo root is three levels up from backend/
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


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

    # AI intake: "mock" (deterministic, offline) or "openai" or "aliyun_qwen"
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
    # source of truth, so the order is never lost, just not announced.
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
    def image_extraction_is_simulated(self) -> bool:
        """True when photos and scanned PDFs would be read by the FAKE extractor.

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
        """
        return (self.ai_provider or "mock").strip().lower() in ("", "mock", "openai")

    def files_path(self, *parts: str) -> Path:
        """Absolute path under the files dir; ensures directories exist."""
        base = Path(self.files_dir)
        target = base.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


settings = Settings()
