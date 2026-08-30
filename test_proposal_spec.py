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
# The narrative runs cover to next steps, then two appendices: what more would
# look like, and the trafficking ZIP list. The ZIPs used to sit inside the
# audience section, where a hundred five-digit numbers buried the strategy.
APPENDICES = ["growth", "zips"]
check("the narrative is ordered cover-first, next-steps-last",
      ids[0] == "cover" and ids[-len(APPENDICES) - 1] == "next",
      ids[:1] + ids[-len(APPENDICES) - 1:])
check("and the appendices come after it, in order",
      ids[-len(APPENDICES):] == APPENDICES, ids[-len(APPENDICES):])
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

# Display and retargeting joined the gate. A display plan reached the
# insertion order with the creative box empty exactly as often as a CTV one
# did -- it simply cost $250 and a week rather than a shoot, so it was found
# at trafficking instead of at launch and nobody called it a failure.
check("video, audio and display are all gated",
      cn.gated_media(plan) == ["video", "audio", "display"], cn.gated_media(plan))
check("display now raises its own question",
      any(r["medium"] == "display" for r in cn.evaluate(plan)["media"]))
check("spend is the whole flight, not one month",
      cn.medium_spend(plan, cn.VIDEO) == 12000.0, cn.medium_spend(plan, cn.VIDEO))
check("nothing is resolved before it is asked",
      cn.evaluate(plan)["unresolved"] == ["video", "audio", "display"],
      cn.evaluate(plan)["unresolved"])
check("an unanswered gate is a stated gap",
      {g["key"] for g in cn.gaps(plan)} ==
      {"creative_video", "creative_audio", "creative_display"})

# Retargeting is asked separately from display: the same six sizes, carrying
# the offer that brings somebody back rather than the one that introduced the
# brand. A plan with both that answers once has answered for one of them.
retarget = {"months": 3, "items": [
    {"category": "RETARGETING", "product": "Website Retargeting", "dollars": 400},
    {"category": "DATA TARGETED DISPLAY",
     "product": "Select Tactics - Comes with Retargeting", "dollars": 1000}]}
check("retargeting is its own medium",
      cn.medium_of(retarget["items"][0]) == cn.RETARGETING)
check("and the programmatic display buy is not retargeting because of its name",
      cn.medium_of(retarget["items"][1]) == cn.DISPLAY,
      cn.medium_of(retarget["items"][1]))
check("so both are asked for",
      cn.gated_media(retarget) == ["display", "retargeting"],
      cn.gated_media(retarget))

# The sizes come from the IO's own spec kit rather than from a second list.
units = cn.required_units(retarget, cn.DISPLAY)
check("the display sizes are the spec kit's", units["measured"] and units["units"])
check("and they are real banner units",
      "300x250" in cn.units_line(retarget, cn.DISPLAY),
      cn.units_line(retarget, cn.DISPLAY))

# A unit is described in the terms it is actually specified in. An audio spot
# has no pixel size -- it has a length and a bitrate -- and listing sizes alone
# made the audio row read "300x250", which is the *optional companion banner*
# presented as the whole requirement. A client reading that sends a banner and
# no spot.
audio_plan = {"months": 3, "items": [
    {"category": "DIGITAL RADIO", "product": "Podcasts - Targeted", "dollars": 900}]}
audio_line = cn.units_line(audio_plan, cn.AUDIO)
check("an audio spot is described as a spot, not as a banner size",
      audio_line.startswith("Audio Spot") and "15–60s" in audio_line, audio_line)
check("and the companion banner is named as the companion",
      "companion banner: 300x250" in audio_line, audio_line)
video_plan = {"months": 3, "items": [
    {"category": "OTT", "product": "Connected TV - Targeted", "dollars": 900}]}
check("a video unit carries its length and format",
      "1920x1080" in cn.units_line(video_plan, cn.VIDEO)
      and "15–30s" in cn.units_line(video_plan, cn.VIDEO),
      cn.units_line(video_plan, cn.VIDEO))
# Desktop, mobile and tablet each carry their own HTML5 package unit.
check("an alternative delivery format is named once, after the sizes",
      cn.units_line(retarget, cn.DISPLAY).count("HTML5 package") == 1,
      cn.units_line(retarget, cn.DISPLAY))
check("and the sizes lead, because that is what the ask is",
      cn.units_line(retarget, cn.DISPLAY).startswith("728x90"),
      cn.units_line(retarget, cn.DISPLAY))

# "A spot" is a video and audio word; the gate covers banners now.
banner_gap = cn.gaps({"months": 1, "items": [
    {"category": "RETARGETING", "product": "Website Retargeting", "dollars": 100}]})
check("a banner gap asks for banners, not for a spot",
      "banners exist" in banner_gap[0]["label"], banner_gap[0]["label"])

# Display creative is $250 of design on the card, so comping it stops being
# sensible far lower down than a shoot does. A confirmation that fires on
# every plan is one nobody reads.
check("display is questioned at $500, not $1,500",
      cn.confirm_under(cn.DISPLAY) == 500 and cn.confirm_under(cn.VIDEO) == 1500)
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
plan["creativePlan"]["display"] = {"answer": cn.HAS}
check("once confirmed, the gate is satisfied", cn.evaluate(plan)["ok"] is True,
      cn.evaluate(plan)["unresolved"])
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

# A display-only plan is no longer a plan with nothing to ask -- that was the
# assumption this whole change undoes.
display_only = {"items": [{"category": "DISPLAY", "product": "Category",
                           "dollars": 900}]}
check("a display-only plan is asked for banners",
      cn.evaluate(display_only)["ok"] is False,
      cn.evaluate(display_only)["unresolved"])
check("and says so on the proposal rather than staying silent",
      "not yet confirmed" in cn.summary_line(display_only),
      cn.summary_line(display_only))

# A plan of pure management-fee work still has nothing to ask.
fees_only = {"items": [{"category": "SEARCH ENGINE OPTIMIZATION",
                        "product": "Search Engine Optimization", "dollars": 900}]}
check("a plan with no media creative at all has nothing to ask",
      cn.evaluate(fees_only)["ok"] is True, cn.evaluate(fees_only)["unresolved"])
check("and no creative line on the proposal",
      cn.summary_line(fees_only) == "", cn.summary_line(fees_only))

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

# ---------------------------------------------------------------------------
section("one minimum rule, read by both documents")
# ---------------------------------------------------------------------------
# There were three numbers for this one question. The wizard held paid search
# to $1,500 in a hardcoded line, hub/rate_card.py said $500, and the IO derived
# its own floor from each product's listed rate. The strictest of the three
# blocked campaigns the IO would have written without complaint, and nothing on
# any screen said the three disagreed.
from hub import rate_card as rc                                    # noqa: E402

check("paid search is quotable at $400", rc.minimum_for("Pay Per Click") == 400,
      rc.minimum_for("Pay Per Click"))
