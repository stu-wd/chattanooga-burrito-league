"""Athlete lifecycle helpers shared by the webhook deauth path and the
user-initiated disconnect endpoint."""

import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Athlete, Job, ProcessedActivity, SegmentEffort

log = logging.getLogger("burrito.athletes")


def purge_athlete(session: Session, athlete_id: int | None) -> None:
    """Hard-delete the athlete's tokens and all derived data (TOS requirement
    on deauthorization)."""
    if athlete_id is None:
        return
    session.execute(delete(SegmentEffort).where(SegmentEffort.athlete_id == athlete_id))
    session.execute(
        delete(ProcessedActivity).where(ProcessedActivity.athlete_id == athlete_id)
    )
    session.execute(delete(Job).where(Job.athlete_id == athlete_id))
    session.execute(delete(Athlete).where(Athlete.athlete_id == athlete_id))
    session.commit()
    log.info("purged all data for athlete %s", athlete_id)


def purge_activity(
    session: Session, activity_id: int | None, athlete_id: int | None = None
) -> int:
    """Remove all cached Strava-derived data for a single activity (TOS §6.3:
    reflect a Strava-side deletion within 48h). Returns rows deleted.
    athlete_id, when given, scopes the delete defensively so one athlete's
    event can never touch another athlete's rows."""
    if activity_id is None:
        return 0

    effort_where = [SegmentEffort.activity_id == activity_id]
    activity_where = [ProcessedActivity.activity_id == activity_id]
    if athlete_id is not None:
        effort_where.append(SegmentEffort.athlete_id == athlete_id)
        activity_where.append(ProcessedActivity.athlete_id == athlete_id)

    efforts = session.execute(delete(SegmentEffort).where(*effort_where))
    activities = session.execute(delete(ProcessedActivity).where(*activity_where))
    removed = (efforts.rowcount or 0) + (activities.rowcount or 0)
    session.commit()
    log.info("purged activity %s (athlete %s): %s rows", activity_id, athlete_id, removed)
    return removed
