"""hub/suite_sso.py — who is looking, when the page is inside a client's Suite.

    python3 test_suite_sso.py

`hub/suite_embed.py` solved the staff case with a companion cookie. Its closing
note says the client case cannot use any of it: a client has no Hub account and
must never be given one, so identity has to come from HighLevel's SSO
handshake instead.

## Why this file is worth more than most

The location id in that payload **is** the authorization. Every refusal below
is a way to show one client another client's record, which is the worst
outcome any tool in this Hub can produce — and every one of them is silent:
the frame renders, the page looks right, and it is the wrong client's data.

So the fixtures encrypt real payloads with a real key and the assertions are
about what is refused, not only about what works:

  * a payload we cannot decrypt is never a session
  * a missing SSO key is never a lenient session
  * a payload with no sub-account in it never falls through to a client
  * a sub-account two clients claim resolves to neither
  * nothing the user says about themselves is ever joined on

`test_suite_embed.py` covers the staff half; this is only the client half.
"""
import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-sso-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["SECRET_KEY"] = "sso-test"
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("GHL_SSO_KEY", None)
os.environ.pop("GHL_APP_SSO_KEY", None)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


from hub import suite_sso as sso                                  # noqa: E402

KEY = "a-real-looking-sso-key-0123456789"
LOCATION = "loc_ABC123xyz"


def encrypt(data: dict, key: str = KEY) -> str:
    """CryptoJS AES.encrypt, from the other end.

    Written here rather than imported so the format is genuinely exercised: if
    hub/suite_sso.py's derivation drifts from OpenSSL's "Salted__" envelope,
    this stops round-tripping. Nothing in this repo has ever seen a live
    HighLevel payload, so a round trip against an independent implementation
    of the same spec is the strongest check available.
    """
    from cryptography.hazmat.primitives.ciphers import (Cipher, algorithms,
                                                        modes)
    salt = os.urandom(8)
    out, block = b"", b""
    while len(out) < 48:
        block = hashlib.md5(block + key.encode() + salt).digest()  # noqa: S324
        out += block
    plain = json.dumps(data).encode("utf-8")
    pad = 16 - (len(plain) % 16)
    plain += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(out[:32]), modes.CBC(out[32:48])).encryptor()
    return base64.b64encode(b"Salted__" + salt + enc.update(plain)
                            + enc.finalize()).decode()


PAYLOAD = {"userId": "u_1", "companyId": "co_1", "role": "admin",
           "type": "location", "userName": "Dana Reyes",
           "email": "dana@northgatedental.com", "activeLocation": LOCATION}


# ---------------------------------------------------------------------------
print("\nWith no SSO key there is no session, not a lenient one")
# ---------------------------------------------------------------------------
check("the key reads as unset", sso.configured() is False)
check("and why_not names the variable somebody sets",
      "GHL_SSO_KEY" in sso.why_not(), sso.why_not())
check("and says it is a different credential from the OAuth pair",
      "GHL_CLIENT_ID" in sso.why_not())

blank = sso.identify(encrypt(PAYLOAD))
check("a perfectly valid payload is still refused with no key configured",
      blank["state"] == "not_configured", blank)
check("and it grants no client", blank["client"] == "" and blank["ok"] is False)

os.environ["GHL_SSO_KEY"] = KEY
check("the key is picked up once set", sso.configured() is True)
check("the second spelling is read too",
      (os.environ.pop("GHL_SSO_KEY"),
       os.environ.__setitem__("GHL_APP_SSO_KEY", KEY),
       sso.configured())[2] is True)
os.environ.pop("GHL_APP_SSO_KEY", None)
os.environ["GHL_SSO_KEY"] = KEY
check("a placeholder value does not count as configured",
      (os.environ.__setitem__("GHL_SSO_KEY", "changeme"),
       sso.configured())[1] is False)
os.environ["GHL_SSO_KEY"] = KEY


# ---------------------------------------------------------------------------
print("\nThe envelope round-trips against an independent implementation")
# ---------------------------------------------------------------------------
got = sso.decrypt(encrypt(PAYLOAD))
check("a payload encrypted the way CryptoJS does it comes back",
      got.get("userId") == "u_1" and got.get("activeLocation") == LOCATION, got)
check("a long payload round-trips too (multi-block, padding boundary)",
      sso.decrypt(encrypt({**PAYLOAD, "pad": "x" * 500}))["pad"] == "x" * 500)
check("and one whose plaintext is an exact block multiple",
      "activeLocation" in sso.decrypt(encrypt({"activeLocation": "a" * 13})))


def refused(payload, key="") -> str:
    try:
        sso.decrypt(payload, key)
        return ""
    except sso.SsoError as exc:
        return str(exc)


check("a payload encrypted with somebody else's key is refused",
      refused(encrypt(PAYLOAD, "a-different-key-entirely")))
check("a tampered payload is refused",
      refused(encrypt(PAYLOAD)[:-8] + "AAAAAAAA"))
check("something that is not base64 is refused", refused("not base64 at all!"))
check("something that is not the envelope is refused",
      refused(base64.b64encode(b"just some bytes here, no salt").decode()))
