"""Durable per-client SEO intelligence files for prompt-time reuse.

The database is the queryable source of truth; this JSON file is the compact,
human-inspectable handoff requested for AI tools. It is regenerated weekly and
contains no OAuth credentials.
"""
from __future__ import annotations

import json
import os
import re
import tempfile

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
    fd, temp = tempfile.mkstemp(prefix=".seo-", suffix=".json", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, target)
    finally:
        try:
            if os.path.exists(temp):
                os.remove(temp)
        except OSError:
            pass
    return target


def read_client(client_id):
    try:
        with open(path_for(client_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None
