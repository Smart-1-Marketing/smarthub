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
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-targeting-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
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
check("it carries the tile attribution, which is a license condition",
      "OpenStreetMap" in meta["attribution"], meta["attribution"])
check("and the areas it could not draw travel with it",
      len(meta["not_plotted"]) == 3, meta["not_plotted"])

served_before = _TILES["served"]
png2, _ = tmap.render(CAMPAIGN, width=900, height=520)
check("the same campaign is not re-fetched tile by tile",
      png2 == png and _TILES["served"] == served_before)

# The shape that broke the PDF: three areas running north-south crop to a
# PORTRAIT picture, and a portrait map drawn at the text column's width is
# 8.8 inches tall -- most of a page, with the page above it half empty, and
# one slightly taller campaign away from a flowable the page frame cannot
# place at all. Both ends are bounded now: the crop comes back landscape,
# and the renderer caps the height whatever arrives.
NORTH_SOUTH = [
    {"name": "South", "type": "City/ZIP + Radius", "origin": "Carmel, IN", "radius": 8},
    {"name": "Middle", "type": "City/ZIP + Radius", "origin": "Fishers, IN", "radius": 8},
]
tall_png, tall_meta = tmap.render(NORTH_SOUTH, width=900, height=520)
from PIL import Image as _Im                                       # noqa: E402
import io as _io                                                   # noqa: E402
_w, _h = _Im.open(_io.BytesIO(tall_png)).size
check("a north-south campaign still comes back landscape",
      _w / _h >= 1.25, (_w, _h, round(_w / _h, 2)))
check("and the key underneath it is counted in that, not added after",
      _h >= 200, (_w, _h))

check("a campaign with nothing placeable draws no map at all",
      tmap.render([{"type": "National"}])[0] is None)

# Four kinds of missing are four answers, and the summary used to print one of
# them whichever had happened: *an area needs a city or a ZIP Code*, over a
# national buy that is covered and correctly not drawn, and over a city the
# geocoder simply could not reach -- which on the areas screen lands directly
# above a box naming the city the area plainly carries. Only two of the four
# are anybody's to fix, so only those two ask for a fix.
national = tmap.render([{"type": "National"}])[1]["reason"]
check("a buy that is covered without being drawn says so, and asks for nothing",
      "covered without being drawn" in national and "ZIP" not in national,
      national)

nowhere = tmap.render([{"name": "Somewhere", "type": "City/ZIP + Radius",
                        "radius": 10}])[1]["reason"]
check("an area carrying no origin at all is the one that does need a city",
      "city or a ZIP" in nowhere, nowhere)

unfound = tmap.render([{"name": "Nowheresville", "type": "City/ZIP + Radius",
                        "origin": "Carmel, TX", "radius": 10}])[1]["reason"]
check("and a place we could not look up never reads as a field somebody "
      "forgot to fill in",
      "could not look up" in unfound and "city or a ZIP" not in unfound,
      unfound)

check("no target areas at all is its own answer",
      "no target areas" in tmap.render([])[1]["reason"],
      tmap.render([])[1]["reason"])
check("and both readers of that sentence are the same reader",
      "nothing_plotted_reason" in open(
          os.path.join(ROOT, "modules", "sales_builder", "app.py")).read())


def _dead_tile(session, z, x, y, deadline=0.0):
    return None


tmap._fetch_tile = _dead_tile
dead, dead_meta = tmap.render([{"name": "Solo", "type": "City/ZIP + Radius",
                                "origin": "Fishers, IN", "radius": 12}])
check("a tile server that will not answer produces no map rather than a grid "
      "of gray boxes", dead is None, len(dead or b""))
check("and says the target areas are unaffected",
      "unaffected" in (dead_meta["reason"] or ""), dead_meta["reason"])
tmap._fetch_tile = _fake_tile

# ---------------------------------------------------------------------------
section("through the running app")
# ---------------------------------------------------------------------------
from io import BytesIO                                             # noqa: E402
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
    "kpis": ["Cost per lead", "Cost per store visit"],
    "sections": [
        {"id": "areas", "title": "Audience & Market Strategy", "kind": "areas",
         "enabled": True,
         "body": "We will reach three areas: • Carmel • Fishers • Noblesville"},
        {"id": "kpis", "title": "How We Measure Success", "kind": "kpis",
         "enabled": True, "body": ""},
        {"id": "roi", "title": "Expected Results & ROI", "kind": "roi",
         "enabled": True, "body": ""},
    ],
    "selectedPackage": {"name": "Recommended", "monthly": 8000, "total": 48000},
    "items": [{"category": "DISPLAY", "product": "Category", "rate": "CPM",
               "rateValue": 4.25, "dollars": 5000},
              {"category": "OTT", "product": "Connected TV - Targeted",
               "rate": "CPM", "rateValue": 35.0, "dollars": 3000}],
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

