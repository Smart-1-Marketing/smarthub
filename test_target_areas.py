"""hub/target_areas.py and the consolidated Proposal Builder — test harness.

    python3 test_target_areas.py

Same shape as test_jsonstore.py and test_ads_module.py: no pytest, no new
dependencies, runs against a throwaway SQLite database and a temporary data
directory so it never touches /var/data or the real one.

## Why this file exists

Two things here are the kind that look right on screen and are wrong in the
data, which is the failure mode this codebase keeps paying for:

  * **A campaign's geography now lives in a list.** Every quote and every IO
    saved before that has it in `geoType` / `geo` / `radius` instead, and
    those records are never migrated — they are converted on read. So the
    thing to prove is not "multiple areas work", it is "an old single-area
    record still reads as exactly the area it always was", which no amount of
    clicking a new quote will tell you.

  * **The browser and the server each size areas.** The wizard estimates reach
    live in JavaScript; the PDF, the list column and the AI prompt do it in
    Python. When those two disagree the proposal contradicts the screen the
    rep quoted from, and nothing errors. The labels are asserted identical
    here against the same fixtures the JavaScript uses.

Plus the ordinary regressions: an empty record must not sprout a phantom
"10-mile radius" target area nobody entered, an area that cannot be sized must
report "not measured" rather than zero, and delivering the same revision twice
must file one document on the client rather than three.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-areas-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "target-areas-test")
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
    print("-" * 60)


# ---------------------------------------------------------------------------
section("the shape")
# ---------------------------------------------------------------------------
from hub import target_areas as ta                                # noqa: E402

areas = ta.normalize([
    {"name": "Carmel showroom", "type": "City/ZIP Code + Radius",
     "origin": "Carmel, IN", "radius": "10", "zips": "46032, 46033, notazip"},
    "Fishers, IN",
    {"type": "DMA", "dma": "Indianapolis"},
    {"name": "", "type": "Other"},              # names nowhere — dropped
    {},                                          # ditto
])
check("blank areas are dropped", len(areas) == 3, [a["name"] for a in areas])
check("the IO builder's type spelling folds into the shared one",
      areas[0]["type"] == ta.RADIUS, areas[0]["type"])
check("a stray word is not read as a ZIP Code",
      areas[0]["zips"] == "46032, 46033", areas[0]["zips"])
check("a bare string becomes an area", areas[1]["origin"] == "Fishers, IN")
check("an unnamed area is named after its geography",
      areas[1]["name"] == "Fishers, IN", areas[1]["name"])

dupes = ta.normalize([{"origin": "Carmel, IN", "radius": 10},
                      {"origin": "carmel, in", "radius": 10}])
check("the same place twice is one area", len(dupes) == 1, len(dupes))

check("a national campaign needs nothing else",
      len(ta.normalize([{"type": "National"}])) == 1)
check("MAX_AREAS bounds the list",
      len(ta.normalize([{"origin": f"City {i}"} for i in range(60)])) == ta.MAX_AREAS)

# ---------------------------------------------------------------------------
section("no phantom areas")
# ---------------------------------------------------------------------------
# `describe` falls back to the default 10-mile radius, so naming an area
# before checking whether it is blank turns an empty record into a target area
# nobody entered. Every quote that never reached the geography step would then
# load carrying one.
check("an empty record has no target area", ta.from_legacy({}) == [])
check("a record with only a geo type has no target area",
      ta.from_legacy({"geoType": "City/ZIP + Radius"}) == [])
check("normalize does not invent one either", ta.normalize([{}, {"radius": 10}]) == [])

# ---------------------------------------------------------------------------
section("old records still read correctly")
# ---------------------------------------------------------------------------
io_legacy = ta.from_legacy({
    "geoType": "City/ZIP Code + Radius", "geoOrigin": "Carmel, IN",
    "geoRadius": "10", "geoZipcodes": "46032,46033",
    "geo": "Carmel, IN + 10-mile radius | ZIP Codes: 46032, 46033"})
check("an old IO reads as its one area", len(io_legacy) == 1)
check("the radius is not doubled up in the name",
      io_legacy[0]["name"] == "Carmel, IN + 10-mile radius", io_legacy[0]["name"])
check("its ZIP list survives", ta.zip_list(io_legacy[0]["zips"]) == ["46032", "46033"])

pb_legacy = ta.from_legacy({"geoType": "City/ZIP + Radius", "geo": "Carmel, IN",
                            "radius": 10})
check("an old quote reads as its one area",
      len(pb_legacy) == 1 and pb_legacy[0]["origin"] == "Carmel, IN")

state_legacy = ta.from_legacy({"geoType": "Statewide", "geo": "Indiana"})
check("a statewide record keeps its state",
      state_legacy[0]["state"] == "Indiana", state_legacy)

explicit = ta.from_legacy({"targetAreas": [{"origin": "Fishers, IN"}],
                           "geoType": "DMA", "geoDMA": "Indianapolis"})
check("an explicit list wins over the legacy fields",
      len(explicit) == 1 and explicit[0]["origin"] == "Fishers, IN", explicit)

# ---------------------------------------------------------------------------
section("what downstream readers get")
# ---------------------------------------------------------------------------
combined = ta.to_legacy_geo(areas)
check("every area reaches the single-string form",
      all(bit in combined for bit in ("Carmel showroom", "Fishers", "Indianapolis DMA")),
      combined)
check("ZIP Codes ride along with their own area",
      "ZIP Codes: 46032, 46033" in combined, combined)

state = {}
ta.apply_to_legacy(state, areas)
check("the first area fills the fields a non-area-aware reader uses",
      state["geoOrigin"] == "Carmel, IN" and state["geoType"] == ta.RADIUS, state)
check("every area's ZIPs reach geoZipcodes",
      state["geoZipcodes"] == "46032, 46033", state["geoZipcodes"])
check("the summary says how many more there are",
      "+1 more" in ta.summary(areas), ta.summary(areas))

# ---------------------------------------------------------------------------
section("not measured is not zero")
# ---------------------------------------------------------------------------
vague = ta.normalize([{"name": "Six-county territory", "type": "Other",
                       "other": "Six-county service territory"}])
check("an area that cannot be sized returns None",
      ta.estimated_population(vague[0]) is None,
      ta.estimated_population(vague[0]))
check("and no total is claimed for it", ta.total_population(vague) is None)
check("it is named as unmeasured, not silently dropped",
      ta.unsized(vague) == ["Six-county territory — Six-county service territory"],
      ta.unsized(vague))
mixed = ta.normalize([{"origin": "Carmel, IN", "radius": 10},
                      {"type": "Other", "other": "somewhere"}])
check("a mixed campaign totals what it can and names what it cannot",
      ta.total_population(mixed) and len(ta.unsized(mixed)) == 1)
check("a stated population is used as given",
      ta.estimated_population({"type": "Other", "other": "x", "population": 4200}) == 4200)

# ---------------------------------------------------------------------------
section("the browser and the server agree")
# ---------------------------------------------------------------------------
# The wizard's JavaScript carries its own copy of these helpers, because the
# reach panel has to update as the rep types. If the two drift, the proposal
# says something different from the screen it was quoted from and nothing
# errors.
FIXTURES = [
    {"name": "Carmel showroom", "type": "City/ZIP + Radius",
     "origin": "Carmel, IN", "radius": 10, "zips": "46032, 46033"},
    {"name": "", "type": "City/ZIP + Radius", "origin": "Fishers, IN", "radius": 15},
    {"name": "", "type": "DMA", "dma": "Indianapolis"},
    {"name": "North stores", "type": "Statewide", "state": "Indiana"},
    {"name": "", "type": "National"},
]
py_labels = [ta.label(ta.normalize_area(f)) for f in FIXTURES]
py_pops = [ta.estimated_population(ta.normalize_area(f)) for f in FIXTURES]

import re                                                          # noqa: E402
import subprocess                                                  # noqa: E402

TEMPLATE = os.path.join(ROOT, "modules", "sales_builder", "templates", "index.html")
js_source = "\n".join(m.group(1) for m in re.finditer(
    r"<script>(.*?)</script>", open(TEMPLATE, encoding="utf-8").read(), re.S))
wanted = ["function areaGeo(", "function areaLabel(", "function areaPopulation("]
extract = ""
for start_token in wanted:
    i = js_source.index(start_token)
    ends = [j for j in (js_source.find("\nfunction ", i + 10),
                        js_source.find("\n/*", i + 10)) if j > 0]
    extract += js_source[i:min(ends)] + "\n"

harness = (extract + "\nconst F=" + json.dumps(FIXTURES) + ";\n"
           "console.log(JSON.stringify({labels:F.map(areaLabel),"
           "pops:F.map(a=>areaPopulation(a))}));\n")
js_path = os.path.join(_TMP, "areas.js")
open(js_path, "w", encoding="utf-8").write(harness)
try:
    out = subprocess.run(["node", js_path], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    js = json.loads(out)
    check("the wizard labels an area exactly as the PDF does",
          js["labels"] == py_labels, f"js={js['labels']} py={py_labels}")
    check("and sizes it the same way",
          js["pops"] == py_pops, f"js={js['pops']} py={py_pops}")
except FileNotFoundError:
    print("  skip node is not installed — browser/server agreement unchecked")
except subprocess.CalledProcessError as exc:
    check("the wizard's area helpers run", False, exc.stderr[:300])

# ---------------------------------------------------------------------------
section("the business description keeps the Google rules")
# ---------------------------------------------------------------------------
from hub import business_description as bd                         # noqa: E402

prompt = bd.prompt_for(["example.com"], client="Riverstone Dental",
                       industry="Dental", areas=areas)
for rule in ("third person", "500 to 750 characters", "do NOT include URLs",
             "Do not invent unsupported claims"):
    check(f"the prompt still states: {rule!r}", rule in prompt)
check("a multi-area client is described across all of its areas",
      "3 distinct target areas" in prompt, prompt[:0])
check("a single-area client gets no multi-area instruction",
      "distinct target areas" not in bd.prompt_for(["example.com"], areas=areas[:1]))
check("a link would be flagged before it reaches Google",
      any("website address" in w for w in bd.check("Visit us at example.com today.")))
check("so would a phone number",
      any("phone number" in w for w in bd.check("Call 317-555-0123 for details.")))
check("and sales language", any("Promotional" in w for w in bd.check("The best dentist, call now.")))
check("clean copy passes", bd.check("A family dental practice serving central Indiana.") == [])

# ---------------------------------------------------------------------------
section("the Suite push asks rather than invents")
# ---------------------------------------------------------------------------
from hub import suite_opportunity as suite                         # noqa: E402

# Every name the token or the location could come from, including the ones
# hub/ghl_contacts.py reads -- suite_opportunity delegates to it now.
for name in ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN",
             "GHL_PRIVATE_INTEGRATION_TOKEN", "GHL_LEAD_LOCATION_ID",
             "SMART1_MARKETING_LOCATION_ID", "GHL_ACCOUNTING_LOCATION_ID",
             "GHL_LOCATION_ID", "GHL_BLOG_LOCATION_ID", "GHL_COMPANY_ID",
             "SUITE_COMPANY_ID"):
    os.environ.pop(name, None)
status = suite.status()
check("an unconfigured Suite says so in words",
      not status["ok"] and any("GHL_PRIVATE_TOKEN" in p for p in status["problems"]),
      status)
check("and names the location setting the rest of the Hub uses",
      any("GHL_LEAD_LOCATION_ID" in p for p in status["problems"]), status["problems"])

# The bug this delegation closes: companyId and locationId are different id
# spaces, and on this deployment both env vars hold the same value. Filing an
# opportunity against the agency puts it where nobody looks for it.
os.environ["GHL_PRIVATE_TOKEN"] = "pit-test-token"
os.environ["GHL_COMPANY_ID"] = "SAME_ID_BOTH_PLACES"
os.environ["GHL_LEAD_LOCATION_ID"] = "SAME_ID_BOTH_PLACES"
check("a company id used as a location id is refused, not used",
      suite.location_id() == "", suite.location_id())
check("and the refusal explains which id is wanted",
      "sub-account" in suite.location_problem(), suite.location_problem())
os.environ["GHL_LEAD_LOCATION_ID"] = "REAL_SUBACCOUNT_ID"
check("a genuine sub-account id is accepted",
      suite.location_id() == "REAL_SUBACCOUNT_ID", suite.location_id())
for name in ("GHL_PRIVATE_TOKEN", "GHL_COMPANY_ID", "GHL_LEAD_LOCATION_ID"):
    os.environ.pop(name, None)
result = suite.push_proposal(client="Riverstone Dental")
check("and pushing reports it rather than raising",
      result["ok"] is False and "GHL_PRIVATE_TOKEN" in result["reason"], result)
check("a push with no client is refused",
      suite.push_proposal(client="")["reason"] == "A client is required.")

# ---------------------------------------------------------------------------
section("the builder, end to end")
# ---------------------------------------------------------------------------
from werkzeug.test import Client                                   # noqa: E402
import wsgi                                                        # noqa: E402
from hub import auth                                               # noqa: E402

http = Client(wsgi.application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                domain="localhost")


def api(method, path, **kw):
    return getattr(http, method)(path, **kw).get_json()


quote_state = {
    "client": "Riverstone Dental", "url": "riverstonedental.com",
    "industry": "legal", "objectives": ["Lead Generation"], "months": 6,
    "budget": 8000, "items": [], "selectedPackage": {"name": "Recommended",
                                                     "monthly": 8000, "total": 48000},
    "targetAreas": FIXTURES[:3],
}
quote = api("post", "/sales/builder/api/quotes", json={"data": quote_state})["quote"]
check("a quote takes three target areas", len(quote["target_areas"]) == 3)
check("the list column names them",
      "+1 more" in quote["geo_summary"], quote["geo_summary"])
check("no geography gap is raised when areas exist",
      "geo" not in [g["key"] for g in quote["gaps"]], quote["gaps"])

blank_quote = api("post", "/sales/builder/api/quotes",
                  json={"data": {"client": "No Geo Co"}})["quote"]
check("a quote with nowhere to run says so",
      "geo" in [g["key"] for g in blank_quote["gaps"]], blank_quote["gaps"])
check("and shows no target area", blank_quote["target_areas"] == [])

pdf = http.get(f"/sales/builder/api/quotes/{quote['id']}/pdf")
check("the branded PDF builds", pdf.status_code == 200 and len(pdf.data) > 2000,
      pdf.status_code)
docx = http.get(f"/sales/builder/api/quotes/{quote['id']}/docx")
check("so does the Word copy", docx.status_code == 200 and len(docx.data) > 2000)

# Delivering the same revision repeatedly must file one document, not three.
# Every click of Send -- including the one after answering the Suite contact
# question -- runs this path.
for _ in range(3):
    delivered = api("post", f"/sales/builder/api/quotes/{quote['id']}/deliver", json={})
check("delivering succeeds even with Suite unconfigured", delivered["ok"] is True)
check("and says why there is no opportunity",
      delivered["suite"]["ok"] is False and delivered["suite"]["reason"])
filed = api("get", "/api/client/proposals?client=Riverstone+Dental")["proposals"]
check("the same revision files one document, not three", len(filed) == 1, len(filed))
check("it is filed as sent, with the quoted value",
      filed[0]["status"] == "sent" and filed[0]["value"] == 8000.0, filed[0])

api("put", f"/sales/builder/api/quotes/{quote['id']}",
    json={"data": quote_state, "bump_revision": True})
api("post", f"/sales/builder/api/quotes/{quote['id']}/deliver", json={})
filed = api("get", "/api/client/proposals?client=Riverstone+Dental")["proposals"]
check("a real revision files a second document", len(filed) == 2, len(filed))

# ---------------------------------------------------------------------------
section("the retired builder redirects and keeps its archive")
# ---------------------------------------------------------------------------
moved = http.get("/sales/proposals/")
check("/sales/proposals redirects to the one builder",
      moved.status_code == 302 and moved.headers["Location"] == "/sales/builder/",
      moved.headers.get("Location"))
prefilled = http.get("/sales/proposals/?prefill=1&business_name=Riverstone%20Dental"
                     "&website=riverstonedental.com")
target = prefilled.headers.get("Location", "")
check("a Client 360 prefill link survives the move",
      "client=Riverstone+Dental" in target and "url=riverstonedental.com" in target,
      target)
check("the archive still reads",
      api("get", "/sales/proposals/api/proposals")["ok"] is True)
check("but it no longer builds anything",
      http.post("/sales/proposals/api/generate", json={}).status_code == 404)
check("the surviving builder can list the archive",
      api("get", "/sales/builder/api/legacy/proposals")["available"] is True)

# ---------------------------------------------------------------------------
section("routes the wizard actually calls")
# ---------------------------------------------------------------------------
# Three AI buttons and all four IO-conversion calls used to point at
# IO_API_BASE + "/sales/builder/api/...", a path that exists on neither app, so
# every one of them silently fell back to a placeholder.
for path in ("/sales/builder/api/estimate-audience",
             "/sales/builder/api/generate-business-description",
             "/sales/builder/api/review-landing-page",
             "/sales/builder/api/zipcodes-in-radius",
             "/sales/builder/api/ai/draft-sections"):
    code = http.post(path, json={}).status_code
    check(f"{path} is served", code != 404, code)
for path in ("/tools/io/api/next-order-number", "/tools/io/api/generate-client-pdf",
             "/tools/io/api/generate-internal-pdf", "/tools/io/api/submit-io"):
    check(f"{path} is served", http.post(path, json={}).status_code != 404)
health = api("get", "/sales/builder/health")
check("/health answers instead of raising NameError",
      health.get("status") == "ok" and health.get("database"), health)

# ---------------------------------------------------------------------------
section("the IO carries every area")
# ---------------------------------------------------------------------------
from modules.io_builder import app as io_app                       # noqa: E402

io_data = {"client": "Riverstone Dental", "orderNumber": "10501",
           "targetAreas": FIXTURES[:3], "sameDates": True,
           "start": "01/01/2026", "end": "06/30/2026"}
check("the webhook's single geo string holds all three",
      io_app._payload_geo(io_data).count("||") == 2, io_app._payload_geo(io_data))
check("and the structured list is sent alongside it",
      len(io_app._payload_areas(io_data)) == 3)
io_pdf, _ = io_app._build_requirements_pdf(io_data, "client")
check("the IO PDF builds with several areas", len(io_pdf) > 2000)
io_old = {"client": "Old Co", "orderNumber": "10404",
          "geoType": "City/ZIP Code + Radius", "geoOrigin": "Muncie, IN",
          "geoRadius": "25", "geo": "Muncie, IN + 25-mile radius"}
check("an IO saved before areas existed still renders",
      len(io_app._build_requirements_pdf(io_old, "internal")[0]) > 2000)
check("and its geography is unchanged",
      io_app._payload_geo(io_old) == "Muncie, IN + 25-mile radius",
      io_app._payload_geo(io_old))

# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
