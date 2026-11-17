import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import strava
from .config import settings
from .db import get_session

log = logging.getLogger("burrito.oauth")
router = APIRouter(tags=["oauth"])


@router.get("/auth/login")
def login():
    """Redirect to Strava's authorize page (the 'Connect with Strava' target).
    Leaderboard consent is captured later on the settings page (with a name)."""
    params = {
        "client_id": settings.strava_client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": settings.strava_scopes,
    }
    return RedirectResponse(
        f"https://www.strava.com/oauth/authorize?{urlencode(params)}"
    )


@router.get("/auth/callback")
def callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    scope: str = "",
    session: Session = Depends(get_session),
):
    if error:
        raise HTTPException(status_code=400, detail=f"Strava returned error: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    payload = strava.exchange_code(code)
    athlete = strava.upsert_athlete(session, payload, scope)
    request.session["athlete_id"] = athlete.athlete_id
    log.info("Onboarded athlete %s (scopes=%s)", athlete.athlete_id, scope)
    return RedirectResponse("/settings", status_code=303)
