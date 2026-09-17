"""Which keys can open a sealed value, and which one seals a new value.

## The problem this exists for

Seven modules in this repo each built their own `Fernet(key)` — `cms_credentials`,
`ghl_oauth`, `google_finder`, `skills360`, `youtube_studio`,
`check_reconciliation`, and the SEO store that reads through the first. Six of
them read the same `TOKEN_ENCRYPTION_KEY`. Every one of them takes exactly one
key and has no idea the others exist.

Which means **`TOKEN_ENCRYPTION_KEY` cannot be changed.** Rotating it — after a
leak, a staff departure, or an accidental commit, which is precisely when it
must be rotated — simultaneously locks out every client's WordPress
application password, every client's own website login, the GoHighLevel OAuth
tokens, the Google OAuth tokens behind Google Finder, and the YouTube and
Skills360 stores. The recovery is per module, per record, by hand: re-consent
every Google account, re-enter every client credential. `hub/cms_credentials.py`
says the remedy in its own error text — "Save the application password again" —
which is honest about the state and quiet about its scale.

So the key that protects everything is the key nobody can ever change, and the
error message for having rotated it is written as though it were routine.

## What a key ring changes

`MultiFernet` is the library's own answer and it is already installed, so this
is no new dependency — the standing rule in CLAUDE.md. It takes an ordered list
of keys: **the first one seals, any of them opens.** That turns a rotation from
an outage into three ordinary deploys:

1. put the new key at the front of `TOKEN_ENCRYPTION_KEYS`, keeping the old one
   behind it — everything still opens, new writes use the new key;
2. let the stores re-seal as records are saved, or sweep them;
3. drop the old key once nothing needs it.

At no point is a credential unreadable, and at no point does anybody have to
ask a client for a password again.

## The spellings, and why both are read

`TOKEN_ENCRYPTION_KEY` is what six modules already read and what Render already
has set, so it keeps working exactly as it does today and is the only key on a
deployment that sets nothing else. `TOKEN_ENCRYPTION_KEYS` — plural — is the
ring: a comma-separated list, newest first. Set both and the plural wins, with
the singular appended if it is not already in the list, because a deployment
mid-rotation should not lose the key everything is currently sealed under just
because somebody set the new variable.

`hub/config.py` owns the reading of settings, and this reads the environment
directly for the same reason `cms_credentials._fernet()` always has: this runs
where a failure must degrade to "cannot seal, and here is why" rather than to
an exception, and it is called from module import paths that must not depend on
an application context.

## What it never does

**It never raises.** A store that cannot seal must still be able to say so; an
exception here takes down the panel that would have reported the problem.

**It never returns key material.** `state()` says how many keys are configured
and whether a rotation is in progress. It does not say what they are, and no
error message carries one — the rule `hub/cms_credentials.py` already holds for
passwords, applied to the thing that protects them.

**It never reads a blob as empty when it means unreadable.** Every caller here
inherits the Google Finder lesson: a rotated key that reads as "no credential"
is what sends somebody to re-connect a thing that is connected.
"""
from __future__ import annotations

import os

SINGULAR = "TOKEN_ENCRYPTION_KEY"
PLURAL = "TOKEN_ENCRYPTION_KEYS"


def key_values() -> list[str]:
    """The configured keys, newest first, deduplicated and in order.

    The plural leads because that is the rotation list. The singular is
    appended rather than replaced: a deployment part-way through a rotation
    has the new key in the list and the old one still in the variable
    everything was sealed under, and dropping it would undo the migration this
    module exists to make safe.
    """
    out: list[str] = []
    for raw in (os.environ.get(PLURAL) or "").split(","):
        value = raw.strip()
        if value and value not in out:
            out.append(value)
    single = (os.environ.get(SINGULAR) or "").strip()
    if single and single not in out:
        out.append(single)
    return out


def _fernets():
    """(MultiFernet or None, how many keys were usable, how many were not).

    A key that is not a valid Fernet key is counted rather than raised on, so
    `state()` can say "one of the two keys configured here is not a valid
    Fernet key" instead of the deployment behaving as though none were set.
    """
    values = key_values()
    if not values:
        return None, 0, 0
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except Exception:                                       # noqa: BLE001
        return None, 0, len(values)
    good, bad = [], 0
    for value in values:
        try:
            good.append(Fernet(value.encode("utf-8")))
        except Exception:                                   # noqa: BLE001
            bad += 1
    if not good:
        return None, 0, bad
    return MultiFernet(good), len(good), bad


