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


class ProcessedActivity(Base):
    """Idempotency guard: one row per activity we have handled."""

    __tablename__ = "processed_activities"

    activity_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    athlete_id: Mapped[int] = mapped_column(BigInteger, index=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class SegmentEffort(Base):
    """Source of truth for per-user and (consent-filtered) community counts."""

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
