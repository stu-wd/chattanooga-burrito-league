"""Session-scoped JSON API — the single source of truth for user settings.
The server-rendered pages call these; a future React app reuses them unchanged.
Identity comes from the signed session cookie, never a URL id (no IDOR)."""

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import strava
from .athletes import purge_athlete
from .config import settings
from .db import get_session
from .models import Athlete, CountedActivity, SegmentEffort, WindowTally
from .processing import _window_total
from .worker import enqueue_backfill

router = APIRouter(prefix="/api", tags=["api"])

WRITE_MODES = ("append", "prepend", "off")
MAX_NAME_LEN = 30


def current_athlete(request: Request, session: Session) -> Athlete:
    athlete_id = request.session.get("athlete_id")
    athlete = session.get(Athlete, athlete_id) if athlete_id else None
    if athlete is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return athlete


def _apply_consent(
    session: Session, athlete: Athlete, consent: bool, display_name: str | None
) -> None:
    """Enforce the TOS invariant: leaderboard visibility requires an affirmative
    opt-in AND a chosen, unique display name. Opting out clears the name."""
    if not consent:
        athlete.community_consent = False
        athlete.display_name = None
        return
    name = (display_name or "").strip()
    if not name:
        raise HTTPException(
            status_code=400, detail="a display name is required to join the leaderboard"
        )
    if len(name) > MAX_NAME_LEN:
        raise HTTPException(
            status_code=400, detail=f"display name must be ≤ {MAX_NAME_LEN} characters"
        )
    taken = session.scalar(
        select(Athlete.athlete_id).where(
            Athlete.community_consent.is_(True),
            Athlete.athlete_id != athlete.athlete_id,
            func.lower(Athlete.display_name) == name.lower(),
        )
    )
    if taken:
        raise HTTPException(status_code=409, detail="that display name is taken")
    athlete.community_consent = True
    athlete.display_name = name


def _me_payload(session: Session, athlete: Athlete) -> dict:
    return {
        "athlete_id": athlete.athlete_id,
        "write_mode": athlete.write_mode,
        "community_consent": athlete.community_consent,
        "display_name": athlete.display_name,
        "total": _window_total(session, athlete.athlete_id),
    }


@router.get("/me")
def me(request: Request, session: Session = Depends(get_session)):
    return _me_payload(session, current_athlete(request, session))


@router.put("/me/write-mode")
def set_write_mode(
    request: Request,
    mode: str = Body(..., embed=True),
    session: Session = Depends(get_session),
):
    if mode not in WRITE_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {WRITE_MODES}")
    athlete = current_athlete(request, session)
    athlete.write_mode = mode
    session.commit()
    return _me_payload(session, athlete)


@router.put("/me/consent")
def set_consent(
    request: Request,
    consent: bool = Body(..., embed=True),
    display_name: str | None = Body(None, embed=True),
    session: Session = Depends(get_session),
):
    athlete = current_athlete(request, session)
    _apply_consent(session, athlete, consent, display_name)
    session.commit()
    return _me_payload(session, athlete)


@router.put("/me/settings")
def save_settings(
    request: Request,
    write_mode: str = Body(..., embed=True),
    community_consent: bool = Body(..., embed=True),
    display_name: str | None = Body(None, embed=True),
    session: Session = Depends(get_session),
):
    """Atomically persist settings (what the Save button calls)."""
    if write_mode not in WRITE_MODES:
        raise HTTPException(
            status_code=400, detail=f"write_mode must be one of {WRITE_MODES}"
        )
    athlete = current_athlete(request, session)
    _apply_consent(session, athlete, community_consent, display_name)
    athlete.write_mode = write_mode
    session.commit()
    return _me_payload(session, athlete)


@router.post("/me/backfill")
def backfill(
    request: Request,
    year: int = Body(..., embed=True),
    session: Session = Depends(get_session),
):
    """Queue a backfill of the athlete's own activities for the given edition.
    Only the active edition's year is allowed (others are 'coming soon')."""
    athlete = current_athlete(request, session)
    active_year = settings.competition_start.year
    if year != active_year:
        raise HTTPException(status_code=400, detail=f"the {year} edition isn't live yet")
    job_id = enqueue_backfill(athlete.athlete_id, write=True)
    return {"status": "queued", "job_id": job_id, "year": year}


@router.post("/me/reset")
def reset_rolls(request: Request, session: Session = Depends(get_session)):
    """Delete the athlete's own tally + counted-activity ledger (keeps
    tokens/settings). Testing aid so backfill can be re-run from zero."""
    athlete = current_athlete(request, session)
    session.query(WindowTally).filter_by(athlete_id=athlete.athlete_id).delete()
    session.query(CountedActivity).filter_by(athlete_id=athlete.athlete_id).delete()
    # Legacy raw cache — normally already empty post-cutover, cleared defensively.
    session.query(SegmentEffort).filter_by(athlete_id=athlete.athlete_id).delete()
    session.commit()
    return _me_payload(session, athlete)


@router.post("/me/disconnect")
def disconnect(request: Request, session: Session = Depends(get_session)):
    athlete = current_athlete(request, session)
    athlete_id = athlete.athlete_id
    strava.deauthorize(session, athlete)   # revoke on Strava (best-effort)
    purge_athlete(session, athlete_id)      # hard-delete locally
    request.session.clear()
    return {"status": "disconnected", "athlete_id": athlete_id}
