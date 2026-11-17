"""Tests for TOS §6.3 per-activity deletion (app.athletes.purge_activity).

Runs under pytest if available, or standalone: `.venv/bin/python tests/test_purge_activity.py`.
Uses an in-memory SQLite session, mirroring the app's get_session shape.

Under the §6.2 fold-and-forget cache there is no retained raw activity id: a
counted activity lives only as a CountedActivity row keyed on a one-way HMAC,
with its crossings folded into WindowTally. So a deletion must be located by
hash (hence athlete_id is required) and must subtract its crossings back out of
the tally. Legacy raw SegmentEffort rows are still purged defensively.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.athletes import purge_activity
from app.db import Base
from app.models import Athlete, CountedActivity, SegmentEffort, WindowTally
from app.tally import activity_hash, fold_activity


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _ensure_athlete(session, athlete_id: int):
    if session.get(Athlete, athlete_id) is None:
        session.add(
            Athlete(
                athlete_id=athlete_id,
                access_token_enc="x",
                refresh_token_enc="x",
                expires_at=0,
            )
        )
        session.commit()


def _count_activity(session, athlete_id: int, activity_id: int, crossings: int):
    """Fold an activity into the aggregate, the way the live path does."""
    _ensure_athlete(session, athlete_id)
    fold_activity(session, athlete_id, activity_id, crossings)


def _add_legacy_efforts(session, athlete_id: int, activity_id: int, n: int):
    """Seed pre-cutover raw SegmentEffort rows (keyed on the raw id)."""
    for i in range(n):
        session.add(
            SegmentEffort(
                strava_effort_id=activity_id * 1000 + i,
                athlete_id=athlete_id,
                activity_id=activity_id,
                segment_id=42,
                effort_start=datetime.now(timezone.utc),
            )
        )
    session.commit()


def _tally(session, athlete_id: int):
    from app.config import settings

    return session.get(WindowTally, (athlete_id, settings.edition_key))


def test_delete_removes_counted_activity_and_decrements_tally():
    s = _session()
    _count_activity(s, athlete_id=1, activity_id=100, crossings=2)
    _count_activity(s, athlete_id=1, activity_id=200, crossings=3)
    assert _tally(s, 1).total_count == 5

    removed = purge_activity(s, 100, 1)

    assert removed == 1  # the single counted-activity ledger row
    assert _tally(s, 1).total_count == 3  # activity 100's crossings subtracted out
    # Ledger row for 100 gone, 200 untouched.
    assert s.get(CountedActivity, (1, activity_hash(1, 100))) is None
    assert s.get(CountedActivity, (1, activity_hash(1, 200))) is not None
    assert s.query(Athlete).count() == 1


def test_delete_recomputes_best_ride_down():
    s = _session()
    _count_activity(s, athlete_id=1, activity_id=100, crossings=5)  # the best ride
    _count_activity(s, athlete_id=1, activity_id=200, crossings=2)
    assert _tally(s, 1).best_ride_count == 5

    purge_activity(s, 100, 1)

    # best_ride is a max over activities, not a sum — deleting the top ride must
    # pull it back down to the next survivor, not leave it stale at 5.
    assert _tally(s, 1).best_ride_count == 2
    assert _tally(s, 1).total_count == 2


def test_delete_also_purges_legacy_raw_efforts():
    s = _session()
    _count_activity(s, athlete_id=1, activity_id=100, crossings=2)
    _add_legacy_efforts(s, athlete_id=1, activity_id=100, n=2)

    removed = purge_activity(s, 100, 1)

    assert removed == 3  # 2 legacy efforts + 1 ledger row
    assert s.query(SegmentEffort).filter_by(activity_id=100).count() == 0
    assert s.get(CountedActivity, (1, activity_hash(1, 100))) is None


def test_delete_of_uncached_activity_is_noop():
    s = _session()
    _count_activity(s, athlete_id=1, activity_id=100, crossings=2)

    removed = purge_activity(s, 999, 1)

    assert removed == 0
    assert _tally(s, 1).total_count == 2
    assert s.query(CountedActivity).count() == 1


def test_delete_cannot_cross_athletes():
    s = _session()
    # Same raw activity_id for two athletes hashes to two distinct tokens, so a
    # purge scoped to athlete 2 must never touch athlete 1's row or tally.
    _count_activity(s, athlete_id=1, activity_id=100, crossings=2)
    _count_activity(s, athlete_id=2, activity_id=100, crossings=4)

    removed = purge_activity(s, 100, 2)

    assert removed == 1
    assert _tally(s, 1).total_count == 2
    assert s.get(CountedActivity, (1, activity_hash(1, 100))) is not None
    assert _tally(s, 2).total_count == 0
    assert s.get(CountedActivity, (2, activity_hash(2, 100))) is None


def test_none_activity_id_is_noop():
    s = _session()
    _count_activity(s, athlete_id=1, activity_id=100, crossings=2)
    assert purge_activity(s, None, 1) == 0
    assert _tally(s, 1).total_count == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
