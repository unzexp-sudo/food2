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

    def files_path(self, *parts: str) -> Path:
        """Absolute path under the files dir; ensures directories exist."""
        base = Path(self.files_dir)
        target = base.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


settings = Settings()
