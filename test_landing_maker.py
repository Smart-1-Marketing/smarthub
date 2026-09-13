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


# ------------------------------------------------------- the Snap concept
section("the Snap concept is an idea, never a build")
# The harvested SNAP_CONCEPT prompt: the positioning language lives in it and
# nowhere else in writing, so what is worth asserting is that the draft
# builds and saves nothing, that the form's blanks are filled from the
# client's record rather than invented, and that the route refuses politely.

from hub import ai as hub_ai                                    # noqa: E402
import hub.client_context as _ctx_mod                           # noqa: E402

_real_chat, _real_tool_ctx = hub_ai.chat, _ctx_mod.tool_context
_snap_calls = []


def _fake_chat(messages, **kw):
    _snap_calls.append((messages[0]["content"], kw))
    return "The Riverstone Snap: five pages..."


def _fake_tool_ctx(name, url="", *, gallery=True):
    return {"client": name, "url": "https://riverstonedental.com",
            "domain": "riverstonedental.com", "industry": "Dental"}


r = anon.post("/api/landing/snap-concept", json={"client": "X"},
              headers={"Accept": "application/json"})
check("an anonymous press is refused", r.status_code in (302, 401), True)

try:
    hub_ai.chat = _fake_chat
    _ctx_mod.tool_context = _fake_tool_ctx
    r = client.post("/api/landing/snap-concept", json={})
    check("a concept for nobody is refused", r.status_code, 400)
    check("and the model was never asked", _snap_calls, [])

    before = len(lm._load())
    r = client.post("/api/landing/snap-concept",
                    json={"client": "Riverstone Dental",
                          "snap_type": "event",
                          "extra": "June 14 open house"})
    check("a named business gets the concept", r.status_code, 200)
    d = r.get_json()
    prompt = _snap_calls[-1][0]
    check("the snap type reaches the prompt", "event" in prompt, True)
    check("and the rep's own detail", "June 14 open house" in prompt, True)
    check("a blank website is filled from the record, not invented",
          "riverstonedental.com" in prompt, True)
    check("at the harvested prompt's own temperature",
          _snap_calls[-1][1].get("temperature"), 0.8)
    check("billed under the maker's own module",
          _snap_calls[-1][1].get("module"), "landing_maker")
    check("nothing was built or saved", len(lm._load()), before)
    check("and the answer says so",
          "The Build button is what makes a page" in d["note"], True)

    def _chat_down(messages, **kw):
        raise hub_ai.AIUnavailable("down")
    hub_ai.chat = _chat_down
    r = client.post("/api/landing/snap-concept", json={"client": "X"})
    check("a dead model is a 502, not a quiet 200", r.status_code, 502)
finally:
    hub_ai.chat, _ctx_mod.tool_context = _real_chat, _real_tool_ctx

maker2 = (ROOT / "hub" / "templates" / "landing_maker.html").read_text(
    encoding="utf-8")
check("the maker carries the concept panel", 'id="snapBtn"' in maker2, True)
from hub import help as hub_help                                # noqa: E402
check("and its help key resolves",
      hub_help.get("landing_maker.snap.concept") is not None, True)


# ---------------------------------------------------------------------------
section("Tier 1 — what the page tells the prospect, and what a lead carries")
# ---------------------------------------------------------------------------
#
# Every check here was confirmed red against the code as it stood. They are
# the conversion audit's Tier 1: a page that thanked visitors whose lead was
# refused, a form that demanded what the page said was optional, leads that
# could not name their own page or the advert that paid for it, an offer that
# reached the prospect only if a model felt like it, and a star rating and a
# byline nobody supplied.

from hub import landing_spec as _ls                               # noqa: E402
from hub.landing_render import render_page as _rp                 # noqa: E402
from hub.landing_maker import DIRECTIONS as _DIRS                 # noqa: E402
from hub.landing_maker import _parse_reviews as _pr               # noqa: E402

_BRIEF = {"client": "Icon Solar", "service_area": "Carmel and Hamilton County",
          "geo": "Carmel, IN + 10-mile radius / Indianapolis DMA / +3 more",
          "phone": "3175550142", "city": "Carmel", "state": "IN",
          "colors": ["#0b5544"]}
_COPY = {"headline": "H", "subhead": "s", "cta": "Get my quote",
         "benefits": [], "how_it_works": [], "faqs": [], "why_us": []}


def _page(**kw):
    return _rp(_BRIEF, _COPY, _DIRS["trust"], {}, **kw)


# -- 1. a refusal is not a thank-you ----------------------------------------
_html = _page(goal_id="quote")
check("the form reads the server's answer before thanking anybody",
      "res.d.ok === false" in _html and "!res.ok" in _html, True)
check("a refusal shows the server's own sentence",
      "res.d.error" in _html, True)
