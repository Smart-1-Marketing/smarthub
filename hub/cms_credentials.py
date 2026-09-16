"""The credential a CMS API call is made with, and why it is not the site login.

`hub/cms_publish.py` opens by saying neither CMS has a write API we can use.
That was true of Smart 1 Sites and it is not true of WordPress: core has had a
write API since 4.7 and Application Passwords since 5.6, so a client's blog
posts and image alt text can be written directly. `hub/wordpress.py` is the
half that makes the calls; this is the half that holds what they are made with.

**It is an application password, never the site login, and that is a security
property rather than a preference.** A WordPress application password is minted
per integration at Users -> Profile, it cannot be used to sign in to wp-admin
interactively, and the client revokes it from their own Users screen without
changing anybody's password or telling us. Storing the human's real login would
give this Hub a book of credentials that open every one of those sites to
anything, including the parts of them this tool has no business in.

**It is sealed, and the state it was found in is one of three.** The Hub has
had `TOKEN_ENCRYPTION_KEY` and Fernet since Google Finder -- `modules/skills360`
is the closest relative of this file -- and the reason that key exists is
exactly this: the SEO store's own `setup.password` is written to
`data/seo/<client>.json` in plain text and, because that goes through
`hub/jsonstore.py`, mirrored verbatim into Postgres and into every database
backup. What this file holds is a credential that can write to a client's live
website, so it is sealed on the way in and the three answers are kept apart:

* **sealed and readable** -- the ordinary case;
* **stored in the clear** -- no key was configured when it was saved, which is
  said out loud on the panel rather than passing as encrypted;
* **cannot be decrypted** -- the key was rotated since. That is never reported
  as "no credential": `connected_accounts_result()` in Google Finder is the
  precedent, and a rotated key reading as an empty book cost that module
  months of silent failure. It is a state with a fix, and the fix is naming
  the variable.

**Nothing returns the secret.** `state()` is what every route answers with --
whether there is one, who saved it, when, what the last probe found -- and
`get()` is reached only from `hub/wordpress.py`, on its way into an
Authorization header. No error message here carries a password, and no prompt
does either: `hub/cms_publish.py`'s own rule, which `test_alt_text.py` already
asserts over the eight Claude prompts.

**One file per client per CMS.** The `hub/drafts.py` rule -- a store keeping
many records in one file changes one of them by writing all of them back, and
two reps connecting two clients at the same moment would each drop the other's
work. Keyed on the client's **name** the way `hub/seo.py` keys its own store,
never on the derived client key, for the reason `hub/client_key.py` gives at
length.
"""
from __future__ import annotations

import os
import re
import time

from . import jsonstore

WORDPRESS = "wordpress"
CMS_KEYS = (WORDPRESS,)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80] or "client"


