"""hub/landing_maker.py + hub/landing_render.py — test harness.

    python3 test_landing_maker.py

Same shape as test_jsonstore.py: no pytest, no new dependencies, a temporary
data directory and a throwaway SQLite mirror, so it never touches /var/data or
the real database. It runs with no OpenAI key, which is the fallback-copy path
— a page still has to be usable when the model is unavailable.

## Why this file exists

A landing page is the one thing in the Hub that is read by someone who is not
staff. Three of the checks below are about that, and each is a failure that
looks fine from the inside:

  1. the built page is PUBLIC          — a prospect has no Hub login, so a
                                         login redirect is a dead campaign
  2. and carries NO staff chrome       — the sidebar, the help layer and the
                                         feedback tab must not appear on a
                                         page pasted onto a client's domain
  3. the maker itself IS gated         — it reads client data

The rest guard the traps CLAUDE.md names:

  4. the proposal picker points at the LIVE builder, not the retired tool
     whose api/ paths all answer 404 — an empty picker that looks loaded
  5. pages survive losing the disk (jsonstore, not a bare file write)
  6. brand colours and fonts are third-party data landing inside <style>
  7. a section with no content is omitted, never a heading over nothing
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1landing_test_")
DISK = os.path.join(TMP, "disk")
MIRROR = os.path.join(TMP, "mirror.sqlite3")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + MIRROR
os.environ["SECRET_KEY"] = "landing-test-secret"
os.environ["PANEL_PASSWORD"] = "landing-test-password"
os.environ.pop("OPENAI_API_KEY", None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


from werkzeug.test import Client                                # noqa: E402
from wsgi import application                                    # noqa: E402
from hub import auth, jsonstore                                 # noqa: E402
from hub import landing_maker as lm                             # noqa: E402
from hub.landing_render import _font_stack, _palette, render_page  # noqa: E402

client = Client(application)
anon = Client(application)
client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test"),
                  domain="localhost")


# --------------------------------------------------------------- 1. storage
section("Where the pages live")

check("store is under the data root",
      os.path.abspath(lm._path()).startswith(os.path.abspath(DISK)), True)

built = lm.create(client="Riverside HVAC", direction="trust",
                  goal="Request a quote", actor="Test")
check("a page builds with no proposal", bool(built.get("ok")), True)
slug = built.get("slug", "")
check("and gets a slug from the client name",
      slug.startswith("riverside-hvac-"), True)
check("with no key set, the copy is the fallback",
      built.get("copy_source"), "fallback")
check("and the response says the page has no products on it",
      built.get("thin"), True)

# The Render disk is not backed up. This is the check that the store is
# jsonstore rather than a bare open()/json.dump, which no page can show you.
os.remove(lm._path())
check("a page survives losing the disk file", bool(lm.get(slug)), True)
check("and the file is put back", os.path.exists(lm._path()), True)


# ------------------------------------------------------------- 2. who may read
section("Who can read what")

r = anon.get(f"/sales/landing/p/{slug}")
check("the built page is public", r.status_code, 200)

body = r.get_data(as_text=True)
check("no staff sidebar on it", "s1hub-sb" in body, False)
check("no feedback tab on it", "s1hub-feed" in body, False)
check("no help layer on it", "hub-help.js" in body, False)
check("it is a whole document", body.lower().startswith("<!doctype html>"), True)

check("a page that does not exist is a 404, not a login",
      anon.get("/sales/landing/p/no-such-page").status_code, 404)

check("the maker itself needs a login",
      anon.get("/sales/landing").status_code in (301, 302, 303), True)
check("so does the listing API",
      anon.get("/api/landing").status_code in (301, 302, 303, 401, 403), True)
check("and creating one", anon.post("/api/landing", json={"client": "X"}
                                    ).status_code in (301, 302, 303, 401, 403), True)


# --------------------------------------------------------------- 3. the page
section("The page itself")

check("the lead form's endpoint token is substituted",
      "{LEAD_ENDPOINT}" in body, False)
check("and it posts to the Hub's lead capture",
      "/api/leads/capture" in body, True)

# A heading over invented content is worse than a short page.
empty = render_page(
    {"client": "Quiet Co", "colors": [], "fonts": [], "logo": "", "geo": "Town"},
    {"headline": "H", "subhead": "S", "cta": "Go",
     "benefits": [], "how_it_works": [], "why_us": [], "faqs": []},
    lm.DIRECTIONS["trust"])
check("no benefits, no benefits section", "How it works" in empty, False)
check("no faqs, no questions section", "Questions" in empty, False)
check("but the hero is still there", "<h1>H</h1>" in empty, True)


# ------------------------------------------------- 4. hostile brand material
section("Brand data is somebody else's input")

hostile = {
    "client": "Test Co",
    "colors": ["red;}</style><script>alert(1)</script>", "#ABC"],
    "fonts": ['Bad", x: url(javascript:alert(1)); y: "'],
    "logo": "", "geo": "Town",
}
pal = _palette(hostile)
check("a color that is not hex is dropped", pal["primary"], "#0D2340")
check("a valid short hex is kept", pal["accent"], "#ABC")

stack = _font_stack(hostile)
check("a font name cannot carry a url()", "url(" in stack, False)
check("nor a javascript: scheme", "javascript:" in stack, False)

hostile_html = render_page(
    hostile, {"headline": "H", "subhead": "S", "cta": "Go", "benefits": [],
              "how_it_works": [], "why_us": [], "faqs": []},
    lm.DIRECTIONS["bold"])
check("nothing closes the style element",
      "</style><script>" in hostile_html, False)
check("and no script is injected",
      "<script>alert(1)</script>" in hostile_html, False)


# ---------------------------------------------------- 5. the proposal picker
section("The proposal picker reads the live builder")

page = client.get("/sales/landing")
check("the maker page renders for a signed-in user", page.status_code, 200)
maker = page.get_data(as_text=True)

# /sales/proposals is the RETIRED tool: every api/ path under it answers 404,
# so a picker pointed there is empty for every proposal written since the
# consolidation while still looking like it loaded.
check("it does not read the retired tool",
      "/sales/proposals/api/proposals" in maker, False)
check("the client is asked for first",
      maker.index('id="lpClient"') < maker.index('id="lpProposal"'), True)
check("client names come from the registry",
      "/api/clients/search" in maker, True)
check("and the proposals are scoped to that client",
      "/api/landing/proposals?client=" in maker, True)
check("both endpoints are real routes",
      (client.get("/api/clients/search?q=a").status_code,
       client.get("/api/landing/proposals?client=x").status_code), (200, 200))


section("A client's proposals, saved and uploaded, kept apart")

both = json.loads(
    client.get("/api/landing/proposals?client=Riverside+HVAC").get_data(as_text=True))
check("saved and uploaded are separate lists",
      isinstance(both.get("saved"), list) and isinstance(both.get("uploaded"), list),
      True)
check("with a total count", "count" in both, True)
check("no client asked, nothing guessed",
      lm.proposals_for("")["count"], 0)

# An uploaded proposal with no readable file must be refused with a reason,
# not silently built from nothing.
made = lm.create(client="Riverside HVAC", uploaded_id="no-such-upload",
                 goal="Book a call", actor="Test")
check("an unreadable uploaded proposal is refused", "error" in made, True)
check("and the reason names the file",
      "file" in made.get("error", "").lower(), True)

# A real uploaded proposal, stored the way this deployment stores one when
# Cloudinary is not configured: on the data disk, with a RELATIVE url. That
# url cannot be fetched over HTTP, and reading it as though it could is how
# every locally stored proposal came back as an empty document.
import io                                                       # noqa: E402
from reportlab.pdfgen import canvas as _canvas                  # noqa: E402
from hub import proposals as hub_proposals                      # noqa: E402

_buf = io.BytesIO()
_pdf = _canvas.Canvas(_buf)
for _i, _line in enumerate(["Proposal for Riverside HVAC",
                            "Monthly budget: $4,500 per month",
                            "Term: 6 months",
                            "Products: Connected TV, Digital Radio"]):
    _pdf.drawString(72, 720 - _i * 18, _line)
_pdf.showPage()
_pdf.save()

_rec = hub_proposals.add_proposal("Riverside HVAC", "spring.pdf",
                                  _buf.getvalue(), date_sent="2026-08-01",
                                  title="Spring campaign")
check("the fixture stored locally, not on Cloudinary",
      _rec.get("storage"), "local")
check("so its url is relative, not fetchable",
      _rec.get("url", "").startswith("/"), True)

both2 = lm.proposals_for("Riverside HVAC")
check("it shows up under uploaded", len(both2["uploaded"]), 1)
check("and is offered as readable", both2["uploaded"][0]["readable"], True)

from_upload = lm.create(client="Riverside HVAC", uploaded_id=_rec["id"],
                        goal="Book a service call", actor="Test")
check("a page builds from it", bool(from_upload.get("ok")), True)
_row = lm.get(from_upload.get("slug", "")) or {}
check("recorded as an uploaded proposal", _row.get("proposal_kind"), "uploaded")
check("and the document's own figure reached the page",
      (_row.get("brief") or {}).get("monthly"), 4500.0)

for label in ("Bold and promotional", "Clean and trustworthy",
              "Premium and image-led"):
    check(f"direction offered: {label}", label in maker, True)


# ------------------------------------------------------------- 6. listing
section("Listing and search")

lm.create(client="Northgate Legal", direction="bold", goal="Book a call",
          actor="Test")
check("every page is listed", lm.listing()["count"], 3)
check("search narrows by client", lm.listing(q="northgate")["count"], 1)
check("and by client filter exactly",
      lm.listing(client="Riverside HVAC")["count"], 2)
check("a near-miss name does not match",
      lm.listing(client="Riverside")["count"], 0)
check("the client list is offered for a filter",
      sorted(lm.listing()["clients"]), ["Northgate Legal", "Riverside HVAC"])

api = json.loads(client.get("/api/landing").get_data(as_text=True))
check("the API agrees", api.get("count"), 3)

# An edit keeps the previous version rather than overwriting it.
saved = lm.update_html(slug, "<html>edited</html>", "Test")
check("an edit is saved", saved.get("ok"), True)
check("and the previous version is kept", saved.get("versions"), 1)
check("the edit is what is served now",
      "edited" in anon.get(f"/sales/landing/p/{slug}").get_data(as_text=True), True)
check("saving to a page that does not exist is reported",
      "error" in lm.update_html("nope", "<html></html>"), True)



section("A sample page for a prospect")

check("a prospect with no website is refused",
      "website" in (lm.create(client="Northgate Dental", kind="prospect",
                              goal="Book a cleaning").get("error") or ""), True)

pros = lm.create(client="Northgate Dental", kind="prospect",
                 website="northgatedental.com", goal="Book a cleaning",
                 actor="Test")
check("with one, the page builds", bool(pros.get("ok")), True)
_p = lm.get(pros.get("slug", "")) or {}
check("it is recorded as a prospect", _p.get("kind"), "prospect")
check("their website is kept", _p.get("website"), "northgatedental.com")
# A prospect has no client record, and a same-named client in the registry is
# a different business — so that lookup must not run at all.
check("it does not claim a client record as its source",
      (_p.get("brief") or {}).get("source"), "prospect website")
check("and it still captures leads, which is the point",
      "/api/leads/capture" in _p.get("page_html", ""), True)
check("a client page is still labeled a client",
      (lm.get(slug) or {}).get("kind"), "client")


section("The built page is made to convert")

_html = _p.get("page_html", "")
for _label, _must in [("a call to action above the fold",
                       '<a class="btn" href="#enquire">'),
                      ("a header that sticks", "position:sticky"),
                      ("a bar pinned to the bottom on phones", 'class="dock"'),
                      ("one form, and every action points at it", 'id="enquire"')]:
    check(_label, _must in _html, True)

# No provider configured is the normal state of a fresh deployment. It must
# produce a colour hero, never a page of broken image icons.
check("with no image provider, no image tags are emitted",
      re.findall(r'<img[^>]+src="([^"]*)"', _html), [])
check("and no empty background url is left behind", "url()" in _html, False)
check("the response says photography was not available",
      "image provider" in (pros.get("note") or ""), True)


section("Pictures, when there are pictures")

from hub.landing_render import render_page as _render               # noqa: E402

_brief = {"client": "Riverside HVAC", "industry": "HVAC", "geo": "Columbus, OH",
          "colors": [], "fonts": [], "logo": "", "products": []}
_copy = {"headline": "H", "subhead": "S", "cta": "Book a visit",
         "benefits": [{"title": "A", "text": "1"}, {"title": "B", "text": "2"}],
         "how_it_works": [], "why_us": [], "faqs": []}


def _img(u):
    return {"url": u, "wide": True, "alt": "", "credit": "Jo Bloggs",
            "credit_url": "", "source": "pexels"}


_pics = {"hero": _img("https://img.test/hero.jpg"),
         "cards": [_img("https://img.test/a.jpg"), _img("https://img.test/b.jpg")],
         "band": _img("https://img.test/band.jpg"),
         "credits": ["Jo Bloggs (pexels)"], "source": "stock", "available": True}
_rich = _render(_brief, _copy, lm.DIRECTIONS["trust"], _pics)
check("the hero carries its photograph",
      "url(https://img.test/hero.jpg)" in _rich, True)
check("every benefit card carries one",
      _rich.count('class="card-pic"'), 2)
check("and the closing band does too",
      "url(https://img.test/band.jpg)" in _rich, True)
check("the photographer is credited", "Photography: Jo Bloggs" in _rich, True)

# A row where some cards have photographs and some do not reads as a page
# that failed to load, so it is all of them or none.
_short = dict(_pics, cards=[_img("https://img.test/only.jpg")])
check("one photograph for two cards gives none",
      'class="card-pic"' in _render(_brief, _copy, lm.DIRECTIONS["trust"], _short),
      False)

# These URLs come off a stock API or somebody's website.
_evil = dict(_pics, hero={"url": "javascript:alert(1)", "wide": True},
             band={"url": "data:text/html,<script>", "wide": True})
_h = _render(_brief, _copy, lm.DIRECTIONS["trust"], _evil)
check("a javascript: image url never reaches the page", "javascript:" in _h, False)
check("nor a data: one", "data:text/html" in _h, False)


section("Changing and removing a page")

check("an empty instruction is refused",
      "error" in lm.revise(pros["slug"], "   "), True)
check("so is one for a page that does not exist",
      "error" in lm.revise("no-such-page", "make it shorter"), True)

# No OPENAI_API_KEY here, which is the failure this has to survive: the page
# must be left exactly as it was rather than half-rewritten.
_before = lm.get(pros["slug"])["page_html"]
_rev = lm.revise(pros["slug"], "lead with the emergency call-out", "Test")
check("a rewrite that cannot run says so", "error" in _rev, True)
check("and changes nothing",
      lm.get(pros["slug"])["page_html"], _before)

_n = lm.listing()["count"]
check("delete removes it", bool(lm.remove(pros["slug"], "Test").get("ok")), True)
check("it is gone from the list", lm.listing()["count"], _n - 1)
check("and cannot be fetched", lm.get(pros["slug"]), None)
check("deleting it twice is reported, not silent",
      "error" in lm.remove(pros["slug"], "Test"), True)
# Removing only the file would leave the mirror to restore it on the next
# read, so the delete has to go through the store, not os.remove.
os.remove(lm._path())
check("and the database mirror does not bring it back",
      lm.get(pros["slug"]), None)


section("The page uses the Hub's own buttons")

check("the build button is the Hub's primary button",
      'class="btn-primary" id="lpBuild"' in maker, True)
check("row actions use the Hub's ghost button",
      maker.count('class="btn-ghost"') >= 3, True)
check("no other module's button class is borrowed",
      "um-btn" in maker, False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
