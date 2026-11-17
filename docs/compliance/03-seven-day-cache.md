# Prompt — §6.2: the DB caches Strava Data indefinitely

Paste everything below into a fresh Claude Code session in the `chattanooga-burrito-league` repo.

---

You are working on the Chattanooga Burrito League FastAPI app. This is the **hardest** Strava API
Policy compliance item: it collides with the app's core architecture. Read
`docs/compliance/README.md` first. **Your first deliverable is a written design doc, not code.**
Produce the design, get my sign-off, *then* implement. Do not start a schema migration before I
approve the approach.

## The rule

Strava API Policy **§6.2**: "You may not retain Strava Data in your cache for longer than seven (7)
days." Reinforced by **§6.4**: "you may not retain Data, and you may use and retain Data only so
long as necessary for the purpose for which it was originally obtained."

## The tension (verified)

The app's design philosophy is "counts are **owned in our DB**, not re-queried" — totals,
leaderboard, and history are all computed from locally stored rows rather than re-fetched from
Strava. Concretely, `app/models.py` `SegmentEffort` stores, **indefinitely**, one row per segment
crossing:

- `strava_effort_id` — Strava's unique per-effort identifier (clearly Strava Data).
- `activity_id`, `segment_id` — Strava identifiers.
- `effort_start` — the Strava-provided UTC timestamp of the crossing (clearly Strava Data).
- `athlete_id`, plus local `id` / `detected_at`.

These rows are the source of truth for:

- `app/processing.py` `_window_total(as_of=...)` — per-athlete cumulative window total (the number
  written back into descriptions), and the per-effort dedup in `process_activity` (keyed on
  `strava_effort_id`).
- `app/leaderboard.py` `leaderboard_data()` — standings + "most in one ride".
- The `/history` page (`app/web.py` + `app/templates/history.html`) — own rolls grouped by
  activity, with EST dates and "View on Strava" links.

`ProcessedActivity` similarly stores `activity_id` + `processed_at` forever as a write-idempotency
guard.

Storing `strava_effort_id` + `effort_start` + `activity_id` for months violates §6.2. The data is
retained far longer than 7 days and far longer than needed to perform the one write-back it was
fetched for.

## Design task — produce `docs/compliance/03-cache-design.md` covering:

### 1. Classify every stored field
For each column in `SegmentEffort` and `ProcessedActivity`, state whether it is **Strava Data**
(subject to the 7-day cap) or **our own derived data** (not Strava Data). Be explicit about the
interpretive edge: a raw `strava_effort_id` and Strava's `effort_start` timestamp are Strava Data;
a plain integer *count of burritos* we computed is arguably our own derived metric. A defensible
compliant design keeps the **derived aggregates** and purges the **raw Strava identifiers and
timestamps** within 7 days. Where a field is genuinely ambiguous, flag it and recommend the
conservative treatment.

### 2. Target schema
Propose a schema that preserves the app's functional invariants while holding no raw Strava Data
beyond 7 days. A likely shape (refine it):

- A durable **aggregate** table — e.g. `WindowTally(athlete_id, edition_key, total_count,
  best_ride_count, best_ride_date?, updated_at)` — storing only derived integers (+ whatever
  minimal timestamp the competition actually needs). This survives indefinitely because it is our
  computed metric, not cached Strava Data.
- A short-lived **raw ledger** (the current `SegmentEffort`, or a slimmer version) used only within
  the 7-day window for dedup and for recomputation, swept on a schedule.
- Activity-level idempotency for the **tally** path, not just the write path — see the dedup
  problem below.

### 3. Solve the dedup-after-purge problem (the crux)
Today dedup relies on `strava_effort_id` uniqueness, and the **tally-only** path (backfill,
`write=False`) has **no** `ProcessedActivity` guard (`app/processing.py:154-159` only guards the
*write* path). If you purge effort rows after 7 days, a later re-fetch of the same activity (e.g. a
backfill re-run) would **double-count**. To make raw effort ids unnecessary beyond the current run,
you need **per-activity idempotency for tallying** too: once an activity's crossings have been
folded into the aggregate, record that fact (activity id or a hash) so it is never counted again —
then the raw per-effort rows can be dropped. Design this carefully; it is the thing that makes the
7-day purge safe.

### 4. Confront the `/history` and cumulative-total implications
- `_window_total(as_of=...)` depends on per-effort `effort_start` timestamps to produce the
  *running month-to-date* number written into each description. If raw timestamps are purged after
  7 days, decide how the cumulative-through-this-activity total is still computed (e.g. store a
  per-activity derived row: activity's crossing count + its own date, treated as derived — or
  accept that only the live aggregate total is available). Spell out the behavior change.
- `/history` currently renders cached `activity_id` + dates + "View on Strava" links older than 7
  days. If `activity_id`/`effort_start` are Strava Data, **`/history` as built is also a §6.2
  problem.** Either (a) re-fetch history live from Strava on page load (within the "fetch, use,
  don't cache >7d" model, at rate-limit cost), or (b) reduce `/history` to derived aggregates only.
  Flag this explicitly — it is a second non-compliant surface this redesign must address, not just
  the leaderboard/totals.

### 5. The 7-day sweep
Specify a scheduled purge that deletes raw Strava Data older than 7 days. The app already runs a
single async worker loop (`app/worker.py` `run_worker`) — propose adding a periodic sweep there (or
a separate `asyncio` task started in `app/main.py` lifespan), with the cutoff configurable. Ensure
the sweep runs even on the free tier where the web service **sleeps when idle** (note: a sleeping
Render free instance won't run background sweeps — call this out as a deployment caveat, since it
means raw rows could sit un-swept while the service is asleep; the sweep must catch up on wake).

### 6. Migration + rollout
- A migration that creates the aggregate table and **backfills it from existing `SegmentEffort`
  rows** before any purge begins (so historical totals survive the cutover).
- Order of operations so no total is lost: build aggregates → verify counts match → enable sweep.
- Note the free-tier reality (from project memory): Postgres is ephemeral and wiped on redeploy, so
  the migration must be idempotent and safe to re-run against a fresh DB.

## Constraints / invariants to preserve
- Per-athlete window total must remain correct (it is the number written into Strava descriptions).
- The append-only write-back invariant in `_compose_description` is untouched by this work.
- Idempotency: no activity's crossings may be counted twice across webhook retries, backfills, or
  re-fetches.
- Whatever the leaderboard becomes (see item #2) must still be computable — coordinate with that
  decision; if the leaderboard is removed/hidden, the aggregate schema can be simpler.

## Deliverable sequence
1. Write `docs/compliance/03-cache-design.md` with sections 1–6 above and a recommended approach.
2. **Stop and get my sign-off.**
3. Implement in small, reviewable steps: aggregate table + backfill migration → tally idempotency →
   repoint reads (`_window_total`, history, leaderboard) → add the sweep → finally shorten raw
   retention. Verify totals are identical before/after at each step.

## Report back
House style: **Was / Think / Fix / Blast radius**, `file:line` citations, no echoing file contents.
Update `strava-tos-constraints.md` in project memory once the design is locked.