check("an empty payload is refused", refused(""))
check("an oversized payload is refused before any work is done on it",
      refused("A" * 70_000) == "Payload too large.")

# The refusals must not distinguish themselves to whoever is probing.
wrong_key = refused(encrypt(PAYLOAD, "another-wrong-key-here"))
tampered = refused(encrypt(PAYLOAD)[:-8] + "BBBBBBBB")
check("a wrong key and a tampered payload give the same answer",
      wrong_key == tampered, (wrong_key, tampered))

# And they must give it *every* time, which one trial cannot say. A wrong key
# produces garbage, and garbage ends in valid PKCS#7 padding about 0.6% of the
# time -- so the single check above passed 199 runs in 200 while the property
# it asserts was broken, and read as a flake on the run it caught. Six hundred
# distinct wrong keys puts the odds of missing it below one in 10^15, and the
# whole sweep costs about a tenth of a second.
#
# The failure message names the count rather than only the odd answer: "3 of
# 600" and "600 of 600" are a leak and a broken decrypt, and they are fixed in
# different places.
_answers: dict[str, int] = {}
for _i in range(600):
    _said = refused(encrypt(PAYLOAD, f"wrong-key-{_i}"))
    _answers[_said] = _answers.get(_said, 0) + 1
check("every wrong key gives that same answer, not almost every one",
      len(_answers) == 1,
      ", ".join(f"{n} of 600: {m!r}" for m, n in sorted(_answers.items(),
                                                        key=lambda kv: -kv[1])))
check("and it is the one a tampered payload gives",
      set(_answers) == {tampered}, (set(_answers), tampered))


# ---------------------------------------------------------------------------
print("\nThe location is the authorization, and nothing else is")
# ---------------------------------------------------------------------------
check("activeLocation is read", sso.location_from(PAYLOAD) == LOCATION)
for alt in ("locationId", "location_id", "location"):
    check(f"  so is {alt}, which the handshake has used",
          sso.location_from({alt: LOCATION}) == LOCATION)
check("a payload naming no sub-account yields nothing",
      sso.location_from({"userId": "u_1", "email": "x@y.com"}) == "")

nowhere = sso.identify(encrypt({"userId": "u_1", "email": "x@y.com"}))
check("and it is refused rather than falling through to a client",
      nowhere["state"] == "no_location" and nowhere["client"] == "", nowhere)
check("its reason says there is nothing to authorize against",
      "authorize" in nowhere["detail"], nowhere["detail"])

junk = sso.identify("not a payload")
check("an unreadable payload is its own state", junk["state"] == "unreadable")
check("and grants nothing", junk["client"] == "" and junk["ok"] is False)

unknown = sso.identify(encrypt(PAYLOAD))
check("a sub-account no client records is refused by name",
      unknown["state"] == "unknown_location", unknown)
check("and it reads as a setup gap, not as an empty client record",
      "Client Image Uploads" in unknown["detail"]
      or "could not be read" in unknown["detail"], unknown["detail"])
check("the location it could not place is carried, for whoever fixes it",
      unknown["location_id"] == LOCATION)


# ---------------------------------------------------------------------------
print("\nOne sub-account resolves to one client, or to none")
# ---------------------------------------------------------------------------
from hub import suite_accounts                                    # noqa: E402


class _Row:
    def __init__(self, name, loc, site=""):
        self.name, self.ghl_location_id, self.website = name, loc, site
        self.url = site


class _Query:
    rows: list = []

    @classmethod
    def all(cls):
        return list(cls.rows)


class _Client:
    query = _Query


import types                                                      # noqa: E402
fake = types.ModuleType("modules.image_picker.models")
fake.PickerClient = _Client
sys.modules["modules.image_picker.models"] = fake

_Query.rows = [_Row("Northgate Dental", LOCATION, "northgatedental.com"),
               _Row("Somebody Else", "loc_OTHER")]
found = suite_accounts.client_for_location(LOCATION)
check("the right client is resolved", found["client"] == "Northgate Dental", found)
check("carrying their website, so downstream can join on domain",
      found["client_url"] == "northgatedental.com")

session = sso.identify(encrypt(PAYLOAD))
check("and the whole handshake lands on that client",
      session["ok"] and session["client"] == "Northgate Dental", session)
check("the user is carried for the audit line",
      session["user"]["email"] == "dana@northgatedental.com")
check("but the client came from the location, not from the user's email",
      session["client"] != session["user"]["email"])

_Query.rows = [_Row("Northgate Dental", LOCATION),
               _Row("Northgate Dental Group", LOCATION)]
both = suite_accounts.client_for_location(LOCATION)
check("two clients on one sub-account resolves to neither",
      both["state"] == "ambiguous", both)
check("and names both rather than picking", len(both["candidates"]) == 2, both)
ambiguous = sso.identify(encrypt(PAYLOAD))
check("the handshake refuses it too, rather than showing the first",
      ambiguous["state"] == "ambiguous" and ambiguous["client"] == "",
      ambiguous)

