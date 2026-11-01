"""Who sees what (design §2): a private project is its owner's alone; a public one is visible to every signed-in user.
Only the owner adds to a project. No admin override."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Artifact, Project, User


def can_view(p: Project, user: User) -> bool:
    return p.visibility == "public" or p.owner_id == user.id


def project_for_view(s: Session, project_id: str, user: User) -> Project:
    p = s.get(Project, project_id)
    if p is None or not can_view(p, user):
        raise HTTPException(404, "no such project")        # a private project's existence isn't disclosed
    return p


def project_for_write(s: Session, project_id: str, user: User) -> Project:
    p = project_for_view(s, project_id, user)
    if p.owner_id != user.id:
        raise HTTPException(403, "only the project's owner can add to it")
    return p


def artifact_for_view(s: Session, artifact_id: str, user: User) -> Artifact:
    a = s.get(Artifact, artifact_id)
    if a is None or not can_view(a.project, user):
        raise HTTPException(404, "no such artifact")
    return a


def visible_projects_clause(user: User):
    return or_(Project.visibility == "public", Project.owner_id == user.id)


def visible_project_ids(user: User):
    return select(Project.id).where(visible_projects_clause(user))
