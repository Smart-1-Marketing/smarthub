"""Commercial Builder — the seven-step wizard, and the six things it used to
get quietly wrong.

    python3 test_commercial_wizard.py

Same shape as test_commercial_heygen.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. It runs with NO provider keys, which is the mock
path — and several of the assertions below are specifically that mock mode
says so rather than producing a plausible answer.

## Why this file exists

Each section guards one failure that was live and invisible from both ends:

  1. **A client picker that could not pick a client.** The Start page listed
     `cb_clients`, this module's own table — empty on a fresh install, and only
     ever holding businesses somebody had typed into it. On a Hub whose client
     book is several hundred businesses in Knack, "pick an existing client" was
     a thing the page appeared to offer and could not do, so a client of eleven
     years' standing got retyped as new and the finished commercial was filed
     under a name that joins to nothing.

  2. **A spot nothing measured against the spec it is sold under.** This tool
     produced finished video for CTV, YouTube and social and never once asked
     `hub/creative_specs.py` about it — while the same file was answering that
     exact question for the IO builder and the client galleries. The kit sells
     Connected TV at 15-30 seconds, so a :05 or a :60 CTV cut is outside the
     buy, and the only way to find out was to have a platform refuse it.

  3. **A QR code with no destination and no owner.** A code was generated from
     whatever the landing page happened to be. Nothing said where it had
     resolved to, nothing refused to build one with nowhere to go, and nothing
     anywhere said which Smart 1 Suite account would count the scan — which is
     a different answer for a client with a sub-account than for a prospect.

  4. **Two AI buttons that read as alternatives.** "Generate AI" makes a still;
     "Generate Video" animates the still it made. Runway has no usable
     text-only path, so the second cannot run before the first — and the pair
     was drawn as two peers with nothing saying so.

  5. **`gpt-image-1` returns b64_json and never a url.** The service read
     `resp.data[0].url` unconditionally, so both options came back empty: the
     picker drew Option A and Option B exactly as it would for a success, and
     clicking either said "This option failed to generate".

  6. **One length per press.** A client almost never wants only a :30, and
     building the :15 afterwards meant walking the wizard again and getting a
     different concept out of it — the same brief, quoted two ways.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbwiz_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbwiz-test-secret"
os.environ["PANEL_PASSWORD"] = "cbwiz-test-password"
# Every provider off. Mock mode is the path under test.
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY", "HEYGEN_API",
           "RUNWAY_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(_k, None)

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


MOUNT = "/tools/commercial-builder"


# ---------------------------------------------------------------------------
# 1. The pieces that do not need an app context
# ---------------------------------------------------------------------------
from modules.commercial_builder import config as cb_config              # noqa: E402
from modules.commercial_builder.services import qc_service              # noqa: E402
from modules.commercial_builder.services import openai_service          # noqa: E402
from modules.commercial_builder.services import elevenlabs_service      # noqa: E402
from hub import creative_specs, qr_codes, voice_casting                 # noqa: E402


section("Social is its own platform, not a third crop")
check("it is offered", any(p["id"] == "social" for p in cb_config.PLATFORMS), True)
check("and recognized", cb_config.is_social("social"), True)
check("CTV is not social", cb_config.is_social("ctv"), False)
# The first beat is the whole difference: a feed has no slot holding the
# viewer in place, so the hook is at zero rather than after an establishing
# shot. If these two ever return the same table, the platform switch has
# stopped changing the script and only the QC messages differ.
check("a social :30 opens on a thumb-stop",
      cb_config.get_structure(30, "social")[0]["label"], "Thumb-stop")
check("a CTV :30 does not",
      cb_config.get_structure(30, "ctv")[0]["label"], "The Hook")
check("no platform given keeps the old structure",
      cb_config.get_structure(30), cb_config.STRUCTURE_TEMPLATES[30])

section("A QR code is offered where nothing can be clicked, and required nowhere")
# It used to be REQUIRED on CTV, and that was this tool insisting on something
# several publishers forbid: Amazon Streaming TV supports no code at all. A
# check that refuses to render a perfectly correct Amazon spot is a check
# somebody switches off. So it is a default now, and an advisory.
check("nothing is required any more", cb_config.QR_CODE_RULES["required_platforms"], [])
check("not required on CTV", cb_config.qr_required(30, "ctv"), False)
check("nor on a CTV+YouTube buy", cb_config.qr_required(30, "both"), False)
check("on by default on CTV", cb_config.qr_default_on(30, "ctv"), True)
check("and on a CTV+YouTube buy", cb_config.qr_default_on(30, "both"), True)
# A feed ad is already tappable, and a code there asks somebody to scan the
# phone they are holding. Defaulting it on there is how a control stops being
# a decision.
check("off by default on social", cb_config.qr_default_on(30, "social"), False)
check("off by default on YouTube alone", cb_config.qr_default_on(30, "youtube"), False)
check("never on a :05, whatever the platform", cb_config.qr_eligible(5), False)
check("nor on a :06 — too short to pull out a phone", cb_config.qr_eligible(6), False)
check("and the default follows the eligibility", cb_config.qr_default_on(6, "ctv"), False)


section("The publisher question exists for one reason, and it is Amazon")
# The smallest possible version of a publisher field. It drives no targeting,
# no spec, no beat structure -- one warning, said while the end card is still
# being built rather than at trafficking.
check("Amazon is offered", any(p["id"] == "amazon" for p in cb_config.CTV_PUBLISHERS), True)
check("so is other/mixed, because most buys are",
      any(p["id"] == "other" for p in cb_config.CTV_PUBLISHERS), True)
check("Amazon refuses a QR code",
      cb_config.publishers_refusing_qr(["roku", "amazon"]), ["Amazon Streaming TV"])
check("Roku alone refuses nothing", cb_config.publishers_refusing_qr(["roku"]), [])
check("and an unknown id is not treated as a refusal",
      cb_config.publishers_refusing_qr(["madeup"]), [])
note = cb_config.publisher_qr_note(["amazon"])
check("the note names the publisher", "Amazon" in note, True)
check("and says what it actually forbids", "QR" in note, True)
# Nothing ticked is not the same as a publisher that allows it. A silent pass
# over an unanswered question is the confident wrong answer this file exists
# to keep undoing.
check("nothing ticked says nothing", cb_config.publisher_qr_note([]), "")
check("labels resolve for the screen",
      cb_config.publisher_labels(["hulu"]), ["Hulu"])

section("The :06 is a real length, not a rounding of the :05")
check("it is offered", 6 in cb_config.COMMERCIAL_LENGTHS, True)
check("and it is a YouTube bumper", "bumper" in cb_config.LENGTH_NOTES[6]["label"].lower()
      or "bumper" in cb_config.LENGTH_NOTES[6].get("note", "").lower(), True)
# A :06 has room for one idea. Sized against the :05's budget it comes back a
# word short of a brand mention; sized against the :15's it cannot be read.
lo6, hi6 = cb_config.VO_WORD_TARGETS[6]
lo5, hi5 = cb_config.VO_WORD_TARGETS[5]
lo15, _ = cb_config.VO_WORD_TARGETS[15]
check("it has its own word budget", (lo6, hi6) != (lo5, hi5), True)
check("bigger than the :05's", lo6 >= lo5, True)
check("and smaller than the :15's", hi6 < lo15, True)
beats6 = [b["label"] for b in cb_config.get_structure(6, "ctv")]
check("two beats, not three", len(beats6), 2)
check("it opens on the hook", beats6[0], "Hook")
check("and ends on the brand", beats6[-1], "Brand")
check("social has its own :06 shape too", bool(cb_config.SOCIAL_STRUCTURE_TEMPLATES.get(6)), True)


section("Several lengths are built in the order they are cut down in")
# :30 first, because the others are cut down from its storyboard, and :60 last
# because it is the most expensive and the first dropped when the budget lands.
check("the order is fixed", cb_config.BUILD_ORDER, [30, 15, 6, 5, 60])
check("the :06 sits between the :15 and the :05",
      cb_config.BUILD_ORDER.index(6) > cb_config.BUILD_ORDER.index(15)
      and cb_config.BUILD_ORDER.index(6) < cb_config.BUILD_ORDER.index(5), True)
check("and sorting a set uses it",
      sorted([60, 5, 30, 6, 15], key=cb_config.build_sort_key), [30, 15, 6, 5, 60])


section("The :60 warning is said at the moment somebody picks it")
warning = cb_config.length_warning(60)
check("a :60 carries one", bool(warning), True)
check("it names the credits", "credits" in warning, True)
check("it names skipping", "skip" in warning, True)
# A note on every length is a note nobody reads, and then the one that
# mattered goes past unread too.
check("a :30 carries none", cb_config.length_warning(30), "")
check("a :15 carries none", cb_config.length_warning(15), "")


# ---------------------------------------------------------------------------
# 2. The published creative specification
# ---------------------------------------------------------------------------
section("The spot is judged against the spec kit, before a frame exists")
check("the kit names where to check it",
      creative_specs.SPEC_KIT_URL, "https://smart1.agency/partner/creative-specs")
check("and the catalog carries it",
      creative_specs.catalogue()["source_url"], creative_specs.SPEC_KIT_URL)

ctv30 = qc_service.spec_preview("ctv", 30, ["16:9"])
check("a :30 CTV cut is inside the buy", ctv30["passed"], True)
check("and the verdict cites the kit", creative_specs.SPEC_KIT_URL in ctv30["message"], True)

# The finding this whole check exists to produce. Both are lengths the Start
# page offers, and both are outside what Connected TV is sold in.
check("a :60 CTV cut is refused", qc_service.spec_preview("ctv", 60, ["16:9"])["passed"], False)
check("a :05 CTV cut is refused", qc_service.spec_preview("ctv", 5, ["16:9"])["passed"], False)
check("a :05 YouTube bumper is fine", qc_service.spec_preview("youtube", 5, ["16:9"])["passed"], True)
check("a :60 YouTube TrueView is fine", qc_service.spec_preview("youtube", 60, ["16:9"])["passed"], True)

# A "both" buy runs one file on CTV *and* YouTube, so satisfying one is not a
# pass. A social buy is bought per network and runs where it fits, so one that
# takes it is — and the ones that would refuse are named rather than dropped.
check("all channels must accept a 'both' buy", cb_config.spec_channel_mode("both"), "all")
check("any network taking it is enough on social", cb_config.spec_channel_mode("social"), "any")
check("a :05 'both' buy is refused, because CTV refuses it",
      qc_service.spec_preview("both", 5, ["16:9"])["passed"], False)
social60 = qc_service.spec_preview("social", 60, ["9:16"])
check("a :60 social cut runs somewhere", social60["passed"], True)
check("and the network that would refuse it is named",
      "Snapchat" in social60["message"], True)

# A crop nobody sells on this buy is named, never judged against the nearest
# channel and reported on — creative_specs.check() draws the same distinction
# with its own "unknown" result.
check("a 1:1 cut of a CTV buy maps to nothing", cb_config.spec_channels("ctv", "1:1"), [])
check("and is reported as not measured, not as a pass",
      "not measured" in qc_service.spec_preview("ctv", 30, ["1:1"])["message"].lower(), True)
# ...and never silently, when it rides alongside a format that did pass.
both_mixed = qc_service.spec_preview("both", 30, ["16:9", "9:16"])
check("a skipped format is named inside a passing verdict",
      "9:16 was not measured" in both_mixed["message"], True)


# ---------------------------------------------------------------------------
# 3. Where a QR code points, and whose scan it is
# ---------------------------------------------------------------------------
section("A QR destination is chosen, never invented")
# The campaign landing page beats the end card's website beats the home page.
# Sending a CTV viewer to a home page when a campaign page exists throws away
# the offer they have just watched.
check("the campaign landing page wins",
      qr_codes.destination(landing_page="acme.com/ac", cta_website="acme.com",
                           client_website="acme.com")["source"], "landing_page")
check("then the website on the card",
      qr_codes.destination(cta_website="acme.com", client_website="x.com")["source"],
      "cta_website")
check("then the client's own site",
      qr_codes.destination(client_website="acme.com")["source"], "client_website")

# The rule modules/ads_builder/logo.py works to: a code that opens the wrong
# company's website is worse than an end card with no code, because nobody
# proof-reads the thing that scans.
nowhere = qr_codes.destination()
check("with nothing on file it refuses", nowhere["url"], "")
check("and names the field that would fix it", "landing page" in nowhere["missing"], True)

section("The tracking travels on the link, and never overwrites a tag")
tracked = qr_codes.tracked_url("acme.com/ac", campaign="Acme :30", platform="ctv")
check("the medium says how they arrived", "utm_medium=qr" in tracked, True)
check("the source says which screen", "utm_source=ctv" in tracked, True)
check("the campaign is slugged", "utm_campaign=acme-30" in tracked, True)
check("social reports as social",
      "utm_source=social" in qr_codes.tracked_url("acme.com", platform="social"), True)
# A landing page handed over already tagged was built that way on purpose, and
# overwriting it re-attributes traffic somebody is already reporting on.
kept = qr_codes.tracked_url("acme.com/ac?utm_campaign=summer", campaign="Winter")
check("an existing tag is kept", "utm_campaign=summer" in kept, True)
check("and not doubled", kept.count("utm_campaign"), 1)
check("nothing to point at means nothing to track", qr_codes.tracked_url(""), "")

section("Which Suite account counts the scan is a tri-state")
own = qr_codes.attribution(client_location_id="loc_123", client_name="Acme")
check("a client with a sub-account gets their own", own["state"], "own")
check("and it is the one on file", own["location_id"], "loc_123")
# With no Suite configured at all, nothing is counting scans anywhere. Drawing
# a tick over that tells somebody the scans are being counted when they are not.
no_suite = qr_codes.attribution()
check("no Suite configured is 'not measured', never a tick", no_suite["state"], "unknown")
check("and says so in words", "Not measured" in no_suite["note"], True)

section("HighLevel publishes no QR endpoint, and the module says so")
plan = qr_codes.plan(landing_page="acme.com/ac", campaign="Acme :30", platform="ctv")
check("the note is carried on every plan", "HighLevel" in plan["provider_note"], True)
check("it explains why the code is rendered here",
      "unpublished" in plan["provider_note"], True)
check("a plan carries a tracked target", "utm_medium=qr" in plan["target_url"], True)
check("and the untracked destination beside it",
      plan["destination_url"], "https://acme.com/ac")


# ---------------------------------------------------------------------------
# 4. Narration that grows with the spot
# ---------------------------------------------------------------------------
section("A longer spot gets more script, inside the budget it actually has")
scenes = [{"narration": "one two three four five", "start": 0, "end": 10},
          {"narration": "six seven eight", "start": 10, "end": 30}]
budget = openai_service.narration_budget(scenes, 30)
check("the words are counted", budget["used"], 8)
check("against this length's target", (budget["target_low"], budget["target_high"]),
      cb_config.VO_WORD_TARGETS[30])
check("and the room is what is left", budget["room"], cb_config.VO_WORD_TARGETS[30][1] - 8)
check("under target is reported", budget["under"], True)

# Floors at zero rather than going negative: a spot already over target has no
# room, and a negative number invites a caller to subtract its way into
# nonsense.
over = openai_service.narration_budget([{"narration": "word " * 200}], 15)
check("a spot over target has no room", over["room"], 0)
check("and is reported as over", over["over"], True)

# A button that appears to work and changes nothing is the thing being fixed.
refused = openai_service.expand_narration([{"narration": "word " * 200}], 15, {}, {})
check("with no room it refuses", refused["scenes"], [])
check("and says why", "no room" in refused["note"], True)
check("with no scenes at all it says that instead",
      "no scenes" in openai_service.expand_narration([], 30, {}, {})["note"], True)
# Mock mode must not read as "nothing to add".
mocked = openai_service.expand_narration(scenes, 30, {}, {})
check("mock mode is named, not silent", "Mock mode" in mocked["note"], True)
check("and still reports the room",
      str(cb_config.VO_WORD_TARGETS[30][1] - 8) in mocked["note"], True)


# ---------------------------------------------------------------------------
# 5. gpt-image-1 returns b64_json, and never a url
# ---------------------------------------------------------------------------
section("A generated still is read whichever way the model returns it")


class _B64Item:
    b64_json = "aGVsbG8="
    url = None


class _UrlItem:
    b64_json = None
    url = "https://example.com/frame.png"


check("b64_json becomes a data URL",
      openai_service._image_result_url(_B64Item()).startswith("data:image/png;base64,"), True)
check("a hosted url is passed through",
      openai_service._image_result_url(_UrlItem()), "https://example.com/frame.png")
check("neither is None, not an exception",
      openai_service._image_result_url(type("E", (), {"b64_json": None, "url": None})()), None)

# Mock mode still hands back two pickable options, so the picker can be
# exercised without a key — and each is flagged as mock.
mock_options = openai_service.generate_ai_stills("a van in a driveway", {})
check("two options in mock mode", len(mock_options), 2)
check("both are pickable", all(o["url"] for o in mock_options), True)
check("and both are flagged as mock", all(o.get("_mock") for o in mock_options), True)


# ---------------------------------------------------------------------------
# 6. Casting a voice, the way the radio builder does
# ---------------------------------------------------------------------------
section("One casting question, shared with the Radio Promo builder")
from modules.radio_promo import catalog as radio_catalog                # noqa: E402
from modules.radio_promo import voices as radio_voices                  # noqa: E402

# There is one copy of these tables. Two would agree only for as long as
# somebody kept them in step, and the same ElevenLabs account would then be
# scored two different ways depending on which tool was open.
check("the radio builder reads the shared characteristics",
      radio_catalog.VOICE_CHARACTERISTICS is voice_casting.CHARACTERISTICS, True)
check("and the shared energy table",
      radio_voices.ENERGY_WORDS is voice_casting.ENERGY_WORDS, True)
check("and the shared style mapping",
      radio_voices.STYLE_BY_ENERGY is voice_casting.STYLE_BY_ENERGY, True)
check("five things are asked", len(voice_casting.CHARACTERISTICS), 5)

matched, note = elevenlabs_service.cast_voices(
    {"gender": "female", "energy": "energetic", "delivery": "spokesperson"}, 3)
check("three voices come back", len(matched), 3)
check("best first", matched[0]["score"] >= matched[-1]["score"], True)
check("and each says what it matched on", bool(matched[0]["match_reasons"]), True)
check("the note explains the ranking", note.startswith("Ranked on"), True)

# The ranking is a ranking, never a filter. An account whose voices carry no
# labels — which is every account with cloned voices on it — must not come back
# empty from a question that was answered perfectly well.
bare = [{"voice_id": f"v{i}", "name": f"Voice {i}", "labels": {}} for i in range(4)]
ranked = voice_casting.match(bare, {"gender": "female"}, 3)
check("unlabeled voices still rank", len(ranked), 3)
check("and the note says the ranking is not one",
      "own order" in voice_casting.match_quality(ranked, len(bare)), True)
check("an empty account is a different answer",
      "No voices came back" in voice_casting.match_quality([], 0), True)


# ---------------------------------------------------------------------------
# 7. The wizard itself, through the composed app
# ---------------------------------------------------------------------------
section("The seven steps, through the app as it is actually mounted")

import werkzeug.test                                                    # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from modules.commercial_builder.db import db as cb_db                   # noqa: E402
from modules.commercial_builder.models import (                         # noqa: E402
    CommercialProject as CommercialProject_cls, RenderJob as RenderJob_cls)

client = werkzeug.test.Client(application)
client.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})


def get_json(path):
    return client.get(path).get_json()


def post_json(path, body=None, method="post"):
    fn = client.post if method == "post" else client.put
    return fn(path, data=json.dumps(body or {}),
              headers={"Content-Type": "application/json"})


start_page = client.get(MOUNT + "/new").get_data(as_text=True)
check("the Start page answers", client.get(MOUNT + "/new").status_code, 200)
# The client picker that could not pick a client. All three ways in have to be
# on the page, and the first one has to be the search.
check("it searches the agency's client list", 'id="hub-q"' in start_page, True)
check("a brand profile is still offered", 'data-value="profile"' in start_page, True)
check("and a new business is still offered", 'data-value="new"' in start_page, True)
check("Social is one of the platforms", "Social — Meta, TikTok, Reels" in start_page, True)

# help_dot renders `<span data-help="key">` and hub-help.js swaps it. A key not
# in the registry is removed client-side rather than left as a dead "?", so the
# assertion that matters is that every key placed here actually resolves.
import re                                                              # noqa: E402
from hub import help as help_registry                                  # noqa: E402

placed = set()
for page in ("commercial_new.html", "commercial_blueprint.html",
             "commercial_voice.html", "commercial_cta.html", "commercial_brief.html"):
    text = (ROOT / "modules/commercial_builder/templates" / page).read_text()
    placed |= set(re.findall(r"help_dot\('([^']+)'\)", text))
known = {h.key for h in help_registry.REGISTRY}
check("every bubble placed on a screen resolves to content",
      sorted(placed - known), [])
check("and there are bubbles to place", len(placed) >= 10, True)

# A screen that names a tour it has no steps for falls back to the module-wide
# one, which is what put Smart 1 Ads' scenario on two screens where none of its
# selectors existed. Every screen declaring data-screen must own its steps.
declared = {}
for page in ("commercial_new.html", "commercial_blueprint.html",
             "commercial_voice.html", "commercial_cta.html",
             "commercial_brief.html", "commercial_concepts.html",
             "commercial_preview.html"):
    text = (ROOT / "modules/commercial_builder/templates" / page).read_text()
    found = re.findall(r'{% set screen = "([^"]+)" %}', text)
    if found:
        declared[page] = found[0]
orphans = [s for s in declared.values()
           if not [h for h in help_registry.REGISTRY
                   if h.step and h.key.startswith(s + ".")]]
check("no screen offers a tour it has no steps for", orphans, [])
check("four screens carry one", len(declared), 4)

# Every tour step's selector has to exist on that screen's own template, or the
# step keeps its narration and silently hides the ring.
SCREEN_TEMPLATE = {
    "commercial_builder.start": "commercial_new.html",
    "commercial_builder.blueprint": "commercial_blueprint.html",
    "commercial_builder.voice": "commercial_voice.html",
    "commercial_builder.cta": "commercial_cta.html",
}
unanchored = []
for screen, page in SCREEN_TEMPLATE.items():
    text = (ROOT / "modules/commercial_builder/templates" / page).read_text()
    for step in help_registry.tour(screen):
        sel = step["selector"]
        if not sel:
            continue
        if sel.startswith("#"):
            hit = f'id="{sel[1:]}"' in text
        elif sel.startswith("["):
            hit = sel.strip("[]").replace("'", '"') in text
        else:
            hit = sel.lstrip(".") in text
        if not hit:
            unanchored.append(f"{screen} -> {sel}")
check("every tour step is anchored on its own screen", unanchored, [])

section("One press, several lengths, one concept")
created = post_json(MOUNT + "/api/clients",
                    {"name": "Wizard Test HVAC", "website": "wizardtest.example"})
client_id = created.get_json()["client"]["id"]

started = post_json(MOUNT + "/api/projects", {
    "client_id": client_id, "lengths": [15, 30, 60], "formats": ["16:9"],
    "commercial_type": "stock_vo", "platform": "ctv"})
check("three lengths start three commercials", started.status_code, 201)
payload = started.get_json()
check("one per length", len(payload["projects"]), 3)
# Not shortest-first: the :30 is the length the others are cut down from, so
# it is the one to get approved before anything else is built.
check("built :30 first, then :15, then :60",
      [p["length_seconds"] for p in payload["projects"]], [30, 15, 60])
# Tied together so they share a concept: building the :15 afterwards means
# walking the wizard again and getting a different idea out of it.
check("they share a campaign", bool(payload["campaign_id"]), True)
kinds = {n["kind"] for n in payload["notes"]}
check("the :60's cost is reported back", "cost" in kinds, True)
check("so is the CTV spec finding", "spec" in kinds, True)

# The old single-length shape still works — everything that already calls this
# route sends length_seconds.
one = post_json(MOUNT + "/api/projects", {
    "client_id": client_id, "length_seconds": 30, "formats": ["16:9"],
    "commercial_type": "stock_vo", "platform": "ctv"})
check("one length still starts one", len(one.get_json()["projects"]), 1)
check("and no campaign is invented for it", one.get_json()["campaign_id"], None)

refused = post_json(MOUNT + "/api/projects", {
    "client_id": client_id, "lengths": [7, 99], "formats": ["16:9"],
    "commercial_type": "stock_vo"})
check("an unknown length is refused, not rounded", refused.status_code, 400)

# By length, not by index: the index of the :30 moved when the build order
# changed from ascending to BUILD_ORDER, and a test that picks position 1 and
# calls it "the :30" starts asserting things about a different spot.
pid = next(p["id"] for p in payload["projects"] if p["length_seconds"] == 30)

section("Every step answers, and the old address still resolves")
for step in ("brief", "blueprint", "voice", "cta", "preview"):
    check(f"/{step}", client.get(f"{MOUNT}/project/{pid}/{step}").status_code, 200)
# That URL is in browser history. A wizard step that 404s reads as the whole
# tool being broken.
moved = client.get(f"{MOUNT}/project/{pid}/storyboard")
check("/storyboard redirects", moved.status_code, 302)
check("...to the blueprint", "blueprint" in moved.headers["Location"], True)

section("The QR plan is answered before anything is saved")
post_json(MOUNT + f"/api/projects/{pid}/brief",
          {"what_advertising": "$79 tune-up", "landing_page": "wizardtest.example/ac"},
          method="put")
qr = get_json(MOUNT + f"/api/projects/{pid}/qr-plan")
# Required nowhere now — several publishers take no code at all, so a check
# that blocked a render over its absence would insist on something Amazon
# forbids. On by default on CTV, and advisory.
check("it is not required on this CTV spot", qr["required"], False)
check("but it is on by default", qr["default_on"], True)
check("the destination is the landing page", qr["plan"]["destination_source"], "landing_page")
check("tracked", "utm_medium=qr" in qr["plan"]["target_url"], True)

saved = post_json(MOUNT + f"/api/projects/{pid}/cta",
                  {"style": "logo_centered", "qr_enabled": True}, method="put")
cta = saved.get_json()["cta"]
check("the code is generated", cta["qr_data_url"].startswith("data:image/png;base64,"), True)
# Both were absent from the saved CTA entirely, so nothing downstream could
# report where the code pointed or who owned the scan.
check("the destination is recorded on it", bool(cta["qr_destination_url"]), True)
check("so is the attribution", cta["qr_attribution"]["state"] in ("own", "agency", "unknown"), True)

check("and no placeholder is ever passed off as one",
      "placehold.co" in (cta.get("qr_data_url") or ""), False)
# A picture that reads as a QR code and scans to nothing goes onto the end
# card of a CTV spot, where the code is the only response mechanism — and it
# walked straight past the QC check written for exactly this, because a
# truthy placeholder is indistinguishable from a real code to `if not
# cta.get("qr_data_url")`.
import modules.commercial_builder.services.qrcode_service as _qr           # noqa: E402
_was = _qr._AVAILABLE
try:
    _qr._AVAILABLE = False
    _absent = _qr.generate_qr("https://example.test")
finally:
    _qr._AVAILABLE = _was
check("a missing dependency draws no code at all", _absent["data_url"], None)
check("and says why rather than leaving a blank",
      "qrcode" in (_absent.get("error") or "").lower(), True)
check("so QC's enabled-but-not-generated block still bites",
      qc_service._check_qr_code({"length_seconds": 30, "platform": "ctv",
                             "cta": {"qr_enabled": True}}, [])["level"], "fail")


section("The QR image reaches storage, rather than a path that cannot exist")
# routes/projects.py hands upload_asset a BytesIO. It took a path or a URL:
# str(BytesIO) is "<_io.BytesIO object at 0x7f...>", open() raised
# FileNotFoundError on that, and its own `except Exception` turned it into a
# quiet {"secure_url": None}. So qr_image_url was never once populated on any
# spot, and the failure was swallowed a second time at the call site by an
# `or` that never read `error`.
import io as _io                                                           # noqa: E402
import types as _types                                                     # noqa: E402
from modules.commercial_builder.services import cloudinary_service as _cs  # noqa: E402

check("bytes are read as bytes", _cs._read_bytes(b"abc"), b"abc")
check("and so is an open file", _cs._read_bytes(_io.BytesIO(b"png")), b"png")
# A caller that has already read the stream would otherwise store nothing,
# which is the same silent-empty failure one layer down.
_used = _io.BytesIO(b"already-read")
_used.read()
check("one that was already read is rewound, not stored empty",
      _cs._read_bytes(_used), b"already-read")
check("a path still names a place", _cs._read_bytes("/tmp/x.png"), None)
check("and so does a URL", _cs._read_bytes("https://x.test/y.png"), None)

# The whole point: bytes must reach storage.put, not open(). Stubbed rather
# than uploaded, because this asserts which branch is taken.
_seen = {}
class _StubAsset:
    url = "https://res.cloudinary.test/acme/logos/p-1-qr.png"
    public_id = "acme/logos/p-1-qr"
_stub = _types.SimpleNamespace(
    put=lambda kind, name, data, **kw: (
        _seen.update(call="put", name=name, data=data, public_id=kw.get("public_id")),
        _StubAsset())[1],
    put_remote=lambda *a, **kw: (_seen.update(call="put_remote"), _StubAsset())[1])
_real_live, _cs.is_live = _cs.is_live, lambda: True
_real_cfg, _cs._ensure_configured = _cs._ensure_configured, lambda: None
# `from hub import storage` reads the attribute on the package, so patching
# sys.modules alone leaves the real one in play — which it did, and the four
# assertions below came back None while the upload quietly succeeded for real.
import hub as _hub                                                         # noqa: E402
_real_storage, _hub.storage = _hub.storage, _stub
try:
    _out = _cs.upload_asset(_io.BytesIO(b"\x89PNG-real-qr"), "acme", "logo",
                            public_id="p-1-qr", resource_type="image",
                            filename="qr.png")
finally:
    _cs.is_live, _cs._ensure_configured = _real_live, _real_cfg
    _hub.storage = _real_storage

check("bytes go to storage.put", _seen.get("call"), "put")
check("carrying the actual image", _seen.get("data"), b"\x89PNG-real-qr")
# Bytes carry no name, and the extension is what the format is read from —
# so it is asked for rather than guessed, since a guess puts .png on an MP3.
check("named by what the caller passed", _seen.get("name"), "qr.png")
check("filed where it always was", _seen.get("public_id"), "acme/logos/p-1-qr")
check("and a URL comes back", bool(_out.get("secure_url")), True)
check("with no error", _out.get("error"), None)


section("One reading of which lengths carry a logo bug")
# Four readings before this: the table, a function nothing called, two call
# sites asking the QR table instead (right by coincidence — both hold the
# same three lengths), and creatomate asking `length_seconds != 5`, a literal
# that had already stopped agreeing the day the :06 arrived.
check("the table is the answer", cb_config.logo_persistence_eligible(30), True)
check("a :05 carries none", cb_config.logo_persistence_eligible(5), False)
check("and neither does the :06", cb_config.logo_persistence_eligible(6), False)
check("the copy names both rather than one", cb_config.short_form_phrase(), ":05 and :06")
check("QC answers a :06 with both named",
      ":05 and :06" in qc_service._check_logo_persistence({"length_seconds": 6})["message"], True)
_creato_src = (ROOT / "modules/commercial_builder/services/creatomate_service.py").read_text()
# Read as CODE, not as text. The comment in that file explains the literal it
# replaced, and a text match reports the explanation as the defect — the rule
# hub/config.py's drift check and the image-audit producer check both work to.
import ast as _ast                                                       # noqa: E402


def _compares_length_to(src, value):
    for node in _ast.walk(_ast.parse(src)):
        if not isinstance(node, _ast.Compare):
            continue
        parts = [node.left, *node.comparators]
        names = {getattr(n, "id", "") for n in parts}
        consts = {getattr(n, "value", None) for n in parts
                  if isinstance(n, _ast.Constant)}
        if "length_seconds" in names and value in consts:
            return True
    return False


check("the renderer keeps no literal of its own",
      _compares_length_to(_creato_src, 5), False)
check("and the check reads code rather than prose",
      _compares_length_to("if length_seconds != 5: pass", 5), True)
check("it asks the table",
      "logo_persistence_eligible(length_seconds)" in _creato_src, True)
# The two questions are separate on purpose: a QR code is a response
# mechanism and needs seconds on screen, a logo bug is brand recall and needs
# none. That they agree today is a fact about the tables, not a rule.
_proj = (ROOT / "modules/commercial_builder/routes/projects.py").read_text()
check("the CTA route asks each of its own table",
      "logo_ok = logo_persistence_eligible(" in _proj, True)


section("Severity is the server's answer, not each screen's")
# Two JS files each kept an ADVISORY set by hand — two copies of a decision
# qc_service has every fact to make, and the fastest way to have one panel
# draw a finding red while the other drew the same finding amber.
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text()
    check(f"{js_file} keeps no advisory list of its own", "ADVISORY = new Set" in text, False)
    check(f"{js_file} reads the level off the result", "result.level" in text, True)
qc_lv = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
levels = {v.get("level") for k, v in qc_lv.items()
          if not k.startswith("_") and isinstance(v, dict)}
check("every check carries one", None in levels, False)
check("and only the three we draw", levels - {"pass", "warn", "fail"}, set())
# A recommendation must not block a render. _all_passed used to mean "nothing
# is amber either", which made a page of red out of a page of advice.
check("a recommendation does not block", set(cb_config.__dict__) and
      all(qc_lv[k].get("level") != "fail" for k in qc_service.ADVISORY_CHECKS
          if k in qc_lv), True)
check("warnings are listed apart from failures", isinstance(qc_lv.get("_warnings"), list), True)


section("Amazon takes no QR code, and the tool says so before the render")
# The one thing the publisher field exists for. Switching a code on for an
# Amazon buy builds something Amazon rejects, and nothing anywhere said so.
post_json(MOUNT + f"/api/projects/{pid}/brief", {"publishers": ["amazon"]}, method="put")
post_json(MOUNT + f"/api/projects/{pid}/cta",
          {"style": "logo_centered", "qr_enabled": True}, method="put")
amz = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]["publisher_rules"]
check("the check fails", amz["passed"], False)
check("as a warning, not a refusal", amz["level"], "warn")
check("and it names Amazon", "Amazon" in amz["message"], True)
# It must not block: the spot may be perfectly correct and the rep may have a
# reason. A check that refuses to render is a check somebody switches off.
check("it does not block the render",
      "publisher_rules" in qc_service.ADVISORY_CHECKS, True)

# Saving a brief must MERGE. It used to assign, so the publishers picked on
# the Start page were wiped by the first save on the Brief step -- and the
# Amazon warning then silently stopped firing, with every screen healthy.
post_json(MOUNT + f"/api/projects/{pid}/brief", {"what_advertising": "$79 tune-up"},
          method="put")
still = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]["publisher_rules"]
check("and it survives the next save of the brief", "Amazon" in still["message"], True)

# Turn the code off and the same buy is clean. A warning that cannot be
# cleared is a warning people learn to scroll past.
post_json(MOUNT + f"/api/projects/{pid}/cta",
          {"style": "logo_centered", "qr_enabled": False}, method="put")
off = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]["publisher_rules"]
check("with no code it passes", off["passed"], True)


section("The plan is scored against somebody else's published numbers")
from modules.commercial_builder.services import abcd_service            # noqa: E402

check("every threshold names its source",
      sorted({v["source"] for v in abcd_service.THRESHOLDS.values()} - set(abcd_service.SOURCES)),
      [])
# The one number nobody publishes. Attributing a type size to a platform that
# has never stated one is the exact failure this module exists to avoid, so
# ours is kept apart and labelled as ours.
check("the house standard says it is ours",
      "not a platform rule" in abcd_service.HOUSE_LEGIBILITY["note"], True)
check("and is not in the platform table",
      "cap_height_pct" in abcd_service.THRESHOLDS, False)

# A :30 built one scene per beat is three shots averaging ten seconds. That is
# the case this whole section is about.
three = [{"start": 0, "end": 10, "visual": "wide shot of the shop"},
         {"start": 10, "end": 20, "visual": "technician working"},
         {"start": 20, "end": 30, "visual": "logo and phone number"}]
slow = abcd_service.score(three, 30, "ctv")
check("three shots in a :30 fails the pacing threshold",
      next(r["passed"] for r in slow["rows"] if r["key"] == "avg_shot_seconds"), False)
check("and the headline counts what it met", slow["score"] < slow["of"], True)

quick = [{"start": i * 2.0, "end": i * 2.0 + 2.0,
          "visual": "logo on the van" if i == 0 else "technician working"}
         for i in range(15)]
fast = abcd_service.score(quick, 30, "ctv")
check("fifteen two-second shots meets all of them", fast["score"], fast["of"])
check("and says so", "all" in fast["headline"], True)

# Not measured is its own answer and is never a pass. Face and logo size need
# the rendered frame, and a green tick over a rule nothing checked is exactly
# the confident wrong answer this codebase keeps undoing.
unmeasurable = {r["key"] for r in fast["rows"] if not r["measured"]}
check("face size cannot be read off a plan", "face_frame_pct" in unmeasurable, True)
check("nor logo size", "logo_frame_pct" in unmeasurable, True)
check("and neither counts toward the score", fast["of"], len(fast["rows"]) - len(unmeasurable))

# Amazon's brand window is tighter than Google's, and a CTV spot is judged
# against the one that will actually refuse it.
ctv_keys = {r["key"] for r in abcd_service.score(quick, 30, "ctv")["rows"]}
yt_keys = {r["key"] for r in abcd_service.score(quick, 30, "youtube")["rows"]}
check("CTV is held to Amazon's window", "brand_by_seconds_ctv" in ctv_keys, True)
check("YouTube to Google's", "brand_by_seconds" in yt_keys, True)
check("and Amazon's is the tighter of the two",
      abcd_service.THRESHOLDS["brand_by_seconds_ctv"]["value"]
      < abcd_service.THRESHOLDS["brand_by_seconds"]["value"], True)

# A brand window measured off shots that mention neither the brand nor the
# product is not a window, it is a guess.
mute = abcd_service.score([{"start": 0, "end": 2, "visual": "a road at dawn"}], 30, "ctv")
brand_row = next(r for r in mute["rows"] if r["key"].startswith("brand_by_seconds"))
check("nothing describing the brand is not measured", brand_row["measured"], False)
check("and never a pass", brand_row["passed"], False)

# A bumper is one idea held still. Cutting a :06 to a two-second average is
# three cuts a second, which is a strobe.
bump = abcd_service.score([{"start": 0, "end": 6, "visual": "logo"}], 6, "youtube")
check("a bumper is not scored on pacing",
      next(r["measured"] for r in bump["rows"] if r["key"] == "avg_shot_seconds"), False)
check("and the shot target says so", "bumper" in abcd_service.shot_targets(6)["note"], True)
check("a :30 wants about fifteen shots",
      abcd_service.shot_targets(30)["low"] <= 15 <= abcd_service.shot_targets(30)["high"], True)

# Never raises: this runs inside QC, and a scoring bug must not take the panel
# down on a screen somebody is working on.
check("garbage in does not raise", abcd_service.score([None, {}], 30, "ctv")["measured"] in (True, False), True)
check("and no shots at all is an answer", abcd_service.score([], 30, "ctv")["score"], 0)


section("A scene is a shot now, and it carries the grammar")
# Mock mode writes the shots, which is the point: a :30 that came back as
# three ten-second scenes is the case the whole shot layer is about.
_cons = post_json(MOUNT + f"/api/projects/{pid}/concepts").get_json()["concepts"]
post_json(MOUNT + f"/api/projects/{pid}/select-concept", {"concept_id": _cons[0]["id"]})
post_json(MOUNT + f"/api/projects/{pid}/script")
scene_rows = get_json(MOUNT + f"/api/projects/{pid}/scenes")["scenes"]
check("there are shots to look at", bool(scene_rows), True)
first = scene_rows[0]
meta = first.get("asset_meta") or {}
check("each knows which beat it belongs to", "beat_index" in meta, True)
# Numbered in tens, the way an edit list is: a shot inserted between 20 and 30
# becomes 25 rather than renumbering everything after it.
check("and carries a shot number", meta.get("shot_no"), cb_config.SHOT_NUMBER_STEP)
check("the numbering leaves room to insert",
      [((s.get("asset_meta") or {}).get("shot_no")) for s in scene_rows[:2]],
      [cb_config.SHOT_NUMBER_STEP, cb_config.SHOT_NUMBER_STEP * 2])

# The grammar is three fields and they are not decoration: what sits
# downstream of a shot is a stock query and a Runway prompt, and both are the
# difference between "technician working" and "close-up, low angle, slow push".
put = post_json(MOUNT + f"/api/projects/{pid}/scenes/{first['id']}",
                {"grammar": {"size": "cu", "angle": "low", "move": "push"}}, method="put")
check("it saves", put.status_code, 200)
saved_meta = put.get_json()["scene"]["asset_meta"]["grammar"]
check("the size sticks", saved_meta["size"], "cu")
check("the angle sticks", saved_meta["angle"], "low")
check("the move sticks", saved_meta["move"], "push")
# A value not in the vocabulary is refused rather than written through: these
# reach a stock search and an AI prompt, and "size: banana" is a query.
bad = post_json(MOUNT + f"/api/projects/{pid}/scenes/{first['id']}",
                {"grammar": {"size": "banana", "angle": "low", "move": "push"}}, method="put")
check("a made-up size falls back to the default",
      bad.get_json()["scene"]["asset_meta"]["grammar"]["size"],
      cb_config.DEFAULT_SHOT_GRAMMAR["size"])
check("the label reads as a shot line",
      cb_config.shot_label({"size": "cu", "angle": "low", "move": "push"}).count(",") >= 1, True)
check("and an empty grammar labels nothing", cb_config.shot_label({}), "")


section("The scoring panel is its own route, not a slice of QC")
# QC makes an OpenAI call for the spelling pass. Re-running the whole set on
# every camera-angle change would be a model call per keystroke.
scored = get_json(MOUNT + f"/api/projects/{pid}/abcd")
check("it answers", scored["ok"], True)
check("with rows", isinstance(scored["abcd"]["rows"], list), True)
check("the shot target travels with it", "low" in scored["targets"], True)
check("so does the measured lift", bool(scored["lift"]), True)
check("and every lift row says what it means here",
      all(r.get("means") for r in scored["lift"]), True)


section("The checks run where the work is, and cover every key")
qc = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
check("the spec check ran", "creative_spec" in qc, True)
check("the feed-hook check ran", "social_hook" in qc, True)
check("the sound-off check ran", "sound_off" in qc, True)

# A check absent from a screen's label map is skipped silently by the render
# loop — which is how `scene_assets`, the check that catches an unfinished
# scene, never appeared on the panel it was written for.
# Keys beginning with an underscore are the panel's own payload — the
# all-passed flag, the warning list, the ABCD block — rather than checks,
# and neither screen draws them as rows.
keys = {k for k in qc if not k.startswith("_")}
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text()
    block_start = text.index("QC_LABELS = {")
    block = text[block_start:text.index("};", block_start)]
    labelled = set(re.findall(r"(\w+):\s*\"", block))
    check(f"{js_file} labels every check run_qc returns", sorted(keys - labelled), [])

# ---------------------------------------------------------------------------
# 8. Rendering: the crash, one size at a time, and approval as the thing that
#    files it.
# ---------------------------------------------------------------------------
section("Render answers at all")
# It did not. `submit_render` read `project.name` and `project.length` —
# attributes CommercialProject does not have; it has `title` and
# `length_seconds` — so the route raised AttributeError before the QC gate,
# before Creatomate, before anything. The 500 came back as HTML, CB.api could
# not parse it as JSON, and a three-second toast said "Bad response from
# server". Press Render, nothing happens. No commercial had ever rendered.
check("CommercialProject still has no .name",
      hasattr(CommercialProject_cls, "name"), False)
check("nor .length", hasattr(CommercialProject_cls, "length"), False)

rendered = post_json(MOUNT + f"/api/projects/{pid}/render",
                     {"format": "16:9", "force_despite_qc_failures": True})
check("a render is accepted", rendered.status_code, 200)
render_payload = rendered.get_json()
check("one job comes back", len(render_payload["render_jobs"]), 1)
# Mock mode reports "succeeded" and produces no file. A panel drawing that as
# a finished render is the confident wrong answer.
check("mock mode is named rather than passed off as success",
      "mock" in (render_payload.get("note") or "").lower(), True)
job_id = render_payload["render_jobs"][0]["id"]

section("One size at a time, until one of them has been approved")
# Three renders at once means the second and third come off a storyboard
# nobody has watched: a note on the first applies to two cuts already paid for.
# That is a statement about UNWATCHED creative, so the gate is an approval on
# the spot rather than a count — and before one exists a batch is refused by
# name, with what would lift the refusal.
many = post_json(MOUNT + f"/api/projects/{pid}/render",
                 {"formats": ["16:9", "9:16"], "force_despite_qc_failures": True})
check("two sizes before anything is approved is refused", many.status_code, 409)
check("and says what would open it",
      "approved" in many.get_json()["error"].lower(), True)
check("named as a state rather than a generic refusal",
      many.get_json().get("needs_approval_first"), True)
bad_fmt = post_json(MOUNT + f"/api/projects/{pid}/render",
                    {"format": "4:3", "force_despite_qc_failures": True})
check("an unknown size is refused", bad_fmt.status_code, 400)
# A size ticked twice is one render, not two — and it must not read as a
# batch and be refused for it.
dupe = post_json(MOUNT + f"/api/projects/{pid}/render",
                 {"formats": ["16:9", "16:9"], "force_despite_qc_failures": True})
check("the same size twice is one render", dupe.status_code, 200)
check("and one job", len(dupe.get_json()["render_jobs"]), 1)
check("no size at all is refused", post_json(
    MOUNT + f"/api/projects/{pid}/render", {"formats": []}).status_code, 400)

section("Approving is what files it, and only a real file can be approved")
# Approving a mock would file nothing into the client's library and log it as
# a delivered commercial — a clean tick over an empty gallery.
mock_approve = post_json(MOUNT + f"/api/projects/{pid}/render-jobs/{job_id}/approve")
check("a render with no file cannot be approved", mock_approve.status_code, 400)
check("and the reason is the missing file",
      "no file" in mock_approve.get_json()["error"].lower(), True)

with hub_app.app_context():
    _job = RenderJob_cls.query.get(job_id)
    _job.output_url = "https://cdn.example/render.mp4"
    cb_db.session.commit()

approved = post_json(MOUNT + f"/api/projects/{pid}/render-jobs/{job_id}/approve")
check("a finished render approves", approved.status_code, 200)
ap = approved.get_json()
# "Filed" and "filed in one of two places" are different outcomes, and one
# tick over both is how somebody learns not to trust the tick.
check("the approval records who and where", "filed_to_client" in ap["approval"], True)
check("and whether the video was stored", "stored_url" in ap["approval"], True)
# This spot asked for one size, so approving it leaves nothing — and the
# field says so rather than being absent.
check("nothing is left to render on a one-size spot", ap["remaining_formats"], [])
# Approving the :30 is not the end of the job when several lengths were
# started together — the next one is waiting at its Blueprint.
check("the next spot in the campaign is handed over", ap["next"]["length_seconds"], 15)
check("as a Blueprint URL", "blueprint" in ap["next"]["url"], True)
check("approving twice does not file twice",
      post_json(MOUNT + f"/api/projects/{pid}/render-jobs/{job_id}/approve")
      .get_json().get("already"), True)

listed = get_json(MOUNT + f"/api/projects/{pid}/render-jobs")
check("the job list carries its approval", bool(listed["render_jobs"][0]["approval"]), True)
check("and which formats are approved", listed["approved_formats"], ["16:9"])
check("and a one-size spot never offers a batch", listed["can_batch"], False)


section("Once one cut is approved, the rest go together")
# The remaining formats come off a storyboard somebody has watched, which is
# exactly the condition the one-at-a-time rule was protecting. Whether the
# batch is open is the server's answer, so the panel does not carry a second
# reading of the rule.
three = post_json(MOUNT + "/api/projects", {
    "client_id": client_id, "lengths": [30], "formats": ["16:9", "9:16", "1:1"],
    "commercial_type": "stock_vo", "platform": "social"}).get_json()["projects"][0]["id"]
first = post_json(MOUNT + f"/api/projects/{three}/render",
                  {"format": "16:9", "force_despite_qc_failures": True}).get_json()
first_job = first["render_jobs"][0]["id"]
with hub_app.app_context():
    _j = RenderJob_cls.query.get(first_job)
    _j.output_url = "https://cdn.example/three-16x9.mp4"
    cb_db.session.commit()
early = get_json(MOUNT + f"/api/projects/{three}/render-jobs")
check("nothing rendered is not enough to open it", early["can_batch"], False)
post_json(MOUNT + f"/api/projects/{three}/render-jobs/{first_job}/approve")
opened = get_json(MOUNT + f"/api/projects/{three}/render-jobs")
check("an approval opens it", opened["can_batch"], True)
check("and what is left is what is not approved",
      sorted(opened["remaining_formats"]), ["1:1", "9:16"])
batch = post_json(MOUNT + f"/api/projects/{three}/render",
                  {"formats": ["9:16", "1:1"], "force_despite_qc_failures": True})
check("the rest render together", batch.status_code, 200)
check("one job per size", len(batch.get_json()["render_jobs"]), 2)
check("and it says it was a batch", batch.get_json()["batched"], True)
# Silently dropping an already-approved size from a batch is how an approved
# cut is quietly replaced, or quietly not replaced, with the panel reporting
# the same success either way.
clash = post_json(MOUNT + f"/api/projects/{three}/render",
                  {"formats": ["16:9", "9:16"], "force_despite_qc_failures": True})
check("an approved size inside a batch is refused", clash.status_code, 409)
check("and named", "16:9" in clash.get_json()["error"], True)
# Re-rendering one deliberately is still a thing somebody may want to do.
check("but that size on its own still renders", post_json(
    MOUNT + f"/api/projects/{three}/render",
    {"format": "16:9", "force_despite_qc_failures": True}).status_code, 200)

section("The voice and music selections survive to the render")
# `set_music` assigned a fresh two-key dict, which wiped `voice_track_url` —
# the key routes/render.py reads to put narration on the timeline. So saving
# the music selection after generating a voiceover threw the voiceover away
# and the commercial came back silent, with no error at either end.
with hub_app.app_context():
    _p = CommercialProject_cls.query.get(pid)
    _m = dict(_p.music or {})
    _m["voice_track_url"] = "https://cdn.example/vo.mp3"
    _p.music = _m
    cb_db.session.commit()
post_json(MOUNT + f"/api/projects/{pid}/music", {"mood": "Energetic", "level": "High"},
          method="put")
with hub_app.app_context():
    _m = CommercialProject_cls.query.get(pid).music
check("the mood saves", _m.get("mood"), "Energetic")
check("and the voice track is still there",
      _m.get("voice_track_url"), "https://cdn.example/vo.mp3")


# ---------------------------------------------------------------------------
# 9. The pickers are pictures, and the pictures are of something real
# ---------------------------------------------------------------------------
section("Casting is tiles carrying what they will actually match on")
detail = get_json(MOUNT + "/api/voice-characteristics")["characteristics"]
check("all five rows", len(detail), 5)
by_id = {row["id"]: row for row in detail}
# Energy is the one characteristic that does more than rank — STYLE_BY_ENERGY
# becomes the `style` sent on the render — which is why it is the one with an
# amplitude drawn for it.
check("energy options carry the style they send",
      all(o["style"] is not None for o in by_id["energy"]["options"]), True)
check("and they differ", len({o["style"] for o in by_id["energy"]["options"]}), 4)
check("delivery says which words it searches for",
      "announcer" in by_id["delivery"]["options"][0]["matches"], True)
check("accent carries its aliases",
      "usa" in by_id["accent"]["options"][0]["matches"], True)
# Nothing is drawn for gender or age: a glyph there would assert something the
# tool does not know.
check("gender claims no match words", by_id["gender"]["options"][0]["matches"], [])

cast = post_json(MOUNT + "/api/voices/cast",
                 {"want": {"gender": "female", "energy": "energetic"}, "count": 3}).get_json()
# "No preference" is not a question, so counting it would make a voice that
# matched everything asked read as "2 of 5".
check("the denominator is what was actually asked", cast["asked"] >= 2, True)

section("The music picker draws the real numbers")
voice_page = client.get(f"{MOUNT}/project/{pid}/voice").get_data(as_text=True)
check("mood is a tile grid, not a dropdown",
      'id="music-mood-choices"' in voice_page and '<select id="music-mood"' not in voice_page, True)
check("level is a tile grid too",
      'id="music-level-choices"' in voice_page and '<select id="music-level"' not in voice_page, True)
# The two dB figures on screen are the ones creatomate_service turns into the
# ducking automation, so what is drawn is what renders.
for label, (bed, ducked) in cb_config.MUSIC_LEVELS.items():
    check(f"{label} carries its real dB pair",
          f'data-bed="{bed}"' in voice_page and f'data-ducked="{ducked}"' in voice_page, True)


section("The chrome arrives as an element, on every one of the new steps")
from html.parser import HTMLParser                                      # noqa: E402
sys.path.insert(0, str(ROOT / "tools"))
from pagecheck import CHROME_MARKERS                                    # noqa: E402


class _ChromeFinder(HTMLParser):
    """pagecheck's own chrome test, asked of a page it cannot reach."""

    def __init__(self):
        super().__init__()
        self.found = False

    def handle_starttag(self, tag, attrs):
        cls = dict(attrs).get("class") or ""
        if tag in ("nav", "div") and any(m in cls for m in CHROME_MARKERS):
            self.found = True


