from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = PROJECT_ROOT / "knowledge"

REQUIRED_KNOWLEDGE_FILES = [
    "company.md",
    "services.md",
    "pricing.md",
    "faq.md",
    "industries.md",
    "sales_rules.md",
    "demo.md",
]


class KnowledgeLoadError(Exception):
    """Raised when the approved business knowledge cannot be loaded reliably."""


def load_knowledge_base(knowledge_dir: Path = KNOWLEDGE_DIR) -> str:
    """Load and concatenate the approved Brilyx knowledge files.

    Loads files deterministically, in a fixed order, from an explicit
    directory. Fails clearly if any required file is missing rather than
    silently proceeding with partial or nonexistent knowledge.
    """
    sections = []
    missing = []

    for filename in REQUIRED_KNOWLEDGE_FILES:
        path = knowledge_dir / filename
        if not path.is_file():
            missing.append(str(path))
            continue
        content = path.read_text(encoding="utf-8").strip()
        sections.append(f"# --- {filename} ---\n{content}")

    if missing:
        raise KnowledgeLoadError(
            "Missing required knowledge file(s): " + ", ".join(missing)
        )

    return "\n\n".join(sections)
