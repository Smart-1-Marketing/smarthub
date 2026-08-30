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
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
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
    # The card files IP targeting under a heading called "Display & Video",
    # and that heading rides in every row's `label`. Read as words about the
    # product it made all three display products video, which asks a client
    # for a TV spot to run a banner buy.
    ("IP TARGETS", "IP Targeted Display - New Movers", cn.DISPLAY),
    ("IP TARGETS", "IP Targeted Display - Venue Replay", cn.DISPLAY),
    # These three answered OTHER, and OTHER is not a medium -- it is the
    # Creative step never mentioning the line at all.
    ("MOBILE ONLY", "RON (Run of Network)", cn.DISPLAY),
    ("MOBILE ONLY", "In-Store Visits", cn.DISPLAY),
    ("EMAIL MARKETING", "List Provided Email", cn.EMAIL),
    ("SMART 1 SIGNAGE", "Digital Outdoor & Indoor Signage", cn.DOOH),
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

# Every product on the real card, not a fixture. A hand-written list proves
# the halves agree about the rows somebody thought to write down, which is
# exactly the set that was already right: the four the label bleed broke were
# not in it. This is also self-maintaining -- a product added to the card is
# covered without anybody remembering to add it here.
from hub import rate_card as _rc_for_media                         # noqa: E402

_ALL = [{"category": p["category"], "product": p["product"],
         "description": p["description"], "label": p["label"]}
        for p in _rc_for_media.products()] \
    + [{"category": c, "product": p} for c, p, _ in CASES]
harness = (extract + "\nconst C=" + json.dumps(_ALL) + ";\n"
           "console.log(JSON.stringify(C.map(mediumOf)));\n")
js_path = os.path.join(_TMP, "medium.js")
open(js_path, "w", encoding="utf-8").write(harness)
try:
    out = subprocess.run(["node", js_path], capture_output=True, text=True,
                         timeout=30, check=True).stdout
    js_media = json.loads(out)
    py_media = [cn.medium_of(row) for row in _ALL]
    check("the wizard classifies every rate-card product exactly as the server does",
          js_media == py_media,
          [f"{r.get('category')}/{r.get('product')}: js={j} py={y}"
           for r, j, y in zip(_ALL, js_media, py_media) if j != y][:8])
except FileNotFoundError:
    print("  skip node is not installed — wizard/server agreement unchecked")
except subprocess.CalledProcessError as exc:
    check("the wizard's creative helpers run", False, exc.stderr[:300])

# ---------------------------------------------------------------------------
section("a paid social buy is asked whether the creative exists")
# ---------------------------------------------------------------------------
# The largest of the three holes and the last found. A Meta-only plan returned
# *nothing* from gated_media(): six real buys -- Awareness, Targeted,
# Programmatic Paid Social, Retargeting, Leads and Boosted Posts -- each with
# three to seven units published in the kit, and the Creative step mentioned
# none of them. The tempting reading is that paid social is usually a boosted
# post the client already has, which is precisely the assumption this module
# exists to stop making.
from hub import creative_specs as cs                               # noqa: E402

_meta = {"items": [{"product": "Facebook | Instagram - Awareness Paid Social Media Advertising",
                    "category": "META", "dollars": 3000}], "months": 3}
check("a Meta-only plan is asked for creative at all",
      cn.gated_media(_meta) == [cn.SOCIAL], cn.gated_media(_meta))
check("and the kit has units to ask for",
      len(cs.units_for_product("Facebook | Instagram - Awareness Paid Social Media Advertising",
                               "META")) > 0)
check("social production is the card's own $35 per platform, not a number invented here",
      cn.TYPICAL_PRODUCTION[cn.SOCIAL] == 35, cn.TYPICAL_PRODUCTION.get(cn.SOCIAL))

# Every Meta product on this card is named "Facebook | Instagram ...", and the
# `instagram` rule -- written for a product named only Instagram, and returning
# a deliberately narrower list -- sits above the Facebook one. So five of the
# seven Meta buys were asked for an Instagram image and a Story and never for
# the Facebook feed, the Facebook video or the carousel. The two named
# "Facebook - ..." got the full set the whole time, which is why it read as
# working.
_meta_rows = [p for p in _rc_for_media.products()
              if "facebook" in p["product"].lower()
              and "instagram" in p["product"].lower()]
check("the card really does name both platforms in one product",
      len(_meta_rows) >= 4, len(_meta_rows))
for _row in _meta_rows:
    _ch = cs.channels_for_product(_row["product"], _row["category"])
    check(f"{_row['product'][:34]!r} is asked for the whole Meta set",
          set(_ch) >= {"facebook", "facebook_video", "facebook_carousel",
                       "instagram", "stories"}, _ch)

# ...and the narrow answer is still there for a product that names one of them.
check("an Instagram-only buy is not asked for the Facebook feed",
      cs.channels_for_product("Instagram - Paid Social Media Advertising", "") ==
      ["instagram", "stories"],
      cs.channels_for_product("Instagram - Paid Social Media Advertising", ""))
check("and the Meta list is written once, so the two rules cannot drift",
      cs.channels_for_product("Facebook - Boosted Posts", "META") is
      cs.channels_for_product("Facebook | Instagram - Awareness Paid Social Media Advertising",
                              "META"))