# tools/pagecheck.py boots each module's index page and asks what the browser
# actually receives after the hub's after_request has rewritten it. It cannot
# reach these: every wizard step past Start needs a project id. So the same
# question is asked here. It is not hypothetical -- HubBar injected at the
# FIRST </body> in a response, and the IO Builder builds two printable
# documents as JavaScript template literals that each carry their own, so the
# sidebar landed inside a string and the whole tool rendered blank.
for step in ("blueprint", "voice", "cta"):
    page = client.get(f"{MOUNT}/project/{pid}/{step}").get_data(as_text=True)
    check(f"/{step} carries the help layer", "hub-help.js" in page, True)
    check(f"/{step} carries the sidebar", "s1hub-sb" in page or 'class="sidebar"' in page, True)
    # Chrome hidden inside a <script> string is chrome the browser never draws.
    # html.parser goes raw-text inside <script> exactly as a browser does, so
    # this asks the same question pagecheck does -- and it imports pagecheck's
    # own markers rather than restating them, for the reason
    # hub/jsonstore.unmirrored_json_writers() exists: two checks asking one
    # question answer it differently, and both answers end up on screen.
    f = _ChromeFinder()
    f.feed(page)
    check(f"/{step}'s sidebar is an element, not text in a script", f.found, True)


section("Deleting takes what it says, and says what it took")
# One unconfirmed DELETE used to answer {"ok": true} and destroy the client,
# every project, every scene and every render job — while the render
# approvals, the review shares and the compliance acknowledgments, which are
# keyed on a project and in no cascade, stayed behind pointing at ids that no
# longer resolve. Those three are the records that exist for the day a client
# says "we never signed off on that". Nothing was recorded anywhere.
from modules.commercial_builder import teardown as cb_teardown          # noqa: E402
from modules.commercial_builder.models import (                         # noqa: E402
    Client as Client_cls, ComplianceAck as ComplianceAck_cls,
    RenderApproval as RenderApproval_cls, ReviewShare as ReviewShare_cls,
    Scene as Scene_cls)
