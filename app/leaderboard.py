"""Public leaderboard data. TOS: only athletes who explicitly opted in
(community_consent + a chosen display_name) ever appear here.

Reads the durable WindowTally aggregate (§6.2) — derived integers, never raw
Strava effort rows. Gated behind LEADERBOARD_ENABLED (§2.3, compliance item #2)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Athlete, WindowTally


def leaderboard_data(session: Session) -> dict:
    consenting = Athlete.community_consent.is_(True)
    edition = settings.edition_key

    # Standings: consenting athletes with ≥1 roll this edition, most first.
    rows = session.execute(
        select(Athlete.display_name, WindowTally.total_count)
        .join(WindowTally, WindowTally.athlete_id == Athlete.athlete_id)
        .where(consenting, WindowTally.edition_key == edition, WindowTally.total_count > 0)
        .order_by(WindowTally.total_count.desc(), Athlete.display_name)
    ).all()
    standings = [
        {"rank": i + 1, "name": name, "total": total}
        for i, (name, total) in enumerate(rows)
    ]

    # Highlight: most crossings in a single activity, among consenting athletes.
    top_ride = session.execute(
        select(Athlete.display_name, WindowTally.best_ride_count)
        .join(WindowTally, WindowTally.athlete_id == Athlete.athlete_id)
        .where(
            consenting, WindowTally.edition_key == edition,
            WindowTally.best_ride_count > 0,
        )
        .order_by(WindowTally.best_ride_count.desc())
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