# Taking it off has to be findable, not merely possible. The 🗺 in the
# section tools row is drawn at 45% opacity among five icons -- the same
# shape as the Smart 1 Ads estimate's per-section pencils, which nobody
# found until the page said so above them. The one question a map provokes
# is "that doesn't look right, how do I get rid of it?", so the answer is in
# words under the picture. Asserted against the template because a control
# that quietly reverts to icon-only reads on screen as no control at all.
_builder_page = (ROOT and open(os.path.join(
    ROOT, "modules", "sales_builder", "templates", "index.html"),
    encoding="utf-8").read())
check("the map carries a remove control in words, not only an icon",
      "Remove this map" in _builder_page, "")
check("and it is the same one flag the three renderers read",
      _builder_page.count("toggleSecMap(") >= 3, _builder_page.count("toggleSecMap("))
check("a removed map offers its own way back",
      "Put the map back" in _builder_page, "")
check("and the areas screen says where the map is removed",
      "it can be removed there without changing any of these areas" in _builder_page, "")

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
# The height cap is the guarantee at the renderer's end. A flowable taller
# than the printable page is not a big picture, it is a PDF that fails to
# build — so this asserts the bound rather than the picture.
from reportlab.lib.units import inch as _inch                      # noqa: E402
check("the map is bounded on both axes, not just scaled to the column",
      builder.MAP_MAX_H <= 4.5 * _inch and builder.MAP_MAX_W <= 7.4 * _inch,
      (builder.MAP_MAX_W / _inch, builder.MAP_MAX_H / _inch))
check("and that leaves room for the section's copy and table on a page",
      builder.MAP_MAX_H < 9.0 * _inch / 2)

tall_state = json.loads(json.dumps(state))
tall_state["targetAreas"] = NORTH_SOUTH
tall_quote = api("post", "/sales/builder/api/quotes",
                 json={"data": tall_state})["quote"]
tall_pdf = http.get(f"/sales/builder/api/quotes/{tall_quote['id']}/pdf")
check("a campaign whose map crops tall still builds a PDF",
      tall_pdf.status_code == 200 and tall_pdf.data[:4] == b"%PDF",
      tall_pdf.status_code)

# What the client actually reads. The rate card is our internal pricing: all
# four mentions this had to remove were our own strings, not model output, so
# the assertion is against the rendered document rather than against the
# prompt that had been asking the model not to say it all along.
from pypdf import PdfReader                                        # noqa: E402
_pdf_text = "\n".join((page.extract_text() or "")
                      for page in PdfReader(BytesIO(pdf.data)).pages)
# Line-wrapped by the extractor, not by the document: a phrase that spans a
# wrap is still a phrase the client reads.
_pdf_flat = " ".join(_pdf_text.split())
check("no rate card is named anywhere in the client's PDF",
      "rate card" not in _pdf_flat.lower() and "rate-card" not in _pdf_flat.lower(),
      [ln for ln in _pdf_text.splitlines() if "rate" in ln.lower()][:3])
_seeded = builder._seeded_sections(state)
check("and the seeded copy the rep starts from does not name one either",
      not any("rate card" in str(sec.get("body") or "").lower()
              for sec in _seeded),
      [sec["id"] for sec in _seeded
       if "rate card" in str(sec.get("body") or "").lower()])
check("a model that writes it anyway loses the sentence, not the paragraph",
      spec.clean_ai_text("Priced from the Smart 1 rate card. The Suite reports it.")
      == "The Suite reports it.",
      spec.clean_ai_text("Priced from the Smart 1 rate card. The Suite reports it."))

# Expected Results & ROI is the KPI framework now, not a table of impressions.
check("the ROI section states the primary KPI",
      "Primary KPI" in _pdf_text, "")
check("and what each product is measured on, with a normal result for it",
      "Expected benchmark" in _pdf_text and "Video Completion Rate" in _pdf_text,
      "")
check("a benchmark range is labeled as an expectation, never a guarantee",
      "not guarantees" in _pdf_flat, "")