def _path(client: str, cms: str) -> str:
    return os.path.join(jsonstore.data_dir("cms_credentials"),
                        f"{_slug(client)}__{_slug(cms)}.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ sealing
def _fernet():
    """The shared key, or None. Never raises -- a store that cannot seal must
    still be able to say so, and raising here would take the panel down with
    the encryption rather than reporting on it."""
    key = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return None


def encryption_state() -> dict:
    """Whether a credential saved right now would be sealed.

    Asked before the save as well as reported after it, because "we stored
    your client's website password in plain text" is worth saying in advance
    rather than discovering in a panel afterwards.
    """
    key = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip()
    if not key:
        return {"configured": False,
                "note": "TOKEN_ENCRYPTION_KEY is not set on this deployment, "
                        "so a credential saved here is stored in the clear and "
                        "is mirrored into the database backup that way. Set it "
                        "before connecting a client's site."}
    if _fernet() is None:
        return {"configured": False,
                "note": "TOKEN_ENCRYPTION_KEY is set but is not a valid Fernet "
                        "key, so nothing can be sealed with it. A credential "
                        "saved now would be stored in the clear."}
    return {"configured": True,
            "note": "Credentials are sealed with TOKEN_ENCRYPTION_KEY."}


def _seal(value: str) -> dict:
    raw = str(value or "")
    f = _fernet()
    if f is None:
        return {"enc": False, "data": raw}
    return {"enc": True, "data": f.encrypt(raw.encode("utf-8")).decode("ascii")}


def _unseal(blob) -> tuple[str, str]:
    """(value, error). An unreadable blob is an error, never an empty value.

    Reading a rotated key as "there is no credential" is what sends somebody to
    re-connect a site that is connected, and it hides the one fact that would
    have explained it.
    """
    if not isinstance(blob, dict):
        return "", "No credential is stored."
    data = str(blob.get("data") or "")
    if not blob.get("enc"):
        return data, ""
    f = _fernet()
    if f is None:
        return "", ("This credential is sealed and TOKEN_ENCRYPTION_KEY is not "
                    "set on this deployment, so it cannot be read. Set the key "
                    "it was saved under, or save the application password again.")
    try:
        return f.decrypt(data.encode("ascii")).decode("utf-8"), ""
    except Exception:                                       # noqa: BLE001
        return "", ("This credential cannot be decrypted with the current "
                    "TOKEN_ENCRYPTION_KEY — the key has been rotated since it "
                    "was saved. Save the application password again.")


# ------------------------------------------------------------------- store
def _load(client: str, cms: str) -> dict:
    return jsonstore.read_json(_path(client, cms), default={}) or {}


def normalise_app_password(value: str) -> str:
    """WordPress prints an application password in groups of four.

    It is generated as `abcd EFGH ijkl MNOP qrst UVWX` and shown that way, so
    that is how it is pasted -- and WordPress itself strips the spaces before
    comparing. Stripping them here means a paste with the spaces and a paste
    without produce the same stored secret, rather than one of the two failing
    authentication for a reason nobody can see on screen.
    """
    return re.sub(r"\s+", "", str(value or ""))


def save(client: str, cms: str, *, rest_root: str, username: str,
         app_password: str, actor: str = "") -> dict:
    """Store one credential, sealed if this deployment can seal it."""
    client = str(client or "").strip()
    if not client:
        return {"error": "client is required."}
    if cms not in CMS_KEYS:
        return {"error": f"Unknown CMS '{cms}'."}
    secret = normalise_app_password(app_password)
    if not secret:
        return {"error": "An application password is required."}
    if not str(username or "").strip():
        return {"error": "A WordPress username is required."}
    if not str(rest_root or "").strip():
        return {"error": "The REST root is required — run the connection "
                         "check first so it is discovered rather than typed."}
    sealed = _seal(secret)
    record = {
        "client": client, "cms": cms,
        "rest_root": str(rest_root).strip(),
        "username": str(username).strip(),
        "secret": sealed,
        "saved_by": str(actor or "") or "unknown",
        "saved_at": _now(),
        # A probe belongs to the credential that produced it: a new password
        # for a different user answers different questions about what it may
        # do, and carrying the old answer forward would report a capability
        # this credential has not been shown to have.
        "probe": {},
    }
    jsonstore.write_json(_path(client, cms), record, indent=1)
    return {"ok": True, "sealed": bool(sealed.get("enc")),
            "state": state(client, cms)}


def get(client: str, cms: str = WORDPRESS) -> dict:
    """The credential itself. Reached from `hub/wordpress.py` and nowhere else.

    Answers `{"error": ...}` rather than raising, and never an empty password
    where the real answer is that it could not be read.
    """
    rec = _load(client, cms)
    if not rec:
        return {"error": "No WordPress connection is saved for this client."}
    secret, err = _unseal(rec.get("secret"))
    if err:
        return {"error": err}
    return {"rest_root": rec.get("rest_root") or "",
            "username": rec.get("username") or "",
            "app_password": secret,
            "saved_by": rec.get("saved_by") or "",
            "saved_at": rec.get("saved_at") or ""}


def state(client: str, cms: str = WORDPRESS) -> dict:
    """Everything a screen may know. Deliberately built as a subset rather
    than left to a template to omit the secret: a subset a renderer merely
    happens to leave out is one the next renderer prints."""
    rec = _load(client, cms)
    enc = encryption_state()
    if not rec:
        return {"connected": False, "cms": cms, "rest_root": "", "username": "",
                "saved_by": "", "saved_at": "", "sealed": False,
                "readable": False, "error": "", "probe": {},
                "encryption": enc}
    _secret, err = _unseal(rec.get("secret"))
    return {"connected": True, "cms": cms,
            "rest_root": rec.get("rest_root") or "",
            "username": rec.get("username") or "",
            "saved_by": rec.get("saved_by") or "",
            "saved_at": rec.get("saved_at") or "",
            "sealed": bool((rec.get("secret") or {}).get("enc")),
            "readable": not err,
            "error": err,
            "probe": dict(rec.get("probe") or {}),
            "encryption": enc}


def record_probe(client: str, cms: str, probe: dict) -> dict:
    """Keep what the connection check found, so a page need not re-ask.

    The probe is one authenticated round trip, and the questions it answers --
    which user this is, what they may do, which SEO plugin is installed --
    change when somebody edits the site rather than when somebody opens a
    page. It is stored and re-taken on a button, the rule
    `hub/brand_lookup.py` arrived at for a call that costs something.
    """
    path = _path(client, cms)

    def mutate(rec):
        if not rec:
            return None
        rec["probe"] = dict(probe or {})
        rec["probe"]["at"] = _now()
        return rec

    jsonstore.update_json(path, mutate, default={}, indent=1)
    return state(client, cms)


def forget(client: str, cms: str = WORDPRESS) -> dict:
    """Remove the credential. Through jsonstore, never os.remove.

    Removing only the file leaves the mirrored copy to be restored by the next
    read, so the disconnect appears to work and then undoes itself -- which on
    a credential store is the worst possible version of that bug.
    """
    if not _load(client, cms):
        return {"ok": True, "removed": False,
                "note": "There was no connection to remove."}
    jsonstore.delete_json(_path(client, cms))
    return {"ok": True, "removed": True,
            "note": "Removed here. The application password is still live in "
                    "WordPress until somebody revokes it under Users -> "
                    "Profile -> Application Passwords."}
