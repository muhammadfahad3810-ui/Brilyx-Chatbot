"""Public GitHub release audit — guards against regressions in the things
that actually matter for a safe public repository: `.gitignore` coverage,
`.env.example` staying placeholder-only, and portable (non-machine-
specific) configuration defaults. See docs/deployment.md and
docs/security.md for the full audit narrative — this file exists so a
future edit to `.gitignore` or `.env.example` that reintroduces a risk
fails CI/pytest immediately instead of only being caught by manual review.
"""

from pathlib import Path

from backend.app.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _gitignore_text() -> str:
    return (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")


def _env_example_lines() -> list[str]:
    return (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# .gitignore coverage
# ---------------------------------------------------------------------------


def test_gitignore_covers_the_environment_file():
    assert ".env" in _gitignore_text().splitlines()


def test_gitignore_covers_local_sqlite_databases():
    assert "data/*.db" in _gitignore_text()


def test_gitignore_covers_python_virtualenv_and_caches():
    text = _gitignore_text()
    for pattern in (".venv/", "__pycache__/", "*.pyc", ".pytest_cache/", "*.egg-info/"):
        assert pattern in text, pattern


def test_gitignore_does_not_exclude_required_project_content():
    # A future edit must not accidentally start ignoring the things a
    # cloned repository actually needs.
    text = _gitignore_text()
    for required_visible_path in ("knowledge", "docs", "tests", "pyproject.toml", "README.md", "frontend"):
        assert required_visible_path not in text.splitlines(), required_visible_path


# ---------------------------------------------------------------------------
# .env.example stays placeholder-only
# ---------------------------------------------------------------------------


def test_env_example_has_no_populated_secret_values():
    secret_keys = {
        "SMTP_USERNAME", "SMTP_PASSWORD", "OWNER_NOTIFICATION_EMAIL", "SMTP_FROM_EMAIL",
        "GEMINI_API_KEY", "GROQ_API_KEY", "RESEND_API_KEY", "RESEND_FROM_EMAIL",
    }
    for line in _env_example_lines():
        if "=" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        if key.strip() in secret_keys:
            assert value.strip() == "", f"{key} must stay blank in .env.example, found: {value!r}"


def test_env_example_documents_every_settings_field_with_a_default_override():
    # Every field a deployer might reasonably need to change is at least
    # mentioned in .env.example — this doesn't assert exact values (some
    # are deliberately blank), just that the variable name is documented.
    example_keys = {line.split("=", 1)[0].strip() for line in _env_example_lines() if "=" in line and not line.strip().startswith("#")}
    expected = {
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL",
        "SMTP_USE_TLS", "OWNER_NOTIFICATION_EMAIL", "CORS_ALLOWED_ORIGINS",
        "OLLAMA_BASE_URL", "OLLAMA_MODEL", "DATABASE_URL",
        "AI_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GROQ_API_KEY", "GROQ_MODEL",
        "GROQ_MAX_TOKENS", "GROQ_EXTRACTION_MODEL", "GROQ_EXTRACTION_MAX_TOKENS",
        "RESEND_API_KEY", "RESEND_FROM_EMAIL",
    }
    missing = expected - example_keys
    assert not missing, f".env.example is missing documented variables: {missing}"


# ---------------------------------------------------------------------------
# Portable configuration defaults (no machine-specific assumptions)
# ---------------------------------------------------------------------------


def test_default_database_url_is_a_relative_portable_path():
    settings = Settings(_env_file=None)
    assert settings.DATABASE_URL == "sqlite:///./data/brilyx.db"
    # No absolute path, no drive letter, no username-bearing directory.
    assert ":\\" not in settings.DATABASE_URL
    assert "Users" not in settings.DATABASE_URL


def test_default_ollama_url_is_localhost_not_a_machine_specific_host():
    settings = Settings(_env_file=None)
    assert settings.OLLAMA_BASE_URL == "http://localhost:11434"


def test_no_setting_defaults_to_a_real_looking_credential():
    settings = Settings(_env_file=None)
    assert settings.SMTP_PASSWORD == ""
    assert settings.SMTP_USERNAME == ""
    assert settings.OWNER_NOTIFICATION_EMAIL == ""
    assert settings.GEMINI_API_KEY == ""
    assert settings.GROQ_API_KEY == ""
    assert settings.RESEND_API_KEY == ""


def test_no_env_file_is_present_in_the_repository_root():
    # A tracked .env would mean a real (or real-looking) local
    # configuration file is sitting where `git add .` could pick it up.
    assert not (REPO_ROOT / ".env").exists()
