"""End-to-end webhook routing for TOS §6.3 deletion + private purge.

Drives POST /strava/webhook through FastAPI with an in-memory SQLite session
overriding get_session. Run: `.venv/bin/python tests/test_webhook_delete.py`.

Each activity is seeded the way the live path leaves it under the §6.2 cache: a
CountedActivity ledger row (keyed on a one-way HMAC, folded into WindowTally)
plus — for the pre-cutover window — some raw SegmentEffort rows. A delete event
must clear both.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import Athlete, CountedActivity, SegmentEffort
from app.tally import activity_hash, fold_activity

# StaticPool keeps one connection so every session shares the same in-memory DB.
engine = create_engine(
    "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)


def _override():
    s = Session()
    try:
        yield s
    finally:
        s.close()


client = TestClient(app)


@pytest.fixture(autouse=True)
def _override_session():
    """Scope the get_session override to each test. app.dependency_overrides is
    global on the shared app singleton, so a permanent module-level assignment
    gets clobbered by any other test module that sets/pops the same key (e.g.
    test_history_live) depending on run order. Save/restore keeps us isolated."""
    prev = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = _override
    try:
        yield
    finally:
        if prev is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = prev


def _seed(activity_id: int, athlete_id: int, n: int = 2):
    s = Session()
    if s.get(Athlete, athlete_id) is None:
        s.add(
            Athlete(
                athlete_id=athlete_id,
                access_token_enc="x",
                refresh_token_enc="x",
                expires_at=0,
            )
        )
        s.commit()
    # Fold the activity into the aggregate (crossings == the raw effort count).
    fold_activity(s, athlete_id, activity_id, n)
    # Plus the transient raw rows that exist before the fold-and-forget sweep.
    for i in range(n):
        s.add(
            SegmentEffort(
                strava_effort_id=activity_id * 1000 + i,
                athlete_id=athlete_id,
                activity_id=activity_id,
                segment_id=42,
                effort_start=datetime.now(timezone.utc),
            )
        )
    s.commit()
    s.close()


def _count(activity_id: int, athlete_id: int) -> int:
    s = Session()
    n = s.query(SegmentEffort).filter_by(activity_id=activity_id).count()
    if s.get(CountedActivity, (athlete_id, activity_hash(athlete_id, activity_id))):
        n += 1
    s.close()
    return n


def test_delete_event_purges():
    _seed(100, 1)
    r = client.post(
        "/strava/webhook",
        json={"object_type": "activity", "aspect_type": "delete",
              "object_id": 100, "owner_id": 1, "updates": {}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "purged", "removed": 3}  # 2 efforts + 1 ledger row
    assert _count(100, 1) == 0


def test_delete_unknown_activity_noop():
    r = client.post(
        "/strava/webhook",
        json={"object_type": "activity", "aspect_type": "delete",
              "object_id": 777, "owner_id": 1, "updates": {}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "purged", "removed": 0}


def test_private_update_purges():
    _seed(200, 2)
    r = client.post(
        "/strava/webhook",
        json={"object_type": "activity", "aspect_type": "update",
              "object_id": 200, "owner_id": 2, "updates": {"private": "true"}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "purged", "removed": 3}
    assert _count(200, 2) == 0


def test_title_update_ignored():
    _seed(300, 3)
    r = client.post(
        "/strava/webhook",
        json={"object_type": "activity", "aspect_type": "update",
              "object_id": 300, "owner_id": 3, "updates": {"title": "New"}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ignored"}
    assert _count(300, 3) == 3  # untouched: 2 efforts + 1 ledger row


if __name__ == "__main__":
    # The autouse fixture only runs under pytest; wire the override for the
    # standalone path.
    app.dependency_overrides[get_session] = _override
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
