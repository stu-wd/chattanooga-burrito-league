#!/usr/bin/env bash
# Manage the Strava webhook push subscription from .env values.
# Usage: scripts/subscribe.sh [create|list|delete]
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a

API="https://www.strava.com/api/v3/push_subscriptions"
cmd="${1:-list}"

case "$cmd" in
  create)
    curl -s -X POST "$API" \
      -F client_id="$STRAVA_CLIENT_ID" \
      -F client_secret="$STRAVA_CLIENT_SECRET" \
      -F callback_url="${BASE_URL%/}/strava/webhook" \
      -F verify_token="$STRAVA_VERIFY_TOKEN" | python3 -m json.tool
    ;;
  list)
    curl -s -G "$API" \
      -d client_id="$STRAVA_CLIENT_ID" \
      -d client_secret="$STRAVA_CLIENT_SECRET" | python3 -m json.tool
    ;;
  delete)
    id="${2:?usage: subscribe.sh delete <subscription_id>}"
    # Strava reads credentials from the query string for DELETE (not the body).
    curl -s -X DELETE \
      "$API/$id?client_id=$STRAVA_CLIENT_ID&client_secret=$STRAVA_CLIENT_SECRET" \
      -w "HTTP %{http_code}\n"
    ;;
  *) echo "usage: subscribe.sh [create|list|delete <id>]" >&2; exit 1 ;;
esac
