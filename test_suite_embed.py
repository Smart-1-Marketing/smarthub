"""hub/embed.py — Hub pages rendered inside Smart 1 Suite's own UI.

    python3 test_suite_embed.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

Every way an embed goes wrong here is silent:

  1.  the cookie is not sent      — SameSite=Lax never reaches a cross-site
                                    frame, so the rep watches a login form
                                    appear inside Suite for an account they
                                    are already signed in to
  2.  the companion grants more
      than the original           — a SameSite=None cookie rides along on
                                    cross-site POSTs too, and this Hub has
                                    destructive buttons behind that cookie
  3.  it is not a second front
      door                        — an allowlist that any path passes is not
                                    an allowlist
  4.  two sidebars                — the hub app's after_request had no iframe
                                    test, and Client 360 is a hub route
  5.  anyone may frame us         — no X-Frame-Options and no CSP is set on
                                    hub pages, so adding the embed without the
                                    allowlist widens an oversight into a
                                    feature
  6.  logout that does not log
      out                         — an embed cookie left behind keeps the
                                    frame signed in
  7.  a blank frame               — refusing in words is what gets the menu
                                    link fixed
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1embed_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))
os.environ.setdefault("SECRET_KEY", "embed-test-secret")

from hub import auth, embed  # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def env(path="/client360", method="GET", cookie=None, dest="iframe", qs=""):
    e = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": qs}
    if cookie:
        e["HTTP_COOKIE"] = cookie
    if dest:
        e["HTTP_SEC_FETCH_DEST"] = dest
    return e


GOOD = f"{embed.COOKIE_NAME}={auth.issue_cookie_value('Todd')}"


# ------------------------------------------------- 1. the cookie authenticates
section("The embed companion carries a real session into the frame")

check("a valid embed cookie names the user",
      embed.user_from_environ(env(cookie=GOOD)), "Todd")
check("and the ordinary guard accepts it, so no route has to remember",
      auth.user_from_environ(env(cookie=GOOD)), "Todd")
check("a forged value authenticates nobody",
      embed.user_from_environ(env(cookie=f"{embed.COOKIE_NAME}=not-a-signed-value")), None)
check("no cookie at all is nobody",
      embed.user_from_environ(env()), None)

# The ordinary cookie must still be preferred and still work on its own.
check("the Lax cookie alone still works everywhere",
      auth.user_from_environ({
          "PATH_INFO": "/anything/at/all", "REQUEST_METHOD": "POST",
          "HTTP_COOKIE": f"{auth.COOKIE_NAME}={auth.issue_cookie_value('Todd')}"}),
      "Todd")


# --------------------------------------------- 2. it grants strictly less
section("The companion cannot do what the original can")

for method in ("POST", "PUT", "PATCH", "DELETE"):
    check(f"{method} from inside the frame authenticates as nobody",
          embed.user_from_environ(env(method=method, cookie=GOOD)), None)
    check(f"and the ordinary guard agrees for {method}",
          auth.user_from_environ(env(method=method, cookie=GOOD)), None)

check("HEAD is safe and still works",
      embed.user_from_environ(env(method="HEAD", cookie=GOOD)), "Todd")


# ------------------------------------------------- 3. the allowlist is real
section("An allowlist every path passes is not an allowlist")

check("Client 360 is embeddable", embed.embeddable("/client360"), True)
check("and the fetches it renders from",
      all(embed.embeddable(p) for p in
          ("/api/c360", "/api/client/work", "/assets/theme.css", "/hub-help.js")),
      True)

# Each of these is a real hub route that must not be reachable this way.
for path in ("/diagnostics", "/activity", "/status", "/qa", "/tools",
             "/seo", "/clients", "/sales/leads", "/login", "/"):
    check(f"{path} is not embeddable", embed.embeddable(path), False)
    check(f"and the cookie is refused there", 
          embed.user_from_environ(env(path=path, cookie=GOOD)), None)


# ----------------------------------------------- 4/5. the response policy
section("Who may frame a page, and what the frame receives")

import flask  # noqa: E402

_app = flask.Flask(__name__)
with _app.test_request_context("/client360"):
    r = embed.framable(flask.make_response("hi"))
    csp = r.headers.get("Content-Security-Policy", "")
    check("the response carries frame-ancestors", csp.startswith("frame-ancestors"), True)
    check("it is an allowlist, not a wildcard", "frame-ancestors *" in csp, False)
    check("HighLevel is on it", "gohighlevel.com" in csp, True)
    check("X-Frame-Options does not survive to contradict it",
          r.headers.get("X-Frame-Options"), None)

check("Sec-Fetch-Dest: iframe reads as embedded",
      embed.is_embedded(env(dest="iframe")), True)
check("so does a nested frame", embed.is_embedded(env(dest="frame")), True)
check("?embed=1 is the explicit opt-out",
      embed.is_embedded(env(dest=None, qs="embed=1")), True)
check("an ordinary page load is not embedded",
      embed.is_embedded(env(dest="document")), False)


# ------------------------------------------------- 7. refusing says why
section("A page that cannot be embedded refuses in words")

msg = embed.refuse("/diagnostics")
check("the refusal names the path", "/diagnostics" in msg, True)
check("and says where the rule lives", "EMBEDDABLE" in msg, True)
check("and is not blank", len(msg) > 80, True)


# ------------------------------- 4 & 6. the composed app, end to end
section("The hub app: no second sidebar, and logout really logs out")

os.environ.setdefault("PANEL_PASSWORD", "embed-test-pw")
from hub import create_hub_app  # noqa: E402

app = create_hub_app()
app.testing = True
c = app.test_client()
c.set_cookie(embed.COOKIE_NAME, auth.issue_cookie_value("Todd"))

framed = c.get("/client360", headers={"Sec-Fetch-Dest": "iframe"})
check("Client 360 renders inside the frame", framed.status_code, 200)
check("with the frame-ancestors allowlist on it",
      framed.headers.get("Content-Security-Policy", "").startswith("frame-ancestors"),
      True)
check("and NO sidebar in it — the whole point",
      b"s1hub-sb" in framed.data, False)

plain = c.get("/client360")
check("the same page outside a frame still gets its sidebar",
      b"s1hub-sb" in plain.data, True)
check("and is not handed an embed CSP it never asked for",
      plain.headers.get("Content-Security-Policy"), None)

blocked = c.get("/diagnostics", headers={"Sec-Fetch-Dest": "iframe"})
check("a non-embeddable page refuses inside a frame", blocked.status_code, 403)
check("and says which path it refused", b"/diagnostics" in blocked.data, True)

# getlist, not get: a response sets several cookies and get() returns only the
# first, which would have passed this test for the wrong reason.
_cookies = c.get("/logout").headers.getlist("Set-Cookie")
check("logout clears the ordinary cookie",
      any(h.startswith(f"{auth.COOKIE_NAME}=;") for h in _cookies), True)
check("logout clears the embed cookie too — or the frame stays signed in",
      any(h.startswith(f"{embed.COOKIE_NAME}=;") for h in _cookies), True)


# --------------------------------------- the half that is not built
section("The client-facing path is written down, not half-built")

check("the SSO note exists", bool(embed.SSO_NOT_BUILT.strip()), True)
check("and names the location id as the authorisation",
      "location id" in embed.SSO_NOT_BUILT, True)
check("and warns about the cross-client failure",
      "worst outcome" in embed.SSO_NOT_BUILT, True)
check("and says the location id must not be trusted from a query string",
      "query string" in embed.SSO_NOT_BUILT, True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
