"""DB-backed job queue + a single async worker that drains it.

Two job kinds:
  - process:  run process_activity for one activity.
  - backfill: list an athlete's window activities and enqueue a process job each.

Pacing is enforced at the Strava HTTP layer (app.ratelimit), so the worker just
runs jobs back-to-back and the limiter blocks when the budget is spent."""

import asyncio
import logging

from sqlalchemy import select

from .db import SessionLocal
from .models import Athlete, Job
from .processing import process_activity

log = logging.getLogger("burrito.worker")

POLL_INTERVAL = 2.0  # seconds between queue polls when idle
MAX_ATTEMPTS = 3


def enqueue_process(activity_id: int, athlete_id: int, write: bool = True) -> bool:
    """Queue one activity unless an identical job is already pending/running."""
    with SessionLocal() as session:
        exists = session.scalar(
            select(Job.id).where(
                Job.kind == "process",
                Job.activity_id == activity_id,
                Job.status.in_(("pending", "running")),
            )
        )
        if exists:
            return False
        session.add(
            Job(
                kind="process",
                activity_id=activity_id,
                athlete_id=athlete_id,
                write=write,
            )
        )
        session.commit()
        return True


def enqueue_backfill(athlete_id: int, write: bool = False) -> int:
    """Queue a backfill job for an athlete; returns the job id."""
    with SessionLocal() as session:
        job = Job(kind="backfill", athlete_id=athlete_id, write=write)
        session.add(job)
        session.commit()
        return job.id


def _claim_next() -> Job | None:
    with SessionLocal() as session:
        job = session.scalar(
            select(Job).where(Job.status == "pending").order_by(Job.id).limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        session.commit()
        session.expunge(job)
        return job


def _run_process(session, athlete: Athlete, job: Job) -> str:
    return process_activity(session, athlete, job.activity_id, write=job.write)


def _run_backfill(session, athlete: Athlete, job: Job) -> str:
    # Import here to avoid a module import cycle (backfill -> processing).
    from .backfill import iter_window_activity_ids

    queued = 0
    for activity_id in iter_window_activity_ids(session, athlete):
        if enqueue_process(activity_id, athlete.athlete_id, write=job.write):
            queued += 1
    return f"backfill queued {queued} activities (write={job.write})"


def _run_job(job: Job) -> None:
    with SessionLocal() as session:
        db_job = session.get(Job, job.id)
        athlete = session.get(Athlete, job.athlete_id)
        try:
            if athlete is None:
                raise RuntimeError(f"no athlete {job.athlete_id} (deauthorized?)")
            if job.kind == "backfill":
                outcome = _run_backfill(session, athlete, job)
            else:
                outcome = _run_process(session, athlete, job)
            db_job.status = "done"
            db_job.last_error = outcome
            log.info("job %s (%s): %s", job.id, job.kind, outcome)
        except Exception as exc:  # noqa: BLE001 - record and retry
            db_job.last_error = str(exc)
            db_job.status = "pending" if job.attempts < MAX_ATTEMPTS else "failed"
            log.warning("job %s failed (attempt %s): %s", job.id, job.attempts, exc)
        session.commit()


async def run_worker(stop: asyncio.Event) -> None:
    log.info("worker started")
    while not stop.is_set():
        job = await asyncio.to_thread(_claim_next)
        if job is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            continue
        await asyncio.to_thread(_run_job, job)
    log.info("worker stopped")
