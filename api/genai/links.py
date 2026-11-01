"""Short-lived download links: `/dl/<token>` serves one finished image's PNG to whoever holds the link, until it
expires. Handed to agents (mcp_server.py) so they can save full-size files; the token is random, stored hashed, and
expires after LINK_TTL. Not single-use: curl may retry."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import timedelta, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import Artifact, DownloadLink, utcnow

LINK_TTL = timedelta(minutes=10)
PREFIX = "gnd_"
PATH = "/dl/"


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create(s: Session, a: Artifact) -> tuple[str, str]:
    """(path, expires_at ISO) for a new link to `a`'s image."""
    s.execute(delete(DownloadLink).where(DownloadLink.expires_at < utcnow()))
    raw = PREFIX + secrets.token_urlsafe(24)
    expires = utcnow() + LINK_TTL
    s.add(DownloadLink(token_hash=_hash(raw), artifact_id=a.id, expires_at=expires))
    return PATH + raw, expires.isoformat()


def resolve(s: Session, raw: str) -> Artifact | None:
    if not raw.startswith(PREFIX) or len(raw) > 100:
        return None
    row = s.get(DownloadLink, _hash(raw))
    if row is None:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    if exp <= utcnow():
        return None
    a = s.get(Artifact, row.artifact_id)
    return a if a is not None and a.status == "done" and a.image_key else None


def filename(a: Artifact) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", a.prompt.lower())[:40].strip("-") or "image"
    return f"{slug}-{a.id[:8]}.png"
