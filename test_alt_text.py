"""hub/alt_text.py and the Claude-in-Chrome publish prompts — test harness.

    python3 test_alt_text.py

Same shape as the other test files: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory. No OpenAI key and no
network — the page fetch is stubbed at hub/seo.py's own `_fetch` seam, so the
scan runs against fixture HTML rather than somebody's live site.

## What is worth asserting

  * **`alt` absent and `alt=""` are different answers.** An empty alt is a
    decision (this image is decorative); a missing one is an omission. Report
    both as "" and every genuinely missing alt hides inside a list of images
    that were already handled correctly — which is the number an audit
    actually reports.

  * **A decorative image keeps an empty alt.** The whole rewrite path is built
    to fill in blanks, so the one case where blank is *correct* has to survive
    it. A 1px spacer described as "air conditioning repair in Dublin" is worse
    than the spacer with no alt at all.

  * **The three rules a model gets wrong.** Length, the "image of" preamble,
    and the fact that both are enforced after the model answers rather than
    asked for in the prompt.

  * **The prompt is the deliverable, and it never carries a password.** This
    Hub stores the site login under Client Setup, and interpolating it into a
    block of text destined for a chat window is the easiest possible mistake
    to make. The human signs in; the prompt says so.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-alt-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "alt-text-test")
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("OPENAI_API_KEY", None)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from wsgi import application                             # noqa: E402
from werkzeug.test import Client as _WClient             # noqa: E402

from hub import alt_text, cms_publish, seo               # noqa: E402

CLIENT = "Alt Test HVAC"
SITE = "https://coolairco.com"

PAGE_HTML = """<html><head><title>AC Repair in Dublin | Cool Air Co</title></head><body>
<h1>Air conditioning repair in Dublin, Ohio</h1>
<h2>Same-day service</h2>
<img src="/img/spacer.gif" width="1" height="1">
<img src="/img/tech-van-1234.jpg" alt="image of a technician">
<img src="/img/logo.png" alt="Cool Air Co">
<img src="/img/divider.svg" alt="" role="presentation">
<img srcset="/img/unit-800.jpg 800w, /img/unit-400.jpg 400w" alt="Air handler">
<img src="data:image/gif;base64,R0lGOD" alt="never scanned">
<p>We repair and service air conditioning across Dublin and Columbus.</p>
</body></html>"""


# ------------------------------------------------------------- reading
print("\nreading what the page already says")
imgs = alt_text.images_on(PAGE_HTML, SITE + "/ac-repair")
srcs = [i["src"] for i in imgs]
check("relative sources are made absolute",
      SITE + "/img/tech-van-1234.jpg" in srcs, srcs)
check("a data: URI is not an image to describe",
      not any("data:" in s for s in srcs), srcs)
check("a srcset-only image is still found",
      any(s.endswith("unit-800.jpg") for s in srcs), srcs)

spacer = next(i for i in imgs if "spacer" in i["src"])
divider = next(i for i in imgs if "divider" in i["src"])
van = next(i for i in imgs if "tech-van" in i["src"])
check("a missing alt attribute is recorded as missing, not as empty",
      spacer["has_alt"] is False and divider["has_alt"] is True,
      (spacer["has_alt"], divider["has_alt"]))
check("a 1px spacer reads as decorative", alt_text.is_decorative(spacer))
check('role="presentation" reads as decorative', alt_text.is_decorative(divider))
check("a real photo does not", not alt_text.is_decorative(van))


# ---------------------------------------------------------- the clamps
print("\nthe three rules enforced after the model answers")
check("the image-of preamble is stripped",
      alt_text._clean_alt("Photo of a technician on a roof") == "a technician on a roof",
      alt_text._clean_alt("Photo of a technician on a roof"))
check("a stacked preamble is stripped too",
      not alt_text._clean_alt("Image of a photo of a van").lower().startswith("image"))
long_alt = alt_text._clean_alt("technician " * 40)
check("length is capped at what a screen reader reads",
      len(long_alt) <= alt_text.MAX_ALT + 1, len(long_alt))
check("and cut on a word boundary, not mid-word",
      not long_alt.replace("…", "").endswith("techni"), long_alt[-20:])
check("markup in an alt is removed",
      "<" not in alt_text._clean_alt("<b>van</b> on site"))


# ------------------------------------------------------------ the scan
print("\nthe scan, against fixture pages")
_real_fetch = seo._fetch
_ASKED = []


def _fake_fetch(url, timeout=15):
    _ASKED.append(url)
    if "broken" in url:
        raise RuntimeError("HTTP 503")
    return PAGE_HTML


seo._fetch = _fake_fetch
try:
    store = seo.load_store(CLIENT)
    store["site_url"] = SITE
    store["sitemap"] = [SITE + "/", SITE + "/ac-repair", SITE + "/heating",
                        SITE + "/broken", SITE + "/about", SITE + "/contact",
                        SITE + "/blog"]
    seo.save_store(CLIENT, store)

    out = alt_text.scan(CLIENT, 5)
    check("only the first N sitemap pages are fetched",
          len(_ASKED) == 5, _ASKED)
    check("a page that 503s is reported, not counted as having no images",
          len(out["errors"]) == 1 and len(out["pages"]) == 4,
          (out["errors"], len(out["pages"])))
    check("the error says why", "503" in out["errors"][0]["error"], out["errors"][0])
    check("every image on every readable page is listed",
          out["total_images"] == 4 * 5, out["total_images"])
    check("images with no usable alt are counted",
          out["missing_alt"] == 4 * 2, out["missing_alt"])
    check("the scan is saved against the client",
          alt_text.load(CLIENT)["total_images"] == out["total_images"])

    over = alt_text.scan(CLIENT, 999)
    check("the page count is clamped rather than crawling a whole site",
          len(over["pages"]) + len(over["errors"]) <= alt_text.MAX_PAGES,
          len(over["pages"]))

    # ------------------------------------------------- the rewrite, no AI key
    print("\nthe rewrite with no AI key — the fallback still has to obey the rules")
    alt_text.scan(CLIENT, 5)
    res = alt_text.rewrite(CLIENT, [SITE + "/ac-repair"])
    page = next(p for p in alt_text.load(CLIENT)["pages"]
                if p["url"] == SITE + "/ac-repair")
    by_src = {i["src"]: i for i in page["images"]}
    dec = by_src[SITE + "/img/spacer.gif"]
    check("a decorative image is left with an EMPTY alt, not a description",
          dec["new_alt"] == "" and dec["decorative"] is True, dec)
    check("and says so, so nobody 'fixes' it later",
          "decorative" in str(dec.get("why", "")).lower(), dec.get("why"))
    real = by_src[SITE + "/img/tech-van-1234.jpg"]
    check("a real image gets something written from the filename and the H1",
          bool(real["new_alt"]), real)
    check("the fallback obeys the length cap",
          len(real["new_alt"]) <= alt_text.MAX_ALT + 1, real["new_alt"])
    check("the fallback does not start with 'image of'",
          not real["new_alt"].lower().startswith("image"), real["new_alt"])
    check("noise words from the filename are dropped",
          "1234" not in real["new_alt"], real["new_alt"])
    check("it reports template mode rather than claiming the AI wrote it",
          res["ai"] is False, res)
    check("rewriting one page leaves the others alone",
          all(not i.get("new_alt") for p in alt_text.load(CLIENT)["pages"]
              if p["url"] != SITE + "/ac-repair" for i in p["images"]
              if not i.get("decorative")))
finally:
    seo._fetch = _real_fetch


# --------------------------------------------------------- hand editing
print("\nediting an alt by hand")
edited = alt_text.set_alt(CLIENT, SITE + "/ac-repair",
                          SITE + "/img/tech-van-1234.jpg",
                          "Photo of a " + "technician " * 40)
check("a hand-typed alt is clamped exactly as the AI's is",
      len(edited["new_alt"]) <= alt_text.MAX_ALT + 1
      and not edited["new_alt"].lower().startswith("photo"), edited["new_alt"])
check("clearing an alt marks the image decorative rather than blank-and-broken",
      alt_text.set_alt(CLIENT, SITE + "/ac-repair",
                       SITE + "/img/tech-van-1234.jpg", "")["decorative"] is True)
check("an image not in the scan is refused",
      alt_text.set_alt(CLIENT, SITE + "/ac-repair", SITE + "/nope.jpg", "x") is None)


# ------------------------------------------------------------- the code
print("\nthe code view")
alt_text.set_alt(CLIENT, SITE + "/ac-repair", SITE + "/img/tech-van-1234.jpg",
                 "Technician servicing a rooftop condenser")
code = alt_text.code_view(CLIENT, [SITE + "/ac-repair"])
check("the new tag is there", 'alt="Technician servicing a rooftop condenser"' in code, code[:400])
check("the old tag is there too, because a find-and-replace needs it",
      "was:" in code, code[:400])
check("a decorative image is marked as deliberately empty",
      "an empty alt is correct" in code)
check("nothing rewritten produces a comment, not an empty file",
      "Nothing rewritten yet" in alt_text.code_view("Nobody At All"))


# ------------------------------------------------- the Claude-in-Chrome prompt
print("\nthe prompt handed to Claude in Chrome")
pages = alt_text.selected_pages(CLIENT, [SITE + "/ac-repair"])
alt_p = cms_publish.instructions("wordpress", "alt", pages,
                                 client=CLIENT, site_url=SITE)
check("the alt panel opens the WordPress dashboard",
      alt_p["admin_url"].endswith("/wp-admin/"), alt_p["admin_url"])
check("the prompt names each image by its src",
      "tech-van-1234.jpg" in alt_p["prompt"], alt_p["prompt"][:300])
check("and gives the current alt beside the new one, so a mismatch is visible",
      "current:" in alt_p["prompt"] and "new alt:" in alt_p["prompt"])
check("it tells the agent to change nothing else",
      "Change nothing else" in alt_p["prompt"])
check("it tells the agent to ask rather than guess which image is which",
      "tell me rather than picking one" in alt_p["prompt"])
check("WordPress's two-places-for-alt-text trap is in the prompt",
      "Media Library" in alt_p["prompt"])

s1_alt = cms_publish.instructions("smart1", "alt", pages, client=CLIENT, site_url=SITE)
check("Smart 1 Sites opens through the Hub, never a guessed Simvoly URL",
      s1_alt["admin_url"].startswith("/sites"), s1_alt["admin_url"])
check("and is told builder changes are not live until published",
      "not live until the site is published" in s1_alt["prompt"])

faq_p = cms_publish.instructions(
    "wordpress", "faqs",
    [{"url": SITE + "/ac-repair", "questions": [{"q": "How much does AC repair cost?"}],
      "html": "<div class='s1-faq'>…</div>"}],
    client=CLIENT, site_url=SITE)
check("an FAQ hand-off carries the accordion block itself",
      "s1-faq" in faq_p["prompt"], faq_p["prompt"][-300:])
check("and the questions, so the reviewer can see what is going up",
      "How much does AC repair cost?" in faq_p["prompt"])
check("it tells the agent the block already carries its own schema",
      "FAQPage JSON-LD" in faq_p["prompt"])

print("\nwhere the FAQ block goes is asked, not left to the agent")
check("the panel offers placements for FAQs and only for FAQs",
      len(faq_p["placements"]) >= 4
      and cms_publish.instructions("wordpress", "alt", pages, client=CLIENT,
                                   site_url=SITE)["placements"] == [],
      len(faq_p["placements"]))
check("the default is the last section before the footer",
      faq_p["placement"] == "before_footer"
      and "before the footer" in faq_p["prompt"], faq_p["placement"])
check("the prompt states the position rather than telling Claude to ask",
      "WHERE IT GOES ON THE PAGE" in faq_p["prompt"])
replaced = cms_publish.instructions(
    "wordpress", "faqs",
    [{"url": SITE + "/ac-repair", "questions": [], "html": "<div>x</div>"}],
    client=CLIENT, site_url=SITE, placement="replace")
check("choosing replace says to replace, and to stop if there is none to replace",
      "REPLACE the FAQ section" in replaced["prompt"]
      and "do not add a second one" in replaced["prompt"])
check("choosing 'ask me' is the one option that hands the decision back",
      "ASK me where to put it" in cms_publish.claude_prompt(
          "smart1", "faqs", [{"url": SITE, "questions": []}], placement="ask"))
check("an unknown placement falls back to the default rather than an empty rule",
      "before the footer" in cms_publish.claude_prompt(
          "smart1", "faqs", [{"url": SITE, "questions": []}], placement="nonsense"))
check("every page in one hand-off gets the same position",
      "same position on every page" in faq_p["prompt"])

for kind, items in (("blogs", []), ("schema", []), ("faqs", []), ("alt", [])):
    out = cms_publish.instructions("wordpress", kind, items, client=CLIENT, site_url=SITE)
    check(f"selecting nothing for {kind} is a warning, not an empty panel",
          any("Nothing selected" in w for w in out["warnings"]), out["warnings"])
check("an unknown content kind is refused",
      "error" in cms_publish.instructions("wordpress", "podcasts", [], client=CLIENT))

# The one thing that must never be true of any prompt this module writes.
seo.save_blog_settings(CLIENT, {"author": {"name": "Dana Reyes"}})
setup = seo.load_store(CLIENT)
setup.setdefault("setup", {})["password"] = "hunter2-not-a-real-password"
setup["setup"]["login"] = "admin@coolairco.com"
seo.save_store(CLIENT, setup)
for cms in cms_publish.CMS_KEYS:
    for kind, items in (("alt", pages), ("faqs", []), ("schema", []), ("blogs", [])):
        text = cms_publish.instructions(cms, kind, items, client=CLIENT,
                                        site_url=SITE).get("prompt", "")
        check(f"no stored credential reaches the {cms}/{kind} prompt",
              "hunter2" not in text and "admin@coolairco.com" not in text)


# ----------------------------------------------------------------- routes
print("\nthe HTTP routes")
client = _WClient(application)
client.post("/login", data={"password": "test", "name": "Tester"})

r = client.get("/api/seo/alt?name=" + CLIENT.replace(" ", "%20"))
check("the alt route answers with the last scan",
      r.status_code == 200 and r.get_json()["total_images"] > 0, r.status_code)

r = client.post("/api/seo/alt/scan", json={})
check("the scan needs a client", r.status_code == 400)

r = client.post("/api/seo/alt/write", json={"client": "Nobody At All"})
check("writing with nothing scanned says so rather than writing nothing",
      "error" in r.get_json(), r.get_json())

r = client.post("/api/seo/alt/update",
                json={"client": CLIENT, "url": SITE + "/ac-repair",
                      "src": SITE + "/img/logo.png", "alt": "Cool Air Co logo"})
check("an alt can be edited through the route", r.get_json().get("ok"), r.get_json())

r = client.post("/api/seo/publish/instructions",
                json={"client": CLIENT, "cms": "wordpress", "kind": "alt",
                      "urls": [SITE + "/ac-repair"]})
body = r.get_json()
check("the publish route serves the alt kind",
      r.status_code == 200 and body.get("prompt"), r.status_code)
r = client.post("/api/seo/publish/instructions",
                json={"client": CLIENT, "cms": "wordpress", "kind": "podcasts"})
check("and refuses a kind it has no instructions for", r.status_code == 400)

slug = seo.slugify(CLIENT)
r = client.get(f"/seo/alt/{slug}/code.html")
check("the code view downloads", r.status_code == 200, r.status_code)

r = client.get("/seo/client?name=" + CLIENT.replace(" ", "%20"))
html = r.get_data(as_text=True)
check("the SEO client page still renders", r.status_code == 200)
for marker, why in [("cardAlt", "the Alt Text card is on the page"),
                    ('data-kind="alt"', "alt text has both Claude buttons"),
                    ('data-kind="faqs"', "FAQs have both Claude buttons"),
                    ("blogSelAllHead", "the blogs selection column has a header control"),
                    ("seoc-promptbox", "the publish panel has somewhere to show the prompt")]:
    check(why, marker in html)

print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