check("and blocked below it",
      any(g["level"] == "block" for g in
          rc.guardrails([{"product": "Pay Per Click", "monthly": 380}])))
check("a $420 paid-search-only plan clears both the line and the plan floor",
      not any(g["level"] == "block" for g in
              rc.guardrails([{"product": "Pay Per Click", "monthly": 420}])))

# The short name is what every document here stores; the card carries the whole
# description in the same field. Exact-only lookup missed them, and each miss
# was a silent default rather than an error.
check("a short product name still resolves to its category",
      (rc.find("Connected TV - Targeted") or {}).get("category") == "OTT")
check("so Connected TV keeps OTT's $1,500 floor rather than the default",
      rc.minimum_for("Connected TV - Targeted") == 1500,
      rc.minimum_for("Connected TV - Targeted"))
# ...but an anchored match only. "Category" is a real product name; a
# contains-match either way round would let it swallow any longer phrase.
check("the match is anchored, not a substring",
      (rc.find("Targeted") or {}) == {} or
      (rc.find("Targeted") or {}).get("product", "").lower().startswith("targeted"))

# "Programmatic - Targeted" exists under DIGITAL RADIO at $18 CPM audio and
# under DISPLAY at $17 CPM. find() can only return one of them, so the line's
# own category has to win or one of the two gets the wrong floor.
check("an ambiguous product takes the floor of the category it was bought in",
      [any(g["level"] == "block" for g in rc.guardrails(
           [{"product": "Programmatic - Targeted", "category": c, "monthly": 900}]))
       for c in ("DIGITAL RADIO", "DISPLAY")] == [True, False])

for name, tmpl in (("wizard", os.path.join(ROOT, "modules", "sales_builder",
                                           "templates", "index.html")),
                   ("insertion order", os.path.join(ROOT, "modules", "io_builder",
                                                    "templates", "index.html"))):
    src = open(tmpl, encoding="utf-8").read()
    got = {}
    for const in ("MIN_MONTHLY_DEFAULT", "MIN_BY_CATEGORY", "MIN_BY_PRODUCT"):
        m = re.search(r"const %s=(.*?);\n" % const, src, re.S)
        got[const] = json.loads(m.group(1)) if m else None
    check(f"the {name} mirrors the default minimum",
          got["MIN_MONTHLY_DEFAULT"] == rc.MIN_MONTHLY_DEFAULT, got["MIN_MONTHLY_DEFAULT"])
    check(f"the {name} mirrors every category minimum",
          got["MIN_BY_CATEGORY"] == rc.MIN_BY_CATEGORY, got["MIN_BY_CATEGORY"])
    check(f"the {name} mirrors the per-product overrides",
          got["MIN_BY_PRODUCT"] == rc.MIN_BY_PRODUCT, got["MIN_BY_PRODUCT"])

# And that the mirrored function agrees with Python product by product, not
# merely that the table was copied.
MIN_CASES = [("Pay Per Click", "SEARCH ENGINE MARKETING / PAY PER CLICK"),
             ("Connected TV - Targeted", "OTT"),
             ("Podcasts - Targeted", "DIGITAL RADIO"),
             ("Category", "DISPLAY"),
             ("Programmatic - Targeted", "DIGITAL RADIO"),
             ("Programmatic - Targeted", "DISPLAY"),
             ("Something Nobody Sells", "")]
min_js = ""
for token in ("const MIN_MONTHLY_DEFAULT=", "const MIN_BY_CATEGORY=",
              "const MIN_BY_PRODUCT=", "function minimumFor("):
    i = js_source.index(token)
    ends = [j for j in (js_source.find("\nfunction ", i + 10),
                        js_source.find("\nconst ", i + 10),
                        js_source.find("\n/*", i + 10)) if j > 0]
    min_js += js_source[i:min(ends)] + "\n"
harness = (min_js + "\nconst M=" + json.dumps(MIN_CASES) + ";\n"
           "console.log(JSON.stringify(M.map(x=>minimumFor(x[0],x[1]))));\n")
js_path2 = os.path.join(_TMP, "minimums.js")
open(js_path2, "w", encoding="utf-8").write(harness)
try:
    js_mins = json.loads(subprocess.run(["node", js_path2], capture_output=True,
                                        text=True, timeout=30, check=True).stdout)
    py_mins = [rc.minimum_for(p, c) for p, c in MIN_CASES]
    check("the wizard and the server floor every product identically",
          js_mins == py_mins,
          [f"{p}/{c}: js={j} py={y}" for (p, c), j, y
           in zip(MIN_CASES, js_mins, py_mins) if j != y])
except FileNotFoundError:
    print("  skip node is not installed — minimum agreement unchecked")
except subprocess.CalledProcessError as exc:
    check("the wizard's minimum helper runs", False, exc.stderr[:300])

# Check the two constants match too — a threshold that differs between the
# screen and the server means the wizard asks and the server does not care.
js_threshold = re.search(r"COMP_CONFIRM_UNDER\s*=\s*(\d+)", js_source)
check("the comp threshold is the same on both sides",
      js_threshold and int(js_threshold.group(1)) == cn.COMP_CONFIRM_UNDER,
      js_threshold.group(1) if js_threshold else "not found")
js_production = re.search(r"TYPICAL_PRODUCTION\s*=\s*\{([^}]*)\}", js_source)
js_figures = dict(re.findall(r"(\w+):(\d+)", js_production.group(1))) \
    if js_production else {}
check("so are the default production figures",
      {k: int(v) for k, v in js_figures.items()} == cn.TYPICAL_PRODUCTION,
      js_figures or "not found")

# Per medium, and it has to be the same table on both sides -- a display comp
# questioned at $1,500 on screen and $500 on the server asks twice or never.
js_by_medium = re.search(r"COMP_CONFIRM_BY_MEDIUM\s*=\s*\{([^}]*)\}", js_source)
js_thresholds = dict(re.findall(r"(\w+):(\d+)", js_by_medium.group(1))) \
    if js_by_medium else {}
check("and the per-medium comp thresholds",
      {k: int(v) for k, v in js_thresholds.items()} == cn.COMP_CONFIRM_BY_MEDIUM,
      js_thresholds or "not found")

# Every gated medium is gated on both sides, or the screen asks for a spot
# the server does not require, or worse the reverse.
js_gated = re.search(r"GATED_MEDIA\s*=\s*\[([^\]]*)\]", js_source)
check("and which media are gated at all",
      js_gated and tuple(re.findall(r'"(\w+)"', js_gated.group(1))) == cn.GATED,
      js_gated.group(1) if js_gated else "not found")

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

