"""The proposal's targeting — the map, the pastes, the research, the bullets.

    python3 test_proposal_targeting.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, and **nothing here reaches a third
party**. The tile server and the geocoder are stubbed, the way CI requires:
a gate that fails because somebody else's API is down teaches people to
ignore it.

## Why this file exists

Four things, each of which fails by producing something plausible:

  * **A bulleted list run into a sentence.** The models write
    "we will reach three areas: • Carmel • Fishers • Noblesville" often
    enough that asking politely is not a fix, and all three renderers set
    that as one paragraph. Nothing errors; a client simply reads a sentence
    with dots in it. The shape is enforced now, in one place, and the three
    renderers read that one place — so what is asserted here is that they
    still all do.

  * **A map that is about somewhere else.** A geocoder handed "Carmel" with
    no state answers Carmel-by-the-Sea, California, and a map of the wrong
    Carmel on an Indiana proposal is the confidently wrong answer this
    codebase keeps having to undo. A named state must match or the area is
    reported as not found — never quietly plotted somewhere plausible.

  * **A paste that quietly assumed things.** Twelve pasted lines producing
    nine areas is a campaign missing three locations that nobody can see is
    missing, and a radius this module chose is a decision nobody made. Every
    line comes back with the sentence saying how it was read, and an
    unreadable one comes back by name.

  * **A researched competitor treated as a fact.** Everything the research
    returns is `accepted: False` until a person ticks it, and an address it
    did not give is never derived from the name.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-targeting-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "targeting-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

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
    print("-" * 62)


# ---------------------------------------------------------------------------
section("a list is a list, in all three renderers")
# ---------------------------------------------------------------------------
from hub import proposal_spec as spec                              # noqa: E402

RUN_ON = ("We will reach three areas: • Carmel • Fishers • Noblesville. "
          "Each carries its own budget.")
cleaned = spec.clean_ai_text(RUN_ON)
check("a run-on bullet list is broken onto its own lines",
      cleaned.count("\n• ") == 3, repr(cleaned))
check("and the lead-in above it survives",
      cleaned.split("\n")[0].startswith("We will reach three areas:"), cleaned)
check("cleaning is still idempotent",
      spec.clean_ai_text(cleaned) == cleaned, repr(spec.clean_ai_text(cleaned)))

blocks = spec.blocks(RUN_ON)
check("blocks() reads it as a paragraph then a list",
      [b["kind"] for b in blocks] == ["para", "list"], blocks)
# The trailing sentence stays attached to the last item, and deliberately:
# where a list ends is not knowable from the text, and cutting at the first
# full stop would split "Carmel, IN. 10 miles" into two items.
check("with every item its own entry",
      [i["text"] for i in blocks[1]["items"]][:2] == ["Carmel", "Fishers"]
      and blocks[1]["items"][2]["text"].startswith("Noblesville"),
      blocks[1]["items"])
check("a Markdown dash list becomes a list too",
      [b["kind"] for b in spec.blocks("Lead:\n- one\n- two")] == ["para", "list"])
check("bold inside an item survives as a run",
      spec.blocks("• <b>Reach</b> matters")[0]["items"][0]["runs"][0] == ("Reach", True),
      spec.blocks("• <b>Reach</b> matters")[0]["items"][0]["runs"])
check("an empty bullet is not an item",
      spec.blocks("• \n• real") == [{"kind": "list",
                                     "items": [{"text": "real",
                                                "runs": [("real", False)]}]}],
      spec.blocks("• \n• real"))
check("prose with no bullets is untouched",
      [b["kind"] for b in spec.blocks("One para.\nAnother.")] == ["para", "para"])
# The directive is the other half: enforcing the shape and still asking for
# it is deliberate, because a model told to write one item per line mostly
# does, and the enforcement is for the times it does not.
check("the directive asks for one item per line",
      "one item per line" in spec.FORMATTING_DIRECTIVE
      and "never run the items together" in spec.FORMATTING_DIRECTIVE.lower(),
      spec.FORMATTING_DIRECTIVE)
check("rich_runs still answers per line, for Word",
      spec.rich_runs("a <b>b</b>") == [[("a ", False), ("b", True)]],
      spec.rich_runs("a <b>b</b>"))

# ---------------------------------------------------------------------------
section("pasting a list of locations")
# ---------------------------------------------------------------------------
from hub import target_areas as areas                              # noqa: E402

PASTE = """Location | City | Radius
Carmel showroom | Carmel, IN | 10
Fishers showroom\tFishers, IN\t15\tweekends only
Noblesville, IN 20mi
Muncie, IN
Indianapolis DMA
Ohio statewide
46032, 46033, 46074
Nationwide
ring Bob about the third rooftop when he is back from leave
"""
read = areas.parse_paste(PASTE)
by_label = [areas.label(a) for a in read["areas"]]
check("a spreadsheet header row is not a location",
      not any("Location" in lbl for lbl in by_label), by_label)
check("name, city and radius columns are read",
      "Carmel showroom — Carmel, IN + 10-mile radius" in by_label, by_label)
check("a radius column with notes after it is still the radius",
      any(a["radius"] == 15 and a["notes"] == "weekends only"
          for a in read["areas"]), read["areas"])
check("a radius written in the line is read",
      "Noblesville, IN + 20-mile radius" in by_label, by_label)
check("a DMA is a DMA", "Indianapolis DMA" in by_label, by_label)
check("statewide is statewide", "Ohio (statewide)" in by_label, by_label)
check("a line of ZIP Codes becomes one area holding them",
      any(len(areas.zip_list(a["zips"])) == 3 for a in read["areas"]), read["areas"])
check("national is national", "National" in by_label, by_label)
# The half that matters: an assumption is stated, not made quietly.
muncie = next(r for r in read["rows"] if r["line"].startswith("Muncie"))
check("a line with no radius says which radius was assumed",
      "no radius was stated" in muncie["read"] and "10 miles" in muncie["read"],
      muncie["read"])
check("a line that is prose is skipped BY NAME, not counted",
      [s["line"] for s in read["skipped"]] == [
          "ring Bob about the third rooftop when he is back from leave"],
      read["skipped"])
check("and the reason says why", bool(read["skipped"][0]["reason"]))
check("the note adds up", "not understood" in read["note"], read["note"])

again = areas.parse_paste("Carmel, IN 10mi", existing=read["areas"])
check("a location already on the campaign is reported, not added twice",
      not again["areas"] and again["rows"][0]["duplicate"], again)
check("nothing is invented from an empty paste",
      areas.parse_paste("")["areas"] == [] and areas.parse_paste("  ")["note"])

places = areas.parse_places(
    "Name | Address | Note\n"
    "Riverside Dental | 1200 Main St, Carmel, IN 46032 | their implant patients\n"
    "Lucas Oil Stadium, 500 S Capitol Ave, Indianapolis, IN\n"
    "Smith, Jones & Co\n", kind="venue")
check("a pasted competitor keeps its address",
      places["places"][0]["address"] == "1200 Main St, Carmel, IN 46032",
      places["places"][0])
check("an address inside one field is split off the name",
      places["places"][1]["name"] == "Lucas Oil Stadium"
      and places["places"][1]["address"].startswith("500 S Capitol"),
      places["places"][1])
check("a business with a comma in its name keeps it",
      places["places"][2]["name"] == "Smith, Jones & Co", places["places"][2])
check("and no address is invented for it",
      places["places"][2]["address"] == "", places["places"][2])
check("the kind asked for is the kind stored",
      {p["kind"] for p in places["places"]} == {"venue"})

# ---------------------------------------------------------------------------
section("the map: what it draws, and what it refuses to")
# ---------------------------------------------------------------------------
from hub import target_map as tmap                                 # noqa: E402
from PIL import Image                                              # noqa: E402

# Both providers stubbed. Nothing in CI may depend on somebody else's API,
# and the answers below are the ones that matter: a state that matches, a
# state that does not, and a tile server that will not answer.
_PLACES = {
    ("carmel", "indiana"): (39.9784, -86.1180),
    ("carmel", "california"): (36.5552, -121.9233),
    ("fishers", "indiana"): (39.9556, -86.0139),
}
_LOOKUPS = []


def _fake_city(name, state):
    _LOOKUPS.append((name, state))
    hits = [(n, s) for (n, s) in _PLACES if n == name.strip().lower()]
    if not hits:
        return None
    if state:
        for n, s in hits:
            if s == state.strip().lower():
                lat, lon = _PLACES[(n, s)]
                return {"lat": lat, "lon": lon, "label": f"{name}, {state}",
                        "source": "place name"}
        return None                     # named a state, found nothing in it
    lat, lon = _PLACES[hits[0]]
    return {"lat": lat, "lon": lon, "label": name, "source": "place name"}


def _fake_zip(zipcode):
    return ({"lat": 39.9784, "lon": -86.1180, "label": "Carmel, IN",
             "source": "postal code"} if zipcode == "46032" else None)


tmap._city_lookup = _fake_city
tmap._zip_lookup = _fake_zip

_TILES = {"served": 0}


def _fake_tile(session, z, x, y, deadline=0.0):
    _TILES["served"] += 1
    return Image.new("RGB", (tmap.TILE, tmap.TILE), (238, 240, 232))


tmap._fetch_tile = _fake_tile

check("a place with its state resolves", bool(tmap.geocode("Carmel, IN")))
check("and the second lookup is cached rather than asked again",
      (lambda before: (tmap.geocode("Carmel, IN"), len(_LOOKUPS) == before)[1])(len(_LOOKUPS)))
check("a state that matches nothing is NOT plotted somewhere plausible",
      tmap.geocode("Carmel, TX") is None)
check("a ZIP Code resolves on its own", bool(tmap.geocode("46032")))

CAMPAIGN = [
    {"name": "Carmel showroom", "type": "City/ZIP + Radius",
     "origin": "Carmel, IN", "radius": 10},
    {"name": "Fishers showroom", "type": "City/ZIP + Radius",
     "origin": "Fishers, IN", "radius": 15},
    {"type": "DMA", "dma": "Indianapolis"},
    {"type": "Statewide", "state": "Indiana"},
    {"name": "Ghost store", "type": "City/ZIP + Radius",
     "origin": "Nowhereville, ZZ", "radius": 5},
]
placed = tmap.locate(CAMPAIGN)
check("every radius that resolved is a point on the map",
      len(placed["points"]) == 2, placed["points"])
covered = {r["label"]: r for r in placed["not_plotted"]}
check("a DMA is named as covered rather than drawn as a blob",
      any("DMA" in lbl for lbl in covered), covered)
check("so is a statewide buy", any("statewide" in lbl for lbl in covered), covered)
check("and an origin nobody can find is its own answer, with the reason",
      any("could not find" in r["reason"] for r in placed["not_plotted"]),
      placed["not_plotted"])
check("a DMA's reason and a bad spelling's reason are not the same sentence",
      len({r["reason"] for r in placed["not_plotted"]}) == len(placed["not_plotted"]))

png, meta = tmap.render(CAMPAIGN, width=900, height=520)
check("the map is drawn", bool(png) and png[:4] == b"\x89PNG", meta.get("reason"))
check("it reports what it plotted", len(meta["plotted"]) == 2, meta)
check("it carries the tile attribution, which is a licence condition",
      "OpenStreetMap" in meta["attribution"], meta["attribution"])
check("and the areas it could not draw travel with it",
      len(meta["not_plotted"]) == 3, meta["not_plotted"])

served_before = _TILES["served"]
png2, _ = tmap.render(CAMPAIGN, width=900, height=520)
check("the same campaign is not re-fetched tile by tile",
      png2 == png and _TILES["served"] == served_before)

check("a campaign with nothing placeable draws no map at all",
      tmap.render([{"type": "National"}])[0] is None)
check("and says why, in words a rep can act on",
      "city or a ZIP" in tmap.render([{"type": "National"}])[1]["reason"],
      tmap.render([{"type": "National"}])[1]["reason"])


def _dead_tile(session, z, x, y, deadline=0.0):
    return None


tmap._fetch_tile = _dead_tile
dead, dead_meta = tmap.render([{"name": "Solo", "type": "City/ZIP + Radius",
                                "origin": "Fishers, IN", "radius": 12}])
check("a tile server that will not answer produces no map rather than a grid "
      "of grey boxes", dead is None, len(dead or b""))
check("and says the target areas are unaffected",
      "unaffected" in (dead_meta["reason"] or ""), dead_meta["reason"])
tmap._fetch_tile = _fake_tile

# ---------------------------------------------------------------------------
section("through the running app")
# ---------------------------------------------------------------------------
from werkzeug.test import Client                                   # noqa: E402
import wsgi                                                        # noqa: E402
from hub import auth                                               # noqa: E402

# The module the composed app actually mounts. `wsgi._try_load` imports each
# module from its path under a name of its own, so
# `modules.sales_builder.app` is a *second* instance of the same file --
# importing that one and stubbing on it patches nothing the running app will
# ever call, and every assertion below would have been about a module with no
# requests going through it. hub/* is shared either way, which is why the map
# stubs work and this one did not.
builder = sys.modules.get("salesb_app")
if builder is None:                             # pragma: no cover - mount failed
    from modules.sales_builder import app as builder

http = Client(wsgi.application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                domain="localhost")


def api(method, path, **kw):
    return getattr(http, method)(path, **kw).get_json()


state = {
    "client": "Riverstone Dental", "industry": "Healthcare / Dental",
    "months": 6, "budget": 8000, "objectives": ["Lead Generation"],
    "targetAreas": [
        {"name": "Carmel showroom", "type": "City/ZIP + Radius",
         "origin": "Carmel, IN", "radius": 10},
        {"type": "DMA", "dma": "Indianapolis"},
    ],
    "targetsOfInterest": [
        {"kind": "competitor", "name": "Riverside Dental",
         "address": "1200 Main St, Carmel, IN 46032", "note": "their implants"},
    ],
    "sections": [
        {"id": "areas", "title": "Audience & Market Strategy", "kind": "areas",
         "enabled": True,
         "body": "We will reach three areas: • Carmel • Fishers • Noblesville"},
    ],
    "selectedPackage": {"name": "Recommended", "monthly": 8000, "total": 48000},
    "items": [{"category": "DISPLAY", "product": "Category", "rate": "CPM",
               "rateValue": 4.25, "dollars": 8000}],
}
quote = api("post", "/sales/builder/api/quotes", json={"data": state})["quote"]

pasted = api("post", "/sales/builder/api/paste-areas",
             json={"text": "Fishers, IN 12mi\nnot a place at all, just a sentence "
                           "somebody typed into the wrong box",
                   "areas": state["targetAreas"]})
check("the paste route reads what it can", len(pasted["areas"]) == 1, pasted)
check("and hands back the line it could not, rather than a count",
      len(pasted["skipped"]) == 1, pasted["skipped"])
check("nothing is added by the route itself — the rep presses Add",
      api("get", f"/sales/builder/api/quotes/{quote['id']}")
      ["quote"]["target_areas"].__len__() == 2)

pasted_places = api("post", "/sales/builder/api/paste-places",
                    json={"text": "Riverside Dental\nCarmel Racquet Club",
                          "kind": "competitor",
                          "existing": state["targetsOfInterest"]})
check("a competitor already named is not pasted in twice",
      [p["name"] for p in pasted_places["places"]] == ["Carmel Racquet Club"],
      pasted_places["places"])

status = api("post", "/sales/builder/api/target-map/status",
             json={"areas": state["targetAreas"]})
check("the builder can ask what the map will show", status["measured"], status)
check("a DMA is listed as covered and not drawn",
      len(status["not_plotted"]) == 1 and not status["unfound"], status)
check("and the two lists are kept apart, because only one is somebody's to fix",
      "not_plotted" in status and "unfound" in status)

img = http.get(f"/sales/builder/api/quotes/{quote['id']}/target-map.png?v=1")
check("the map serves as a PNG", img.status_code == 200
      and img.headers["Content-Type"].startswith("image/png"), img.status_code)
check("and is cached by the browser rather than rebuilt per redraw",
      "max-age" in img.headers.get("Cache-Control", ""),
      img.headers.get("Cache-Control"))

# The map is a section setting, like a generated table, and one flag decides
# it for all three renderers.
off = json.loads(json.dumps(state))
off["sections"][0]["showMap"] = False
off_quote = api("post", "/sales/builder/api/quotes", json={"data": off})["quote"]
check("a proposal can leave the map out",
      builder.show_map(off) is False and builder.show_map(state) is True)
check("and asking for it then answers 404 rather than a placeholder image",
      http.get(f"/sales/builder/api/quotes/{off_quote['id']}/target-map.png").status_code
      == 404)

pdf = http.get(f"/sales/builder/api/quotes/{quote['id']}/pdf")
check("the PDF builds", pdf.status_code == 200 and pdf.data[:4] == b"%PDF",
      pdf.status_code)
plain_pdf = http.get(f"/sales/builder/api/quotes/{off_quote['id']}/pdf")
# The same proposal, one flag apart. Size is the only thing a test can read
# out of a reportlab PDF without parsing it, and an embedded image is worth
# thousands of bytes -- a map that quietly failed would make these equal.
check("and the map is genuinely in it rather than silently skipped",
      plain_pdf.status_code == 200
      and len(pdf.data) - len(plain_pdf.data) > 2000,
      (len(plain_pdf.data), len(pdf.data)))
docx = http.get(f"/sales/builder/api/quotes/{quote['id']}/docx")
check("the Word export builds with the same map",
      docx.status_code == 200 and len(docx.data) > 20000, len(docx.data))
check("and it is bigger than the one without a picture in it",
      len(docx.data) > len(http.get(
          f"/sales/builder/api/quotes/{off_quote['id']}/docx").data))

# What reaches the PDF: the section body as blocks, not as one sentence.
stored = api("get", f"/sales/builder/api/quotes/{quote['id']}",
             query_string={"data": "1"})["quote"]
body = ((stored.get("data") or {}).get("sections") or [{}])[0].get("body") \
    or state["sections"][0]["body"]
check("the run-on list reaches the document as a list",
      [b["kind"] for b in spec.blocks(body)] == ["para", "list"], spec.blocks(body))

# ---------------------------------------------------------------------------
section("researching who to target")
# ---------------------------------------------------------------------------
# The model is stubbed. What is asserted is the handling: a suggestion stays
# a suggestion, an address is carried only where one was given, and the two
# kinds of empty answer are told apart.
_ASKED = {}


def _fake_ai(prompt, max_output_tokens=6000):
    _ASKED["prompt"] = prompt
    return json.dumps({"targets": [
        {"kind": "competitor", "name": "Riverside Dental",
         "address": "1200 Main St", "why": "their implant patients"},
        {"kind": "venue", "name": "Carmel Racquet Club", "address": "",
         "why": "where their patients spend Saturday"},
        {"kind": "nonsense", "name": "Fishers Farmers Market",
         "why": "weekend footfall", "confidence": "confirmed"},
        {"name": "", "why": "an empty row the model returned"},
    ]})


_real_ai = builder._openai_response
builder._openai_response = _fake_ai

found = api("post", "/sales/builder/api/find-targets",
            json={"client": "Riverstone Dental", "areas": state["targetAreas"],
                  "objectives": ["Lead Generation"]})
check("the research returns rows", len(found["targets"]) == 3, found)
check("every row arrives unticked — a researched name is a suggestion",
      all(row["accepted"] is False for row in found["targets"]))
check("an address the model gave is carried",
      found["targets"][0]["address"] == "1200 Main St")
check("and one it did not is left empty rather than derived from the name",
      found["targets"][1]["address"] == "")
check("an unknown kind falls back to competitor rather than reaching the IO",
      found["targets"][2]["kind"] == "competitor", found["targets"][2])
check("a nameless row is dropped", all(r["name"] for r in found["targets"]))
check("the answer says the addresses are unverified",
      "check any address" in found["note"].lower(), found["note"])
check("the prompt forbids inventing an address",
      "leave the address empty" in _ASKED["prompt"], "")
check("and forbids naming a business that does not exist there",
      "Do not invent a name" in _ASKED["prompt"], "")
check("the search is scoped to the campaign's own areas",
      "Carmel, IN" in _ASKED["prompt"], "")

dupes = api("post", "/sales/builder/api/find-targets",
            json={"client": "Riverstone Dental", "areas": state["targetAreas"],
                  "existing": [{"name": "Riverside Dental"}]})
check("a business already named on the campaign is not offered again",
      "Riverside Dental" not in [r["name"] for r in dupes["targets"]],
      [r["name"] for r in dupes["targets"]])

no_area = http.post("/sales/builder/api/find-targets", json={"client": "X"})
check("with nowhere to look, it refuses rather than returning national brands",
      no_area.status_code == 400, no_area.status_code)


def _empty_ai(prompt, max_output_tokens=6000):
    return json.dumps({"targets": []})


builder._openai_response = _empty_ai
empty = api("post", "/sales/builder/api/find-targets",
            json={"client": "X", "areas": state["targetAreas"]})
check("nobody worth naming is an answer, and says so",
      empty["ok"] and empty["targets"] == []
      and "does not mean the search failed" in empty["note"], empty)


def _broken_ai(prompt, max_output_tokens=6000):
    raise RuntimeError("upstream is down")


builder._openai_response = _broken_ai
broken = http.post("/sales/builder/api/find-targets",
                   json={"client": "X", "areas": state["targetAreas"]})
check("and 'we could not look' is a different answer from 'there is nobody'",
      broken.status_code == 502 and broken.get_json()["ok"] is False,
      broken.status_code)
builder._openai_response = _real_ai

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
