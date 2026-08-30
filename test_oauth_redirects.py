"""Every OAuth callback the Hub sends, and the hostname trap underneath it.

The failure this file guards is the one that shipped: a custom domain was
added to the Render service, the `onrender.com` subdomain stayed live beside
it, and three of the six OAuth flows here build their callback from whichever
hostname the browser used. Nothing listed them, so the first sign that half
the registrations were missing was `redirect_uri_mismatch` on a Google
consent screen.

What is asserted:

  1.  a host-derived callback is    — one string per hostname. Reporting one
      one string per hostname         string is the confidently wrong answer:
                                      the other hostname fails and the panel
                                      says everything is registered.
  2.  an env-derived callback       — PUBLIC_BASE_URL's, whatever host the
      does NOT follow the browser     request came in on. Somebody who adds two
                                      URIs at Google must still be told the
                                      client-facing one did not move.
  3.  a provider with no client     — reads *not in use*, never as a missing
      is not a to-do                  redirect URI. Google Ads is parked here.
  4.  no client id or secret        — the panel is rendered into a page and
      is ever returned                pasted into chats, the rule
                                      services/provider_check.py works to.
  5.  PUBLIC_BASE_URL unset, or     — named, rather than a callback quietly
      carrying a path, is named       built with no origin or with a path in
                                      the middle of it.
  6.  the API is a Utilities path   — it names internal callback URLs, and the
                                      page it feeds is admin-only already.
  7.  every path listed is a route  — a callback nothing serves is a
      the composed app serves         registration somebody makes and a 404
                                      the provider reports as our fault.
  8.  report() never raises         — a panel that 500s costs the eleven
                                      others on the page.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1oauth_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")

from hub import oauth_redirects as orx  # noqa: E402
from hub import access  # noqa: E402

FAILED = []


def check(name, got, want=True):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        print(f"         got {got!r}, want {want!r}")
        FAILED.append(name)


def env(**kw):
    """Set exactly this environment for the settings this module reads."""
    for name in ("PUBLIC_BASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_ACCESS_CLIENT_ID",
                 "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_REDIRECT_URI",
                 "GOOGLE_ADS_DEVELOPER_TOKEN", "GHL_CLIENT_ID", "QB_CLIENT_ID",
                 "QB_REDIRECT_URI"):
        os.environ.pop(name, None)
    os.environ.update({k: v for k, v in kw.items() if v})


def row(rows, key):
    return next(r for r in rows if r["key"] == key)


# --------------------------------------------------------------- 1 & 2
print("\nTwo hostnames answer for one service")
env(PUBLIC_BASE_URL="https://smart1-hub.onrender.com",
    GOOGLE_CLIENT_ID="cid", GHL_CLIENT_ID="ghl", QB_CLIENT_ID="qb")
rows = orx.rows("https://smart1.agency/")

finder = row(rows, "google_finder")
check("Google Finder is one URI per hostname", sorted(finder["uris"]),
      ["https://smart1-hub.onrender.com/google/oauth2callback",
       "https://smart1.agency/google/oauth2callback"])
check("and it is flagged as following the browser", finder["follows_browser"])
check("and it asks to be acted on rather than reading ok", finder["state"], "warn")

login = row(rows, "hub_login")
check("Hub sign-in is one URI per hostname too", sorted(login["uris"]),
      ["https://smart1-hub.onrender.com/auth/google/callback",
       "https://smart1.agency/auth/google/callback"])

# The half somebody misses. It is built from PUBLIC_BASE_URL, so browsing the
# new domain changes nothing about it at all.
access_row = row(rows, "google_access")
check("Google Access does NOT follow the browser",
      access_row["follows_browser"], False)
check("so it stays on PUBLIC_BASE_URL's host", access_row["uris"],
      ["https://smart1-hub.onrender.com/connect/callback"])
check("and it is marked as the one a client meets", access_row["client_facing"])
check("Suite is env-derived as well", row(rows, "suite")["uris"],
      ["https://smart1-hub.onrender.com/suite/oauth/callback"])

probs = orx.problems("https://smart1.agency/")
check("the two-hostname split is reported at the top",
      any("smart1.agency" in p["detail"] and "smart1-hub.onrender.com" in p["detail"]
          for p in probs))

print("\nOne hostname is not a finding")
env(PUBLIC_BASE_URL="https://smart1.agency", GOOGLE_CLIENT_ID="cid")
one = orx.rows("https://smart1.agency/")
check("a single host means a single URI", row(one, "google_finder")["uris"],
      ["https://smart1.agency/google/oauth2callback"])
check("and nothing to act on", row(one, "google_finder")["state"], "ok")
check("no split is reported",
      any(p["name"] == "Two hostnames" for p in orx.problems("https://smart1.agency/")),
      False)

# --------------------------------------------------------------------- 3
print("\nA provider with no credentials is not a to-do")
# The Ads flow falls back to the shared GOOGLE_CLIENT_ID, so it has a client
# and no developer token — parked, which must read as parked and not as a
# missing redirect URI standing in amber for ever.
check("Google Ads reads off, not warn", row(one, "google_ads")["state"], "off")
check("and says it is not in use on this deployment",
      "Not in use" in row(one, "google_ads")["note"])
check("while naming the variable that would put it back",
      "GOOGLE_ADS_DEVELOPER_TOKEN" in row(one, "google_ads")["note"])

env(PUBLIC_BASE_URL="https://smart1.agency")
check("with no OAuth client at all there is nothing to register",
      "nothing to register" in
      row(orx.rows("https://smart1.agency/"), "google_ads")["note"])

env(PUBLIC_BASE_URL="https://smart1.agency", GOOGLE_CLIENT_ID="cid",
    GOOGLE_ADS_CLIENT_ID="ads", GOOGLE_ADS_REDIRECT_URI="https://smart1.agency/tools/ads/oauth/callback")
ads = row(orx.rows("https://smart1.agency/"), "google_ads")
check("with a client but no developer token it is 'not in use'", ads["state"], "off")
check("and still prints the URI to register", ads["uris"],
      ["https://smart1.agency/tools/ads/oauth/callback"])

env(PUBLIC_BASE_URL="https://smart1.agency", GOOGLE_ADS_CLIENT_ID="ads",
    GOOGLE_ADS_DEVELOPER_TOKEN="tok",
    GOOGLE_ADS_REDIRECT_URI="https://smart1-hub.onrender.com/tools/ads/oauth/callback")
ads = row(orx.rows("https://smart1.agency/"), "google_ads")
check("a pinned URI on an unknown host is named", ads["state"], "warn")
check("and names the host it points at", "smart1-hub.onrender.com" in ads["note"])

# --------------------------------------------------------------------- 4
print("\nNo credential value is ever carried")
env(PUBLIC_BASE_URL="https://smart1.agency",
    GOOGLE_CLIENT_ID="SECRET-CLIENT-VALUE", GHL_CLIENT_ID="SECRET-GHL",
    QB_CLIENT_ID="SECRET-QB")
blob = repr(orx.report("https://smart1.agency/"))
check("the client id value does not appear", "SECRET-CLIENT-VALUE" not in blob)
check("nor the Suite one", "SECRET-GHL" not in blob)
check("nor the QuickBooks one", "SECRET-QB" not in blob)
check("the variable that supplied it is named instead",
      row(orx.rows(""), "google_finder")["client_var"], "GOOGLE_CLIENT_ID")

# --------------------------------------------------------------------- 5
print("\nPUBLIC_BASE_URL is an origin, and unset is not a URL")
env(GOOGLE_CLIENT_ID="cid")
none = orx.rows("https://smart1.agency/")
check("an env-derived callback with no base is refused, not built",
      row(none, "google_access")["uris"], [])
check("and says which variable is missing",
      "PUBLIC_BASE_URL" in row(none, "google_access")["note"])

env(PUBLIC_BASE_URL="https://smart1.agency/tools/ads/oauth/callback",
    GOOGLE_CLIENT_ID="cid")
pathy = row(orx.rows("https://smart1.agency/"), "google_access")
check("a path in PUBLIC_BASE_URL is named", pathy["state"], "warn")
check("and the URI is still built from the origin alone", pathy["uris"],
      ["https://smart1.agency/connect/callback"])

# --------------------------------------------------------------------- 6
print("\nThe API is a Utilities path")
check("/api/oauth-redirects is gated", access.is_utility("/api/oauth-redirects"))
check("and /api/oauth-redirects-public would not be",
      access.is_utility("/api/oauth-redirects-public"), False)

# --------------------------------------------------------------------- 7
print("\nEvery path listed is one the composed app actually serves")
os.environ.setdefault("SECRET_KEY", "test-only")
os.environ.setdefault("GOOGLE_ADS_REDIRECT_URI",
                      "http://localhost/tools/ads/oauth/callback")
try:
    from werkzeug.middleware.dispatcher import DispatcherMiddleware
    import wsgi  # noqa: E402
    # wsgi.application is wrapped in ProxyFix and two more middlewares, so the
    # dispatcher is reached by unwrapping — the same walk tools/linkcheck.py
    # does, for the same reason.
    disp = wsgi.application
    while not isinstance(disp, DispatcherMiddleware):
        disp = disp.app
    served = set()
    for prefix, app in [("", disp.app)] + list(disp.mounts.items()):
        inner = app
        while not hasattr(inner, "url_map"):
            inner = inner.app
        for rule in inner.url_map.iter_rules():
            served.add((prefix + str(rule)).replace("//", "/"))
    for flow in orx.FLOWS:
        check(f"{flow['path']} is served", flow["path"] in served)
except Exception as exc:  # noqa: BLE001
    # A composed-app boot needs the whole environment; CI has it and a laptop
    # may not. Skipping loudly beats a check that quietly never ran.
    print(f"  skip  composed app would not boot here: {exc}")

# --------------------------------------------------------------------- 8
print("\nThe report never raises")
for origin in ("", "not a url", "https://", "http://x.example/a/b", None):
    try:
        out = orx.report(origin if origin is not None else "")
        ok = isinstance(out, dict) and "redirects" in out
    except Exception:  # noqa: BLE001
        ok = False
    check(f"report({origin!r}) answers", ok)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED")
    for f in FAILED:
        print("  - " + f)
    sys.exit(1)
print("All OAuth redirect checks passed.")
