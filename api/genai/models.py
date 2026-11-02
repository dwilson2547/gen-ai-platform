"""Database schema (design §3). Accounts and sessions are copied from webdoc."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


Json = JSON().with_variant(JSONB(), "postgresql")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    identities: Mapped[list["UserIdentity"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    sessions: Mapped[list["SessionToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True)


class UserIdentity(Base):
    """An authentik identity (issuer + sub) linked to a local user."""
    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="identities")


class SessionToken(Base):
    """A browser session. Only the SHA-256 of the token is stored, so signing out is a row delete."""
    __tablename__ = "session_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class Project(Base):
    """A way to organise artifacts (design §2): private = its owner only, public = every signed-in user. Only the
    owner adds to it."""
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()


class Artifact(Base):
    """One generated image and how it was made (design §3)."""
    __tablename__ = "artifacts"
    __table_args__ = (Index("artifacts_project_created", "project_id", "created_at"),
                      Index("artifacts_pending", "status"))

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    kind: Mapped[str] = mapped_column(String(20), default="image")

    prompt: Mapped[str] = mapped_column(Text)
    """What the user typed."""
    full_prompt: Mapped[str] = mapped_column(Text)
    """What the model got, after the preset's template."""
    preset: Mapped[str] = mapped_column(String(40), default="none")
    model: Mapped[str] = mapped_column(String(60))
    seed: Mapped[int | None] = mapped_column(Integer)
    """Asked for, or (once done) the one the model used."""
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    steps: Mapped[int | None] = mapped_column(Integer)
    cfg_scale: Mapped[float | None] = mapped_column(Float)

    parent_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)
    op: Mapped[str] = mapped_column(String(20), default="generate")

    status: Mapped[str] = mapped_column(String(10), default="queued")
    """queued | running | done | failed."""
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(String(40))
    ran_model: Mapped[str | None] = mapped_column(String(60))
    worker: Mapped[str | None] = mapped_column(String(60))
    seconds: Mapped[float | None] = mapped_column(Float)
    """GPU time for the image, as the scheduler reported it."""
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    image_key: Mapped[str | None] = mapped_column(String(200))
    bytes: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(200))

    project: Mapped[Project] = relationship()
    creator: Mapped[User] = relationship()


# --- agents: personal access tokens and OAuth for MCP clients (copied from webdoc) -----------------------------------

class AccessToken(Base):
    """A personal access token for MCP clients. Stored hashed like sessions; `prefix` is the visible start of the token
    so a user can tell them apart."""
    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()


class OAuthClient(Base):
    """An MCP client that registered itself (RFC 7591). `info` is the SDK's OAuthClientInformationFull as JSON."""
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    info: Mapped[dict] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OAuthRequest(Base):
    """An /authorize request waiting on the consent page. `user_id` and `nonce` are filled when the page is shown, so
    only that signed-in user's form submission can approve it."""
    __tablename__ = "oauth_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id", ondelete="CASCADE"))
    params: Mapped[dict] = mapped_column(Json)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthCode(Base):
    __tablename__ = "oauth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    params: Mapped[dict] = mapped_column(Json)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OAuthGrant(Base):
    """One connected app for one user: the current access and refresh token, rotated in place on refresh. Deleting the
    row disconnects the app."""
    __tablename__ = "oauth_grants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(ForeignKey("oauth_clients.client_id", ondelete="CASCADE"), index=True)
    client_name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[str] = mapped_column(String(200), default="")
    resource: Mapped[str | None] = mapped_column(String(300), nullable=True)
    access_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    refresh_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship()


class DownloadLink(Base):
    """A short-lived link to one image's full PNG that needs no other auth (links.py), so an agent can save the file
    with curl even when its own token lives inside its MCP client. Only the SHA-256 of the token is stored."""
    __tablename__ = "download_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Variant(Base):
    """A file derived from an artifact's image (design §5): `cutout` (background keyed out, made automatically for
    sprite presets) or `pixel` (shrunk to a pixel grid, on demand). `params` records how, so it can be redone."""
    __tablename__ = "variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict] = mapped_column(Json, default=dict)
    image_key: Mapped[str] = mapped_column(String(200))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    artifact: Mapped[Artifact] = relationship()


# --- character sheets (design §11) ------------------------------------------------------------------------------------

class Sheet(Base):
    """A named character: a base image and variations of it, in one of its creator's projects (whose visibility it
    shares). The base is picked from candidates of `description` (or given); variations are edits of the base."""
    __tablename__ = "sheets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(60))                 # the base's; variations need a model that edits
    background: Mapped[str] = mapped_column(String(10), default="magenta")
    base_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"),
                                                         nullable=True)
    pixel_size: Mapped[int] = mapped_column(Integer, default=64)
    pixel_colors: Mapped[int] = mapped_column(Integer, default=24)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship()
    creator: Mapped[User] = relationship()
    base: Mapped[Artifact | None] = relationship()


class SheetSlot(Base):
    """One row of a sheet: the base (kind "base", from the description) or a variation (an edit instruction)."""
    __tablename__ = "sheet_slots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))
    label: Mapped[str] = mapped_column(String(100))
    instruction: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SheetCandidate(Base):
    """One of a slot's images. `held`: not submitted yet because the user's queue was full; the poller submits it when
    there is room. An artifact deleted from the project leaves the candidate empty (artifact_id null, not held)."""
    __tablename__ = "sheet_candidates"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    slot_id: Mapped[str] = mapped_column(ForeignKey("sheet_slots.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True)
    held: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    kept: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    slot: Mapped[SheetSlot] = relationship()
    artifact: Mapped[Artifact | None] = relationship()


class SheetLink(Base):
    """A short-lived link to a sheet's latest export zip (as DownloadLink is for one image)."""
    __tablename__ = "sheet_links"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    sheet_id: Mapped[str] = mapped_column(ForeignKey("sheets.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
