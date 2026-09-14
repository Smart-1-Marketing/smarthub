"""Durable, locked client state. OAuth secrets never leave this module."""
import hashlib
import os
import secrets
import time
from cryptography.fernet import Fernet, InvalidToken
from hub import jsonstore


def path():
    return os.path.join(jsonstore.data_dir("youtube"), "studio.json")


def read():
    return jsonstore.read_json(path(), default={}) or {}


def update(fn):
    result = []
    def mutate(data):
        result.append(fn(data))
        return data
    if not jsonstore.update_json(path(), mutate, default={}):
        raise RuntimeError("YouTube changes could not be saved. Please retry.")
    return result[0]


def client_key(name):
    name = str(name or "").strip()
    if not name or len(name) > 200:
        raise ValueError("Choose a client first (maximum 200 characters).")
    return hashlib.sha256(name.casefold().encode()).hexdigest()


def client(data, name):
    return data.setdefault("clients", {}).setdefault(client_key(name), {
        "name": name.strip(), "channels": {}, "drafts": {}, "launch": {}})


def cipher():
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise ValueError("Token encryption is not configured. Ask your Hub administrator to finish setup.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ValueError("Token encryption configuration is invalid.") from exc


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("This connection needs reconnecting; its saved credentials cannot be read.") from exc


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def invite(name, channel_id="", kind="connect", draft_id=""):
    token = secrets.token_urlsafe(32)
    def save(data):
        row = client(data, name)
        if channel_id and channel_id not in row["channels"]:
            raise ValueError("That channel is not linked to this client.")
        if draft_id and draft_id not in row["drafts"]:
            raise ValueError("That draft does not belong to this client.")
        # One active link per purpose and target; regenerating revokes the old link.
        links = data.setdefault("invites", {})
        for key in list(links):
            link = links[key]
            if link["expires"] < time.time() or (link["client"] == name and
                    link["kind"] == kind and link.get("draft_id", "") == draft_id and
                    link.get("channel_id", "") == channel_id):
                del links[key]
        links[digest(token)] = {"client": name, "channel_id": channel_id,
            "kind": kind, "draft_id": draft_id, "expires": time.time() + 7 * 86400}
    update(save)
    return token


def lookup(token, kind=None):
    row = read().get("invites", {}).get(digest(token), {})
    if not row or row.get("expires", 0) < time.time() or row.get("used"):
        raise ValueError("This link has expired or was replaced. Ask your Smart 1 contact for a new link.")
    if kind and row["kind"] != kind:
        raise ValueError("This link cannot be used for that action.")
    return row


def public_client(name):
    row = read().get("clients", {}).get(client_key(name), {
        "name": name, "channels": {}, "drafts": {}, "launch": {}})
    # Explicit field allowlist: never return tokens, resumable URLs or OAuth state.
    channels = []
    for channel in row.get("channels", {}).values():
        safe = {k: channel.get(k) for k in ("id", "title", "description", "thumbnail", "url",
            "statistics", "refreshed_at", "videos", "connected_at", "connection_error", "review")}
        safe["connected"] = bool(channel.get("refresh_token"))
        channels.append(safe)
    drafts = [{k: v for k, v in draft.items() if k not in ("upload_session",)}
              for draft in row.get("drafts", {}).values()]
    return {"name": row["name"], "channels": channels, "drafts": drafts, "launch": row.get("launch", {})}

