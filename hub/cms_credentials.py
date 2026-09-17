"""The credential a CMS API call is made with, and why it is not the site login.

`hub/cms_publish.py` opens by saying neither CMS has a write API we can use.
That was true of Smart 1 Sites and it is not true of WordPress: core has had a
write API since 4.7 and Application Passwords since 5.6, so a client's blog
posts and image alt text can be written directly. `hub/wordpress.py` is the
half that makes the calls; this is the half that holds what they are made with.

**What the API calls are made with is an application password, never the site
login, and that is a security property rather than a preference.** A WordPress
application password is minted per integration at Users -> Profile, it cannot
be used to sign in to wp-admin interactively, and the client revokes it from
their own Users screen without changing anybody's password or telling us.
Nothing in this Hub authenticates as the human, and nothing should.

**And the human's login is here too, because it already existed and was worse
off where it was.** The paragraph below describes `setup.password` on the SEO
record -- the client's real website login -- being written to
`data/seo/<client>.json` in plain text and mirrored into every database backup
that way. That is the value this file was written *about*, and a second module
to hold it would have been a second copy of the sealing, the three states and
the one-file-per-client rule, which is the drift `hub/storage.py` exists to
stop. It is the `SITE_LOGIN` kind here: same sealing, same three answers, same
file-per-client, and deliberately **not** something any API call authenticates
with. Which of the two a caller wants is the difference between `WORDPRESS`
and `SITE_LOGIN`, and no code path reaches for the second.

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

from . import jsonstore, keyring

WORDPRESS = "wordpress"
# The client's own login to their website -- what a rep signs in with by hand,
# which is the credential `hub/cms_publish.py`'s prompts tell them to use
# before pasting anything. Held apart from WORDPRESS because they are not
# interchangeable: one authenticates an API call and the other is a person's
# password, and only the first is ever sent anywhere.
SITE_LOGIN = "site_login"
CMS_KEYS = (WORDPRESS, SITE_LOGIN)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80] or "client"


def _path(client: str, cms: str) -> str:
    return os.path.join(jsonstore.data_dir("cms_credentials"),
                        f"{_slug(client)}__{_slug(cms)}.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ------------------------------------------------------------------ sealing
# The keys themselves are `hub/keyring.py`, and this file deliberately holds
# none of the reading. It had its own `Fernet(TOKEN_ENCRYPTION_KEY)`, which was
# the sixth copy of that line in this repo -- and a single-key one, so rotating
# that variable locked out every credential here at once with no way back but
# asking each client for their password again. The key ring takes an ordered
# list: the newest seals, any of them opens, which is what makes a rotation
# three ordinary deploys instead of an outage.
#
# The stored shape is unchanged -- {"enc": bool, "data": str} -- so a record
# written before this and one written after it are the same bytes, and nothing
# had to be migrated.
def encryption_state() -> dict:
    """Whether a credential saved right now would be sealed.

    Asked before the save as well as reported after it, because "we stored
    your client's website password in plain text" is worth saying in advance
    rather than discovering in a panel afterwards. Read from the key ring so
    this panel and `/status` cannot drift into two answers.
    """
    return keyring.state()


def _seal(value: str) -> dict:
    return keyring.seal(value)


def _unseal(blob) -> tuple[str, str]:
    """(value, error). An unreadable blob is an error, never an empty value.

    Reading a rotated key as "there is no credential" is what sends somebody to
    re-connect a site that is connected, and it hides the one fact that would
    have explained it.
    """
    return keyring.unseal(blob)


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


def save_site_login(client: str, *, login: str, password: str,
                    access_method: str = "", access_url: str = "",
                    actor: str = "") -> dict:
    """Seal the client's own website login.

    Separate from `save()` because the two records are not the same shape and
    must not be able to be mistaken for one another: this one carries no REST
    root, is never handed to `hub/wordpress.py`, and nothing authenticates
    with it.

    A blank password **keeps** whatever is stored rather than clearing it --
    the setup form sends the field only when somebody typed in it, and the
    alternative is a form somebody edits for an unrelated reason quietly
    throwing away a credential.
    """
    client = str(client or "").strip()
    if not client:
        return {"error": "client is required."}
    rec = _load(client, SITE_LOGIN) or {}
    secret = str(password or "")
    if secret:
        rec["secret"] = _seal(secret)
        rec["saved_by"] = str(actor or "") or "unknown"
        rec["saved_at"] = _now()
    elif "secret" not in rec:
        rec["secret"] = {}
    rec["client"] = client
    rec["cms"] = SITE_LOGIN
    for key, value in (("login", login), ("access_method", access_method),
                       ("access_url", access_url)):
        if value is not None:
            rec[key] = str(value or "").strip()
    jsonstore.write_json(_path(client, SITE_LOGIN), rec, indent=1)
    return {"ok": True, "state": site_login_state(client)}


def site_login_state(client: str) -> dict:
    """What a screen may know about it. A subset, never the password.

    The three states are the same three, and the third one matters as much
    here: a rotated key reading as "no login on file" is what sends somebody
    to type a client's password in again when the one on file was fine.
    """
    rec = _load(client, SITE_LOGIN)
    enc = encryption_state()
    if not rec or not (rec.get("secret") or {}).get("data"):
        return {"has_password": False, "login": (rec or {}).get("login") or "",
                "sealed": False, "readable": False, "error": "",
                "saved_by": "", "saved_at": "", "encryption": enc}
    _secret, err = _unseal(rec.get("secret"))
    return {"has_password": True,
            "login": rec.get("login") or "",
            "sealed": bool((rec.get("secret") or {}).get("enc")),
            "readable": not err,
            "error": err,
            "saved_by": rec.get("saved_by") or "",
            "saved_at": rec.get("saved_at") or "",
            "encryption": enc}


def adopt_plaintext_site_login(client: str, setup: dict) -> bool:
    """Move a plaintext `setup.password` into the sealed store. Once.

    The SEO store held the client's real website login in the clear, and
    because that store goes through `hub/jsonstore.py` it was mirrored into
    Postgres and into every database backup that way -- the whole book of them.

    This is the `hub/ad_assets.py` migration shape and its warning: the old
    value is read only while the new store is empty, and the caller deletes it
    from the SEO record in the same save, so this runs exactly once per client
    and never again. It returns whether there was anything to move, so the
    caller knows whether it has a write to make -- a migration that rewrites
    the source on every read is the defect that file records paying for.

    **It refuses to run on a deployment that cannot seal.** Moving a plaintext
    password out of `data/seo/<client>.json` and into
    `data/cms_credentials/<client>__site_login.json` changes which file in the
    backup it is in and nothing else -- `_seal()` falls back to storing the raw
    value when there is no key, by design, so that a save can still say so.
    Migrating under that fallback would spend the client's one plaintext copy
    on no improvement and leave the SEO panel reading from a store that cannot
    answer. So an unset or invalid `TOKEN_ENCRYPTION_KEY` leaves the record
    exactly where it is, and `/status` keeps saying why.

    Never raises. A seal that cannot happen must not cost somebody the page
    they were opening.
    """
    try:
        legacy = str((setup or {}).get("password") or "")
        if not legacy:
            return False
        rec = _load(client, SITE_LOGIN) or {}
        if not encryption_state().get("configured") and not (
                rec.get("secret") or {}).get("data"):
            return False
        if (rec.get("secret") or {}).get("data"):
            # Already sealed; the plaintext is a leftover for the caller to
            # drop rather than something to copy over a newer value.
            return True
        save_site_login(client, login=(setup or {}).get("login") or "",
                        password=legacy,
                        access_method=(setup or {}).get("access_method") or "",
                        access_url=(setup or {}).get("access_url") or "",
                        actor="migrated from the SEO record")
        return True
    except Exception:                                       # noqa: BLE001
        return False


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