check("and the section no longer leads with an impressions table",
      "Estimated delivery" not in _pdf_text, "")

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
section("how the campaign will be judged")
# ---------------------------------------------------------------------------
from hub import kpi_framework as kpi                               # noqa: E402

check("a Connected TV line is judged on completion, not clicks",
      kpi.benchmark_for("OTT", "Connected TV - Targeted")["kpi"]
      == "Video Completion Rate")
check("and the order of the table is load-bearing — CTV is not YouTube",
      kpi.benchmark_for("OTT", "Connected TV - Targeted")["expected"] == "95%–99%"
      and kpi.benchmark_for("YOUTUBE", "YouTube Video")["expected"] == "70%–95%")
check("a product the table does not know is not given the nearest benchmark",
      kpi.benchmark_for("SPONSORSHIP", "Stadium Board") == {
          "kpi": "Delivery / Completion",
          "expected": "Track against campaign objective", "matched": False})

# The mirror. The IO builder draws its own KPI Framework from a JavaScript
# copy of this table, and a benchmark that says one thing on the quote and
# another on the insertion order is the exact failure the shared module was
# written to end. Parsed out of the template the same way test_target_areas
# parses the area helpers.
_IO_TEMPLATE = open(os.path.join(ROOT, "modules", "io_builder", "templates",
                                 "index.html"), encoding="utf-8").read()
_IO_RULES = re.findall(
    r"if\(/([^/]+)/\.test\(s\)\)return\['([^']+)','([^']+)'\];", _IO_TEMPLATE)
check("the IO builder's benchmark table was found to compare against",
      len(_IO_RULES) == len(kpi.BENCHMARKS), (len(_IO_RULES), len(kpi.BENCHMARKS)))
check("and it says exactly what the shared table says, in the same order",
      [(a, b, c) for a, b, c in _IO_RULES]
      == [(a, b, c) for a, b, c in kpi.BENCHMARKS],
      [r for r, k in zip(_IO_RULES, kpi.BENCHMARKS) if tuple(r) != tuple(k)])
_IO_FALLBACK = re.search(r"return\['([^']+)','([^']+)'\];\s*\n\}", _IO_TEMPLATE)
check("including the row for a product neither of them knows",
      _IO_FALLBACK and _IO_FALLBACK.groups() == kpi.FALLBACK,
      _IO_FALLBACK.groups() if _IO_FALLBACK else None)

_plan = kpi.framework({"kpis": ["Cost per lead", "Cost per store visit"],
                       "items": [{"category": "OTT",
                                  "product": "Connected TV - Targeted"}]})
check("the first KPI is the primary one and the rest are secondary",
      _plan["primary"] == "Cost per lead"
      and _plan["secondary"] == ["Cost per store visit"])
check("success metrics follow the media on the plan, not only the ticks",
      "Video completion rate" in _plan["metrics"], _plan["metrics"])
check("and what is printed beneath the KPIs excludes the KPIs themselves",
      "Cost per lead" not in _plan["additional_metrics"]
      and "Video completion rate" in _plan["additional_metrics"],
      _plan["additional_metrics"])
check("a campaign with no KPI says so rather than printing an empty framework",
      kpi.framework({})["measured"] is False and "Measurement step"
      in kpi.framework({})["note"])

# The Measurement step offers choices rather than a text box, and the choices
# are this same table — choices() derives them from BENCHMARKS rather than
# keeping a second list of what a KPI can be, which is the drift the mirror
# checks above exist to stop.
_choices = kpi.choices()
check("every distinct KPI in the benchmark table is offered as a choice",
      [c["kpi"] for c in _choices["catalogue"]]
      == list(dict.fromkeys(k for _, k, _ in kpi.BENCHMARKS)),
      _choices["catalogue"])
check("each choice carries the range it would be judged against",
      all(c["expected"] for c in _choices["catalogue"]),
      _choices["catalogue"])
check("the always-reported Suite metrics are carried apart, never as choices",
      _choices["always"] == kpi.success_metrics({})
      and not any(c["kpi"] in _choices["always"]
                  for c in _choices["catalogue"]),
      _choices)
_cfg = api("get", "/sales/builder/api/config")
check("the page is handed the choices on /api/config, never a restated copy",
      (_cfg or {}).get("kpi_choices") == _choices, (_cfg or {}).keys())

_SB_TEMPLATE = open(os.path.join(ROOT, "modules", "sales_builder",
                                 "templates", "index.html"),
                    encoding="utf-8").read()