# The listed rate is what Smart 1 pays; the quoted rate is what is sold, and
# it starts at 2x. Delivery is what the client's money buys at the rate the
# client is quoted -- computed off the card's own number, a $4,000 line
# promised 114,285 impressions on the client's own ROI table and could only
# ever have bought half of them.
SELL = rc.SELL_MULTIPLIER
check("a CPM buy is priced off the quoted rate, not the card rate",
      by_product["Connected TV - Targeted"]["units"] == int(4000 / (35 * SELL) * 1000),
      by_product["Connected TV - Targeted"]["units"])
check("and the quoted rate is on the row",
      by_product["Connected TV - Targeted"]["quoted_rate"] == 35 * SELL,
      by_product["Connected TV - Targeted"]["quoted_rate"])
# Four categories carry a product called "Demographic" or "TrueView", so the
# quote names the line by its category as well -- a client cannot tell which
# of the four they bought from the product name alone, and neither can the IO.
check("an ambiguous product name is qualified by its category",
      "Youtube — TrueView" in by_product, sorted(by_product))
check("a CPV buy is counted in views",
      by_product["Youtube — TrueView"]["units"] == int(1000 / (0.20 * SELL))
      and by_product["Youtube — TrueView"]["unit_label"].startswith("views"),
      by_product["Youtube — TrueView"])
check("a management fee reports no impressions at all",
      by_product["Google Ads Management"]["units"] is None,
      by_product["Google Ads Management"])
check("and is not marked up either — there is no rate to double",
      by_product["Google Ads Management"]["quoted_rate"] is None,
      by_product["Google Ads Management"]["quoted_rate"])
check("and is named rather than silently excluded",
      results["unpriced"] == ["Google Ads Management"], results["unpriced"])
check("impressions and views are totalled separately",
      results["totals"]["impressions"] == int(4000 / (35 * SELL) * 1000) * 6
      and results["totals"]["views"] == int(1000 / (0.20 * SELL)) * 6,
      results["totals"])

# A rep's own number wins over the 2x start, and only for the line they set.
adjusted = json.loads(json.dumps(campaign))
adjusted["items"][0]["sellRate"] = 52.5
adj = {r["product"]: r for r in builder.expected_results(adjusted)["rows"]}
check("a rate a rep adjusts is what the delivery is computed at",
      adj["Connected TV - Targeted"]["units"] == int(4000 / 52.5 * 1000),
      adj["Connected TV - Targeted"]["units"])
check("and the other lines keep the 2x start",
      adj["Youtube — TrueView"]["quoted_rate"] == 0.20 * SELL,
      adj["Youtube — TrueView"]["quoted_rate"])
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
section("discovery, and what we suggest they should do")
# ---------------------------------------------------------------------------
from hub import current_marketing as cm                            # noqa: E402

check("the three new questions are asked",
      {"retargeting", "aiOptimized", "websiteHappy"} <= {q["key"] for q in cm.QUESTIONS},
      [q["key"] for q in cm.QUESTIONS])
check("and the five that came after them",
      {"reputation", "email", "chat", "callTracking", "texting"}
      <= {q["key"] for q in cm.QUESTIONS},
      [q["key"] for q in cm.QUESTIONS])
check("each of the five raises a suggestion when the answer is no",
      all(any(r["key"] == k for r in cm.SUGGESTION_RULES)
          for k in ("reputation", "email", "chat", "callTracking", "texting")))

# Every answer is required. A blank was reaching the proposal as though the
# client did not do that thing, which is a different claim from "we did not
# ask" -- and Unknown exists on the form precisely so there is an honest
# answer for the second case.
check("a blank discovery step is incomplete", cm.complete({}) is False)
check("and says exactly which are missing",
      len(cm.unanswered({})) == len(cm.QUESTIONS))
half = {"mkt": {q["key"]: cm.YES for q in cm.QUESTIONS[:4]}}
check("a half-answered one names only the rest",
      [q["key"] for q in cm.unanswered(half)]
      == [q["key"] for q in cm.QUESTIONS[4:]])
check("Unknown counts as answered, because it is an answer",
      cm.complete({"mkt": {q["key"]: cm.UNKNOWN for q in cm.QUESTIONS}}) is True)

# The wizard carries its own copy so the step can react without a round trip.
# Twelve questions on one screen and eleven rules behind them is exactly the
# kind of list that gets edited on one side only.
wiz = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                        "index.html"), encoding="utf-8").read()
mq = re.search(r"const DISCOVERY_QUESTIONS=\[(.*?)\n\];", wiz, re.S)
js_keys = re.findall(r'key:"([^"]+)"', mq.group(1)) if mq else []
check("the wizard asks the same questions, in the same order",
      js_keys == [q["key"] for q in cm.QUESTIONS], js_keys)
js_labels = re.findall(r'label:"([^"]+)"', mq.group(1)) if mq else []
check("with the same wording",
      js_labels == [q["label"] for q in cm.QUESTIONS],
      [(a, b) for a, b in zip(js_labels, [q["label"] for q in cm.QUESTIONS]) if a != b])
mr = re.search(r"const SUGGESTION_RULES=\[(.*?)\n\];", wiz, re.S)
js_rules = re.findall(r'key:"([^"]+)",when:', mr.group(1)) if mr else []
check("and holds the same suggestion rules",
      js_rules == [r["key"] for r in cm.SUGGESTION_RULES], js_rules)
# The products matter as much as the titles now: the media-mix step offers
# what discovery pointed at as one press each, and it can only do that if the
# wizard's copy of a rule names the same product the server's does. Without
# this the panel would come back empty on a client with real gaps and read as
# "discovery found nothing".
js_products = re.findall(r'products:(\[[^\]]*\])', mr.group(1)) if mr else []
check("including the products behind each one",
      [json.loads(p) for p in js_products] ==
      [list(r.get("products") or []) for r in cm.SUGGESTION_RULES],
      js_products)
# Every product a rule names has to be something a rep can act on, or the
# row is advice with no next step. Two of them are deliberately not rate-card
# lines and are allowed by name with the reason, so a typo cannot hide in the
# gap -- the allowlist pattern hub/leads.py uses for its webhook variables.
NOT_ON_THE_CARD = {
    # Quoted as part of the Suite licence rather than as a media line; the
    # Suite panel two steps on is where it is priced.
    "Smart 1 Suite",
    # The Suite's own call centre. It has never been a separate card product,
    # and naming it here is what makes the suggestion readable to a rep.
    "Call Tracking",
}
card_products = {p["product"] for p in rc.products()}
unsellable = sorted({name for r in cm.SUGGESTION_RULES
                     for name in (r.get("products") or [])
                     if name not in card_products
                     and not any(p.startswith(name) for p in card_products)
                     and name not in NOT_ON_THE_CARD})
check("and every product named is one the card sells, or is allowed by name",
      unsellable == [], unsellable)

# Every discovery question has to change something. An answer that changes
# nothing is what this module was written to undo, and a thirteenth question
# added without a rule is how it comes back.
check("no discovery question is read by nothing",
      cm.unanswered_keys() == [], cm.unanswered_keys())