# An image unit with no size of its own was folded into the run of sizes,
# where it contributed nothing and vanished. Every social unit is in that
# position, so a paid social buy's whole requirement read "Stories Video
# (MP4/MOV, 0-120s)" -- four image units silently absent from the one line a
# rep and the client document read.
_meta_state = {"items": [dict(_meta_rows[0], dollars=3000)], "months": 3}
_meta_line = cn.units_line(_meta_state, cn.medium_of(_meta_rows[0]))
for _want in ("Facebook Display", "Instagram Display", "Carousel Image",
              "Stories Display"):
    check(f"a paid social requirement names {_want}",
          _want in _meta_line, _meta_line)

# "plus a companion banner" is a claim about digital radio's optional 300x250
# and was fired on a count -- one sized image plus anything described -- so
# Snapchat's Single Image Ad was announced to the client as an optional
# companion to the video.
_snap = next(p for p in _rc_for_media.products() if "Snapchat" in p["product"])
_snap_line = cn.units_line({"items": [dict(_snap, dollars=3000)], "months": 3},
                           cn.medium_of(_snap))
check("a Snapchat image is not called a companion banner",
      "companion banner" not in _snap_line, _snap_line)

# ...and the unit the phrase was written for still carries it.
_radio_row = next(p for p in _rc_for_media.products()
                  if p["category"] == "DIGITAL RADIO"
                  and p["product"].lower().startswith("programmatic - targeted"))
_radio_line = cn.units_line({"items": [dict(_radio_row, dollars=3000)], "months": 3},
                            cn.medium_of(_radio_row))
check("but digital radio's optional 300x250 still is one",
      "plus a companion banner: 300x250" in _radio_line, _radio_line)
check("and the spot is still named first, not the banner",
      _radio_line.index("Audio Spot") < _radio_line.index("companion"), _radio_line)

# The card files LinkedIn's display-and-text product under a heading called
# SOCIAL ADS - VIDEO, and the heading is what the keyword pass reads -- so a
# product whose own name says "Display & Text Ads" was asked for a video spot.
_linkedin = next(p for p in _rc_for_media.products() if "LinkedIn" in p["product"])
check("a LinkedIn display-and-text buy is not asked for a video spot",
      cn.medium_of(_linkedin) != cn.VIDEO, cn.medium_of(_linkedin))
check("and what it is asked for is LinkedIn's own units",
      any("Sponsored Content" in (u.get("name") or "")
          for u in cs.units_for_product(_linkedin["product"], _linkedin["category"])),
      [u.get("name") for u in cs.units_for_product(_linkedin["product"], _linkedin["category"])][:3])

# The rest of that heading is left alone deliberately: the heading is right
# about them, and reclassifying a generic "Paid Social Media Advertising" on
# our own reading of which platforms are video-first would be inventing.
check("the genuinely video social products still ask for a spot",
      cn.medium_of({"category": "SOCIAL ADS - VIDEO",
                    "product": "Tik Tok - Paid Social Media Advertising"}) == cn.VIDEO)

# Gating paid social made the word "social" decisive in the keyword pass, and
# two headings that are not media buys were caught by it: the card's own $35
# social ad *production* line, asked whether the client already had the
# creative it exists to produce, and a $199/month organic posting retainer
# that buys no advertising at all. Both then printed "the spec kit maps no
# unit for this" onto the client's creative section, and both counted their
# spend into the social medium, which is what decides whether a comped line
# is questioned.
for _n in ("Social Media Ad Creation per platform", "Social Media Management"):
    _row = next(p for p in _rc_for_media.products() if p["product"] == _n)
    check(f"{_n!r} is not gated as a media buy",
          cn.medium_of(_row) == cn.OTHER, cn.medium_of(_row))
    check(f"...so a plan of only {_n[:24]!r} asks for no creative",
          cn.gated_media({"items": [dict(_row, dollars=1500)], "months": 3}) == [],
          cn.gated_media({"items": [dict(_row, dollars=1500)], "months": 3}))

# It is the *heading* that is named, not those two products: the other four
# lines under CREATIVE / DESIGN SERVICES answer OTHER only because they happen
# to contain no medium keyword, and the next production line added there must
# not depend on that luck.
for _row in _rc_for_media.products():
    if _row["category"] == "CREATIVE / DESIGN SERVICES":
        check(f"no production line is gated: {_row['product'][:34]!r}",
              cn.medium_of(_row) == cn.OTHER, cn.medium_of(_row))

# ...and a real paid social buy is still asked, or this has undone the gate.
_meta_buy = next(p for p in _rc_for_media.products()
                 if p["product"].startswith("Facebook | Instagram - Awareness"))
check("a real Meta buy is still asked whether the creative exists",
      cn.gated_media({"items": [dict(_meta_buy, dollars=3000)], "months": 3})
      == [cn.SOCIAL])

# The spend a comp confirmation is measured against is the media, not the
# production line sitting beside it on the same plan.
_mixed = {"items": [dict(_meta_buy, dollars=3000),
                    dict(next(p for p in _rc_for_media.products()
                              if p["product"] == "Social Media Ad Creation per platform"),
                         dollars=35)],
          "months": 3}
check("a production line does not inflate its own medium's spend",
      cn.medium_spend(_mixed, cn.SOCIAL) == 9000.0,
      cn.medium_spend(_mixed, cn.SOCIAL))

# Pinterest is on the card and is in no part of the kit, so the category was
# answering for it: "SOCIAL ADS - VIDEO" matched the `social ads?` pattern and
# a Pinterest buy was asked for Facebook and Instagram units -- 1:1 feed
# squares and 9:16 stories against a platform whose feed is 2:3. A client who
# supplied exactly what was asked for delivers creative Pinterest crops, with
# every screen reading as correct while it happens.
_pin = next(p for p in _rc_for_media.products() if "Pinterest" in p["product"])
check("a Pinterest buy is still asked whether the creative exists",
      cn.medium_of(_pin) in cn.GATED, cn.medium_of(_pin))