check("the Measurement step draws its choices from the served table",
      "CFG.kpi_choices" in _SB_TEMPLATE and "kpiChoiceGroups" in _SB_TEMPLATE)
check("and the plan-derived group reads the server's own framework rows",
      "kpiPlan()" in _SB_TEMPLATE.split("function kpiChoiceGroups()", 1)[1]
      .split("return groups;", 1)[0])
# Matched as a call, because the template's own comment names benchmarkFor to
# explain why there is no copy of it — and prose is not a call site.
check("without the proposal builder growing a benchmarkFor mirror of its own",
      "benchmarkFor(" not in _SB_TEMPLATE)
check("a KPI typed by hand still renders as a selected pill",
      "Added by hand" in _SB_TEMPLATE)

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
section("the target-area step writes into a real area, from every route in")
# ---------------------------------------------------------------------------
# Reported from the floor: "cannot select city/DMA/national, radius drop down
# doesn't stay selected". Nothing errored anywhere -- the editor renders
# `currentArea() || blankArea()`, so with an empty list every control on the
# step was bound to a throwaway object that the next redraw threw away. The
# route in was ordinary: press Back off a half-typed area, which spliced the
# list empty while `S._areaView` still said "edit" -- the one state the step's
# own seeding skipped. `_areaView` rides in the saved quote, so the step
# stayed dead across reloads.
#
# The wizard's own source is run rather than a copy of it, the way the area
# helpers above are, or this asserts a second description of the step.
import re as _re                                                   # noqa: E402
import subprocess as _sub                                          # noqa: E402

_TPL = os.path.join(ROOT, "modules", "sales_builder", "templates", "index.html")
_src = "\n".join(m.group(1) for m in _re.finditer(
    r"<script>(.*?)</script>", open(_TPL, encoding="utf-8").read(), _re.S))


def _lift(token):
    """One top-level function or const, out of the page, as written."""
    i = _src.index(token)
    ends = [j for j in (_src.find("\nfunction ", i + 10),
                        _src.find("\nconst ", i + 10),
                        _src.find("\n/*", i + 10)) if j > 0]
    return _src[i:min(ends)] + "\n"


def _between(first, last):
    """The lines of a step body, by the text either end of them."""
    i = _src.index(first)
    j = _src.index(last, i) + len(last)
    return _src[i:j]


# The seeding the step does on the way in, and what Back leaves behind, both
# taken from the page itself so an edit to either is what this test reads.
_seed = _between("if(!S.targetAreas)S.targetAreas=[];",
                 'if(S._areaView!=="edit")S._areaView="list";')
_back = _between("const a=currentArea();\n   if(a&&!areaComplete(a))",
                 "S._areaView=null;syncLegacyGeo();saveSoon();")

_harness = (
    "".join(_lift(t) for t in ("function uid(", "function blankArea(",
                               "function areaGeo(", "function areaLabel(",
                               "function areaZipsAll(", "function areaZips(",
                               "function areaComplete(", "function syncLegacyGeo(",
                               "function currentArea(", "function setArea("))
    + "function saveSoon(){}function saveNow(){}function renderStep(){}\n"
    + "var S={targetAreas:[],geo:'',geoType:'',radius:0};\n"
    + "function enterStep(){" + _seed + "}\n"
    + "function pressBack(){" + _back + " return false;}\n"
    + """
const out={};
enterStep();
out.seeded=S.targetAreas.length;
pressBack();                       // half-typed area, nothing saved
out.afterBack={rows:S.targetAreas.length, view:S._areaView};
enterStep();                       // the rep comes back to the step
out.reentered=S.targetAreas.length;
setArea('type','DMA');
out.picked=(currentArea()||{}).type;
setArea('type','City/ZIP + Radius');setArea('radius',25);setArea('origin','Carmel, IN');
out.radius=(currentArea()||{}).radius;
out.rowsAfterTyping=S.targetAreas.length;
// A quote already saved in the dead state has to come back to life on its
// own: nobody is going to know to press anything in particular.
S={targetAreas:[],_areaView:'edit',geo:'',geoType:'',radius:0};
enterStep();
out.stored={rows:S.targetAreas.length,current:!!currentArea()};
// And the write path seeds on its own, whatever route emptied the list.
S={targetAreas:[],_areaView:'edit',geo:'',geoType:'',radius:0};
setArea('type','National');
out.blindWrite=(currentArea()||{}).type;
console.log(JSON.stringify(out));
""")
_js_path = os.path.join(_TMP, "areastep.js")
open(_js_path, "w", encoding="utf-8").write(_harness)
try:
    _r = json.loads(_sub.run(["node", _js_path], capture_output=True, text=True,
                             timeout=30, check=True).stdout)
    check("opening the step seeds an area to type into", _r["seeded"] == 1, _r)
    check("Back off a half-typed area drops it", _r["afterBack"]["rows"] == 0, _r)
    check("and does not leave the view claiming an editor over nothing",
          _r["afterBack"]["view"] != "edit", _r["afterBack"])
    check("coming back to the step seeds again", _r["reentered"] == 1, _r)
    check("picking DMA sticks — the reported failure",
          _r["picked"] == "DMA", _r)
    check("and the radius holds what was typed", _r["radius"] == 25, _r)
    check("one area, not one per keystroke", _r["rowsAfterTyping"] == 1, _r)
    check("a quote saved in the dead state recovers on its own",
          _r["stored"]["rows"] == 1 and _r["stored"]["current"], _r["stored"])
    check("and a write with no area seeds rather than being discarded",
          _r["blindWrite"] == "National", _r)
