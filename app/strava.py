"""Thin Strava API client: OAuth token lifecycle + the few v3 calls we need."""

import time

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .crypto import decrypt, encrypt
from .models import Athlete
from .ratelimit import limiter

API = "https://www.strava.com/api/v3"
OAUTH_TOKEN = "https://www.strava.com/oauth/token"
OAUTH_DEAUTH = "https://www.strava.com/oauth/deauthorize"
_TIMEOUT = httpx.Timeout(15.0)


def exchange_code(code: str) -> dict:
    """Trade an authorization code for tokens + athlete summary."""
    resp = httpx.post(
        OAUTH_TOKEN,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def upsert_athlete(session: Session, token_payload: dict, scopes: str) -> Athlete:
    athlete_id = token_payload["athlete"]["id"]
    athlete = session.get(Athlete, athlete_id)
    if athlete is None:
        athlete = Athlete(athlete_id=athlete_id)
        session.add(athlete)
    athlete.access_token_enc = encrypt(token_payload["access_token"])
    athlete.refresh_token_enc = encrypt(token_payload["refresh_token"])
    athlete.expires_at = token_payload["expires_at"]
    athlete.scopes = scopes
    session.commit()
    return athlete


def _valid_access_token(session: Session, athlete: Athlete) -> str:
    """Return a non-expired access token, refreshing in place if needed."""
    if athlete.expires_at - time.time() > 60:
        return decrypt(athlete.access_token_enc)

    limiter.acquire()
    resp = httpx.post(
        OAUTH_TOKEN,
        data={
            "client_id": settings.strava_client_id,
            "client_secret": settings.strava_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": decrypt(athlete.refresh_token_enc),
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    athlete.access_token_enc = encrypt(data["access_token"])
    athlete.refresh_token_enc = encrypt(data["refresh_token"])
    athlete.expires_at = data["expires_at"]
    session.commit()
    return data["access_token"]


def get_activity(session: Session, athlete: Athlete, activity_id: int) -> dict:
    """Detailed activity — includes segment_efforts[] when present."""
    token = _valid_access_token(session, athlete)
    limiter.acquire()
    resp = httpx.get(
        f"{API}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={"include_all_efforts": "true"},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def deauthorize(session: Session, athlete: Athlete) -> None:
    """Revoke our access on Strava's side. Best-effort — caller purges locally
    regardless of the result."""
    try:
        token = _valid_access_token(session, athlete)
        limiter.acquire()
        httpx.post(
            OAUTH_DEAUTH,
            data={"access_token": token},
            timeout=_TIMEOUT,
        )
    except Exception:  # noqa: BLE001 - revoke is best-effort; local purge is the point
        pass


def get_athlete_activities(
    session: Session,
    athlete: Athlete,
    after: int | None = None,
    before: int | None = None,
    page: int = 1,
    per_page: int = 100,
) -> list[dict]:
    """Summary activities for the athlete, optionally bounded by epoch seconds."""
    token = _valid_access_token(session, athlete)
    params: dict = {"page": page, "per_page": per_page}
    if after is not None:
        params["after"] = after
    if before is not None:
        params["before"] = before
    limiter.acquire()
    resp = httpx.get(
        f"{API}/athlete/activities",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def update_description(
    session: Session, athlete: Athlete, activity_id: int, description: str
) -> dict:
    token = _valid_access_token(session, athlete)
    limiter.acquire()
    resp = httpx.put(
        f"{API}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"description": description},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()