check("but it is not judged against Meta's units",
      cs.channels_for_product(_pin["product"], _pin["category"]) == [],
      cs.channels_for_product(_pin["product"], _pin["category"]))
_pin_req = cn.required_units({"items": [dict(_pin, dollars=2000)], "months": 3},
                             cn.medium_of(_pin))
check("and the screen says the kit maps no unit for it, rather than a size",
      _pin_req["measured"] is False and "Pinterest" in _pin_req["note"],
      _pin_req["note"])
check("which is a statement about the kit: it publishes no Pinterest section",
      not any("pinterest" in (u.get("channel") or "").lower() for u in cs.UNITS))

# ...and the entry above the Meta rule must stay about Pinterest alone. The
# other four platforms on that heading each have units of their own, and
# widening one pattern to cover "the social ones" would take those away.
for _plat, _chan in (("Snapchat", "snapchat"), ("Tik Tok", "tiktok"),
                     ("Twitter", "x")):
    _row = next(p for p in _rc_for_media.products() if _plat in p["product"])
    check(f"{_plat} still reaches its own units",
          cs.channels_for_product(_row["product"], _row["category"]) == [_chan],
          cs.channels_for_product(_row["product"], _row["category"]))

# ---------------------------------------------------------------------------
section("LinkedIn is judged at the weights the kit publishes")
# ---------------------------------------------------------------------------
# The 2025 model named five formats to the kit's eleven, and three of its
# numbers refused files the kit allows -- the Half Page failure this file
# already records, one channel over.
_li = {u["id"]: u for u in cs.UNITS if u["channel"] == "linkedin"}
check("LinkedIn carries the eleven formats the kit publishes", len(_li) == 11,
      sorted(u["name"] for u in _li.values()))
check("and 'Sponsored InMail' is called what LinkedIn calls it",
      _li["li_inmail"]["name"] == "Message Ads", _li["li_inmail"]["name"])
check("...keeping its id, because tags_for() has written it onto creative",
      "li_inmail" in cs.BY_ID)

_MB = 1024 * 1024
check("a 1.5 MB Message Ads banner is accepted, against a published 2 MB",
      cs.check(unit_id="li_inmail", width=300, height=250,
               size_bytes=int(1.5 * _MB), fmt="jpg")["result"] == "pass",
      cs.check(unit_id="li_inmail", width=300, height=250,
               size_bytes=int(1.5 * _MB), fmt="jpg")["result"])
check("a 300 MB video is accepted, against a published 500 MB",
      cs.check(unit_id="li_video", width=1920, height=1080,
               size_bytes=300 * _MB, fmt="mp4")["result"] == "pass")
check("and 1200x628 — the kit's own size — is accepted",
      cs.check(unit_id="li_single_image", width=1200, height=628,
               size_bytes=2 * _MB, fmt="jpg")["result"] == "pass")

# A real file still lands on a sensible unit; the two formats that carry no
# file of their own must never be what one is judged against.
for _w, _h, _fmt, _want in ((1200, 628, "jpg", "Sponsored Content — Single Image"),
                            (1080, 1080, "jpg", "Sponsored Content — Carousel"),
                            (1920, 1080, "mp4", "Sponsored Content — Video"),
                            (100, 100, "png", "Text Ad")):
    _v = cs.check(product="LinkedIn - Display & Text Ads - Budget Based - "
                          "No Impression Guarantee",
                  category="SOCIAL ADS - VIDEO", width=_w, height=_h,
                  size_bytes=2 * _MB, fmt=_fmt)
    check(f"a {_w}x{_h} {_fmt} is judged as {_want}",
          (_v.get("unit") or {}).get("name") == _want, _v.get("unit"))


# ---------------------------------------------------------------------------
section("TikTok is judged at the lengths and shapes the kit publishes")
# ---------------------------------------------------------------------------
# The 2025 model named three formats to the kit's six, and none of the three
# was a format TikTok sells. Two of them refused creative the kit allows: the
# in-feed video capped at :60 against a published 10 minutes, and the image ad
# pinned to 1200x628 when the kit specs images by ratio.
_tt = {u["id"]: u for u in cs.UNITS if u["channel"] == "tiktok"}
check("TikTok carries the six formats the kit publishes", len(_tt) == 6,
      sorted(u["name"] for u in _tt.values()))
check("and the in-feed video is called Auction In-Feed",
      _tt["tiktok_video"]["name"] == "Auction In-Feed",
      _tt["tiktok_video"]["name"])
check("...keeping its id, because tags_for() has written it onto creative",
      "tiktok_video" in cs.BY_ID)

check("a 90-second in-feed spot is accepted, against a published 10 minutes",
      cs.check(unit_id="tiktok_video", width=1080, height=1920, fmt="mp4",
               size_bytes=80 * _MB, duration=90)["result"] == "pass",
      cs.check(unit_id="tiktok_video", width=1080, height=1920, fmt="mp4",
               size_bytes=80 * _MB, duration=90)["summary"])
check("...and an .avi, one of the five file types the kit takes",
      cs.check(unit_id="tiktok_video", width=1080, height=1920, fmt="avi",
               size_bytes=80 * _MB, duration=30)["result"] == "pass")
check("a 720x1280 vertical image is accepted — the shape TikTok recommends",
      cs.check(unit_id="tiktok_gab_image", width=720, height=1280, fmt="jpg",
               size_bytes=2 * _MB)["result"] == "pass")

