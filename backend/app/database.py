"""SQLAlchemy engine, session factory, and declarative base."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from .config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables if they don't exist.

    Phase 1 convenience: we create_all on startup so `docker compose up` works
    with zero manual steps. Alembic is configured (backend/alembic) for real
    migrations as the schema evolves.
    """
    from . import models  # noqa: F401  (ensure models are registered)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Lightweight additive migrations for columns added after a table already
    exists (create_all only creates missing *tables*, not columns). Idempotent.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    additions = {
        "projects": [("llm_model", "VARCHAR(64)")],
    }
    with engine.begin() as conn:
        for table, cols in additions.items():
            if table not in insp.get_table_names():
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
