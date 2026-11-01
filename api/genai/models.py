"""Database schema (design §3). Accounts and sessions are copied from webdoc."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


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
