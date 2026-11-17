# Chattanooga Burrito League

Strava webhook service that detects completion of a target Strava **segment**
and appends a playful note to the activity's description.

Phase 1 scope: OAuth onboarding, webhook validation + event intake, encrypted
token storage, a DB-backed job queue drained by a single worker, and the
detect → tally → write-back pipeline. Single-player until Strava approves the app.

## Layout
- `app/config.py` — env-driven settings
- `app/crypto.py` — Fernet encryption for stored tokens
- `app/models.py` — `athletes`, `processed_activities`, `segment_efforts`, `jobs`
- `app/strava.py` — OAuth token exchange/refresh + activity read/update
- `app/oauth.py` — `/auth/login`, `/auth/callback`
- `app/webhook.py` — `GET/POST /strava/webhook` (+ deauth purge)
- `app/worker.py` — DB queue `enqueue()` + async worker loop
- `app/processing.py` — segment detection, counts, description builder
- `app/main.py` — app wiring + `/debug/*` helpers

## One-time setup

1. **Register a Strava API app** at <https://www.strava.com/settings/api>.
   - Authorization Callback Domain: your ngrok host **without scheme/path**,
     e.g. `abc123.ngrok-free.app` (update when the ngrok URL changes).
   - Copy the **Client ID** and **Client Secret**.
2. **Fill `.env`** (already has a generated `ENCRYPTION_KEY` and a `STRAVA_VERIFY_TOKEN`):
   - `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`
   - `TARGET_SEGMENT_ID` — the burrito segment's numeric ID (from its Strava URL).
3. **Install deps:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`

## Run (dev)

```bash
# terminal 1 — the app
.venv/bin/uvicorn app.main:app --reload --port 8000

# terminal 2 — public tunnel; copy the https URL into .env BASE_URL and the
# Strava app's Authorization Callback Domain
ngrok http 8000
```

## Onboard your account

Open `<BASE_URL>/auth/login` in a browser (add `?consent=true` to opt into the
community counter). Approve on Strava → redirected to `/auth/callback` → tokens
stored. Verify with `GET /debug/state`.

## Create the webhook subscription (once per app)

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=$STRAVA_CLIENT_ID \
  -F client_secret=$STRAVA_CLIENT_SECRET \
  -F callback_url=$BASE_URL/strava/webhook \
  -F verify_token=$STRAVA_VERIFY_TOKEN
```

Strava immediately calls `GET /strava/webhook` to validate; the service echoes
the challenge. List/delete subscriptions via the same endpoint with GET/DELETE.

## Manual testing without a real ride

- **Fire a synthetic create event** (same path a real webhook takes → enqueues a job):
  ```bash
  curl -X POST localhost:8000/strava/webhook -H 'content-type: application/json' \
    -d '{"object_type":"activity","aspect_type":"create","object_id":<ACTIVITY_ID>,"owner_id":<YOUR_ATHLETE_ID>}'
  ```
- **Process one activity synchronously** (bypass the queue, see the outcome inline):
  ```bash
  curl -X POST "localhost:8000/debug/process/<ACTIVITY_ID>?athlete_id=<YOUR_ATHLETE_ID>"
  ```
  Point `TARGET_SEGMENT_ID` at a segment you've actually ridden on `<ACTIVITY_ID>`
  to see a real description write-back.
- **Inspect queue + onboarded athletes:** `GET /debug/state`

## Compliance guardrails (built in)
- Only the athlete's own data goes in their own activity. The community-total
  line is added **only for athletes who consented** (`?consent=true`).
- Deauthorization webhook hard-deletes that athlete's tokens and derived data.
- "Powered by Strava" appended to every written description.
