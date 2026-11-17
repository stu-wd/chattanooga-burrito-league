"""Server-rendered pages. Intentionally thin — they render status and then call
the JSON API (app/api.py) via fetch(), so a future React SPA can replace these
templates without touching the backend."""

from datetime import timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_session
from .leaderboard import leaderboard_data
from .models import Athlete, SegmentEffort
from .processing import _window_total

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter(tags=["web"])


def _session_athlete(request: Request, session: Session) -> Athlete | None:
    """Resolve the signed-in athlete. If the cookie references a row that no
    longer exists (e.g. DB reset), clear the stale session so pages don't
    redirect-loop between / and /settings."""
    athlete_id = request.session.get("athlete_id")
    if not athlete_id:
        return None
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        request.session.clear()
    return athlete


@router.get("/dev/login")
def dev_login(request: Request, session: Session = Depends(get_session)):
    """Local-only shortcut: log in as the first athlete in the DB so you can
    iterate on /settings and /history without the OAuth/ngrok round-trip.
    Refuses unless running on SQLite (i.e. local dev)."""
    from .config import settings as _s

    if not _s.database_url.startswith("sqlite"):
        raise HTTPException(status_code=404, detail="Not Found")
    athlete = session.scalars(select(Athlete).limit(1)).first()
    if athlete is None:
        return HTMLResponse(
            "<p>No athlete in the local DB yet. Connect once via ngrok, or seed one.</p>"
        )
    request.session["athlete_id"] = athlete.athlete_id
    return RedirectResponse("/settings", status_code=303)


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, session: Session = Depends(get_session)):
    if _session_athlete(request, session) is not None:
        return RedirectResponse("/settings", status_code=303)
    return templates.TemplateResponse("landing.html", {"request": request})


@router.get("/leaderboard", response_class=HTMLResponse)
def leaderboard(request: Request, session: Session = Depends(get_session)):
    data = leaderboard_data(session)
    signed_in = bool(request.session.get("athlete_id"))
    return templates.TemplateResponse(
        "leaderboard.html",
        {"request": request, "signed_in": signed_in, "show_nav": signed_in, **data},
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, session: Session = Depends(get_session)):
    athlete = _session_athlete(request, session)
    if athlete is None:
        return RedirectResponse("/", status_code=303)
    # The athlete's own rolls, grouped by activity, newest first.
    rows = session.execute(
        select(
            SegmentEffort.activity_id,
            func.count(SegmentEffort.id),
            func.min(SegmentEffort.effort_start),
        )
        .where(
            SegmentEffort.athlete_id == athlete.athlete_id,
            SegmentEffort.segment_id == settings.target_segment_id,
        )
        .group_by(SegmentEffort.activity_id)
        .order_by(func.min(SegmentEffort.effort_start).desc())
    ).all()
    est = timezone(timedelta(hours=-5))
    activities = [
        {
            "activity_id": aid,
            "crossings": n,
            "when": dt.astimezone(est).strftime("%b %-d, %Y") if dt else "—",
        }
        for aid, n, dt in rows
    ]
    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "show_nav": True,
            "activities": activities,
            "total": _window_total(session, athlete.athlete_id),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = Depends(get_session)):
    athlete = _session_athlete(request, session)
    if athlete is None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "show_nav": True,
            "athlete": athlete,
            "total": _window_total(session, athlete.athlete_id),
        },
    )
