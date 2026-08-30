"""Find — or make — the upload gallery for one Hub client.

The picker has always been able to hand a client a link they can upload their
own photographs through (`/tools/image-picker/pick/<token>`). Getting one
meant opening the picker's admin page, finding or adding the client, and
copying the link out of a row. So the two places that actually need it — the
client's own record, and an insertion order that has just said "creative is
being supplied" — offered nothing, and the link got made by whoever
remembered the tool existed.

This is that step, done once, from anywhere. The rules are the ones
`hub/client_key.py` argues at length, because the failure they prevent is the
expensive one: a link that collects a client's photographs into **another
client's gallery** is worse than no link at all.

* **Exactly one gallery, or none.** The client is resolved through
  `client_key`, and a gallery matches on that derived key or on an exactly
  normalised name — never on a substring. "Riverside HVAC" must not collect
  "Riverside HVAC Supply".

* **Two candidates propose neither.** If more than one gallery could be meant,
  this refuses and names them. Picking the first would file a client's photos
  under whichever row the database returned first.

* **Creating is asked for, not assumed.** `create=False` answers "is there
  one?" without making one, so a page can show the state before a button is
  pressed and a page load can never create a gallery.

* **A disabled link is reported, not silently re-enabled.** Somebody switched
  sharing off for that gallery; quietly turning it back on because a different
  screen asked for a link is not this function's decision to make.

Nothing here is written to Knack, and no client record is invented: a gallery
is a Hub-side container for files, and `kind` stays `prospect` until it is
attached to a real Hub client record.
"""
from __future__ import annotations

from sqlalchemy import select

from .models import PickerClient, new_token, session, unique_slug
from . import taxonomy


def _norm(name: str) -> str:
    try:
        from hub.client_key import normalise_name
        return normalise_name(name or "")
    except Exception:                                     # noqa: BLE001
        import re
        return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _resolved_key(name: str, url: str = "") -> str:
    try:
        from hub.client_key import resolve
        return resolve(name=name or "", url=url or "").get("key") or ""
    except Exception:                                     # noqa: BLE001
        return ""


def share_url(client: PickerClient, base: str = "") -> str:
    """The absolute link a client is sent.

    Absolute because it is pasted into an email, a PDF and a Suite automation,
    none of which have an origin to resolve a path against. `base` is the
    request's own root where there is one; PUBLIC_BASE_URL is the fallback,
    since a PDF built by the scheduler has no request at all.
    """
    root = _origin(base)
    if not root:
        try:
            from hub.config import settings
            root = _origin(settings.public_base_url or "")
        except Exception:                                 # noqa: BLE001
            root = ""
    return f"{root}/tools/image-picker/pick/{client.share_token}"


def _origin(value: str) -> str:
    """scheme://host, with any path thrown away.

    Every caller hands this a different shape. A hub blueprint's
    ``request.url_root`` is the origin already; a dispatcher-mounted module's
    carries its own mount (``…/tools/io/``), and pasting the picker's path onto
    that builds ``/tools/io/tools/image-picker/…`` — a 404 the client meets and
    nobody else does. PUBLIC_BASE_URL is documented as an origin and
    hub/config.py reports a path in it as a fault, but one env group here has
    held a callback URL in that variable before now, so it is trimmed rather
    than trusted.
    """
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if "//" not in raw:
        return raw.split("/")[0]
    scheme, _, rest = raw.partition("//")
    return f"{scheme}//{rest.split('/')[0]}"


def find(db, name: str, url: str = "") -> tuple[list[PickerClient], str]:
    """(galleries that could be this client, how they matched).

    A list rather than one row on purpose — the caller has to be able to tell
    "none" from "more than one", and only the first of those is safe to act on.
    """
    wanted_key = _resolved_key(name, url)
    wanted_name = _norm(name)
    if not (wanted_key or wanted_name):
        return [], ""

    rows = db.execute(select(PickerClient)).scalars().all()

    if wanted_key:
        by_key = [c for c in rows if c.client_key() and c.client_key() == wanted_key]
        if by_key:
            return by_key, "client key"
    by_name = [c for c in rows if _norm(c.name) == wanted_name]
    if by_name:
        return by_name, "exact name"
    return [], ""


def link_for(name: str, url: str = "", *, create: bool = False,
             hub_client_id: str = "", base: str = "",
             actor: str = "") -> dict:
    """The upload link for this client, making the gallery only if asked.

    Always answers; never raises at the caller. Every outcome says which it
    is, because "there isn't one yet", "there are two and I will not guess"
    and "sharing is switched off for this one" are three different things to
    do next and they read identically as an empty string.
    """
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "No client name, so there is nothing to "
                                      "open a gallery for."}
    try:
        db = session()
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The image galleries could not be read: {exc}"}

    try:
        found, how = find(db, name, url)
        if len(found) > 1:
            return {"ok": False, "ambiguous": True,
                    "candidates": [{"id": c.id, "name": c.name} for c in found],
                    "error": ("More than one upload gallery could be this client "
                              f"({', '.join(c.name for c in found)}). Open Client "
                              "Image Uploads and pick the right one rather than "
                              "risking a client's photos landing in another "
                              "client's gallery.")}
        if found:
            client = found[0]
            # Attach the Hub client id if this gallery was made as a prospect
            # and the caller knows the record now. Never cleared, and never
            # pointed at a different client.
            if hub_client_id and not client.hub_client_id:
                client.hub_client_id = str(hub_client_id)[:64]
                client.kind = "client"
                db.commit()
            return {"ok": True, "created": False, "matched_on": how,
                    "client": client.to_dict(include_secrets=True),
                    "share_url": share_url(client, base),
                    "share_enabled": bool(client.share_enabled),
                    "note": ("This gallery's link is switched off — turn sharing "
                             "back on in Client Image Uploads before sending it."
                             if not client.share_enabled else "")}

        if not create:
            return {"ok": True, "created": False, "exists": False,
                    "share_url": "", "can_create": True,
                    "note": "No upload gallery for this client yet."}

        client = PickerClient(
            name=name[:200],
            slug=unique_slug(db, name),
            industry_key=taxonomy.guess_industry(name),
            kind="client" if hub_client_id else "prospect",
            hub_client_id=str(hub_client_id)[:64] if hub_client_id else None,
            share_token=new_token(),
        )
        db.add(client)
        db.commit()
        try:
            from hub import audit
            audit.log("image_picker", "upload_link_created", client=name,
                      actor=actor or "", gallery=client.slug)
        except Exception:                                 # noqa: BLE001
            pass
        return {"ok": True, "created": True, "matched_on": "",
                "client": client.to_dict(include_secrets=True),
                "share_url": share_url(client, base),
                "share_enabled": True,
                "note": "A new upload gallery was created for this client."}
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The upload link could not be made: {exc}"}
