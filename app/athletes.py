"""Athlete lifecycle helpers shared by the webhook deauth path and the
user-initiated disconnect endpoint."""

import logging

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .models import Athlete, CountedActivity, Job, SegmentEffort, WindowTally
from .tally import activity_hash

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


def purge_activity(
    session: Session, activity_id: int | None, athlete_id: int | None = None
) -> int:
    """Remove all cached Strava-derived data for a single activity (TOS §6.3:
    reflect a Strava-side deletion within 48h). Returns rows deleted.

    Under the §6.2 fold-and-forget cache the activity is identified by a one-way
    HMAC (no raw id is retained), so athlete_id is required to locate the
    CountedActivity ledger row; its folded crossings are then subtracted back out
    of the WindowTally so the aggregate reflects the deletion. Legacy raw
    SegmentEffort rows — present only transiently before the cutover sweep, and
    still keyed on the raw id — are purged defensively. athlete_id also scopes
    every delete so one athlete's event can never touch another athlete's rows."""
    if activity_id is None:
        return 0

    removed = 0

    # Legacy raw cache: keyed on the raw activity id, present only until the
    # fold-and-forget sweep runs, but a delete event can land inside that window.
    effort_where = [SegmentEffort.activity_id == activity_id]
    if athlete_id is not None:
        effort_where.append(SegmentEffort.athlete_id == athlete_id)
    removed += session.execute(delete(SegmentEffort).where(*effort_where)).rowcount or 0

    # Aggregate ledger (§6.2 source of truth): locatable only by HMAC, so without
    # athlete_id we cannot — by design — find the row from the raw id alone.
    if athlete_id is not None:
        counted = session.get(
            CountedActivity, (athlete_id, activity_hash(athlete_id, activity_id))
        )
        if counted is not None:
            edition_key = counted.edition_key
            crossings = counted.crossings
            session.delete(counted)
            session.flush()  # so the best-ride recompute below excludes this row
            tally = session.get(WindowTally, (athlete_id, edition_key))
            if tally is not None:
                tally.total_count = max(0, tally.total_count - crossings)
                # best_ride_count is a max over activities, not a sum — it can't be
                # decremented in place, so recompute it from the surviving rows.
                tally.best_ride_count = session.scalar(
                    select(func.coalesce(func.max(CountedActivity.crossings), 0)).where(
                        CountedActivity.athlete_id == athlete_id,
                        CountedActivity.edition_key == edition_key,
                    )
                ) or 0
            removed += 1

    session.commit()
    log.info("purged activity %s (athlete %s): %s rows", activity_id, athlete_id, removed)
    return removed