# The ceilings that are real still refuse, or raising the others would have
# been a loosening rather than a correction.
check("but 11 minutes is still refused",
      cs.check(unit_id="tiktok_video", width=1080, height=1920, fmt="mp4",
               size_bytes=80 * _MB, duration=700)["result"] == "fail")
check("and a 400px-wide spot is under every published minimum",
      cs.check(unit_id="tiktok_video", width=400, height=711, fmt="mp4",
               size_bytes=8 * _MB, duration=20)["result"] == "fail")

# The two formats the kit no longer sells are retired rather than re-pointed:
# out of UNITS so nothing asks a client for one, still in BY_ID so a row
# carrying the tag resolves to a unit that says what replaced it.
for _gone in ("tiktok_image", "tiktok_profile"):
    check(f"{_gone!r} is retired rather than asked for",
          _gone not in _tt and _gone in cs.BY_ID
          and cs.BY_ID[_gone].get("retired"),
          cs.BY_ID.get(_gone, {}).get("retired"))

# Every one of the six reaches the requirement line. An image unit with no
# size of its own has to be named rather than folded into the run of sizes --
# folded in it contributes nothing and vanishes, which is this function's own
# recorded failure.
_tt_line = cn.units_line(
    {"items": [{"product": "Tik Tok - Paid Social Media Advertising",
                "category": "SOCIAL ADS - VIDEO", "dollars": 3000}]}, "video")
for _name in (u["name"] for u in _tt.values()):
    if _name == "Carousel Ads":
        check("the carousel reaches the line as its three sizes",
              "1200x628" in _tt_line and "720x1280" in _tt_line, _tt_line)
    else:
        check(f"{_name!r} is named on the requirement line",
              _name in _tt_line, _tt_line)


# ---------------------------------------------------------------------------
section("Snapchat is judged at the lengths and shapes the kit publishes")
# ---------------------------------------------------------------------------
# The 2025 model named two formats to the kit's seven, and both of the two
# refused creative the kit allows: video capped at :30 against a published
# :03 to 3:00, and both pinned to a fixed 1080x1920 when the kit names that as
# what to build at and 720x1280 as the minimum.
_sn = {u["id"]: u for u in cs.UNITS if u["channel"] == "snapchat"}
check("Snapchat carries the seven formats the kit publishes", len(_sn) == 7,
      sorted(u["name"] for u in _sn.values()))
check("and the two are named the way the kit names them",
      _sn["snap_image"]["name"] == "Single Image Ads"
      and _sn["snap_video"]["name"] == "Video Ads",
      [_sn["snap_image"]["name"], _sn["snap_video"]["name"]])
check("...keeping their ids, because tags_for() has written them onto creative",
      "snap_image" in cs.BY_ID and "snap_video" in cs.BY_ID)

check("a :45 spot is accepted — the :30 cap is gone",
      cs.check(unit_id="snap_video", width=1080, height=1920, fmt="mp4",
               size_bytes=200 * _MB, duration=45)["result"] == "pass",
      cs.check(unit_id="snap_video", width=1080, height=1920, fmt="mp4",
               size_bytes=200 * _MB, duration=45)["summary"])
check("and 2:30, up to the published 3:00",
      cs.check(unit_id="snap_video", width=1080, height=1920, fmt="mp4",
               size_bytes=200 * _MB, duration=150)["result"] == "pass")
check("but 3:30 is still outside it",
      cs.check(unit_id="snap_video", width=1080, height=1920, fmt="mp4",
               size_bytes=200 * _MB, duration=210)["result"] == "fail")

# Three numbers, not one: 9:16 is required and fails, 1080x1920 is what to
# build at and warns, 720x1280 is the floor and fails under it. Collapsing
# them into a fixed size is what refused a legal file.
_at_min = cs.check(unit_id="snap_image", width=720, height=1280, fmt="jpg",
                   size_bytes=2 * _MB)
check("720x1280 — the stated minimum — is not refused",
      _at_min["result"] == "warn", _at_min["summary"])
check("...and says it is under what the kit says to build at",
      "1080x1920" in _at_min["summary"], _at_min["summary"])
check("under the 720px floor is a failure, not a warning",
      cs.check(unit_id="snap_image", width=640, height=1138, fmt="jpg",
               size_bytes=2 * _MB)["result"] == "fail")
check("and a square still is refused on the ratio",
      cs.check(unit_id="snap_image", width=1080, height=1080, fmt="jpg",
               size_bytes=2 * _MB)["result"] == "fail")
check("the 5 MB ceiling on a still still bites",
      cs.check(unit_id="snap_image", width=1080, height=1920, fmt="jpg",
               size_bytes=12 * _MB)["result"] == "fail")

# One row on the kit, two shapes. It stays one unit because "AR Filters" is
# what Snapchat sells; two units would be two names the kit does not publish.
for _w, _h, _f in ((945, 2048, "png"), (720, 1560, "gif")):
    check(f"an AR filter at {_w}x{_h} {_f} is accepted",
          cs.check(unit_id="snap_ar_filter", width=_w, height=_h,
                   fmt=_f)["result"] == "pass")
check("and no file-weight ceiling is invented for it",
      "max_bytes" not in _sn["snap_ar_filter"])

# ---------------------------------------------------------------------------
section("a unit specified by ratio still says what it is on the line")
# ---------------------------------------------------------------------------
# An image unit with no fixed size used to reach the requirement line as a
# bare name -- "or Single Image Ads", with nothing saying 9:16, 1080x1920 or
# JPG. That is the same silence as vanishing, one step less complete, and it
# is the line a client reads.
_sn_line = cn.units_line(
    {"items": [{"product": "Snapchat - Paid Social Media Advertising",
                "category": "SOCIAL ADS - VIDEO", "dollars": 3000}]}, "video")