# ---------------------------------------------------------------------------
section("the Suite covers what discovery says they lack")
# ---------------------------------------------------------------------------
# The Suite was quoted on every proposal at a tier picked purely from media
# spend, with the client never told which of the things they said they were
# not doing it closes -- so the one line on the Investment Summary that
# recurs for ever had no stated reason for being there.
lacking = {"mkt": {"chat": cm.NO, "texting": cm.NO, "reputation": cm.UNKNOWN,
                   "socialScheduling": cm.NO, "callTracking": cm.YES}}
cover = cm.suite_coverage(lacking, "Smart 1")
check("what this tier closes is named",
      {r["key"] for r in cover["covered"]} ==
      {"texting", "reputation", "socialScheduling"},
      [r["key"] for r in cover["covered"]])
check("something they already do is not claimed",
      "callTracking" not in {r["key"] for r in cover["covered"]})
# Smart webchat is Smarter and up. Offering it against a Smart 1 licence
# sells something the client cannot switch on.
check("a feature above this tier is named with the tier that has it",
      [(r["key"], r["tier"]) for r in cover["needs_upgrade"]] == [("chat", "Smarter")],
      cover["needs_upgrade"])
check("and it is covered once the tier is raised",
      "chat" in {r["key"] for r in cm.suite_coverage(lacking, "Smarter")["covered"]})
# An unanswered question is not a gap the Suite gets credit for closing.
check("an unanswered question is not measured, not a gap",
      {r["key"] for r in cover["not_measured"]} == {"email"},
      [r["key"] for r in cover["not_measured"]])
# Two questions can want the same part of the Suite. Claimed twice they read
# as two things the licence buys -- "Social planner" directly above "Social
# planner and media library" -- on the one panel whose job is to justify a
# recurring charge. `socialPosting` is unanswered in this fixture and its
# capability is already covered by `socialScheduling`, so it is neither
# claimed again nor reported as a hole.
check("a capability is claimed once, however many questions want it",
      [r["feature"] for r in cover["covered"]].count("Social planner") == 1,
      [r["feature"] for r in cover["covered"]])
check("and a covered capability is not also reported as not measured",
      "socialPosting" not in {r["key"] for r in cover["not_measured"]})
check("the proposal line names them in prose",
      "texting" in cm.suite_line(lacking, "Smart 1"), cm.suite_line(lacking, "Smart 1"))
check("and says nothing at all when they already have everything",
      cm.suite_line({"mkt": {q["key"]: cm.YES for q in cm.QUESTIONS}}, "Smart 1") == "")

doing_everything = {"mkt": {q["key"]: cm.YES for q in cm.QUESTIONS},
                    "traditional": {"running": cm.NO}}
check("a client doing everything gets no suggestions",
      cm.suggestions(doing_everything) == [])

gaps_everywhere = {"mkt": {"retargeting": cm.NO, "aiOptimized": cm.NO,
                           "websiteHappy": cm.NO, "seo": cm.NO,
                           "paidSearch": cm.NO, "paidSocial": cm.NO}}
titles = [s["title"] for s in cm.suggestions(gaps_everywhere)]
check("not retargeting raises retargeting",
      any("Retarget" in t for t in titles), titles)
check("not optimized for AI raises it", any("AI search" in t for t in titles))
check("an unhappy website raises the website first",
      any("page the media points at" in t for t in titles))
check("every suggestion names a product that could deliver it",
      all(s["products"] for s in cm.suggestions(gaps_everywhere)))

# "Unknown" is a gap we have not confirmed, and worth raising — but an
# unhappy website is a stated fact, so Unknown there is not a complaint.
unknowns = {"mkt": {"retargeting": cm.UNKNOWN, "aiOptimized": cm.UNKNOWN,
                    "websiteHappy": cm.UNKNOWN}}
unknown_titles = [s["title"] for s in cm.suggestions(unknowns)]
check("unknown retargeting is still raised",
      any("Retarget" in t for t in unknown_titles), unknown_titles)
check("but an unknown opinion of their website is not",
      not any("page the media points at" in t for t in unknown_titles), unknown_titles)

check("a rep can dismiss a suggestion",
      cm.suggestions(dict(gaps_everywhere, suggestDismissed=["retargeting"]))
      != cm.suggestions(gaps_everywhere))

# ---------------------------------------------------------------------------
section("traditional media")
# ---------------------------------------------------------------------------
none_running = {"traditional": {"running": cm.NO}}
check("no traditional media means no posture to decide", cm.gaps(none_running) == [])
check("and nothing added to ROI", cm.roi_note(none_running) == "")
check("and no guidance for the writer", cm.guidance(none_running) == "")

undecided = {"traditional": {"running": cm.YES, "detail": "Radio on WBNS"}}
check("running it with no posture is a stated gap",
      [g["key"] for g in cm.gaps(undecided)] == ["traditional_posture"],
      cm.gaps(undecided))

supplement = {"traditional": {"running": cm.YES, "detail": "Radio on WBNS",
                              "budget": "$6,000/mo", "posture": cm.SUPPLEMENT}}
shift = {"traditional": dict(supplement["traditional"], posture=cm.SHIFT)}
check("choosing a posture closes the gap", cm.gaps(supplement) == [])
check("what they told us survives into the summary",
      "WBNS" in cm.traditional_summary(supplement)
      and "$6,000/mo" in cm.traditional_summary(supplement),
      cm.traditional_summary(supplement))
check("supplement and shift give the writer different guidance",
      cm.guidance(supplement) != cm.guidance(shift))
check("both carry the detail and the budget",
      all("WBNS" in cm.guidance(s) and "$6,000/mo" in cm.guidance(s)
          for s in (supplement, shift)))

# The instruction not to argue matters as much as the posture. A model handed
# "they want to move budget to digital" writes a case against radio, and a
# proposal that opens by calling a client's spend wasted loses the room.
for name, state in (("supplement", supplement), ("shift", shift)):
    text = cm.guidance(state)
    check(f"the {name} guidance forbids arguing against their media",
          "do not argue against their traditional media" in text, text[-160:])
    check(f"the {name} guidance forbids opening with it", "do not open with it" in text)
    check(f"the {name} guidance forbids calling their spend wasted",
          "never claim their existing spend is wasted" in text)

check("supplement and shift say different things in ROI",
      cm.roi_note(supplement) != cm.roi_note(shift)
      and cm.roi_note(supplement) and cm.roi_note(shift))
check("shift is the one that talks about attribution",
      "attributable" in cm.roi_note(shift), cm.roi_note(shift))
check("an undecided posture adds nothing to ROI", cm.roi_note(undecided) == "")
check("the discovery picture reaches the writer's facts",
      "Radio on WBNS" in cm.for_prompt(dict(gaps_everywhere, **supplement)))

