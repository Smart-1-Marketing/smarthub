"""Projects on disk, and the rule about what a source-frame edit may touch.

**One file per project, never one file holding all of them** — the
`hub/drafts.py` rule. Two people resizing at the same moment would each write
the whole collection back and the second write would drop the first one's
work, which is precisely the failure a project store exists to prevent. The
index is a separate small file so a library can be listed without reading
every canvas.

**Through `hub/jsonstore.py`**, so a project outlives the Render disk, and
deleted through `jsonstore.delete_json` rather than `os.remove`, or the
database mirror restores it and the delete undoes itself.

**The role lives on the object, not in a second map.** The build plan carries
a `role_map` of `{object_id: role}` beside the objects; `role_map()` still
answers in that shape for anything that wants it, but it is *derived*. Two
records of one fact is how an object comes to be a logo in one of them and a
headline in the other, with nothing on any screen saying which is right.

## What a source edit propagates to

This is the whole of §6 and both halves are load-bearing in opposite
directions.

**Copy propagates to every frame, an edited one included.** A headline change
is a headline change, and making a rep retype it in eight frames is how one of
the eight goes out with last week's offer on it. An edited frame that receives
one is *flagged* rather than silently updated, because new copy in a
hand-tuned box may no longer fit and only a person can see that.

**Layout never propagates to an edited frame.** Somebody moved that button on
purpose. Regenerating it because the source moved is destroying a decision
nobody recorded a reason for, and it is invisible — the frame still renders,
just not the way it was left.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
import threading
from typing import Any

from hub import jsonstore

from . import engine
from . import sizes as S

_lock = threading.Lock()

# A project is bounded on both axes for the reason `hub/drafts.py` gives: an
# autosave loop that fills the 5 GB disk takes every other module with it.
MAX_OBJECTS = 300
MAX_FRAMES = 40


def _dir() -> str:
    return jsonstore.data_dir("magic-resize")


def _index_path() -> str:
    return os.path.join(_dir(), "_index.json")


def _project_path(pid: str) -> str:
    return os.path.join(_dir(), f"{pid}.json")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def slugify(v: str, fallback: str = "resize") -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
    return s[:60] or fallback


def new_id() -> str:
    return secrets.token_hex(8)


# --------------------------------------------------------------------------

def load_index() -> list[dict]:
    rows = jsonstore.read_json(_index_path(), default=[])
    return rows if isinstance(rows, list) else []


def _write_index(rows: list[dict]) -> None:
    with _lock:
        jsonstore.write_json(_index_path(), rows[:2000])


def _index_row(project: dict) -> dict:
    frames = project.get("frames") or {}
    return {
        "id": project["id"], "name": project.get("name", ""),
        "client": project.get("client", ""),
        "created": project.get("created", ""),
        "updated": project.get("updated", ""),
        "created_by": project.get("created_by", ""),
        "frames": len(frames),
        "needs_review": sum(1 for f in frames.values()
                            if f.get("status") == engine.NEEDS_REVIEW),
    }


def _touch_index(project: dict) -> None:
    rows = [r for r in load_index() if r.get("id") != project["id"]]
    rows.insert(0, _index_row(project))
    _write_index(rows)


def get(pid: str) -> dict | None:
    if not pid or not re.fullmatch(r"[a-f0-9]{4,40}", str(pid)):
        return None
    row = jsonstore.read_json(_project_path(pid), default=None)
    return row if isinstance(row, dict) else None


def save(project: dict) -> dict:
    project["updated"] = _now()
    jsonstore.write_json(_project_path(project["id"]), project)
    _touch_index(project)
    return project


def delete(pid: str) -> bool:
    project = get(pid)
    if not project:
        return False
    # Never a bare os.remove: the jsonstore mirror would restore it on the
    # next read and the delete would undo itself.
    jsonstore.delete_json(_project_path(pid))
    _write_index([r for r in load_index() if r.get("id") != pid])
    return True


def create(*, name: str, client: str = "", source: dict,
           bundle: str = "display_standard", created_by: str = "") -> dict:
    project = {
        "id": new_id(),
        "name": (name or "Untitled").strip()[:120],
        "client": (client or "").strip()[:160],
        "created": _now(),
        "created_by": created_by or "",
        "bundle": bundle,
        # Placeholder for the BrandTemplate decision that is still open (§9,
        # step 6). Carried as a reference and read by nothing yet, so it
        # cannot quietly become a working integration nobody signed off.
        "brand_profile_ref": "",
        "source": _clean_source(source),
        "frames": {},
    }
    return save(project)


def _clean_source(source: dict) -> dict:
    objects = list((source or {}).get("objects") or [])[:MAX_OBJECTS]
    return {
        "width": int((source or {}).get("width") or 0),
        "height": int((source or {}).get("height") or 0),
        "family": (source or {}).get("family") or "",
        "objects": objects,
    }


def role_map(project: dict) -> dict[str, str]:
    """The build plan's `{object_id: role}` shape, derived rather than stored."""
    return {o.get("id", ""): o.get("role", "")
            for o in (project.get("source") or {}).get("objects") or []
            if o.get("id")}


