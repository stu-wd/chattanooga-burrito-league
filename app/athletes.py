"""Athlete lifecycle helpers shared by the webhook deauth path and the
user-initiated disconnect endpoint."""

import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Athlete, CountedActivity, Job, SegmentEffort, WindowTally

log = logging.getLogger("burrito.athletes")


def purge_athlete(session: Session, athlete_id: int | None) -> None:
    """Hard-delete the athlete's tokens and all derived data (TOS requirement
    on deauthorization)."""
    if athlete_id is None:
        return
    session.execute(delete(WindowTally).where(WindowTally.athlete_id == athlete_id))
    session.execute(
        delete(CountedActivity).where(CountedActivity.athlete_id == athlete_id)
    )
    # Legacy raw cache — normally already empty post-cutover, purged defensively.
    session.execute(delete(SegmentEffort).where(SegmentEffort.athlete_id == athlete_id))
    session.execute(delete(Job).where(Job.athlete_id == athlete_id))
    session.execute(delete(Athlete).where(Athlete.athlete_id == athlete_id))
    session.commit()
    log.info("purged all data for athlete %s", athlete_id)
