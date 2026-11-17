"""End-to-end webhook routing for TOS §6.3 deletion + private purge.

Drives POST /strava/webhook through FastAPI with an in-memory SQLite session
overriding get_session. Run: `.venv/bin/python tests/test_webhook_delete.py`.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.models import ProcessedActivity, SegmentEffort

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


app.dependency_overrides[get_session] = _override
client = TestClient(app)


def _seed(activity_id: int, athlete_id: int, n: int = 2):
    s = Session()
    s.add(ProcessedActivity(activity_id=activity_id, athlete_id=athlete_id, matched=True))
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


def _count(activity_id: int) -> int:
    s = Session()
    n = (
        s.query(SegmentEffort).filter_by(activity_id=activity_id).count()
        + s.query(ProcessedActivity).filter_by(activity_id=activity_id).count()
    )
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
    assert r.json() == {"status": "purged", "removed": 3}
    assert _count(100) == 0


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
    assert _count(200) == 0


def test_title_update_ignored():
    _seed(300, 3)
    r = client.post(
        "/strava/webhook",
        json={"object_type": "activity", "aspect_type": "update",
              "object_id": 300, "owner_id": 3, "updates": {"title": "New"}},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ignored"}
    assert _count(300) == 3  # untouched


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
