"""Character sheets (design §11): a base character picked from candidates, then variations of it (poses, expressions,
weapon sets), each an edit of the base itself, kept and exported at the base's scale.

Every slot (the base, each variation) has CANDIDATES images. The user freezes the ones they like and re-rolls the rest;
at the end they keep any number. Candidates are ordinary artifacts, so lineage, cutouts and the queue work as for any
image. A candidate the queue can't take yet is `held` and submitted by the poller once there is room (fill_held), so
a big sheet waits instead of being refused.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import secrets
import zipfile
from datetime import timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import sprites
from .catalogue import BACKGROUNDS, DEFAULT_MODEL, MODELS, background_of
from .generation import GenerateError, create, make_cutout, pending_count
from .models import Artifact, Project, Sheet, SheetCandidate, SheetLink, SheetSlot, User, utcnow
from .scheduler import Scheduler
from .storage import Store

log = logging.getLogger("genai.sheets")

CANDIDATES = 4
MAX_SLOTS = 40
LINK_TTL = timedelta(minutes=10)
LINK_PREFIX = "gns_"
# Put in front of every variation's instruction: the 2026-11-02 test showed edits redrawing the subject bigger.
FRAMING = "same character, same pixel art style, same size and framing, {bg}: {line}"


def edit_model(sheet: Sheet) -> str:
    """Variations are edits: a model that can't edit (Qwen) made the base, the default FLUX model edits it."""
    m = MODELS.get(sheet.model)
    return sheet.model if m and m.edits else DEFAULT_MODEL


def slots(s: Session, sheet: Sheet) -> list[SheetSlot]:
    return list(s.scalars(select(SheetSlot).where(SheetSlot.sheet_id == sheet.id).order_by(SheetSlot.position)))


def candidates(s: Session, slot: SheetSlot) -> list[SheetCandidate]:
    return list(s.scalars(select(SheetCandidate).where(SheetCandidate.slot_id == slot.id)
                          .order_by(SheetCandidate.position)))


# --- submitting -------------------------------------------------------------------------------------------------------

def _submit(s: Session, scheduler: Scheduler, store: Store, user: User, sheet: Sheet, slot: SheetSlot,
            cand: SheetCandidate, max_pending: int, interactive: bool) -> None:
    """Queue one candidate's image, or hold it when the user's queue is full. A scheduler outage holds it too when
    nobody is waiting on the answer (the poller); in a request it is reported."""
    if pending_count(s, user) >= max_pending:
        cand.held, cand.artifact_id = True, None
        return
    try:
        if slot.kind == "base":
            arts = create(s, scheduler, store, user, sheet.project, prompt=slot.instruction, preset="sprite",
                          model=sheet.model, width=None, height=None, seed=None, count=1, steps=None,
                          max_pending=max_pending, background=sheet.background)
        else:
            base = sheet.base
            if base is None or base.status != "done":
                raise GenerateError("Pick the base first", 409)
            bg = background_of(base.full_prompt) or sheet.background
            arts = create(s, scheduler, store, user, sheet.project, prompt=slot.instruction, preset=base.preset,
                          model=edit_model(sheet), width=base.width, height=base.height, seed=None, count=1,
                          steps=None, max_pending=max_pending, parent=base, op="edit_image", reference=base,
                          full_prompt=FRAMING.format(bg=BACKGROUNDS[bg], line=slot.instruction.strip()))
    except GenerateError as e:
        if e.status == 429 or (not interactive and e.status == 503):
            cand.held, cand.artifact_id = True, None
            return
        raise
    cand.held, cand.artifact_id = False, arts[0].id


def _fill_slot(s: Session, scheduler: Scheduler, store: Store, user: User, sheet: Sheet, slot: SheetSlot,
               max_pending: int) -> None:
    """Make the slot's candidates up to CANDIDATES, then (re)submit every unfrozen one."""
    have = candidates(s, slot)
    for i in range(len(have), CANDIDATES):
        c = SheetCandidate(slot_id=slot.id, position=i)
        s.add(c)
        have.append(c)
    s.flush()
    for c in have:
        if not c.frozen:
            c.kept = False
            _submit(s, scheduler, store, user, sheet, slot, c, max_pending, interactive=True)


