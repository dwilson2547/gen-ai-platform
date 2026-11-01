"""Settings, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./genai.db"
    public_url: str = "http://localhost:8080"
    """Where the app is reached. The OIDC redirect URI derives from it, and an https URL turns on Secure /
    __Host- cookies."""
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    user_group: str | None = None
    """authentik group allowed to sign in (design §2). Unset: anyone authentik lets through."""
    session_ttl_days: int = 30
    s3_endpoint: str | None = None
    """AIStor/S3 API for images, e.g. http://192.168.0.10:30320. gpu-scheduler uploads to presigned PUTs on it, so it
    must be reachable from the GPU node too."""
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    images_dir: str | None = None
    """Local alternative to S3 for development: images come back from gpu-scheduler inline and are stored here."""
    scheduler_url: str | None = None
    """gpu-scheduler's API, e.g. http://gpu-scheduler-api.gpu-scheduler.svc."""
    scheduler_token: str | None = None
    poll_seconds: float = 1.5
    background_poll: bool = True
    """Run the job poller thread (generation.Poller). Tests switch it off and poll on demand."""
    max_pending_per_user: int = 16
    static_dir: str | None = None
    dev_sd_server: str | None = None
    """Development only: a local sd-server URL that stands in for gpu-scheduler (scheduler.LocalSdServer). Ignored
    when public_url is https."""
    dev_login: bool = False
    """Local-only sign-in without authentik, for development and UI tests. Refused when public_url is https."""

    @property
    def secure(self) -> bool:
        return self.public_url.startswith("https://")

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def dev_login_enabled(self) -> bool:
        return self.dev_login and not self.secure

    @classmethod
    def from_env(cls, env=os.environ) -> "Settings":
        return cls(
            database_url=env.get("DATABASE_URL") or cls.database_url,
            public_url=(env.get("PUBLIC_URL") or cls.public_url).rstrip("/"),
            # authentik's issuer ends in "/" and the ID token's iss must match it exactly, so it is kept as configured.
            oidc_issuer=(env.get("OIDC_ISSUER") or "").strip() or None,
            oidc_client_id=env.get("OIDC_CLIENT_ID") or None,
            oidc_client_secret=env.get("OIDC_CLIENT_SECRET") or None,
            user_group=(env.get("USER_GROUP") or "").strip() or None,
            session_ttl_days=int(env.get("SESSION_TTL_DAYS") or cls.session_ttl_days),
            s3_endpoint=env.get("S3_ENDPOINT") or None,
            s3_bucket=env.get("S3_BUCKET") or None,
            s3_access_key=env.get("S3_ACCESS_KEY") or None,
            s3_secret_key=env.get("S3_SECRET_KEY") or None,
            s3_region=env.get("S3_REGION") or cls.s3_region,
            images_dir=env.get("IMAGES_DIR") or None,
            scheduler_url=(env.get("SCHEDULER_URL") or "").rstrip("/") or None,
            scheduler_token=env.get("SCHEDULER_TOKEN") or None,
            poll_seconds=float(env.get("POLL_SECONDS") or cls.poll_seconds),
            background_poll=(env.get("BACKGROUND_POLL") or "1") != "0",
            max_pending_per_user=int(env.get("MAX_PENDING_PER_USER") or cls.max_pending_per_user),
            static_dir=env.get("STATIC_DIR") or None,
            dev_sd_server=env.get("DEV_SD_SERVER") or None,
            dev_login=(env.get("DEV_LOGIN") or "") == "1",
        )
