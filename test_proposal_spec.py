"""The Smart 1 proposal specification and the creative gate — test harness.

    python3 test_proposal_spec.py

Same shape as the other three: no pytest, no new dependencies, a throwaway
SQLite database and a temporary data directory.

## Why this file exists

Three things here fail silently and expensively:

  * **The creative gate.** A Connected TV buy priced without anyone
    establishing that a :30 exists is a signed insertion order and a launch
    date nobody can hit. The classifier that decides what counts as video is
    the whole gate, and it cannot work off the rate card's categories: four
    programmatic *video* products are filed under DISPLAY beside banner
    inventory, and three of the four have names that say nothing. If that
    lookup silently stops matching, the gate stops asking and everything
    still looks fine.

  * **The comp confirmation.** Comping production on a $10,000 flight is
    winning the business; comping it on a $600 test is Smart 1 paying to run
    someone's campaign. The confirmation has to survive a budget being edited
    afterwards — a comp confirmed at $1,400 must not still count once the
    line is cut to $300.

  * **Expected Results & ROI.** It is computed from the rate card rather than
    written, so a management fee must report *no* impressions rather than a
    plausible number. A projection that contradicts the media plan printed
    above it is worse than no projection.

Plus the standing directives, which are rules rather than requests: the
Smart 1 Labs exclusion is verified against generated copy, not merely asked
for in a prompt.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-spec-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(_TMP, "t.db"))
os.environ.setdefault("SECRET_KEY", "proposal-spec-test")
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
section("the 13-part structure")
# ---------------------------------------------------------------------------
from hub import proposal_spec as spec                              # noqa: E402

ids = [s["id"] for s in spec.OUTLINE]
check("the outline is ordered cover-first, next-steps-last",
      ids[0] == "cover" and ids[-1] == "next", ids[:1] + ids[-1:])
for needed in ("summary", "objectives", "friction", "areas", "channels",
               "mediaplan", "creative", "technology", "reporting", "timeline",
               "packages", "roi", "next"):
    check(f"the outline includes {needed}", needed in ids)
check("Expected Results & ROI comes after the media plan",
      ids.index("roi") > ids.index("mediaplan"))
check("every section id is unique", len(ids) == len(set(ids)))
check("ROI, media plan and investment are required",
      set(spec.REQUIRED) == {"roi", "mediaplan", "packages"}, spec.REQUIRED)

fresh = spec.default_sections()
check("a fresh proposal has every section", len(fresh) == len(spec.OUTLINE))
check("required sections are flagged as such",
      {s["id"] for s in fresh if s["required"]} == set(spec.REQUIRED))

# ---------------------------------------------------------------------------
section("the standing directives")
# ---------------------------------------------------------------------------
prompt = spec.system_prompt({"industry": "Law Firms"})
for rule, phrase in (
        ("the Smart 1 Labs exclusion", "Never reference Smart 1 Labs"),
        ("the Suite as central nervous system", "central nervous system"),
        ("client confidentiality", "Never name another Smart 1 Marketing client"),
        ("no invented figures", "Do not invent statistics"),
        ("the strategist voice", "Based on the information provided")):
    check(f"the prompt states {rule}", phrase in prompt)

check("Smart 1 Labs in generated copy is caught",
      spec.violations("We'd start you on Smart 1 Labs for this.") != [])
check("...however it is spelled",
      spec.violations("smart1 labs handles that") != [])
check("the Smart 1 Suite is not caught by mistake",
      spec.violations("Everything lands in the Smart 1 Suite.") == [])

# ---------------------------------------------------------------------------
section("audience segments are named, not described")
# ---------------------------------------------------------------------------
legal = spec.audience_segments_for("Law Firms")
check("a law firm gets contextual collision placement",
      any("collision" in s.lower() for s in legal), legal)
boat = spec.audience_segments_for("Boat Dealers")
check("a boat dealer gets the Experian marine segment",
      any("Boating" in s for s in boat), boat)
check("every campaign gets the base layer",
      all(any("ConsumerView" in s for s in spec.audience_segments_for(i))
          for i in ("", "Law Firms", "Something Unheard Of")))
check("the list is a shortlist, not the whole taxonomy",
      all(len(spec.audience_segments_for(i)) <= 8
          for i in ("Law Firms", "Auto Dealers", "")))

# ---------------------------------------------------------------------------
section("operating facts are only cited when they apply")
# ---------------------------------------------------------------------------
audio_plan = {"items": [{"category": "DIGITAL RADIO", "product": "Podcasts - Targeted"}]}
display_plan = {"items": [{"category": "DISPLAY", "product": "Category"}]}
check("an audio plan may cite the audio/CTV lift",
      any("21.8%" in f for f in spec.nuances_for(audio_plan)))
check("a display-only plan may not",
      not any("21.8%" in f for f in spec.nuances_for(display_plan)),
      spec.nuances_for(display_plan))
check("an auto client gets the 60-30-10 framework",
      any("60-30-10" in f for f in spec.nuances_for({"industry": "Auto Dealers",
                                                     "items": []})))
check("a dental client does not",
      spec.nuances_for({"industry": "Dental", "items": []}) == [])

# ---------------------------------------------------------------------------
section("Suite licensing")
# ---------------------------------------------------------------------------
check("three tiers at the published prices",
      [t["monthly"] for t in spec.SAAS_TIERS] == [199, 599, 999],
      [t["monthly"] for t in spec.SAAS_TIERS])
check("a small campaign gets Smart 1", spec.suggested_tier(1500)["name"] == "Smart 1")
check("a mid campaign gets Smarter", spec.suggested_tier(8000)["name"] == "Smarter")
check("a large campaign gets Smartest", spec.suggested_tier(20000)["name"] == "Smartest")
check("a junk budget does not raise", spec.suggested_tier("not a number")["name"])

# ---------------------------------------------------------------------------
section("what counts as video and audio")
# ---------------------------------------------------------------------------
from hub import creative_needs as cn                               # noqa: E402

# The four rate-card products that make this hard: programmatic video filed
# under the DISPLAY category, with names that do not say "video".
CASES = [
    ("DISPLAY", "Programmatic - Targeted", cn.VIDEO),          # $17.00 CPM video
    ("DISPLAY", "Programmatic - RON (Run of Network)", cn.VIDEO),   # $14.00 CPM
    ("DISPLAY", "Premium: Non-Skippable", cn.VIDEO),           # $23.00 CPM
    ("DISPLAY", "Premium Native Video", cn.VIDEO),             # $26.00 CPM
    ("DISPLAY", "Category", cn.DISPLAY),                       # $4.25 CPM display
    ("DISPLAY", "RON (Run of Network)", cn.DISPLAY),           # $3.50 CPM display
    ("DISPLAY", "Advanced Audience", cn.DISPLAY),
    ("DIGITAL RADIO", "Programmatic - Targeted", cn.AUDIO),    # same name, audio
    ("DIGITAL RADIO", "Podcasts - Targeted", cn.AUDIO),
    ("OTT", "Connected TV - Targeted", cn.VIDEO),
    ("OTT", "Advanced TV - Targeted", cn.VIDEO),
    ("YOUTUBE", "TrueView", cn.VIDEO),
    ("YOUTUBE", "Bumpers", cn.VIDEO),
    ("META", "Facebook | Instagram - Awareness", cn.SOCIAL),
    ("MOBILE ONLY", "Geo-Fence", cn.DISPLAY),
    ("LOCATION LOOKBACK", "Brand Affinity", cn.DISPLAY),
    ("IP TARGETS", "IP Targeted Video - List is supplied", cn.VIDEO),
]
for category, product, want in CASES:
    got = cn.medium_of({"category": category, "product": product})
    check(f"{category} / {product} is {want}", got == want, got)

check("the same product name means different things in different categories",
      cn.medium_of({"category": "DISPLAY", "product": "Programmatic - Targeted"}) !=
      cn.medium_of({"category": "DIGITAL RADIO", "product": "Programmatic - Targeted"}))
check("every named product still exists on the rate card",
      cn.card_drift() == [], cn.card_drift())

# ---------------------------------------------------------------------------
section("the creative gate")
# ---------------------------------------------------------------------------
plan = {"months": 3, "items": [
    {"category": "OTT", "product": "Connected TV - Targeted", "dollars": 4000},
    {"category": "DIGITAL RADIO", "product": "Podcasts - Targeted", "dollars": 200},
    {"category": "DISPLAY", "product": "Category", "dollars": 1000}]}

check("only video and audio are gated", cn.gated_media(plan) == ["video", "audio"])
check("display raises no question",
      not any(r["medium"] == "display" for r in cn.evaluate(plan)["media"]))
check("spend is the whole flight, not one month",
      cn.medium_spend(plan, cn.VIDEO) == 12000.0, cn.medium_spend(plan, cn.VIDEO))
check("nothing is resolved before it is asked",
      cn.evaluate(plan)["unresolved"] == ["video", "audio"])
check("an unanswered gate is a stated gap",
      {g["key"] for g in cn.gaps(plan)} == {"creative_video", "creative_audio"})
check("and never quietly assumed",
      "not yet confirmed" in cn.summary_line(plan), cn.summary_line(plan))

plan["creativePlan"] = {"video": {"answer": cn.HAS}}
check("'they have it' resolves with no further question",
      "video" not in cn.evaluate(plan)["unresolved"])

plan["creativePlan"]["video"] = {"answer": cn.CLIENT_PAYS}
result = cn.evaluate(plan)
check("'the client pays' puts a production fee on the proposal",
      result["fees"] == cn.TYPICAL_PRODUCTION[cn.VIDEO], result["fees"])
plan["creativePlan"]["video"] = {"answer": cn.CLIENT_PAYS, "fee": 1200}
check("and the fee is whatever was actually agreed",
      cn.evaluate(plan)["fees"] == 1200.0)

# A $12,000 video campaign: comping production needs no second question.
plan["creativePlan"]["video"] = {"answer": cn.COMP}
video = next(r for r in cn.evaluate(plan)["media"] if r["medium"] == "video")
check("comping on a large campaign is recorded without a confirmation",
      video["resolved"] and not video["needs_confirm"], video)

# A $600 audio campaign: it does.
plan["creativePlan"]["audio"] = {"answer": cn.COMP}
audio = next(r for r in cn.evaluate(plan)["media"] if r["medium"] == "audio")
check("comping under $1,500 is not resolved until it is confirmed",
      audio["needs_confirm"] and not audio["resolved"], audio)
check("and the question names the number",
      "$600" in audio["question"], audio["question"])
check("with a warning that says why",
      "$1,500" in audio["warning"], audio["warning"])

plan["creativePlan"]["audio"] = {"answer": cn.COMP, "confirmed": True,
                                 "confirmed_at": 600}
check("once confirmed, the gate is satisfied", cn.evaluate(plan)["ok"] is True)
# Video is on comp too by this point, and it needed no confirmation — both
# are recorded, because both are production Smart 1 is absorbing.
check("and every comp is recorded",
      cn.evaluate(plan)["comped"] == ["video", "audio"], cn.evaluate(plan)["comped"])

# The confirmation was given against $600. Cut the line and it must lapse.
plan["items"][1]["dollars"] = 50
lapsed = next(r for r in cn.evaluate(plan)["media"] if r["medium"] == "audio")
check("a comp confirmed at $600 does not carry to a $150 campaign",
      not lapsed["confirmed"] and not lapsed["resolved"], lapsed)
check("and it asks again with the new number",
      "$150" in lapsed["question"], lapsed["question"])
plan["items"][1]["dollars"] = 400
check("raising the budget back above what was confirmed keeps it",
      cn.evaluate(plan)["ok"] is True)

check("a plan with no video or audio has nothing to ask",
      cn.evaluate({"items": [{"category": "DISPLAY", "product": "Category",
                              "dollars": 900}]})["ok"] is True)
check("and no creative line on the proposal",
      cn.summary_line({"items": [{"category": "DISPLAY", "product": "Category"}]}) == "")

# ---------------------------------------------------------------------------
section("the wizard and the server classify identically")
# ---------------------------------------------------------------------------
# The wizard carries a JavaScript copy so the Creative step can react as a rep
# edits the plan. If the two drift, the screen asks for a spot the server does
# not require — or worse, the reverse — and nothing errors.
TEMPLATE = os.path.join(ROOT, "modules", "sales_builder", "templates", "index.html")
js_source = "\n".join(m.group(1) for m in re.finditer(
    r"<script>(.*?)</script>", open(TEMPLATE, encoding="utf-8").read(), re.S))

extract = ""
for token in ("const EXPLICIT_MEDIUM=", "const CATEGORY_MEDIUM=", "function mediumOf("):
    i = js_source.index(token)
    ends = [j for j in (js_source.find("\nfunction ", i + 10),
                        js_source.find("\nconst ", i + 10),
                        js_source.find("\n/*", i + 10)) if j > 0]
    extract += js_source[i:min(ends)] + "\n"

harness = (extract + "\nconst C=" + json.dumps([{"category": c, "product": p}
                                                for c, p, _ in CASES]) + ";\n"
           "console.log(JSON.stringify(C.map(mediumOf)));\n")
js_path = os.path.join(_TMP, "medium.js")
open(js_path, "w", encoding="utf-8").write(harness)
try:
    out = subprocess.run(["node", js_path], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    js_media = json.loads(out)
    py_media = [cn.medium_of({"category": c, "product": p}) for c, p, _ in CASES]
    check("the wizard classifies every rate-card product exactly as the server does",
          js_media == py_media,
          [f"{c}/{p}: js={j} py={y}" for (c, p, _), j, y
           in zip(CASES, js_media, py_media) if j != y])
except FileNotFoundError:
    print("  skip node is not installed — wizard/server agreement unchecked")
except subprocess.CalledProcessError as exc:
    check("the wizard's creative helpers run", False, exc.stderr[:300])

# Check the two constants match too — a threshold that differs between the
# screen and the server means the wizard asks and the server does not care.
js_threshold = re.search(r"COMP_CONFIRM_UNDER\s*=\s*(\d+)", js_source)
check("the comp threshold is the same on both sides",
      js_threshold and int(js_threshold.group(1)) == cn.COMP_CONFIRM_UNDER,
      js_threshold.group(1) if js_threshold else "not found")
js_production = re.search(r"TYPICAL_PRODUCTION\s*=\s*\{video:(\d+),audio:(\d+)\}", js_source)
check("so are the default production figures",
      js_production and [int(js_production.group(1)), int(js_production.group(2))]
      == [cn.TYPICAL_PRODUCTION[cn.VIDEO], cn.TYPICAL_PRODUCTION[cn.AUDIO]],
      js_production.groups() if js_production else "not found")

# ---------------------------------------------------------------------------
section("Expected Results & ROI is computed, not written")
# ---------------------------------------------------------------------------
from modules.sales_builder import app as builder                   # noqa: E402

campaign = {
    "months": 6, "kpis": ["Cost per lead"],
    "items": [
        {"category": "OTT", "product": "Connected TV - Targeted",
         "label": "OTT — Connected TV - Targeted", "rate": "CPM",
         "rateValue": 35.0, "dollars": 4000},
        {"category": "YOUTUBE", "product": "TrueView", "rate": "CPV",
         "rateValue": 0.20, "dollars": 1000},
        {"category": "SEARCH ENGINE MARKETING / PAY PER CLICK",
         "product": "Google Ads Management", "dollars": 1900},
    ],
}
results = builder.expected_results(campaign)
by_product = {r["product"]: r for r in results["rows"]}

# $4,000 at a $35.00 CPM is 114,285 impressions a month. If this number ever
# changes, either the rate card moved or the arithmetic broke.
check("a CPM buy is priced off the card rate",
      by_product["Connected TV - Targeted"]["units"] == int(4000 / 35 * 1000),
      by_product["Connected TV - Targeted"]["units"])
check("a CPV buy is counted in views",
      by_product["TrueView"]["units"] == int(1000 / 0.20)
      and by_product["TrueView"]["unit_label"].startswith("views"),
      by_product["TrueView"])
check("a management fee reports no impressions at all",
      by_product["Google Ads Management"]["units"] is None,
      by_product["Google Ads Management"])
check("and is named rather than silently excluded",
      results["unpriced"] == ["Google Ads Management"], results["unpriced"])
check("impressions and views are totalled separately",
      results["totals"]["impressions"] == int(4000 / 35 * 1000) * 6
      and results["totals"]["views"] == int(1000 / 0.20) * 6, results["totals"])
check("the campaign total is monthly x months",
      results["totals"]["campaign"] == 6900 * 6, results["totals"]["campaign"])
check("video in the plan adds completion metrics",
      any("Completed video views" == m for m in results["metrics"]), results["metrics"])
check("the Suite is named as the source of truth for closing",
      any("Smart 1 Suite" in m for m in results["metrics"]), results["metrics"])
check("an empty plan totals nothing rather than guessing",
      builder.expected_results({"items": []})["totals"]["impressions"] == 0)

# ---------------------------------------------------------------------------
section("the investment summary keeps the money apart")
# ---------------------------------------------------------------------------
state = dict(campaign)
state["creativePlan"] = {"video": {"answer": cn.CLIENT_PAYS, "fee": 750}}


class _Q:                       # the columns investment_lines reads
    months = 6
    monthly_budget = 6900


invest = builder.investment_lines(state, _Q())
kinds = {line["kind"] for line in invest["lines"]}
check("platform licensing is its own line", "saas" in kinds, kinds)
check("media spend is its own line", "media" in kinds, kinds)
check("one-time production is its own line", "setup" in kinds, kinds)
check("only the recurring lines recur",
      invest["recurring_monthly"] == 599 + 6900, invest["recurring_monthly"])
check("production is charged once, not monthly",
      invest["campaign_total"] == (599 + 6900) * 6 + 750, invest["campaign_total"])
check("the first month includes the one-time cost",
      invest["first_month"] == 599 + 6900 + 750, invest["first_month"])

# ---------------------------------------------------------------------------
section("a proposal cannot lose its required sections")
# ---------------------------------------------------------------------------
old_shape = {"client": "Legacy Co", "months": 3, "items": [],
             "sections": [{"id": "about", "title": "About", "kind": "text",
                           "enabled": True, "body": "Written last year."}]}
builder.ensure_sections(old_shape)
have = {s["id"] for s in old_shape["sections"]}
check("an older quote keeps the copy someone wrote",
      any(s.get("body") == "Written last year." for s in old_shape["sections"]))
check("but gains Expected Results & ROI", "roi" in have, sorted(have))
check("and the media plan and investment summary",
      {"mediaplan", "packages"} <= have, sorted(have))
builder.ensure_sections(old_shape)
check("running it twice does not duplicate them",
      len([s for s in old_shape["sections"] if s["id"] == "roi"]) == 1)

fresh_state = {"client": "New Co", "months": 6, "objectives": ["Lead Generation"],
               "items": [], "industry": "legal"}
builder.ensure_sections(fresh_state)
seeded = fresh_state["sections"]
check("a new proposal follows the full structure",
      [s["id"] for s in seeded] == [s["id"] for s in spec.OUTLINE])
check("the Suite is positioned in the technology section",
      "central nervous system" in
      next(s for s in seeded if s["id"] == "technology")["body"])
check("no seeded copy breaks a directive",
      all(spec.violations(s["body"]) == [] for s in seeded))

# ---------------------------------------------------------------------------
section("through the running app")
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
    "client": "Riverstone Dental", "industry": "legal", "months": 6,
    "budget": 8000, "objectives": ["Lead Generation"], "kpis": ["Cost per lead"],
    "targetAreas": [{"name": "Carmel", "type": "City/ZIP + Radius",
                     "origin": "Carmel, IN", "radius": 10}],
    "selectedPackage": {"name": "Recommended", "monthly": 8000, "total": 48000},
    "items": [
        {"category": "OTT", "product": "Connected TV - Targeted", "rate": "CPM",
         "rateValue": 35.0, "dollars": 6000},
        {"category": "DIGITAL RADIO", "product": "Podcasts - Targeted",
         "rate": "CPM", "rateValue": 18.0, "dollars": 100},
    ],
}
quote = api("post", "/sales/builder/api/quotes", json={"data": quote_state})["quote"]
check("an unanswered creative gate shows on the quote",
      set(quote["creative"]["unresolved"]) == {"video", "audio"},
      quote["creative"]["unresolved"])
check("and in the IO gap list",
      {"creative_video", "creative_audio"} <= {g["key"] for g in quote["gaps"]},
      [g["key"] for g in quote["gaps"]])

served = api("get", "/sales/builder/api/proposal-spec")
check("the wizard can read the specification from the server",
      len(served["outline"]) == len(spec.OUTLINE))
check("including the comp threshold",
      served["comp_confirm_under"] == cn.COMP_CONFIRM_UNDER)

checked = api("post", "/sales/builder/api/creative-check", json={"data": quote_state})
audio_row = next(r for r in checked["media"] if r["medium"] == "audio")
check("the route asks first whether the creative already exists",
      "already have audio creative" in audio_row["question"], audio_row["question"])
check("and flags that a comp here would need confirming",
      audio_row["needs_confirm"] is True, audio_row)

# Only once comp is chosen does the "are you sure" question appear.
comping = dict(quote_state, creativePlan={"audio": {"answer": cn.COMP}})
comped_row = next(r for r in api("post", "/sales/builder/api/creative-check",
                                 json={"data": comping})["media"]
                  if r["medium"] == "audio")
check("choosing to comp a $600 audio campaign asks for confirmation, with the number",
      "$600" in comped_row["question"] and not comped_row["resolved"],
      comped_row["question"])

answered = dict(quote_state)
answered["creativePlan"] = {"video": {"answer": cn.HAS},
                            "audio": {"answer": cn.COMP, "confirmed": True,
                                      "confirmed_at": 600}}
api("put", f"/sales/builder/api/quotes/{quote['id']}", json={"data": answered})
after = api("get", f"/sales/builder/api/quotes/{quote['id']}")["quote"]
check("answering it clears the gaps",
      after["creative"]["ok"] is True
      and not [g for g in after["gaps"] if g["key"].startswith("creative_")],
      after["creative"])

pdf = http.get(f"/sales/builder/api/quotes/{quote['id']}/pdf")
check("the 13-part PDF builds", pdf.status_code == 200 and len(pdf.data) > 8000,
      f"{pdf.status_code} / {len(pdf.data)} bytes")
docx = http.get(f"/sales/builder/api/quotes/{quote['id']}/docx")
check("so does the Word copy", docx.status_code == 200 and len(docx.data) > 8000)

# A plan with nothing gated must still produce a proposal.
display_only = dict(quote_state)
display_only["items"] = [{"category": "DISPLAY", "product": "Advanced Audience",
                          "rate": "CPM", "rateValue": 5.5, "dollars": 3000}]
plain = api("post", "/sales/builder/api/quotes", json={"data": display_only})["quote"]
check("a display-only plan has no creative gate to answer",
      plain["creative"]["ok"] is True and plain["creative"]["media"] == [])
check("and still renders",
      http.get(f"/sales/builder/api/quotes/{plain['id']}/pdf").status_code == 200)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
