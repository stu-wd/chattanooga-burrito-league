# Prompt — §2.3 / §6.1: the public leaderboard shows other athletes' data

Paste everything below into a fresh Claude Code session in the `chattanooga-burrito-league` repo.

---

You are working on the Chattanooga Burrito League FastAPI app. This is a **Strava API Policy
compliance decision + implementation**. Read `docs/compliance/README.md` first for the governing
clauses. **This task changes a user-facing feature — do not start editing until you have completed
the "Decision gate" below and I have chosen a direction.**

## The rule

Strava API Policy **§2.3** (flat, no exception): "Strava Data provided by a specific Strava user
may be displayed or disclosed in your Developer Application only to that user. You may not display
or disclose Strava Data related to other users, even if such data is publicly viewable on the
Strava Platform."

**§6.1** adds an ambiguous wrinkle: "*Unless* your Developer Application has an athlete capacity of
9,999 or less, you may display or disclose to an end user only the specific Strava Data related to
that end user. You may not display or disclose Strava Data related to other users, even if such
data is publicly viewable on the Strava Platform." The two sentences contradict each other — the
first reads as an exemption for apps ≤9,999 athletes, the second flatly bans cross-athlete display.
Other developers auditing the identical clause call it "unclear." **Treat the ≤9,999 exemption as
unusable until Strava developer support confirms it in writing.**

Note what is **not** here: there is **no consent-based exception** in the 2026 Policy. The app's
current design assumed one (affirmative opt-in + a chosen display name = "explicit consent", the
"riverwalkmiles model"). That was a valid read of the *pre-2026* agreement; it is **not supported
by the June 2026 Policy**. §2.1 consent governs *accessing a user's own data*, not redistributing
it to other users.

## The gap (verified)

The public `/leaderboard` page displays, to any viewer, **other athletes' Strava-derived data**:
their `display_name` alongside a burrito-crossing `total` (a count derived from Strava
segment-effort data), ranked, plus a "most in one ride" highlight naming another athlete.

- `app/leaderboard.py` → `leaderboard_data()` builds ranked standings + biggest-ride highlight by
  joining `Athlete` to `SegmentEffort` and counting, filtered to `community_consent` athletes.
- `app/web.py` serves the `/leaderboard` route (public, no auth) with `app/templates/leaderboard.html`.
- `app/templates/base.html` links to it from the hamburger nav.
- The consent machinery feeding it: `Athlete.community_consent` + `Athlete.display_name`
  (`app/models.py:21-24`), applied via `_apply_consent` and the settings page
  (`app/api.py`, `app/templates/settings.html`).

Under §2.3 this is the exact prohibited behavior: showing athlete A's data to athlete B (and to the
anonymous public). **High compliance risk.** The consent gate does not cure it, because the Policy
has no consent exception for cross-athlete display.

What is **fine and must not change**: the write-back message in `app/processing.py`
(`_build_body`) contains only the athlete's **own** this-ride count and their **own**
competition-window total, written to their **own** activity. That is §2.3-compliant. Leave it alone.

## Decision gate — present these options to me and recommend one, then STOP

Lay out the trade-offs and wait for my choice before implementing:

- **Option A — Remove the public leaderboard entirely.** Delete the `/leaderboard` route, template,
  nav link, and `app/leaderboard.py`; retire the `community_consent` + `display_name` opt-in UI in
  settings (keep the columns in the DB or drop them via migration — propose which). Most defensible;
  zero cross-athlete disclosure. Cost: loses the app's social hook.

- **Option B — Personal-only view (own data).** Replace the public leaderboard with an
  authenticated page that shows the logged-in athlete **only their own** numbers: their window
  total and their own best single-ride count. No other names, no ranks relative to others (a rank
  like "3rd of 12" still discloses the existence/ordering of others' data, so avoid it). This is
  essentially a summary card on top of `/history`. Fully §2.3-compliant. Cost: no competitive
  element.

- **Option C — Keep the code, hide the feature, pursue a ruling.** Feature-flag the leaderboard
  OFF (route returns 404, nav link hidden) so no cross-athlete data is served today, preserve all
  the code and consent plumbing, and I email Strava developer support for a written ruling on the
  §6.1 ≤9,999 exemption. Re-enable only if they bless it. Lowest code churn, keeps optionality.
  **Recommend this as the default** unless I say otherwise — it removes the live risk immediately
  while keeping the door open, and pairs with a support email I can send.

Whatever I pick, the live risk (serving other athletes' data to the public) must be gone at the end
of this task.

## Implementation notes (apply after I choose)

- **Option C (likely default):** add a config flag (e.g. `LEADERBOARD_ENABLED`, default `false`) in
  `app/config.py`; the `/leaderboard` route returns 404 when off; `base.html` hides the nav link
  when off (pass a flag into the template context). Keep `leaderboard.py` intact. Also draft, as a
  markdown file in `docs/compliance/`, the Strava support email requesting a §6.1 clarification —
  state the app's athlete capacity (≤10 today, Standard tier), describe the consent-gated
  name+count leaderboard precisely, and ask whether §6.1's ≤9,999 clause permits it or whether §2.3
  controls.
- **Option A:** remove route/template/nav/`leaderboard.py`; for the DB columns, prefer a migration
  that drops `community_consent`/`display_name` only if nothing else reads them (grep first —
  `_apply_consent`, settings page, OAuth). If unsure, leave the columns and just remove the UI.
- **Option B:** add an authenticated summary endpoint/section reusing the athlete's own
  `_window_total` (`app/processing.py`) and an own-best-ride query; reuse the existing session auth
  (`app/web.py` `_session_athlete`). No joins across athletes.

## Acceptance criteria

- After this task, no unauthenticated or cross-athlete Strava-derived data is served anywhere (grep
  for every `select(... Athlete ... SegmentEffort ...)` join and confirm each remaining one is
  scoped to the requesting athlete, except any left dormant behind an OFF flag).
- The own-data write-back (`_build_body`) is unchanged.
- Nav, routes, and templates are internally consistent (no dead link to a removed/hidden page).
- If Option C: with the flag off, `/leaderboard` is 404 and the nav link is absent; with it on,
  behavior is unchanged (so the ruling can flip it back).

## Report back

House style: **Was / Think / Fix / Blast radius**, `file:line` citations, no echoing file contents.
Also update the project memory note `strava-tos-constraints.md` with the final decision.
