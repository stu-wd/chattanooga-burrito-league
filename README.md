# Chattanooga Burrito League 🌯

A Strava companion that watches a target **segment** and, each time you cross it,
tallies your "burritos" and appends a running total to your activity description.
Opt in to a public, consent-based leaderboard with a display name of your choice.

Live: https://chattanooga-burrito-league.onrender.com

## How it works
1. You connect with Strava (OAuth). Tokens are stored encrypted.
2. Strava sends a webhook when you finish an activity.
3. A background worker fetches the activity, counts crossings of the target
   segment (`segment_efforts` from Strava), records each as a row, and — per your
   write preference — appends/prepends a note (or stays off).
4. Totals are always *computed by counting rows*, never a stored counter.

```
Strava webhook ─▶ enqueue ─▶ DB job queue ─▶ worker (rate-limited)
                                               ├─ fetch activity + segment efforts
                                               ├─ record SegmentEffort rows
                                               └─ write description (append/prepend/off)
```

## Pages
- `/` — landing + Connect with Strava
- `/settings` — write mode (append/prepend/off) with live preview, leaderboard
  opt-in + display name, backfill by year, reset, disconnect
- `/leaderboard` — public standings (opted-in athletes only), aggregate totals
- `/history` — your own rolls per activity
- Hamburger (☰) nav links History / Leaderboard / Settings when signed in.

## TOS & privacy
Strava's API Agreement: a user's data may only be shown to that user, **except
with explicit consent**. So:
- Write-backs contain only the athlete's own data, on their own activity.
- The leaderboard shows only athletes who affirmatively opted in **and** chose a
  display name (that choice is the consent). Opting out / disconnecting removes
  them and deletes their data.
- `/debug/*` and admin endpoints expose cross-athlete data and are gated behind
  `ADMIN_TOKEN` (404 when unset). Never expose them publicly.

## Local development
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env      # fill in values (see table below)
.venv/bin/uvicorn app.main:app --reload --port 8000
ngrok http 8000           # public URL for OAuth + webhooks in dev
```
Put the ngrok URL in `.env` `BASE_URL` and as the Strava app's Authorization
Callback Domain, then create the webhook subscription:
```bash
scripts/subscribe.sh create   # also: list | delete <id>
```

## Deploy (Render)
`render.yaml` is a Blueprint: one always-on web service (app + in-process worker)
+ managed Postgres. Create via Render → New → Blueprint, pointing at this repo.
Set the `sync:false` env vars in the dashboard. Tables auto-create on boot
(`init_db`). After the first deploy, set `BASE_URL` to the Render URL and the
Strava callback domain to the Render host, then (re)create the subscription
against `https://<host>/strava/webhook`.

> Deploys are **manual** (not on-push): trigger "Deploy latest commit" in Render.

## Operations

### Free-tier caveats
- **Postgres is ephemeral.** Render's free database is deleted ~30 days after
  creation, and Blueprint re-syncs / some deploys can replace it with an empty
  one — so the data resets periodically.
- **The web service sleeps** after ~15 min idle; a webhook or visit wakes it
  (~30s cold start), and the background worker only runs while awake.
- Both are solved by paid plans (paid Postgres, or a paid web instance with a
  persistent disk + SQLite) when this goes real.

### Recovering from a DB reset
The database is essentially a **cache of Strava** — the backfill reconstructs it.
After a reset, each athlete:
1. Clears site cookies (or uses a new tab) and **Connects** again.
2. Runs **Backfill <year>** on Settings to rebuild their rolls.
3. Re-enters the only data that lives only here: write mode + leaderboard opt-in
   and display name.

The `/` ↔ `/settings` redirect loop that a stale cookie used to cause is handled
(`_session_athlete` clears the stale session), so a reset no longer bricks the site.

### Admin / debug endpoints
`/debug/*` and `/athlete/{id}/*` expose cross-athlete data and are gated behind
`ADMIN_TOKEN` (404 when unset). To use them in prod, set `ADMIN_TOKEN` in Render
and send it as the `X-Admin-Token` header, e.g.:
```bash
curl https://<host>/debug/state -H "X-Admin-Token: $ADMIN_TOKEN"
```

## Environment variables
| Var | Purpose |
|---|---|
| `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` | Strava API app credentials |
| `STRAVA_VERIFY_TOKEN` | Echoed during webhook subscription validation |
| `ENCRYPTION_KEY` | Fernet key for encrypting stored tokens |
| `SESSION_SECRET` | Signs the session cookie |
| `ADMIN_TOKEN` | Gates `/debug/*` + admin endpoints (unset = locked) |
| `DATABASE_URL` | SQLite (dev) / Postgres (prod) |
| `BASE_URL` | Public base URL (builds the OAuth redirect) |
| `TARGET_SEGMENT_ID` | The burrito segment |
| `COMPETITION_START` / `COMPETITION_END` | Active edition window (ISO, UTC) |
| `STRAVA_SCOPES` | `read,activity:read,activity:write` |

## Layout
- `app/oauth.py` · `app/api.py` · `app/web.py` — OAuth, session JSON API, pages
- `app/webhook.py` · `app/worker.py` — webhook intake, DB queue + worker
- `app/processing.py` · `app/backfill.py` — detect/tally/write, backfill
- `app/leaderboard.py` · `app/strava.py` · `app/ratelimit.py` — standings, Strava client, limiter
- `app/models.py` · `app/db.py` · `app/crypto.py` · `app/config.py` — data + infra
- `app/templates/` — Jinja pages (thin callers of the JSON API)

See `ROADMAP.md` for what's next.
