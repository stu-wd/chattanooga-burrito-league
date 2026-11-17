from datetime import datetime, timezone

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    strava_client_id: str
    strava_client_secret: str
    strava_verify_token: str
    encryption_key: str
    session_secret: str = "dev-insecure-change-me"
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


settings = Settings()  # type: ignore[call-arg]
