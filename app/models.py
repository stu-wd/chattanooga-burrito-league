from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Athlete(Base):
    __tablename__ = "athletes"

    athlete_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[int] = mapped_column(Integer)  # epoch seconds
    scopes: Mapped[str] = mapped_column(String(255), default="")
    community_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    # Chosen name shown on the leaderboard. Required when community_consent is True
    # (the affirmative opt-in + name together = explicit consent under Strava's TOS).
    display_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # How the burrito block is written to the activity: append | prepend | off
    write_mode: Mapped[str] = mapped_column(String(8), default="append")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class SegmentEffort(Base):
    """LEGACY raw cache (pre-§6.2). No longer written — process_activity now folds
    crossings straight into WindowTally/CountedActivity and keeps no raw efforts.
    The model is retained only so the boot cutover can fold-then-purge any rows
    left in an existing DB. Holds raw Strava Data; must never be repopulated."""

    __tablename__ = "segment_efforts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Strava's unique per-effort id — one per segment crossing. Dedupes recounts.
    strava_effort_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    athlete_id: Mapped[int] = mapped_column(BigInteger, index=True)
    activity_id: Mapped[int] = mapped_column(BigInteger, index=True)
    segment_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # When the crossing happened per Strava (UTC) — drives the competition window.
    effort_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class WindowTally(Base):
    """Durable derived aggregate (§6.2 cache design): our computed counts per
    athlete per edition. Holds only integers we calculated — NOT cached Strava
    Data — so it survives indefinitely past the 7-day cap."""

    __tablename__ = "window_tallies"

    athlete_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Our own label for the competition window (settings.edition_key), e.g. 2027-01.
    edition_key: Mapped[str] = mapped_column(String(16), primary_key=True)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    # Most crossings in a single activity — the leaderboard "biggest ride" metric.
    best_ride_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CountedActivity(Base):
    """Per-activity idempotency ledger (§6.2 cache design). Keyed on a one-way
    HMAC of the activity id — NOT the raw Strava id — so re-folding the same
    activity (webhook retry, backfill re-run) can't double-count, while retaining
    no raw Strava Data. Stores only our derived crossing count; kept indefinitely."""

    __tablename__ = "counted_activities"

    athlete_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # HMAC-SHA256 hex of f"{athlete_id}:{activity_id}" keyed by cache_hash_key.
    activity_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    edition_key: Mapped[str] = mapped_column(String(16), index=True)
    crossings: Mapped[int] = mapped_column(Integer)
    counted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    # Set once the burrito block has been written to this activity's description —
    # the write-idempotency guard that replaces the old ProcessedActivity row
    # (which keyed on the raw Strava activity id). NULL = counted but not written.
    written_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class Job(Base):
    """Durable DB-backed work queue drained by a rate-limited worker."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(
        String(16), default="process", index=True
    )  # process | backfill
    activity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    athlete_id: Mapped[int] = mapped_column(BigInteger, index=True)
    write: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
