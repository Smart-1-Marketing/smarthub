"""The two radio commercial builders: Radio Promo and Fan Radio.

    python3 test_radio_builders.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

Two tools in this Hub write, cast and record a radio commercial, and until now
neither had a test. They divide the work cleanly — Radio Promo is the general
:15/:30 pair, spec or attached to a client; Fan Radio is the football-daypart
version with a page the customer approves on — and each carries a rule that
fails silently when it breaks. Every check below is one of those.

**Three defects this file was written against, all of them invisible from
either end:**

  1. **The customer's approval page was behind the staff login.** Fan Radio's
     own docstring has said since the day it was written that `/r/…`,
     `/api/public/…` and `/audio/…` are the customer's. The mount declared no
     public prefixes at all, so `_mount()` handed `AuthGuard` nothing: mailing
     a client their approval link mailed them a sign-in form for an account
     they will never have, and the audio player on it was dead too. Nothing
     errored — the redirect is a perfectly correct answer to a question nobody
     had asked. The module declares `PUBLIC_PREFIXES` now and `wsgi.py` reads
     it, so the mount and the module cannot disagree about what is public.

  2. **And the other half of "public" would have failed the opposite way.**
     `_mount()` hands the same list to `HubBar`, so without it the staff
     sidebar, help layer and feedback tab are injected into a page a client
     reads — live links to Client 360 and the leads panel on a customer's
     screen. Both halves are asserted, because either one alone is its own
     failure.

  3. **Neither store read `HUB_DATA_DIR`.** Both carried their own copy of the
     six-line data-root expression, and both ignored the variable
     `hub/jsonstore.data_root()` exists to be the single reader of — so on a
     deployment that sets it, every radio project would land outside the root
     the database mirror keys against and `/api/backup` reports on, while
     every other module moved. They agreed on this service only because the
     variable happens to be unset. Both go through `jsonstore.data_dir()` now,
     which is also what makes this file able to run in a temp directory at all.

**And one divergence that is reported rather than fixed.** The two modules
each carry their own speech-normalisation pass, and `fan_radio/speech.py`
claimed in its docstring to use "the same rules as Radio Promo, so a script
moved between the two tools is read identically". It does not: Radio Promo
says numbers as words and spells web addresses and emails out loud, and Fan
Radio does neither. Making them one reader is a change to what a client
hears, so it is named here and in that docstring rather than done quietly.
The sections below pin what each engine actually guarantees, so the two
cannot drift further apart without this file saying so.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1radio_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "radio-test-secret"
os.environ["PANEL_PASSWORD"] = "radio-test-password"
# No provider keys: every check below must hold on a clean checkout, and the
# refusals are most of what is being asserted.
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "ELEVENLABS_KEY", "GHL_OPPORTUNITY_WEBHOOK_URL", "PUBLIC_BASE_URL",
           "CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY"):
    os.environ.pop(_k, None)

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


from hub import jsonstore                                     # noqa: E402
from modules.fan_radio import app as fan_app                  # noqa: E402
from modules.fan_radio import catalog as fan_catalog          # noqa: E402
from modules.fan_radio import phrases                         # noqa: E402
from modules.fan_radio import speech as fan_speech            # noqa: E402
from modules.fan_radio import store as fan_store              # noqa: E402
from modules.radio_promo import app as promo_app              # noqa: E402
from modules.radio_promo import speech as promo_speech        # noqa: E402
from modules.radio_promo import store as promo_store          # noqa: E402
from modules.radio_promo.catalog import (DURATIONS, TONES,     # noqa: E402
                                         duration_by_key, tone_by_id)

fan = fan_app.app.test_client()
promo = promo_app.app.test_client()


# =====================================================================
section("Both stores write where every other module writes")
# =====================================================================
# The reason this file can run in a temp directory at all, and the reason a
# deployment that sets HUB_DATA_DIR does not quietly strand these projects
# outside the root the mirror keys against.

check("Fan Radio's data directory is under the shared root",
      fan_store.data_dir().startswith(jsonstore.data_root()), True)
check("Radio Promo's is too",
      promo_store.data_dir().startswith(jsonstore.data_root()), True)
check("so this test never reaches the repo's own data/ directory",
      str(ROOT / "data") in fan_store.data_dir() + promo_store.data_dir(), False)

# The mirror keys on the path relative to the data root. A store writing
# outside it gets an "abs:" key instead, which is the shape reserved for a
# deliberate override — not for a module that simply never read the variable.
_key = jsonstore.key_for(os.path.join(fan_store.data_dir(), "index.json"))
check("so the database mirror keys it relatively, not as an override",
      _key.startswith("abs:"), False)
check("and it is keyed under the module's own name",
      _key.startswith("fan_radio/"), True)


# =====================================================================
section("The customer's approval page is the customer's")
# =====================================================================
# Fan Radio's docstring has always said /r/, /api/public/ and /audio/ are
# reached by somebody with no Hub account. The mount passed AuthGuard no
# public prefixes, so all three redirected a client to a staff sign-in form.

import wsgi                                                   # noqa: E402
from werkzeug.test import Client as WsgiClient                # noqa: E402

composed = WsgiClient(wsgi.application)

project = fan_store.create({"scope": "spec", "company": "Ridgeline Tyre",
                            "team_context": "Cincinnati Bengals",
                            "tone": "warm"}, "tester")
project["spots"] = [
    {"id": "sp15", "daypart": "pregame", "seconds": 15, "outcome": "neutral",
     "script": "Beat the pregame rush at Ridgeline Tyre.", "status": "pending",
     "notes": "internal note", "ai": False, "versions": [{"why": "first draft"}]},
    {"id": "sp30", "daypart": "gameday", "seconds": 30, "outcome": "neutral",
     "script": "Kickoff is Sunday. Ridgeline Tyre is open till noon.",
     "status": "pending"},
    {"id": "spDraft", "daypart": "postgame", "seconds": 30,
     "outcome": "neutral", "script": "", "status": "draft"},
    {"id": "spHidden", "daypart": "postgame", "seconds": 15,
     "outcome": "neutral", "script": "Withdrawn.", "status": "draft",
     "hidden": True},
]
project["share"]["enabled"] = True
fan_store.save(project)
PID, TOKEN = project["id"], project["share"]["token"]

page = composed.get(f"/tools/fan-radio/r/{TOKEN}")
check("the approval page opens with no Hub session", page.status_code, 200)
check("and it is the right project", "Ridgeline Tyre" in page.get_data(as_text=True), True)

check("what that page fetches is public too",
      composed.get(f"/tools/fan-radio/api/public/{TOKEN}").status_code, 200)
# A missing Cloudinary credential falls the render back to local disk, so the
# player's src is /audio/<name>. Guarded, it is a page whose audio silently
# never plays. 404 here is the file being absent, not the login refusing.
check("and so is the audio the player asks for",
      composed.get("/tools/fan-radio/audio/absent.mp3").status_code, 404)

approval = composed.post(f"/tools/fan-radio/api/public/{TOKEN}/feedback",
                         json={"action": "approve", "name": "Dana Reed",
                               "spot_id": "sp15"})
check("a client can actually answer without signing in", approval.status_code, 200)


# =====================================================================
section("And the other half of public: no staff chrome on it")
# =====================================================================
# _mount() hands the same list to HubBar. Without it the sidebar, help layer
# and feedback tab are injected into a page a client reads.

html = page.get_data(as_text=True)
for marker, what in (("s1hub-sidebar", "the sidebar"),
                     ("hub-help.js", "the help layer"),
                     ("hub-feedback", "the feedback tab"),
                     ("/client360", "a live link to Client 360"),
                     ("/sales/leads", "a live link to the leads panel"),
                     ("/qa", "a live link to the QA reports")):
    check(f"{what} is not injected into it", marker in html, False)


# =====================================================================
section("The mount and the module cannot disagree about what is public")
# =====================================================================
# Declared in the module and read by wsgi.py with getattr, the arrangement
# modules/scans, ads_builder and sales_builder already use. Restated in
# wsgi.py instead, the two drift the day one of them is edited.

check("the module declares its own public prefixes",
      tuple(getattr(fan_app, "PUBLIC_PREFIXES", ())),
      ("/r/", "/api/public/", "/audio/"))
check("and wsgi.py reads them from it rather than restating them",
      tuple(getattr(wsgi, "_FANRAD_PUBLIC", ())),
      tuple(fan_app.PUBLIC_PREFIXES))

_wsgi_src = (ROOT / "wsgi.py").read_text()
check("and the mount is handed them",
      "public_prefixes=_FANRAD_PUBLIC" in _wsgi_src, True)

# Radio Promo is staff-only and declares none. That is not an oversight: it
# has no customer-facing route, and a prefix wide enough to be "helpful" here
# would open the whole tool.
check("Radio Promo declares none, because none of it is a client's",
      hasattr(promo_app, "PUBLIC_PREFIXES"), False)


# =====================================================================
section("Staff work stays behind the login")
# =====================================================================
# A prefix wide enough to fix the page above goes wrong in the other
# direction just as quietly: every client name answering 200 to anyone.

for path, what in (("/tools/fan-radio/", "the builder itself"),
                   ("/tools/fan-radio/library", "the project library"),
                   ("/tools/radio-promo/", "Radio Promo's builder"),
                   ("/tools/radio-promo/library", "Radio Promo's library")):
    check(f"{what} still redirects a stranger to the login",
          composed.get(path).status_code, 302)

for path, what in (("/tools/fan-radio/api/projects", "the project list"),
                   ("/tools/fan-radio/api/catalog", "the reference data"),
                   ("/tools/radio-promo/api/library", "Radio Promo's library API"),
                   ("/tools/radio-promo/api/clients", "the client picker")):
    check(f"{what} refuses one outright", composed.get(path).status_code, 401)

# The scan endpoint is a staff tool that takes arbitrary text. Public, it is
# an open trademark-checking service on somebody else's bandwidth.
check("and the phrase checker is not an open API",
      composed.post("/tools/fan-radio/api/phrase-check",
                    json={"text": "hello"}).status_code, 401)


# =====================================================================
section("A link that is not live answers what a link that never existed does")
# =====================================================================
# Saying "that one expired" tells somebody probing which tokens are real.

live = composed.get(f"/tools/fan-radio/r/{TOKEN}").status_code
project = fan_store.load(PID)
project["share"]["enabled"] = False
fan_store.save(project)
disabled = composed.get(f"/tools/fan-radio/r/{TOKEN}").status_code
disabled_api = composed.get(f"/tools/fan-radio/api/public/{TOKEN}").status_code

project = fan_store.load(PID)
project["share"]["enabled"] = True
stale = project["share"]["token"]
project["share"]["token"] = fan_store.new_token()
fan_store.save(project)
TOKEN = project["share"]["token"]

check("a live link opens", live, 200)
check("a switched-off one is 404, not a message", disabled, 404)
check("and so is what its page fetches", disabled_api, 404)
check("a regenerated link retires the one already sent",
      composed.get(f"/tools/fan-radio/r/{stale}").status_code, 404)
check("an invented token answers the same",
      composed.get(f"/tools/fan-radio/r/{'A' * 30}").status_code, 404)
# A token too short to be one of ours is refused by shape before any file is
# opened, so a scan cannot walk the project directory by guessing.
check("and a malformed one never reaches the store",
      fan_store.find_by_token("short"), None)

# A switched-off link must not take the answer with it, either: the client
# approved something, and that record is what the whole page exists to make.
check("switching a link off does not lose what was already approved",
      fan_store.get_spot(fan_store.load(PID), "sp15")["status"], "approved")


# =====================================================================
section("The page shows what it was built to show, and nothing else")
# =====================================================================
# public_view() picks fields out rather than deleting them — a denylist
# forgets the field somebody adds next month.

view = fan_app.public_view(fan_store.load(PID))
check("only spots with a script reach it",
      [s["id"] for s in view["spots"]], ["sp15", "sp30"])
check("a withdrawn spot is withheld",
      "spHidden" in str(view["spots"]), False)
check("and an unwritten one is not shown as an empty script",
      "spDraft" in str(view["spots"]), False)

allowed = {"id", "daypart", "daypart_label", "daypart_when", "seconds",
           "length_label", "outcome", "script", "audio_url", "audio_seconds",
           "voice_name", "status", "comments"}
check("a spot carries exactly the fields it is meant to",
      set(view["spots"][0].keys()), allowed)
check("the page itself carries exactly its own",
      set(view.keys()),
      {"company", "headline", "intro", "cta_label", "cta_url", "spots", "general"})

blob = str(view)
for leaked, what in (("internal note", "the writer's internal notes"),
                     ("first draft", "the version history"),
                     ("Cincinnati Bengals", "the project's team context"),
                     ("tester", "who at the agency built it")):
    check(f"{what} does not reach the client", leaked in blob, False)


# =====================================================================
section("Nobody's trademark leaves the building")
# =====================================================================
# The whole point of Fan Radio: sound like football without borrowing a mark.

def scan(text, also=None, daypart="gameday", outcome="neutral"):
    return phrases.scan(text, also or [], daypart, outcome)


for text, what in (("Get ready for the Super Bowl.", "a league mark"),
                   ("Go Bengals!", "a club nickname"),
                   ("Roll Tide from all of us.", "a fan slogan"),
                   ("Catch the Rose Bowl with us.", "a bowl mark"),
                   ("Home of the Buckeyes.", "a college nickname")):
    check(f"{what} fails the scan", scan(text)["clean"], False)

check("generic football language passes",
      scan("Kickoff is Sunday. Beat the rush before the whistle blows.")["clean"],
      True)
check("and is reported, because a football spot with no football in it "
      "is the other failure",
      bool(scan("Stop in before kickoff.")["football"]), True)
check("a spot with no football language says so",
      scan("We sell tyres at a fair price.")["football"], [])

# The client's own team is context for the writer, never copy for the spot.
own = phrases.extra_blocked("Cincinnati Bengals")
check("a project's own team becomes a block term, not a phrase to use",
      own, ["bengals", "cincinnati bengals"])
check("and the scan enforces it",
      scan("Proud supporter of the Cincinnati Bengals.", own)["clean"], False)
check("the city on its own survives — it is a place, not a mark",
      scan("Serving Cincinnati since 1994.", own)["clean"], True)
check("a school name is a mark too",
      "kansas state" in phrases.extra_blocked("Kansas State Wildcats"), True)

# Advisory is surfaced for a human, never a block: a check that refuses the
# correct thing is a check somebody switches off.
big = scan("Watch the big game with us.")
check("'the big game' is advisory, not blocked", big["clean"], True)
check("and it is surfaced rather than swallowed", bool(big["advisory"]), True)

# A post-game spot is voiced days before it airs. It cannot know the score.
assumed = scan("After that big win, come celebrate.", daypart="postgame")
check("post-game copy that assumes a result is flagged",
      bool(assumed["outcome"]), True)
check("but the same line on game day is not — it is not airing after a result",
      scan("After that big win, come celebrate.", daypart="gameday")["outcome"], [])
check("and an alternate booked for a known result is not flagged either",
      scan("After that big win, come celebrate.",
           daypart="postgame", outcome="win")["outcome"], [])
check("result-neutral post-game copy passes",
      scan("However it ended, we open at nine.", daypart="postgame")["outcome"], [])


# =====================================================================
section("A render is never spent on a script that cannot be delivered")
# =====================================================================
# The trademark check runs before the voice check: it is the problem that
# has to be fixed either way, and it costs nothing to find.

project = fan_store.load(PID)
project["spots"][0]["script"] = "Proud supporter of the Bengals."
fan_store.save(project)
r = fan.post(f"/api/projects/{PID}/spots/sp15/record", json={})
check("a script carrying a mark is refused before any provider is called",
      r.status_code, 400)
check("and the refusal names the term rather than being generic",
      "Bengals" in r.get_json()["error"], True)
check("and it says the render was not spent",
      "before spending a render" in r.get_json()["error"], True)

# Voice comes second, and only once the copy is deliverable.
project = fan_store.load(PID)
project["spots"][0]["script"] = "Beat the rush before kickoff at Ridgeline."
fan_store.save(project)
r = fan.post(f"/api/projects/{PID}/spots/sp15/record", json={})
check("a clean script then asks for the voice", r.status_code, 400)
check("and says so in those words",
      "Cast a voice" in r.get_json()["error"], True)

r = fan.post(f"/api/projects/{PID}/spots/spDraft/record", json={})
check("an unwritten spot is refused first of all",
      "write the spot first" in r.get_json()["error"], True)

# Nothing in a refusal may carry a provider body or a key fragment.
project = fan_store.load(PID)
project["voice"] = {"voice_id": "abc123", "name": "Reed", "energy": "energetic"}
fan_store.save(project)
r = fan.post(f"/api/projects/{PID}/spots/sp15/record", json={})
check("with no ElevenLabs key the refusal is plain and safe to show",
      r.status_code, 503)
check("and it names the variable to set",
      "ELEVENLABS_API" in r.get_json()["error"], True)


# =====================================================================
section("The customer's answer is written before anything else runs")
# =====================================================================

def answer(**payload):
    return fan.post(f"/api/public/{TOKEN}/feedback", json=payload)


check("an answer with no name is refused",
      "your name" in answer(action="approve", spot_id="sp30").get_json()["error"],
      True)
check("a change request with no words is refused — 'they want changes' "
      "is not actionable",
      "what to change" in answer(action="changes", name="Dana",
                                 spot_id="sp30").get_json()["error"], True)
check("an unknown action is refused",
      answer(action="delete", name="Dana", spot_id="sp30").status_code, 400)
check("approving a spot that is not on this page is refused",
      answer(action="approve", name="Dana", spot_id="nope").status_code, 400)
check("and so is approving nothing in particular",
      answer(action="approve", name="Dana").status_code, 400)

check("a general comment needs no spot",
      answer(action="comment", name="Dana", comment="Love these.").status_code, 200)
check("a change request lands",
      answer(action="changes", name="Dana", spot_id="sp30",
             comment="Use the new number.").status_code, 200)

saved = fan_store.load(PID)
check("and it is on disk against the spot it belongs to",
      fan_store.get_spot(saved, "sp30")["status"], "changes")
check("with the name of whoever asked",
      fan_store.get_spot(saved, "sp30")["decided_by"], "Dana")
check("the general comment is filed against no spot, not against the first",
      [f["spot_id"] for f in saved["feedback"] if f["comment"] == "Love these."],
      [""])

answer(action="approve_all", name="Owner")
saved = fan_store.load(PID)
check("approve-all covers the spots actually on the page",
      {s["id"]: s.get("status") for s in saved["spots"]
       if s["id"] in ("sp15", "sp30")},
      {"sp15": "approved", "sp30": "approved"})
check("and never a withdrawn one the client cannot see",
      fan_store.get_spot(saved, "spHidden").get("status"), "draft")
check("nor an unwritten one",
      fan_store.get_spot(saved, "spDraft").get("status"), "draft")

# Feedback is appended, never edited by the customer afterwards.
check("every answer is kept rather than the last one winning",
      len(saved["feedback"]) >= 4, True)


# =====================================================================
section("Radio Promo: spec and client are the only structural difference")
# =====================================================================


def new_promo(**fields):
    return promo.post("/api/projects", json=fields)


check("a spot with no business and no client is refused",
      "business name" in new_promo().get_json()["error"], True)
check("and one with nothing to read from is refused too — the brief needs "
      "a source",
      "home page" in new_promo(company="Acme").get_json()["error"], True)

spec = new_promo(company="Acme Tyre", home_url="acmetyre.com").get_json()["project"]
check("a project with no client is a spec piece", spec["spec"], True)
check("and files under spec/, not under a client who has not asked for it",
      "/spec/" in promo_store.cloud_folder(spec), True)

held = new_promo(client="Icon Solar", home_url="iconsolar.com").get_json()["project"]
check("a project with a client is not spec", held["spec"], False)
check("and takes the client's name when none was typed", held["company"], "Icon Solar")
check("and files under the client's slug",
      "/icon-solar/" in promo_store.cloud_folder(held), True)

# The normal path: a spec spot wins the account and becomes theirs.
SPEC_ID = spec["id"]
attached = promo.post(f"/api/projects/{SPEC_ID}/settings",
                      json={"client": "Icon Solar"}).get_json()["project"]
check("attaching a spec project to a client flips it", attached["spec"], False)
check("and re-slugs it", attached["client_slug"], "icon-solar")
check("and moves where it files",
      "/icon-solar/" in promo_store.cloud_folder(attached), True)
check("without losing anything it already had",
      attached["company"], "Acme Tyre")

detached = promo.post(f"/api/projects/{SPEC_ID}/settings",
                      json={"client": ""}).get_json()["project"]
check("and detaching returns it to spec rather than leaving a stale slug",
      (detached["spec"], detached["client_slug"]), (True, "spec"))

promo.post(f"/api/projects/{SPEC_ID}/settings", json={"client": "Icon Solar"})

check("the library separates the two kinds",
      [p["id"] for p in promo.get("/api/library?scope=spec").get_json()["projects"]],
      [])
check("and lists the attached one under client",
      SPEC_ID in [p["id"] for p in
                  promo.get("/api/library?scope=client").get_json()["projects"]],
      True)


# =====================================================================
section("Radio Promo: the push refuses what it should")
# =====================================================================
# An opportunity for a business that has not asked for one pollutes the
# pipeline, so spec work is refused by name before anything else is checked.

fresh_spec = new_promo(company="Nobody Asked", home_url="na.com").get_json()["project"]
r = promo.post(f"/api/projects/{fresh_spec['id']}/push", json={})
check("spec work is refused", r.status_code, 400)
check("and told how to make it pushable",
      "Attach it to a client" in r.get_json()["error"], True)

r = promo.post(f"/api/projects/{SPEC_ID}/push", json={})
check("with no webhook set it says so rather than reporting a clean send",
      r.status_code, 503)
check("and names the variable",
      "GHL_OPPORTUNITY_WEBHOOK_URL" in r.get_json()["error"], True)

os.environ["GHL_OPPORTUNITY_WEBHOOK_URL"] = "https://example.invalid/hook"
r = promo.post(f"/api/projects/{SPEC_ID}/push", json={})
check("and with nothing approved it refuses before reaching the network",
      "Approve at least one spot" in r.get_json()["error"], True)
os.environ.pop("GHL_OPPORTUNITY_WEBHOOK_URL", None)

check("a project that no longer exists is a 404, not a 500",
      promo.post("/api/projects/rp_nothing/push", json={}).status_code, 404)


# =====================================================================
section("Runtime against the clock is measured, and never trimmed")
# =====================================================================
# A read that runs long comes back flagged with how many words to cut.
# Trimming the audio clips a word — usually off the end of the phone number.

long_read = promo_speech.grade_duration(33.0, 30)
check("a long read is graded long", long_read["status"], "long")
check("and says how many words to cut rather than cutting them",
      long_read["trim_words"], 8)
check("a read on the clock passes",
      promo_speech.grade_duration(29.6, 30)["status"], "good")
check("a fractionally long one is not fussed over",
      promo_speech.grade_duration(30.3, 30)["status"], "good")
check("a short one reports the dead air",
      promo_speech.grade_duration(26.0, 30)["status"], "short")

# Absent is not zero. A render whose length could not be measured must not
# read as a spot that lands perfectly.
check("a length that could not be measured says so",
      promo_speech.grade_duration(None, 30)["status"], "unknown")
check("and never reads as good", promo_speech.grade_duration(0, 30)["status"],
      "unknown")

# Fan Radio grades the written words instead, before a render is spent.
check("Fan Radio grades a long script long",
      fan_catalog.grade(" ".join(["word"] * 95), 30)["state"], "long")
check("and says how many words over",
      fan_catalog.grade(" ".join(["word"] * 95), 30)["delta"], 20)
check("a script inside the budget is on the clock",
      fan_catalog.grade(" ".join(["word"] * 70), 30)["state"], "ok")
check("and a thin one is reported as room, not as a fault",
      fan_catalog.grade(" ".join(["word"] * 20), 15)["state"], "short")

# Tightening is refused when there is nothing to tighten, rather than
# spending a model call to be told the script already fits.
project = fan_store.load(PID)
project["spots"][0]["script"] = " ".join(["word"] * 34)
fan_store.save(project)
check("a script already on the clock is not re-tightened",
      "already on the clock" in
      fan.post(f"/api/projects/{PID}/spots/sp15/tighten", json={}).get_json()["error"],
      True)


# =====================================================================
section("The word budgets the two tools quote are the same clock")
# =====================================================================
# A script written in one and moved to the other must not need re-timing.

check("Radio Promo sells two slots", [d["key"] for d in DURATIONS],
      ["fifteen", "thirty"])
check("Fan Radio sells the same two lengths", fan_catalog.LENGTH_IDS, [15, 30])
check("and they agree that a :15 and a :30 are what a radio spot is",
      sorted(d["seconds"] for d in DURATIONS), fan_catalog.LENGTH_IDS)

# They are budgets, not limits, and the numbers are deliberately close
# rather than identical — Radio Promo's are the studio's, measured at a
# natural read pace. What must not happen is one calling a script long that
# the other calls short.
for seconds, key in ((15, "fifteen"), (30, "thirty")):
    promo_slot = duration_by_key(key)
    fan_slot = fan_catalog.budget(seconds)
    overlap = (max(promo_slot["low"], fan_slot["min"])
               <= min(promo_slot["high"], fan_slot["max"]))
    check(f"the two :{seconds} budgets overlap rather than contradicting",
          overlap, True)


# =====================================================================
section("What actually reaches the voice")
# =====================================================================
# A TTS engine reads a phone number as one enormous integer and "$19.99" as
# "dollar nineteen point nine nine". Both modules rewrite the copy for the
# ear; what they guarantee in common is asserted here, and where they differ
# is asserted as a difference rather than papered over.

promo_said = promo_speech.normalize_for_speech(
    "Call 614-536-0768 and save $1,250 — 20% off at 100 Main St.")["spoken"]
fan_said = fan_speech.normalize_for_speech(
    "Call 614-536-0768 and save $1,250 — 20% off at 100 Main St.")["spoken"]

for label, said in (("Radio Promo", promo_said), ("Fan Radio", fan_said)):
    check(f"{label} never hands the voice a raw dollar sign", "$" in said, False)
    check(f"{label} never hands it a raw percent sign", "%" in said, False)
    check(f"{label} breaks the phone number up rather than sending "
          f"an integer", "614-536-0768" in said, False)
    check(f"{label} spells the abbreviated street out",
          "Street" in said and "St." not in said, True)

check("Radio Promo says numbers as words",
      "twenty percent" in promo_said and "one thousand two hundred fifty" in promo_said,
      True)
# Named rather than fixed: making these one reader changes what a client
# hears. fan_radio/speech.py's docstring carries the same statement.
check("Fan Radio leaves the digits for the engine to say — a real "
      "difference, not the identical pass its docstring used to claim",
      "20 percent" in fan_said and "1250 dollars" in fan_said, True)

# The gap worth closing, pinned so it cannot widen unnoticed.
web = "Visit ridgelinetyre.com or email sales@ridgelinetyre.com."
check("Radio Promo spells a web address out loud",
      "dot com" in promo_speech.normalize_for_speech(web)["spoken"], True)
check("and an email address too",
      " at " in promo_speech.normalize_for_speech(web)["spoken"], True)
check("Fan Radio does neither, and that is the known gap",
      "dot com" in fan_speech.normalize_for_speech(web)["spoken"], False)

# A project override wins over every rule in both, because the business
# really is called St. Mary's.
saint = [{"from": "St. Mary's", "to": "Saint Mary's"}]
check("a project pronunciation beats the Street rule in Radio Promo",
      "Saint Mary's" in
      promo_speech.normalize_for_speech("Visit St. Mary's Grill.", saint)["spoken"],
      True)
check("and in Fan Radio",
      "Saint Mary's" in
      fan_speech.normalize_for_speech("Visit St. Mary's Grill.", saint)["spoken"],
      True)

# Every substitution is reported, so the team can show a client how the
# spot will be read rather than asking them to trust it.
changed = promo_speech.normalize_for_speech("Call 614-536-0768.")
check("and every change is reported rather than applied silently",
      bool(changed["changes"]), True)
check("with what it was and what it became",
      set(changed["changes"][0]) >= {"from", "to"}, True)

# The model is told not to write stage directions; the pass strips any that
# survive, because "(SFX: crowd noise)" read aloud is a ruined spot.
check("a stage direction never reaches the voice",
      "SFX" in promo_speech.normalize_for_speech(
          "(SFX: crowd) Kickoff is Sunday.")["spoken"], False)


# =====================================================================
section("Nothing a client approved is silently overwritten")
# =====================================================================
# Every draft, rewrite, tighten and hand edit is appended.

promo_store.add_version(SPEC_ID, "draft", {"note": "first"}, "Ada")
promo_store.add_version(SPEC_ID, "hand-edit", {"note": "second"}, "Ada")
versions = promo_store.get(SPEC_ID)["versions"]
check("Radio Promo appends a version rather than replacing one",
      [v["kind"] for v in versions], ["draft", "hand-edit"])
check("and each says who and when",
      all(v.get("actor") and v.get("at") for v in versions), True)

spot = {"id": "v1", "script": "First words.", "versions": []}
fan_store.push_version(spot, "first draft", "Ada")
spot["script"] = "Second words."
fan_store.push_version(spot, "hand edited", "Ada")
check("Fan Radio keeps both drafts, not just the latest",
      [v["script"] for v in spot["versions"]], ["First words.", "Second words."])
check("and says why each was written",
      [v["why"] for v in spot["versions"]], ["first draft", "hand edited"])

empty = {"id": "v2", "script": "", "versions": []}
fan_store.push_version(empty, "first draft", "Ada")
check("an empty script is not filed as a version of anything",
      empty["versions"], [])


# =====================================================================
section("Both tools are findable")
# =====================================================================
# A tool with no tile is invisible; CLAUDE.md counts six that were, for weeks.

creative = (ROOT / "hub" / "templates" / "creative.html").read_text()
check("Radio Promo has a tile", 'href="/tools/radio-promo/"' in creative, True)
check("and it is named", "<h3>Radio Promo</h3>" in creative, True)
check("Fan Radio has a tile", 'href="/tools/fan-radio/"' in creative, True)
check("and it is named", "<h3>Fan Radio</h3>" in creative, True)

from hub import sidebar                                       # noqa: E402
_side = str(getattr(sidebar, "__file__", ""))
_side_src = Path(_side).read_text() if _side else ""
check("both are on the sidebar too",
      "/tools/radio-promo" in _side_src and "/tools/fan-radio" in _side_src, True)

# Work filed against a client has to be nameable, or the client record reads
# as a client nobody has done any work for.
from hub import client_brand                                  # noqa: E402
check("Fan Radio's log name is one the client work log can name",
      "fan_radio" in client_brand.WORK_KINDS, True)


# =====================================================================
section("The client picker is offered a count, not a list run together")
# =====================================================================
# A registry row's `products` is the LIST of product names and
# `product_count` is the number. Fan Radio's picker route passed the list
# through, so the dropdown printed "RETARGETING: Website Retargeting,
# Search Engine Marketing (Pay Per Click),... product(s)" where a count
# belongs -- and the panel it filled was tall enough to bury the row a
# person was trying to click.

class _StubRegistry:
    @staticmethod
    def search_clients(q, limit=12):
        return [
            {"name": "Monogram Homes", "slug": "monogram-homes",
             "url": "https://monogramhomes.example",
             "products": ["RETARGETING: Website Retargeting",
                          "Search Engine Marketing (Pay Per Click)"],
             "product_count": 2},
            # A row from an older reader with no product_count and a list.
            {"name": "Monarca Academy", "slug": "monarca", "url": "",
             "products": ["Programmatic Display"]},
            # And one with nothing at all.
            {"name": "Monarch Behavioral Health", "slug": "monarch", "url": ""},
        ]

    @staticmethod
    def all_clients():
        return _StubRegistry.search_clients("")


_real_registry = fan_app.clients_registry
try:
    fan_app.clients_registry = _StubRegistry
    _pick = fan_app.app.test_client().get("/api/clients?q=mon").get_json()
finally:
    fan_app.clients_registry = _real_registry

check("the route answers", _pick.get("ok"), True)
_counts = [c["products"] for c in _pick.get("clients", [])]
check("every products value is a number",
      [isinstance(v, int) for v in _counts], [True, True, True])
check("and it is the count, not the joined names", _counts, [2, 1, 0])

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