# Every unit but the AR filter, which is announced by the words below rather
# than by its unit name — the shape the radio companion already had.
for _name in (u["name"] for u in _sn.values() if u["id"] != "snap_ar_filter"):
    check(f"{_name!r} is named on the requirement line",
          _name in _sn_line, _sn_line)
check("and a ratio-specified unit carries its shape and its formats",
      "Single Image Ads (9:16, build at 1080x1920, JPG" in _sn_line, _sn_line)

# An optional extra with a size of its own must not lead. Named first it
# reads as the whole requirement -- how somebody sends a banner and no audio.
check("the AR filter is named after the ask, not before it",
      _sn_line.index("Video Ads") < _sn_line.index("945x2048")
      and "plus an AR filter" in _sn_line, _sn_line)
check("and the radio companion still says it is a companion banner",
      "plus a companion banner: 300x250" in cn.units_line(
          {"items": [{"product": "Digital Radio - Targeted",
                      "category": "DIGITAL RADIO", "dollars": 3000}]}, "audio"))
check("each addition names itself rather than sharing one sentence",
      set(cn.ADDITIONS) == {"radio_companion", "snap_ar_filter"}
      and len(set(cn.ADDITIONS.values())) == 2, cn.ADDITIONS)


# ---------------------------------------------------------------------------
section("the gate and the spec kit read the same product the same way")
# ---------------------------------------------------------------------------
# Two readings of one question -- whether to ask for creative, and what to ask
# for -- disagreed on 25 of 90 products, in both directions, and silently:
# each screen was internally consistent, so the rep was asked for one thing
# and judged against another.
_dis = cn.spec_disagreements()
check("the creative gate and the spec kit agree on every product",
      _dis == [], [f"{d['category']}/{d['product'][:30]}: gate={d['gate']} kit={d['kit']}"
                   for d in _dis][:8])

# ...and the check can go red, or it is furniture. Read OTT as audio and the
# two Connected TV products must be named.
_saved_cat = dict(cn.CATEGORY_MEDIUM)
try:
    cn.CATEGORY_MEDIUM["ott"] = cn.AUDIO
    _bit = cn.spec_disagreements()
finally:
    cn.CATEGORY_MEDIUM.clear()
    cn.CATEGORY_MEDIUM.update(_saved_cat)
check("and a wrong reading is reported rather than passing quietly",
      any("Connected TV" in d["product"] for d in _bit), _bit[:3])
check("with both sides of the disagreement named, not just the count",
      bool(_bit) and {"gate", "kit"} <= set(_bit[0]), _bit[:1])

# The four products whose names identify nothing must reach a *video* unit.
# They are named in EXPLICIT_MEDIUM so the gate asks for a spot; the kit has
# to agree, or the rep is asked for a spot and handed a list of banner sizes.
# ...the ones mapped to video, that is. EXPLICIT_MEDIUM also carries the
# entry running the other way -- a LinkedIn "Display & Text Ads" product the
# card files under a heading called SOCIAL ADS - VIDEO -- and asserting that
# one reaches a video unit would be asserting the bug.
for _name, _want in cn.EXPLICIT_MEDIUM.items():
    if _want != cn.VIDEO:
        continue
    _row = next((p for p in _rc_for_media.products()
                 if p["product"].lower() == _name), None)
    if not _row:
        continue
    _units = cs.units_for_product(_row["product"], _row["category"])
    check(f"{_row['product'][:34]!r} is asked for video, not banners",
          bool(_units) and all(u.get("kind") == "video" for u in _units),
          [u.get("name") for u in _units][:4])

# The same product name under DIGITAL RADIO is the $18 CPM audio buy, and the
# rule above must not have reached it.
_radio = next((p for p in _rc_for_media.products()
               if p["product"].lower().startswith("programmatic - targeted")
               and p["category"] == "DIGITAL RADIO"), None)
if _radio:
    check("but its DIGITAL RADIO twin still asks for a spot",
          cs.channels_for_product(_radio["product"], _radio["category"]) == ["digital_radio"],
          cs.channels_for_product(_radio["product"], _radio["category"]))

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

# A green check that cannot go red is not a check. Hand it a template that
# plainly carries the collision and require it to say so -- this started life
# green, which is the only way it was worth adding.
_dupe_io = os.path.join(_TMP, "dupe_io.html")
_dupe_rows = [dict(r) for r in _embedded[:2]]
_dupe_rows[1]["label"] = _dupe_rows[0]["label"]
open(_dupe_io, "w", encoding="utf-8").write(
    "const rateCard=" + _json.dumps(_dupe_rows) + ";\n")
_bit = rc.check_drift(_dupe_io)
check("a template that carries a collision is reported, not collapsed",
      _bit["duplicate_labels"] and not _bit["in_sync"], _bit)
check("and the note says what a duplicate label costs the IO",
      "dropped" in (_bit.get("note") or ""), _bit.get("note"))

# The published rate card ships in this repo, so the naming can be held to it.
_page = open(os.path.join(ROOT, "hub", "partner_pages",
                          "rate-card-universal.html"), encoding="utf-8").read()
