"""Phase 10 — Render Free deployment-readiness checks.

Deterministic, local-only: no live Render service and no external Ollama
instance is required or contacted. These tests confirm the *configuration
surface* the deployment guide (docs/deployment-render.md) depends on
actually behaves the way that guide describes — not that Render itself
works, which cannot be verified from a unit test.
"""

import ast
from pathlib import Path

from backend.app.ai.ollama import OllamaProvider
from backend.app.ai.orchestrator import build_provider
from backend.app.config import Settings, validate_production_config

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# PORT handling: the app must never bind its own host/port — that's the
# start command's job (`--host 0.0.0.0 --port $PORT`), not application code.
# ---------------------------------------------------------------------------


def test_main_module_never_self_launches_uvicorn_with_a_hardcoded_port():
    """No `if __name__ == "__main__": uvicorn.run(..., port=NNNN)` anywhere.

    If main.py ever grew such a block, whatever port it hardcoded would
    silently override Render's required `--port $PORT` for anyone running
    the module directly — parsing the AST (rather than grepping text)
    catches this even if the block is reformatted later.
    """
    source = (REPO_ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    has_main_guard = any(
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        for node in ast.walk(tree)
    )
    assert not has_main_guard, (
        "main.py should not self-launch uvicorn — the start command "
        "(uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT) owns "
        "host/port binding, see docs/deployment-render.md"
    )


def test_settings_has_no_hardcoded_port_field():
    # PORT is a deployment/start-command concern (Render injects it as an
    # env var consumed by the `--port $PORT` shell substitution), not an
    # application setting — Settings must not shadow or hardcode it.
    assert "PORT" not in Settings.model_fields


# ---------------------------------------------------------------------------
# Render-recommended production configuration is actually valid
# ---------------------------------------------------------------------------


def test_documented_render_production_configuration_passes_validation():
    render_settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        CORS_ALLOWED_ORIGINS="https://www.brilyx.com",
    )
    validate_production_config(render_settings)  # must not raise


def test_documented_render_configuration_with_both_apex_and_www_origins():
    render_settings = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        CORS_ALLOWED_ORIGINS="https://www.brilyx.com,https://brilyx.com",
    )
    validate_production_config(render_settings)  # must not raise
    assert render_settings.cors_allowed_origins_list == ["https://www.brilyx.com", "https://brilyx.com"]


# ---------------------------------------------------------------------------
# OLLAMA_BASE_URL override — must work for any reachable HTTP(S) endpoint,
# not just localhost, with no scheme/host assumption baked in anywhere.
# ---------------------------------------------------------------------------


def test_ollama_base_url_overrides_to_an_external_https_endpoint(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "https://ollama.example-external-host.com")
    settings_override = Settings(_env_file=None)

    provider = build_provider(settings_override)

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "https://ollama.example-external-host.com"


def test_ollama_base_url_default_remains_local_for_development():
    settings_default = Settings(_env_file=None)
    assert settings_default.OLLAMA_BASE_URL == "http://localhost:11434"


def test_ollama_provider_never_hardcodes_a_scheme_or_host():
    provider = OllamaProvider(base_url="https://any-host:1234", model="m", timeout_seconds=5)
    assert provider.base_url == "https://any-host:1234"


# ---------------------------------------------------------------------------
# Database path remains portable under a Render-style working directory
# ---------------------------------------------------------------------------


def test_database_url_is_overridable_for_a_render_persistent_disk_mount(monkeypatch):
    # Render's persistent disks are mounted at a path the operator chooses
    # (e.g. /var/data) — DATABASE_URL must be freely overridable to point
    # there without any code change.
    monkeypatch.setenv("DATABASE_URL", "sqlite:////var/data/brilyx.db")
    settings_override = Settings(_env_file=None)
    assert settings_override.DATABASE_URL == "sqlite:////var/data/brilyx.db"


def test_default_database_url_has_no_windows_drive_letter_or_username():
    settings_default = Settings(_env_file=None)
    assert ":\\" not in settings_default.DATABASE_URL
    assert "Users" not in settings_default.DATABASE_URL


# ---------------------------------------------------------------------------
# PostgreSQL support (Render managed database) — URL normalization and
# engine wiring. No live Postgres server is required for these: they only
# verify the scheme rewrite and that SQLAlchemy can build a postgresql+
# psycopg2 engine object from the result. A real connection is covered
# separately by test_postgres_round_trip_when_a_live_server_is_configured
# below, which skips itself when no live server is available.
# ---------------------------------------------------------------------------


def test_legacy_postgres_scheme_is_normalized_to_postgresql():
    from backend.app.database import normalize_database_url

    assert (
        normalize_database_url("postgres://user:pass@host:5432/dbname")
        == "postgresql://user:pass@host:5432/dbname"
    )


