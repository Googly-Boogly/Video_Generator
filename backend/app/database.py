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