from hub import audit as _audit_mod                                     # noqa: E402


def _seed_client(name, slug, with_work=True):
    """A client, and optionally a spot carrying one of each kept record."""
    with hub_app.app_context():
        c = Client_cls(name=name, slug=slug)
        cb_db.session.add(c)
        cb_db.session.commit()
        cid = c.id
        if not with_work:
            return cid, None
        pr = CommercialProject_cls(client_id=cid, title=f"{name} spot",
                                   length_seconds=30)
        cb_db.session.add(pr)
        cb_db.session.commit()
        prid = pr.id
        cb_db.session.add(Scene_cls(project_id=prid, order_index=0))
        job = RenderJob_cls(project_id=prid, format="ctv_16x9",
                            status="succeeded")
        cb_db.session.add(job)
        cb_db.session.commit()
        cb_db.session.add(RenderApproval_cls(project_id=prid,
                                             render_job_id=job.id,
                                             approved_by="Todd"))
        cb_db.session.add(ReviewShare_cls(project_id=prid,
                                          token=f"tok-{slug}", round_no=1))
        cb_db.session.add(ComplianceAck_cls(project_id=prid,
                                            acknowledged_by="Todd",
                                            findings_key="k"))
        cb_db.session.commit()
        return cid, prid


def _kept_rows(prid):
    """The three records no cascade reaches, counted."""
    with hub_app.app_context():
        return (RenderApproval_cls.query.filter_by(project_id=prid).count()
                + ReviewShare_cls.query.filter_by(project_id=prid).count()
                + ComplianceAck_cls.query.filter_by(project_id=prid).count())


