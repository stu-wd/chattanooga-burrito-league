# §6.2 cache design — 7-day retention cap

**Status:** IMPLEMENTED (2026-11-17, branch `compliance/02-leaderboard-flag-off`). Full
fold-and-forget: the app persists **zero raw Strava Data** — `WindowTally` + `CountedActivity`
(derived integers, one-way `HMAC` activity key) replace `SegmentEffort` and `ProcessedActivity`.
`process_activity` folds crossings and discards raw efforts; `_window_total`, leaderboard, and
`/history` (live re-fetch) read the aggregate; a boot-time cutover (`ensure_compliant_cache`) folds
then purges any legacy raw rows; a worker sweep is the ongoing guard. All four open questions below
resolved. **Caveat (no Alembic):** `create_all` won't ALTER an existing table — adding a column needs
a fresh DB (prod Postgres is ephemeral) or a manual ALTER; the derived tables are rebuildable from
raw efforts / Strava. **§6.3 interaction:** when branch `compliance/01` merges, its `purge_activity`
must be reworked to recompute the aggregate (subtract the activity's `CountedActivity.crossings`,
recompute `best_ride_count`) instead of deleting effort rows.

**Coordination with item #2 (resolved).** The leaderboard decision landed as **Option C** (commit
`93d22c7`, `compliance/02-leaderboard-flag-off`): `/leaderboard` is gated behind `LEADERBOARD_ENABLED`
(default `false`) and `leaderboard.py` + the `community_consent`/`display_name` columns are **kept
intact so flipping the flag restores everything** once Strava rules on §6.1 (see
[`02a-strava-support-email.md`](02a-strava-support-email.md)). It was **not** dropped. Consequence for
this design: `WindowTally` **must keep `best_ride_count`** and the leaderboard must remain computable
from derived aggregates — so re-enabling the flag still works without reintroducing raw Strava Data.

**Clause.** §6.2: "You may not retain Strava Data in your cache for longer than seven (7) days."
Reinforced by §6.4 (retain Data only as long as necessary for the original purpose).

**The collision.** The app's design philosophy is "counts are owned in our DB, not re-queried."
Today that is literally implemented as one indefinitely-retained raw `SegmentEffort` row per
segment crossing, carrying Strava's `strava_effort_id`, `activity_id`, and `effort_start`. Those are
raw Strava Data retained for months — a direct §6.2 violation. This doc keeps the *philosophy*
(counts owned locally, not re-queried) while making the stored thing a **derived integer**, not
cached Strava Data.

---

## 1. Field classification

Legend: **S** = Strava Data (7-day cap applies) · **O** = our own derived data (not capped) ·
**⚠** = interpretive edge, conservative call noted.

### `SegmentEffort` ([app/models.py:46](app/models.py))

| Column | Class | Reasoning |
|---|---|---|
| `id` | O | Local autoincrement PK. Ours. |
| `strava_effort_id` | **S** | Strava's unique per-effort identifier. Raw Strava Data. **Must be purged ≤7d.** |
| `athlete_id` | ⚠→keep | Strava's athlete id. It is Strava-issued, but it is the *account identity* we are authorized to key everything on — the same class of thing as the OAuth tokens we hold. Treated as an account key, not cached activity data. Retained like the `Athlete` row itself. |
| `activity_id` | **S** | Strava's activity identifier. Raw Strava Data. This is the field that also makes `/history` non-compliant (see §4). **Must be purged ≤7d.** |
| `segment_id` | O (constant) | Always `settings.target_segment_id` (40758296) — a fixed app config value, identical on every row, not user-specific. Not meaningfully "retained Strava Data." |
| `effort_start` | **S** | Strava-provided UTC crossing timestamp. Raw Strava Data. **Must be purged ≤7d.** |
| `detected_at` | O | When *we* processed it. Ours — and the correct retention clock (how long *we* have cached). |

### `ProcessedActivity` ([app/models.py:33](app/models.py))

| Column | Class | Reasoning |
|---|---|---|
| `activity_id` (PK) | **S** → replace | Raw Strava identifier used purely as an idempotency key. Keeping it raw is non-compliant; replace with a derived one-way token (see §3). |
| `athlete_id` | ⚠→keep | Account key, as above. |
| `matched` | O | Our boolean. |
| `processed_at` | O | Our timestamp. |

