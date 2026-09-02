"""Client-safe, shareable index of approvals and proofs.

The Hub has several customer links, but each tool used to hand them out in
isolation.  This module creates one stable, navigation-free page per client
and keeps optional proof links alongside the links the tools already publish.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from itsdangerous import BadSignature, URLSafeSerializer

from . import jsonstore


def _path():
    return os.path.join(jsonstore.data_dir("client_portal"), "links.json")


def _store():
    return jsonstore.read_json(_path(), default={}) or {}


def _save(data):
    jsonstore.write_json(_path(), data, durable=True, indent=2)


def _key(client: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(client or "").lower()).strip("-")


def _serializer(secret: str):
    # The Hub's authentication uses the environment-backed secret rather than
    # Flask's session config, which is intentionally unset in this app.
    secret = secret or os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY") or os.environ.get("SESSION_SECRET")
    return URLSafeSerializer(secret or "smart1-client-links-development", salt="smart1-client-links-v1")


def token(client: str, secret: str) -> str:
    return _serializer(secret).dumps({"client": str(client or "").strip()})


def client_from_token(value: str, secret: str) -> str:
    try:
        return str(_serializer(secret).loads(value).get("client") or "").strip()
    except BadSignature:
        return ""


def add(client: str, label: str, url: str, kind: str = "Proof") -> dict:
    """Save an explicitly selected, client-safe link. Internal URLs are refused."""
    client, label, url = str(client or "").strip(), str(label or "").strip(), str(url or "").strip()
    if not client or not label or not re.match(r"^https?://", url, re.I):
        return {"ok": False, "error": "Enter a client, label, and full https:// link."}
    # A copied app URL is not a client link. This page must never become a
    # side door into the Hub just because a staff member pasted one by mistake.
    if re.search(r"/(client360|tools/(?!social/c/|image-picker/pick/)|suite|google|sites)(?:/|$)", url, re.I):
        return {"ok": False, "error": "That is an internal Hub link, not a client-safe proof link."}
    data, key = _store(), _key(client)
    rows = data.setdefault(key, {"client": client, "links": []})["links"]
    rows.append({"label": label[:120], "url": url[:2000], "kind": kind[:40] or "Proof",
                 "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    _save(data)
    return {"ok": True}


def links(client: str, origin: str = "") -> list[dict]:
    """Known public tool links plus staff-selected proof links."""
    out, seen = [], set()
    def include(label, url, kind, icon):
        url = str(url or "").strip()
        if url and url not in seen:
            seen.add(url); out.append({"label": label, "url": url, "kind": kind, "icon": icon})
    try:
        from modules.social_planner import links as social_links
        for row in social_links.all_links(client, base=origin):
            include(row.get("label"), row.get("url"), "Social", "✦")
    except Exception:
        pass
    try:
        from modules.image_picker import provisioning
        got = provisioning.link_for(client, "", create=False, base=origin)
        include("Upload files", got.get("share_url"), "Files", "↑")
    except Exception:
        pass
    for row in _store().get(_key(client), {}).get("links", []):
        include(row.get("label"), row.get("url"), row.get("kind") or "Proof", "✓")
    return out
