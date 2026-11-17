from datetime import datetime, timezone

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    strava_client_id: str
    strava_client_secret: str
    strava_verify_token: str
    encryption_key: str
    session_secret: str = "dev-insecure-change-me"
    # Keys the one-way HMAC that identifies a counted activity without retaining
    # the raw Strava activity id (§6.2 cache design). MUST be stable — rotating it
    # re-hashes every activity and would reintroduce double-counting, so a rotation
    # requires a full WindowTally rebuild. Independent of session_secret on purpose.
    cache_hash_key: str = "dev-insecure-cache-hash-key"
    # §6.2 ongoing guard: the worker periodically purges any legacy raw effort rows
    # older than this. Normally a no-op (fold-and-forget writes none); it is
    # defense-in-depth against a regression that reintroduces raw rows.
    cache_retention_days: int = 7
    # Gates /debug/* and admin endpoints (X-Admin-Token header). Empty = locked.
    admin_token: str = ""
    database_url: str = "sqlite:///./burrito.db"
    base_url: str = "http://localhost:8000"
    # Strava API Policy §2.3/§6.1: the public leaderboard displays other
    # athletes' Strava-derived data, which the 2026 Policy prohibits (no consent
    # exception). Kept OFF until Strava developer support rules on the §6.1
    # ≤9,999 exemption. When false: /leaderboard 404s and its nav link is hidden.
    leaderboard_enabled: bool = False
    target_segment_id: int = 0
    strava_scopes: str = "read,activity:read,activity:write"

    # Strava app-wide rate limits (single-player tier: 200/15min, 2000/day;
    # bumps to 400/4000 after review). The worker paces every Strava call to these.
    strava_rate_15min: int = 200
    strava_rate_daily: int = 2000

    # Competition window — only efforts whose Strava start time falls inside it
    # count toward a user's "total". Jan 2027 EST (UTC-5, no DST in January).
    # Override via COMPETITION_START / COMPETITION_END (ISO 8601) in .env.
    competition_start: datetime = datetime(2027, 1, 1, 5, 0, 0, tzinfo=timezone.utc)
    competition_end: datetime = datetime(2027, 2, 1, 4, 59, 59, tzinfo=timezone.utc)

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url.rstrip('/')}/auth/callback"

    @property
    def edition_key(self) -> str:
        """Durable label for the active competition window, derived from our own
        config (not Strava Data). Keys WindowTally / CountedActivity rows so past
        editions coexist. e.g. 2027-01."""
        return self.competition_start.strftime("%Y-%m")


settings = Settings()  # type: ignore[call-arg]