except FileNotFoundError:
    print("  skip node is not installed — the area step is unchecked")
except _sub.CalledProcessError as exc:
    check("the target-area step runs", False, exc.stderr[:400])

# ---------------------------------------------------------------------------
section("the landing page is read before it is reviewed")
# ---------------------------------------------------------------------------
# Reported from the floor: "doesn't seem to have a web crawling function, when
# it failed it showed its criteria to look for, but failed to gain permission
# to the webpage". It had none: the URL went into a prompt with the word
# "Visit", which no model here can do. The page is fetched now, through the
# reader Smart 1 Ads already uses, and the model is handed the facts.
from modules.ads_builder import landing_page as _lp                # noqa: E402

_PAGE = {
    "ok": True, "url": "https://carmelsolar.example/lp", "status": 200,
    "redirected": False, "error": "",
    "html": ('<html><head><title>Carmel Solar</title>'
             '<meta name="viewport" content="width=device-width"></head><body>'
             '<h1>Solar for Carmel homes</h1>'
             '<a href="tel:+13175550142">Call (317) 555-0142</a>'
                          # An absolute action, so tools/linkcheck.py does not read this
             # fixture's markup as a Hub route that ought to resolve.
             '<form method="post" action="https://carmelsolar.example/lead">'
             '<input name="email" required>'
             '<button>Get a free quote</button></form></body></html>'),
}
_real_fetch = _lp.fetch
_ASKED_LP = {}


def _fake_lp_ai(prompt, max_output_tokens=6000, search=False):
    _ASKED_LP["prompt"] = prompt
    return "CTA Status: one form and a click-to-call."


_lp.fetch = lambda url: dict(_PAGE)
builder._openai_response = _fake_lp_ai
_rev = api("post", "/sales/builder/api/review-landing-page",
           json={"url": "https://carmelsolar.example/lp", "client": "Carmel Solar"})
check("the page is actually fetched", (_rev.get("observed") or {}).get("measured") is True, _rev)
check("what a visitor can do on it is counted off the markup",
      {p["kind"] for p in _rev["observed"]["conversion_points"]}
      >= {"calls", "form_submissions"}, _rev["observed"]["conversion_points"])
check("and each point carries the evidence, not just the claim",
      any("(317) 555-0142" in p["evidence"] for p in _rev["observed"]["conversion_points"]),
      _rev["observed"]["conversion_points"])
check("the model is handed the page rather than its address",
      "Solar for Carmel homes" in _ASKED_LP["prompt"], _ASKED_LP["prompt"][:300])
check("and is told not to describe anything that is not in it",
      "do not describe" in _ASKED_LP["prompt"].lower(), "")
check("the reading is returned beside the judgment, not merged into it",
      _rev["summary"] and _rev["review"] and "summary" in _rev, _rev.keys())

_lp.fetch = lambda url: {"ok": False, "url": url, "status": 404,
                         "error": "The page answered HTTP 404.", "html": ""}
_dead = http.post("/sales/builder/api/review-landing-page",
                  json={"url": "https://carmelsolar.example/gone"})
check("a page that could not be read is refused, not reviewed anyway",
      _dead.status_code == 502 and "404" in (_dead.get_json().get("detail") or ""),
      _dead.get_json())
check("and the model is never asked about it",
      "gone" not in _ASKED_LP["prompt"], "")


def _dead_ai(prompt, max_output_tokens=6000, search=False):
    raise RuntimeError("upstream is down")


