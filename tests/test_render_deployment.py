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
# .python-version pins the Render Python runtime to 3.11 (not 3.12+)
# ---------------------------------------------------------------------------


def test_python_version_file_pins_3_11():
    content = (REPO_ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert content.startswith("3.11")


def test_pyproject_requires_python_3_11_or_newer():
    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.11"' in pyproject_text
