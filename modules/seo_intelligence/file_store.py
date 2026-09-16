"""Per-client SEO intelligence files for prompt-time reuse.

The database is the queryable source of truth; this JSON file is the compact,
human-inspectable handoff requested for AI tools. It is regenerated weekly and
contains no OAuth credentials.

## Why this one is NOT moved into the database

`jsonstore.unmirrored_json_writers()` named this file once it could see past
`json.dump(`, and the obvious reading -- another store on a disk nobody backs
up -- is the wrong one here. `context._memory()` reads this file **only where
the SEOMemory query raised**. It is the offline copy of a database row, for
the one case where the database cannot answer. Mirroring it into that same
database would leave the fallback needing the thing it is a fallback for.

So it goes through `jsonstore.write_json(durable=False)`: the same atomic
write it already had, declared as a cache rather than left for a scanner to
guess about, and listed on `status()` beside everything else deliberately not
backed up. Losing it costs one weekly regeneration from the row it was
built from.
"""
from __future__ import annotations

import json
import os
import re

from .models import SEOMemory


def _root():
    try:
        from hub import jsonstore
        base = jsonstore.data_root()
    except Exception:
        base = "/var/data" if os.path.isdir("/var/data") else "data"
    path = os.path.join(base, "seo-intelligence")
    os.makedirs(path, exist_ok=True)
    return path


def _slug(client_id):
    clean = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(client_id or "client")).strip("-.")
    return clean[:160] or "client"


def path_for(client_id):
    return os.path.join(_root(), f"{_slug(client_id)}.json")


def mirror_client(client_id):
    row = SEOMemory.query.filter_by(client_id=client_id).first()
    if not row:
        return None
    try:
        payload = json.loads(row.memory_json or "{}")
    except Exception:
        payload = {}
    payload["file_meta"] = {
        "purpose": "Shared SEO evidence for SmartHub AI calls",
        "contains_credentials": False,
        "source_week": str(row.source_week) if row.source_week else None,
    }
    target = path_for(client_id)
    # fsync=True because the hand-rolled write this replaces did it, and the
    # point of this file is being readable when the database is not -- which
    # includes after the kind of unclean stop that takes the database with it.
    from hub import jsonstore
    if not jsonstore.write_json(target, payload, durable=False, indent=2,
                                fsync=True):
        return None
    return target


def read_client(client_id):
    try:
        with open(path_for(client_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
