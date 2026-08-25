"""The gameplan embeds: the marketing site's forms, connected to the Hub.

    python3 test_landing_embeds.py

Same shape as test_msa_embed.py — no pytest, no new dependencies, a temporary
data directory and a throwaway SQLite mirror, so it never touches /var/data or
the real database.

## Why this file exists

smart1marketing.com carries a gameplan page per industry, and the Hub runs the
tool behind each one. Until `hub/embed.py` those were two different forms, and
only the Hub's was connected: a prospect who filled the marketing site's form
did not reach the Leads panel, was not created in Smart 1 Suite, and got no
plan. Nothing errored on either side, which is the whole problem — the number
on the Leads panel looked like the number of prospects.

Every failure this file guards is silent in exactly that way:

  1. **The embed must be reachable without a login.** These modules are mounted
     public, but that is a decision in wsgi.py rather than in the module, and a
     login redirect inside an iframe is invisible — the frame renders the sign-in
     page and staff, who are always signed in, never see it.

  2. **It must carry no staff chrome.** The sidebar and the feedback tab on a
     prospect's screen on a client-facing domain. Checked on the response the
     browser actually receives, since HubBar and the hub's after_request both
     rewrite HTML they did not write.

  3. **It must be framable by us and by nobody else.** `frame-ancestors *` is
     right for the scans widget, which is pasted onto clients' domains; it is
     wrong here, and it is one character to introduce.

  4. **`/embed` must have no trailing slash.** modules/tourism calls its API as
     `fetch('api/partial-lead')`, which resolves against the *directory* of the
     current URL. From `/land/tourism/embed/` that is a 404 the prospect does
     not meet until they have filled in the whole wizard and pressed submit.

  5. **The height reporter must actually be in the page.** Without it the frame
     stays at its starting height and the plan is cut off — the tool works and
     the visitor cannot see the answer.

  6. **A lead posted from inside the frame must land in hub/leads.py.** This is
     the claim the whole change exists to make, so it is made against the store
     rather than against a 200.

And one that is about the paste rather than the code: no shipped snippet may
carry a placeholder host. `smart1-multipart-embed.html` did, and a placeholder
reads as configured to every glance.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1embed_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "embed-test-secret"
os.environ["PANEL_PASSWORD"] = "embed-test-password"

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


# The composed app, not each module's own. Mount shadowing and the chrome
# injection only exist once everything is stacked together, which is why
# CLAUDE.md says to boot through wsgi.application.
from wsgi import application                                  # noqa: E402
from werkzeug.test import Client                              # noqa: E402

client = Client(application)

# Every landing tool with a page on the marketing site, plus HVAC, which has
# the tool and not yet the page. Listed here rather than discovered, so a
# module that loses its embed fails this file instead of quietly shrinking the
# set it is checked against.
MOUNTS = ["/land/stadium", "/land/boat", "/land/rv", "/land/legal",
          "/land/restaurant", "/land/tourism", "/land/ski", "/land/recruit",
          "/land/hvac"]


section("A prospect with no Hub login can reach every embed")

for m in MOUNTS:
    r = client.get(m + "/embed")
    check(f"{m}/embed is served, not redirected to sign in", r.status_code, 200)
    check(f"{m}/embed is HTML",
          r.headers.get("Content-Type", "").startswith("text/html"), True)
    js = client.get(m + "/embed.js")
    check(f"{m}/embed.js is JavaScript",
          js.headers.get("Content-Type", "").startswith("application/javascript"), True)
    # The frame title is the accessible name of the embed, and it carries an em
    # dash. A classic script with no charset is decoded as the HOST page's
    # encoding, so this is the difference between a name and mojibake.
    check(f"{m}/embed.js states its charset",
          "charset=utf-8" in js.headers.get("Content-Type", "").lower(), True)


section("No staff chrome reaches the marketing site")

for m in MOUNTS:
    body = client.get(m + "/embed").get_data(as_text=True)
    check(f"{m}/embed has no sidebar",
          "s1hub-sb" in body or 'class="sidebar"' in body, False)
    check(f"{m}/embed has no feedback tab", "hub-feedback" in body, False)


section("Only our domains may frame the tools")

for m in MOUNTS:
    csp = client.get(m + "/embed").headers.get("Content-Security-Policy", "")
    check(f"{m}/embed sends frame-ancestors", csp.startswith("frame-ancestors"), True)
    check(f"{m}/embed allows smart1marketing.com",
          "https://smart1marketing.com" in csp, True)
    check(f"{m}/embed allows its subdomains",
          "https://*.smart1marketing.com" in csp, True)
    # The one that matters, and a single character to introduce by accident.
    check(f"{m}/embed is an allowlist, not a wildcard", "frame-ancestors *" in csp, False)
    check(f"{m}/embed drops X-Frame-Options, which cannot express an allowlist",
          client.get(m + "/embed").headers.get("X-Frame-Options"), None)


section("The trailing slash that would 404 the submission")

for m in MOUNTS:
    r = client.get(m + "/embed/")
    check(f"{m}/embed/ redirects rather than serving", r.status_code in (301, 308), True)
    check(f"{m}/embed/ redirects under the mount, not to the hub app",
          r.headers.get("Location", "").endswith(m + "/embed"), True)

# Spelled out for the module the rule exists for. tourism is a static asset
# with no Jinja to inject a prefix, so it posts to a relative path.
tourism_js = client.get("/land/tourism/app.js").get_data(as_text=True)
check("tourism calls its API with a relative literal",
      "fetch('api/partial-lead'" in tourism_js, True)
check("and its beacon uses the same path, not the site root",
      "sendBeacon('api/partial-lead'" in tourism_js, True)
check("/land/tourism/api/partial-lead is a real route",
      client.post("/land/tourism/api/partial-lead", json={}).status_code != 404, True)


section("The frame can size itself and forward its scrolls")

for m in MOUNTS:
    body = client.get(m + "/embed").get_data(as_text=True)
    check(f"{m}/embed carries the height reporter", "s1embed:height" in body, True)
    check(f"{m}/embed forwards the tool's own scrolls", "s1embed:scroll" in body, True)
    js = client.get(m + "/embed.js").get_data(as_text=True)
    check(f"{m}/embed.js listens for the height", "s1embed:height" in js, True)
    # Both checks, not either: the origin says it came from the Hub, the source
    # says it came from THIS frame rather than another embed further down.
    check(f"{m}/embed.js checks the message origin", "e.origin !== origin" in js, True)
    check(f"{m}/embed.js checks the message source",
          "e.source !== frame.contentWindow" in js, True)

# The reporter is appended, so the page it is appended to has to still be whole.
for m in MOUNTS:
    body = client.get(m + "/embed").get_data(as_text=True)
    plain = client.get(m + "/").get_data(as_text=True)
    check(f"{m}/embed closes its body exactly once", body.count("</body>"),
          plain.count("</body>"))
    check(f"{m}/embed is the same page, not a fragment", len(body) > len(plain), True)
    # A stale Content-Length truncates the page at the point the browser stops
    # reading — which is mid-script, so the tool half-loads and looks broken.
    r = client.get(m + "/embed")
    declared = r.headers.get("Content-Length")
    check(f"{m}/embed does not declare a length shorter than it sends",
          declared is None or int(declared) == len(r.get_data()), True)


section("The Hub URL appears once, and never as a placeholder")

for m in MOUNTS:
    js = client.get(m + "/embed.js").get_data(as_text=True)
    # Everything is derived from the script's own src, so moving the Hub is a
    # one-word edit per page rather than a hunt through pasted markup.
    check(f"{m}/embed.js derives the frame from its own src",
          "document.currentScript" in js and "s.src" in js, True)
    check(f"{m}/embed.js hardcodes no host", "onrender.com" in js, False)

# The snippet that shipped a placeholder host and a hand-built URL. It is now
# instructions rather than a second copy of the form, so it carries no code at
# all -- which is the only version of this check that cannot be gamed by
# renaming a variable.
snippet = (ROOT / "modules/rv/public/smart1-multipart-embed.html").read_text()
# The whole file is one HTML comment, so the markup it shows is an example
# rather than a second form quietly collecting answers. Checked as "one
# comment, opened once and closed once at the end" rather than by hunting for
# <script>, because the example it prints is a <script> tag.
check("the RV snippet opens as a comment", snippet.lstrip().startswith("<!--"), True)
check("and closes only at the very end", snippet.strip().index("-->"),
      len(snippet.strip()) - 3)
check("it points at the real mount instead", "/land/rv/embed.js" in snippet, True)


section("A lead from inside the frame lands in the Leads store")

from hub import leads                                         # noqa: E402

before = leads.listing(days=2)["count"]
r = client.post("/land/tourism/api/partial-lead", json={
    "name": "Dana Reed", "email": "dana@example.com", "phone": "614-555-0142",
    "business": "Hocking Hills Cabins", "category": "lodging", "zip": "43138"})
check("the wizard's lead endpoint accepts it", r.status_code, 200)

panel = leads.listing(days=2)
check("the lead is stored, not merely acknowledged", panel["count"] > before, True)
row = panel["leads"][0]
check("it is filed under the tool that captured it", row.get("source"), "tourism")
check("with the contact on it", row.get("email"), "dana@example.com")
check("and the business it came from", row.get("company"), "Hocking Hills Cabins")
# Undelivered is the expected state with no Suite credentials in CI. The point
# is that the row exists first: a Suite outage must delay a contact, never
# destroy a lead we already had.
check("and it is kept whether or not Suite accepted it",
      "delivered" in row, True)


section("A tool with its own embed mode uses it on /embed")

# boat and restaurant hide their own hero when embedded, because the marketing
# page they sit on already has one. The switch is client-side and reads the
# URL, so /embed has to be one of the URLs it recognises -- otherwise the
# documented one-line embed draws two headlines on the host page, which reads
# as a broken paste rather than an embed.
#
# ?embed=1 stays recognised: it is what the hand-written iframe on
# smart1marketing.com has always used, and a page pasted before the next deploy
# must not stop working.
for m in ("/land/boat", "/land/restaurant"):
    body = client.get(m + "/embed").get_data(as_text=True)
    check(f"{m} still honours ?embed=1", "get('embed')==='1'" in body, True)
    check(f"{m}/embed is recognised as embedded too",
          "/\\/embed$/.test(location.pathname)" in body, True)
    # The rule is worth nothing without the CSS it switches on.
    check(f"{m} has a rule that hides its hero when embedded",
          ".embed" in body and "hero" in body, True)


section("No landing tool posts a lead to the site root")

# The bug this section exists for: sendBeacon('/api/partial-lead'). Under the
# /land/... mount a root-absolute path leaves the module and reaches the hub
# app, which has no such route -- and the beacon fires on pagehide and returns
# a boolean nobody reads, so every abandoned-form lead 404'd in silence while
# the fetch() beside it, written with the prefix, worked. tools/linkcheck.py
# only sees a literal sitting directly inside fetch("..."), so it saw none of
# this. Six modules had it.
import re                                                     # noqa: E402

ASSETS = ["modules/stadium/public/index.html", "modules/boat/templates/index.html",
          "modules/rv/public/script.js", "modules/legal/templates/index.html",
          "modules/restaurant/templates/index.html", "modules/tourism/public/app.js",
          "modules/ski/templates/index.html", "modules/recruit/templates/index.html",
          "modules/hvac/templates/index.html"]

# A rooted path passed DIRECTLY to fetch or sendBeacon -- which is the failure,
# and is not the same thing as a rooted path appearing in the file. These pages
# also write apiUrl("/api/health") and API_BASE + "/api/partial-lead", where the
# leading slash is correct because the base in front of it carries the mount.
# Matching the string anywhere would flag those and teach whoever hits it that
# this check cries wolf.
ROOTED_CALL = re.compile(
    r"""(?:fetch|sendBeacon)\(\s*['"`](/[a-z0-9/_.-]*)['"`]""", re.I)

for rel in ASSETS:
    src = (ROOT / rel).read_text(errors="ignore")
    check(f"{rel} passes no root-absolute path to fetch or sendBeacon",
          ROOTED_CALL.findall(src), [])

# Proved rather than asserted: what the pages now build must be a real route.
for path in ("/land/boat/api/partial-lead", "/land/ski/api/partial-lead",
             "/land/recruit/api/partial-lead", "/land/legal/api/partial-lead",
             "/land/tourism/api/partial-lead", "/land/stadium/api/partial-lead",
             "/land/restaurant/api/lead"):
    check(f"POST {path} is not a 404",
          client.post(path, json={}).status_code != 404, True)
# ...and what they used to build must not be, or the check above proves nothing.
check("the site root has no /api/partial-lead to catch them",
      client.post("/api/partial-lead", json={}).status_code, 404)


section("Both scripts parse, according to the real parser")

# tools/jscheck.py reads .js files and inline blocks in templates. These two
# are Python strings inside hub/embed.py, so it never sees them -- and a syntax
# error in the reporter takes out the frame's height on every gameplan page at
# once, silently, since a script that fails to parse simply does not run.
import re as _re, subprocess, tempfile as _tf                 # noqa: E402

from hub import embed as hub_embed                            # noqa: E402

_reporter = _re.sub(r"^\s*<script>|</script>\s*$", "",
                    hub_embed.REPORTER_JS.strip())
_scripts = {"reporter": _reporter,
            "loader": hub_embed.loader_js("Smart 1 Marketing — Test", 1400)}

try:
    subprocess.run(["node", "--version"], capture_output=True, check=True)
    _node = True
except Exception:                                             # noqa: BLE001
    _node = False
    print("  skip  node is not installed — CI runs this, see tools/jscheck.py")

if _node:
    for _name, _src in _scripts.items():
        with _tf.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(_src)
            _tmp = fh.name
        _r = subprocess.run(["node", "--check", _tmp], capture_output=True, text=True)
        check(f"the {_name} parses", _r.returncode, 0)
        if _r.returncode:
            print("          " + _r.stderr.strip().splitlines()[-1])
        os.unlink(_tmp)

# The loader is served to a browser as a classic script, so it has to be the
# script and nothing else -- no HTML wrapper, no stray markup.
_served = client.get("/land/boat/embed.js").get_data(as_text=True)
check("the loader is served as bare JavaScript",
      _served.lstrip().startswith("(function()"), True)


section("The allowlist can be widened without editing code")

check("the default names our domain",
      "https://smart1marketing.com" in hub_embed.frame_ancestors(), True)

os.environ["HUB_FRAME_ANCESTORS"] = "'self' https://example.test"
check("an override is honoured", hub_embed.frame_ancestors(),
      "'self' https://example.test")
# Render stores quotes literally, so a value pasted with them arrives with them.
os.environ["HUB_FRAME_ANCESTORS"] = '"\'self\' https://example.test"'
check("Render's literal quotes are stripped", hub_embed.frame_ancestors(),
      "'self' https://example.test")
# But CSP's own single quotes are syntax: stripping those leaves a rule that
# allows nothing, and the embed goes blank with the variable looking set.
check("CSP's own quotes are not", hub_embed.frame_ancestors().startswith("'self'"), True)
os.environ["HUB_FRAME_ANCESTORS"] = "   "
check("a blank value falls back rather than refusing every framer",
      hub_embed.frame_ancestors(), hub_embed.DEFAULT_FRAME_ANCESTORS)
os.environ.pop("HUB_FRAME_ANCESTORS", None)


section("A page with no body is left alone")

# with_reporter appends before the last </body>. A fragment has none, and
# appending script to a partial response is how a partial becomes a broken one.
check("a fragment is returned untouched",
      hub_embed.with_reporter(b"<div>partial</div>"), b"<div>partial</div>")
# The last one, not the first: the IO Builder builds printable documents as
# JavaScript template literals that each carry their own </body>, and injecting
# at the first one put the Hub sidebar inside a string and rendered it blank.
two = b"<body><script>var t = '</body>';</script>done</body>"
check("the reporter goes before the LAST </body>",
      hub_embed.with_reporter(two).index(b"s1embed:height") > two.index(b"var t"), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
