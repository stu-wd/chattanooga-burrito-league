# Strava API Policy compliance — work queue

Three compliance items surfaced by a TOS review on 2026-11-16 against the **Strava API Policy**
(https://www.strava.com/legal/api_policy, effective 2026-06-01) and **API Agreement**
(https://www.strava.com/legal/api). The Policy is the operational ruleset; it is materially
stricter than the pre-2026 agreement and — critically — contains **no consent-based exception**
for showing one athlete's data to another.

None of these block the app today: Strava review is only required to exceed **10 connected
athletes**, and the app is at the self-upgraded 10-athlete Standard tier (no review pending).
These are *standing obligations* and *must be resolved before submitting for review past 10*.

Each file below is a self-contained prompt. Paste it into a fresh Claude Code session in this repo.
Do them in this order — #1 is a clean win, #2 is a product decision, #3 reshapes the data model.

| # | File | Clause | Type | Risk |
|---|------|--------|------|------|
| 1 | [`01-activity-deletion-webhook.md`](01-activity-deletion-webhook.md) | §6.3 (reflect deletions ≤48h) | Code fix | Clear gap, cheap |
| 2 | [`02-leaderboard-display.md`](02-leaderboard-display.md) | §2.3 / §6.1 (own-data-only) | Product decision + code | High |
| 3 | [`03-seven-day-cache.md`](03-seven-day-cache.md) | §6.2 (7-day cache cap) | Data-model redesign | High, architectural |

## Verbatim clauses (transcribed 2026-11-16)

**§2.3** — "Strava Data provided by a specific Strava user may be displayed or disclosed in your
Developer Application only to that user. You may not display or disclose Strava Data related to
other users, even if such data is publicly viewable on the Strava Platform."

**§6.1** — "Unless your Developer Application has an athlete capacity of 9,999 or less, you may
display or disclose to an end user only the specific Strava Data related to that end user. You may
not display or disclose Strava Data related to other users, even if such data is publicly viewable
on the Strava Platform." *(The two sentences contradict; the ≤9,999 "exemption" is unresolved —
do not rely on it without a written ruling from Strava developer support.)*

**§6.2** — "You may not retain Strava Data in your cache for longer than seven (7) days."

**§6.3** — "Deletions must be reflected in your Developer Application expeditiously but in all
cases within forty-eight (48) hours."

**§5.3** — prohibits use of Strava materials "in connection with the development, training,
evaluation, or operation of any AI Application." *(App does none — compliant.)*
