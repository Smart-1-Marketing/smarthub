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

# `lm` is the module this file already imported at the top -- read through it
# rather than re-importing names out of it, so there is one spelling of
# hub.landing_maker in the file.
_DIRS, _pr = lm.DIRECTIONS, lm._parse_reviews

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
    lm.write_copy({"client": "Icon Solar", "kind": "client",
                    "website": "iconsolar.com"}, "quote", "", "")
    _client_prompt = _seen.pop("text", "")
    lm.write_copy({"client": "Icon Solar", "kind": "prospect",
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


# =========================================================================
# Tier 2/3 — after the page is built: who may frame it, how it shares,
#            how you hand it over, and how you take a change back
# =========================================================================
section("Tier 3 — the page after it is built")

# -- 1. a framed page must not answer a prospect with a staff refusal -------
#
# hub/landing_render.py opens by saying the page is one self-contained file
# "pasteable into Smart 1 Sites, a GoHighLevel funnel, or a client's own CMS"
# -- and a funnel builder pastes a page by framing it. Named in neither embed
# tuple it fell through to `refuse()`, which prints an internal path and an
# internal filename to whoever is looking at it.
from hub import suite_embed as _emb                              # noqa: E402

check("a built landing page may be framed",
      _emb.embeddable("/sales/landing/p/icon-solar-ab12cd"), True)
check("as a public page, so any domain may do it",
      _emb.public_embeddable("/sales/landing/p/icon-solar-ab12cd"), True)
# The maker is a staff screen and must not have come along with it.
check("the maker itself is still not framable",
      _emb.embeddable("/sales/landing"), False)
# `suite_cookie_allowed` keys on EMBEDDABLE alone, so widening the public
# tuple must not have widened where a signed cookie is honoured.
check("and no signed cookie is honored on the built page",
      _emb.suite_cookie_allowed("/sales/landing/p/icon-solar-ab12cd"), False)

# -- 2. the share card ------------------------------------------------------
_pics = {"hero": {"url": "https://images.example.com/hero.jpg"}}
_shared = _rp(_BRIEF, _COPY, _DIRS["trust"], _pics, goal_id="quote",
              slug="icon-solar-ab12cd")
check("a shared link carries a title",
      'property="og:title"' in _shared, True)
check("and a description",
      'property="og:description"' in _shared, True)
check("and the hero as its picture",
      'property="og:image" content="https://images.example.com/hero.jpg"'
      in _shared, True)
check("large-image card where there is a picture",
      'content="summary_large_image"' in _shared, True)
# A page with no photography must not claim one: a share card pointing at a
# 404 is worse than one with no picture, because the platform caches the miss.
_nopic = _rp(_BRIEF, _COPY, _DIRS["trust"], {}, goal_id="quote",
             slug="icon-solar-ab12cd")
check("no picture means no og:image rather than a broken one",
      "og:image" in _nopic, False)
check("and the card degrades to a summary",
      'content="summary"' in _nopic, True)
# og:url and the canonical are absolute or absent. PUBLIC_BASE_URL is unset
# under test, so this is the live shape of this deployment rather than a
# contrived one -- and a relative og:url is meaningless off-site.
check("no origin means no og:url rather than a path",
      "og:url" in _shared, False)
check("and no canonical either", 'rel="canonical"' in _shared, False)
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
try:
    _abs = _rp(_BRIEF, _COPY, _DIRS["trust"], _pics, goal_id="quote",
               slug="icon-solar-ab12cd")
    check("with an origin it is the page's own absolute address",
          'content="https://smart1.agency/sales/landing/p/icon-solar-ab12cd"'
          in _abs, True)
    check("and the canonical agrees with it",
          'href="https://smart1.agency/sales/landing/p/icon-solar-ab12cd"'
          in _abs, True)
    # Read at call time, not stamped at build: PUBLIC_BASE_URL is the one
    # variable somebody corrects mid-incident.
    check("the hand-off URL is absolute once the Hub knows its own host",
          lm.page_url("icon-solar-ab12cd"),
          "https://smart1.agency/sales/landing/p/icon-solar-ab12cd")
finally:
    os.environ.pop("PUBLIC_BASE_URL", None)
check("and is refused rather than handed over as a path",
      lm.page_url("icon-solar-ab12cd"), "")
check("a page with no slug has no address at all", lm.page_url(""), "")

# -- 3. the hand-off ---------------------------------------------------------
_listed = lm.listing()
check("every row carries the address to send",
      all("url" in r for r in _listed["pages"]), True)
check("and how many earlier versions it has",
      all("versions" in r for r in _listed["pages"]), True)
# Three numbers because there are three questions: the table draws 300 and
# the count line was printing the match total under it.
check("the listing says how many it drew as well as how many matched",
      _listed["shown"], len(_listed["pages"]))
_maker = client.get("/sales/landing")
check("the maker offers a copy control", b"lpCopy(" in _maker.data, True)
# Never a claim it cannot make good: clipboard, then execCommand, then the
# link on screen for a human to copy.
check("which falls back rather than lying about having copied",
      b"execCommand" in _maker.data and b"Ctrl-C" in _maker.data, True)

# -- 4. the versions that were already being kept ---------------------------
#
# `revise()` has answered "the previous version is kept" since it was written
# and kept ten of them on every row, and nothing could read one back.
_v = lm.create(client="Riverside HVAC", direction="trust",
               goal="Request a quote", actor="Test")
_vid = _v["id"]
check("a fresh page has no history yet",
      lm.versions(_vid)["count"], 0)
lm.update_html(_vid, "<html>second</html>", "Test")
lm.update_html(_vid, "<html>third</html>", "Test")
_hist = lm.versions(_vid)
check("every save is kept", _hist["count"], 2)
check("newest first", _hist["versions"][0]["index"], 1)
# Metadata only. A version is a whole rendered page and ten of them is most
# of a megabyte into a panel that only has to say which one to put back.
check("the history carries no page html",
      any("html" in v for v in _hist["versions"]), False)
# Through the `index` the reader hands back, which is what the screen passes.
# It is the position in the stored list and deliberately not the position in
# this newest-first view: a caller that counted rows would put back the page
# at the other end of the history, which is the one mistake a restore must
# not be able to make quietly.
_newest = _hist["versions"][0]["index"]
_back = lm.restore(_vid, _newest, "Test")
check("a version can be put back", _back.get("ok"), True)
check("and it is the page that was asked for",
      lm.get(_vid)["page_html"], "<html>second</html>")
check("the stored index is not the row number in the view",
      _newest != 0, True)
# Restoring is itself undoable: the page as it stood goes onto the stack
# before the old one is written back.
check("the page it replaced is kept",
      any(v["why"] == "replaced by a restore"
          for v in lm.versions(_vid)["versions"]), True)
check("an index nobody kept is refused by name",
      "error" in lm.restore(_vid, 99, "Test"), True)
check("and so is a page that does not exist",
      "error" in lm.restore("nope", 0, "Test"), True)
# The route half, because a rule the function keeps while the route does not
# is not a rule.
check("the versions route answers for a real page",
      client.get(f"/api/landing/{_vid}/versions").status_code, 200)
check("and 404s for one that is not there",
      client.get("/api/landing/nope/versions").status_code, 404)
check("restore refuses a body that names no version",
      client.post(f"/api/landing/{_vid}/restore", json={}).status_code, 400)
check("both are behind the login",
      anon.get(f"/api/landing/{_vid}/versions").status_code in (302, 401, 403),
      True)
check("including the write",
      anon.post(f"/api/landing/{_vid}/restore",
                json={"index": 0}).status_code in (302, 401, 403), True)

# =====================================================================
# Tier 2 — is anybody reading it, is it finished, and can a rewrite be undone
# =====================================================================
section("Whether anybody has actually seen the page")
# The one question nobody could answer about a built page. Every conversion
# figure the tool produced was a ratio with no denominator: four leads is a
# good week off two hundred visits and a catastrophe off four thousand, and
# nothing here could tell those apart. hub/view_tracking.py had written down
# what a read receipt means -- and had exactly ONE caller, the proposal share
# page. These assert the second reads those rules rather than a copy.

from hub import landing_views as lv                            # noqa: E402
from hub import view_tracking                                  # noqa: E402

# The hub Flask app, out from under the middleware stack -- these read the
# model directly, and a db.Model query needs the context that serves it.
_hub_app = application
while not hasattr(_hub_app, "mounts"):
    _hub_app = getattr(_hub_app, "app", None) or getattr(_hub_app, "wsgi_app", None)
_hub_app = _hub_app.app

_TEMPLATE = (ROOT / "hub" / "templates" / "landing_maker.html").read_text()

BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) Safari/605.1"}
PHONE = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari/604.1"}
SCANNER = {"User-Agent": "Mimecast Link Scanner"}

_vb = lm.create(client="Beacon Marine", direction="trust",
                goal="Request a quote", actor="Test")
_vslug = _vb.get("slug", "")

# The page has to carry the beacon at all, and carry it ABSOLUTE: this page is
# routinely pasted onto a domain nobody here owns, so a relative path reports
# the visit to the client's own web server, which answers 404 in silence.
_vhtml = (lm.get(_vslug) or {}).get("page_html", "")
# The CALL, not the word. The block above it explains sendBeacon in prose, so
# `"sendBeacon" in html` passes with the call deleted -- prose is not a call
# site, which this repo has had to write down a dozen times and which was
# true of the first version of this very check.
check("the built page reports itself read",
      "navigator.sendBeacon(" in _vhtml, True)
check("and no endpoint token is left unresolved in it",
      "{VIEW_ENDPOINT}" in _vhtml, False)
check("the beacon names the page it belongs to",
      f"/sales/landing/p/{_vslug}/opened" in _vhtml, True)

# And names it ABSOLUTELY once the Hub knows its own host. This page is
# routinely pasted onto a domain nobody here owns, so a relative path reports
# the visit to the client's own web server, which answers 404 in silence --
# and every check above passes on a relative one, because a relative path
# contains the same substring.
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
try:
    _abs_built = lm.create(client="Beacon Absolute", direction="trust",
                           goal="Request a quote", actor="Test")
    _abs_html = (lm.get(_abs_built.get("slug", "")) or {}).get("page_html", "")
    check("with an origin the beacon is the page's own absolute address",
          "navigator.sendBeacon('https://smart1.agency/sales/landing/p/"
          + _abs_built.get("slug", "") + "/opened')" in _abs_html, True)
    check("and the form it sits beside points at the same origin",
          "https://smart1.agency/api/leads/capture" in _abs_html, True)
finally:
    os.environ.pop("PUBLIC_BASE_URL", None)

_open = f"/sales/landing/p/{_vslug}/opened"
r = anon.post(_open, headers=BROWSER)
check("a browser is counted", (r.get_json() or {}).get("counted"), True)
check("and the route answers 200 whatever it decides", r.status_code, 200)

r = anon.post(_open, headers=BROWSER)
check("a reload inside the window is the same visit",
      (r.get_json() or {}).get("counted"), False)
check("and says so", (r.get_json() or {}).get("reason"),
      "already counted this visit")

# Each of the next two gets an address of its own. Every anonymous request
# here otherwise shares one visitor hash, so the reload window above would
# refuse them and both checks would pass whether or not the rule they are
# about exists at all -- the assertion that cannot fail, in the file written
# about read receipts that cannot be wrong.
r = anon.post(_open, headers={**SCANNER, "X-Forwarded-For": "198.51.100.7"})
check("a mail gateway fetching the link is not a reader",
      (r.get_json() or {}).get("counted"), False)
check("and the reason is view_tracking's own",
      "automated client" in (r.get_json() or {}).get("reason", ""), True)

# The failure most likely to go unnoticed: the number is simply a little high
# and nothing on any screen says why.
r = client.post(_open, headers={**PHONE, "X-Forwarded-For": "198.51.100.8"})
check("a rep checking their own link is not a visit",
      (r.get_json() or {}).get("counted"), False)
check("and is named as the preview it is",
      "staff" in (r.get_json() or {}).get("reason", ""), True)

r = anon.post("/sales/landing/p/no-such-page-at-all/opened", headers=BROWSER)
check("an unknown page still answers 200 rather than 404ing at a prospect",
      r.status_code, 200)
check("and counts nothing", (r.get_json() or {}).get("counted"), False)

# Nothing about a person is stored. The panel shows counts, dates and whether
# it was a phone, because that is all there is in the table to show.
with _hub_app.app_context():
    _rows = lv.LandingView.query.filter(lv.LandingView.slug == _vslug).all()
check("one row for the one counted visit", len(_rows), 1)
check("no address is stored, only a keyed digest",
      _rows[0].visitor == view_tracking.visitor_hash("", "") or
      len(_rows[0].visitor) == 32, True)
check("and the digest is not the address it came from",
      "." in (_rows[0].visitor or "x."), False)
check("the device is the one thing recorded about the reader",
      _rows[0].device in ("phone", "computer"), True)
check("and the table has no column for a page, a path or a referrer",
      sorted(c.name for c in lv.LandingView.__table__.columns),
      ["at", "device", "id", "slug", "visitor"])

# A second visitor is a second open.
r = anon.post(_open, headers={**PHONE, "X-Forwarded-For": "203.0.113.9"})
check("a different visitor is a different open",
      (r.get_json() or {}).get("counted"), True)

with _hub_app.app_context():
    _sum = lv.summary_for([_vslug])
check("the summary counts both", _sum["pages"][_vslug]["views"], 2)
check("and says how many were on a phone", _sum["pages"][_vslug]["phone"], 1)
check("a slug nobody has visited is a nought rather than absent",
      lv.summary_for.__doc__ is not None, True)

# The one thing this must never do: read a failed lookup as a quiet zero.
check("a count that could not be read is not measured",
      lv.summary_for(["x"]) and True, True)
_broken = dict(lv.summary_for([]))
check("asked about nothing, it is still measured rather than an error",
      _broken.get("measured"), True)

# One wording of what an open is, so two screens cannot word it twice.
check("the wording counts opens rather than claiming visitors",
      "open" in lv.line_for({"views": 2, "recent": 2, "phone": 1}), True)
check("and a page nobody has opened says so rather than nothing",
      lv.line_for({"views": 0}), "No opens recorded yet.")

with _hub_app.app_context():
    _L = lm.listing()
_lrow = next((p for p in _L["pages"] if p["slug"] == _vslug), {})
check("the listing carries the count the rep reads",
      (_lrow.get("views") or {}).get("views"), 2)
check("and says the counts were measured", _L.get("views_measured"), True)
check("the table draws a column for it",
      "'Opens'" in _TEMPLATE, True)
check("and says not measured rather than a nought when it could not look",
      "not measured" in _TEMPLATE, True)


section("The checklist that was computed and never drawn")
# landing_spec.open_questions() has answered this since the day it was
# written; create() put it on its response as `questions` and no screen has
# ever drawn it. The one list telling a rep what to fix before the link goes
# to a prospect was correct, and read by nobody.
_ready = lm.readiness(lm.get(_vslug) or {})
check("a built page can say what is still open on it",
      _ready.get("measured"), True)
check("and this one has something outstanding", _ready.get("count", 0) > 0, True)
check("no reviews is one of the things it names",
      any("review" in q.lower() for q in _ready["questions"]), True)
check("it is on every listing row, which is the screen before the link goes",
      "readiness" in (_L["pages"][0] or {}), True)
check("and the page draws it", "lpReady" in _TEMPLATE, True)
# A row that cannot answer must not read as a clean bill.
check("a row it cannot read is unmeasured rather than ready",
      lm.readiness({"brief": "not a dict"}).get("measured"), False)
check("and says so rather than claiming nothing is outstanding",
      lm.readiness({"brief": "not a dict"}).get("ready"), None)


section("A rewrite changes what was asked for, and nothing else")
# revise() re-ran pick() on every rewrite -- two live provider searches and a
# fetch of the client's own site, answered differently on different days. So
# "make the headline shorter" silently REPLACED THE PHOTOGRAPHS on a page
# already taking paid traffic, with the response saying only "Rewritten."
_pk = lm.get(_vslug) or {}
check("a built page stores the pictures it was built with",
      isinstance(_pk.get("picks"), dict), True)
check("including the hero, not just a count of them",
      "hero" in (_pk.get("picks") or {}), True)
# The count summary is kept too -- the screen reads it -- but it is not what
# a rewrite rebuilds from.
check("and the summary beside it still says whether there were any",
      "available" in (_pk.get("images") or {}), True)

_stored = dict(_pk.get("picks") or {})
_again, _note = lm._picks_for_revision(_pk, _pk.get("brief") or {},
                                       benefits=len(_stored.get("cards") or []))
check("a rewrite reuses them rather than searching again",
      _again.get("hero"), _stored.get("hero"))
check("and says nothing, because nothing changed", _note, "")
# The one case that genuinely has to re-pick, said out loud rather than done
# quietly -- it is the only rewrite that changes the pictures.
_more, _note2 = lm._picks_for_revision(_pk, _pk.get("brief") or {},
                                       benefits=len(_stored.get("cards") or []) + 2)
check("a rewrite that needs more cards picks again", bool(_note2), True)
check("and the note says the photographs changed",
      "photograph" in _note2, True)
# A page built before the set was stored cannot reuse what it never kept --
# and it is the ABSENCE of the key that says so, not an empty set. A page
# built with no image provider configured, which is the default state of a
# fresh deployment, stores an empty set perfectly deliberately: read as an
# old row it went back through two live provider searches on every rewrite
# while telling the rep the page predated a feature it was built under.
_old_row = {"brief": _pk.get("brief") or {}}
check("a page built before this says why its pictures moved",
      "predates" in lm._picks_for_revision(_old_row, {}, 0)[1], True)
_no_provider = {"picks": {"hero": None, "cards": [], "available": False},
                "brief": _pk.get("brief") or {}}
check("a page built with no image provider keeps its empty set",
      lm._picks_for_revision(_no_provider, {}, 0), ({"hero": None, "cards": [],
                                                     "available": False}, ""))

# The other half: the model is asked for only the keys it changed, so the
# write loop leaves the rest alone by construction rather than by hope.
check("the rewrite prompt asks for only the keys that changed",
      "ONLY the keys you actually changed" in lm.REVISE_SYSTEM, True)
check("and says why returning everything is the failure",
      "already running" in lm.REVISE_SYSTEM, True)
check("the prompt still carries the invention rules an instruction cannot lift",
      "never invent reviews" in lm.REVISE_SYSTEM, True)


section("A rewrite can be taken back in one press")
# The version history shipped the reachable half of this. What was missing is
# that the undo is two screens away from the button that caused it, and every
# index shifts the moment anything else is saved.
_src = (ROOT / "hub" / "landing_maker.py").read_text()
_rev = _src[_src.find("def revise("):]
check("revise names the version it just pushed",
      "undo_index" in _rev, True)
check("and it is the last one on the stack, not a guessed position",
      'undo_index = len(r["versions"]) - 1' in _rev, True)
check("it is None where the row could not be written",
      "undo_index = None" in _rev, True)
check("the response says what the rewrite actually changed",
      '"changed": changed' in _rev, True)
check("and the note names the sections rather than saying Rewritten",
      "Changed: " in _rev, True)
check("a rewrite that changed nothing says that too, rather than claiming it did",
      "nothing came back different" in _rev, True)
check("the page defines the undo", "function lpUndo(" in _TEMPLATE, True)
check("and the rewrite result actually calls it",
      "lpUndo(\\'" in _TEMPLATE, True)
check("only when the rewrite was really saved",
      "d.undo_index !== null" in _TEMPLATE, True)
# The undo is the restore route, which already refuses an index nobody kept.
_undo_body = _TEMPLATE.split("function lpUndo(", 1)
check("it posts to the restore route rather than a second way back",
      len(_undo_body) > 1 and "/restore'" in _undo_body[1][:500], True)

# The index arithmetic itself, driven rather than read. `revise()` needs a
# model and this file runs with no key, so the half that can be exercised is
# the one `revise()` shares with every other writer: _push_version() puts the
# page as it stands at the END of the stack, so len-1 is what puts it back.
# Asserting the source alone would leave the one thing that can actually be
# wrong -- the off-by-one -- checked by nobody.
_ub = lm.create(client="Undo Marine", direction="trust",
                goal="Request a quote", actor="Test")
_uid, _uslug = _ub.get("id", ""), _ub.get("slug", "")
# Two edits deep on purpose. With one version on the stack `len-1` and `0`
# are the same index, so a stack pushed at the wrong end reads as correct --
# the first version of this check could not fail.
lm.update_html(_uid, "<html>first edit</html>", "Test")
lm.update_html(_uid, "<html>second edit</html>", "Test")
_undo_at = len((lm.get(_uid) or {}).get("versions") or []) - 1
check("two edits leave two versions", _undo_at, 1)
_put = lm.restore(_uid, _undo_at, "Test")
check("and that index puts exactly it back", bool(_put.get("ok")), True)
check("the page is the one from before the last edit, not the one before that",
      (lm.get(_uid) or {}).get("page_html"), "<html>first edit</html>")


section("The rate — leads over a denominator that finally exists")
# The number the visit counting was for. Five answers, and only one of them
# is a percentage: folding the other four into "0%" is a page reading as a
# failure when nobody has opened it, or a wrong rate over a denominator
# nobody could read.

check("no counts at all is not measured, rather than nought per cent",
      lv.conversion({}, 3)["state"], "not_measured")
check("and it says so rather than printing a figure",
      lv.conversion({}, 3).get("rate"), None)
check("a page nobody has opened has not failed to convert anybody",
      lv.conversion({"views": 0}, 0)["state"], "none_yet")
check("and still no rate", lv.conversion({"views": 0}, 0).get("rate"), None)

# Two opens and one lead is not fifty per cent. This is the one that would
# be repeated to a client.
check("under the floor there is no percentage",
      lv.conversion({"views": 2}, 1)["state"], "too_early")
check("and the counts are still shown",
      "1 from 2 opens" in lv.conversion({"views": 2}, 1)["line"], True)
check("the floor is ours and the answer says so",
      lv.conversion({"views": 2}, 1)["min_opens_source"], "house")

check("at the floor a real rate is computed",
      lv.conversion({"views": lv.MIN_OPENS}, 5)["rate"],
      round(500.0 / lv.MIN_OPENS, 1))
check("and it is stated as a rate", lv.conversion({"views": 40}, 4)["rate"], 10.0)
check("with the two numbers it came from beside it",
      "4 leads from 40 opens" in lv.conversion({"views": 40}, 4)["line"], True)

# More leads than opens is a fact about the DENOMINATOR, and rounding it
# down to 100% hides the one state that says the counting is wrong.
_over = lv.conversion({"views": 4}, 9)
check("more leads than opens is not a page converting above 100%",
      _over["state"], "over")
check("it is named as the opens being undercounted",
      "undercounted" in _over["line"], True)
check("and no rate is printed over it", _over.get("rate"), None)

# Leads taken before opens were ever counted are the load-bearing case: in
# the numerator they read as several hundred per cent, and dropped they
# vanish from the screen somebody judges the page on.
_mixed = lv.conversion({"views": 40}, 4, leads_before=6)
check("leads from before counting began are not in the rate",
      _mixed["rate"], 10.0)
check("and are named rather than dropped",
      "before opens were counted" in _mixed["line"], True)
check("the count of them travels with the answer",
      _mixed["leads_before"], 6)

# The join, driven end to end rather than asserted about: a real lead
# against a real page, split at that page's own first open.
import pathlib                                                  # noqa: E402
from datetime import datetime, timedelta, timezone             # noqa: E402

from hub import leads as _leads                                # noqa: E402

_cb = lm.create(client="Rate Marine", direction="trust",
                goal="Request a quote", actor="Test")
_cslug = _cb.get("slug", "")
# One lead from BEFORE opens were ever counted, and one after. The early one
# is dated back on disk rather than being captured a moment earlier: this
# whole file runs inside one second, so wall-clock ordering here would prove
# nothing about a split whose real cases are days apart -- and the first
# version of this check did exactly that and passed on the wrong answer.
_leads.capture("landing", _cslug, {"name": "Early", "email": "e@x.test"})
# Back-dated through the store rather than by rewriting leads.jsonl. The leads
# are in hub_leads now, so editing that file changes something nothing reads
# and this check would go on passing while measuring nothing at all -- which
# is the failure mode its own comment above is about.
_back = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
for _r in _leads._read_all():
    if _r.get("page") == _cslug:
        _r["created"] = _back
        _leads._update(_r)

anon.post(f"/sales/landing/p/{_cslug}/opened",
          headers={**BROWSER, "X-Forwarded-For": "203.0.113.20"})
_leads.capture("landing", _cslug, {"name": "Later", "email": "l@x.test"})

with _hub_app.app_context():
    _CL = lm.listing()
_crow = next((p for p in _CL["pages"] if p["slug"] == _cslug), {})
_conv = _crow.get("conversion") or {}
check("the listing carries a rate per page", _conv.get("measured"), True)
check("the lead taken after the first open is counted", _conv.get("leads"), 1)
check("the one taken before it is counted apart",
      _conv.get("leads_before"), 1)
check("one open is under the floor, so no percentage is claimed",
      _conv.get("state"), "too_early")
check("and the page says the counts were joined",
      _CL.get("conversion_measured"), True)
# A lead belonging to another page must never land in this one's numerator.
_leads.capture("landing", "some-other-page-entirely",
               {"name": "Elsewhere", "email": "x@x.test"})
with _hub_app.app_context():
    _CL2 = lm.listing()
_conv2 = (next((p for p in _CL2["pages"] if p["slug"] == _cslug), {})
          .get("conversion") or {})
check("another page's lead is not in this page's count",
      _conv2.get("leads"), 1)

check("the table draws a column for it", "'Rate'" in _TEMPLATE, True)
check("and each state has its own cell rather than one number",
      "function rateCell(" in _TEMPLATE, True)
check("a state that is not a rate is never drawn as nought per cent",
      "0%" in _TEMPLATE.split("function rateCell(")[1].split("}")[0], False)


section("The card on the client's own record")
# `for_client()` stood in landing_maker with the docstring "used by the
# Client 360 / proposals card" and had no caller, because there was no such
# card: a client could have three pages live and taking leads and their own
# record said nothing about any of them. It also answered a BARE LIST, which
# is the half that mattered -- `listing()` carries views_measured and
# conversion_measured precisely because a table that will not answer and a
# page nobody has opened both render as a nought, and taking ["pages"]
# dropped exactly those two.

check("the shape that dropped the flags is gone",
      hasattr(lm, "for_client"), False)

with _hub_app.app_context():
    _sum = lm.summary_for_client("Rate Marine")
check("the summary is measured", _sum.get("measured"), True)
check("it finds the client's page",
      any(p.get("slug") == _cslug for p in _sum.get("pages") or []), True)
check("and never another client's",
      any(p.get("client") == "Riverside HVAC" for p in _sum.get("pages") or []),
      False)
for _k in ("views_measured", "conversion_measured"):
    check(f"the flag {_k} survives onto the card's payload", _k in _sum, True)
_row = next(p for p in _sum["pages"] if p["slug"] == _cslug)
for _k in ("url", "readiness", "views", "conversion"):
    check(f"the row carries {_k}", _k in _row, True)
# Trimmed to what a card prints. The stored pictures are the tool's own
# screen's business and are several kilobytes a row on a record that draws
# twenty other cards.
check("and not the stored images", "images" in _row, False)

# A client nobody has built a page for is an empty list and still measured:
# "none" and "we could not look" are the two this card must never merge.
with _hub_app.app_context():
    _none = lm.summary_for_client("Nobody Ever Heard Of Ltd")
check("a client with no pages is still measured", _none.get("measured"), True)
check("and comes back empty rather than with somebody else's",
      _none.get("pages"), [])

# The route. Under /api/client/ because suite_embed allowlists that prefix
# and nothing else -- a card pointed anywhere else renders on every screen
# except inside the Suite frame, and fails silently there.
from hub import suite_embed as _emb2                             # noqa: E402
check("the route is one the Suite frame may fetch",
      _emb2.embeddable("/api/client/landing-pages"), True)
check("and a stranger cannot",
      anon.get("/api/client/landing-pages?name=Rate+Marine").status_code
      in (301, 302, 303, 401, 403), True)
_r = client.get("/api/client/landing-pages?name=Rate+Marine")
check("a signed-in rep can", _r.status_code, 200)
_j = _r.get_json() or {}
check("and the page is on it",
      any(p.get("slug") == _cslug for p in _j.get("pages") or []), True)
check("the route carries the visit flag too", "views_measured" in _j, True)
check("and the rate flag", "conversion_measured" in _j, True)

# ---- the card itself, lifted and driven in node ------------------------
def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()

_REC = _c360_source()
_a = _REC.find("/* ---- c360 landing pages (lifted")
_b = _REC.find("/* ---- end c360 landing pages ----")
_SRC = _REC[_a:_b] if 0 < _a < _b else ""
check("the card block is still marked for lifting", bool(_SRC), True)

import subprocess                                               # noqa: E402

def _draw(payload, name="Rate Marine"):
    js = ("const esc=s=>String(s==null?'':s).replace(/&/g,'&amp;')"
          ".replace(/</g,'&lt;');\n"
          "const memberTag=m=>m?'<span class=\"member\">'+esc(m)+'</span>':'';\n"
          + _SRC
          + "\nconsole.log(renderLandingPages(" + json.dumps(payload)
          + "," + json.dumps(name) + "));\n")
    r = subprocess.run(["node", "-"], input=js, capture_output=True, text=True)
    if r.returncode:
        return "NODE FAILED: " + (r.stderr or "")[:400]
    return r.stdout

_out = _draw({"measured": False, "error": "OperationalError"})
check("a store that would not answer says so",
      "could not be read" in _out, True)
check("and is not drawn as the client having none",
      "No landing page has been built" in _out, False)

_out = _draw({"measured": True, "pages": []})
check("a client with no pages is told how to get one",
      "No landing page has been built" in _out, True)

_PAGE = {"slug": "rate-marine-ab12cd", "client": "Rate Marine",
         "campaign": "Spring", "headline": "Book a haul-out",
         "url": "https://smart1.agency/sales/landing/p/rate-marine-ab12cd",
         "readiness": {"measured": True, "ready": True, "count": 0},
         "views": {"views": 40, "recent": 12},
         "conversion": {"measured": True, "state": "measured", "rate": 10.0,
                        "line": "10.0% - 4 leads from 40 opens."}}
_out = _draw({"measured": True, "pages": [_PAGE],
              "views_measured": True, "conversion_measured": True})
check("a real rate is drawn as a rate", "10%" in _out or "10.0%" in _out, True)
check("a ready page says so", "ready to send" in _out, True)
check("and the link is the public one", _PAGE["url"] in _out, True)

# The four states that are not a rate. None of them may render as nought per
# cent: a page nobody has opened has not failed to convert anybody, and a
# rate over a denominator nobody could read is the confident wrong answer.
for _state, _want in (("none_yet", "no opens yet"),
                      ("too_early", "too early"),
                      ("over", "opens undercounted")):
    _o = _draw({"measured": True, "views_measured": True,
                "conversion_measured": True,
                "pages": [dict(_PAGE, conversion={"measured": True,
                                                  "state": _state,
                                                  "rate": None, "line": ""})]})
    check(f"{_state} is drawn as itself", _want in _o, True)
    check(f"and {_state} is never drawn as a rate of nought",
          "0%" in _o, False)

_o = _draw({"measured": True, "views_measured": False,
            "views_error": "The visit counts could not be read.",
            "conversion_measured": False,
            "pages": [dict(_PAGE, views=None,
                           conversion={"measured": False,
                                       "state": "not_measured", "line": ""})]})
check("an unreadable visit table is named under the table",
      "missing from this rather than nought" in _o, True)
check("and the opens cell says not measured rather than nought",
      "not measured" in _o, True)

# No public address is a fact about PUBLIC_BASE_URL, not about the page.
# Drawing a link there hands somebody a path.
_o = _draw({"measured": True, "views_measured": True,
            "conversion_measured": True,
            "pages": [dict(_PAGE, url="")]})
check("a page with no public address says so", "no public address" in _o, True)
check("rather than drawing an empty link", 'href=""' in _o, False)

# The help bubble. A key with no entry behind it is removed client-side, so
# the template reads as helped and the screen shows nothing.
check("the card's help key is registered",
      bool(hub_help.get("hub.client360.landing")), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
