"""Server-rendered pages. Intentionally thin — they render status and then call
the JSON API (app/api.py) via fetch(), so a future React SPA can replace these
templates without touching the backend."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import strava
from .backfill import window_bounds
from .config import settings
from .db import get_session
from .leaderboard import leaderboard_data
from .models import Athlete, CountedActivity
from .processing import _window_total
from .tally import activity_hash

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# Exposed to every template so base.html can hide the leaderboard nav link
# without each route threading the flag through its context dict.
templates.env.globals["leaderboard_enabled"] = settings.leaderboard_enabled
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
    # Strava API Policy §2.3: serving other athletes' data is prohibited. Gated
    # off until Strava rules on the §6.1 ≤9,999 exemption. See config.py.
    if not settings.leaderboard_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
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
    # §6.2: we no longer retain per-activity Strava ids/dates. Re-fetch the
    # athlete's OWN window activities live from Strava (used to render, not
    # cached), and pull each activity's crossing count from our derived
    # CountedActivity ledger. ~1 Strava call per view (plus pagination); no raw
    # Strava Data is stored. Only activities we actually counted are shown.
    after, before = window_bounds()
    est = timezone(timedelta(hours=-5))
    rows: list[tuple[datetime | None, int, int]] = []
    page = 1
    while True:
        batch = strava.get_athlete_activities(
            session, athlete, after=after, before=before, page=page, per_page=100
        )
        if not batch:
            break
        for summary in batch:
            aid = summary.get("id")
            if aid is None:
                continue
            counted = session.get(
                CountedActivity,
                (athlete.athlete_id, activity_hash(athlete.athlete_id, aid)),
            )
            if counted is None:
                continue
            raw = summary.get("start_date")
            start = (
                datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
            )
            rows.append((start, aid, counted.crossings))
        page += 1
    rows.sort(key=lambda r: (r[0] is not None, r[0]), reverse=True)
    activities = [
        {
            "activity_id": aid,
            "crossings": crossings,
            "when": start.astimezone(est).strftime("%b %-d, %Y") if start else "—",
        }
        for start, aid, crossings in rows
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