_i = _page.index("const DATA = ")
_data = _json.loads(_page[_i + len("const DATA = "):_page.index("\n", _i)].rstrip().rstrip(";"))
_page_names = {it["p"] for s_ in _data for g in s_["groups"] for it in g["items"]}
for _n in ("Google Grant (Setup)", "Google Grant (Management)"):
    check(f"the card we publish and the card we quote from agree on {_n!r}",
          _n in _page_names and rc.find(_n) is not None)

# ...and the two agree about how a product is *sold*, not only what it is
# called. Four IP Targeting products carried a bare `listedRate` of "25.0"
# with `rateType: null`, while the page we publish sells every one of them per
# CPM. Nothing errored at either end and every screen was internally
# consistent, which is why it stood: `sell_rate()` returns None for a line
# with no rate type, so the buy-side rate went onto the proposal with no
# margin on it, beside a display line correctly doubling $4.25 to $8.50; and
# `estimate_delivery()` answered "not an impression-based rate" about a $25
# CPM buy, so the media plan quoted IP Targeting with no impressions and
# printed the bare float at the client.
import unicodedata as _ud                                          # noqa: E402


def _card_norm(x):
    x = _ud.normalize("NFKD", str(x or "")).replace("\u2014", "-").replace("\u2013", "-")
    return " ".join(_re.sub(r"[^a-z0-9]+", " ", x.lower()).split())


_page_rates = {}
for _s in _data:
    for _g in _s["groups"]:
        for _it in _g["items"]:
            _page_rates.setdefault(_card_norm(_it["p"]), set()).add(_it.get("w") or "")

_unpriced, _compared = [], 0
for _p in rc.products():
    _rates = _page_rates.get(_card_norm(_p.get("product")))
    if not _rates:
        continue
    _compared += 1
    _per_unit = [r for r in _rates if _re.search(r"\bcp[mv]\b", r, _re.I)]
    if _per_unit and not _p.get("rate_type"):
        _unpriced.append(f"{_p['product'][:38]} — page says {sorted(_per_unit)}, "
                         f"card carries rate_type={_p.get('rate_type')!r}")

check("a product the published card sells per CPM is quoted as one",
      _unpriced == [], _unpriced[:6])
# ...and the join has to keep working, or this stops checking anything and
# says so nowhere — the failure a coverage check exists to catch.
check("and enough of the card matched the page for that to mean something",
      _compared >= 50, _compared)

# The four that were wrong, by name, because that is what a client was quoted.
# Two of them are named here rather than caught by the page comparison above:
# the Hub spells the product "purchased seperately" and the page spells it
# "purchased separately", so the names do not join. Worth knowing — the sweep
# above is a floor, not the whole guarantee.
for _n in ("IP Targeted Display - New Movers", "IP Targeted Display - Venue Replay",
           "IP Targeted Display - List is supplied or purchased seperately",
           "IP Targeted Video - List is supplied or purchased seperately"):
    _row = next(p for p in rc.products() if p["product"] == _n)
    check(f"{_n!r} is marked up rather than sold at cost",
          rc.sell_rate(_row.get("rate_value"), _row.get("rate_type")) ==
          _row["rate_value"] * rc.SELL_MULTIPLIER,
          rc.sell_rate(_row.get("rate_value"), _row.get("rate_type")))
    check(f"...and {_n!r} reports the impressions it buys",
          (rc.estimate_delivery(_row, 2000).get("units") or 0) > 0,
          rc.estimate_delivery(_row, 2000))

# A flat fee is left exactly as the card lists it -- that is what rate_type
# None means -- but it must not reach a document as a bare float.
for _n in ("Standard Set of 6 Ad Creation", "Social Media Management"):
    _row = next(p for p in rc.products() if p["product"] == _n)
    check(f"{_n!r} is still a flat fee, not marked up",
          not rc.is_marked_up(_row.get("rate_type")), _row.get("rate_type"))
    check(f"...and {_n!r} carries a rate a person can read",
          str(_row.get("listed_rate", "")).startswith("$"), _row.get("listed_rate"))

def _kit_unreadable():
    """A page we cannot read must not report as no drift."""
    import pathlib as _pl
    real = cs._KIT_PAGE
    try:
        cs._KIT_PAGE = _pl.Path("/definitely/not/here.html")
        rows = cs.kit_drift()
    finally:
        cs._KIT_PAGE = real
    return bool(rows) and "not measured" in rows[0]["detail"].lower()


# ---------------------------------------------------------------------------
section("the spec numbers are the ones on the kit the client is sent")
# ---------------------------------------------------------------------------
# The kit is transcribed on purpose -- a table fetched live changes what a
# check says with no diff to point at. What that never covered is the
# transcription going stale, and it had, in both directions: Half Page and
# 970x250 judged at 150 KB against a published 250 KB, so the checker refused
# files the client was told to send; and a smartphone banner allowed 150 KB
# against a published 50 KB, the same fault the other way. 970x250 was also
# still called "Rising Star" after the IAB retired that programme, so the kit
# and the verdict named one unit two things.
check("the transcription and the published kit agree",
      cs.kit_drift() == [], [r["detail"] for r in cs.kit_drift()][:6])

_by_id = {u["id"]: u for u in cs.UNITS}
check("Half Page is judged at the kit's 250 KB, not the retired flat 150",
      _by_id["half_page"]["max_bytes"] == 250 * cs.KB,
      _by_id["half_page"]["max_bytes"])
check("and 970x250 is a Billboard, which is what the client's copy calls it",
      _by_id["rising_star"]["name"] == "Billboard", _by_id["rising_star"]["name"])
check("a smartphone banner is held to 50 KB rather than three times it",
      _by_id["mobile_banner_320"]["max_bytes"] == 50 * cs.KB,
      _by_id["mobile_banner_320"]["max_bytes"])
