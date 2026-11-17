"""Backfill an athlete's competition-window activities: walk every activity
whose start falls in the window, tally the burrito-segment crossings, and
optionally write the description. Reads/writes only this athlete's own data."""

import logging

from sqlalchemy.orm import Session

from . import strava
from .config import settings
from .models import Athlete
from .processing import _window_total, process_activity

log = logging.getLogger("burrito.backfill")


def window_bounds() -> tuple[int, int]:
    """Epoch-second bounds for Strava's after/before, padded by a second."""
    after = int(settings.competition_start.timestamp()) - 1
    before = int(settings.competition_end.timestamp()) + 1
    return after, before


def iter_window_activity_ids(session: Session, athlete: Athlete):
    """Yield every activity id whose start falls in the competition window,
    OLDEST-first. Strava returns newest-first, but the running month-to-date
    total only accumulates correctly if we process chronologically."""
    after, before = window_bounds()
    ids: list[int] = []
    page = 1
    while True:
        batch = strava.get_athlete_activities(
            session, athlete, after=after, before=before, page=page, per_page=100
        )
        if not batch:
            break
        ids.extend(summary["id"] for summary in batch)
        page += 1
    yield from reversed(ids)


def backfill_athlete(
    session: Session, athlete: Athlete, write: bool = False
) -> dict:
    """Inline (synchronous) backfill — used by scripts/tests. The production path
    goes through the job queue (see worker.run_backfill_job)."""
    scanned = 0
    matched_activities = 0
    for activity_id in iter_window_activity_ids(session, athlete):
        scanned += 1
        outcome = process_activity(session, athlete, activity_id, write=write)
        if outcome.startswith(("tallied", "matched", "already")):
            matched_activities += 1
        log.info("backfill %s: %s", activity_id, outcome)

    return {
        "athlete_id": athlete.athlete_id,
        "activities_scanned": scanned,
        "activities_with_burrito": matched_activities,
        "window_total": _window_total(session, athlete.athlete_id),
        "wrote_descriptions": write,
    }
