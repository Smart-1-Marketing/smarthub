"""hub/gpt_ads_spec.py and the GPT Ads Builder — test harness.

    python3 test_gpt_ads.py

Same shape as test_social_plan.py and test_blog_publish.py: no pytest, no new
dependencies, a throwaway SQLite database and a temporary data directory, so it
never touches /var/data or the real one. The requests seam and the OpenAI seam
are both stubbed, so it needs no credentials and reaches no third party.

## What is actually worth asserting here

Most of this tool is a form, and a form does not need a test. Four things are
not a form, and every one of them fails in the reassuring direction — the pack
looks complete and is not:

  * **The image gate.** The requirement is 1:1, and the ordinary way an ad gets
    rejected is a rep attaching a 1200x628 crop that a form field described as
    square. So the pixels are measured on the bytes, and a non-square file is
    refused at the door rather than becoming a red flag somebody exports
    anyway. Asserted both ways: the square attaches, the rectangle does not.

  * **The copy checks.** A model that writes "$50 off through Friday" produces
    copy that reads perfectly, passes every syntax check and gets the client a
    phone call from someone holding them to an offer they never made. The
    fixtures here are deliberately plausible; the failure mode is not
    gibberish, it is confident and wrong.

  * **The landing check reports what it measured.** "Live and mobile-friendly"
    is on the sheet as something to *confirm*, so a check that never ran, one
    that ran and failed, and one that ran and passed must be three different
    answers — and only the third may read as a tick.

  * **The pack that goes to ad ops is complete, and honest when it is not.**
    The ZIP has to carry the image, the copy sheet, the brief and the manifest;
    and when the image cannot be embedded it has to say so rather than quietly
    shipping three files where there should be four.

Plus the ordinary regressions: deleting a pack must not leave a database copy
to restore itself, and an offer that expired last month must not be markable
as ready to traffic.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-gptads-")
# Set, not setdefault: this file always gets its own throwaway mirror, so it is
# safe to re-run in a job whose DATABASE_URL is already a real Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "gpt-ads-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def png_bytes(width, height, colour=(40, 90, 160)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


SQUARE = png_bytes(1024, 1024)
WIDE = png_bytes(1200, 628)
TINY = png_bytes(200, 200)


# ---------------------------------------------------------------------------
section("The image unit lives in the shared spec kit")
# ---------------------------------------------------------------------------
from hub import creative_specs                                    # noqa: E402
from hub import gpt_ads_spec as spec                              # noqa: E402

unit = creative_specs.BY_ID.get(spec.IMAGE_UNIT)
check("hub/creative_specs.py owns the numbers", unit is not None)
check("1:1 is the required ratio", unit and unit.get("ratios") == [(1, 1)])
check("256x256 is a recommended minimum, not a required size",
      unit and unit.get("min_size") == (256, 256) and not unit.get("size"))
check("no file-weight ceiling is invented", unit and "max_bytes" not in unit)
check("the unit says where its numbers came from", bool(unit and unit.get("source")))

verdict = creative_specs.check(width=1024, height=1024, size_bytes=len(SQUARE),
                               fmt="png", unit_id=spec.IMAGE_UNIT)
check("a 1024 square passes", verdict["result"] == "pass", verdict["summary"])
verdict = creative_specs.check(width=1200, height=628, size_bytes=len(WIDE),
                               fmt="png", unit_id=spec.IMAGE_UNIT)
check("a 1200x628 fails on ratio", verdict["result"] == "fail", verdict["summary"])
verdict = creative_specs.check(width=200, height=200, size_bytes=len(TINY),
                               fmt="png", unit_id=spec.IMAGE_UNIT)
check("a 200 square warns rather than failing — it runs, it runs soft",
      verdict["result"] == "warn", verdict["summary"])
check("a GPT ads product routes to the GPT ads channel",
      creative_specs.channels_for_product("ChatGPT Ads") == ["gpt_ads"])
check("and is not swept up by the generic display pattern",
      creative_specs.channels_for_product("GPT display ads") == ["gpt_ads"])


# ---------------------------------------------------------------------------
section("The copy checks — plausible, and wrong")
# ---------------------------------------------------------------------------
AD = {
    "client": "Riverstone Heating",
    "offer": {"summary": "$89 furnace tune-up for new customers",
              "pricing": "$89", "product": "Furnace maintenance",
              "expires": "2099-11-30", "restrictions": "One per household"},
    "brand": {"approver_name": "Dana Whitfield", "phone": "(704) 555-0142"},
    "landing": {"url": "https://riverstoneheating.example/tuneup"},
}

flags = spec.validate_copy("$89 furnace tune-up", "headline", AD)
check("a price that IS in the offer passes clean",
      not [f for f in flags if f["level"] == "block"], flags)

flags = spec.validate_copy("$49 furnace tune-up", "headline", AD)
check("a price nobody authorised is a block",
      any(f["code"] == "price" and f["level"] == "block" for f in flags), flags)

flags = spec.validate_copy("Book by Friday and save", "body", AD)
check("a deadline nobody authorised is a block",
      any(f["code"] == "deadline" for f in flags), flags)

flags = spec.validate_copy("Call (704) 555-0142 today", "body", AD)
check("the phone number on file is allowed",
      not [f for f in flags if f["code"] == "phone"], flags)

flags = spec.validate_copy("Call (704) 555-9999 now", "body", AD)
check("a phone number that is not on file is a block",
      any(f["code"] == "phone" and f["level"] == "block" for f in flags), flags)

flags = spec.validate_copy("The best HVAC company in town", "headline", AD)
check("an unprovable superlative warns rather than blocking",
      any(f["code"] == "superlative" and f["level"] == "warn" for f in flags), flags)

flags = spec.validate_copy("Heating you can trust from Smart 1 Labs", "headline", AD)
check("the Smart 1 Labs name never reaches a client",
      any(f["code"] == "banned" and f["level"] == "block" for f in flags), flags)

flags = spec.validate_copy("Call [CLIENT NAME] today", "headline", AD)
check("an unfilled placeholder is a block",
      any(f["code"] == "placeholder" for f in flags), flags)

long_headline = "A headline considerably longer than our own working guidance for one"
flags = spec.validate_copy(long_headline, "headline", AD)
length = [f for f in flags if f["code"] == "length"]
check("over our own guidance is a warning, never a rejection",
      length and length[0]["level"] == "warn", flags)
check("and it says whose limit it is",
      length and "no published limit" in length[0]["message"].lower(), flags)


# ---------------------------------------------------------------------------
section("Absent data reads as absent")
# ---------------------------------------------------------------------------
no_image = spec.image_verdict({})
check("no image is 'missing', not a pass", no_image["result"] == "missing")
unmeasured = spec.image_verdict({"url": "https://x/y.png"})
check("an image nobody measured is 'unknown', not a pass",
      unmeasured["result"] == "unknown", unmeasured)

state = spec.readiness(dict(AD, image={"url": "https://x/y.png"}))
img = [s for s in state["sections"] if s["key"] == "image"][0]
check("an unmeasured image blocks the pack",
      img["state"] == "block" and
      any(f["code"] == "image_unmeasured" for f in img["flags"]), img)

landing = [s for s in spec.readiness(AD)["sections"] if s["key"] == "landing"][0]
check("a landing page nobody fetched says 'not measured'",
      any(f["code"] == "landing_unchecked" for f in landing["flags"]), landing)

brief = spec.handoff_brief(AD)
check("the brief names what was never supplied", spec.NOT_SUPPLIED in brief)
check("the brief keeps the requirement sheet's section order",
      brief.index("1. STATIC SQUARE IMAGE") < brief.index("2. AD COPY OPTIONS")
      < brief.index("3. LANDING PAGE") < brief.index("4. BRAND REQUIREMENTS")
      < brief.index("5. OFFER DETAILS"))


# ---------------------------------------------------------------------------
section("An expired offer cannot be trafficked")
# ---------------------------------------------------------------------------
expired = dict(AD, offer=dict(AD["offer"], expires="2020-01-31"))
offer = [s for s in spec.readiness(expired)["sections"] if s["key"] == "offer"][0]
check("an expiry date in the past is a block",
      any(f["code"] == "offer_expired" for f in offer["flags"]), offer)

unreadable = dict(AD, offer=dict(AD["offer"], expires="end of the season"))
offer = [s for s in spec.readiness(unreadable)["sections"] if s["key"] == "offer"][0]
check("a date we cannot read says so rather than guessing",
      any(f["code"] == "offer_expiry_unparsed" and f["level"] == "warn"
          for f in offer["flags"]), offer)


# ---------------------------------------------------------------------------
section("The landing page check reports what it measured")
# ---------------------------------------------------------------------------
import hub.gpt_ads_spec as _spec_mod                              # noqa: E402
import requests                                                   # noqa: E402


class FakeResponse:
    def __init__(self, status=200, text="", url=""):
        self.status_code = status
        self.text = text
        self.url = url
        self.content = text.encode()


_responses = {}
_real_get = requests.get


def fake_get(url, **kw):
    if url in _responses:
        return _responses[url]
    raise requests.exceptions.ConnectionError("stubbed: nothing at " + url)


requests.get = fake_get

MOBILE_PAGE = '<html><head><meta name="viewport" content="width=device-width">' \
              "</head><body>Offer</body></html>"
DESKTOP_PAGE = "<html><head><title>Offer</title></head><body>Offer</body></html>"

_responses["https://live.example/offer"] = FakeResponse(
    200, MOBILE_PAGE, "https://live.example/offer")
_responses["https://desktop.example/offer"] = FakeResponse(
    200, DESKTOP_PAGE, "https://desktop.example/offer")
_responses["https://gone.example/offer"] = FakeResponse(
    404, "not found", "https://gone.example/offer")

result = spec.check_landing_page("https://live.example/offer")
check("a live page with a viewport is reachable and mobile",
      result["checked"] and result["ok"] and result["mobile"] is True, result)
result = spec.check_landing_page("https://desktop.example/offer")
check("a page with no viewport is reported as probably not mobile-friendly",
      result["ok"] and result["mobile"] is False, result)
result = spec.check_landing_page("https://gone.example/offer")
check("a 404 is not reachable", result["checked"] and not result["ok"], result)
result = spec.check_landing_page("https://nothing.example/offer")
check("an unreachable host is a measured failure, not an exception",
      result["checked"] and not result["ok"] and result["note"], result)
check("and it never raises on a blank URL",
      spec.check_landing_page("")["checked"] is False)


# ---------------------------------------------------------------------------
section("The module — a pack from first click to handoff")
# ---------------------------------------------------------------------------
from hub import storage                                           # noqa: E402
import modules.gpt_ads.app as mod                                 # noqa: E402

# ---------------------------------------------------------------------------
# The mount, before anything else touches this app.
#
# The trap CLAUDE.md names first: a module page that works standalone and 404s
# under its mount. tools/linkcheck.py checks every literal in the repo; this
# asserts the routes the page cannot work without, through the real composed
# app with the Hub's own auth in front of them.
#
# It runs here rather than at the end because wsgi.py installs an error
# handler on every mounted app as it composes them, and Flask refuses that
# once an app has served its first request — so importing wsgi after the test
# client below has been used fails on the ordering, not on the mount.
# ---------------------------------------------------------------------------
section("The mount")
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})
    check("the tool answers under its mount",
          composed.get("/tools/gpt-ads/").status_code == 200)
    check("and so does the list its page opens with",
          composed.get("/tools/gpt-ads/api/ads").status_code == 200)
    check("the Creative page links to it",
          b"/tools/gpt-ads/" in composed.get("/creative").data)
except Exception as exc:                                          # noqa: BLE001
    check("the composed app boots with the module mounted", False, exc)


# Cloudinary is not configured in a test run, and hub.storage would fall back
# to the disk. Stubbing it keeps the assertions about what the pack records —
# a URL ad ops can open, and no "this link is not real" warning.
_stored = {}


class FakeAsset:
    backend = "cloudinary"

    def __init__(self, public_id, url):
        self.public_id, self.url = public_id, url
        self.resource_type, self.bytes, self.folder = "image", 0, ""


def fake_put(kind, filename, data, **kw):
    public_id = f"smart1-{kind}/{kw.get('subpath') or 'x'}/{filename}"
    url = "https://res.cloudinary.test/" + public_id
    _stored[url] = data
    _responses[url] = FakeResponse(200, "")
    _responses[url].content = data
    return FakeAsset(public_id, url)


storage.put = fake_put
mod.storage.put = fake_put

mod.app.config["TESTING"] = True
client = mod.app.test_client()

created = client.post("/api/ads/create", json={"client": "Riverstone Heating",
                                               "campaign": "Fall tune-up"}).get_json()
check("a pack is created for the client", created.get("ok") and created["ad"]["id"])
pack_id = created["ad"]["id"]
check("and it starts with every deliverable outstanding",
      created["readiness"]["ready"] is False and created["readiness"]["block"] >= 4,
      created["readiness"])

saved = client.post("/api/ads/save", json={
    "id": pack_id,
    "offer": {"summary": "$89 furnace tune-up for new customers",
              "pricing": "$89", "product": "Furnace maintenance",
              "expires": "2099-11-30", "restrictions": "One per household"},
    "brand": {"approver_name": "Dana Whitfield", "approver_email": "dana@example.com",
              "tone": "Plain and local", "logo_url": "https://x/logo.png",
              "colors": ["#1a2e58"]},
    "landing": {"url": "live.example/offer", "tracking": "utm_source=chatgpt"},
    "copy": {"headlines": [{"text": "$89 furnace tune-up"},
                           {"text": "Beat the first cold snap"},
                           {"text": "Save $200 this week only"}],
             "bodies": [{"text": "Our techs check every burner and flue."},
                        {"text": "Booked in under a minute."}],
             "ctas": [{"text": "Book Now"}, {"text": "Learn More"}]},
}).get_json()
check("saving normalises a bare domain into an https URL",
      saved["ad"]["landing"]["url"] == "https://live.example/offer",
      saved["ad"]["landing"]["url"])
copy_flags = [f for s in saved["readiness"]["sections"] if s["key"] == "copy"
              for f in s["flags"] if f["level"] == "block"]
check("the invented “$200 this week only” headline blocks the pack",
      len(copy_flags) >= 1, copy_flags)

# Drop the invented line and the pack's copy section clears.
saved = client.post("/api/ads/save", json={
    "id": pack_id,
    "copy": {"headlines": [{"text": "$89 furnace tune-up"},
                           {"text": "Beat the first cold snap"},
                           {"text": "Furnace maintenance done right"}],
             "bodies": [{"text": "Our techs check every burner and flue."},
                        {"text": "Booked in under a minute."}],
             "ctas": [{"text": "Book Now"}, {"text": "Learn More"}]},
}).get_json()
copy_section = [s for s in saved["readiness"]["sections"] if s["key"] == "copy"][0]
check("with the invented line gone, the copy deliverable is complete",
      copy_section["state"] == "ok", copy_section)

# ---- the image gate ----
wide = client.post("/api/ads/image/upload", data={
    "id": pack_id, "file": (io.BytesIO(WIDE), "hero.png")},
    content_type="multipart/form-data").get_json()
check("a 1200x628 upload is refused, with its measured size",
      not wide.get("ok") and "1200x628" in wide.get("error", ""), wide)
check("and the refusal points at the tool that crops it",
      "/tools/image/" in wide.get("error", ""), wide)

square = client.post("/api/ads/image/upload", data={
    "id": pack_id, "file": (io.BytesIO(SQUARE), "square.png"), "alt": "A furnace"},
    content_type="multipart/form-data").get_json()
check("a 1024 square attaches", square.get("ok") and square["ad"]["image"]["url"],
      square.get("error"))
check("with its real pixels recorded, not what a form said",
      square["ad"]["image"]["width"] == 1024 and square["ad"]["image"]["height"] == 1024)
image_section = [s for s in square["readiness"]["sections"] if s["key"] == "image"][0]
check("and the image deliverable goes complete", image_section["state"] == "ok",
      image_section)

tiny = client.post("/api/ads/image/upload", data={
    "id": pack_id, "file": (io.BytesIO(TINY), "small.png")},
    content_type="multipart/form-data").get_json()
tiny_section = [s for s in tiny["readiness"]["sections"] if s["key"] == "image"][0]
check("a 200px square attaches but is flagged as soft",
      tiny.get("ok") and tiny_section["state"] == "warn", tiny_section)

# Put the good one back for the export assertions below.
client.post("/api/ads/image/upload", data={
    "id": pack_id, "file": (io.BytesIO(SQUARE), "square.png"), "alt": "A furnace"},
    content_type="multipart/form-data")

# ---- the landing check ----
before = client.post("/api/ads/load", json={"id": pack_id}).get_json()
landing_section = [s for s in before["readiness"]["sections"] if s["key"] == "landing"][0]
check("before the check runs, the landing deliverable is not complete",
      landing_section["state"] != "ok", landing_section)

checked = client.post("/api/ads/landing/check", json={"id": pack_id}).get_json()
landing_section = [s for s in checked["readiness"]["sections"] if s["key"] == "landing"][0]
check("after fetching a live, mobile page it is complete",
      landing_section["state"] == "ok", landing_section)

moved = client.post("/api/ads/save", json={
    "id": pack_id, "landing": {"url": "https://desktop.example/offer"}}).get_json()
check("changing the URL discards the check that belonged to the old one",
      not (moved["ad"]["landing"]["check"] or {}).get("checked"),
      moved["ad"]["landing"]["check"])
client.post("/api/ads/landing/check", json={"id": pack_id})
client.post("/api/ads/save", json={"id": pack_id,
                                   "landing": {"url": "https://live.example/offer"}})
client.post("/api/ads/landing/check", json={"id": pack_id})

# ---- readiness and the status gate ----
state = client.post("/api/ads/load", json={"id": pack_id}).get_json()["readiness"]
check("with all five deliverables in, the pack is ready", state["ready"], state)

r = client.post("/api/ads/status", json={"id": pack_id, "status": "ready"})
check("and it can be marked ready to traffic", r.status_code == 200, r.get_json())

client.post("/api/ads/save", json={"id": pack_id,
                                   "offer": {"expires": "2020-01-31"}})
r = client.post("/api/ads/status", json={"id": pack_id, "status": "ready"})
check("an expired offer cannot be marked ready", r.status_code == 400, r.get_json())
client.post("/api/ads/save", json={"id": pack_id, "offer": {"expires": "2099-11-30"}})


# ---------------------------------------------------------------------------
section("Writing copy — one request per kind, appended not replaced")
# ---------------------------------------------------------------------------
from hub import ai                                                # noqa: E402

_asked = []


def fake_chat_json(messages, **kw):
    _asked.append((kw.get("purpose"), messages))
    purpose = kw.get("purpose", "")
    if purpose.endswith("ctas"):
        # One good, one the platform does not offer. The second must not
        # survive: a CTA ad ops has to translate is one they will guess at.
        return {"options": ["Get a Quote", "Smash that button"]}
    return {"options": ["Furnace tune-ups from a local team",
                        "$89 furnace tune-up",          # a duplicate
                        "Heat that holds through winter"]}


ai.chat_json = fake_chat_json

r = client.post("/api/ads/copy", json={"id": pack_id, "kind": "headlines"}).get_json()
check("writing headlines is one request of its own",
      r.get("ok") and _asked and _asked[-1][0] == "gptad:headlines", _asked[-1:])
headlines = [h["text"] for h in r["ad"]["copy"]["headlines"]]
check("new options are appended, not swapped for what was there",
      "$89 furnace tune-up" in headlines and
      "Furnace tune-ups from a local team" in headlines, headlines)
check("and a repeat of an existing line is not added twice",
      headlines.count("$89 furnace tune-up") == 1, headlines)

prompt = _asked[-1][1][-1]["content"]
check("the prompt carries the offer as the only offer it may mention",
      "$89 furnace tune-up for new customers" in prompt)
check("and says the character guidance is ours, not a platform limit",
      "not a platform limit" in prompt)

r = client.post("/api/ads/copy", json={"id": pack_id, "kind": "ctas"}).get_json()
ctas = [c["text"] for c in r["ad"]["copy"]["ctas"]]
check("a CTA off the approved list is kept", "Get a Quote" in ctas, ctas)
check("and one that is not on it is dropped",
      "Smash that button" not in ctas, ctas)

# An empty pack with no offer must still be writable — and the prompt has to
# say there is no offer rather than leaving the model to fill the gap.
empty = client.post("/api/ads/create", json={"client": "Riverstone Heating"}).get_json()
client.post("/api/ads/copy", json={"id": empty["ad"]["id"], "kind": "bodies"})
check("with no offer supplied the model is told to mention none",
      "Mention no offer, discount or price" in _asked[-1][1][-1]["content"])
client.post("/api/ads/delete", json={"id": empty["ad"]["id"]})


# ---------------------------------------------------------------------------
section("Generating the square")
# ---------------------------------------------------------------------------
def fake_image(prompt, **kw):
    _asked.append(("image", prompt))
    return SQUARE


ai.image = fake_image
r = client.post("/api/ads/image/generate",
                json={"id": pack_id,
                      "image_brief": "A technician servicing a furnace"}).get_json()
check("a generated square attaches and is measured like any other file",
      r.get("ok") and r["ad"]["image"]["width"] == 1024, r.get("error"))
check("it is recorded as generated rather than as the client's own asset",
      r["ad"]["image"]["source"] == "generated", r["ad"]["image"])
image_prompt = [a[1] for a in _asked if a[0] == "image"][-1]
check("the prompt forbids text in the image",
      "no text" in image_prompt.lower(), image_prompt)
check("and it carries the rep's brief",
      "technician servicing a furnace" in image_prompt.lower(), image_prompt)
check("the generated file is asked for square",
      spec.image_prompt({"client": "x"}, {}).lower().count("square") >= 1)

# Put the known bytes back so the ZIP assertions below compare against a file
# this test wrote rather than one the optimiser re-encoded.
client.post("/api/ads/image/upload", data={
    "id": pack_id, "file": (io.BytesIO(SQUARE), "square.png"), "alt": "A furnace"},
    content_type="multipart/form-data")


# ---------------------------------------------------------------------------
section("The handoff pack")
# ---------------------------------------------------------------------------
r = client.post("/api/export.zip", data={"id": pack_id})
check("the export serves a ZIP", r.status_code == 200 and
      r.headers.get("Content-Type", "").startswith("application/zip"),
      r.headers.get("Content-Type"))
check("named for the client", "riverstone-heating" in
      r.headers.get("Content-Disposition", ""), r.headers.get("Content-Disposition"))

zf = zipfile.ZipFile(io.BytesIO(r.data))
names = [n.split("/")[-1] for n in zf.namelist()]
check("carrying the brief", "README-handoff.txt" in names, names)
check("the copy sheet", "ad-copy.csv" in names, names)
check("the manifest", "manifest.json" in names, names)
check("and the square image itself",
      any(n.endswith("-1x1.png") for n in names), names)

blob = zf.read([n for n in zf.namelist() if n.endswith("-1x1.png")][0])
check("the image in the ZIP is the file that was attached", blob == SQUARE)

brief_text = zf.read([n for n in zf.namelist()
                      if n.endswith("README-handoff.txt")][0]).decode()
check("the brief says the pack is ready", "READY TO TRAFFIC" in brief_text)
check("it carries the copy options", "$89 furnace tune-up" in brief_text)
check("it carries the destination URL", "https://live.example/offer" in brief_text)
check("it records the live check rather than claiming one",
      "HTTP 200" in brief_text and "viewport" in brief_text)
check("it names the approval contact", "Dana Whitfield" in brief_text)
check("and it states whose character guidance the copy was written to",
      "house guidance" in brief_text.lower())

manifest = json.loads(zf.read([n for n in zf.namelist()
                               if n.endswith("manifest.json")][0]).decode())
check("the manifest says the pack is trafficable",
      manifest["ready_to_traffic"] is True, manifest["outstanding"])
check("it records the measured spec verdict",
      manifest["image"]["spec_result"] == "pass", manifest["image"])
check("it records that the landing page was checked and live",
      manifest["landing_page"]["checked"] and manifest["landing_page"]["live"],
      manifest["landing_page"])
check("and the copy it carries is the copy in the pack",
      "$89 furnace tune-up" in manifest["copy"]["headlines"], manifest["copy"])

csv_text = zf.read([n for n in zf.namelist()
                    if n.endswith("ad-copy.csv")][0]).decode()
check("the copy sheet has a row per option",
      csv_text.count("\n") >= 8, csv_text)

# ---- the image that cannot be embedded ----
pack = mod.load_pack(pack_id)
cache = os.path.join(mod._image_cache_dir(), pack_id + ".png")
if os.path.isfile(cache):
    os.remove(cache)
_responses.pop(pack["image"]["url"], None)
r = client.post("/api/export.zip", data={"id": pack_id})
zf = zipfile.ZipFile(io.BytesIO(r.data))
names = [n.split("/")[-1] for n in zf.namelist()]
check("a ZIP whose image cannot be fetched still goes",
      r.status_code == 200 and "README-handoff.txt" in names, names)
check("and says so, loudly, rather than shipping three files quietly",
      "IMAGE NOT INCLUDED" in zf.read(
          [n for n in zf.namelist() if n.endswith("README-handoff.txt")][0]).decode())
manifest = json.loads(zf.read([n for n in zf.namelist()
                               if n.endswith("manifest.json")][0]).decode())
check("the manifest records why the image is absent",
      bool(manifest["image"].get("not_included")), manifest["image"])


# ---------------------------------------------------------------------------
section("Storage")
# ---------------------------------------------------------------------------
index = client.get("/api/ads").get_json()["ads"]
check("the pack appears in the index", any(a["id"] == pack_id for a in index))
check("the index row counts rather than trusting a stored total",
      [a for a in index if a["id"] == pack_id][0]["total"] == 5)

check("deleting removes it",
      client.post("/api/ads/delete", json={"id": pack_id}).status_code == 200)
check("and it does not restore itself from the database mirror",
      mod.load_pack(pack_id) is None)
check("nor linger in the index",
      not any(a["id"] == pack_id for a in client.get("/api/ads").get_json()["ads"]))
check("deleting it twice is a 404, not a 500",
      client.post("/api/ads/delete", json={"id": pack_id}).status_code == 404)
check("an unknown id is a 404 everywhere",
      client.post("/api/ads/load", json={"id": "nosuchpack"}).status_code == 404)


requests.get = _real_get

# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
