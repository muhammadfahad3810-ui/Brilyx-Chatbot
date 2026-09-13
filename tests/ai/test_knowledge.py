import pytest

from backend.app.ai.knowledge import (
    KNOWLEDGE_DIR,
    REQUIRED_KNOWLEDGE_FILES,
    KnowledgeLoadError,
    load_knowledge_base,
)


def test_load_knowledge_base_includes_all_required_files():
    content = load_knowledge_base()

    for filename in REQUIRED_KNOWLEDGE_FILES:
        assert filename in content


def test_load_knowledge_base_uses_real_knowledge_directory_by_default():
    assert KNOWLEDGE_DIR.name == "knowledge"
    assert KNOWLEDGE_DIR.is_dir()


def test_missing_knowledge_file_raises_clear_error(tmp_path):
    # Only create some of the required files, leaving others missing.
    (tmp_path / "company.md").write_text("Brilyx company info", encoding="utf-8")
    (tmp_path / "services.md").write_text("Brilyx services info", encoding="utf-8")

    with pytest.raises(KnowledgeLoadError) as exc_info:
        load_knowledge_base(knowledge_dir=tmp_path)

    message = str(exc_info.value)
    assert "pricing.md" in message
    assert "faq.md" in message


def test_empty_knowledge_directory_raises_error(tmp_path):
    with pytest.raises(KnowledgeLoadError):
        load_knowledge_base(knowledge_dir=tmp_path)