def available() -> bool:
    """Whether a value sealed right now would actually be encrypted."""
    ring, usable, _bad = _fernets()
    return ring is not None and usable > 0


def state() -> dict:
    """What a panel may know. Counts and conditions, never key material."""
    values = key_values()
    ring, usable, bad = _fernets()
    if not values:
        return {"configured": False, "keys": 0, "unusable": 0,
                "rotating": False,
                "note": f"{SINGULAR} is not set on this deployment, so a "
                        f"credential saved here is stored in the clear and is "
                        f"mirrored into the database backup that way. Set it "
                        f"before connecting a client's site."}
    if ring is None or not usable:
        return {"configured": False, "keys": 0, "unusable": bad,
                "rotating": False,
                "note": f"{SINGULAR} is set but is not a valid Fernet key, so "
                        f"nothing can be sealed with it. A credential saved "
                        f"now would be stored in the clear."}
    note = "Credentials are sealed with " + SINGULAR + "."
    if usable > 1:
        note = (f"Credentials are sealed with the newest of {usable} keys in "
                f"{PLURAL}; the older ones are still accepted for reading, "
                f"which is what makes a key rotation safe. Drop them once "
                f"every store has been re-sealed.")
    if bad:
        note += (f" {bad} configured key(s) are not valid Fernet keys and are "
                 f"being ignored.")
    return {"configured": True, "keys": usable, "unusable": bad,
            "rotating": usable > 1, "note": note}


def seal(value: str) -> dict:
    """{"enc": bool, "data": str} — always sealed with the NEWEST key.

    The shape is the one `hub/cms_credentials.py` already writes, so a record
    written before this module and a record written after it are the same
    bytes on disk and no migration is needed to read either.
    """
    raw = str(value or "")
    ring, usable, _bad = _fernets()
    if ring is None or not usable:
        return {"enc": False, "data": raw}
    return {"enc": True,
            "data": ring.encrypt(raw.encode("utf-8")).decode("ascii")}


def unseal(blob) -> tuple[str, str]:
    """(value, error). Opened by ANY configured key; an unreadable blob is an
    error and never an empty value.

    Reading a blob nothing can open as "there is no credential" is what sends
    somebody to re-connect something that is connected, and it hides the one
    fact that would have explained it — the failure
    `connected_accounts_result()` cost Google Finder months over.
    """
    if not isinstance(blob, dict):
        return "", "No credential is stored."
    data = str(blob.get("data") or "")
    if not blob.get("enc"):
        return data, ""
    ring, usable, _bad = _fernets()
    if ring is None or not usable:
        return "", (f"This credential is sealed and {SINGULAR} is not set on "
                    f"this deployment, so it cannot be read. Set the key it "
                    f"was saved under, or save the credential again.")
    try:
        return ring.decrypt(data.encode("ascii")).decode("utf-8"), ""
    except Exception:                                       # noqa: BLE001
        extra = ""
        if usable > 1:
            extra = (f" All {usable} keys configured in {PLURAL} were tried.")
        return "", (f"This credential cannot be decrypted with any key this "
                    f"deployment holds — the key it was sealed under has been "
                    f"rotated away.{extra} Add the old key to {PLURAL} to "
                    f"recover it, or save the credential again.")


def needs_reseal(blob) -> bool:
    """Whether this blob is readable but sealed under an older key.

    What turns a rotation from "both keys forever" into something that
    finishes: a store can ask this on read and write the value back under the
    newest key. Answers False when there is nothing to do and False when the
    blob cannot be read at all -- re-sealing is not a repair, and a caller
    must not be told to rewrite something it cannot open.
    """
    if not isinstance(blob, dict) or not blob.get("enc"):
        return False
    ring, usable, _bad = _fernets()
    if ring is None or usable < 2:
        return False
    value, err = unseal(blob)
    if err:
        return False
    try:
        from cryptography.fernet import Fernet
        newest = Fernet(key_values()[0].encode("utf-8"))
        newest.decrypt(str(blob.get("data") or "").encode("ascii"))
        return False
    except Exception:                                       # noqa: BLE001
        del value
        return True
