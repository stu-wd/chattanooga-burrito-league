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

## Shipped (as of Nov 2026) — deployed & multi-user ✅
- **Live on Render** (web service + Postgres), OAuth + webhook subscription wired.
  Verified end-to-end with two real athletes.
- **Write preferences** (append / prepend / off) with a dual-sentinel block that's
  append-only and idempotent across mode switches.
- **Backfill** by edition year (queued, rate-limited, oldest-first cumulative
  totals), plus a "reset my rolls" testing aid.
- **Onboarding UX**: landing + Connect button, settings page (write mode w/ live
  preview, backfill, disconnect), hamburger nav, history page.
- **Consent-based leaderboard**: affirmative opt-in + chosen display name
  (TOS-compliant), standings + aggregate + "biggest ride" highlight.
- **Security**: `/debug/*` + admin endpoints gated behind `ADMIN_TOKEN`.

---

## Roadmap (next)

### Phase 4 — Path to Strava approval (current gate)
- Onboard toward **10 connected athletes**, then submit the app for review
  (removes the "unverified app" warning, raises rate limits to 400/4000).
- Review submission must disclose: the consent-based leaderboard, deauth →
  hard-delete, scope justification, activity-privacy handling.
- Verify webhook `update`/privacy-change handling before submitting.

### Phase 5 — Leaderboard extras (consent-gated)
Standings + aggregate + "biggest ride" are shipped. Remaining, all sourced only
from consenting athletes:
- **Activity feed:** "another burrito-er just added N to the Chattanooga total."
- **Records tier:** highest 24h total, fastest time — needs storing effort
  `elapsed_time` (not captured yet); built so it can be pulled if Strava objects.

### Cross-cutting — hardening (as needed)
- Rate limiter is in-process (correct for one worker). Move to Redis/DB only if
  running >1 worker/instance (`app/ratelimit.py` is the seam).
- Webhook `update`/`delete` events, edited/late activities, duplicate rides,
  privacy changes.
- Real 429 backoff (parse Retry-After) as a limiter backstop.
- Render free tier → paid for always-on (no idle sleep) + durable Postgres.