# --------------------------------------------------------------------------
# Generating and re-generating frames
# --------------------------------------------------------------------------

def targets_for(project: dict) -> list[dict]:
    bundle = project.get("bundle") or "display_standard"
    rows = S.bundle_sizes(bundle)
    custom = project.get("custom_sizes") or []
    for row in custom:
        rows.append({"id": row.get("id") or f"custom_{row.get('w')}x{row.get('h')}",
                     "label": row.get("label") or f"{row.get('w')}x{row.get('h')}",
                     "w": int(row.get("w") or 0), "h": int(row.get("h") or 0),
                     "family": "", "source": "custom"})
    return rows[:MAX_FRAMES]


def generate(project: dict, *, only: list[str] | None = None) -> dict:
    """Build every frame that may be rebuilt, and say what happened to each.

    A frame marked `edited` is skipped and *named* in the report. A skip
    nobody is told about is indistinguishable from a frame that regenerated
    into exactly what it already was.
    """
    frames = project.setdefault("frames", {})
    report: dict[str, list[str]] = {"built": [], "kept": [], "skipped": []}

    for target in targets_for(project):
        sid = target["id"]
        if only and sid not in only:
            continue
        existing = frames.get(sid) or {}
        if existing.get("status") == engine.EDITED:
            report["skipped"].append(sid)
            continue
        frame = engine.resize(project["source"], target)
        frame["last_synced_at"] = _now()
        frame["exported_asset_url"] = existing.get("exported_asset_url", "")
        frames[sid] = frame
        report["built"].append(sid)

    for sid in list(frames):
        if sid not in {t["id"] for t in targets_for(project)}:
            report["kept"].append(sid)
    return report


TEXT_FIELDS = ("text",)


def propagate_text(project: dict) -> dict:
    """Copy the source's words into every frame, edited frames included.

    Only the words. An edited frame keeps its own geometry, its own type size
    and its own everything else — and is flagged, because copy that has grown
    may no longer fit a box somebody set by hand and nothing here can see
    that.
    """
    source_text = {o.get("id"): o.get("text", "")
                   for o in (project.get("source") or {}).get("objects") or []}
    changed: list[str] = []
    flagged: list[str] = []

    for sid, frame in (project.get("frames") or {}).items():
        touched = False
        for obj in frame.get("objects") or []:
            oid = obj.get("id")
            if oid not in source_text:
                continue
            if obj.get("text", "") != source_text[oid]:
                obj["text"] = source_text[oid]
                touched = True
        if not touched:
            continue
        changed.append(sid)
        frame["text_synced_at"] = _now()
        if frame.get("status") == engine.EDITED:
            flagged.append(sid)
            frame["text_changed_since_edit"] = True
            frame.setdefault("findings", []).append({
                "code": "copy_changed", "level": "warn", "source": "house",
                "objects": [],
                "message": ("The copy changed on the design after this frame "
                            "was hand-tuned. The words are updated; check "
                            "they still fit."),
            })
    return {"changed": changed, "flagged_edited": flagged}


def mark_edited(project: dict, size_id: str, objects: list[dict]) -> dict | None:
    frame = (project.get("frames") or {}).get(size_id)
    if not frame:
        return None
    frame["objects"] = list(objects or [])[:MAX_OBJECTS]
    frame["status"] = engine.EDITED
    frame["edited_at"] = _now()
    frame["text_changed_since_edit"] = False
    findings = engine.guard(frame["objects"], frame["width"], frame["height"])
    from . import qc
    frame["findings"] = findings + qc.run(frame)
    frame["verdict"] = qc.verdict(frame["findings"])
    return frame


def mark_ai(project: dict, size_id: str, objects: list[dict]) -> dict | None:
    """A frame a model laid out — its own status, never folded into `auto`.

    Which frames a template produced and which a model adjusted is the thing
    somebody scanning a set wants to know at a glance, and one status for both
    makes it unanswerable.
    """
    frame = (project.get("frames") or {}).get(size_id)
    if not frame:
        return None
    frame["objects"] = list(objects or [])[:MAX_OBJECTS]
    frame["status"] = engine.AI
    frame["last_synced_at"] = _now()
    findings = engine.guard(frame["objects"], frame["width"], frame["height"])
    from . import qc
    frame["findings"] = findings + qc.run(frame)
    frame["verdict"] = qc.verdict(frame["findings"])
    return frame
