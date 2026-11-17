# Chattanooga Burrito League — Roadmap

## Context
Phase 1 (OAuth, webhook, encrypted tokens, DB queue, detect→tally→write pipeline)
and Phase 2 (rate limiter + queued backfill) are done and tested against live
Strava. Write-backs are append-only and TOS-safe (own data, own activity only).

Goals: (a) let each athlete choose how their burrito tally is written to their
activity, and (b) eventually stand up a public site with aggregate stats, an
anonymous feed, and fun records — without violating Strava's TOS (no names, no
leaderboard of identifiable athletes).

**TOS design principle that governs everything public:** Strava's API Agreement
says a user's data "can only be displayed or disclosed… to that user," and data
about other users "may not be displayed or disclosed" — except with **explicit
consent**. So: anything shown to an athlete about *themselves* is free; anything
public/aggregate requires explicit per-athlete consent + anonymization, and the
records tier (fastest, biggest single effort) is the highest-risk slice and must
be disclosed in the Strava review submission and be easy to pull.

---

## Current build: Write-preferences (prepend / append / off)
Each athlete chooses how their burrito block appears in their Strava activity:
- `append` (default) — block below the athlete's own notes.
- `prepend` — block above the athlete's own notes.
- `off` — never write to the description, but still **tally** (so the athlete
  still counts toward aggregates if they later consent).

Implementation: our block is bracketed by a START and END sentinel so it can be
stripped from anywhere and re-inserted at top/bottom/not-at-all, keeping writes
idempotent across mode switches. `Athlete.write_mode` stores the choice; set via
`PUT /athlete/{id}/write-mode?mode=append|prepend|off`.

---

## Roadmap (later phases — gated on external milestones)

### Phase 3 — Onboarding UX + explicit consent
- Minimal front page (replaces URL-poking): brand-compliant "Connect with Strava"
  button, a write-mode selector, and a community-consent checkbox captured as a
  deliberate action (replaces the dormant `?consent=true` query param; record
  consent + timestamp).
- Post-connect settings page to change write-mode / consent / disconnect.

### Phase 4 — Path to Strava approval
- Onboard friends to reach **10 connected athletes** (Strava's gate to leave
  single-player mode).
- Submit for review. Disclose: consented + anonymized aggregate stats; deauth →
  hard-delete (implemented in `webhook._purge_athlete`); scope justification
  (`activity:write` for descriptions); respect for activity privacy.
- Verify webhook `update`/privacy-change handling before submitting.
- Unlocks higher rate limits (400/4000) and multi-user data.

### Phase 5 — Public site (post-approval only)
No real multi-athlete data exists until Phase 4 passes. Build in risk order, all
sourced **only from consented athletes**, all anonymized:
- **Aggregate-first (low risk):** Chattanooga daily / monthly burrito total.
- **Anonymous feed (medium):** "another burrito-er just added N to the total."
- **Records tier (higher risk):** biggest single-effort, highest 24h total,
  fastest time — unnamed, consent-gated, built so it can be pulled if Strava
  objects.

### Cross-cutting — production hardening (as needed, parallel)
- Deploy off ngrok → real host + reserved domain + Postgres (`DATABASE_URL`
  already supports it); move secrets out of `.env`.
- Rate limiter is in-process (correct for one worker). Move to Redis/DB only if
  running >1 worker/instance (`app/ratelimit.py` is the seam).
- Webhook `update`/`delete` events, edited/late activities, duplicate rides,
  privacy changes.
- Real 429 backoff (parse Retry-After) as a limiter backstop.

---

## Sequencing rationale
Single-player mode means there's no other-athlete data to aggregate until Strava
review passes — so the public site (Phase 5) cannot meaningfully exist before
Phase 4. Natural order: **write-prefs → onboarding+consent → 10 athletes →
review → public site.** Building the feed/records UI earlier risks investing in the
exact surface most likely to trigger a review rejection.