_lp.fetch = lambda url: dict(_PAGE)
builder._openai_response = _dead_ai
_half = http.post("/sales/builder/api/review-landing-page",
                  json={"url": "https://carmelsolar.example/lp"})
check("the AI failing costs the judgment and not the reading",
      _half.status_code == 502
      and (_half.get_json().get("observed") or {}).get("measured") is True,
      _half.get_json())
_lp.fetch = _real_fetch
builder._openai_response = _real_ai

# ---------------------------------------------------------------------------
section("one shared call to the model, and three ways of failing named")
# ---------------------------------------------------------------------------
# The hosted web-search tool rode on every call in this module. Whether it is
# available depends on the model, which is OPENAI_MODEL and is not the default
# written here -- so a model that refuses the tool refused the whole request,
# and the ZIP button that the tool was meant to help was what it stopped.
_CALLS = []


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


_ANSWER = {"status": "completed",
           "output": [{"content": [{"type": "output_text", "text": "46032, 46033"}]}]}


def _capture(payload, api_key):
    # A copy: the retry edits the payload it was handed, so a stub holding the
    # live reference records what the call ended up as rather than what it was.
    _CALLS.append(json.loads(json.dumps(payload)))
    if "tools" in payload:
        return _Resp(400, {"error": {"message": "Hosted tool 'web_search' is not supported."}})
    return _Resp(200, _ANSWER)


_real_call, _real_key = builder._openai_call, os.environ.get("OPENAI_API_KEY")
builder._openai_call = _capture
os.environ["OPENAI_API_KEY"] = "test-key"
try:
    _text = builder._openai_response("anything", 100)
    check("an ordinary call carries no search tool at all",
          "tools" not in _CALLS[0], _CALLS[0])
    _CALLS.clear()
    _text = builder._openai_response("anything", 100, search=True)
    check("a call that asks for search asks for it", "tools" in _CALLS[0], _CALLS[0])
    check("and falls back without it rather than losing the answer",
          len(_CALLS) == 2 and "tools" not in _CALLS[1] and "46032" in _text, _CALLS)

    _CALLS.clear()

    def _unauthorised(payload, api_key):
        _CALLS.append(json.loads(json.dumps(payload)))
        return _Resp(401, {"error": {"message": "Incorrect API key provided."}})

    builder._openai_call = _unauthorised
    try:
        builder._openai_response("anything", 100, search=True)
        check("a refused call raises", False, "it did not")
    except RuntimeError as exc:
        check("and says what the API said, not just the status line",
              "Incorrect API key" in str(exc), str(exc))
    check("a bad key is not asked the same question twice — only a 400 is the tool",
          len(_CALLS) == 1, _CALLS)

    builder._openai_call = lambda p, k: _Resp(
        200, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
              "output": []})
    try:
        builder._openai_response("anything", 100)
        check("an answer cut short raises", False, "it did not")
    except RuntimeError as exc:
        check("and is named as that rather than as an empty answer",
              "max output tokens" in str(exc), str(exc))
finally:
    builder._openai_call = _real_call
    if _real_key is None:
        os.environ.pop("OPENAI_API_KEY", None)
    else:
        os.environ["OPENAI_API_KEY"] = _real_key

# ---------------------------------------------------------------------------
section("the ZIP lookup says which thing went wrong")
# ---------------------------------------------------------------------------
_ZIP_ASKED = {}


def _zip_ai(prompt, max_output_tokens=6000, search=False):
    _ZIP_ASKED["search"] = search
    return "46032, 46033, 46074"


builder._openai_response = _zip_ai
_z = api("post", "/sales/builder/api/zipcodes-in-radius",
         json={"origin": "Carmel, IN", "radius": 10})
check("the ZIP lookup returns the list", _z["count"] == 3, _z)
check("and is the one call that asks for live search", _ZIP_ASKED["search"] is True)


def _no_zips(prompt, max_output_tokens=6000, search=False):
    return "I was unable to look that up."


builder._openai_response = _no_zips
_zz = http.post("/sales/builder/api/zipcodes-in-radius",
                json={"origin": "Carmel, IN", "radius": 10})
check("an empty answer names the origin and the radius it was asked about",
      "Carmel, IN" in _zz.get_json()["error"] and "10" in _zz.get_json()["error"],
      _zz.get_json())
check("and offers the way round it rather than only reporting failure",
      "by hand" in _zz.get_json()["error"], _zz.get_json())
builder._openai_response = _real_ai

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
