# Prompt — §6.3: reflect Strava activity deletions within 48h

Paste everything below into a fresh Claude Code session in the `chattanooga-burrito-league` repo.

---

You are working on the Chattanooga Burrito League FastAPI app. This is a **Strava API Policy
compliance fix**. Read `docs/compliance/README.md` first for the governing clauses, then do the
task below.

## The rule

Strava API Policy **§6.3**: "Deletions must be reflected in your Developer Application
expeditiously but in all cases within forty-eight (48) hours." Combined with **§6.2/§6.4** (retain
Strava Data only as long as needed for its original purpose), when an athlete deletes an activity
on Strava, any Strava-derived data we cached for that activity must be removed.

## The gap (verified)

`app/webhook.py` only handles two event types:

- `object_type == "athlete"` with `updates.authorized == "false"` → `purge_athlete(...)` (deauth).
- `object_type == "activity"` with `aspect_type == "create"` → `enqueue_process(...)`.

Everything else falls through to `return {"status": "ignored"}` at `app/webhook.py:48`. So a Strava
**activity `delete`** event is silently ignored: the `SegmentEffort` rows and the
`ProcessedActivity` row we stored for that activity survive forever. Because the public leaderboard
and every athlete's window total are computed live by counting `SegmentEffort` rows
(`app/processing.py:_window_total`, `app/leaderboard.py:leaderboard_data`), a deleted activity keeps
inflating totals indefinitely. That is a §6.3 violation and also skews the competition.

## Strava webhook event shape (for reference)

A delete event POSTed to `/strava/webhook` looks like:

```json
{
  "object_type": "activity",
  "aspect_type": "delete",
  "object_id": 123456789,        // the activity id
  "owner_id": 156536423,         // the athlete id
  "subscription_id": 12345,
  "event_time": 1700000000,
  "updates": {}
}
```

`update` events use `"aspect_type": "update"` and carry an `updates` object, e.g.
`{"private": "true"}` when an athlete flips an activity to private, or `{"title": "..."}`.

## What to build

### 1. A purge-one-activity helper

Add a function next to `purge_athlete` in `app/athletes.py` (it already owns lifecycle deletes and
imports the right models):

```python
def purge_activity(session: Session, activity_id: int | None, athlete_id: int | None = None) -> int:
    """Remove all cached Strava-derived data for a single activity (TOS §6.3:
    reflect a Strava-side deletion within 48h). Returns rows deleted.
    athlete_id, when given, scopes the delete defensively so one athlete's
    event can never touch another athlete's rows."""
```

- Delete matching `SegmentEffort` rows (filter `activity_id`, and `athlete_id` when provided).
- Delete the `ProcessedActivity` row for that `activity_id` (same athlete scoping).
- Do **not** touch `Athlete`, tokens, or `Job` rows — this is per-activity, not per-athlete.
- `commit()`, log at info with the activity id and the count removed, return the count.
- Be idempotent: an activity we never cached deletes 0 rows and must not error.

### 2. Wire it into the webhook

In `app/webhook.py`, before the final `return {"status": "ignored"}`, handle activity deletion:

```python
if object_type == "activity" and aspect_type == "delete":
    removed = purge_activity(session, object_id, owner_id)
    log.info("activity delete %s owner %s removed=%s", object_id, owner_id, removed)
    return {"status": "purged", "removed": removed}
```

Do this **inline** (not via the job queue). It is a pure local DB delete with no Strava HTTP call,
so it is fast, the webhook stays within its ack-fast budget, and the deletion is reflected
immediately — far inside the 48h window. (The existing `receive` handler already takes a
`session: Session = Depends(get_session)`, so the session is in scope.)

### 3. Decide on `update` + `private` (ask me, then implement)

There is a judgment call on `aspect_type == "update"` with `updates == {"private": "true"}`. Two
readings:

- **Conservative:** an activity made private is no longer something the athlete is publicly
  sharing; purge its cached efforts too (same `purge_activity` call). Safest under §6.4.
- **Minimal:** the data is still the athlete's own, still only ever shown back to that athlete, so
  §2.3 is satisfied and no purge is required; only hard deletes trigger §6.3.

**Stop and ask me which reading to implement before writing the privacy branch.** Implement the
hard-`delete` handler regardless — that part is unambiguous.

## Acceptance criteria

- A POST to `/strava/webhook` with `aspect_type: "delete"` for an activity that has cached efforts
  removes exactly that activity's `SegmentEffort` and `ProcessedActivity` rows, and nothing else.
- The same event for an unknown activity returns cleanly with `removed: 0`.
- After a delete, `_window_total(...)` and `leaderboard_data(...)` reflect the lower count with no
  other change (verify by counting rows before/after; no need to re-write any Strava description —
  we deliberately do not re-edit historical activities).
- An athlete's delete event can never delete another athlete's rows (the `athlete_id` scope holds).
- Existing `create` and deauth paths are untouched and still pass.

## Testing

- Add/extend tests under the repo's test setup (check how existing tests run — look for a
  `tests/` dir or test config; if none exists, create a minimal `pytest` test using an in-memory
  SQLite session like the app's `get_session`). Cover: delete removes the right rows; delete of an
  uncached activity is a no-op; delete scoped to the owner.
- Manually: run `.venv/bin/uvicorn app.main:app --reload --port 8000`, seed a `SegmentEffort` +
  `ProcessedActivity` (via `/dev/login` + a backfill, or direct DB insert), POST a crafted delete
  event with `curl`, confirm the rows are gone and totals drop.

## Out of scope

- Re-writing historical activity descriptions to update their cumulative totals (we never re-edit
  past activities; stale in-description numbers are acceptable and avoid extra Strava writes).
- The 7-day cache redesign (that is item #3) and the leaderboard decision (item #2).

## Report back

Use the house style: **Was / Think / Fix / Blast radius**. Cite `file:line`. Do not echo file
contents back after editing.