# ---------------------------------------------------------------------------
section("the PDF scales its type to what it holds")
# ---------------------------------------------------------------------------
import copy as _copy                                               # noqa: E402

fresh_doc = {"client": "Scale Co", "months": 6,
             "items": [{"category": "OTT", "product": "CTV", "dollars": 6000}],
             "targetAreas": [{"name": "A", "type": "DMA", "dma": "Indy"}]}
builder.ensure_sections(fresh_doc)
check("an ordinary proposal is not shrunk at all",
      builder._type_scale(fresh_doc) == 1.0, builder._type_scale(fresh_doc))

heavy = _copy.deepcopy(fresh_doc)
heavy["items"] *= 10
heavy["targetAreas"] *= 8
for sec in heavy["sections"]:
    if sec["kind"] in ("text", "friction"):
        sec["body"] = (sec["body"] or "") + " More copy. " * 90
heavy_scale = builder._type_scale(heavy)
check("a document with far more in it shrinks", heavy_scale < 1.0, heavy_scale)
check("but never below the readable floor",
      heavy_scale >= builder.TYPE_SCALE_MIN, heavy_scale)

extreme = _copy.deepcopy(heavy)
extreme["items"] *= 4
for sec in extreme["sections"]:
    if sec["kind"] in ("text", "friction"):
        sec["body"] = (sec["body"] or "") * 3
check("the floor holds however much is added",
      builder._type_scale(extreme) == builder.TYPE_SCALE_MIN,
      builder._type_scale(extreme))
check("scaling never raises", builder._type_scale({}) == 1.0)

# ---------------------------------------------------------------------------
section("every section can be written, one request at a time")
# ---------------------------------------------------------------------------
plan_state = _copy.deepcopy(fresh_doc)
plan_ids = [s["id"] for s in builder.writable_sections(plan_state)]
UNWRITTEN = {"cover", "zips"}
check("only the cover and the ZIP appendix have nothing to write",
      not (UNWRITTEN & set(plan_ids))
      and len(plan_ids) == len(spec.OUTLINE) - len(UNWRITTEN),
      plan_ids)
for needed in ("mediaplan", "packages", "roi", "reach", "timeline"):
    check(f"the generated section {needed} still takes intro copy",
          needed in plan_ids)

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

discovery_state = dict(quote_state, mkt={"retargeting": cm.NO, "aiOptimized": cm.NO,
                                          "websiteHappy": cm.NO},
                       traditional={"running": cm.YES, "detail": "Radio on WBNS",
                                    "budget": "$6,000/mo", "posture": cm.SHIFT})
discovered = api("post", "/sales/builder/api/quotes",
                 json={"data": discovery_state})["quote"]
check("the quote carries the suggestions the rep will see",
      len(discovered["suggestions"]) >= 3,
      [s["title"] for s in discovered["suggestions"]])
check("a decided traditional posture raises no gap",
      "traditional_posture" not in {g["key"] for g in discovered["gaps"]})

undecided_quote = api("post", "/sales/builder/api/quotes",
                      json={"data": dict(quote_state,
                                         traditional={"running": cm.YES})})["quote"]
check("an undecided one does",
      "traditional_posture" in {g["key"] for g in undecided_quote["gaps"]},
      [g["key"] for g in undecided_quote["gaps"]])

served_plan = api("post", "/sales/builder/api/ai/section-plan",
                  json={"data": discovery_state})
check("the wizard is told exactly what will be written",
      len(served_plan["sections"]) == len(spec.OUTLINE) - 2,
      len(served_plan["sections"]))
# ---------------------------------------------------------------------------
# Monthly or one-time, and for how long. A dollar figure on its own has not
# said which, and the difference is what gets billed: a $3,000 line read as
# monthly over a six-month flight is $18,000 nobody agreed to.
basis_base = {"months": 6, "items": [
    {"category": "OTT", "product": "Connected TV - Targeted",
     "rate": "CPM", "rateValue": 35.0, "dollars": 3000}]}


def _basis(**extra):
    st = dict(basis_base, items=[dict(basis_base["items"][0], **extra)])
    return builder.expected_results(st)


full = _basis()
check("a monthly line runs the whole flight by default",
      (full["rows"][0]["campaign"], full["totals"]["campaign"]) == (18000.0, 18000.0),
      full["rows"][0])

once = _basis(basis="one_time")
check("a one-time line bills once, not every month",
      once["rows"][0]["campaign"] == 3000.0, once["rows"][0]["campaign"])
check("but still sits in the monthly plan, spread across the flight",
      once["rows"][0]["monthly"] == 500.0, once["rows"][0]["monthly"])
check("and delivers its impressions once rather than six times",
      once["totals"]["impressions"] == full["totals"]["impressions"] // 6,
      (once["totals"]["impressions"], full["totals"]["impressions"]))

short = _basis(basis="monthly", term_months=None, termMonths=3)
check("a line that stops early costs only the months it runs",
      short["rows"][0]["campaign"] == 9000.0, short["rows"][0]["campaign"])
check("a term longer than the campaign is capped at it",
      _basis(termMonths=99)["rows"][0]["term_months"] == 6)
check("and a nonsense term falls back to the campaign length",
      _basis(termMonths="soon")["rows"][0]["campaign"] == 18000.0)

# The campaign total is the sum of the lines, not the monthly average times the
# term -- a one-time line and a line that stops early each break that shortcut.
mixed = builder.expected_results({"months": 6, "items": [
    dict(basis_base["items"][0]),
    {"category": "CREATIVE / DESIGN SERVICES", "product": "Standard Set of 6",
     "dollars": 250, "basis": "one_time"}]})
check("mixed bases total correctly",
      mixed["totals"]["campaign"] == 18000.0 + 250.0, mixed["totals"]["campaign"])

# ---------------------------------------------------------------------------
# Recommended budgets: what "more" looks like, at the foot of the proposal.
# The point of the section is that neither route invents a number -- raising a
# line quotes the Accelerated package already printed above it, and adding one
# quotes the rate card's own minimum. A recommended budget a rep cannot defend
# on a call is worse than not offering one.
grow_state = dict(
    quote_state, months=6,
    mkt={"retargeting": cm.NO, "aiOptimized": cm.NO, "paidSearch": cm.NO},
    items=[{"category": "OTT", "product": "Connected TV - Targeted",
            "rate": "CPM", "rateValue": 35.0, "dollars": 6000}],
    packages=[{"name": "Accelerated",
               "lines": [{"product": "Connected TV - Targeted", "dollars": 9000}]}])
grow = builder.growth_options(grow_state)
check("raising a line quotes the Accelerated package, not a new number",
      [(r["current"], r["suggested"]) for r in grow["increases"]] == [(6000.0, 9000.0)],
      grow["increases"])
check("and states the uplift over the whole flight",
      grow["increases"][0]["campaign"] == 3000.0 * 6, grow["increases"][0])