check("and never its hint, which names environment variables",
      "res.d.hint" in _html, False)
# `.find()` rather than `.index()`: against the unfixed renderer the guard
# is absent and `.index()` raises, which would take every check below it out
# of the run -- a file that reports two failures where there are fourteen.
check("the conversion event fires only past that gate",
      0 <= _html.find("res.d.ok === false") < _html.find("generate_lead"), True)
check("a double-submit is refused by the page",
      "dataset.sending" in _html, True)

# -- 2. required-ness matches what the page promises ------------------------
_both = [f["name"] for f in _ls.form_fields("quote") if f["required"]]
check("a goal offering both contacts requires neither", _both, ["name"])
check("a phone-only goal requires the phone",
      sorted(f["name"] for f in _ls.form_fields("call") if f["required"]),
      ["name", "phone"])
check("an email-only goal requires the email",
      sorted(f["name"] for f in _ls.form_fields("download") if f["required"]),
      ["email", "name"])
check("and the fine print is derived, not hard-coded",
      _ls.contact_note("call"),
      "We\u2019ll need a phone number to call you back.")
check("so no goal offers a choice its form does not draw",
      [g["id"] for g in _ls.PAGE_GOALS
       if "whichever" in _ls.contact_note(g["id"])
       and not {"phone", "email"} <= set(g["fields"])], [])

# -- 3. a lead names its page and the advert that paid for it ---------------
_html = _page(goal_id="quote", slug="icon-solar-ab12cd")
check("the lead is filed under the page, not the client's name",
      '"icon-solar-ab12cd"' in _html, True)
check("the client still travels beside it", '"Icon Solar"' in _html, True)
for _k in ("utm_source", "utm_campaign", "gclid", "fbclid"):
    check(f"the arrival carries {_k}", _k in _html, True)
check("and the referrer and landing url",
      "document.referrer" in _html and "landing_url" in _html, True)
check("campaign tags never reach the controlled segmentation field",
      "tags" in _html.split("function arrivalMeta")[1].split("}")[0], False)

# -- 4. the offer is on the page, not only in the prompt --------------------
_off = "Free spring service with every new system"
check("a usable offer is printed once, above the button",
      _page(goal_id="quote", offer=_off, offer_usable=True).count(_off), 1)
check("an offer the checker could not read is not printed",
      _off in _page(goal_id="quote", offer=_off, offer_usable=False), False)

# -- 5. stars and bylines nobody supplied -----------------------------------
_rows, _refused = _pr("Jane D. | 4.5 | They came same day")
check("a half-star rating is refused rather than rounded",
      ("rating" in _rows[0], _refused), (False, ["4.5"]))
_html = _page(goal_id="quote",
              reviews=[{"quote": "Great work", "author": ""},
                       {"quote": "On time", "author": "Jane D.", "rating": 4}])
check("a rating that is not a whole 1-5 draws no stars",
      (_html.count("&#9733;"), _html.count("&#9734;")), (4, 1))
check("a quote with no name gets no byline",
      "Google review" in _html, False)
check("a name that was given still does", "<cite>Jane D.</cite>" in _html, True)

# -- 6. the client brief reaches the copy writer, for a client only ---------
import hub.landing_maker as _lm                                   # noqa: E402
_seen = {}


def _spy(messages, **kw):
    _seen["text"] = messages[-1]["content"]
    raise RuntimeError("stop after capture")


_real = hub_ai.chat
try:
    hub_ai.chat = _spy
    # A website, because hub/client_brief.py answers "" for a client it can
    # join to nothing at all -- which is its own correct behaviour and would
    # make this assertion pass whether or not the wiring existed.
    _lm.write_copy({"client": "Icon Solar", "kind": "client",
                    "website": "iconsolar.com"}, "quote", "", "")
    _client_prompt = _seen.pop("text", "")
    _lm.write_copy({"client": "Icon Solar", "kind": "prospect",
                    "website": "iconsolar.com"}, "quote", "", "")
    _prospect_prompt = _seen.pop("text", "")
finally:
    hub_ai.chat = _real
check("a client page is handed what the Hub already holds",
      "Use it as proof" in _client_prompt, True)
check("a prospect page is not — a same-named client is another business",
      "Use it as proof" in _prospect_prompt, False)

# -- 7. the media plan's targeting string is not the service area -----------
_html = _page(goal_id="quote")
check("the confirmed service area is what the page prints",
      "Serving Carmel and Hamilton County" in _html, True)
check("the media buy's targeting string never reaches the prospect",
      "Indianapolis DMA" in _html, False)
_bare = dict(_BRIEF)
_bare["service_area"] = ""
check("and with none confirmed the line is omitted, never guessed",
      "Serving" in _rp(_bare, _COPY, _DIRS["trust"], {}, goal_id="quote"), False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
