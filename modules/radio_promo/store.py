"""Where radio projects live.

One JSON file on the persistent disk, same pattern as the UTM Builder. A
project is either **attached to a client** (``client`` holds a name from the
Hub's client registry) or a **spec** piece (``spec: true``, no client) — which
is the only structural difference between the two kinds of work. Spec projects
can be attached to a client later without losing anything, so a spec spot that
wins the business becomes that client's first spot.

Every draft, rewrite, tighten and hand edit is appended to ``versions`` rather
than overwriting, so nothing a client approved can be silently lost.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
from hub import jsonstore

MAX_PROJECTS = 4000


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, fallback: str = "spec") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s[:80] or fallback


def data_dir() -> str:
    """Through jsonstore, which is the one place that decides where
    persistent files live.

    This was its own six-line copy of that expression and it did not read
    HUB_DATA_DIR at all -- so on a deployment that sets it, every project
    here landed outside the root the mirror keys against and the backup
    sweep reports on, while every other module moved. They agreed on this
    service only because the variable happens to be unset, which is the luck
    rather than design jsonstore.data_root() was written to end.
    """
    return jsonstore.data_dir("radio_promo")


def _path() -> str:
    return os.path.join(data_dir(), "projects.json")


# Reads and writes go through hub.jsonstore, which keeps the atomic .tmp +
# rename this module already had and adds a mirror into the database. The
# Render disk these files live on is not part of the database backup and does
# not survive being recreated, and every draft, rewrite and hand edit
# this module deliberately appends rather than overwrites lives in here.

def _read() -> list[dict]:
    rows = jsonstore.read_json(_path(), default=[])
    return rows if isinstance(rows, list) else []


def _mutate(apply):
    """Read, change and write the collection as one indivisible step.

    `threading.Lock` was the wrong tool twice over and it is gone. It guarded
    only the write, so two threads that had already read the same snapshot
    still overwrote each other; and it is per-process, while this deployment
    runs two gunicorn workers, so it never saw the other one at all.
    `jsonstore.update_json` holds both a per-path thread lock and an flock
    across workers, for the whole read-change-write.

    `apply` returns the rows to write, or None to write nothing -- which is
    what "no such project" and "nothing to delete" mean here, and is why a
    lookup that misses does not queue a pointless write on both workers.
    """
    return jsonstore.update_json(_path(), apply, default=[], indent=1)


def all_projects() -> list[dict]:
    return _read()


def create(fields: dict) -> dict:
    row = {
        "id": "rp_" + secrets.token_urlsafe(9),
        "created_at": now(),
        "updated_at": now(),
        "status": "draft",
        "spec": bool(fields.get("spec")),
        "client": (fields.get("client") or "").strip(),
        "client_slug": slugify(fields.get("client") or "", "spec"),
        "project_name": (fields.get("project_name") or "").strip(),
        "team_member": (fields.get("team_member") or "").strip(),
        "company": (fields.get("company") or "").strip(),
        "home_url": (fields.get("home_url") or "").strip(),
        "landing_url": (fields.get("landing_url") or "").strip(),
        "include_phone": bool(fields.get("include_phone")),
        "phone": (fields.get("phone") or "").strip(),
        "promotion": (fields.get("promotion") or "").strip(),
        "disclaimer": (fields.get("disclaimer") or "").strip(),
        "pronunciations": fields.get("pronunciations") or [],
        "brand": fields.get("brand") or {},
        # These four are empty on a new project and carried on a variation,
        # which is why they read from `fields` rather than being hardcoded: a
        # clone that arrived with no brief and no scripts would be a new
        # project wearing a lineage.
        "analysis": fields.get("analysis"),
        "tone_id": (fields.get("tone_id") or "").strip(),
        "scripts": fields.get("scripts") or {},
        "voice_want": fields.get("voice_want") or {},
        "voice_matches": [],
        "music_beds": [],
        # Which lengths this job writes. Normalised by the caller through
        # `catalog.slots_of()`; a row saved before this field existed carries
        # none, and that function reads the absence as the :15/:30 pair.
        "slots": list(fields.get("slots") or ()),
        # A bed and a mix per slot, because a :15 and a :60 need beds of their
        # own length. Keyed on the slot rather than a list, so replacing one
        # cannot leave two claiming the same slot.
        "beds": {},
        "mixes": {},
        "vo_only": bool(fields.get("vo_only")),
        # Lineage, both ways. A variation names its parent and the parent lists
        # its variations, so neither row is the only account of the pair.
        "variation_of": (fields.get("variation_of") or "") or None,
        "variations": [],
        "spots": [],
        "banner": None,
        "versions": [],
    }

    def _insert(rows):
        rows.insert(0, row)
        return rows[:MAX_PROJECTS]

    _mutate(_insert)
    return row


def get(project_id: str) -> dict | None:
    for row in _read():
        if row.get("id") == project_id:
            return row
    return None


def update(project_id: str, changes: dict) -> dict | None:
    """Change one project, reading and writing the collection as one step.

    The read has to be inside the lock, not merely the write. This file holds
    every project, so a change to one is written back as the whole list -- and
    with the read outside, two people editing two *unrelated* projects each
    start from the same snapshot and the second save silently drops the first.
    Both are told it worked. Reproduced with two threads and two projects
    before this moved onto `jsonstore.update_json`.
    """
    found: list[dict] = []

    def _apply(rows):
        for i, row in enumerate(rows):
            if row.get("id") != project_id:
                continue
            row.update(changes)
            row["updated_at"] = now()
            if "client" in changes:
                row["client_slug"] = slugify(row.get("client") or "", "spec")
                row["spec"] = not bool(row.get("client"))
            rows[i] = row
            found.append(row)
            return rows
        return None                     # no such project: write nothing

    _mutate(_apply)
    return found[0] if found else None


def add_version(project_id: str, kind: str, payload: dict, actor: str = "") -> dict | None:
    """Append one version, reading the list inside the same lock that writes it.

    This module opens by promising that every draft, rewrite, tighten and hand
    edit is *appended* rather than overwriting, "so nothing a client approved
    can be silently lost". Read through `get()` and written through `update()`
    that promise did not hold: the list was read in one step and written whole
    in another, so two appends racing kept one and dropped the other -- the
    exact loss the append was there to prevent.
    """
    entry = {"at": now(), "kind": kind, "actor": actor, "payload": payload}
    found: list[dict] = []

    def _apply(rows):
        for i, row in enumerate(rows):
            if row.get("id") != project_id:
                continue
            versions = list(row.get("versions") or [])
            versions.append(entry)
            row["versions"] = versions[-60:]
            row["updated_at"] = now()
            rows[i] = row
            found.append(row)
            return rows
        return None

    _mutate(_apply)
    return found[0] if found else None


def delete(project_id: str) -> bool:
    gone: list[bool] = []

    def _apply(rows):
        kept = [r for r in rows if r.get("id") != project_id]
        if len(kept) == len(rows):
            return None                 # nothing to remove: write nothing
        gone.append(True)
        return kept

    _mutate(_apply)
    return bool(gone)


def library(query: str = "", scope: str = "all") -> list[dict]:
    """``scope`` is ``all``, ``spec`` or ``client``."""
    q = str(query or "").strip().lower()
    out = []
    for row in _read():
        if scope == "spec" and not row.get("spec"):
            continue
        if scope == "client" and row.get("spec"):
            continue
        if q:
            haystack = " ".join(str(row.get(k) or "") for k in (
                "client", "company", "project_name", "team_member", "promotion")).lower()
            if q not in haystack:
                continue
        out.append(row)
    return out


def cloud_folder(row: dict) -> str:
    """``smart1-radio-promo/<spec|client-slug>/<project>-<date>``."""
    root = os.environ.get("RADIO_PROMO_FOLDER", "smart1-radio-promo")
    who = "spec" if row.get("spec") else (row.get("client_slug") or "client")
    project = slugify(row.get("project_name") or "project", "project")
    day = (row.get("created_at") or now())[:10]
    return f"{root}/{who}/{project}-{day}"
