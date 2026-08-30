"""Who is looking, when the page is inside a client's own Smart 1 Suite.

`hub/suite_embed.py` solved the *staff* case: a rep already has a Hub session
in that browser, so a companion cookie limited to safe methods and an
allowlist lets their own pages survive being framed. Its closing note says why
the client case cannot use any of that, and this is that half.

A client has no Hub account and must never be given one. HighLevel's answer is
an SSO handshake: the framed page posts `REQUEST_USER_DATA` to its parent,
HighLevel replies with a payload encrypted under the app's SSO key, and the
app decrypts it server-side to learn the HighLevel user and the location they
are in.

## The location id is the authorization, and that is the whole security model

Everything else in the payload is context. The location id is the only thing
in it that says *whose data this person may see*, so it is the only thing
identity is derived from — resolved to a client the way `hub/client_key.py`
resolves one, and never, ever from a name in a query string. Getting that
wrong shows one client another client's record, which is the worst outcome any
tool in this Hub can produce. Four consequences, each stated rather than left
to be discovered:

**No key means no session, never a lenient one.** An unset `GHL_SSO_KEY`
resolves to `not_configured` and the caller must refuse. The tempting failure
here is to treat an unverifiable frame as trusted because it *looks* like it
came from HighLevel; a frame is a URL anybody can point at us.

**A payload that will not decrypt is not a payload.** Wrong key, tampering and
truncation all land on `unreadable`. None of them is a reason to fall through
to anything.

**One location resolves to exactly one client, or to none.** Two client rows
recording the same sub-account is `ambiguous`, named and refused — picking
between them is picking whose data a stranger sees. A location we hold no
record for is `unknown_location`, which is a setup gap and says so.

**Nothing the user claims about themselves is identity.** `userId`, `email`,
`role` and `type` are carried for the audit line and are never joined on. A
payload is decrypted with our own key, so its *contents* are trustworthy —
but the client it grants access to is the location's, not the user's.

## The crypto

HighLevel encrypts the payload with CryptoJS's `AES.encrypt(json, ssoKey)`,
which is OpenSSL's "Salted__" envelope: `Salted__` + an 8-byte salt +
AES-256-CBC ciphertext, with the key and IV derived by EVP_BytesToKey over
MD5. That is transcribed here rather than pulled from a library, the rule
`hub/creative_specs.py` works to — and `test_suite_sso.py` round-trips it
against its own encryptor, so the format is exercised even though nothing here
has ever seen a live HighLevel payload.

MD5 appears only inside that key derivation, where the format specifies it. It
is not used as a digest of anything.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os

__all__ = ["configured", "why_not", "identify", "decrypt", "SsoError",
           "STATES", "KEY_NAMES"]

# The SSO key is a *third* credential, separate from GHL_CLIENT_ID and
# GHL_CLIENT_SECRET, issued on the marketplace app's own settings page. Both
# spellings are read, because this Hub has been bitten by a key that was set
# under the name the code did not ask for — see hub/config.py's ALIASES.
KEY_NAMES = ("GHL_SSO_KEY", "GHL_APP_SSO_KEY")

STATES = ("ok", "not_configured", "unreadable", "no_location",
          "unknown_location", "ambiguous")

_PLACEHOLDERS = {"", "changeme", "your-sso-key", "sso-key", "todo"}


class SsoError(Exception):
    """A payload that could not be turned into a session."""


# ------------------------------------------------------------------ the key
def sso_key() -> str:
    for name in KEY_NAMES:
        value = str(os.environ.get(name) or "").strip()
        if value and value.lower() not in _PLACEHOLDERS:
            return value
    return ""


def configured() -> bool:
    return bool(sso_key())


def why_not() -> str:
    """What is missing, named where somebody can act on it.

    A client-facing embed that is simply absent reads as a broken integration
    and gets reported as one; naming the variable turns it into a one-line fix
    by whoever configured the app — the same argument `suite_embed.refuse()`
    makes about a blank frame.
    """
    if configured():
        return ""
    return (f"{KEY_NAMES[0]} is not set, so a page framed inside a client's "
            "Smart 1 Suite cannot prove who is looking at it. It is issued on "
            "the marketplace app's own settings page and is a different "
            "credential from GHL_CLIENT_ID and GHL_CLIENT_SECRET.")


# ------------------------------------------------------------------ crypto
def _evp_bytes_to_key(password: bytes, salt: bytes, length: int = 48) -> bytes:
    """OpenSSL's EVP_BytesToKey with MD5 and one iteration.

    What CryptoJS does by default, and therefore what HighLevel's payload is
    built with. Transcribed rather than depended on: the alternative is a
    third-party package for eleven lines of well-specified derivation.
    """
    out = b""
    block = b""
    while len(out) < length:
        block = hashlib.md5(block + password + salt).digest()   # noqa: S324
        out += block
    return out[:length]


def decrypt(payload: str, key: str = "") -> dict:
    """The SSO payload as a dict. Raises SsoError for anything else.

    Every failure is one exception type on purpose: the caller must not be
    able to tell a wrong key from a truncated payload from a tampered one,
    because each of those answers, handed back to a frame, tells whoever is
    probing which of their guesses was closer.
    """
    key = key or sso_key()
    if not key:
        raise SsoError("No SSO key is configured.")
    raw = str(payload or "").strip()
    if not raw:
        raise SsoError("No payload.")
    # A payload big enough to be a denial of service is refused before any
    # work is done on it: this route is reachable by anybody who can point a
    # frame at us.
    if len(raw) > 64_000:
        raise SsoError("Payload too large.")
    try:
        blob = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SsoError("Payload is not base64.") from exc
    if len(blob) < 32 or blob[:8] != b"Salted__":
        raise SsoError("Payload is not in the expected envelope.")

    derived = _evp_bytes_to_key(key.encode("utf-8"), blob[8:16])
    try:
        from cryptography.hazmat.primitives.ciphers import (Cipher, algorithms,
                                                            modes)
        decryptor = Cipher(algorithms.AES(derived[:32]),
                           modes.CBC(derived[32:48])).decryptor()
        plain = decryptor.update(blob[16:]) + decryptor.finalize()
    except Exception as exc:                                # noqa: BLE001
        raise SsoError("Payload would not decrypt.") from exc

    # Everything from here on is decided by bytes the key produced, so every
    # refusal below says the same words. The two above it -- not base64, not
    # the envelope -- stay distinct on purpose: anybody can tell those about
    # their own payload without holding a key, so they give nothing away.
    #
    # This is the "almost" that the padding branch used to have written on it.
    # A wrong key *almost* always fails the padding check -- but AES-CBC under
    # the wrong key produces garbage, and garbage ends in bytes that are valid
    # PKCS#7 padding about 0.6% of the time. Those fell through to json.loads
    # and answered "Payload is not JSON." instead, which is exactly the finer
    # answer this function exists not to give: it tells a prober their guess
    # produced valid padding. Measured against this module, 4,000 wrong keys
    # gave 3,975 of one message and 25 of the other. Weak as an oracle against
    # a high-entropy key, and still the distinction we refuse to draw -- and
    # it made test_suite_sso.py fail roughly one CI run in 160, which is how a
    # real finding comes to read as a flake somebody re-runs.
    #
    # The cause is not lost: each `from exc` keeps it on the exception chain,
    # so a genuine HighLevel integration fault is still diagnosable from a
    # traceback. It is the *answer handed back to the frame* that is one word.
    if not plain:
        raise SsoError("Payload would not decrypt.")
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise SsoError("Payload would not decrypt.")
    try:
        data = json.loads(plain[:-pad].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SsoError("Payload would not decrypt.") from exc
    if not isinstance(data, dict):
        raise SsoError("Payload would not decrypt.")
    return data


# ------------------------------------------------------------------ identity
def _text(value, limit: int = 200) -> str:
    return str(value if value is not None else "").strip()[:limit]


def location_from(data: dict) -> str:
    """The sub-account this person is looking at.

    HighLevel has published this under more than one name across versions of
    the handshake, so all of them are read — and a payload carrying none is
    `no_location` rather than being quietly granted the first client we find.
    """
    for key in ("activeLocation", "locationId", "location_id", "location"):
        found = _text(data.get(key), 120)
        if found:
            return found
    return ""


def _answer(state: str, detail: str, **extra) -> dict:
    out = {"state": state, "detail": detail, "ok": state == "ok",
           "client": "", "client_url": "", "location_id": "", "user": {}}
    out.update(extra)
    return out


def identify(payload: str, key: str = "") -> dict:
    """Turn an SSO payload into "which client may this person see".

    Never raises. Every refusal names its own kind, because *not configured*,
    *could not read that*, *no sub-account in the payload* and *no client
    recorded for this sub-account* send four different people to four
    different places, and exactly one of them is a Hub setup gap.
    """
    if not configured() and not key:
        return _answer("not_configured", why_not())
    try:
        data = decrypt(payload, key)
    except SsoError as exc:
        return _answer("unreadable", str(exc))
    except Exception as exc:                                # noqa: BLE001
        return _answer("unreadable", f"Payload could not be read "
                                     f"({type(exc).__name__}).")

    location = location_from(data)
    # Carried for the audit line only. Never joined on: see the header.
    user = {"id": _text(data.get("userId"), 60),
            "email": _text(data.get("email"), 200),
            "name": _text(data.get("userName") or data.get("name"), 120),
            "role": _text(data.get("role"), 40),
            "type": _text(data.get("type"), 40),
            "company_id": _text(data.get("companyId"), 60)}

    if not location:
        return _answer("no_location",
                       "Smart 1 Suite did not say which sub-account this page "
                       "is being viewed in, so there is nothing to authorize "
                       "against.", user=user)

    try:
        from hub.suite_accounts import client_for_location
        found = client_for_location(location)
    except Exception as exc:                                # noqa: BLE001
        return _answer("unknown_location",
                       f"The sub-account mapping could not be read "
                       f"({type(exc).__name__}).",
                       location_id=location, user=user)

    if found["state"] == "ambiguous":
        return _answer("ambiguous", found["detail"],
                       location_id=location, user=user,
                       candidates=found.get("candidates", []))
    if found["state"] != "connected":
        return _answer("unknown_location", found["detail"],
                       location_id=location, user=user)

    return _answer("ok", "", client=found["client"],
                   client_url=found.get("client_url", ""),
                   location_id=location, user=user)
