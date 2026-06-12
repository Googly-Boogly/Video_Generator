"""Job status polling + SSE stream for live pipeline progress."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..database import SessionLocal, get_db
from ..models import Job
from ..schemas import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@router.get("/project/{project_id}", response_model=list[JobOut])
def jobs_for_project(project_id: str, db: Session = Depends(get_db)):
    return db.scalars(
        select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc())
    ).all()


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Server-Sent Events stream of a job's status until it reaches a terminal state."""

    async def event_gen():
        terminal = {"success", "failed"}
        while True:
            if await request.is_disconnected():
                break
            db = SessionLocal()
            try:
                job = db.get(Job, job_id)
                if not job:
                    yield {"event": "error", "data": json.dumps({"error": "job not found"})}
                    break
                payload = {
                    "id": job.id,
                    "type": job.type,
                    "status": job.status,
                    "progress": job.progress,
                    "result": job.result,
                    "error": job.error,
                }
                yield {"event": "status", "data": json.dumps(payload)}
                if job.status in terminal:
                    break
            finally:
                db.close()
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_gen())