_cid, _prid = _seed_client("Teardown Co", "teardown-co")
_refused = client.delete(f"{MOUNT}/api/clients/{_cid}")
check("a client with work behind them is not deleted on one press",
      _refused.status_code, 409)
# Read with guards. A KeyError here takes every check after it out of the
# run, and a file that stops early is how a regression hides behind a green
# tail — the trap CLAUDE.md names about an assertion that raises on a missing
# field, which is precisely what the bite run for this section found.
_body = _refused.get_json() or {}
check("the refusal names what would go",
      (_body.get("counts") or {}).get("compliance_acks"), 1)
# A refusal that does not say how to proceed is a wall, and a wall is what
# gets a guard switched off — the QR_CODE_RULES lesson.
check("and it names the way through",
      "confirm=" in (_body.get("error") or ""), True)
check("nothing was deleted", _kept_rows(_prid), 3)
with hub_app.app_context():
    check("the client is still there",
          Client_cls.query.get(_cid) is not None, True)

_wrong = client.delete(f"{MOUNT}/api/clients/{_cid}",
                       data=json.dumps({"confirm": "Some Other Co"}),
                       headers={"Content-Type": "application/json"})
check("a confirmation naming a different client is refused",
      _wrong.status_code, 409)

_gone = client.delete(f"{MOUNT}/api/clients/{_cid}",
                      data=json.dumps({"confirm": "Teardown Co"}),
                      headers={"Content-Type": "application/json"})