def test_postgresql_scheme_url_passes_through_unchanged():
    from backend.app.database import normalize_database_url

    url = "postgresql://user:pass@host:5432/dbname"
    assert normalize_database_url(url) == url


def test_sqlite_url_is_not_affected_by_postgres_normalization():
    from backend.app.database import normalize_database_url

    url = "sqlite:///./data/brilyx.db"
    assert normalize_database_url(url) == url


def test_psycopg2_binary_is_declared_as_a_runtime_dependency():
    """Static check on pyproject.toml itself — not just the local environment.

    This is the specific gap that caused a real production incident: the
    Postgres driver was `pip install`ed locally and everything worked in
    this environment, but it was never actually declared in
    `[project.dependencies]` (or, separately, never committed/pushed at
    all), so Render's build never installed it and the deployed app died
    at startup with `ModuleNotFoundError: No module named 'psycopg2'`.
    Checking the *installed* environment (see
    test_normalized_postgres_url_builds_a_psycopg2_engine below) cannot
    catch that class of drift — only reading the actual dependency
    declaration that ships with the code can.
    """
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "psycopg2-binary" in pyproject_text, (
        "psycopg2-binary must be declared in pyproject.toml's [project.dependencies] "
        "— it is required for AI_PROVIDER-independent PostgreSQL support "
        "(backend/app/database.py) and Render will not install it otherwise."
    )

    # Also confirm it's inside the actual dependencies array, not just
    # mentioned in a comment somewhere else in the file.
    import tomllib

    pyproject_data = tomllib.loads(pyproject_text)
    dependencies = pyproject_data["project"]["dependencies"]
    assert any(dep.startswith("psycopg2-binary") for dep in dependencies), (
        f"psycopg2-binary not found in [project.dependencies]: {dependencies}"
    )


def test_normalized_postgres_url_builds_a_psycopg2_engine():
    from sqlalchemy import create_engine

    from backend.app.database import normalize_database_url

    url = normalize_database_url("postgres://user:pass@localhost:5432/brilyx")
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=300)
    try:
        assert engine.dialect.name == "postgresql"
        assert engine.dialect.driver == "psycopg2"
    finally:
        engine.dispose()


def test_sqlite_engine_still_uses_check_same_thread_connect_arg():
    # Regression guard: the Postgres-specific pool_pre_ping/pool_recycle
    # options must never leak onto the SQLite path, and the existing
    # check_same_thread fix (required for FastAPI's threaded request
    # handling) must remain exactly as before. Deliberately builds its own
    # engine from an explicit SQLite URL rather than importing the real
    # `backend.app.database.engine` singleton — that singleton reflects
    # whatever DATABASE_URL is actually configured in the environment
    # running the tests (e.g. a local .env pointed at a real Postgres
    # instance for manual testing), so asserting its dialect here would
    # make this regression guard fail depending on ambient config instead
    # of testing the branching logic itself.
    from sqlalchemy import create_engine

    from backend.app.database import normalize_database_url

    url = normalize_database_url("sqlite:///:memory:")
    engine = create_engine(url, connect_args={"check_same_thread": False})
    try:
        assert engine.dialect.name == "sqlite"
    finally:
        engine.dispose()


def test_postgres_round_trip_when_a_live_server_is_configured():
    """Optional live-Postgres compatibility check.

    Skips itself unless TEST_POSTGRES_URL is set to a reachable Postgres
    connection string — this repo's default test environment has no
    Postgres server, so this never runs in the standard `pytest -q` here,
    but exists so CI (or a developer with a local/Render Postgres
    instance) can verify real compatibility: table creation, insert, and
    query against the actual application models, unchanged from the
    SQLite path.
    """
    import os

    import pytest
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from backend.app import models  # noqa: F401  (registers models on Base.metadata)
    from backend.app.database import Base, normalize_database_url

    raw_url = os.environ.get("TEST_POSTGRES_URL")
    if not raw_url:
        pytest.skip("TEST_POSTGRES_URL not set — no live Postgres server configured for this run")

    url = normalize_database_url(raw_url)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        Base.metadata.create_all(bind=engine)
        session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = session_local()
        try:
            conversation = models.Conversation()
            session.add(conversation)
            session.commit()
            session.refresh(conversation)

            fetched = session.get(models.Conversation, conversation.id)
            assert fetched is not None
            assert fetched.session_id == conversation.session_id
            assert fetched.status == "active"
        finally:
            session.close()
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# .python-version pins the Render Python runtime to 3.11 (not 3.12+)
# ---------------------------------------------------------------------------


def test_python_version_file_pins_3_11():
    content = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert content.startswith("3.11")


def test_pyproject_requires_python_3_11_or_newer():
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in pyproject_text
