"""Tell the people on a client's account that files arrived, and when the
SEO copies are all made.

A client uploading through their share link is the one event on a gallery
that nobody at Smart 1 is watching for, because it happens on a page with
no login. Before this, the only trace was an activity-log row under the
actor "client" -- which the inbox filters out, because it shows a person
their own work -- so forty photographs could sit in a gallery for a week
with the rep who asked for them none the wiser.

Two channels, both already in the Hub, neither of which is email (there is
no mail sender here; `hub/scheduler.py` says so twice):

* **`hub/job_notify.py`**, the corner card that follows somebody to
  whatever page they are on. One pointer per person per client per hour,
  re-registered under the same id as more files land so a batch of forty
  is one card saying forty rather than forty cards.
* **The activity log**, which the inbox bell reads. `hub/help_center.py`
  draws these rows for the people on the client's book.

Who is "on the account": the owner (`client_owner.owner_of`), everybody
following the client (`client_owner.followers_of`), and the Client Success
contact Knack names (`client_owner.client_success_of`). Never raises --
a notice that cannot be delivered costs the nudge, never the upload.
"""
from __future__ import annotations

import hashlib
import logging
import time

log = logging.getLogger(__name__)

TOOL = "client_assets"


def attached(client: str) -> list[str]:
    """The email addresses on this client's account, deduplicated."""
    client = str(client or "").strip()
    if not client:
        return []
    out: list[str] = []
    try:
        from hub import client_owner
    except Exception:                                     # noqa: BLE001
        return []
    try:
        owner = client_owner.owner_of(client)
        if owner and owner.get("email"):
            out.append(str(owner["email"]).strip().lower())
    except Exception:                                     # noqa: BLE001
        pass
    try:
        for f in client_owner.followers_of(client):
            if f.get("email"):
                out.append(str(f["email"]).strip().lower())
    except Exception:                                     # noqa: BLE001
        pass
    try:
        cs = client_owner.client_success_of(client)
        if cs.get("email"):
            out.append(str(cs["email"]).strip().lower())
    except Exception:                                     # noqa: BLE001
        pass
    seen, unique = set(), []
    for email in out:
        if email and email not in seen:
            seen.add(email)
            unique.append(email)
    return unique


def _pointer_id(client: str, email: str, suffix: str) -> str:
    raw = f"{TOOL}:{client.strip().lower()}:{suffix}:{email}"
    return "assets-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _gallery_url(client: str) -> str:
    from urllib.parse import quote
    return "/tools/image-picker/gallery/for-client?name=" + quote(client)


def tell(client: str, *, label: str, suffix: str, return_url: str = "") -> int:
    """Register one finished pointer per attached person. Returns how many."""
    try:
        from hub import job_notify
    except Exception:                                     # noqa: BLE001
        return 0
    n = 0
    for email in attached(client):
        try:
            job_notify.register(owner=email, tool=TOOL, label=label,
                                return_url=return_url or _gallery_url(client),
                                status="done",
                                id=_pointer_id(client, email, suffix))
            n += 1
        except Exception:                                 # noqa: BLE001
            log.warning("image_picker: could not notify %s about %s", email, client)
    return n


def uploads_recorded(client: str, *, count: int, by: str = "client",
                     folder: str = "") -> int:
    """Files landed in the gallery. One card per person per client per hour.

    `count` is how many this hour, which the caller counts off the gallery
    rows so the card re-registered on the fortieth file says forty.
    """
    who = "from the client" if by == "client" else "added by our team"
    where = f" into {folder}" if folder else ""
    label = (f"{count} new file{'s' if count != 1 else ''} for {client}"
             f"{where} — {who}")
    hour = time.strftime("%Y%m%d%H", time.gmtime())
    return tell(client, label=label, suffix=f"uploads-{hour}")


def optimized_all(client: str, count: int) -> int:
    """Every queued image for this client now has an SEO copy."""
    label = (f"All {count} image{'s' if count != 1 else ''} for {client} "
             f"are SEO-optimized")
    try:
        from hub import audit
        audit.log("image_picker", "optimized_all", actor="scheduler",
                  client=client, count=count)
    except Exception:                                     # noqa: BLE001
        pass
    return tell(client, label=label, suffix="optimized-" + time.strftime("%Y%m%d%H%M", time.gmtime()))
