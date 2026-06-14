"""Celery application. Every generation step runs here, never in an HTTP request."""
from __future__ import annotations

from celery import Celery
from celery.signals import worker_ready

from .config import settings

celery_app = Celery(
    "storyforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=60 * 60 * 24,
)

# Ensure tasks are registered when the worker boots.
celery_app.autodiscover_tasks(["app"])

from . import tasks  # noqa: E402,F401


@worker_ready.connect
def _recover_orphaned_jobs(**_):
    """On worker boot, clear jobs orphaned by a crash/restart (see fail_orphaned_jobs)."""
    from .database import SessionLocal
    from .jobs_util import fail_orphaned_jobs

    db = SessionLocal()
    try:
        fail_orphaned_jobs(db)
    finally:
        db.close()
