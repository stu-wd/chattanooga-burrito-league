"""Public leaderboard data. TOS: only athletes who explicitly opted in
(community_consent + a chosen display_name) ever appear here."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Athlete, SegmentEffort


def _window_filter():
    return (
        SegmentEffort.segment_id == settings.target_segment_id,
        SegmentEffort.effort_start >= settings.competition_start,
        SegmentEffort.effort_start <= settings.competition_end,
    )


def leaderboard_data(session: Session) -> dict:
    consenting = Athlete.community_consent.is_(True)

    # Standings: consenting athletes with ≥1 roll this edition, most first.
    rows = session.execute(
        select(Athlete.display_name, func.count(SegmentEffort.id))
        .join(SegmentEffort, SegmentEffort.athlete_id == Athlete.athlete_id)
        .where(consenting, *_window_filter())
        .group_by(Athlete.athlete_id)
        .order_by(func.count(SegmentEffort.id).desc(), Athlete.display_name)
    ).all()
    standings = [
        {"rank": i + 1, "name": name, "total": total}
        for i, (name, total) in enumerate(rows)
    ]

    # Highlight: most crossings in a single activity, among consenting athletes.
    top_ride = session.execute(
        select(Athlete.display_name, func.count(SegmentEffort.id).label("c"))
        .join(SegmentEffort, SegmentEffort.athlete_id == Athlete.athlete_id)
        .where(consenting, *_window_filter())
        .group_by(Athlete.athlete_id, SegmentEffort.activity_id)
        .order_by(func.count(SegmentEffort.id).desc())
        .limit(1)
    ).first()

    return {
        "edition": settings.competition_start.strftime("%B %Y"),
        "total": sum(s["total"] for s in standings),
        "participants": len(standings),
        "standings": standings,
        "biggest_ride": (
            {"name": top_ride[0], "count": top_ride[1]} if top_ride else None
        ),
    }