check("the exact name goes ahead", _gone.status_code, 200)
# The half that was silently wrong: the cascade never reached these, so they
# survived as fragments naming a project nobody could look up.
check("and the records outside the cascade go with it", _kept_rows(_prid), 0)
_gone_body = _gone.get_json() or {}
check("the response says how many were swept",
      (_gone_body.get("removed_orphans") or 0) >= 3, True)
check("and it says what went",
      (_gone_body.get("counts") or {}).get("projects"), 1)

# A client nobody has done work for is a row somebody is tidying up. Asking
# them to type a name for it is friction with nothing behind it, and a
# confirmation people click through without reading is not a confirmation.
_empty_cid, _ = _seed_client("Tidy Co", "tidy-co", with_work=False)
_tidied = client.delete(f"{MOUNT}/api/clients/{_empty_cid}")
check("a client with no work still deletes on one press",
      _tidied.status_code, 200)

# The activity entry is the only record the deletion happened and the only
# place the counts survive, so it is what somebody reconstructs from — and it
# carries the row's OWN name, never one the caller passed, which is what
# modules/suite_panel had to undo on the route that deletes a sub-account.
_rows = [e for e in _audit_mod.read(400)
         if e.get("type") == "cb_client_deleted"]
check("the deletion reached the activity log", len(_rows) >= 2, True)
check("under the client's own name",
      sorted({e.get("client") for e in _rows}), ["Teardown Co", "Tidy Co"])

