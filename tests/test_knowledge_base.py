from pathlib import Path

import pytest

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

EXPECTED_FILES = [
    "company.md",
    "services.md",
    "pricing.md",
    "faq.md",
    "industries.md",
    "sales_rules.md",
    "demo.md",
]


@pytest.mark.parametrize("filename", EXPECTED_FILES)
def test_knowledge_file_exists_and_is_non_empty(filename):
    path = KNOWLEDGE_DIR / filename
    assert path.is_file(), f"Missing knowledge file: {filename}"
    assert path.read_text(encoding="utf-8").strip(), f"Knowledge file is empty: {filename}"


def test_knowledge_base_doc_exists_and_is_non_empty():
    doc_path = Path(__file__).resolve().parent.parent / "docs" / "knowledge-base.md"
    assert doc_path.is_file(), "Missing docs/knowledge-base.md"
    assert doc_path.read_text(encoding="utf-8").strip(), "docs/knowledge-base.md is empty"
