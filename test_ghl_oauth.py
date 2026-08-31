"""The Marketplace install every Suite call depends on.

    python3 test_ghl_oauth.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches HighLevel: the transport is stubbed, because what is worth asserting
is what this module does when HighLevel says no.

## Why this file exists

`hub/ghl_oauth.py` is four hundred lines that mint the location-scoped tokens
the Suite panel, the social planner, `hub/suite_accounts.py` and Client 360's
sub-account joins all run on — and no test named it except `test_ghl_scopes.py`,
which only asserts that the *scope table* mentions the file. The token half
was unasserted end to end.

**The finding that came out of reading it is in `test_oauth_redirects.py`**,
because it is not really about this module: `/diagnostics` printed one callback
URI and two flows built another. What is asserted here is the rest — the store
that holds the only copy of the agency refresh token, and the refusals.

Every rule below is one this module already states in a comment and that
nothing was holding it to:

* **A refresh that omits the refresh token must not lose it.** HighLevel may
  return the same one or a new one; dropping it on the omission makes the
  install unrecoverable and the only way back is an agency owner re-consenting,
  with nothing saying that is what happened.
* **`disconnect()` must use `jsonstore.delete_json`.** Dropping the file alone
  leaves the mirrored copy for the next `_load()` to restore, so disconnecting
  appears to work and then silently undoes itself — the one way the backup can
  bite you, and this file is the only copy of the token.
* **A rotated `TOKEN_ENCRYPTION_KEY` reads as "not connected", never as a
  crash.** The re-consent is the way back and the panel has to be able to say
  so.
* **"Not connected" and "HighLevel refused" are different answers.** One is a
  one-time consent step somebody has not done; the other is a token problem.
* **`status()` carries no secret.** It is rendered into a page and pasted into
  chats — the rule `services/provider_check.py` works to, which
  `test_oauth_redirects.py` already holds the redirect panel to.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ghloauth_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "ghl-oauth-test-secret"
os.environ["GHL_CLIENT_ID"] = "app-id-half-SUFFIX"
os.environ["GHL_CLIENT_SECRET"] = "SECRET-CLIENT-SECRET-VALUE"
os.environ["GHL_COMPANY_ID"] = "comp_1"
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
os.environ.pop("GHL_OAUTH_STORE", None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def raises(label, want_type, fn, *a, **kw):
    """Assert fn() refuses with want_type, and never let it end the run.

    An assertion that raises takes every check after it out of the file, so a
    revert that breaks one thing reads as a crash rather than as the one
    finding it is -- which is exactly what happened to a first draft of this
    file when a mutated disconnect() left the mirrored copy behind and the
    next refusal came back as the wrong exception type.
    """
    try:
        got = fn(*a, **kw)
        check(label, f"returned {got!r}", f"raised {want_type.__name__}")
        return None
    except want_type as exc:
        check(label, True, True)
        return exc
    except Exception as exc:                                  # noqa: BLE001
        check(label, f"raised {type(exc).__name__}: {exc}",
              f"raised {want_type.__name__}")
        return None


from hub import ghl_oauth as go                              # noqa: E402


class FakeResponse:
    def __init__(self, payload, ok=True, status=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


POSTS = []


def stub_post(payload, ok=True, status=200):
    """Answer the next token POST with this, and record what was sent."""
    def _post(url, data=None, headers=None, timeout=None, **kw):
        POSTS.append({"url": url, "data": dict(data or {})})
        return FakeResponse(payload, ok=ok, status=status)
    go.requests.post = _post


def reset():
    POSTS.clear()
    go.disconnect()


# =====================================================================
section("Consent stores the install, and the callback is the origin's")
# =====================================================================

reset()
check("nothing is connected before consent", go.connected(), False)
check("but the app credentials are configured", go.configured(), True)
check("the callback is built from the origin",
      go.redirect_uri(), "https://smart1.agency/suite/oauth/callback")
check("and the app id is the half before the dash", go.APP_ID, "app")

stub_post({"access_token": "at-1", "refresh_token": "rt-1",
           "companyId": "comp_live", "scope": "a b", "expires_in": 86400})
rec = go.exchange_code("the-code")
check("consent stores an install", go.connected(), True)
check("with the company id HighLevel returned", rec["company_id"], "comp_live")
check("and the redirect it was granted against was sent",
      POSTS[-1]["data"].get("redirect_uri"),
      "https://smart1.agency/suite/oauth/callback")
check("as an authorization_code exchange",
      POSTS[-1]["data"].get("grant_type"), "authorization_code")
check("at the company level, not a location",
      POSTS[-1]["data"].get("user_type"), "Company")

# The company id falls back to the environment only where HighLevel omits it:
# a token whose company id is blank cannot list sub-accounts, which reads on
# the panel as a bad token rather than a missing variable.
reset()
stub_post({"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 86400})
check("an omitted company id falls back to the environment",
      go.exchange_code("c")["company_id"], "comp_1")


# =====================================================================
section("A refresh that omits the refresh token must not lose it")
# =====================================================================
# This file is the only copy of the agency refresh token. Losing it means an
# agency owner re-consenting, and nothing anywhere would say that is why.

reset()
stub_post({"access_token": "at-old", "refresh_token": "rt-original",
           "expires_in": 86400})
go.exchange_code("c")

# Expire it, then answer the refresh with no refresh_token at all.
stored = go._load()
stored["expires_at"] = 0
go._save(stored)
stub_post({"access_token": "at-new", "expires_in": 86400})
check("a refresh happens when the token is close to expiry",
      go.agency_token(), "at-new")
check("as a refresh_token grant",
      POSTS[-1]["data"].get("grant_type"), "refresh_token")
check("sending the refresh token we hold",
      POSTS[-1]["data"].get("refresh_token"), "rt-original")
check("and the old refresh token survives the response omitting it",
      go._load()["refresh_token"], "rt-original")

# A rotated one replaces it.
stored = go._load()
stored["expires_at"] = 0
go._save(stored)
stub_post({"access_token": "at-3", "refresh_token": "rt-rotated",
           "expires_in": 86400})
go.agency_token()
check("a rotated refresh token is kept", go._load()["refresh_token"],
      "rt-rotated")

# A live token is handed straight back without spending a call.
POSTS.clear()
check("a token with time left is not refreshed", go.agency_token(), "at-3")
check("and no call was made", len(POSTS), 0)


# =====================================================================
section("Two workers refreshing at once, and a refusal")
# =====================================================================
# HighLevel rotates the refresh token, so the worker that loses the race is
# holding a dead one. Its refusal must not read as a broken install when the
# other worker's good token is already on disk.

reset()
stub_post({"access_token": "at-a", "refresh_token": "rt-a", "expires_in": 86400})
go.exchange_code("c")
stale = go._load()
stale["expires_at"] = 0
go._save(stale)


def _post_race(url, data=None, headers=None, timeout=None, **kw):
    """The other worker won: our refresh token is dead, and its token is
    already on disk by the time we look again."""
    POSTS.append({"url": url, "data": dict(data or {})})
    winner = dict(stale)
    winner.update({"access_token": "at-winner", "refresh_token": "rt-winner",
                   "expires_at": __import__("time").time() + 3600})
    go._save(winner)
    return FakeResponse({"message": "invalid refresh token"}, ok=False, status=400)


go.requests.post = _post_race
# Through a guard: if the fallback is removed this raises, and an assertion
# that raises takes every check after it out of the run rather than naming
# the one thing that broke.
try:
    _raced = go.agency_token()
except Exception as _exc:                                     # noqa: BLE001
    _raced = f"raised {type(_exc).__name__}"
check("the worker that lost the race returns the winner's token",
      _raced, "at-winner")

# But a refusal with nothing good on disk is a refusal, not a silent empty.
reset()
stub_post({"access_token": "at-b", "refresh_token": "rt-b", "expires_in": 86400})
go.exchange_code("c")
dead = go._load()
dead["expires_at"] = 0
go._save(dead)
stub_post({"message": "invalid refresh token"}, ok=False, status=400)
exc = raises("a genuine refusal raises GhlOAuthError", go.GhlOAuthError,
             go.agency_token)
check("carrying HighLevel's own sentence rather than an invented one",
      "invalid refresh token" in str(exc or ""), True)

# "Nobody has consented" is a different answer from "HighLevel refused".
reset()
exc = raises("no install raises NotConnected", go.NotConnected, go.agency_token)
check("and says which one-time step is missing", "Connect" in str(exc or ""), True)
check("NotConnected is not a kind of GhlOAuthError",
      issubclass(go.NotConnected, go.GhlOAuthError), False)


# =====================================================================
section("The store: encrypted, mirrored, and honestly unreadable")
# =====================================================================

reset()
os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
stub_post({"access_token": "at-clear", "refresh_token": "rt-clear",
           "expires_in": 86400})
go.exchange_code("c")
raw = json.loads(Path(go._store_path()).read_text())
check("with no key the record is stored in the clear", raw["enc"], False)
check("and reads back", go._load()["access_token"], "at-clear")

try:
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    os.environ["TOKEN_ENCRYPTION_KEY"] = key
    reset()
    stub_post({"access_token": "at-enc", "refresh_token": "rt-enc",
               "expires_in": 86400})
    go.exchange_code("c")
    raw = json.loads(Path(go._store_path()).read_text())
    check("with a key the record is encrypted", raw["enc"], True)
    check("and the token is not readable on disk",
          "at-enc" in Path(go._store_path()).read_text(), False)
    check("but reads back through the key", go._load()["access_token"], "at-enc")

    # A rotated key is the case that must not crash: re-consent is the way
    # back, and the panel can only say so if this answers cleanly.
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    # Guarded: a rotated key must come back as "nobody is connected" rather
    # than as an exception, because the panel can only offer the re-consent if
    # this answers. Without the guard a regression here ends the run instead
    # of naming itself.
    try:
        _rotated = go._load()
    except Exception as _exc:                                 # noqa: BLE001
        _rotated = f"raised {type(_exc).__name__}"
    check("a rotated key reads as not connected", _rotated, None)
    try:
        _still = go.connected()
    except Exception as _exc:                                 # noqa: BLE001
        _still = f"raised {type(_exc).__name__}"
    check("rather than raising", _still, False)
    os.environ["TOKEN_ENCRYPTION_KEY"] = key
    check("and the original key still reads it",
          go._load()["access_token"], "at-enc")
finally:
    os.environ.pop("TOKEN_ENCRYPTION_KEY", None)

# The mirror is the reason disconnect cannot be os.remove: dropping only the
# file leaves the database copy for the next read to restore, so the disconnect
# appears to work and then undoes itself.
import ast                                                    # noqa: E402
_src = (ROOT / "hub" / "ghl_oauth.py").read_text()
_tree = ast.parse(_src)
_disc = next(n for n in ast.walk(_tree)
             if isinstance(n, ast.FunctionDef) and n.name == "disconnect")
_calls = [ast.unparse(n) for n in ast.walk(_disc) if isinstance(n, ast.Call)]
check("disconnect goes through jsonstore.delete_json",
      any("delete_json" in c for c in _calls), True)
check("and never os.remove", any("os.remove" in c or "unlink" in c
                                 for c in _calls), False)
check("the record is written through jsonstore, so it is mirrored",
      "jsonstore.write_json" in _src, True)

reset()
stub_post({"access_token": "at-x", "refresh_token": "rt-x", "expires_in": 86400})
go.exchange_code("c")
go.disconnect()
check("and a disconnect does not undo itself on the next read",
      go.connected(), False)


# =====================================================================
section("Location tokens, and what is refused before a call")
# =====================================================================

reset()
stub_post({"access_token": "agency-tok", "refresh_token": "rt",
           "expires_in": 86400})
go.exchange_code("c")

raises("an empty location id is refused by name", go.GhlOAuthError,
       go.location_token, "")
check("and no call was spent on it",
      [p for p in POSTS if "locationToken" in p["url"]], [])

stub_post({"access_token": "loc-tok-1", "expires_in": 86400})
check("a location token is minted", go.location_token("loc_a"), "loc-tok-1")
check("against the company id we hold",
      POSTS[-1]["data"].get("companyId"), "comp_1")
check("and the location asked for", POSTS[-1]["data"].get("locationId"), "loc_a")

before = len(POSTS)
check("a second read is cached", go.location_token("loc_a"), "loc-tok-1")
check("so it costs no call", len(POSTS), before)

# A new install is a new grant, so tokens minted under the old one are dead.
stub_post({"access_token": "agency-2", "refresh_token": "rt2",
           "expires_in": 86400})
go.exchange_code("c2")
check("consenting again drops the cached location tokens", go._loc_cache, {})

# A refusal names the sub-account and what it usually means, rather than a
# bare status code somebody cannot act on.
stub_post({"message": "not installed"}, ok=False, status=401)
exc = raises("a refused location token raises", go.GhlOAuthError,
             go.location_token, "loc_missing")
check("naming the sub-account", "loc_missing" in str(exc or ""), True)
check("and saying what it usually means", "installed" in str(exc or ""), True)


# =====================================================================
section("status() is rendered into a page, so it carries no secret")
# =====================================================================

reset()
stub_post({"access_token": "SECRET-ACCESS-TOKEN", "refresh_token": "SECRET-REFRESH",
           "companyId": "comp_live", "scope": "locations.readonly",
           "expires_in": 3600})
go.exchange_code("c")
st = go.status()
blob = repr(st)
check("connected", st["connected"], True)
check("the access token does not appear", "SECRET-ACCESS-TOKEN" in blob, False)
check("nor the refresh token", "SECRET-REFRESH" in blob, False)
check("nor the client secret", "SECRET-CLIENT-SECRET-VALUE" in blob, False)
check("the scope granted is reported, because that is the actionable part",
      st["scope"], "locations.readonly")
check("and whether it is the full set asked for",
      isinstance(st.get("scopes_complete"), bool), True)

# Not configured, not connected and connected are three states, and the first
# two send somebody to two different places.
reset()
_cid, _sec = os.environ["GHL_CLIENT_ID"], os.environ["GHL_CLIENT_SECRET"]
try:
    go.CLIENT_ID, go.CLIENT_SECRET = "", ""
    st = go.status()
    check("no credentials reads as not configured", st["configured"], False)
    check("and names both variables",
          "GHL_CLIENT_ID" in st["detail"] and "GHL_CLIENT_SECRET" in st["detail"],
          True)
finally:
    go.CLIENT_ID, go.CLIENT_SECRET = _cid, _sec

st = go.status()
check("credentials but no consent reads as configured and not connected",
      (st["configured"], st["connected"]), (True, False))
check("and names the one-time step",
      "connect" in st["detail"].lower(), True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