# The same failure one level down, and the same reading of it: a spot does
# not weigh itself, or every delete of an untouched draft would come back
# asking the rep to type its title.
_cid2, _prid2 = _seed_client("Spot Teardown Co", "spot-teardown-co")
_p_refused = client.delete(f"{MOUNT}/api/projects/{_prid2}")
check("a spot with a sign-off behind it is not deleted on one press",
      _p_refused.status_code, 409)
check("and the refusal does not count the spot as its own baggage",
      "1 spot" not in ((_p_refused.get_json() or {}).get("error") or ""), True)
with hub_app.app_context():
    _draft = CommercialProject_cls(client_id=_cid2, title="Untouched draft",
                                   length_seconds=15)
    cb_db.session.add(_draft)
    cb_db.session.commit()
    _draft_id = _draft.id
check("an untouched draft still deletes on one press",
      client.delete(f"{MOUNT}/api/projects/{_draft_id}").status_code, 200)

# teardown is one reading, not two: both routes would otherwise have grown
# their own idea of what deleting means.
check("both deletes read one teardown",
      "teardown" in (ROOT / "modules/commercial_builder/routes/clients.py").read_text()
      and "teardown" in (ROOT / "modules/commercial_builder/routes/projects.py").read_text(),
      True)
# Nothing in it may raise: a count that cannot be taken must not cost the
# refusal it informs, and a sweep that fails must not strand the delete.
check("a teardown count over nothing answers rather than raising",
      cb_teardown.work_behind([])["projects"], 0)
