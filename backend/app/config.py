from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "Brilyx Chatbot"
    ENVIRONMENT: str = "development"
    DATABASE_URL: str = "sqlite:///./data/brilyx.db"

    AI_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5:3b-instruct-q4_K_M"
    OLLAMA_TIMEOUT_SECONDS: float = 60

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

    # Off by default (Phase 8 spec section 16): every lead creation would
    # otherwise notify the owner, which is noisy — the important
    # notifications are HIGH-VALUE LEAD, DEMO REQUEST, and HUMAN HANDOFF.
    NOTIFY_ON_LEAD_CREATED: bool = False

    @property
    def notifications_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.OWNER_NOTIFICATION_EMAIL)

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
