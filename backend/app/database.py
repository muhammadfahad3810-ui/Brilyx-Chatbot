from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from backend.app.config import settings


def normalize_database_url(url: str) -> str:
    """Rewrite a legacy `postgres://` scheme to `postgresql://`.

    Some platforms (Render's managed Postgres included) issue connection
    strings starting with `postgres://` — a scheme SQLAlchemy 2.x's
    `postgresql` dialect does not recognize on its own (it raises
    `NoSuchModuleError`). This is the one normalization needed; everything
    else about the URL (host, port, credentials, database name) is passed
    through unchanged.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


DATABASE_URL = normalize_database_url(settings.DATABASE_URL)

if DATABASE_URL.startswith("sqlite"):
    Path("data").mkdir(parents=True, exist_ok=True)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # pool_pre_ping avoids "server closed the connection unexpectedly"
    # errors from a managed Postgres provider silently dropping idle
    # connections; pool_recycle proactively refreshes connections before
    # that can happen. Neither option applies to (or is needed by) SQLite.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=300)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create any tables that don't exist yet.

    create_all() only creates missing tables — it never drops or alters
    existing ones, so this is safe to call on every app startup without
    resetting existing data.
    """
    from backend.app import models  # noqa: F401  (registers models on Base.metadata)

    Base.metadata.create_all(bind=engine)