check("and a sweep over nothing answers 0", cb_teardown.sweep_orphans([]), 0)
check("an empty count summarizes to nothing to weigh",
      cb_teardown.summarize({}), "")


section("The guard is still in front of all of it")
anon = werkzeug.test.Client(application)
check("a page redirects to login", anon.get(MOUNT + "/").status_code in (301, 302), True)
# A fetch() that follows a redirect to the login page parses HTML as JSON and
# reports "Bad response from server", which says nothing about the real problem.
check("an API answers 401, not HTML", anon.get(MOUNT + "/api/voices").status_code, 401)
check("and so do the new ones",
      anon.get(MOUNT + "/api/clients/hub-search?q=a").status_code, 401)
check("including the spec preview",
      anon.get(MOUNT + "/api/projects/spec-preview?lengths=30").status_code, 401)


# ---------------------------------------------------------------------------
# 12. The Cloudinary read path goes through the shared service
#
# CLAUDE.md's standing rule: never leave a module you have just touched still
# doing its own Cloudinary. The write path moved last change; this is the read
# path and the configure, which were the last direct SDK use in the module.
# ---------------------------------------------------------------------------
section("Listing a client's tree goes through hub.storage, and pages")
import types                                                            # noqa: E402
import hub.storage as _storage                                          # noqa: E402
from modules.commercial_builder.services import cloudinary_service as _cs  # noqa: E402

