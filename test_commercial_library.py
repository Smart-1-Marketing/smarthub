"""Commercial Builder — what a spot is, and what its category needs.

    python3 test_commercial_library.py

Same shape as test_commercial_compliance.py: no pytest, no new dependencies,
a temporary data directory and a throwaway SQLite database.

## Why this file exists

  1. **One field held two questions.** `COMMERCIAL_TYPES` mixes how a spot
     gets made (stock, AI spokesperson) with what it is (testimonial, sale,
     seasonal) in a single-select, so "an AI spokesperson testimonial" was
     unsayable and the writer was told half of what had been decided.

  2. **Two taxonomies for one client is the year the two proposal builders
     cost.** An industry pack is creative data and `hub/industries.py` is
     media-plan data — different data, same clients, so the ids must match.

  3. **A wrong pack is worse than none**, because it reads as research
     somebody did rather than as a gap.

  4. **A picker whose answer changes nothing** is the failure
     `hub/current_marketing.py` was written to undo, sitting inside the module
     that undid it.

  5. **An archetype nobody can supply is a launch date that moves.** Nobody
     photographs the before, because at the time it was just a Tuesday.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cblib_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cblib-test-secret"
os.environ["PANEL_PASSWORD"] = "cblib-test-password"
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

from modules.commercial_builder import library_spec as ls                 # noqa: E402
from modules.commercial_builder import config as cb_config                # noqa: E402


section("Nothing in the library is a field that changes nothing")
# hub/current_marketing.unanswered_keys()'s rule. This module shipped four
# discovery questions read by nothing, so a rep could answer all four and the
# document came out identical. Empty today, which is the only way it was worth
# adding.
check("check_spec finds nothing dead", ls.check_spec(), [])
check("twelve archetypes", len(ls.ARCHETYPES), 12)
check("twelve packs", len(ls.INDUSTRY_PACKS), 12)


section("What it IS and how it gets made are two questions now")
check("the production methods are the three that are methods",
      sorted(ls.PRODUCTION_METHODS), ["ai_spokesperson", "ai_spokesperson_stock", "stock_vo"])
# The narrative values of commercial_type are NOT methods, and resolve to the
# default rather than to nothing — that is what those projects have always
# actually been.
for narrative in ("testimonial", "promo_sale", "seasonal", "weather_triggered",
                  "product_spotlight"):
    check(f"“{narrative}” is not a production method",
          ls.production_method(narrative), "stock_vo")
check("a real method survives", ls.production_method("ai_spokesperson"), "ai_spokesperson")
# Every value the Start page can produce still resolves to something.
for row in cb_config.COMMERCIAL_TYPES:
    check(f"“{row['id']}” resolves to a method",
          ls.production_method(row["id"]) in ls.PRODUCTION_METHODS, True)


section("A project saved before this reads as the archetype it always was")
# hub/target_areas.from_legacy()'s rule: converted on read, never migrated.
check("testimonial", ls.archetype_for({}, "testimonial"), ("testimonial", "legacy"))
check("promo_sale is offer-led", ls.archetype_for({}, "promo_sale"), ("offer_led", "legacy"))
check("weather_triggered is seasonal urgency",
      ls.archetype_for({}, "weather_triggered"), ("seasonal_urgency", "legacy"))
check("a chosen archetype beats the legacy value",
      ls.archetype_for({"archetype": "before_after"}, "testimonial"),
      ("before_after", "chosen"))
check("a method-only type falls to the default",
      ls.archetype_for({}, "stock_vo"), ("problem_solution", "default"))
# Named rather than collapsed: "a rep picked this" and "we inferred it from a
# column that meant two things" are different confidences.
check("garbage in the brief does not become an archetype",
      ls.archetype_for({"archetype": "banana"}, "")[1], "default")
check("every legacy mapping points at a real archetype",
      all(v in ls.ARCHETYPES for v in ls.LEGACY_ARCHETYPE.values()), True)


section("One client, one taxonomy")
# Two ids for one industry is how the same business gets described two ways
# depending on which tool somebody opened.
from hub.industries import INDUSTRIES                                     # noqa: E402
check("every shared id exists in hub/industries",
      sorted(set(ls.SHARED_WITH_HUB) - set(INDUSTRIES)), [])
check("and every shared id has a pack here",
      sorted(set(ls.SHARED_WITH_HUB) - set(ls.INDUSTRY_PACKS)), [])
# The Commercial Builder builds spots for categories the Proposal Builder has
# no industry page for, so the pack list is legitimately longer.
extra = sorted(set(ls.INDUSTRY_PACKS) - set(ls.SHARED_WITH_HUB))
check("the extra packs are named and deliberate",
      extra, ["home_services", "hvac", "medical_dental", "solar"])
check("and none of them collides with a hub id",
      sorted(set(extra) & set(INDUSTRIES)), [])


section("A wrong pack is worse than none")
for industry, want in (("HVAC & plumbing", "hvac"), ("Law Firms", "legal"),
                       ("Fine dining restaurant", "restaurant"),
                       ("Roofing contractor", "home_services"),
                       ("Solar installer", "solar")):
    key, _pack, state = ls.pack_for(industry)
    check(f"“{industry}” matches {want}", (key, state), (want, "matched"))
# Three answers, not two. Guessing from a name nobody matched puts a
# restaurant's vocabulary on a machine shop, and that reads as research.
key, pack, state = ls.pack_for("Precision machine shop")
check("an unmatched industry is generic", (key, state), ("", "unmatched"))
check("and carries no category words", pack["stock"], [])
key, pack, state = ls.pack_for("")
check("no industry recorded is its own answer", state, "not_recorded")
check("which is different from unmatched",
      ls.pack_for("Precision machine shop")[2] != ls.pack_for("")[2], True)


section("The choice reaches the model as instruction, not as a label")
# hub/current_marketing.for_prompt()'s rule: a model handed a label writes
# label-flavored adjectives; one told what to DO writes a different script.
g = ls.prompt_guidance("before_after", "Roofing contractor")
check("the structure is handed over", bool(g["structure"]), True)
check("so is what it is bad at", bool(g["avoid_because"]), True)
check("the category's hooks travel", bool(g["category_hooks"]), True)
check("and what falls flat in it", bool(g["category_avoid"]), True)
check("the stock vocabulary is both halves",
      "before and after" in g["stock_vocabulary"]
      and "roofer at work" in g["stock_vocabulary"], True)
# A model told a category it does not have is a model inventing one.
un = ls.prompt_guidance("before_after", "Precision machine shop")
check("an unmatched category names no category", un["category"], "")
check("and says so rather than staying silent", bool(un["category_note"]), True)
check("a matched one has nothing to explain",
      ls.prompt_guidance("before_after", "Roofing contractor")["category_note"], "")


section("Suggestions, never a filter")
s = ls.suggested_archetypes("Roofing contractor")
check("a matched category suggests some", bool(s["keys"]), True)
check("all of which are real", all(k in ls.ARCHETYPES for k in s["keys"]), True)
# An unusual spot for a category is often the reason it works, and a picker
# that hides nine of twelve makes that impossible.
check("but it never shortens the list", len(ls.ARCHETYPES), 12)
check("an unmatched category still answers",
      ls.suggested_archetypes("Precision machine shop")["state"], "unmatched")


section("An archetype names what it needs, and readiness reads that")
check("a testimonial needs a customer",
      [n["key"] for n in ls.ARCHETYPES["testimonial"]["needs"]], ["testimonial_source"])
check("a before-and-after needs the before",
      [n["key"] for n in ls.ARCHETYPES["before_after"]["needs"]], ["before_footage"])
gaps = ls.readiness("before_after", {})
check("an empty brief is not ready", gaps["ready"], False)
check("and the gap asks the question", "BEFORE" in gaps["gaps"][0]["question"], True)
check("with the reason beside it", "Tuesday" in gaps["gaps"][0]["why"], True)
check("answering it clears the gap",
      ls.readiness("before_after", {"before_footage": "phone photos"})["ready"], True)
# An archetype with nothing to supply is genuinely ready, and says so rather
# than printing an empty checklist that reads as unfinished.
ready = ls.readiness("problem_solution", {})
check("an archetype that needs nothing is ready", ready["ready"], True)
check("and says why rather than showing nothing", bool(ready["note"]), True)
# Derived rather than typed out, so an archetype that gains a need next month
# is saved by the route without anybody remembering to widen a list.
check("NEED_KEYS covers every archetype's needs",
      sorted(ls.NEED_KEYS),
      sorted({n["key"] for a in ls.ARCHETYPES.values() for n in a["needs"]}))


section("An archetype names the rules it tends to bring with it")
# Read by nothing here — compliance_spec.py scans the finished copy and is the
# authority — but printed while the archetype is picked, because it is worth
# knowing before the script exists.
from modules.commercial_builder import compliance_spec as cs              # noqa: E402
check("a testimonial names the FTC guides",
      ls.ARCHETYPES["testimonial"]["engages"], ["ftc_endorsements"])
check("an offer-led spot names Truth in Lending",
      ls.ARCHETYPES["offer_led"]["engages"], ["reg_z"])
# A regime named here that compliance_spec does not have is a pointer at
# nothing, which is the confidently wrong answer wearing a citation.
for key, spec in ls.ARCHETYPES.items():
    for regime in spec["engages"]:
        check(f"“{key}” points at a regime that exists", regime in cs.REGIMES, True)


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
import werkzeug.test                                                      # noqa: E402
from wsgi import application, hub_app                                     # noqa: E402
from modules.commercial_builder.db import db                              # noqa: E402
from modules.commercial_builder.models import Client                      # noqa: E402

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)

row = staff.post(MOUNT + "/api/clients",
                 json={"name": "Ridge Roofing", "website": "ridge.test"}
                 ).get_json()["client"]
with hub_app.app_context():
    c = db.session.get(Client, row["id"])
    c.industry = "Roofing contractor"
    db.session.commit()
pid = staff.post(MOUNT + "/api/projects",
                 json={"client_id": row["id"], "lengths": [30], "formats": ["16:9"],
                       "commercial_type": "product_spotlight", "platform": "ctv"}
                 ).get_json()["projects"][0]["id"]


section("The picker is on the screen the offer is on")
page = staff.get(MOUNT + f"/project/{pid}/brief").get_data(as_text=True)
check("all twelve are offered", page.count("data-needs="), 12)
# An inferred archetype is a weaker claim than a chosen one, and saying which
# beats drawing a selection somebody never made.
check("an inferred one says it was inferred", "inferred from the" in page, True)
check("the category's usual ones are named", "Common in this category" in page
      or "Before and after" in page, True)
check("the bubble is placed", "commercial_builder.brief.archetype" in page, True)
from hub import help as hub_help                                          # noqa: E402
check("and it resolves to content",
      bool(hub_help.get("commercial_builder.brief.archetype")), True)
# Handed over as data so tools/jscheck.py can pass the page's real JavaScript
# to node --check instead of skipping it for carrying Jinja.
check("the tables reach the browser as JSON",
      'type="application/json" id="archetype-needs"' in page, True)
check("and the browser holds no second copy of the archetype table",
      page.count("beat_emphasis"), 0)


section("Choosing one changes what gets built")
staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "Roof replacement", "archetype": "before_after",
                "before_footage": "Client has phone photos from the estimate"})
saved = staff.get(MOUNT + f"/api/projects/{pid}").get_json()["project"]
check("the archetype is saved on the brief", saved["brief"]["archetype"], "before_after")
check("and so is what it asked for",
      "phone photos" in saved["brief"]["before_footage"], True)
# It lives in the brief JSON because create_all() adds no column to an
# existing table, and commercial_type is read by compliance_spec.
check("commercial_type is untouched", saved["commercial_type"], "product_spotlight")

first = staff.post(MOUNT + f"/api/projects/{pid}/concepts").get_json()["concepts"]
staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "Roof replacement", "archetype": "recruitment",
                "roles": "Crew leads, $28/hr"})
second = staff.post(MOUNT + f"/api/projects/{pid}/concepts").get_json()["concepts"]
# A picker whose answer produces identical output whatever is chosen is the
# thing being fixed — and mock mode is where a developer forms their
# impression of whether the field does anything at all.
check("the concepts differ", first[0]["title"] != second[0]["title"], True)
check("and they name the archetype", "hiring" in second[0]["title"].lower(), True)


section("An unanswered need shows up where the work is")
staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "Roof replacement", "archetype": "testimonial"})
qc = staff.post(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
check("the check ran", "archetype_ready" in qc, True)
check("it is not satisfied", qc["archetype_ready"]["passed"], False)
check("and it asks the question", "Which customer" in qc["archetype_ready"]["message"], True)
# A rep may have the customer lined up and not have typed it here, so it asks
# rather than refuses — hub/creative_needs.py's posture.
check("as a warning, never a refusal", qc["archetype_ready"]["level"], "warn")
check("and it is declared advisory server-side",
      "archetype_ready" in __import__(
          "modules.commercial_builder.services.qc_service",
          fromlist=["x"]).ADVISORY_CHECKS, True)
for js_file in ("blueprint.js", "preview.js"):
    text = (ROOT / "modules/commercial_builder/static/js" / js_file).read_text()
    block = text[text.index("QC_LABELS = {"):]
    check(f"{js_file} draws the row",
          "archetype_ready:" in block[:block.index("};")], True)

staff.put(MOUNT + f"/api/projects/{pid}/brief",
          json={"what_advertising": "Roof replacement", "archetype": "testimonial",
                "testimonial_source": "The Hendersons, agreed on the 3rd"})
qc = staff.post(MOUNT + f"/api/projects/{pid}/qc").get_json()["qc_results"]
check("answering it settles the check", qc["archetype_ready"]["passed"], True)


section("Switching archetype does not eat what was already typed")
# Only what the CHOSEN archetype asks for is sent, so an answer given under a
# different one is not written over with an empty string.
page = staff.get(MOUNT + f"/project/{pid}/brief").get_data(as_text=True)
check("the before-and-after answer survived", "phone photos" in page, True)
check("and so did the recruitment one", "Crew leads" in page, True)
check("beside the current archetype's own", "Hendersons" in page, True)


section("A client with no industry is offered everything and told why")
bare = staff.post(MOUNT + "/api/clients",
                  json={"name": "Nameless Co", "website": "nameless.test"}
                  ).get_json()["client"]
pid2 = staff.post(MOUNT + "/api/projects",
                  json={"client_id": bare["id"], "lengths": [30], "formats": ["16:9"],
                        "commercial_type": "stock_vo", "platform": "ctv"}
                  ).get_json()["projects"][0]["id"]
page2 = staff.get(MOUNT + f"/project/{pid2}/brief").get_data(as_text=True)
check("all twelve are still offered", page2.count("data-needs="), 12)
check("and no category is claimed",
      ls.prompt_guidance("problem_solution", "")["category"], "")
check("with the absence explained",
      "no industry recorded" in ls.prompt_guidance("problem_solution", "")["category_note"],
      True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
