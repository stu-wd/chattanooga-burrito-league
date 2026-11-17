"""Derived aggregate layer (§6.2 cache design, docs/compliance/03-cache-design.md).

Counts are owned as derived integers — WindowTally (per athlete/edition totals)
and CountedActivity (per-activity idempotency ledger) — instead of as retained raw
Strava effort rows. An activity is identified by a one-way HMAC of its id, never
the raw id, so we keep no Strava Data beyond the single fetch that produced it.

This module holds only the aggregate primitives + the one-time rebuild/verify used
at cutover. Wiring the live write path and repointing reads happen in later steps."""

import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Athlete, CountedActivity, SegmentEffort, WindowTally

log = logging.getLogger("burrito.tally")


def activity_hash(athlete_id: int, activity_id: int) -> str:
    """One-way, non-enumerable token identifying a counted activity without
    retaining the raw Strava activity id. Stable for a fixed cache_hash_key."""
    msg = f"{athlete_id}:{activity_id}".encode()
    return hmac.new(
        settings.cache_hash_key.encode(), msg, hashlib.sha256
    ).hexdigest()


def fold_activity(
    session: Session,
    athlete_id: int,
    activity_id: int,
    crossings: int,
    edition_key: str | None = None,
    commit: bool = True,
) -> bool:
    """Fold one activity's crossing count into the athlete's aggregate, exactly
    once. Idempotent: if this activity was already counted (same hash), do nothing
    and return False. Returns True when newly folded.

    This is the per-ACTIVITY idempotency that makes the raw per-EFFORT id
    unnecessary — the whole effort list arrives in one fetch, so counting the
    list length once per activity is sufficient."""
    edition_key = edition_key or settings.edition_key
    h = activity_hash(athlete_id, activity_id)

    if session.get(CountedActivity, (athlete_id, h)) is not None:
        return False

    session.add(
        CountedActivity(
            athlete_id=athlete_id,
            activity_hash=h,
            edition_key=edition_key,
            crossings=crossings,
        )
    )
    tally = session.get(WindowTally, (athlete_id, edition_key))
    if tally is None:
        tally = WindowTally(
            athlete_id=athlete_id, edition_key=edition_key,
            total_count=0, best_ride_count=0,
        )
        session.add(tally)
    tally.total_count += crossings
    tally.best_ride_count = max(tally.best_ride_count, crossings)
    # Flush so a newly-created tally/ledger row is visible to the next lookup even
    # under autoflush=False (the app's SessionLocal) — folding several activities
    # for the same athlete before a commit would otherwise duplicate the tally.
    session.flush()
    if commit:
        session.commit()
    return True


def already_written(session: Session, athlete_id: int, activity_id: int) -> bool:
    """Has this activity's description already been written? Replaces the old
    ProcessedActivity lookup (which held the raw activity id)."""
    ca = session.get(
        CountedActivity, (athlete_id, activity_hash(athlete_id, activity_id))
    )
    return ca is not None and ca.written_at is not None


def mark_written(session: Session, athlete_id: int, activity_id: int) -> None:
    """Record that we wrote the burrito block to this activity's description."""
    ca = session.get(
        CountedActivity, (athlete_id, activity_hash(athlete_id, activity_id))
    )
    if ca is not None:
        ca.written_at = datetime.now(timezone.utc)
        session.commit()


def rebuild_tallies_from_efforts(session: Session) -> dict:
    """Cutover migration: fold existing SegmentEffort rows (grouped per activity,
    within the active window) into WindowTally + CountedActivity. Idempotent —
    already-counted activities are skipped — so it is safe to re-run, including
    against a fresh/empty DB (then it is a no-op)."""
    rows = session.execute(
        select(
            SegmentEffort.athlete_id,
            SegmentEffort.activity_id,
            func.count(),
        )
        .where(
            SegmentEffort.segment_id == settings.target_segment_id,
            SegmentEffort.effort_start >= settings.competition_start,
            SegmentEffort.effort_start <= settings.competition_end,
        )
        .group_by(SegmentEffort.athlete_id, SegmentEffort.activity_id)
    ).all()

    folded = 0
    for athlete_id, activity_id, crossings in rows:
        if fold_activity(
            session, athlete_id, activity_id, crossings,
            edition_key=settings.edition_key, commit=False,
        ):
            folded += 1
    session.commit()
    result = {"groups": len(rows), "activities_folded": folded}
    log.info("rebuild_tallies_from_efforts: %s", result)
    return result


def _legacy_window_count(session: Session, athlete_id: int) -> int:
    """Raw-effort COUNT over the active window — the pre-§6.2 source of truth.
    Kept here (not via _window_total, which now reads WindowTally) so verification
    still compares the aggregate against the raw rows."""
    return session.scalar(
        select(func.count())
        .select_from(SegmentEffort)
        .where(
            SegmentEffort.athlete_id == athlete_id,
            SegmentEffort.segment_id == settings.target_segment_id,
            SegmentEffort.effort_start >= settings.competition_start,
            SegmentEffort.effort_start <= settings.competition_end,
        )
    ) or 0


def verify_tallies(session: Session) -> list[dict]:
    """Compare each athlete's new WindowTally.total_count against the legacy
    raw-effort count. Run before trusting the cutover (meaningful only while raw
    efforts are still present — i.e. before step 4's fold-and-forget)."""
    results = []
    for athlete_id in session.scalars(select(Athlete.athlete_id)).all():
        old = _legacy_window_count(session, athlete_id)
        tally = session.get(WindowTally, (athlete_id, settings.edition_key))
        new = tally.total_count if tally is not None else 0
        results.append(
            {"athlete_id": athlete_id, "old": old, "new": new, "match": old == new}
        )
    return results


def sweep_raw_efforts(session: Session, older_than_days: int | None = None) -> int:
    """Delete raw SegmentEffort rows. With older_than_days=None, purge ALL (the
    cutover case — they are already folded). Otherwise purge only rows cached
    longer than the cutoff (the ongoing §6.2 guard). Returns rows deleted."""
    stmt = delete(SegmentEffort)
    if older_than_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        stmt = stmt.where(SegmentEffort.detected_at < cutoff)
    removed = session.execute(stmt).rowcount or 0
    session.commit()
    if removed:
        log.info("sweep_raw_efforts: purged %s rows (older_than_days=%s)",
                 removed, older_than_days)
    return removed


def ensure_compliant_cache(session: Session) -> dict:
    """Boot-time §6.2 cutover, idempotent and self-healing: if any legacy raw
    efforts remain, fold them into the aggregate, verify the new totals equal the
    raw counts, and only then purge ALL raw efforts. No-op on a clean/fresh DB, so
    a running instance holds zero raw Strava Data within seconds of startup."""
    if session.scalar(select(SegmentEffort.id).limit(1)) is None:
        return {"status": "clean"}
    rebuild_tallies_from_efforts(session)
    verification = verify_tallies(session)
    if not all(v["match"] for v in verification):
        log.error(
            "ensure_compliant_cache: tally verify MISMATCH — leaving raw efforts "
            "in place for inspection: %s", verification
        )
        return {"status": "mismatch", "verify": verification}
    purged = sweep_raw_efforts(session)  # all — they are now folded
    log.info("ensure_compliant_cache: cutover complete, purged %s raw rows", purged)
    return {"status": "cutover", "purged": purged}