check("hub.storage offers a public configure()", callable(
    getattr(_storage, "configure", None)), True)
# Called by provider_check to ping the account, and by this module. It must be
# safe to call with Cloudinary unconfigured, or a deployment with no key would
# 500 on the panel that exists to say the key is missing.
try:
    _storage.configure(); _storage.configure()
    _cfg_ok = True
except Exception:                                                       # noqa: BLE001
    _cfg_ok = False
check("and it is idempotent and safe unconfigured", _cfg_ok, True)

_seen = {}


class _FakeAPI:
    @staticmethod
    def resources(**kw):
        _seen.update(kw)
        return {"resources": [{"public_id": "acme/photos/a", "secure_url": "u",
                               "format": "jpg", "created_at": "t", "bytes": 1}],
                "next_cursor": None}


# Patched on the module object, not in sys.modules: `from hub import storage`
# binds the attribute on the package, so replacing the entry alone leaves the
# real one in play — the trap the QR upload's own test hit.
_real_cloudinary, _real_ready, _real_configured = (
    _storage.cloudinary, _storage.ready, _storage._configured)
_storage.cloudinary = types.SimpleNamespace(api=_FakeAPI, config=lambda **k: None)
_storage.ready = lambda: True
_storage._configured = True
try:
    rows = _storage.manifest("commercials", prefix="acme/photos/")
    check("manifest lists the folder it was given", _seen.get("prefix"), "acme/photos/")
    # The orphaned-asset audit reads these; a prefix must not narrow the row.
    check("and a row still carries what the audit needs",
          all(k in rows[0] for k in ("public_id", "secure_url", "format", "bytes")), True)
    _seen.clear()
    check("no prefix still means the bucket's own folder",
          bool(_storage.manifest("commercials")) and _seen.get("prefix") != "acme/photos/", True)

    _seen.clear()
    _real_live, _cs._CONFIGURED = _cs.is_live, True
    _cs.is_live = lambda: True
    listed = _cs.list_client_assets("acme", "photo")
    check("list_client_assets reads the client's own folder",
          _seen.get("prefix"), "acme/photos/")
    # Read with a guard rather than listed[0]: an assertion that raises on the
    # empty case takes every check after it out of the run, which is how a
    # regression hides behind a test file that stopped early.
    check("and returns the picker's shape",
          sorted(listed[0]) if listed else [],
          ["created_at", "format", "public_id", "secure_url"])
    # It asked for one page of 100 and reported it as the whole folder, so a
    # client with more photographs than that was quietly shown some of them.
    check("asking for one page of 100 is gone", _seen.get("max_results") == 100, False)
    _cs.is_live = _real_live
finally:
    _storage.cloudinary, _storage.ready, _storage._configured = (
        _real_cloudinary, _real_ready, _real_configured)

# The point of the migration: no second reading of how to reach the account.
_cs_src = (ROOT / "modules/commercial_builder/services/cloudinary_service.py").read_text()
check("the module still names hub.storage for the listing",
      "storage.manifest(" in _cs_src, True)
check("and for the configure", "storage.configure()" in _cs_src, True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