added = {r["product"]: r["suggested"] for r in grow["additions"]}
check("additions come from the discovery answers",
      "Website Retargeting" in added and "Pay Per Click" in added, sorted(added))
check("and are priced at the rate card's own minimum",
      added.get("Pay Per Click") == rc.minimum_for("Pay Per Click"), added)
check("nothing already on the plan is offered as an addition",
      not any("Connected TV" in p for p in added), sorted(added))

# A product with no card entry gets no price rather than a guess.
check("a product the card does not carry is offered to be quoted",
      all(r["quoted"] is False for r in grow["additions"] if not rc.find(r["product"])))

# The rep's edits win, and removals stick.
edited = builder.growth_options(dict(
    grow_state, growthEdits={"inc:Connected TV - Targeted": 7500}))
check("a rep can re-price a recommendation",
      edited["increases"][0]["suggested"] == 7500.0
      and edited["increases"][0]["uplift"] == 1500.0, edited["increases"][0])
dropped = builder.growth_options(dict(
    grow_state, growthDropped=["inc:Connected TV - Targeted"]))
check("and remove one entirely", dropped["increases"] == [], dropped["increases"])

check("a plan with nothing to add or raise says so rather than inventing one",
      builder.growth_options({"months": 3, "items": [], "packages": []})["any"] is False)

# Every product a suggestion names has to exist on the card, or it cannot be
# priced, added to a plan, or acted on at all. Three named products did not.
unpriceable = sorted({n for r in cm.SUGGESTION_RULES for n in r["products"]
                      if not rc.find(n)})
check("suggestion products use the card's own names",
      unpriceable == ["Call Tracking", "Smart 1 Suite"], unpriceable)

grow_quote = api("post", "/sales/builder/api/quotes", json={"data": grow_state})["quote"]
check("the quote carries the computed options", grow_quote["growth"]["any"] is True)
gpdf = http.get(f"/sales/builder/api/quotes/{grow_quote['id']}/pdf")
check("a proposal with recommended budgets renders its PDF",
      gpdf.status_code == 200 and gpdf.data[:4] == b"%PDF", gpdf.status_code)
gdocx = http.get(f"/sales/builder/api/quotes/{grow_quote['id']}/docx")
check("and its Word copy", gdocx.status_code == 200 and len(gdocx.data) > 2000)

# The ZIP list is the one section trafficking cannot launch without, and it is
# now generated at the back rather than typed into the middle of the audience
# section. Worth proving it survives into the documents that get sent.
zip_state = dict(quote_state, targetAreas=[
    {"name": "Carmel", "type": "City/ZIP + Radius", "origin": "Carmel, IN",
     "radius": 10, "zips": "46032, 46033, 46074"}])
zip_quote = api("post", "/sales/builder/api/quotes", json={"data": zip_state})["quote"]
zip_pdf = http.get(f"/sales/builder/api/quotes/{zip_quote['id']}/pdf")
check("a proposal with ZIP Codes still renders its PDF",
      zip_pdf.status_code == 200 and zip_pdf.data[:4] == b"%PDF", zip_pdf.status_code)
zip_docx = http.get(f"/sales/builder/api/quotes/{zip_quote['id']}/docx")
check("and its Word copy", zip_docx.status_code == 200 and len(zip_docx.data) > 2000)
check("the ZIP appendix is generated, never handed to the writer",
      "zips" not in [x["id"] for x in builder.writable_sections(zip_state)],
      [x["id"] for x in builder.writable_sections(zip_state)])

check("and which of those carry a generated table",
      any(s["has_table"] for s in served_plan["sections"])
      and any(not s["has_table"] for s in served_plan["sections"]))

pdf = http.get(f"/sales/builder/api/quotes/{quote['id']}/pdf")
check("the 13-part PDF builds", pdf.status_code == 200 and len(pdf.data) > 8000,
      f"{pdf.status_code} / {len(pdf.data)} bytes")
docx = http.get(f"/sales/builder/api/quotes/{quote['id']}/docx")
check("so does the Word copy", docx.status_code == 200 and len(docx.data) > 8000)

# A display plan is asked for banners now -- and an unanswered gate must
# still produce a proposal, because a rep who has not answered it yet still
# has to be able to read the document.
display_only = dict(quote_state)
display_only["items"] = [{"category": "DISPLAY", "product": "Advanced Audience",
                          "rate": "CPM", "rateValue": 5.5, "dollars": 3000}]
plain = api("post", "/sales/builder/api/quotes", json={"data": display_only})["quote"]
check("a display-only plan is asked for creative",
      plain["creative"]["ok"] is False
      and [r["medium"] for r in plain["creative"]["media"]] == ["display"],
      plain["creative"]["media"])
check("and the sizes it names are the IO's own spec kit",
      "300x250" in " ".join(
          sz for row in plain["creative"]["media"]
          for unit in (row.get("units") or {}).get("units", [])
          for sz in unit["sizes"]),
      plain["creative"]["media"][0].get("units"))
check("and still renders",
      http.get(f"/sales/builder/api/quotes/{plain['id']}/pdf").status_code == 200)

# ---------------------------------------------------------------------------
section("a table can be left out of the proposal, or edited")
# ---------------------------------------------------------------------------
# The standing rule is that a table is computed and the copy above it
# introduces one -- a proposal whose prose and figures disagree is the failure
# the whole specification is built around. What that rule never covered is a
# table that is right and still wrong for this client: a row naming a location
# under NDA, a KPI they asked us to drop. The alternative a rep actually has
# is exporting to Word, which takes the document out of the system entirely.
check("a section with no table is unaffected",
      builder.section_table({"kind": "text", "showTable": False})["show"] is True)
check("a generated table can be left out",
      builder.section_table({"kind": "roi", "showTable": False})["show"] is False)
check("an untouched table is still generated",
      builder.section_table({"kind": "roi"})["rows"] is None)
edited = builder.section_table({"kind": "roi", "tableEdit":
                                {"head": ["Product", "Monthly"],
                                 "rows": [["Connected TV", "$4,000"]]}})
check("an edited table replaces the generated one",
      edited["rows"] == [["Connected TV", "$4,000"]], edited)
check("and keeps its own headings", edited["head"] == ["Product", "Monthly"])

no_tables = json.loads(json.dumps(quote_state))
for sec in no_tables.get("sections") or []:
    sec["showTable"] = False
hidden = api("post", "/sales/builder/api/quotes", json={"data": no_tables})["quote"]
check("a proposal with every table excluded still renders",
      http.get(f"/sales/builder/api/quotes/{hidden['id']}/pdf").status_code == 200)
check("and so does its Word copy",
      http.get(f"/sales/builder/api/quotes/{hidden['id']}/docx").status_code == 200)

