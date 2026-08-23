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
check("a colour that is not hex is dropped", pal["primary"], "#0D2340")
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
check("it reads the live builder's quotes",
      "/sales/builder/api/quotes" in maker, True)
check("and that endpoint is a real route",
      client.get("/sales/builder/api/quotes").status_code, 200)

for label in ("Bold and promotional", "Clean and trustworthy",
              "Premium and image-led"):
    check(f"direction offered: {label}", label in maker, True)


# ------------------------------------------------------------- 6. listing
section("Listing and search")

lm.create(client="Northgate Legal", direction="bold", goal="Book a call",
          actor="Test")
check("both pages are listed", lm.listing()["count"], 2)
check("search narrows by client", lm.listing(q="northgate")["count"], 1)
check("and by client filter exactly",
      lm.listing(client="Riverside HVAC")["count"], 1)
check("a near-miss name does not match",
      lm.listing(client="Riverside")["count"], 0)
check("the client list is offered for a filter",
      sorted(lm.listing()["clients"]), ["Northgate Legal", "Riverside HVAC"])

api = json.loads(client.get("/api/landing").get_data(as_text=True))
check("the API agrees", api.get("count"), 2)

# An edit keeps the previous version rather than overwriting it.
saved = lm.update_html(slug, "<html>edited</html>", "Test")
check("an edit is saved", saved.get("ok"), True)
check("and the previous version is kept", saved.get("versions"), 1)
check("the edit is what is served now",
      "edited" in anon.get(f"/sales/landing/p/{slug}").get_data(as_text=True), True)
check("saving to a page that does not exist is reported",
      "error" in lm.update_html("nope", "<html></html>"), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
