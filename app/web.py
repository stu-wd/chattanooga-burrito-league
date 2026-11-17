"""Server-rendered pages. Intentionally thin — they render status and then call
the JSON API (app/api.py) via fetch(), so a future React SPA can replace these
templates without touching the backend."""

from datetime import timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
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


@router.get("/", response_class=HTMLResponse)
def landing(request: Request):
    if request.session.get("athlete_id"):
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
    athlete_id = request.session.get("athlete_id")
    athlete = session.get(Athlete, athlete_id) if athlete_id else None
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
    athlete_id = request.session.get("athlete_id")
    athlete = session.get(Athlete, athlete_id) if athlete_id else None
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