# ---------------------------------------------------------------------------
from hub import target_areas as ta                                  # noqa: E402
section("what a goal leads with, and what a client reads it as")
# ---------------------------------------------------------------------------
# `findProduct(category)` used to mean "the first row the card happens to list
# under that heading", and the card's order is the order somebody typed it in.
# So Run of Network -- $3.50 CPM of untargeted inventory, a volume top-up to a
# targeted buy -- was the display product every awareness and traffic goal
# opened with, on a document arguing that Smart 1 targets precisely.
check("run of network is never what a goal picks",
      rc.is_add_on("RON (Run of Network)")
      and rc.is_add_on("Programmatic - RON (Run of Network)"))
check("a display goal leads with programmatic",
      rc.default_product("DISPLAY")["product"] == "Select Tactics - Comes with Retargeting",
      rc.default_product("DISPLAY"))
check("which is the data-targeted category, not a display row",
      rc.default_product("DISPLAY")["category"] == "DATA TARGETED DISPLAY")
check("and RON is still reachable by name",
      any(p["product"] == "RON (Run of Network)" for p in rc.products()))
check("a category with no add-ons is unaffected",
      rc.default_product("OTT")["category"] == "OTT")

# Four categories carry a product literally called "Demographic" or
# "Behavioral". A quote line reading "Demographic" cannot tell a client which
# of the four they bought, and neither can the IO.
check("location lookback is named as location lookback",
      rc.quote_label("Demographic", "LOCATION LOOKBACK")
      == "Location Lookback — Demographic", rc.quote_label("Demographic", "LOCATION LOOKBACK"))
check("and so is the display one, differently",
      rc.quote_label("Demographic", "DISPLAY") == "Display — Demographic")
check("a product that already says what it is keeps its own name",
      rc.quote_label("Connected TV - Targeted", "OTT") == "Connected TV - Targeted")
check("and a product with no category is left alone",
      rc.quote_label("Website Retargeting", "") == "Website Retargeting")

# Social Ads is video inventory -- Facebook and Instagram video, LinkedIn,
# TikTok. The heading said "Social Ads", which reads as the whole of paid
# social and is the Meta category next to it.
check("the social video category says it is video",
      "SOCIAL ADS - VIDEO" in rc.categories(), rc.categories())
check("and the old heading is gone", "SOCIAL ADS" not in rc.categories())
io_card = open(os.path.join(ROOT, "modules", "io_builder", "templates",
                            "index.html"), encoding="utf-8").read()
check("the insertion order renames it the same way",
      '"VIDEO":"SOCIAL ADS - VIDEO"' in io_card)
wiz_card = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                             "index.html"), encoding="utf-8").read()
check("and so does the wizard",
      '"VIDEO":"SOCIAL ADS - VIDEO"' in wiz_card)

# The card is the buy side. Every CPM and CPV line is quoted at a multiple of
# it; a management fee has nothing to multiply.
check("a CPM line starts at twice the card", rc.sell_rate(4.25, "CPM") == 8.50)
check("so does a CPV line", rc.sell_rate(0.20, "CPV") == 0.40)
check("a management fee is not marked up", rc.sell_rate(0.15, None) is None)
check("and neither is a flat monthly", rc.sell_rate(199, None) is None)
check("the multiplier is one number both sides read",
      rc.rate_rules_for_js()["sellMultiplier"] == rc.SELL_MULTIPLIER)

# SEO now sells AI-answer optimisation, and the client reads the product
# description rather than the folklore.
seo = next(p for p in rc.products() if p["product"] == "Search Engine Optimization")
check("SEO says it covers AI answer engines",
      "AI Overviews" in seo["description"] and "ChatGPT" in seo["description"],
      seo["description"])

# ---------------------------------------------------------------------------
section("a ZIP exception is a rule, not a note")
# ---------------------------------------------------------------------------
# A radius does not stop at a state line and a campaign frequently does. The
# restriction used to live in an email, and the only two outcomes were a rep
# deleting a hundred ZIPs by hand or the list shipping as it came back.
rule = ta.parse_zip_rule("only New Jersey zip codes")
check("the way somebody says it is understood",
      rule["understood"] and rule["mode"] == "only" and rule["states"] == ["NJ"], rule)
check("a bare state name means only", ta.parse_zip_rule("New Jersey")["mode"] == "only")
check("exclusions are read as exclusions",
      ta.parse_zip_rule("everything except Ohio")["mode"] == "exclude")
check("named ZIPs work as well as states",
      ta.parse_zip_rule("exclude 46032, 46033")["zips"] == ["46032", "46033"])
# The half that matters most: a rule nobody could read must never read as
# applied. A restriction that reads as saved and does nothing is worse than
# one nobody typed.
unread = ta.parse_zip_rule("whatever john said on the call")
check("a rule we cannot read is not applied", unread["understood"] is False)
check("and says so in words", "not been applied" in unread["note"], unread["note"])
check("and the ZIP list is left whole",
      ta.apply_zip_rule("07001, 19103", unread)["kept"] == ["07001", "19103"])

applied = ta.apply_zip_rule("07001, 07002, 19103, 46032, 08540", rule)
check("only the ZIPs in that state run", applied["kept"] == ["07001", "07002", "08540"])
check("and what was dropped is counted, not discarded",
      applied["dropped"] == ["19103", "46032"], applied["dropped"])
check("the note says how many", "2 of 5" in applied["note"], applied["note"])
empty = ta.apply_zip_rule("46032, 46033", rule)
check("a rule that leaves nothing says so rather than failing open",
      empty["kept"] == [] and "Nothing is left" in empty["note"], empty)

# Everything downstream has to read the running list, or the document a client
# signed and the campaign that was trafficked disagree while both look right.
area = {"name": "Philly", "origin": "Philadelphia, PA", "radius": 25,
        "zips": "07001, 19103, 08540",
        "zipException": "only New Jersey zip codes"}
check("the IO's ZIP field is the running list",
      ta.all_zips([area]) == ["07001", "08540"], ta.all_zips([area]))
check("and the one geo string carries the exception",
      "Exception: only New Jersey zip codes" in ta.to_legacy_geo([area]),
      ta.to_legacy_geo([area]))
check("an exception nobody could read is named on that string too",
      "NOT applied" in ta.to_legacy_geo([dict(area, zipException="mumble")]))
check("the exception survives a normalize",
      ta.normalize_area(area)["zipException"] == "only New Jersey zip codes")
check("and is re-parsed on read rather than trusted from the record",
      ta.normalize_area(dict(area, zipRule={"mode": "exclude"}))["zipRule"]["mode"] == "only")
check("an area carrying only an exception still reads as blank",
      ta.is_empty(ta.normalize_area({"zipException": "only NJ"})) is True)
check("the exception list names the ones that did not apply",
      [r["applied"] for r in ta.zip_exceptions([area, dict(area, zipException="mumble")])]
      == [True, False])