def fill_held(s: Session, scheduler: Scheduler, store: Store, max_pending: int) -> int:
    """Submit held candidates, oldest first, as their users' queues allow. Called by the poller; returns how many
    went out."""
    sent = 0
    held = s.execute(select(SheetCandidate, SheetSlot, Sheet)
                     .join(SheetSlot, SheetCandidate.slot_id == SheetSlot.id)
                     .join(Sheet, SheetSlot.sheet_id == Sheet.id)
                     .where(SheetCandidate.held.is_(True))
                     .order_by(SheetCandidate.created_at).limit(200)).all()
    full: set[str] = set()
    for cand, slot, sheet in held:
        if sheet.created_by in full:
            continue
        user = sheet.creator
        _submit(s, scheduler, store, user, sheet, slot, cand, max_pending, interactive=False)
        if cand.held:
            full.add(sheet.created_by)
        else:
            sent += 1
    return sent


# --- the user's actions -----------------------------------------------------------------------------------------------

def new_sheet(s: Session, scheduler: Scheduler, store: Store, user: User, project: Project, *, name: str,
              description: str | None, base: Artifact | None, model: str, background: str,
              max_pending: int) -> Sheet:
    """From a description (base candidates are generated) or from an existing image (it is the base)."""
    if not name.strip():
        raise GenerateError("Name the character")
    if model not in MODELS:
        raise GenerateError(f"Unknown model {model!r}")
    if background not in BACKGROUNDS:
        raise GenerateError(f"Unknown background {background!r}: {', '.join(BACKGROUNDS)}")
    if base is None and not (description or "").strip():
        raise GenerateError("Describe the character, or start from an image")
    if base is not None and (base.status != "done" or not base.image_key):
        raise GenerateError("That image isn't finished")
    sheet = Sheet(project_id=project.id, created_by=user.id, name=name.strip()[:100], model=model,
                  background=background_of(base.full_prompt) or background if base else background,
                  description=(description or (base.prompt if base else "")).strip())
    s.add(sheet)
    s.flush()
    slot = SheetSlot(sheet_id=sheet.id, position=0, kind="base", label="base", instruction=sheet.description)
    s.add(slot)
    s.flush()
    if base is not None:
        sheet.base_artifact_id = base.id
        s.add(SheetCandidate(slot_id=slot.id, position=0, artifact_id=base.id, frozen=True, kept=True))
    else:
        _fill_slot(s, scheduler, store, user, sheet, slot, max_pending)
    s.flush()
    return sheet


def set_base(s: Session, sheet: Sheet, cand: SheetCandidate) -> None:
    slot = s.get(SheetSlot, cand.slot_id)
    if slot.sheet_id != sheet.id or slot.kind != "base":
        raise GenerateError("That isn't one of this sheet's base candidates")
    if cand.artifact is None or cand.artifact.status != "done":
        raise GenerateError("That image isn't finished")
    if any(x.kind == "variation" for x in slots(s, sheet)) and sheet.base_artifact_id != cand.artifact_id:
        raise GenerateError("The variations are edits of the current base; remove them before changing it", 409)
    sheet.base_artifact_id = cand.artifact_id
    cand.frozen = cand.kept = True


def add_variations(s: Session, scheduler: Scheduler, store: Store, user: User, sheet: Sheet, lines: list[str],
                   max_pending: int) -> list[SheetSlot]:
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        raise GenerateError("Add at least one variation")
    if sheet.base is None:
        raise GenerateError("Pick the base first", 409)
    existing = slots(s, sheet)
    if len(existing) + len(lines) > MAX_SLOTS:
        raise GenerateError(f"A sheet holds at most {MAX_SLOTS - 1} variations")
    pos = max((x.position for x in existing), default=0)
    out = []
    for ln in lines:
        pos += 1
        slot = SheetSlot(sheet_id=sheet.id, position=pos, kind="variation", label=ln[:100], instruction=ln[:1000])
        s.add(slot)
        s.flush()
        _fill_slot(s, scheduler, store, user, sheet, slot, max_pending)
        out.append(slot)
    return out


