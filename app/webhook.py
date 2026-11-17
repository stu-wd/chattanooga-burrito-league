import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .athletes import purge_activity, purge_athlete
from .config import settings
from .db import get_session
from .worker import enqueue_process

log = logging.getLogger("burrito.webhook")
router = APIRouter(tags=["webhook"])


@router.get("/strava/webhook")
def verify(request: Request):
    """Strava subscription validation: echo hub.challenge if the token matches."""
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and (
        params.get("hub.verify_token") == settings.strava_verify_token
    ):
        return JSONResponse({"hub.challenge": params.get("hub.challenge")})
    raise HTTPException(status_code=403, detail="verify token mismatch")


@router.post("/strava/webhook")
async def receive(request: Request, session: Session = Depends(get_session)):
    """Ack fast: validate, dedupe, enqueue. Never do slow work inline."""
    event = await request.json()
    object_type = event.get("object_type")
    aspect_type = event.get("aspect_type")
    object_id = event.get("object_id")
    owner_id = event.get("owner_id")

    # Deauthorization: Strava requires we stop using and delete the athlete's data.
    if object_type == "athlete" and str(
        (event.get("updates") or {}).get("authorized")
    ).lower() == "false":
        purge_athlete(session, object_id or owner_id)
        return {"status": "purged"}

    if object_type == "activity" and aspect_type == "create":
        added = enqueue_process(activity_id=object_id, athlete_id=owner_id)
        log.info("activity create %s owner %s enqueued=%s", object_id, owner_id, added)
        return {"status": "enqueued" if added else "duplicate"}

    # TOS §6.3: reflect a Strava-side activity deletion within 48h. Pure local DB
    # delete (no Strava HTTP call), so handle inline and reflect it immediately.
    if object_type == "activity" and aspect_type == "delete":
        removed = purge_activity(session, object_id, owner_id)
        log.info("activity delete %s owner %s removed=%s", object_id, owner_id, removed)
        return {"status": "purged", "removed": removed}

    # An activity flipped to private is no longer being publicly shared; purge its
    # cached efforts too (conservative read of §6.2/§6.4 — retaining less is always
    # permitted). Same inline local delete.
    if object_type == "activity" and aspect_type == "update" and str(
        (event.get("updates") or {}).get("private")
    ).lower() == "true":
        removed = purge_activity(session, object_id, owner_id)
        log.info("activity private %s owner %s removed=%s", object_id, owner_id, removed)
        return {"status": "purged", "removed": removed}

    return {"status": "ignored"}
