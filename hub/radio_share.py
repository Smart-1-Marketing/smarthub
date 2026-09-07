"""Client approval for a radio spot — one implementation, read by both tools.

Fan Radio built this first: a random share token, a public page with no
login, approve/comment per spot, feedback written to disk before anything
else runs. Radio Promo had none of it. Building a second version would be
two client-facing approval pages for one medium, differing in whatever each
remembered — the two-proposal-builders failure this codebase already paid
for once, and the reason this file exists rather than a copy of Fan Radio's
route pasted into Radio Promo.

**A share is a dict, not a row.** `{token, enabled, opened, headline, intro,
cta_label, cta_url}` — the exact shape Fan Radio has written since it was
built. Regenerating a token overwrites it in place; there is no round
history or cap here, unlike the Commercial Builder's `ReviewShare` rows,
because the simpler shape is the one already live and tested and a radio
spot is a shorter conversation than a multi-scene commercial.

**`record_decision()` takes the dict to mutate, not a shape.** Fan Radio's
spots are a list, each carrying its own `id`; Radio Promo's client-facing
units are keyed by length (`"15"`, `"30"`, `"60"`), so the two cannot share
one storage shape. What they can share is the rule for what a decision
writes onto whichever dict holds it — `status`, `decided_at`, `decided_by`
— so that rule cannot drift between the two the day one of them changes it.

**Nothing here touches a disk or a database.** Both tools' own stores
persist their own project shape around this; this file is pure data and
functions, the same split `hub/radio_spec.py` already draws for the
length/dB/QC rules the two tools share.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import secrets
import threading
import time

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,64}")

# Three real decisions, plus a general note that is not one. "approve_all"
# is its own action rather than a loop the caller runs once per spot,
# because a bulk approval and a per-spot one are different claims — a
# single press covering spots the client never opened individually.
FEEDBACK_ACTIONS = ("approve", "changes", "comment", "approve_all")


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def new_token() -> str:
    """Random, never derived. A page carrying scripts that haven't aired
    yet needs a real token, not `{slug}-{timestamp}` a competitor can guess."""
    return secrets.token_urlsafe(24)


def is_token(value: str) -> bool:
    return bool(TOKEN_RE.fullmatch(str(value or "")))


def new_share() -> dict:
    return {"token": new_token(), "enabled": False, "opened": 0,
            "headline": "", "intro": "", "cta_label": "", "cta_url": ""}


def share_url(mount: str, token: str) -> str:
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    path = f"{mount.rstrip('/')}/r/{token}"
    return (base + path) if base else path


def update_share(share: dict, body: dict) -> dict:
    """Apply a settings POST onto a share dict, in place, and return it.

    `regenerate` mints a fresh token and resets the open count — the old
    token stops resolving, but nothing already on the project, feedback and
    decisions included, is touched.
    """
    share = dict(share or new_share())
    if "enabled" in body:
        share["enabled"] = bool(body["enabled"])
    for key, cap in (("headline", 120), ("intro", 600),
                     ("cta_label", 60), ("cta_url", 400)):
        if key in body:
            share[key] = str(body[key] or "").strip()[:cap]
    if body.get("regenerate"):
        share["token"] = new_token()
        share["opened"] = 0
    return share


def validate_feedback(body: dict) -> dict:
    """One reading of what a feedback POST must carry, for both tools.

    Never raises: `{"ok": False, "error": "..."}` is a sentence a route can
    hand straight back rather than a route re-deriving its own wording.
    """
    action = body.get("action")
    if action not in FEEDBACK_ACTIONS:
        return {"ok": False, "error": "Pick approve or request changes.",
                "name": "", "comment": "", "action": "", "spot_id": ""}
    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "Add your name so we know who "
                "approved it.", "name": "", "comment": "", "action": action,
                "spot_id": ""}
    comment = str(body.get("comment") or "").strip()
    if action == "changes" and not comment:
        return {"ok": False, "error": "Tell us what to change and we'll "
                "turn it around.", "name": name, "comment": "",
                "action": action, "spot_id": ""}
    spot_id = str(body.get("spot_id") or "").strip()
    return {"ok": True, "error": "", "name": name, "comment": comment,
            "action": action, "spot_id": spot_id}


def record_decision(target: dict | None, feedback: list, *, name: str,
                    action: str, comment: str, spot_id: str = "") -> dict:
    """Write one decision, and append it to the feedback list.

    `target` is whichever dict a decision lands on — a spot, or whatever a
    tool keys its own per-item state by — and is `None` for a general
    comment naming no spot. Only `approve`/`approve_all`/`changes` change a
    target's status; `comment` never does, because a general note is not a
    decision about any one spot.
    """
    entry = {"at": now(), "name": name[:80], "spot_id": spot_id,
            "action": "approve" if action in ("approve", "approve_all")
            else action, "comment": comment[:4000]}
    if target is not None and action != "comment":
        target["status"] = ("approved" if action in ("approve", "approve_all")
                            else "changes")
        target["decided_at"] = entry["at"]
        target["decided_by"] = name[:80]
    feedback.append(entry)
    return entry


def notify(env_var: str, payload: dict) -> None:
    """Best-effort ping to a configured webhook.

    Never raises, and is only ever called after the feedback is on disk —
    a failure here must not cost the record, the rule Fan Radio's own
    `_notify()` was written under.
    """
    url = (os.environ.get(env_var) or "").strip()
    if not url:
        return
    try:
        import requests
        requests.post(url, json=payload, timeout=6)
    except Exception:                                      # noqa: BLE001
        pass


class RateLimiter:
    """Per-address request counting for a public feedback route.

    Each tool keeps its own instance — a flood on one must not throttle the
    other's legitimate traffic — but the counting rule is one implementation
    rather than two hand-typed copies that drift the day a threshold changes.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = {}

    def hit(self, bucket: str, address: str, limit: int,
           window: int = 60) -> bool:
        key = f"{bucket}:{address}"
        cutoff = time.time() - window
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if t > cutoff]
            if len(hits) >= limit:
                self._hits[key] = hits
                return True
            hits.append(time.time())
            self._hits[key] = hits
            if len(self._hits) > 5000:                    # cheap sweep
                for k in list(self._hits):
                    if not [t for t in self._hits[k] if t > cutoff]:
                        self._hits.pop(k, None)
        return False
