"""§6.2 step 1 — WindowTally rebuild + verify.

Runnable standalone (`.venv/bin/python tests/test_tally_rebuild.py`) or under pytest.
Uses an isolated temp SQLite DB and seeds efforts inside the configured competition
window, so it tracks whatever .env window/segment is active."""

import os
import sys
import tempfile
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Athlete, SegmentEffort  # noqa: E402
from app.tally import (  # noqa: E402
    ensure_compliant_cache,
    rebuild_tallies_from_efforts,
    verify_tallies,
)
from app.models import WindowTally  # noqa: E402
from sqlalchemy import func, select  # noqa: E402


def _session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    # Mirror the app's SessionLocal (autoflush=False) so the test exercises the
    # same flush behavior production uses.
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return maker(), path


def _athlete(session, athlete_id):
    session.add(
        Athlete(
            athlete_id=athlete_id,
            access_token_enc="x",
            refresh_token_enc="x",
            expires_at=0,
        )
    )


def _efforts(session, athlete_id, activity_id, n, base_effort_id):
    """Add n crossings of the target segment within the window."""
    when = settings.competition_start + timedelta(hours=1)
    for i in range(n):
        session.add(
            SegmentEffort(
                strava_effort_id=base_effort_id + i,
                athlete_id=athlete_id,
                activity_id=activity_id,
                segment_id=settings.target_segment_id,
                effort_start=when,
            )
        )


def test_rebuild_matches_and_is_idempotent():
    session, path = _session()
    try:
        _athlete(session, 100)
        _athlete(session, 200)
        _efforts(session, 100, 1, 3, base_effort_id=1000)   # activity 1: 3
        _efforts(session, 100, 2, 5, base_effort_id=2000)   # activity 2: 5
        _efforts(session, 200, 3, 2, base_effort_id=3000)   # activity 3: 2
        # Out-of-window effort must be ignored by both old and new paths.
        session.add(
            SegmentEffort(
                strava_effort_id=9999,
                athlete_id=100,
                activity_id=9,
                segment_id=settings.target_segment_id,
                effort_start=settings.competition_start - timedelta(days=1),
            )
        )
        session.commit()

        summary = rebuild_tallies_from_efforts(session)
        assert summary == {"groups": 3, "activities_folded": 3}, summary

        a = session.get(WindowTally, (100, settings.edition_key))
        b = session.get(WindowTally, (200, settings.edition_key))
        assert (a.total_count, a.best_ride_count) == (8, 5), (a.total_count, a.best_ride_count)
        assert (b.total_count, b.best_ride_count) == (2, 2), (b.total_count, b.best_ride_count)

        verification = verify_tallies(session)
        assert all(v["match"] for v in verification), verification

        # Re-run: nothing new folded, totals unchanged (idempotent cutover).
        again = rebuild_tallies_from_efforts(session)
        assert again["activities_folded"] == 0, again
        a2 = session.get(WindowTally, (100, settings.edition_key))
        assert a2.total_count == 8, a2.total_count
    finally:
        session.close()
        os.unlink(path)


def test_ensure_compliant_cache_folds_then_purges():
    session, path = _session()
    try:
        _athlete(session, 100)
        _efforts(session, 100, 1, 3, base_effort_id=1000)
        _efforts(session, 100, 2, 5, base_effort_id=2000)
        session.commit()

        out = ensure_compliant_cache(session)
        assert out["status"] == "cutover" and out["purged"] == 8, out

        # Totals preserved in the aggregate...
        t = session.get(WindowTally, (100, settings.edition_key))
        assert t.total_count == 8, t.total_count
        # ...and ZERO raw Strava Data left at rest.
        assert session.scalar(select(func.count()).select_from(SegmentEffort)) == 0

        # Idempotent: a second boot finds nothing to do.
        assert ensure_compliant_cache(session)["status"] == "clean"
    finally:
        session.close()
        os.unlink(path)


if __name__ == "__main__":
    test_rebuild_matches_and_is_idempotent()
    print("OK test_rebuild_matches_and_is_idempotent")
    test_ensure_compliant_cache_folds_then_purges()
    print("OK test_ensure_compliant_cache_folds_then_purges")