def reroll(s: Session, scheduler: Scheduler, store: Store, user: User, sheet: Sheet, slot: SheetSlot,
           max_pending: int, instruction: str | None = None) -> None:
    """New images for the slot's unfrozen candidates (with a new instruction when rewording). Refused while one of
    them is still being made: re-rolling it would waste the GPU time already queued."""
    cands = candidates(s, slot)
    if all(c.frozen for c in cands):
        raise GenerateError("Every image here is frozen; unfreeze one to re-roll it")
    if any(not c.frozen and c.artifact is not None and c.artifact.status in ("queued", "running") for c in cands):
        raise GenerateError("Wait for these to finish before re-rolling", 409)
    if instruction is not None:
        if not instruction.strip():
            raise GenerateError("Say what this variation is")
        slot.instruction = instruction.strip()[:1000]
        if slot.kind == "variation":
            slot.label = slot.instruction[:100]
        else:
            sheet.description = slot.instruction
    _fill_slot(s, scheduler, store, user, sheet, slot, max_pending)


def remove_slot(s: Session, slot: SheetSlot) -> None:
    if slot.kind == "base":
        raise GenerateError("The base can't be removed; delete the sheet instead")
    s.delete(slot)


# --- export -----------------------------------------------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "image"


def export(s: Session, sheet: Sheet, store: Store) -> tuple[str, int, list[str]]:
    """Zip the kept images (cutout + pixel sprite each) into the store. Pixel sprites share the sheet's size and
    colours and the base's scale, standing on the bottom row of a square tile. Returns (key, files, shrunk names)."""
    base = sheet.base
    if base is None or base.status != "done":
        raise GenerateError("Pick the base first", 409)
    params = sprites.PixelParams(sheet.pixel_size, sheet.pixel_colors, "square")
    if err := params.check():
        raise GenerateError(err)
    scale = sprites.subject_scale(b"".join(store.get(make_cutout(base, store).image_key)), sheet.pixel_size)
    buf, files, shrunk, used = io.BytesIO(), 0, [], set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for slot in slots(s, sheet):
            n = 0
            for c in candidates(s, slot):
                a = c.artifact
                if not c.kept or a is None or a.status != "done":
                    continue
                n += 1
                stem = f"{_slug(slot.label)}_{n}"
                while stem in used:
                    stem += "x"
                used.add(stem)
                cut = b"".join(store.get(make_cutout(a, store).image_key))
                px, was_shrunk = sprites.pixelate_at(cut, params, scale)
                z.writestr(f"{stem}_cutout.png", cut)
                z.writestr(f"{stem}_{params.size}px.png", px)
                files += 2
                if was_shrunk:
                    shrunk.append(f"{stem}_{params.size}px.png")
    if not files:
        raise GenerateError("Keep at least one image first")
    key = f"sheets/{sheet.id}/export.zip"
    store.put(key, buf.getvalue(), "application/zip")
    return key, files, shrunk


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def link(s: Session, sheet: Sheet) -> tuple[str, str]:
    s.execute(delete(SheetLink).where(SheetLink.expires_at < utcnow()))
    raw = LINK_PREFIX + secrets.token_urlsafe(24)
    expires = utcnow() + LINK_TTL
    s.add(SheetLink(token_hash=_hash(raw), sheet_id=sheet.id, expires_at=expires))
    return "/dl/" + raw, expires.isoformat()


def resolve_link(s: Session, raw: str) -> Sheet | None:
    if not raw.startswith(LINK_PREFIX) or len(raw) > 100:
        return None
    row = s.get(SheetLink, _hash(raw))
    if row is None:
        return None
    exp = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    return s.get(Sheet, row.sheet_id) if exp > utcnow() else None


def zip_name(sheet: Sheet) -> str:
    return f"{_slug(sheet.name)}-sheet.zip"


def counts(s: Session, sheet_ids: list[str]) -> dict[str, int]:
    """Kept images per sheet, for lists."""
    if not sheet_ids:
        return {}
    rows = s.execute(select(SheetSlot.sheet_id, func.count()).join(SheetCandidate, SheetCandidate.slot_id == SheetSlot.id)
                     .where(SheetSlot.sheet_id.in_(sheet_ids), SheetCandidate.kept.is_(True))
                     .group_by(SheetSlot.sheet_id)).all()
    return dict(rows)