check("the interstitial is sold at the three sizes the kit lists",
      set(cs._sizes_of(_by_id["mobile_interstitial"]))
      == {(640, 1136), (750, 1334), (1080, 1920)},
      cs._sizes_of(_by_id["mobile_interstitial"]))
check("SVG is accepted, as the kit says it now is",
      "svg" in _by_id["half_page"]["formats"], _by_id["half_page"]["formats"])

# The unit ids are deliberately NOT renamed with the names. tags_for() writes
# "unit_<id>" onto every file delivered through the upload manager, so a
# rename orphans the tags already on a year of creative to correct a label.
check("the id stays what Cloudinary already has tagged",
      "rising_star" in _by_id and "wide_skyscraper" in _by_id,
      sorted(_by_id)[:4])

# A DOOH target is what the kit asks for, not what it refuses. Carried as
# min_bytes it was a *fail*, so a clean 30 KB billboard was rejected for
# being too small against a number nobody published as a floor.
_dooh = _by_id["dooh_1920x1080"]
check("the DOOH 40 KB target is carried as a target",
      _dooh.get("target_bytes") == 40 * cs.KB, _dooh.get("target_bytes"))
check("and not as a floor", _dooh.get("min_bytes") is None, _dooh.get("min_bytes"))
_small = cs.check(width=1920, height=1080, fmt="jpg", size_bytes=30 * cs.KB,
                  unit_id="dooh_1920x1080")
_fails = [c for c in (_small.get("checks") or []) if c.get("state") == "fail"]
check("so a 30 KB billboard is not refused for being too small", not _fails, _fails)

# ...and the check has to be able to go red, or it is furniture.
_orig = _by_id["half_page"]["max_bytes"]
try:
    _by_id["half_page"]["max_bytes"] = 150 * cs.KB
    _bit = cs.kit_drift()
finally:
    _by_id["half_page"]["max_bytes"] = _orig
check("a number put back the way it was is reported",
      any("Half Page" in r["detail"] for r in _bit), _bit[:2])
check("and a kit that cannot be read is not measured, never a clean answer",
      _kit_unreadable(), "reported no drift for an unreadable page")


# ---------------------------------------------------------------------------
section("X is asked for formats X still sells")
# ---------------------------------------------------------------------------
# The 2025 model named eight X formats and not one of them is a format X still
# sells: "Website Card" and "Direct Message Card" are retired, and the
# mobile/desktop pairs modelled a split the kit says in as many words is gone
# -- "the mobile-versus-desktop creative split is gone. one asset set serves
# both." So the requirement line a client reads asked for four things that do
# not exist, and two of them twice. Silent from both ends: every name was a
# real format's name once, and nothing errors.
_x_units = [u for u in cs.UNITS if u["channel"] == "x"]
_x_names = {u["name"] for u in _x_units}
check("X is modeled on the eight formats the kit publishes",
      _x_names == {"Image Ads", "Video Ads", "Vertical Video Ads",
                   "Carousel Ads", "Conversation Button", "Amplify Pre-roll",
                   "Spotlight Takeover", "Polls"}, sorted(_x_names))
check("and no unit we ask for is a format the kit no longer sells",
      cs.kit_name_drift() == [],
      [d["detail"][:70] for d in cs.kit_name_drift()][:4])

# ...and the check goes red on exactly that, or it is furniture.
_saved_name = cs.BY_ID["x_image_website_card"]["name"]
try:
    cs.BY_ID["x_image_website_card"]["name"] = "Image Website Card"
    _name_bit = cs.kit_name_drift()
finally:
    cs.BY_ID["x_image_website_card"]["name"] = _saved_name
check("a retired name is reported rather than passing quietly",
      len(_name_bit) == 1 and "Image Website Card" in _name_bit[0]["detail"],
      _name_bit)

# The ids are what Cloudinary has tagged, so a format that is gone is retired
# rather than deleted -- a gallery filtering on unit_<id> must still find a
# unit rather than nothing.
for _rid in ("x_direct_message", "x_multi_image_desktop",
             "x_single_image_mobile", "x_single_image_desktop"):
    check(f"{_rid!r} still resolves by id", _rid in cs.BY_ID)
    check(f"...and {_rid!r} is not asked of a client",
          _rid not in {u["id"] for u in cs.UNITS})
    check(f"...and says what replaced it",
          bool(cs.BY_ID[_rid].get("retired")), cs.BY_ID[_rid].get("retired"))

# The ids that survived kept their ids, for the same reason.
for _kid in ("x_image_website_card", "x_video_website_card",
             "x_conversational", "x_multi_image_mobile"):
    check(f"{_kid!r} kept its id through the rename",
          _kid in {u["id"] for u in cs.UNITS})

# A real file still lands on a sensible unit -- Polls carries no media at all
# and must never be what a file is judged against.
for _w, _h, _fmt, _want in ((1080, 1080, "jpg", "Image Ads"),
                            (1080, 1920, "mp4", "Vertical Video Ads"),
                            (800, 418, "png", "Carousel Ads")):
    _v = cs.check(product="Twitter - Paid Social Media Advertising",
                  category="SOCIAL ADS - VIDEO", width=_w, height=_h,
                  size_bytes=2 * 1024 * 1024, fmt=_fmt)
    check(f"a {_w}x{_h} {_fmt} is judged as {_want}",
          (_v.get("unit") or {}).get("name") == _want, _v.get("unit"))

