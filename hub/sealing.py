"""Sealing a secret with `TOKEN_ENCRYPTION_KEY`, in one place.

Three files in this repo had already written this: `hub/cms_credentials.py`,
`hub/ghl_oauth.py` and `modules/check_reconciliation/app.py`. That is the drift
`hub/jsonstore.py`, `hub/storage.py` and `hub/images.py` exist to stop, and the
moment a fourth caller wanted it the choice was to copy it again or to share
it. This is the shared one; `hub/cms_credentials.py` is a thin binding over it
now and `test_wordpress_publish.py`'s 102 checks pass unaltered.

**Why it matters more than an ordinary helper.** A store that seals is
protecting something from the *database backup*, not from an attacker at the
door: everything written through `hub/jsonstore.py` is mirrored into Postgres
verbatim, so a plaintext secret in a JSON store is a plaintext secret in every
backup ever taken of it. Sealing on the way in is what keeps it out of the
next one. It does nothing whatever about the ones already taken, and no caller
here may imply otherwise.

**Three states, kept apart.** This is the part that is easy to collapse and
expensive to get wrong:

* **sealed** — the ordinary case;
* **stored in the clear** — no usable key was configured when it was saved,
  which is said out loud rather than passing as encrypted;
* **cannot be decrypted** — the key has been rotated since. That is never
  reported as "there is no secret". `connected_accounts_result()` in Google
  Finder is the precedent: a rotated key reading as an empty book cost that
  module months of silent failure. It is a state with a fix, and the fix is
  naming the variable.

**Nothing here raises.** A store that cannot seal must still be able to say so,
and raising would take the panel down with the encryption rather than reporting
on it.
"""
from __future__ import annotations

import os

ENV_KEY = "TOKEN_ENCRYPTION_KEY"


def _fernet(env_key: str = ENV_KEY):
    """The configured key as a Fernet, or None. Never raises."""
    key = (os.environ.get(env_key) or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode("utf-8"))
    except Exception:                                       # noqa: BLE001
        return None


def available(env_key: str = ENV_KEY) -> bool:
    """Whether a value sealed right now would actually be sealed."""
    return _fernet(env_key) is not None


def encryption_state(env_key: str = ENV_KEY) -> dict:
    """Whether a secret saved right now would be sealed, and in what words.

    Asked *before* the save as well as reported after it, because "we stored
    your client's website password in plain text" is worth saying in advance
    rather than discovering in a panel afterwards.
    """
    key = (os.environ.get(env_key) or "").strip()
    if not key:
        return {"configured": False,
                "note": f"{env_key} is not set on this deployment, so a secret "
                        f"saved here is stored in the clear and is mirrored "
                        f"into the database backup that way. Set it before "
                        f"saving a client's credential."}
    if _fernet(env_key) is None:
        return {"configured": False,
                "note": f"{env_key} is set but is not a valid Fernet key, so "
                        f"nothing can be sealed with it. A secret saved now "
                        f"would be stored in the clear."}
    return {"configured": True,
            "note": f"Secrets are sealed with {env_key}."}


def seal(value: str, env_key: str = ENV_KEY) -> dict:
    """A stored shape that says whether it is sealed.

    The `enc` flag travels *with* the value rather than being inferred on read.
    Inferring it means guessing from the ciphertext's shape, and a guess that
    is wrong in the safe-looking direction reports a plaintext password as
    encrypted.
    """
    raw = str(value or "")
    f = _fernet(env_key)
    if f is None:
        return {"enc": False, "data": raw}
    return {"enc": True, "data": f.encrypt(raw.encode("utf-8")).decode("ascii")}


def unseal(blob, env_key: str = ENV_KEY) -> tuple[str, str]:
    """``(value, error)``. An unreadable blob is an error, never an empty value.

    Reading a rotated key as "there is no secret" is what sends somebody to
    re-enter a credential that is already there, and it hides the one fact that
    would have explained it.

    A bare string is accepted and returned as-is, because every caller of this
    is a store that held plaintext before it held a sealed blob, and a
    migration that cannot read its own history is a migration that loses it.
    """
    if isinstance(blob, str):
        return blob, ""
    if not isinstance(blob, dict):
        return "", "No secret is stored."
    data = str(blob.get("data") or "")
    if not blob.get("enc"):
        return data, ""
    f = _fernet(env_key)
    if f is None:
        return "", (f"This secret is sealed and {env_key} is not set on this "
                    f"deployment, so it cannot be read. Set the key it was "
                    f"saved under, or save the credential again.")
    try:
        return f.decrypt(data.encode("ascii")).decode("utf-8"), ""
    except Exception:                                       # noqa: BLE001
        return "", (f"This secret cannot be decrypted with the current "
                    f"{env_key} — the key has been rotated since it was saved. "
                    f"Save the credential again.")


def is_sealed(blob) -> bool:
    """Whether this stored shape is actually ciphertext.

    Deliberately not "is it a dict": a `{"enc": False}` blob is the shape
    without the protection, which is the case a panel most needs to tell apart
    from the real one.
    """
    return isinstance(blob, dict) and bool(blob.get("enc"))


def is_plain(blob) -> bool:
    """Whether something is stored here and is NOT protected.

    Two shapes mean this, and a check that knows only one of them is worse
    than none: the **legacy bare string**, written before this store sealed
    anything, and ``{"enc": False, "data": ...}``, which is what `seal()`
    returns when no usable key is configured. The second is the one a live
    deployment actually produces when the key is missing, so a scan that
    looked only for a bare string would report a clean bill on precisely the
    deployment that has the problem.
    """
    if isinstance(blob, str):
        return bool(blob)
    return (isinstance(blob, dict) and bool(blob.get("data"))
            and not blob.get("enc"))


def has_secret(blob) -> bool:
    """Whether anything is stored, readable or not.

    Separate from `unseal()` on purpose. A screen answering "is there a
    password on file?" must say yes for one sealed under a rotated key —
    answering no would be the Google Finder failure, in a placeholder.
    """
    if isinstance(blob, str):
        return bool(blob)
    return isinstance(blob, dict) and bool(blob.get("data"))