# ---------------------------------------------------------------------------
section("competitors are named, and who built the proposal is recorded")
# ---------------------------------------------------------------------------
# The audience step offered "Competitor conquesting" as a tick box and nothing
# anywhere asked which competitors -- so the proposal promised to target a
# client's rivals without naming one, and whoever builds the geo-fence went
# back to the rep weeks later.
targets = builder.targets_of_interest({"targetsOfInterest": [
    {"name": "Riverside Dental", "address": "1200 Main St, Carmel IN",
     "note": "their implant patients"},
    {"name": "", "address": "ignored"},
    {"name": "Lucas Oil Stadium", "kind": "venue"}]})
check("a row nobody named is dropped", len(targets) == 2, targets)
check("an address makes a row fenceable", targets[0]["fenceable"] is True)
check("and one without an address is still kept",
      targets[1]["fenceable"] is False and targets[1]["name"] == "Lucas Oil Stadium")

with_targets = json.loads(json.dumps(quote_state))
with_targets["targetsOfInterest"] = [
    {"name": "Riverside Dental", "address": "1200 Main St", "note": "their implants"}]
tq = api("post", "/sales/builder/api/quotes", json={"data": with_targets})["quote"]
check("they reach the quote", tq["targets_of_interest"][0]["name"] == "Riverside Dental")
check("and the proposal renders with them",
      http.get(f"/sales/builder/api/quotes/{tq['id']}/pdf").status_code == 200)

# `salesperson` is the sales contact typed onto the proposal for the client's
# benefit -- blank on most drafts and sometimes somebody else's name. The list
# was showing that, so "who wrote this?" had no answer on the one screen the
# question is asked.
check("who built it is its own field, not the sales contact",
      "created_by" in tq and "salesperson" in tq)
check("and it is whoever the Hub session says is signed in",
      tq["created_by"] == "Harness", tq["created_by"])
# It is recorded once, at creation. A later edit by somebody else does not
# change who wrote it -- overwriting on every save would make the column read
# "last touched by" while the heading says Created by.
api("put", f"/sales/builder/api/quotes/{tq['id']}", json={"data": with_targets})
check("and a later save does not rewrite it",
      api("get", f"/sales/builder/api/quotes/{tq['id']}")["quote"]["created_by"]
      == "Harness")

# ---------------------------------------------------------------------------
section("one product, one name — on the card, in the proposal and in the IO")
# ---------------------------------------------------------------------------
# Two products were both called "Google Grant" (a $125 setup fee and a 15%
# monthly management fee) and two more "Local Service Ads (LSA)". Three things
# went wrong at once and none of them errored:
#
#   * find() returned whichever the card listed first, so a quote for
#     management billed the setup fee;
#   * the IO's productConfig is keyed on the label, so 90 card rows became 88
#     and neither setup fee could be put on an insertion order at all;
#   * check_drift() is keyed on the label too, so it could not have seen a
#     difference between the pair it was collapsing.
#
# The published rate card had the answer already — it names them "(Setup)" and
# "(Management)" — so both copies of the card were renamed to match it.
import re as _re                                                   # noqa: E402
import json as _json                                               # noqa: E402
from hub import product_intake                                     # noqa: E402

_labels = [p["label"] for p in rc.products()]
check("every product on the shared card has a label of its own",
      len(set(_labels)) == len(_labels),
      sorted({l for l in _labels if _labels.count(l) > 1}))

_io_src = open(os.path.join(ROOT, "modules", "io_builder", "templates",
                            "index.html"), encoding="utf-8").read()
_embedded = _json.loads(_re.search(r"const rateCard=(\[.*?\]);\n", _io_src, _re.S).group(1))
_io_labels = [p.get("label", "") for p in _embedded]
check("and so does every product the IO carries",
      len(set(_io_labels)) == len(_io_labels),
      sorted({l for l in _io_labels if _io_labels.count(l) > 1}))
check("so the IO's product list is the whole card, not what survived a collision",
      len(set(_io_labels)) == len(rc.products()),
      f"io={len(set(_io_labels))} card={len(rc.products())}")

check("the setup fee and the management fee are two quotable products",
      (rc.find("Google Grant (Setup)") or {}).get("listed_rate") == "$125 One time set up fee"
      and (rc.find("Google Grant (Management)") or {}).get("listed_rate") == "15% Mgmt fee monthly")
check("...and the same for Local Service Ads",
      (rc.find("Local Service Ads \u2014 LSA (Setup)") or {}).get("listed_rate")
      == "$125 One time set up fee")

# The refusal, which is the half that keeps a stored record honest. A quote
# saved before the rename says "Google Grant", and that name is now a question
# rather than an answer.
check("a name that could mean two products resolves to neither",
      rc.find("Google Grant") is None and rc.find("Behavioral") is None)
check("and the candidates are named instead, so it never reads as 'not on the card'",
      {c["product"] for c in rc.candidates("Google Grant")}
      == {"Google Grant (Setup)", "Google Grant (Management)"},
      [c["product"] for c in rc.candidates("Google Grant")])
check("the intake asks rather than guessing",
      product_intake.classify("Google Grant")["status"] == "near"
      and product_intake.classify("Google Grant")["product"] == "")

# A caller that knows the heading is answered rather than asked: four
# categories carry a product called "Behavioral" and they are different rates.
check("a category resolves a name that cannot resolve alone",
      (rc.find("Behavioral", "MOBILE ONLY") or {}).get("listed_rate") == "$4.00 / CPM"
      and (rc.find("Behavioral", "LOCATION LOOKBACK") or {}).get("listed_rate") == "$7.50 / CPM")
check("and the intake matches on it",
      product_intake.classify("Behavioral", "MOBILE ONLY")["category"] == "MOBILE ONLY")

# The unambiguous anchored match this fallback was written for still works.
check("a short name that can only mean one product still resolves",
      (rc.find("Connected TV - Targeted") or {}).get("category") == "OTT")

_drift = rc.check_drift()
check("the drift check reports duplicate labels rather than collapsing them",
      "duplicate_labels" in _drift and not _drift["duplicate_labels"], _drift)
check("and the two copies of the card agree", _drift["in_sync"], _drift.get("note"))

# The published rate card ships in this repo, so the naming can be held to it.
_page = open(os.path.join(ROOT, "hub", "partner_pages",
                          "rate-card-universal.html"), encoding="utf-8").read()
_i = _page.index("const DATA = ")
_data = _json.loads(_page[_i + len("const DATA = "):_page.index("\n", _i)].rstrip().rstrip(";"))
_page_names = {it["p"] for s_ in _data for g in s_["groups"] for it in g["items"]}
for _n in ("Google Grant (Setup)", "Google Grant (Management)"):
    check(f"the card we publish and the card we quote from agree on {_n!r}",
          _n in _page_names and rc.find(_n) is not None)

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
