import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from . import api, oauth, web, webhook
from .config import settings
from .db import get_session, init_db
from .models import Athlete, Job, SegmentEffort
from .processing import process_activity
from .worker import enqueue_backfill
from .worker import run_worker

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(stop))
    yield
    stop.set()
    await task


app = FastAPI(title="Chattanooga Burrito League", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.include_router(web.router)
app.include_router(api.router)
app.include_router(oauth.router)
app.include_router(webhook.router)


def require_admin(x_admin_token: str | None = Header(default=None)):
    """Guard for /debug/* and admin endpoints. Exposes cross-athlete data, so it
    must never be public (privacy + Strava TOS). Returns 404 to hide existence."""
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(status_code=404, detail="Not Found")


@app.get("/healthz")
def healthz():
    return {"status": "ok", "target_segment_id": settings.target_segment_id}


@app.get("/debug/state", dependencies=[Depends(require_admin)])
def debug_state(session: Session = Depends(get_session)):
    athletes = session.scalars(select(Athlete)).all()
    return {
        "athletes": [
            {"id": a.athlete_id, "consent": a.community_consent,
             "write_mode": a.write_mode, "scopes": a.scopes}
            for a in athletes
        ],
        "efforts": session.scalars(select(SegmentEffort)).all().__len__(),
        "jobs": [
            {"id": j.id, "kind": j.kind, "activity": j.activity_id,
             "status": j.status, "attempts": j.attempts, "last": j.last_error}
            for j in session.scalars(select(Job)).all()
        ],
    }


@app.put("/athlete/{athlete_id}/consent", dependencies=[Depends(require_admin)])
def set_consent(
    athlete_id: int, value: bool, session: Session = Depends(get_session)
):
    """Explicit opt-in/out of the community counter (TOS-required consent).
    A real UI would POST this from a checkbox; headlessly we call it directly."""
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=404, detail=f"athlete {athlete_id} not found")
    athlete.community_consent = value
    session.commit()
    return {"athlete_id": athlete_id, "community_consent": athlete.community_consent}


@app.put("/athlete/{athlete_id}/write-mode", dependencies=[Depends(require_admin)])
def set_write_mode(
    athlete_id: int, mode: str, session: Session = Depends(get_session)
):
    """How the burrito block is written to the activity: append | prepend | off."""
    if mode not in ("append", "prepend", "off"):
        raise HTTPException(
            status_code=400, detail="mode must be append, prepend, or off"
        )
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=404, detail=f"athlete {athlete_id} not found")
    athlete.write_mode = mode
    session.commit()
    return {"athlete_id": athlete_id, "write_mode": athlete.write_mode}


@app.post("/athlete/{athlete_id}/backfill", dependencies=[Depends(require_admin)])
def backfill(
    athlete_id: int, write: bool = False, session: Session = Depends(get_session)
):
    """Queue a backfill of the athlete's competition-window activities. The worker
    expands it into one rate-limited process job per activity.
    write=false tallies only; write=true also updates each activity's description."""
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=404, detail=f"athlete {athlete_id} not found")
    job_id = enqueue_backfill(athlete_id, write=write)
    return {"status": "queued", "job_id": job_id, "write": write}


@app.post("/debug/process/{activity_id}", dependencies=[Depends(require_admin)])
def debug_process(
    activity_id: int, athlete_id: int, session: Session = Depends(get_session)
):
    """Run the pipeline synchronously for one activity (manual-test shortcut)."""
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        raise HTTPException(status_code=404, detail=f"athlete {athlete_id} not found")
    return {"outcome": process_activity(session, athlete, activity_id)}
