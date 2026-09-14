from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Brilyx Chatbot"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./data/brilyx.db"

    # "ollama" (local, free, default), "gemini", or "groq" (both cloud,
    # for production — see backend/app/ai/{gemini,groq}.py and
    # docs/ai-engine.md). Switching this never changes business logic:
    # pricing, qualification, lead capture, and every deterministic rule
    # live entirely outside the AI provider layer regardless of which one
    # is selected.
    AI_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b-instruct-q4_K_M"
    OLLAMA_TIMEOUT_SECONDS: float = 60

    # Gemini (Google GenAI SDK). GEMINI_API_KEY is only required when
    # AI_PROVIDER=gemini is actually selected — left blank, Ollama keeps
    # working exactly as before. Never hard-code a real key here or
    # anywhere else; it must come from the environment.
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    GEMINI_TIMEOUT_SECONDS: float = 60

    # Groq (official `groq` SDK — OpenAI-compatible chat completions API).
    # GROQ_API_KEY is only required when AI_PROVIDER=groq is selected.
    # Default model chosen after inspecting the models actually offered by
    # the installed SDK version (see backend/app/ai/groq.py for the full
    # reasoning) — a general-purpose instruction-following model
    # appropriate for a business chatbot, not one of Groq's agentic
    # "compound" models (which can autonomously browse/use tools — not
    # appropriate here, where all facts must come only from the approved
    # Brilyx knowledge base) and not a moderation-only model.
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GROQ_TIMEOUT_SECONDS: float = 60

    # Caps the main chat reply's output length. Without this, Groq reserves
    # a large default output budget against its tokens-per-minute limit for
    # every request (observed in production: a single normal chat request
    # was rejected with HTTP 413 "tokens per minute" even though the actual
    # prompt was well under the limit) — capping max_tokens fixed this
    # without any change to prompt size or model. 800 was chosen after a
    # live stress test across realistic Brilyx conversations at 500/600/
    # 700/800: 500 truncated common scenarios (including lead capture);
    # 600-700 still truncated some detailed/consultative replies; 800
    # reliably completed every common business scenario while still
    # leaving ~1,650 tokens of headroom under the 8,000 TPM ceiling.
    GROQ_MAX_TOKENS: int = 800

    # Structured lead-extraction (backend/app/intelligence/extractor.py) is
    # a narrow, low-stakes task — its output is Pydantic-validated and
    # silently discarded on any failure (falling back to deterministic
    # rules), so a smaller/cheaper/faster model is appropriate here. Using a
    # different model than GROQ_MODEL also matters for the TPM fix above:
    # Groq enforces tokens-per-minute per model, so routing extraction
    # through its own model keeps it from competing with the main chat
    # call's budget.
    GROQ_EXTRACTION_MODEL: str = "openai/gpt-oss-20b"
    # The extraction JSON schema itself (backend/app/intelligence/models.py)
    # is tiny — a handful of enum/short-string fields, a requirements array
    # capped at 5 items, and a confidence float. But GROQ_EXTRACTION_MODEL
    # is a *reasoning* model: Groq counts its hidden reasoning tokens
    # against max_tokens before any visible output is produced. Measured
    # live against this exact model: 130-850+ reasoning tokens depending on
    # message complexity, so a cap sized only for the visible JSON (e.g.
    # ~300) truncates before any content is emitted, silently returning an
    # empty response every time. 1200 was verified live across simple,
    # complex, and minimal messages to reliably leave room for both.
    GROQ_EXTRACTION_MAX_TOKENS: int = 1200

    CHAT_HISTORY_MAX_MESSAGES: int = 20
    CHAT_MAX_MESSAGE_LENGTH: int = 4000

    # Comma-separated list of allowed browser origins for the Phase 7 widget
    # (see docs/frontend-widget.md). Defaults cover common local static-file
    # dev servers only — production deployments must set the real
    # www.brilyx.com origin(s) via environment configuration; this project
    # never hard-codes a guessed production domain.
    CORS_ALLOWED_ORIGINS: str = "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8080,http://localhost:8080"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Phase 8: owner notifications for high-value leads, demo requests, and
    # human-handoff requests, via standard authenticated SMTP (see
    # docs/business-actions.md). Never hard-code credentials or a personal
    # address here — everything comes from the environment, and the app
    # must keep working (notifications simply stay disabled) if any of this
    # is left unset.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_USE_TLS: bool = True
    OWNER_NOTIFICATION_EMAIL: str = ""

    # Resend (HTTPS API, not SMTP) — added because Render's outbound
    # network cannot reach smtp.gmail.com:587 (see
    # backend/app/diagnostics_smtp_tcp.py / the Render SMTP connectivity
    # investigation this followed). Preferred over SMTP whenever configured
    # — see events/service.py::build_notification_provider(). Never
    # hard-code a real key here; set it via the environment only.
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = ""

    # Off by default (Phase 8 spec section 16): every lead creation would
    # otherwise notify the owner, which is noisy — the important
    # notifications are HIGH-VALUE LEAD, DEMO REQUEST, and HUMAN HANDOFF.
    NOTIFY_ON_LEAD_CREATED: bool = False

    # Temporary, opt-in diagnostic (see backend/app/diagnostics_smtp_tcp.py):
    # tests only whether a bare TCP connection to SMTP_HOST:SMTP_PORT
    # succeeds — no TLS, no login, no email. Off by default so it changes
    # nothing unless explicitly enabled; intended to be removed once the
    # Render SMTP connectivity question it was added to answer is resolved.
    SMTP_TCP_DIAGNOSTIC_ENABLED: bool = False

    @property
    def notifications_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.OWNER_NOTIFICATION_EMAIL)

    @property
    def resend_configured(self) -> bool:
        return bool(self.RESEND_API_KEY and self.RESEND_FROM_EMAIL and self.OWNER_NOTIFICATION_EMAIL)

    # Phase 9: lightweight, single-process, in-memory rate limits (see
    # backend/app/rate_limit.py for the mechanism and its documented
    # limitations). Keyed per client IP, never a client-supplied identity.
    CHAT_RATE_LIMIT_MAX_REQUESTS: int = 20
    CHAT_RATE_LIMIT_WINDOW_SECONDS: float = 60
    CONVERSATION_CREATE_RATE_LIMIT_MAX_REQUESTS: int = 10
    CONVERSATION_CREATE_RATE_LIMIT_WINDOW_SECONDS: float = 60

    # Phase 9: defense-in-depth cap on request body size, checked via
    # Content-Length before any JSON parsing/validation — see
    # backend/app/main.py's body-size middleware and docs/security.md for
    # the residual risk this does NOT cover (chunked transfer with no
    # Content-Length header).
    MAX_REQUEST_BODY_BYTES: int = 32_768


settings = Settings()


def validate_production_config(config: Settings) -> None:
    """Fail fast on obviously unsafe production configuration.

    Only runs meaningful checks when ENVIRONMENT=="production" — Phase 9
    spec section 16: "make unsafe settings difficult to deploy
    accidentally," not "restrict local development." Raises RuntimeError
    (never silently downgrades a setting) so a misconfigured production
    deployment fails at startup instead of quietly running unsafely.
    """
    if config.ENVIRONMENT.lower() != "production":
        return

    errors = []
    origins = config.cors_allowed_origins_list
    if not origins:
        errors.append("CORS_ALLOWED_ORIGINS must not be empty in production.")
    if "*" in origins:
        errors.append("CORS_ALLOWED_ORIGINS must not contain '*' in production.")
    if any(origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost") for origin in origins):
        errors.append("CORS_ALLOWED_ORIGINS still contains a local development origin in production.")

    if errors:
        raise RuntimeError("Unsafe production configuration detected: " + " ".join(errors))
