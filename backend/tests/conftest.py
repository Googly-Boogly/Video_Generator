"""Test harness: self-contained SQLite DB + eager Celery + mock generation.

Lets the full API surface be tested with no Postgres, Redis, MinIO, or worker —
just `python -m pytest`. Env is set BEFORE any app module imports so the cached
Settings and the SQLAlchemy engine bind to the temp SQLite database.
"""
import os
import tempfile

_db_fd, _db_path = tempfile.mkstemp(suffix=".sqlite")
os.environ.update(
    MOCK_GENERATION="true",
    DATABASE_URL=f"sqlite:///{_db_path}",
    OPENAI_API_KEY="",
    ANTHROPIC_API_KEY="",
    FAL_KEY="",
    ELEVENLABS_API_KEY="",
)

import pytest
from fastapi.testclient import TestClient

from app import storage as storage_mod
from app.celery_app import celery_app
from app.database import Base, engine, init_db

# Run Celery tasks synchronously, in-process, against the same SQLite DB.
celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)

# In-memory storage shim so asset-producing stages need no MinIO. The app's
# storage helpers are imported lazily at call time, so patching the module
# attributes here is picked up everywhere.
_MEM: dict[str, bytes] = {}
storage_mod.put_bytes = lambda key, data, content_type="application/octet-stream": (_MEM.__setitem__(key, data) or key)
storage_mod.get_bytes = lambda key: _MEM[key]
storage_mod.public_url = lambda key, expires=3600: f"memory://{key}"
storage_mod.delete_object = lambda key: _MEM.pop(key, None)
storage_mod.ensure_bucket = lambda: None


@pytest.fixture()
def storage_mem():
    """The in-memory blob store, for asserting MinIO cleanup."""
    return _MEM


@pytest.fixture(scope="session", autouse=True)
def _schema():
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    # No context manager -> skip the lifespan (avoids MinIO/boto3 at startup).
    from app.main import app

    return TestClient(app)