**The one genuinely ambiguous element** is the per-activity idempotency key. We *need* to remember
"already counted this activity" forever (that is what prevents double-counting on re-fetch), but the
raw `activity_id` is Strava Data. Recommended conservative treatment: store
`HMAC-SHA256(stable_secret, "{athlete_id}:{activity_id}")` instead of the raw id — a one-way,
non-enumerable token that can't query Strava or identify the activity to anyone, functionally a
deterministic idempotency nonce. It is a MAC (someone holding the secret *and* a candidate id can
confirm a match), so it is pseudonymized, not anonymized — but it sits defensibly between "keep the
raw id" (clearly non-compliant) and "keep nothing" (can't dedup). Secret must be stable; see §3.

---

## 2. Target schema

Two new durable tables of **derived data only**, plus retiring raw `SegmentEffort` as a durable
store. Recommended shape:

### `WindowTally` — durable aggregate (our computed metric, never swept)
```
athlete_id      BIGINT   -- account key
edition_key     STRING   -- our label, e.g. "2027-01", derived from the config window
total_count     INT      -- cumulative crossings this edition (derived integer)
best_ride_count INT      -- most crossings in a single activity (for leaderboard highlight)
updated_at      TIMESTAMP
PRIMARY KEY (athlete_id, edition_key)
```
Holds no raw Strava Data, so it survives indefinitely — it is our metric, not a cache of Strava's.
Note: **no `best_ride_date`** — a date tied to a specific activity is Strava Data. The leaderboard
"most in one ride" needs only the count, so we drop the date.

### `CountedActivity` — idempotency ledger (derived tokens only, kept indefinitely)
```
athlete_id    BIGINT
edition_key   STRING
activity_hash STRING   -- HMAC(secret, "{athlete_id}:{activity_id}"), one-way (§3)
crossings     INT      -- this activity's crossing count (derived integer)
counted_at    TIMESTAMP
PRIMARY KEY (athlete_id, activity_hash)
```
Replaces both the per-effort dedup role of `SegmentEffort` and the raw-id role of
`ProcessedActivity`. Carries no raw Strava Data. The per-activity `crossings` integer is what lets
`/history` and the cumulative total survive purge (§4).

### Raw efforts — **fold-and-forget (recommended), no durable raw table**
The strong recommendation is that `process_activity` fold an activity's crossings straight into
`WindowTally` + `CountedActivity` and **never persist** `strava_effort_id` / `effort_start` /
`activity_id` at all. Then the DB holds **zero raw Strava Data at rest** and §6.2 is satisfied by
construction — there is nothing to sweep.

Why this is safe here: an activity's *full* effort list arrives together in one
`GET /activities/{id}` fetch, so correctness only needs **per-activity** idempotency, never
per-effort (see §3). The raw `strava_effort_id` becomes unnecessary the moment the fetch completes.

Trade-off vs. the prompt's "short-lived raw ledger": fold-and-forget loses the ability to *replay*
the tally from stored rows if the fold logic ever has a bug. For this app that is acceptable —
Strava is always the ultimate source and `backfill` already re-derives from it. If replay-ability is
wanted, the alternative is to keep a slim `SegmentEffort` and sweep it at 7 days (§5); the sweep is
specified either way, because it must also purge any **pre-migration** rows.

---

## 3. Dedup after purge (the crux)

**Today's gap.** Dedup relies on `strava_effort_id` uniqueness, and the tally-only path
(`write=False` backfill) has **no** `ProcessedActivity` guard —
[app/processing.py:154-159](app/processing.py) only guards the *write* path. Re-running a `write=False`
backfill today re-inserts nothing only because of the per-effort unique constraint; once raw effort
rows are purged, that constraint is gone and the same activity would be counted again.

**The fix — per-activity idempotency, from a one-way key.**
1. On every `process_activity`, compute `activity_hash = HMAC(secret, f"{athlete_id}:{activity_id}")`.
2. If `CountedActivity(athlete_id, activity_hash)` exists → **already folded, return early.** This is
   the per-activity guard the tally path lacks today; it covers webhook retries, backfill re-runs,
   and any re-fetch — on *both* the write and tally-only paths.
3. If new → count crossings in this fetch (`len(efforts)`), insert `CountedActivity(..., crossings)`,
   and `WindowTally.total_count += crossings`, `best_ride_count = max(best, crossings)`, in one
   transaction.

