"""§6.2 step 2 — process_activity folds into WindowTally with per-activity
idempotency. Drives the real process_activity with a stubbed Strava fetch.

Runnable standalone (`.venv/bin/python tests/test_process_fold.py`) or under pytest."""

import os
import sys
import tempfile
from datetime import timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import processing  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Athlete, CountedActivity, SegmentEffort, WindowTally  # noqa: E402
from app.tally import activity_hash  # noqa: E402


def _session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return maker(), path


def _activity(activity_id, n):
    """Synthetic Strava activity with n crossings of the target segment, in-window."""
    when = (settings.competition_start + timedelta(hours=1)).isoformat()
    return {
        "id": activity_id,
        "start_date": when,
        "description": "",
        "segment_efforts": [
            {
                "id": activity_id * 1000 + i,
                "segment": {"id": settings.target_segment_id},
                "start_date": when,
            }
            for i in range(n)
        ],
    }


def test_process_folds_once_and_is_idempotent(monkeypatch=None):
    session, path = _session()
    try:
        session.add(
            Athlete(
                athlete_id=42, access_token_enc="x",
                refresh_token_enc="x", expires_at=0,
            )
        )
        session.commit()

        activities = {7: _activity(7, 4), 8: _activity(8, 6)}
        writes = []
        # Stub Strava: detail fetch + description write — no network.
        processing.strava.get_activity = lambda s, a, aid: activities[aid]
        processing.strava.update_description = (
            lambda s, a, aid, desc: writes.append(aid) or {}
        )
        athlete = session.get(Athlete, 42)
        key = (42, settings.edition_key)

        processing.process_activity(session, athlete, 7, write=False)
        t = session.get(WindowTally, key)
        assert (t.total_count, t.best_ride_count) == (4, 4), (t.total_count, t.best_ride_count)

        processing.process_activity(session, athlete, 8, write=False)
        session.refresh(t)
        assert (t.total_count, t.best_ride_count) == (10, 6), (t.total_count, t.best_ride_count)

        # Re-process activity 7: must NOT double-count (per-activity idempotency).
        processing.process_activity(session, athlete, 7, write=False)
        session.refresh(t)
        assert (t.total_count, t.best_ride_count) == (10, 6), (t.total_count, t.best_ride_count)

        # Fold-and-forget: NO raw effort rows are persisted.
        assert session.scalar(
            select(func.count()).select_from(SegmentEffort)
        ) == 0

        # Derived ledger holds the per-activity crossing count (for /history).
        ca = session.get(CountedActivity, (42, activity_hash(42, 7)))
        assert ca.crossings == 4 and ca.written_at is None

        # Write path marks written_at; a second write is idempotent (no re-PUT).
        processing.process_activity(session, athlete, 8, write=True)
        assert writes == [8], writes
        processing.process_activity(session, athlete, 8, write=True)
        assert writes == [8], writes  # still only one description write
        session.refresh(t)
        assert t.total_count == 10, t.total_count  # write path never double-counts
    finally:
        session.close()
        os.unlink(path)


if __name__ == "__main__":
    test_process_folds_once_and_is_idempotent()
    print("OK test_process_folds_once_and_is_idempotent")