# What is still on the 2025 transcription is a named backlog, not an absence.
# Asserted as an invariant rather than as a restated list: a copy of the two
# rosters here would be a third thing to keep in step, and it would have to be
# edited on every platform transcribed — which is how a test stops meaning
# anything and starts being updated to match whatever the code says.
_cov = cs.kit_coverage()
check("the channels held to the 2026 names are the ones with no drift",
      _cov["names_checked"] and cs.kit_name_drift() == [],
      (_cov["names_checked"], cs.kit_name_drift()[:2]))
check("a channel is on one roster or the other, never both",
      not (set(_cov["names_checked"]) & set(_cov["names_pending"])),
      sorted(set(_cov["names_checked"]) & set(_cov["names_pending"])))
check("every channel named as checked is one this module has units for",
      set(_cov["names_checked"]) <= {u["channel"] for u in cs.UNITS},
      sorted(set(_cov["names_checked"]) - {u["channel"] for u in cs.UNITS}))
check("...and so is every channel named as pending",
      set(_cov["names_pending"]) <= {u["channel"] for u in cs.UNITS},
      sorted(set(_cov["names_pending"]) - {u["channel"] for u in cs.UNITS}))
check("each pending channel says what moved, so it can be acted on",
      all(str(v).strip() for v in _cov["names_pending"].values()),
      [k for k, v in _cov["names_pending"].items() if not str(v).strip()])
# The two done so far, by name — the backlog shrinks, and this is what says
# the shrinking is real rather than a roster edited to match.
for _done in ("x", "linkedin"):
    check(f"{_done!r} is held to the 2026 names", _done in _cov["names_checked"],
          _cov["names_checked"])


# ---------------------------------------------------------------------------
section("every section of the published kit is accounted for")
# ---------------------------------------------------------------------------
# kit_drift() read three sections of twenty-three and answered "no drift",
# which is a clean bill of health about seven per cent of what it audits. The
# page in the repo is the 2026 kit and says on itself that twenty formats were
# updated and three added, against a transcription taken from 2025 — so a
# section outside the parser is not hypothetical, and a section the *next*
# rebuild adds would be silently outside every check here for ever.
_cov = cs.kit_coverage()
check("the coverage of the published kit is measured",
      _cov["measured"] is True, _cov.get("error"))
check("every published section is declared one way or the other",
      _cov["undeclared"] == [], _cov["undeclared"])
check("and no declaration outlives the section it described",
      _cov["stale"] == [], _cov["stale"])
check("the three the numbers are checked against are still checked",
      set(_cov["checked"]) == set(cs._KIT_SECTIONS), _cov["checked"])
check("and the count is the whole page, not the part that parses",
      _cov["sections"] == len(_cov["checked"]) + len(_cov["unread"])
      + len(_cov["not_modelled"]), _cov["sections"])

# ...and it goes red on a section nobody has declared, or it is furniture.
_saved_unread = dict(cs._KIT_UNREAD)
try:
    cs._KIT_UNREAD.pop("tiktok", None)
    _bit_cov = cs.kit_coverage()
finally:
    cs._KIT_UNREAD.clear()
    cs._KIT_UNREAD.update(_saved_unread)
check("an undeclared section is reported rather than passing quietly",
      _bit_cov["undeclared"] == ["tiktok"], _bit_cov["undeclared"])

# Three published sections are a different kind of gap: the kit sells them and
# this module holds no unit for them at all. A Meta requirement that lists
# Stories and never Reels reads as complete, and the page itself says the two
# are not interchangeable.
check("a Meta buy is told the kit also sells both Reels",
      cs.unmodelled_for(["facebook", "instagram", "stories"]) ==
      ["Instagram Reels", "Facebook Reels"],
      cs.unmodelled_for(["facebook", "instagram", "stories"]))
check("a CTV buy is told about the interactive formats",
      len(cs.unmodelled_for(["ctv"])) == 1, cs.unmodelled_for(["ctv"]))
check("and a display buy is told nothing, because nothing is missing",
      cs.unmodelled_for(["desktop_display", "mobile_display"]) == [],
      cs.unmodelled_for(["desktop_display", "mobile_display"]))

# It has to reach the line the client document prints, not only the payload:
# left in the note alone the requirement a client reads still looks complete.
_meta_row = next(p for p in _rc_for_media.products()
                 if p["product"].startswith("Facebook | Instagram - Awareness"))
_meta_st = {"items": [dict(_meta_row, dollars=4000)], "months": 3}
_meta_req = cn.required_units(_meta_st, cn.medium_of(_meta_row))
check("the requirement payload names what it cannot size",
      _meta_req["not_measured_formats"] == ["Instagram Reels", "Facebook Reels"],
      _meta_req["not_measured_formats"])
check("...and says so without claiming to have measured it",
      "not measured" not in _meta_req["note"].lower()
      and "no unit here to measure" in _meta_req["note"], _meta_req["note"])
check("and the one-line requirement carries it too",
      "Instagram Reels and Facebook Reels" in
      cn.units_line(_meta_st, cn.medium_of(_meta_row)),
      cn.units_line(_meta_st, cn.medium_of(_meta_row)))
check("but the gate still measures the units it does have",
      _meta_req["measured"] is True)

_disp_row = next(p for p in _rc_for_media.products() if p["category"] == "DISPLAY")
_disp_st = {"items": [dict(_disp_row, dollars=4000)], "months": 3}
check("a display requirement gains no such clause",
      "the kit also sells" not in cn.units_line(_disp_st, cn.medium_of(_disp_row)),
      cn.units_line(_disp_st, cn.medium_of(_disp_row)))

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
