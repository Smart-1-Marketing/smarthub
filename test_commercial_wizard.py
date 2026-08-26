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
check("and recognised", cb_config.is_social("social"), True)
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

section("A QR code is required where nothing can be clicked, and nowhere else")
check("required on CTV", cb_config.qr_required(30, "ctv"), True)
check("required on a CTV+YouTube buy", cb_config.qr_required(30, "both"), True)
# A feed ad is already tappable, and a code there asks somebody to scan the
# phone they are holding. Reporting its absence as a finding on every social
# spot is how a warning stops being read.
check("not required on social", cb_config.qr_required(30, "social"), False)
check("not required on YouTube alone", cb_config.qr_required(30, "youtube"), False)
check("never on a :05, whatever the platform", cb_config.qr_required(5, "ctv"), False)

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
check("and the catalogue carries it",
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
check("against this length's target", (budget["target_low"], budget["target_high"]), (65, 75))
check("and the room is what is left", budget["room"], 67)
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
check("and still reports the room", "67" in mocked["note"], True)


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
check("unlabelled voices still rank", len(ranked), 3)
check("and the note says the ranking is not one",
      "own order" in voice_casting.match_quality(ranked, len(bare)), True)
check("an empty account is a different answer",
      "No voices came back" in voice_casting.match_quality([], 0), True)


# ---------------------------------------------------------------------------
# 7. The wizard itself, through the composed app
# ---------------------------------------------------------------------------
section("The seven steps, through the app as it is actually mounted")

import werkzeug.test                                                    # noqa: E402
from wsgi import application                                            # noqa: E402

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
check("sorted shortest first",
      [p["length_seconds"] for p in payload["projects"]], [15, 30, 60])
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

pid = payload["projects"][1]["id"]        # the :30

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
check("it is required on this CTV spot", qr["required"], True)
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

section("The checks run where the work is, and cover every key")
qc = post_json(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
check("the spec check ran", "creative_spec" in qc, True)
check("the feed-hook check ran", "social_hook" in qc, True)
check("the sound-off check ran", "sound_off" in qc, True)

# A check absent from a screen's label map is skipped silently by the render
# loop — which is how `scene_assets`, the check that catches an unfinished
# scene, never appeared on the panel it was written for.
keys = {k for k in qc if k != "_all_passed"}
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text()
    block_start = text.index("QC_LABELS = {")
    block = text[block_start:text.index("};", block_start)]
    labelled = set(re.findall(r"(\w+):\s*\"", block))
    check(f"{js_file} labels every check run_qc returns", sorted(keys - labelled), [])

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


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
