"""Business logic for a single activity: detect the burrito segment, tally,
build the description, write it back. TOS note: the description contains ONLY the
athlete's own data (this ride's crossings + their own competition-window total),
written to their own activity — no cross-athlete aggregation."""

import logging
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import strava
from .config import settings
from .models import Athlete, ProcessedActivity, SegmentEffort

log = logging.getLogger("burrito.processing")


def _matching_efforts(activity: dict) -> list[dict]:
    """Every crossing of the target segment in this activity (one per lap)."""
    return [
        effort
        for effort in (activity.get("segment_efforts") or [])
        if (effort.get("segment") or {}).get("id") == settings.target_segment_id
    ]


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _effort_start(effort: dict) -> datetime | None:
    return _parse_dt(effort.get("start_date"))  # UTC ISO 8601


def _activity_in_window(activity: dict) -> bool:
    start = _parse_dt(activity.get("start_date"))
    if start is None:
        return False
    return settings.competition_start <= start <= settings.competition_end


def _window_total(
    session: Session, athlete_id: int, as_of: datetime | None = None
) -> int:
    """Athlete's own crossings within the window, cumulative through `as_of`.
    `as_of` = this activity's latest crossing, so each activity shows the running
    month-to-date total (the final activity lands on the grand total)."""
    upper = settings.competition_end
    if as_of is not None and as_of < upper:
        upper = as_of
    return session.scalar(
        select(func.count())
        .select_from(SegmentEffort)
        .where(
            SegmentEffort.athlete_id == athlete_id,
            SegmentEffort.segment_id == settings.target_segment_id,
            SegmentEffort.effort_start >= settings.competition_start,
            SegmentEffort.effort_start <= upper,
        )
    ) or 0


# Our block is bracketed by START/END so it can be located and stripped from
# anywhere in the description (top or bottom) — never touching the user's own text,
# and idempotent even when the athlete switches write_mode.
BLOCK_START = "—— 🌯 Chattanooga Burrito League ——"
BLOCK_END = "—— end 🌯 ——"

_BLOCK_RE = re.compile(
    re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END),
    re.DOTALL,
)


def _build_body(this_ride: int, total: int) -> str:
    ride_noun = "burrito" if this_ride == 1 else "burritos"
    total_noun = "burrito" if total == 1 else "burritos"
    return (
        f"🌯 You rolled {this_ride} {ride_noun} today!\n"
        f"You've rolled {total} {total_noun} this month!"
    )


def _strip_block(text: str) -> str:
    """Remove our block (and any legacy sentinel-less tail) wherever it sits,
    returning only the athlete's own text."""
    cleaned = _BLOCK_RE.sub("", text or "")
    # Legacy: a START with no END (pre-dual-sentinel) — drop from START onward.
    cleaned = cleaned.split(BLOCK_START)[0]
    return cleaned.strip()


def _compose_description(existing: str, body: str, mode: str) -> str:
    """Preserve the athlete's own text; place our block per `mode`.
    mode: append (below) | prepend (above) | off (leave unchanged)."""
    user = _strip_block(existing)
    if mode == "off":
        return user
    block = f"{BLOCK_START}\n{body}\n{BLOCK_END}"
    if not user:
        return block
    return f"{block}\n\n{user}" if mode == "prepend" else f"{user}\n\n{block}"


def process_activity(
    session: Session, athlete: Athlete, activity_id: int, write: bool = True
) -> str:
    """Detect crossings, tally them, and (if write) update the description.

    write=False is 'tally-only' — record efforts but do not touch the activity's
    description or mark it written. Used by backfill to populate totals without
    editing historical activities."""
    activity = strava.get_activity(session, athlete, activity_id)

    if not _activity_in_window(activity):
        return "out of window"

    efforts = _matching_efforts(activity)
    if not efforts:
        return "no match"

    # Record each crossing once, keyed by Strava's effort id (idempotent recount).
    for effort in efforts:
        effort_id = effort.get("id")
        if effort_id is None:
            continue
        already = session.scalar(
            select(SegmentEffort.id).where(
                SegmentEffort.strava_effort_id == effort_id
            )
        )
        if already is None:
            session.add(
                SegmentEffort(
                    strava_effort_id=effort_id,
                    athlete_id=athlete.athlete_id,
                    activity_id=activity_id,
                    segment_id=settings.target_segment_id,
                    effort_start=_effort_start(effort),
                )
            )
    session.commit()

    this_ride = len(efforts)
    effort_times = [t for t in (_effort_start(e) for e in efforts) if t is not None]
    as_of = max(effort_times) if effort_times else None
    total = _window_total(session, athlete.athlete_id, as_of=as_of)

    # Tally-only when the job says so (backfill) or the athlete opted out of writes.
    if not write or athlete.write_mode == "off":
        reason = "write off" if athlete.write_mode == "off" else "tally-only"
        return f"tallied {this_ride} ({reason}, window total {total})"

    if session.get(ProcessedActivity, activity_id) is not None:
        return f"already written (window total {total})"

    body = _build_body(this_ride, total)
    description = _compose_description(
        activity.get("description") or "", body, athlete.write_mode
    )
    strava.update_description(session, athlete, activity_id, description)
    session.add(
        ProcessedActivity(
            activity_id=activity_id, athlete_id=athlete.athlete_id, matched=True
        )
    )
    session.commit()
    log.info("Updated activity %s: %r", activity_id, description)
    return f"matched — {description!r}"