_Query.rows = [_Row("Northgate Dental", LOCATION)]
check("a near-miss sub-account id is not a match",
      suite_accounts.client_for_location(LOCATION[:-2])["state"]
      == "not_connected")
check("nor is a prefix of a real one",
      suite_accounts.client_for_location(LOCATION + "x")["state"]
      == "not_connected")
check("an empty sub-account id matches nothing",
      suite_accounts.client_for_location("")["state"] == "not_connected")


# ---------------------------------------------------------------------------
print("\nThe frame a client's Suite opens, end to end")
# ---------------------------------------------------------------------------
_Query.rows = [_Row("Northgate Dental", LOCATION, "northgatedental.com")]

import wsgi                                                       # noqa: E402
from werkzeug.test import Client as WzClient                      # noqa: E402

web = WzClient(wsgi.application)

page = web.get("/suite-app")
body = page.get_data(as_text=True)
check("the frame opens with no Hub login at all", page.status_code == 200,
      page.status_code)
check("it asks its parent who is looking", "REQUEST_USER_DATA" in body)
check("and it arrives with no staff chrome on it",
      "s1hub-sidebar" not in body and "hub-help" not in body)
check("crawlers are told to stay out", "noindex" in body)

# The route is /suite-app and not /suite/app. /suite is a dispatcher-mounted
# module, and a hub route under a mounted prefix never receives the request —
# the first trap CLAUDE.md names. /api/integrity's high-severity check caught
# this exact mistake while this was being written.
# It does not 404 — it is swallowed by the mounted module, which redirects to
# a staff login. That is worse than a 404, because it looks like a working
# page: the client would meet a Hub sign-in form for an account they will
# never have.
under_mount = web.get("/suite/app")
check("the frame is not hidden behind the mounted /suite module",
      "REQUEST_USER_DATA" not in under_mount.get_data(as_text=True),
      under_mount.status_code)
check("and the mount swallows that path rather than 404ing, which is why the "
      "integrity check exists", under_mount.status_code != 200,
      under_mount.status_code)

ok = web.post("/suite-app/session", json={"payload": encrypt(PAYLOAD)})
answer = ok.get_json()
check("a valid payload resolves to the client", ok.status_code == 200
      and answer["client"] == "Northgate Dental", answer)
check("and hands back their own content link, not a new data surface",
      "/tools/social/c/" in answer["url"], answer["url"])

# The one that matters most: identity comes from the location inside the
# encrypted payload and from nothing else. A request that simply names a
# client is a stranger asking to be shown one.
named = web.post("/suite-app/session", json={"client": "Northgate Dental"})
check("naming a client in the request body gets you nothing",
      named.status_code == 403, named.get_json())
check("and it is refused as unreadable, not as that client",
      named.get_json()["state"] == "unreadable")

for label, payload, state in (
        ("a payload signed with another key", encrypt(PAYLOAD, "wrong-key-xx"),
         "unreadable"),
        ("a payload naming no sub-account", encrypt({"userId": "u"}),
         "no_location"),
        ("a sub-account no client records",
         encrypt({**PAYLOAD, "activeLocation": "loc_NOBODY"}),
         "unknown_location")):
    resp = web.post("/suite-app/session", json={"payload": payload})
    check(f"{label} is refused ({state})",
          resp.status_code == 403 and resp.get_json()["state"] == state,
          resp.get_json())

_Query.rows = [_Row("A Client", LOCATION), _Row("B Client", LOCATION)]
shared = web.post("/suite-app/session", json={"payload": encrypt(PAYLOAD)})
check("a sub-account two clients claim is refused rather than shown to either",
      shared.status_code == 403
      and shared.get_json()["state"] == "ambiguous", shared.get_json())
_Query.rows = [_Row("Northgate Dental", LOCATION, "northgatedental.com")]

# With no key the frame must say so rather than drawing a broken page, and
# the session route must refuse everything.
_key = os.environ.pop("GHL_SSO_KEY")
unset = web.get("/suite-app")
check("with no SSO key the frame explains itself instead of spinning",
      "GHL_SSO_KEY" in unset.get_data(as_text=True), unset.status_code)
check("and does not run the handshake at all",
      "REQUEST_USER_DATA" not in unset.get_data(as_text=True))
blocked = web.post("/suite-app/session", json={"payload": encrypt(PAYLOAD, _key)})
check("and a valid payload is still refused",
      blocked.status_code == 403
      and blocked.get_json()["state"] == "not_configured", blocked.get_json())
os.environ["GHL_SSO_KEY"] = _key


# ---------------------------------------------------------------------------
print("\nNothing here raises, whatever it is handed")
# ---------------------------------------------------------------------------
for bad in (None, "", "x", 12345, "A" * 100_000, encrypt({"activeLocation": ""})):
    try:
        out = sso.identify(bad if isinstance(bad, str) else str(bad))
        ok = out["state"] in sso.STATES and out["ok"] is (out["state"] == "ok")
    except Exception as exc:                                      # noqa: BLE001
        ok = False
        out = f"{type(exc).__name__}: {exc}"
    check(f"  identify({str(bad)[:18]!r}) answers rather than raising", ok, out)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