Because the full effort list is in-hand per fetch, we count the list length once and discard the raw
ids — per-effort dedup is no longer needed, so purging raw effort rows is safe.

**Secret stability.** `activity_hash` is only durable if its key never changes — a rotated secret
re-hashes every activity and reintroduces double-counting. Recommendation: derive a dedicated,
*non-rotating* `cache_hash_key` (store it, independent of `session_secret`, which may rotate). Flag
in config with a comment that rotating it requires a full tally rebuild.

**Known behavior change.** If an athlete later *edits* an activity to add laps and Strava re-emits
it, per-activity idempotency skips the recount (first count is authoritative). Today's per-effort
model would pick up the new laps. For a private describer this is rare and acceptable; if it matters,
wire the activity `update` webhook to delete the `CountedActivity` row, decrement the tally by its
old `crossings`, and re-fold. Recommend: accept the skip for v1, note the webhook option.

---

## 4. `/history` and cumulative-total implications

### Cumulative "running month-to-date" total written into descriptions
`_window_total(as_of=...)` today filters per-effort `effort_start <= as_of` to write a *running* MTD
number ([app/processing.py:46-64](app/processing.py)). After purge there are no per-effort timestamps.

Key observation: **we don't need them if processing is chronological.** We only ever count
in-window activities, and after folding activity A, `WindowTally.total_count` already includes A plus
everything counted before it — i.e. it *is* the running total through A. The live webhook path is
naturally chronological (you ride, it's processed within minutes); `backfill` is *explicitly*
oldest-first for exactly this reason ([app/backfill.py:23-37](app/backfill.py)). So the written number
becomes `total_count` immediately after the fold, and under chronological processing it equals
today's MTD number.

**Behavior change (spell out):** for an **out-of-order** arrival (a late older activity processed
after a newer one already bumped the tally), the older activity's written number would read as the
*current* total rather than "total through its own date." This affects only the transient in-text
number at write time; we never rewrite past descriptions, and the authoritative **final grand total
is order-independent** (it's just the sum). Acceptable. Optional tightening: within the 7-day window
those efforts still have live timestamps if we briefly retain them, but recommend not bothering.

### `/history` is a second non-compliant surface
`/history` ([app/web.py:79-115](app/web.py)) renders cached `activity_id` + `effort_start` dates +
"View on Strava" links for activities arbitrarily older than 7 days. Both `activity_id` and
`effort_start` are Strava Data → **`/history` as built also violates §6.2**, independent of the
leaderboard/totals. This redesign must address it. Options:

- **(a) Live re-fetch** on page load (fetch-use-don't-retain). Preserves dates + per-activity
  "View on Strava" links, at rate-limit cost (1 + N Strava calls per view).
- **(b) Derived aggregates only** — render from `CountedActivity`: grand total + per-activity
  `crossings`, **no dates, no Strava links** (both need purged Strava Data).

**Recommendation: (b) as the default** (zero rate cost, fully compliant), with (a) available as an
opt-in richer view. Be honest about the UX downgrade: without `activity_id` there is no "View on
Strava" link and without `effort_start` no date — so (b) realistically collapses `/history` to
"you've rolled N this month" plus an unlabeled per-activity count list/sparkline. If that is too thin,
choose (a) and pay the rate cost. **This is a product call — flagging for your decision in §Open
questions.**

---

## 5. The 7-day sweep

Even under fold-and-forget, a sweep is specified — it purges any **pre-migration** `SegmentEffort`
rows and is belt-and-suspenders if a slim raw ledger is kept.

- **Mechanism:** a periodic task that runs
  `DELETE FROM segment_efforts WHERE detected_at < now() - interval '{cache_retention_days} days'`.
  Clock on `detected_at` (our own field = how long *we* cached it). `cache_retention_days: int = 7`
  in config, configurable.
- **Where:** add a time-gated branch to the existing worker loop
  ([app/worker.py:108-119](app/worker.py)) — track `last_sweep`, run at most hourly between queue
  polls. No new task needed; a separate `asyncio` task in the lifespan is the alternative.
- **Free-tier caveat (call out explicitly):** Render's free web service **sleeps when idle**, which
  freezes the worker loop, so the hourly sweep does **not** run while asleep. A raw row created just
  before sleep could sit **past 7 days** until the service wakes. On wake the loop resumes and the
  first sweep tick immediately deletes everything older than the cutoff, so the over-retention window
  equals the idle-sleep duration and is purged the instant anyone touches the app. Mitigations if
  stricter timing is required: (i) also fire the guarded sweep at the top of each webhook POST
  (cheap), or (ii) go always-on. Under fold-and-forget this caveat is largely moot — nothing raw is
  written in the first place, so there is nothing to over-retain while asleep.

---

## 6. Migration + rollout

**Mechanism.** No Alembic in this project — `init_db()` runs `Base.metadata.create_all`
([app/db.py:31-34](app/db.py)). So "migration" = (1) add the two models, which `create_all` makes
idempotently, plus (2) a one-time idempotent **backfill routine** that folds existing `SegmentEffort`
rows into the new tables.

**Backfill routine (idempotent).** For each `(athlete_id, activity_id)` group in existing
`SegmentEffort`: `crossings = count`, `activity_hash = HMAC(...)`, upsert `CountedActivity`, and sum
into `WindowTally`. Keyed on `(athlete_id, activity_hash)`, so re-running skips rows already folded.
Safe to re-run against a fresh/empty DB (free-tier reset): no efforts → no-op, and the tally then
builds from live webhooks/backfill going forward.

**Order of operations (no total lost):**
1. Add models; `init_db` creates `window_tally` + `counted_activity`.
2. Run backfill routine → populate aggregates from existing efforts.
3. **Verify:** assert `WindowTally.total_count == _window_total(old)` per athlete before trusting the
   cutover. Gate the next steps on this passing.
4. Repoint **reads**: `_window_total` → `WindowTally.total_count`; `leaderboard_data` → `WindowTally`;
   `/history` → `CountedActivity` (or live, per §4).
5. Repoint **writes**: `process_activity` folds via the `activity_hash` guard instead of per-effort
   insert.
6. Enable the sweep (§5), then stop persisting raw efforts (fold-and-forget). Raw Strava Data is now
   gone from durable storage.

**Free-tier reality.** Postgres is ephemeral (wiped on redeploy/≈30 days). The backfill must be
idempotent and safe on a fresh DB (it is — see above). Recovery after a reset is unchanged:
reconnect + backfill rebuilds `WindowTally` from Strava.

---

## Invariants preserved

- **Per-athlete window total correct** — `WindowTally.total_count`, verified `==` old at cutover (step 3).
- **Append-only write-back untouched** — `_compose_description` / sentinels unchanged.
- **Idempotency strengthened** — per-activity `activity_hash` guard now covers the tally-only path
  too, closing the current [app/processing.py:154-159](app/processing.py) backfill double-count gap.
- **Leaderboard still computable** — `WindowTally` carries `total_count` + `best_ride_count`, and
  `leaderboard_data` repoints to it (joining the retained `display_name`) while staying behind
  `LEADERBOARD_ENABLED`. Item #2 landed as **flag-off-but-restorable (Option C)**, so this path must
  survive: re-enabling the flag after a favorable Strava ruling must light the board back up without
  reintroducing raw Strava Data. **Do not drop `best_ride_count`.**

---

## Recommended approach (one line)

Durable derived `WindowTally` + one-way `CountedActivity` idempotency ledger; **fold-and-forget** raw
efforts (zero raw Strava Data at rest); `/history` reduced to derived aggregates (option b); 7-day
sweep retained as a safety net for pre-migration rows.

## Open questions for sign-off
1. ~~**`/history`**~~ — **resolved: option (a), live re-fetch.** `/history` re-fetches the athlete's
   own window activities from Strava on page load (fetch-use-don't-retain), preserving dates +
   "View on Strava" links, at ~1+N Strava calls per view. No raw Strava Data persisted for the page.
2. ~~**Edited-activity recount**~~ — **resolved: option 1, skip.** First count is authoritative; a
   re-fetched/edited activity is not re-counted. The `update` webhook re-fold path is deferred.
3. ~~**Raw retention**~~ — **resolved: fold-and-forget.** `process_activity` folds crossings straight
   into `WindowTally` + `CountedActivity` and never persists raw efforts. Zero raw Strava Data at
   rest. The 7-day sweep is kept only as a one-time broom for pre-migration `SegmentEffort` rows.
4. ~~**Leaderboard coordination**~~ — **resolved.** Item #2 landed as flag-off-but-restorable
   (Option C), so `best_ride_count` and the computable-leaderboard path stay. No action needed.
