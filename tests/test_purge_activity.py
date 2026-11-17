"""Tests for TOS §6.3 per-activity deletion (app.athletes.purge_activity).

Runs under pytest if available, or standalone: `.venv/bin/python tests/test_purge_activity.py`.
Uses an in-memory SQLite session, mirroring the app's get_session shape.
"""

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.athletes import purge_activity
from app.db import Base
from app.models import Athlete, ProcessedActivity, SegmentEffort


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _seed(session, athlete_id: int, activity_id: int, n_efforts: int = 2):
    if session.get(Athlete, athlete_id) is None:
        session.add(
            Athlete(
                athlete_id=athlete_id,
                access_token_enc="x",
                refresh_token_enc="x",
                expires_at=0,
            )
        )
    session.add(
        ProcessedActivity(activity_id=activity_id, athlete_id=athlete_id, matched=True)
    )
    for i in range(n_efforts):
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


def test_delete_removes_exactly_that_activity():
    s = _session()
    _seed(s, athlete_id=1, activity_id=100, n_efforts=2)
    _seed(s, athlete_id=1, activity_id=200, n_efforts=3)

    removed = purge_activity(s, 100, 1)

    assert removed == 3  # 2 efforts + 1 processed row
    assert s.query(SegmentEffort).filter_by(activity_id=100).count() == 0
    assert s.query(ProcessedActivity).filter_by(activity_id=100).count() == 0
    # Other activity untouched.
    assert s.query(SegmentEffort).filter_by(activity_id=200).count() == 3
    assert s.query(ProcessedActivity).filter_by(activity_id=200).count() == 1
    # Athlete row untouched (per-activity, not per-athlete).
    assert s.query(Athlete).count() == 1


def test_delete_of_uncached_activity_is_noop():
    s = _session()
    _seed(s, athlete_id=1, activity_id=100, n_efforts=2)

    removed = purge_activity(s, 999, 1)

    assert removed == 0
    assert s.query(SegmentEffort).count() == 2
    assert s.query(ProcessedActivity).count() == 1


def test_delete_cannot_cross_athletes():
    s = _session()
    _seed(s, athlete_id=1, activity_id=100, n_efforts=2)
    # Simulate a hypothetical effort row collision on the same activity_id tagged to
    # another athlete (ProcessedActivity.activity_id is a global PK so only efforts can
    # collide). The athlete_id scope must keep athlete 2's purge off athlete 1's rows.
    s.add(
        SegmentEffort(
            strava_effort_id=999,
            athlete_id=2,
            activity_id=100,
            segment_id=42,
            effort_start=datetime.now(timezone.utc),
        )
    )
    s.commit()

    removed = purge_activity(s, 100, 2)

    assert removed == 1  # only athlete 2's single effort
    assert s.query(SegmentEffort).filter_by(athlete_id=1).count() == 2
    assert s.query(ProcessedActivity).filter_by(athlete_id=1).count() == 1
    assert s.query(SegmentEffort).filter_by(athlete_id=2).count() == 0


def test_none_activity_id_is_noop():
    s = _session()
    _seed(s, athlete_id=1, activity_id=100, n_efforts=2)
    assert purge_activity(s, None, 1) == 0
    assert s.query(SegmentEffort).count() == 2


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
