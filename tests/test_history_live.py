"""§6.2 step 3 — /history renders from a LIVE Strava activity list + the derived
CountedActivity ledger, with no reliance on retained effort rows.

Runnable standalone (`.venv/bin/python tests/test_history_live.py`) or under pytest."""

import os
import sys
import tempfile
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import strava, web  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import Base, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Athlete  # noqa: E402
from app.tally import fold_activity  # noqa: E402


def test_history_renders_live_list_plus_ledger():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = maker()

    try:
        athlete = Athlete(
            athlete_id=42, access_token_enc="x", refresh_token_enc="x", expires_at=0
        )
        session.add(athlete)
        session.commit()
        # Activity 7 counted with 4 crossings (hash-keyed, no raw ids retained).
        fold_activity(session, 42, 7, 4)

        when = (settings.competition_start + timedelta(hours=1)).isoformat()
        # Strava's live list: activity 7 (counted) + activity 99 (never counted).
        strava.get_athlete_activities = (
            lambda s, a, after=None, before=None, page=1, per_page=100: (
                [
                    {"id": 7, "start_date": when},
                    {"id": 99, "start_date": when},
                ]
                if page == 1
                else []
            )
        )

        app.dependency_overrides[get_session] = lambda: session
        web._session_athlete = lambda request, sess: athlete
        try:
            client = TestClient(app)
            resp = client.get("/history")
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert resp.status_code == 200, resp.status_code
        body = resp.text
        assert "4" in body                      # crossing count from the ledger
        assert "/activities/7" in body          # View-on-Strava link, live id
        assert "/activities/99" not in body     # uncounted activity is excluded
    finally:
        session.close()
        os.unlink(path)


if __name__ == "__main__":
    test_history_renders_live_list_plus_ledger()
    print("OK test_history_renders_live_list_plus_ledger")
