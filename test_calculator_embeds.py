"""The media calculators on smart1marketing.com: framed, public, chrome-free.

    python3 test_calculator_embeds.py

Same shape as test_landing_embeds.py — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror, so it never touches
/var/data or the real database.

## Why this file exists

Five marketing-site pages frame a Hub calculator: `/ims` frames the IMS
Advertising Trade Calculator and `/ctv-ott-calculator`,
`/digital-audio-calculator` and `/dooh-calculator` frame the other three.
test_landing_embeds.py already asserts all of this for `/land/<tool>/embed` —
and asserted none of it here, because **the calculators are a blueprint on the
hub app and every gameplan tool is a dispatcher-mounted module**, so not one of
the rules written for the gameplan embeds was in force on this path.

Both halves failed at once, and each was invisible from the other end:

  1. **`_embed_policy` answered the marketing site with the Suite refusal.**
     `suite_embed.is_embedded()` is true for ANY framer — it reads
     `Sec-Fetch-Dest`, which a browser sends whoever owns the outer page — and
     a hub path that is not in `EMBEDDABLE` is refused 403 in plain text. So a
     prospect on smart1marketing.com/ims got "This Hub page is not available
     inside Smart 1 Suite." where the calculator should have been. The same
     403 hit the `/api/<slug>/estimate` POST, so even a frame that rendered
     could not compute. Nothing errored at either end: the Hub was answering
     exactly as designed, to the wrong question.

  2. **The staff sidebar was injected into a prospect-facing page.**
     `bare_prefixes` in wsgi.py covers dispatcher-mounted modules only, and
     `/tools/calculators/` was not in `CHROMELESS`, so `/c/<slug>` — the
     standalone link you can run an ad to — arrived carrying the Smart 1 Hub
     nav, live links to /client360, /sales/leads and /qa among them, plus the
     help layer and the feedback tab.

Neither is caught by anything else we run: linkcheck resolves the URL, the
template is valid, and the page returns 200 to a signed-in member of staff
opening it in a tab. It only breaks for the one visitor it exists for.

The fix reads `modules.calculators.public_paths()` rather than restating the
prefixes in hub/__init__.py, so the mount and the module cannot disagree about
what is public — the rule modules/ads_builder gives wsgi.py. That is asserted
here too, or the list drifts the day a route is added.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1calc_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "calc-test-secret"
os.environ["PANEL_PASSWORD"] = "calc-test-password"
# A configured Suite route, so the lead section below can prove the calculator
# form reaches the Contacts API. The write itself is stubbed; nothing here
# reaches the network.
os.environ["GHL_PRIVATE_TOKEN"] = "pit-calc-test"
os.environ["GHL_COMPANY_ID"] = "COMPANY_calc"
os.environ["GHL_LEAD_LOCATION_ID"] = "LOC_calc"
os.environ.pop("CALC_WEBHOOK_TRADE", None)

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


# The composed app, not the calculators blueprint on a bare Flask app: the
# chrome injection and the embed policy are both hub-app after_request
# handlers, so neither exists until everything is stacked together.
from wsgi import application                                   # noqa: E402
from werkzeug.test import Client                               # noqa: E402

client = Client(application)

MOUNT = "/tools/calculators"

# The four calculators with a page on the marketing site, plus female-18-34,
# which has the calculator and no page yet. Listed rather than discovered, so a
# calculator that loses its embed fails this file instead of quietly shrinking
# the set it is checked against.
PAGES = {
    "trade": "/ims",
    "ctv": "/ctv-ott-calculator",
    "digital-audio": "/digital-audio-calculator",
    "dooh": "/dooh-calculator",
    "female-18-34": "(no page yet)",
}

# What a browser sends when it is loading a document into an iframe. Sending it
# is the whole point of this file: every one of these routes answers 200 to a
# plain GET and answered 403 to this one.
FRAMED = {"Sec-Fetch-Dest": "iframe", "Referer": "https://smart1marketing.com/ims"}

SIDEBAR = b'<nav class="s1hub-sb"'


section("Every calculator is framable from the marketing site")

for slug in PAGES:
    r = client.get(f"{MOUNT}/embed/{slug}", headers=FRAMED)
    check(f"/embed/{slug} framed is served, not refused", r.status_code, 200)
    # The refusal is text/plain, so the mimetype alone distinguishes a rendered
    # calculator from the 403 that was reaching prospects.
    check(f"/embed/{slug} framed is HTML, not the plain-text refusal",
          r.headers.get("Content-Type", "").startswith("text/html"), True)
    check(f"/embed/{slug} carries the marketing-site allowlist",
          r.headers.get("Content-Security-Policy"),
          "frame-ancestors " + __import__("hub.embed", fromlist=["x"]).frame_ancestors())


section("Framable by us, and not by frame-ancestors *")

import hub.embed as hub_embed                                  # noqa: E402

r = client.get(f"{MOUNT}/embed/trade", headers=FRAMED)
csp = r.headers.get("Content-Security-Policy") or ""
check("smart1marketing.com may frame it", "https://smart1marketing.com" in csp, True)
check("the apex domain is named, not only the wildcard",
      "'self' https://smart1marketing.com" in csp, True)
check("it is an allowlist, not a wildcard", "frame-ancestors *" in csp, False)
# X-Frame-Options has no allowlist form and some browsers let it override CSP.
check("X-Frame-Options is not set beside it",
      r.headers.get("X-Frame-Options"), None)


section("No staff chrome reaches a prospect")

# Both shapes: inside the frame, and the standalone /c/<slug> link that an ad
# can point at. The second is the one that was leaking, because the iframe case
# happened to be short-circuited by the 403.
for slug in PAGES:
    for path, how in ((f"{MOUNT}/embed/{slug}", "framed"),
                      (f"{MOUNT}/c/{slug}", "opened directly")):
        body = client.get(path, headers=FRAMED if how == "framed" else {}).get_data()
        check(f"{path} {how}: no sidebar", SIDEBAR in body, False)
        check(f"{path} {how}: no help layer", b"hub-help.js" in body, False)
        check(f"{path} {how}: no feedback tab", b"hub-demo.js" in body, False)
        # The sidebar's own links are the leak that matters: internal
        # navigation printed onto a third party's domain.
        check(f"{path} {how}: no internal nav links", b'href="/client360"' in body, False)


section("A prospect with no Hub login can reach every calculator")

for slug in PAGES:
    for path in (f"{MOUNT}/embed/{slug}", f"{MOUNT}/c/{slug}"):
        r = client.get(path)
        check(f"{path} is served, not redirected to sign in", r.status_code, 200)

# The estimate endpoint is what the framed page calls on submit. A login
# redirect or a 403 here is invisible until a prospect presses the button.
r = client.post(f"{MOUNT}/api/trade/estimate", json={}, headers=FRAMED)
check("the estimate API answers the framed page rather than refusing it",
      r.status_code in (200, 400), True)
check("and answers as JSON, not the plain-text Suite refusal",
      r.headers.get("Content-Type", "").startswith("application/json"), True)


section("Staff pages keep their chrome")

# The other half of the same rule. A prefix wide enough to cover the public
# routes would also strip the nav off the calculator index and the leads page,
# which is how this fix goes wrong in the opposite direction.
#
# Signed in, and on a client of its own. These two are staff pages behind the
# blueprint's login guard, so an anonymous request lands on /login -- which has
# no sidebar and is not what this section means to be asking about. The client
# above stays logged out on purpose: every assertion before this one is that a
# prospect with no Hub account can reach the public routes.
staff = Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"], "name": "CI"})
for path in (f"{MOUNT}/", f"{MOUNT}/leads"):
    body = staff.get(path, follow_redirects=True).get_data()
    check(f"{path} still has the sidebar", SIDEBAR in body, True)

# ...and the public routes stay chromeless even for somebody who *is* signed
# in, or a rep checking the link sees a different page from the prospect.
for path in (f"{MOUNT}/embed/trade", f"{MOUNT}/c/trade"):
    body = staff.get(path, follow_redirects=True).get_data()
    check(f"{path} has no staff chrome even when signed in", SIDEBAR in body, False)


section("A hub page that is genuinely not embeddable is still refused")

# The Suite refusal must survive: this fix carves out the calculators, not the
# rule. /qa is a staff report and is not in EMBEDDABLE.
r = client.get("/qa", headers=FRAMED)
check("/qa framed is refused", r.status_code, 403)
check("and refused in words rather than blank",
      b"not available inside Smart 1 Suite" in r.get_data(), True)


section("The prefix list is the module's own")

from modules.calculators import public_paths, PUBLIC_PREFIXES, MOUNT as MOD_MOUNT  # noqa: E402

paths = public_paths()
check("public_paths() is built from the module's PUBLIC_PREFIXES",
      paths, [MOD_MOUNT.rstrip("/") + p for p in PUBLIC_PREFIXES])
# Restating these in hub/__init__.py is what the fix deliberately does not do.
# If someone inlines them later, this fails rather than drifting in silence.
hub_src = (ROOT / "hub" / "__init__.py").read_text(errors="ignore")
check("hub/__init__.py reads them rather than restating them",
      "public_paths" in hub_src, True)
# The mount is legitimately named once, in the blueprint registration table.
# What must not appear is a second copy of the public paths inside the
# CHROMELESS literal, which is the copy that would drift.
_chromeless = hub_src[hub_src.index("CHROMELESS = ("):]
_chromeless = _chromeless[:_chromeless.index("@app.after_request")]
check("the CHROMELESS literal does not restate the calculator paths",
      "/tools/calculators" in _chromeless, False)
check("it extends itself from the derived tuple instead",
      "PUBLIC_EMBED_PREFIXES" in _chromeless, True)

# Each public prefix must actually be covered, or a route added under one of
# them inherits the bug this file exists to stop.
for p in paths:
    probe = p if p.endswith(".js") else p + "trade"
    r = client.get(probe, headers=FRAMED)
    check(f"{p} is not answered with the Suite refusal",
          r.status_code == 403 and r.mimetype == "text/plain", False)


section("The diagnostic under the public prefix is staff-only")

# /api/ is public because the embedded calculator posts to /api/<slug>/estimate
# and /api/<slug>/unlock from a client's page, and a variable slug cannot be
# separated from a fixed sibling by prefix. /api/health hid under it: the
# calculator slugs, the database state and which CALC_WEBHOOK_ overrides are
# set, answering anyone with the URL. The August fix stopped it being FRAMED
# (suite_embed.PUBLIC_EXCLUDED) and not being READ.
from modules.calculators import public_excluded                # noqa: E402
from hub import suite_embed                                    # noqa: E402

r = client.get(f"{MOUNT}/api/health", headers=FRAMED)
check("anonymous GET of /api/health is refused", r.status_code, 401)
check("...as JSON a fetch() can read, not a login page",
      r.headers.get("Content-Type", "").startswith("application/json"), True)
check("...carrying none of what it reports", b"delivery" in r.data, False)
r = client.get(f"{MOUNT}/api/health")
check("and refused without the frame headers too", r.status_code, 401)

staff2 = Client(application)
staff2.post("/login", data={"password": "calc-test-password"})
r = staff2.get(f"{MOUNT}/api/health")
check("a signed-in member of staff still gets it", r.status_code, 200)
check("...with the delivery report on it", b"delivery" in r.data, True)

# Every path the embed genuinely calls still answers a stranger in a frame:
# the estimate, then the unlock the estimate's token feeds. The unlock is
# answered 400 here because the body is empty -- the route ran and validated
# it, which is what an open write looks like when nothing satisfies it.
r = client.post(f"{MOUNT}/api/trade/estimate",
                json={"inputs": {"trade": 5000, "cash": 2500, "services": ["Paid Search"]}},
                headers=FRAMED)
check("the estimate the embed posts still answers a stranger", r.status_code, 200)
r = client.post(f"{MOUNT}/api/trade/unlock", json={}, headers=FRAMED)
check("and so does the unlock", r.status_code, 400)
check("...as JSON", r.headers.get("Content-Type", "").startswith("application/json"), True)
r = client.get(f"{MOUNT}/embed.js")
check("and the resizer", r.status_code, 200)

check("the exclusion is declared once, in the module",
      public_excluded(), [f"{MOUNT}/api/health"])
check("and the framing allowlist reads that same list",
      tuple(suite_embed.PUBLIC_EXCLUDED), tuple(public_excluded()))
check("so the guard cannot be reached through a longer public prefix",
      any(f"{MOUNT}/api/health".startswith(p) for p in public_excluded()), True)


section("A calculator lead reaches the panel and the Suite")

# The path traced end to end, against a fake Suite: the gate form posts to
# /api/<slug>/unlock, the module stores its own row, hands the lead to
# hub/leads.py, which stores it and writes it as a Suite contact through
# hub/ghl_contacts.upsert. Every hop has a caller; what was never proved is
# that a submission from the client's page ends up as one contact with the
# calculator's tags on it and a row on /sales/leads.
import json                                                    # noqa: E402
import requests                                                # noqa: E402
from hub import leads as hub_leads                             # noqa: E402
from modules.calculators import store as calc_store            # noqa: E402

SUITE_CALLS = []


class _Resp:
    status_code, ok, text = 200, True, json.dumps({"contact": {"id": "CT_calc_1", "new": True}})

    def json(self):
        return json.loads(self.text)


def _fake_post(url, **kw):
    SUITE_CALLS.append({"url": url, "json": kw.get("json"), "headers": kw.get("headers")})
    return _Resp()


_real_post = requests.post
requests.post = _fake_post
try:
    check("the route is the Suite API before the form is submitted",
          hub_leads.delivery_mode(), "api")
    est = client.post(f"{MOUNT}/api/trade/estimate",
                      json={"inputs": {"trade": 5000, "cash": 2500,
                                       "services": ["Paid Search"]}},
                      headers=FRAMED).get_json()
    token = est.get("token")
    check("the estimate hands back a token", bool(token), True)
    unlock = client.post(f"{MOUNT}/api/trade/unlock", json={
        "token": token, "name": "Pat Calculator", "email": "pat@example.com",
        "phone": "6145551234", "company": "Calc Test Boats", "website": ""},
        headers=FRAMED)
    body = unlock.get_json() or {}
    check("the unlock answers the stranger", unlock.status_code, 200)
    check("...and unlocks the plan", body.get("ok") and "detail" in body, True)

    check("exactly one contact was written", len(SUITE_CALLS), 1)
    sent = (SUITE_CALLS or [{}])[0]
    check("...to the Contacts upsert", (sent.get("url") or "").endswith("/contacts/upsert"), True)
    payload = sent.get("json") or {}
    check("...into the lead location", payload.get("locationId"), "LOC_calc")
    check("...with the visitor's email", payload.get("email"), "pat@example.com")
    check("...their phone", payload.get("phone"), "6145551234")
    check("...and their company", payload.get("companyName"), "Calc Test Boats")
    check("...tagged with the tool and the calculator's own name",
          payload.get("tags"),
          ["smart1-hub", "calculators", "IMS Advertising Trade Calculator"])

    rows = [r for r in calc_store.leads("trade", 50) if r["email"] == "pat@example.com"]
    check("the module's own row records the delivery", [r["webhook_status"] for r in rows], ["sent"])

    panel = staff2.get("/api/leads?days=1&source=calculators").get_json() or {}
    mine = [r for r in panel.get("leads", []) if r.get("email") == "pat@example.com"]
    check("the panel lists the lead", len(mine), 1)
    lead = (mine or [{}])[0]
    check("...as delivered, with the contact id as proof", lead.get("contact_id"), "CT_calc_1")
    check("...filed under the calculator", lead.get("page"), "IMS Advertising Trade Calculator")
    check("...carrying which calculator and what it was worth",
          (lead.get("meta") or {}).get("calculator"), "trade")
    r = staff2.get("/sales/leads")
    check("and /sales/leads renders for staff", r.status_code, 200)
    r = client.get("/api/leads?days=1")
    check("while the panel's data refuses a stranger", r.status_code != 200, True)
finally:
    requests.post = _real_post


section("Every page the marketing site frames has a calculator behind it")

from modules.calculators import catalog                        # noqa: E402

for slug, page in PAGES.items():
    check(f"{page} -> {slug} exists in the catalog",
          catalog.get(slug) is not None, True)

# /paid-search-calculator is a live page on smart1marketing.com with no
# calculator here. Asserted as a known absence rather than left implicit, so
# adding one makes this line the reminder to point the page at it.
check("there is still no paid-search calculator to frame",
      catalog.get("paid-search"), None)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
