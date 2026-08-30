"""The media calculators, framed on a site that is not ours.

    python3 test_calculator_embed.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

`modules/calculators` was built to be embedded — `/embed/<slug>` is described in
its own docstring as the "chrome-free version for an iframe on
smart1marketing.com or Sites", it ships `/embed.js` so the host page can size
the frame, and its public routes sit outside the Hub login. None of it reached
a browser.

The calculators register as a *blueprint on the hub app*, not as a mount in
wsgi.py. So `_embed_policy` in hub/__init__.py ran on them, `EMBEDDABLE` in
hub/suite_embed.py did not list them, and every framed request was answered
with the 403 refusal text meant for a misconfigured Suite menu link. On
smart1marketing.com/ims that rendered as an empty grey box with a broken-image
icon, for every IMS member who scrolled that far, from the day it shipped.

The regression to be afraid of is the *opposite* one, and it is why the wide
rule and the narrow rule are separate lists rather than one list with an
exception in it:

  1.  the calculator is framed        — a prospect on any domain sees the tool
  2.  the staff pages are not         — /tools/calculators/ is the index and
                                        /tools/calculators/leads is a list of
                                        captured names, emails and phone
                                        numbers. Neither may ever be framable,
                                        and "calculators" being an embeddable
                                        word must not make them so
  3.  the wildcard stays local        — a staff page keeps the narrow
                                        allowlist even while a public page next
                                        to it is wide open
  4.  the cookie did not follow       — the embed cookie is refused on the
                                        public routes: they need no session, so
                                        widening it there would grant reach for
                                        nothing
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1calcembed_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))
os.environ.setdefault("SECRET_KEY", "calc-embed-test-secret")

import flask  # noqa: E402

from hub import auth, suite_embed as embed  # noqa: E402

_passed = _failed = 0

# The one staff route sitting under a public prefix; see PUBLIC_EXCLUDED.
PUBLIC_EXCLUDED_SAMPLE = ("/tools/calculators/api/health",)


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


_app = flask.Flask(__name__)


def csp_for(path):
    """The frame-ancestors a real framed request to `path` would come back with."""
    with _app.test_request_context(path):
        resp = embed.framable(flask.make_response("body"))
        return resp.headers.get("Content-Security-Policy", "")


# ------------------------------------------------- 1. the calculator embeds
section("A calculator page may be framed")

PUBLIC = (
    "/tools/calculators/embed/trade",
    "/tools/calculators/embed/trade/",
    "/tools/calculators/embed.js",
    "/tools/calculators/c/trade",
    "/tools/calculators/api/trade/estimate",
    "/tools/calculators/api/trade/unlock",
)
for path in PUBLIC:
    check(f"{path} is embeddable", embed.embeddable(path), True)
    check(f"{path} is recognized as a public page", embed.public_embeddable(path), True)

# The refusal is what a prospect saw where the calculator should have been.
check("so the framed request is not refused",
      embed.embeddable("/tools/calculators/embed/trade"), True)


# --------------------------------------- 2. the staff pages next door are not
section("The staff pages beside it stay unframable")

STAFF = (
    "/tools/calculators",           # the index
    "/tools/calculators/",
    "/tools/calculators/leads",     # captured names, emails, phone numbers
    "/tools/calculators/leads.csv",
    "/tools/calculators/leads/retry",
    "/tools/calculators/api/health",
)
for path in STAFF:
    check(f"{path} is NOT embeddable", embed.embeddable(path), False)
    check(f"{path} is NOT public", embed.public_embeddable(path), False)

check("and the refusal still names the path it refused",
      "/tools/calculators/leads" in embed.refuse("/tools/calculators/leads"), True)


# ------------------------------------------- 3. the wildcard is not contagious
section("A wide rule on one page does not widen the page beside it")

check("a calculator is framable from anywhere",
      csp_for("/tools/calculators/embed/trade"), "frame-ancestors *")
check("and so is its API, or the page renders and its data does not",
      csp_for("/tools/calculators/api/trade/estimate"), "frame-ancestors *")

client360 = csp_for("/client360")
check("Client 360 still carries an allowlist", client360.startswith("frame-ancestors"), True)
check("and it is NOT a wildcard", "frame-ancestors *" in client360, False)
check("HighLevel is still on it", "gohighlevel.com" in client360, True)

# The apex is a separate host from *.smart1marketing.com in CSP, and the page
# that frames the calculator is served from the apex. Missing it would have
# blocked the embed a second time, after the allowlist let it through.
check("the apex domain is on the staff list too",
      "https://smart1marketing.com" in client360, True)

refused = csp_for("/diagnostics")
check("a page being refused is not handed a wildcard either",
      "frame-ancestors *" in refused, False)


# ------------------------------------------- 4. the cookie did not follow
section("The embed cookie stays where it was")

GOOD = f"{embed.COOKIE_NAME}={auth.issue_cookie_value('Todd')}"


def env(path, method="GET", cookie=None):
    e = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": "",
         "HTTP_SEC_FETCH_DEST": "iframe"}
    if cookie:
        e["HTTP_COOKIE"] = cookie
    return e


check("the cookie authenticates nobody on a public calculator",
      embed.user_from_environ(env("/tools/calculators/embed/trade", cookie=GOOD)), None)
check("nor on its API",
      embed.user_from_environ(env("/tools/calculators/api/trade/unlock", cookie=GOOD)), None)
check("and still works where it always did",
      embed.user_from_environ(env("/client360", cookie=GOOD)), "Todd")
check("still refused on a staff calculator page",
      embed.user_from_environ(env("/tools/calculators/leads", cookie=GOOD)), None)
check("embeddable() and the cookie rule are deliberately different",
      (embed.embeddable("/tools/calculators/embed/trade"),
       embed.suite_cookie_allowed("/tools/calculators/embed/trade")),
      (True, False))


# ------------------------------------ 5. one description of what is public
section("The module's own public list and this one do not drift")

# modules/calculators already declares which of its routes sit outside the Hub
# login -- PUBLIC_PREFIXES, the same shape wsgi.py reads off modules/scans and
# modules/ads_builder. Nothing reads the calculators' copy, because a blueprint
# on the hub app never passes AuthGuard, so PUBLIC_EMBEDDABLE here is a second
# statement of the same fact. Two lists agreeing only while somebody keeps them
# in step is the failure this codebase pays test_target_areas.py to prevent, so
# it is asserted rather than trusted: a route added to the module's public list
# and not to this one is framed nowhere, and -- far worse -- one dropped from
# the module's list becomes a staff page that is still framable from any
# domain.
from modules import calculators as _calc  # noqa: E402

for _p in _calc.public_paths():
    _sample = _p if _p.endswith(".js") else _p + "example"
    check(f"the module calls {_p} public, and so does this file",
          embed.public_embeddable(_sample) or _sample.startswith(PUBLIC_EXCLUDED_SAMPLE),
          True)

for _p in embed.PUBLIC_EMBEDDABLE:
    check(f"{_p} is one of the module's own public prefixes",
          any(_p.startswith(m.rstrip("/")) for m in _calc.public_paths()), True)

# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
