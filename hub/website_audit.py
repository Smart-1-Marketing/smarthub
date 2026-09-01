"""The full website audit: one description, read by four screens.

A rep opens a client record, a rep opens this tool, a prospect fills in a
widget on somebody else's website, and the Proposal Builder asks what we know
before it writes a word. All four are asking the same question of the same
440-field Insites audit, and before this each of them answered it its own way.

What is here, and why each piece is here rather than in a template:

* **What they are already spending comes first.** `spend()` is the block, and
  it leads every rendering of this audit. It is the one section that changes
  the conversation: a business already putting $2,400 a month into Google Ads
  is a different sale from one putting in nothing, and the second question a
  rep asks is what that money is buying. `hub/scan_facts.py` carries the same
  five fields as one collapsed reference group among ten — right for a
  reference card on a client record, wrong for the document the sales
  conversation is built on. The group is *dropped* from the rest of this
  audit rather than printed twice: two panels answering one question is how a
  reader learns to believe neither, the rule
  `jsonstore.unmirrored_json_writers()` exists to close.

* **An estimate is labelled, and arithmetic shows its working.** Every spend
  figure in an Insites audit is a third-party estimate of somebody else's
  spend. Annualising one is our multiplication, not their measurement, so the
  row says `× 12`. A cost per visit is *their* two numbers divided, so the row
  says which two. Nothing is inferred from an industry average: a benchmark
  CPC applied to an organic visit count produces a five-figure "value" that
  reads as measurement, and the number a client checks hardest is the one
  about their own money.

* **A total is only a total when every part of it was measured.** Facebook
  and display are observed as *running*, with no spend figure published for
  either — so a monthly total that quietly counted them as zero would report
  a business spending $6,000 as spending $2,400, in a clean confident row.
  `spend()["total"]` covers only what carries a number, and everything left
  out is **named** in `total_excludes`.

* **What they told us and what was observed never merge.** The intake below
  is the customer's own answer; the audit is a crawler's. Where both exist
  and disagree, the disagreement is the finding — `analytics_ids.py` makes the
  same point about a recorded GA id against an observed one — so
  `spend()["stated"]` sits beside `spend()["observed"]` and neither is folded
  into the other.

* **Sixty days.** `STALE_DAYS` is when an audit stops being worth quoting
  from. A proposal written against a five-month-old reading of a site that has
  since been rebuilt is wrong in the one direction a client will notice, and
  the reading carries no sign of its own age once it is copied into a
  document. `staleness()` answers with the age *and* the day it was read, so
  a screen never has to render an age it computed itself.

* **A scan is a lead.** Somebody typed a business and a website into this
  Hub, which is a prospect however it arrived. `lead_fields()` is what that
  lead carries; `hub/leads.py` owns the writing, the delivery and the panel,
  and there is no second lead book here for the reason `modules/scans/leads.py`
  gives at length.

* **Nothing here raises and nothing here scans.** Reading is free; a page
  that spends a credit on load is a page nobody can open twice. Starting an
  audit is `POST /scans/api/scans`, from the browser, in the module that owns
  scans — reaching into a dispatcher-mounted module from a hub route is the
  `flask.g` trap CLAUDE.md names at length.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from hub.client_context import canonical_domain

# How old an audit may be before a proposal should not be written from it.
# Sixty days is one quarter's worth of a site changing under us; override for a
# deployment that rescans on a different rhythm.
try:
    STALE_DAYS = max(1, int(os.environ.get("AUDIT_STALE_DAYS") or 60))
except (TypeError, ValueError):
    STALE_DAYS = 60

# The scan_facts group this module replaces with its own, richer block. Matched
# on the title because that is what scan_facts publishes; a title changed there
# without changing this one shows the section twice, which `check_spec()`
# below is what catches.
SPEND_GROUP_TITLE = "What they are already spending"


# ==========================================================================
# What we ask, and what each answer changes
# ==========================================================================
#
# Three rules, each learned elsewhere in this Hub:
#
# * **Every question feeds something.** `hub/current_marketing.py` shipped
#   four discovery questions that were read by nothing, so a rep could answer
#   all four and the document came out identical. `feeds` names what each
#   answer changes and `check_spec()` fails on a question that changes
#   nothing — the `unanswered_keys()` rule, one form later.
# * **"Not asked" is not "no".** Every yes/no is tri-state. A prospect who
#   skipped a question has not told us they are doing nothing, and printing
#   that as a confident No on a proposal is us inventing their answer.
# * **A public form is short.** `ask` is "both" or "staff": a prospect on
#   somebody else's website answers six questions or leaves, and the rest are
#   things a rep can fill in from the call afterwards.

INTAKE: list[dict] = [
    {"key": "goal", "ask": "both", "type": "choice", "required": True,
     "label": "What would you like more of?",
     "why": "What the campaign is bought to do decides the products on it.",
     "feeds": "proposal goal",
     "options": ["Phone calls", "Form inquiries", "Booked appointments",
                 "Store or showroom visits", "Online sales",
                 "Brand awareness"]},
    {"key": "services", "ask": "both", "type": "text", "required": True,
     "label": "What do you sell, in your own words?",
     "why": "The keyword set and the ad copy are written from this rather "
            "than from whatever the home page happens to say.",
     "feeds": "proposal copy, keyword set"},
    {"key": "areas", "ask": "both", "type": "text", "required": True,
     "label": "Where do your customers come from?",
     "why": "Towns, ZIP codes or a radius — this becomes the target areas on "
            "the media plan.",
     "feeds": "target areas"},
    {"key": "monthly_budget", "ask": "both", "type": "choice",
     "label": "Roughly what are you spending on marketing a month?",
     "why": "Sizes the recommendation, and is the figure the estimate is "
            "checked against. Optional — refusing to build anything until a "
            "client picks a number is how the conversation stops before it "
            "starts.",
     "feeds": "spend block, proposal budget",
     "options": ["Nothing yet", "Under $1,000", "$1,000 - $2,500",
                 "$2,500 - $5,000", "$5,000 - $10,000", "Over $10,000",
                 "Rather not say"]},
    {"key": "traditional", "ask": "both", "type": "choice",
     "label": "Running any radio, TV, print or outdoor?",
     "why": "Decides whether the proposal supplements that spend or moves "
            "part of it — the posture, which reaches Expected Results.",
     "feeds": "proposal posture",
     "options": ["No", "Yes, and it stays", "Yes, and some could move",
                 "Not sure"]},
    {"key": "website_happy", "ask": "both", "type": "yesno",
     "label": "Happy with your website?",
     "why": "A campaign pointed at a page the client already dislikes is the "
            "cheapest fix on the plan and the one nobody raises.",
     "feeds": "discovery answer websiteHappy"},
    {"key": "competitors", "ask": "staff", "type": "text",
     "label": "Who do they lose business to?",
     "why": "The client is the only person in the room who knows. Names here "
            "become competitor targeting, and stay a suggestion until "
            "somebody ticks them.",
     "feeds": "competitor targeting"},
    {"key": "handles_enquiries", "ask": "staff", "type": "text",
     "label": "Who follows up an inquiry, and how fast?",
     "why": "Decides whether the plan needs the Suite's speed-to-lead at all.",
     "feeds": "Suite tier"},
    {"key": "timeline", "ask": "staff", "type": "choice",
     "label": "When would they want to start?",
     "why": "Creative lead time is what a launch date is missed on.",
     "feeds": "creative gate",
     "options": ["As soon as possible", "Within a month", "This quarter",
                 "Just looking"]},
    {"key": "notes", "ask": "staff", "type": "text",
     "label": "Anything else worth knowing?",
     "why": "Goes to the proposal writer as context, unchecked — most of it "
            "is how they operate rather than a claim about them.",
     "feeds": "proposal context"},
]

INTAKE_INDEX = {q["key"]: q for q in INTAKE}

# The budget bands, as a monthly midpoint. Used only to *size* — never printed
# as what a client spends, because a band is not a figure and a midpoint of a
# band is our arithmetic on their approximation.
BUDGET_MIDPOINT = {
    "Nothing yet": 0,
    "Under $1,000": 500,
    "$1,000 - $2,500": 1750,
    "$2,500 - $5,000": 3750,
    "$5,000 - $10,000": 7500,
    "Over $10,000": 12000,
}


def questions(audience: str = "both") -> list[dict]:
    """The intake, for a customer ("customer") or for a rep ("staff")."""
    if audience == "customer":
        return [dict(q) for q in INTAKE if q["ask"] == "both"]
    return [dict(q) for q in INTAKE]


def check_spec() -> list[str]:
    """Findings about this file itself. Empty is the only acceptable answer.

    Two things it will not let drift: a question nobody reads (the
    `current_marketing.unanswered_keys()` rule) and the spend group's title
    getting out of step with `hub/scan_facts.py`, which would print the same
    five figures twice on one page under two headings.
    """
    out = []
    for q in INTAKE:
        if not str(q.get("feeds") or "").strip():
            out.append(f"intake question {q['key']} changes nothing")
        if not str(q.get("why") or "").strip():
            out.append(f"intake question {q['key']} does not say what it is for")
        if q.get("type") == "choice" and not q.get("options"):
            out.append(f"intake question {q['key']} offers no options")
    try:
        from hub import scan_facts
        titles = {t for t in _spend_titles(scan_facts)}
        if SPEND_GROUP_TITLE not in titles:
            out.append(
                f"scan_facts no longer publishes a group called "
                f"{SPEND_GROUP_TITLE!r}, so this audit's own spend block sits "
                f"beside whatever replaced it and the same figures are on the "
                f"page twice")
    except Exception as exc:                              # noqa: BLE001
        out.append(f"scan_facts could not be read: {type(exc).__name__}")
    return out


def _spend_titles(scan_facts_module) -> list[str]:
    """Every group title `scan_facts.facts()` can emit, read from its source.

    Read rather than run, because running it needs a scanned domain and a
    check that only works where there is data is a check that is green on a
    fresh deployment for the wrong reason.
    """
    import inspect
    src = inspect.getsource(scan_facts_module.facts)
    return re.findall(r'group\(\s*"([^"]+)"', src)


# ==========================================================================
# Reading the audit
# ==========================================================================

def _get(report: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = report
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _n(value: Any) -> float | int | None:
    """A number, or None. `True` is not a number; the string "12" is."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def _b(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _s(value: Any, limit: int = 300) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)[:limit]
    return str(value).strip()[:limit]


def _money(n) -> str:
    return f"${n:,.0f}" if n is not None else ""


def _parse_stamp(stamp: str) -> datetime | None:
    s = str(stamp or "").strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def staleness(scanned_at: str) -> dict:
    """How old this reading is, and whether it should be quoted from.

    `age_days` is None where the date could not be read — *not measured*, and
    never zero, which would read as "scanned today" on the one screen that
    decides whether to spend a credit rescanning.
    """
    when = _parse_stamp(scanned_at)
    if when is None:
        return {"age_days": None, "stale": None, "limit_days": STALE_DAYS,
                "read_on": "", "measured": False,
                "note": "We could not read when this audit was taken, so "
                        "whether it is still current is not measured."}
    age = max(0, (datetime.now(timezone.utc) - when).days)
    stale = age > STALE_DAYS
    return {
        "age_days": age, "stale": stale, "limit_days": STALE_DAYS,
        "read_on": when.date().isoformat(), "measured": True,
        "note": (f"This website was read {age} days ago, which is over the "
                 f"{STALE_DAYS}-day mark — rescan before quoting from it."
                 if stale else
                 f"Read {age} days ago." if age else "Read today."),
    }


# ==========================================================================
# What they are already spending — the first block
# ==========================================================================

def _fig(label: str, value, *, note: str = "", link: str = "",
         measured: bool = True, why: str = "") -> dict:
    """One figure. `measured=False` says so rather than showing a zero."""
    return {"label": label, "value": value, "note": note, "link": link,
            "measured": bool(measured), "why": why}


def spend(report: dict, intake: dict | None = None) -> dict:
    """What this business is already putting into marketing.

    Two halves that are never added together. `observed` is what a third party
    estimates from the outside; `stated` is what the business itself told us
    through the intake. Where both exist the gap is the finding — an estimate
    a long way under what somebody says they spend usually means most of the
    money is going somewhere the audit cannot see, which is the most useful
    sentence on the page.
    """
    intake = intake or {}
    g = lambda p, d=None: _get(report, p, d)                   # noqa: E731

    ads_running = _b(g("paid_search.has_adwords_spend"))
    ads_month = _n(g("paid_search.average_adspend"))
    ads_visits = _n(g("paid_search.average_adtraffic"))
    fb_active = _n(g("facebook_ads.fb_ads_currently_active"))
    display = _b(g("display_ads.uses_display_ads"))
    organic = _n(g("organic_search.average_monthly_traffic"))
    keywords = _n(g("organic_search.num_keywords_ranked_for"))

    rows: list[dict] = []
    counted: list[str] = []
    excluded: list[str] = []

    # --- Google Ads. The only channel the audit puts a number on. -----------
    if ads_month is not None:
        rows.append(_fig("Google Ads, estimated monthly", _money(ads_month),
                         note="A third-party estimate of their spend, not a "
                              "billed figure."))
        rows.append(_fig("Google Ads, estimated annually", _money(ads_month * 12),
                         why="the monthly estimate × 12",
                         note="Our multiplication of their estimate, not a "
                              "second measurement."))
        counted.append("Google Ads")
    elif ads_running is True:
        rows.append(_fig("Google Ads", "Running — spend not measured",
                         measured=False,
                         note="They are advertising on Google; no spend "
                              "figure came back for them."))
        excluded.append("Google Ads, which is running with no figure against it")
    elif ads_running is False:
        rows.append(_fig("Google Ads", "Not running"))
    else:
        rows.append(_fig("Google Ads", "Not measured", measured=False))

    if ads_visits is not None:
        rows.append(_fig("Estimated visits from those ads, monthly",
                         f"{ads_visits:,.0f}"))
    if ads_month is not None and ads_visits:
        rows.append(_fig("Implied cost per visit",
                         f"${ads_month / ads_visits:,.2f}",
                         why="their estimated monthly spend ÷ their estimated "
                             "monthly paid visits",
                         note="Two estimates divided, so it carries both "
                              "margins of error."))

    # --- Everything else that is running and carries no number. -------------
    if fb_active is not None and fb_active > 0:
        rows.append(_fig("Facebook / Instagram ads live now",
                         f"{fb_active:,.0f}", measured=False,
                         link=_s(g("facebook_ads.fb_ad_library_url"), 500),
                         note="Meta publishes the ads, never the spend behind "
                              "them, so this counts creative and not money."))
        excluded.append("paid social, which is running with no figure against it")
    elif fb_active is not None:
        rows.append(_fig("Facebook / Instagram ads live now", "None"))
    if display is True:
        rows.append(_fig("Display advertising", "Running", measured=False,
                         link=_s(g("display_ads.ad_transparency_centre_url"), 500),
                         note="Seen in Google's ad transparency center. No "
                              "spend is published there either."))
        excluded.append("display, which is running with no figure against it")
    elif display is False:
        rows.append(_fig("Display advertising", "Not running"))

    # --- The total, and the honest limits on it. ---------------------------
    total = ads_month if ads_month is not None else None
    total_note = ""
    if total is not None and excluded:
        total_note = ("This is the part of their spend that carries a number. "
                      "It leaves out " + _join(excluded) + ".")
    elif total is not None:
        total_note = ("Everything the audit could see a figure for. Anything "
                      "bought outside these channels is not in it.")

    # --- What they told us, kept apart from what was seen. ------------------
    said = _s(intake.get("monthly_budget"))
    stated = {}
    if said and said != "Rather not say":
        mid = BUDGET_MIDPOINT.get(said)
        stated = {"band": said, "midpoint": mid,
                  "note": "What the business told us, not something measured."}
        if mid is not None and total is not None:
            gap = mid - total
            if abs(gap) >= max(250, total * 0.3):
                stated["finding"] = (
                    f"They put their marketing at about {_money(mid)} a month "
                    f"and the audit can only account for {_money(total)} of it"
                    + (f" — roughly {_money(abs(gap))} a month is going "
                       f"somewhere this audit cannot see."
                       if gap > 0 else
                       " — the estimate is the higher of the two, which is "
                       "worth asking about before it is quoted back.")
                )
    elif said == "Rather not say":
        stated = {"band": "", "midpoint": None,
                  "note": "They chose not to say, which is an answer and not "
                          "a blank."}

    # --- What organic is doing beside it, never priced. ---------------------
    earned = []
    if organic is not None:
        earned.append(_fig("Visits they earn without paying, monthly",
                           f"{organic:,.0f}"))
    if keywords is not None:
        earned.append(_fig("Search terms they already rank for",
                           f"{keywords:,.0f}"))
    earned_note = ""
    if organic is not None and ads_month is not None and ads_visits:
        earned_note = (
            "Priced at their own implied cost per visit that traffic would be "
            + _money(organic * (ads_month / ads_visits)) + " a month — their "
            "two numbers multiplied by a third of theirs, and an illustration "
            "rather than a bill anybody is paying.")
    elif organic is not None:
        earned_note = ("What that traffic would cost to buy is not measured: "
                       "it needs a cost per visit from their own campaign, and "
                       "there is not one here. A sector average put against it "
                       "would look like a measurement of their business.")

    measured = any(r["measured"] for r in rows)
    return {
        "title": "What they are already spending",
        "why": "The first thing worth knowing, and the one that decides what "
               "the conversation is about. Every figure here is a third-party "
               "estimate of somebody else's spend.",
        "observed": rows,
        "total": total,
        "total_display": _money(total),
        "total_note": total_note,
        "total_excludes": excluded,
        "counted": counted,
        "stated": stated,
        "earned": earned,
        "earned_note": earned_note,
        "measured": measured,
        "note": ("" if measured else
                 "Nothing about their advertising came back for this site. "
                 "That is a plan that does not include it or a site that "
                 "could not be read — not a business spending nothing."),
    }


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ==========================================================================
# The headline: what a rep reads before anything else
# ==========================================================================

def headline(report: dict, meta: dict) -> dict:
    """Four or five figures that fit on one line, each with a link behind it."""
    g = lambda p, d=None: _get(report, p, d)                   # noqa: E731
    out = []
    score = meta.get("score")
    if score is not None:
        out.append(_fig("Overall score", f"{score}",
                        note=_s(meta.get("tier"))))
    ai = _b(g("ai_readiness.is_ai_optimised"))
    if ai is not None:
        out.append(_fig("Ready for AI search", "Yes" if ai else "No"))
    gbp = _b(g("google_business_profile.is_listing_found"))
    if gbp is not None:
        out.append(_fig("Google Business Profile",
                        "Found" if gbp else "Not found",
                        link=_s(g("google_business_profile.listing_url"), 500)))
    rating = _n(g("google_business_profile.review_rating"))
    reviews = _n(g("google_business_profile.review_count"))
    if rating is not None:
        out.append(_fig("Google rating",
                        f"{rating} ★" + (f" ({reviews:,.0f})" if reviews else "")))
    speed = _n(g("page_speed.mobile_speed_score")) or _n(g("speed.speed_index"))
    if speed is not None:
        out.append(_fig("Mobile speed", f"{speed}"))
    return {"rows": out, "measured": bool(out)}


# ==========================================================================
# The opportunities: what this audit says we could sell, and why
# ==========================================================================
#
# Deliberately a *finding plus its evidence*, never a product name on its own.
# "They should buy retargeting" is an opinion; "no retargeting pixel is on the
# site at all, so a visitor who leaves cannot be brought back" is the thing
# that was measured, with the product as the consequence. The second survives
# being read out to the client; the first is what a rep gets argued with over.

OPPORTUNITIES = [
    {"key": "no_retargeting",
     "when": lambda g: _b(g("retargeting.has_facebook_pixel")) is False
                       and _b(g("retargeting.has_google_pixel")) is False,
     "finding": "No retargeting pixel of any kind is on the site.",
     "means": "Every visitor who leaves without acting is gone for good — "
              "there is nothing to bring them back with.",
     "sells": "Retargeting"},
    {"key": "no_analytics",
     "when": lambda g: _b(g("analytics.has_analytics")) is False,
     "finding": "No analytics tag was found.",
     "means": "Nothing running to this site can be measured, so the first "
              "campaign would be reporting on itself.",
     "sells": "Analytics setup"},
    {"key": "universal_ga",
     "when": lambda g: _b(g("analytics.uses_universal_ga")) is True,
     "finding": "The site is still on Universal Analytics.",
     "means": "Universal Analytics stopped collecting in 2023, so whatever "
              "the client is reading is not this year's traffic.",
     "sells": "GA4 migration"},
    {"key": "gbp_missing",
     "when": lambda g: _b(g("google_business_profile.is_listing_found")) is False,
     "finding": "No Google Business Profile was found.",
     "means": "They are absent from the map pack, which is where a local "
              "search decides who gets the call.",
     "sells": "Local listings"},
    {"key": "gbp_unclaimed",
     "when": lambda g: (_b(g("google_business_profile.is_listing_found")) is True
                        and _b(g("google_business_profile.is_listing_claimed")) is False),
     "finding": "Their Google listing is unclaimed.",
     "means": "Anybody can edit it, including the hours and the phone number.",
     "sells": "Local listings"},
    {"key": "not_ai_ready",
     "when": lambda g: _b(g("ai_readiness.is_ai_optimised")) is False,
     "finding": "The site is not readable by the AI assistants.",
     "means": "ChatGPT, Gemini and Perplexity answer the questions customers "
              "used to type into Google, and this business is not in those "
              "answers.",
     "sells": "AI search optimization"},
    {"key": "no_paid_search",
     "when": lambda g: _b(g("paid_search.has_adwords_spend")) is False,
     "finding": "They are not running paid search.",
     "means": "Every search for what they sell goes to somebody who is.",
     "sells": "Paid search"},
    {"key": "no_booking",
     "when": lambda g: (_b(g("booking_widget.has_booking_widget")) is False
                        and _n(g("click_to_contact.tel_links_found_count")) == 0),
     "finding": "There is no booking tool and no click-to-call link.",
     "means": "A visitor on a phone has no way to act without typing a number "
              "out, which is where most of them stop.",
     "sells": "Website work"},
    {"key": "no_chat",
     "when": lambda g: _b(g("live_chat.has_live_chat")) is False,
     "finding": "No chat widget on the site.",
     "means": "An inquiry outside office hours has nowhere to go.",
     "sells": "Smart 1 Suite"},
    {"key": "slow_mobile",
     "when": lambda g: _b(g("mobile.is_mobile")) is False,
     "finding": "The site is not mobile optimized.",
     "means": "Most of the traffic any campaign buys arrives on a phone.",
     "sells": "Website work"},
    {"key": "alt_text",
     "when": lambda g: (_n(g("alternative_text.images_no_alt_count")) or 0) > 0,
     "finding": "Images on the site carry no alt text.",
     "means": "Search engines and screen readers can see nothing in them.",
     "sells": "SEO images"},
    {"key": "no_schema",
     "when": lambda g: (_n(g("structured_data.count_missing_schema_items")) or 0) > 0,
     "finding": "Schema markup is missing from the site.",
     "means": "It is what tells a search engine what the business is, and it "
              "is what the AI assistants read first.",
     "sells": "Schema"},
    {"key": "reviews_thin",
     "when": lambda g: (_n(g("google_business_profile.review_count")) is not None
                        and (_n(g("google_business_profile.review_count")) or 0) < 20),
     "finding": "Under 20 Google reviews.",
     "means": "Reviews are the last thing read before a local customer picks, "
              "and this is below where that comparison is usually won.",
     "sells": "Reputation"},
    {"key": "social_quiet",
     "when": lambda g: (_n(g("facebook_page.days_since_last_post")) or 0) > 60,
     "finding": "Their Facebook page has not been posted to in over two months.",
     "means": "A prospect who checks finds a page that looks abandoned.",
     "sells": "Social planner"},
]


def opportunities(report: dict) -> list[dict]:
    """Findings this audit supports, each carrying the evidence behind it.

    Only what was *measured* produces a finding. An absent field is not a
    finding: reporting "no pixel found" about a plan that does not check for
    pixels is the confident wrong answer this codebase keeps having to undo,
    which is why every rule tests `is False` and `is True` rather than
    truthiness.
    """
    g = lambda p, d=None: _get(report, p, d)                   # noqa: E731
    out = []
    for rule in OPPORTUNITIES:
        try:
            hit = bool(rule["when"](g))
        except Exception:                                      # noqa: BLE001
            hit = False
        if hit:
            out.append({"key": rule["key"], "finding": rule["finding"],
                        "means": rule["means"], "sells": rule["sells"]})
    return out


# ==========================================================================
# What the audit can answer of the discovery questions
# ==========================================================================
#
# `hub/current_marketing.py` asks a set of questions and the answers change
# what a proposal recommends. Several of them were already measured on this
# client's own website weeks ago, and a rep was retyping them off a screen
# they had open. These are **proposals a person accepts**, never applied
# silently: "are they doing SEO" is a judgement from evidence, and evidence is
# what is carried beside each one so it can be argued with.

_YES, _NO, _UNKNOWN = "yes", "no", "unknown"


def discovery_answers(report: dict) -> list[dict]:
    """`[{key, answer, evidence}]` for the discovery questions this audit can
    speak to. A question it cannot speak to is left out entirely, rather than
    answered `unknown` — a screen offering a full column of "we don't know"
    is a screen nobody reads to the bottom of.
    """
    g = lambda p, d=None: _get(report, p, d)                   # noqa: E731
    out: list[dict] = []

    def add(key, answer, evidence):
        if answer in (_YES, _NO):
            out.append({"key": key, "answer": answer, "evidence": evidence})

    paid = _b(g("paid_search.has_adwords_spend"))
    if paid is not None:
        spend_m = _n(g("paid_search.average_adspend"))
        add("paidSearch", _YES if paid else _NO,
            (f"Estimated at {_money(spend_m)} a month on Google Ads."
             if paid and spend_m is not None else
             "Google Ads activity was found for this domain." if paid else
             "No Google Ads activity was found for this domain."))

    fb_ads = _n(g("facebook_ads.fb_ads_currently_active"))
    if fb_ads is not None:
        add("paidSocial", _YES if fb_ads > 0 else _NO,
            (f"{fb_ads:,.0f} ads live in Meta's ad library."
             if fb_ads > 0 else "No live ads in Meta's ad library."))

    fbp, gp = _b(g("retargeting.has_facebook_pixel")), _b(g("retargeting.has_google_pixel"))
    if fbp is not None or gp is not None:
        found = [n for n, v in (("Meta pixel", fbp), ("Google remarketing tag", gp))
                 if v is True]
        add("retargeting", _YES if found else _NO,
            (_join(found) + " on the site." if found else
             "Neither a Meta pixel nor a Google remarketing tag is on the site."))

    ai = _b(g("ai_readiness.is_ai_optimised"))
    if ai is not None:
        allowed = _b(g("ai_readiness.ai_user_agents_allowed"))
        add("aiOptimized", _YES if ai else _NO,
            ("The site reads as optimized for AI search." if ai else
             "AI crawlers are blocked in robots.txt." if allowed is False else
             "The site is not set up for the AI assistants to read."))

    chat = _b(g("live_chat.has_live_chat"))
    if chat is not None:
        apps = _s(g("live_chat.live_chat_apps"))
        add("chat", _YES if chat else _NO,
            (f"{apps} is on the site." if chat and apps else
             "A chat widget is on the site." if chat else
             "No chat widget on the site."))

    # A booking widget on the site and a booking link on the Google listing
    # are both online scheduling, so either one is a yes. A no only ever
    # claims what was measured: the widget test answered False about the
    # site, and nothing here speaks for a listing that was not read.
    booked = _b(g("booking_widget.has_booking_widget"))
    gbp_booking = _s(g("google_business_profile.booking_link_url"))
    if booked is not None or gbp_booking:
        booker = _s(g("booking_widget.booking_widget_apps"))
        if booked:
            add("appointments", _YES,
                f"{booker} is on the site." if booker else
                "A booking widget is on the site.")
        elif gbp_booking:
            add("appointments", _YES,
                "Their Google listing carries a booking link.")
        else:
            add("appointments", _NO, "No online booking widget on the site.")

    email = _s(g("email_provider.email_providers"))
    if email:
        add("email", _YES, f"{email} is in use on the domain.")

    reviews = _n(g("google_business_profile.review_count"))
    if reviews is not None:
        add("reputation", _YES if reviews >= 20 else _NO,
            (f"{reviews:,.0f} Google reviews." if reviews else
             "No Google reviews found."))

    posted = _n(g("facebook_page.days_since_last_post"))
    if posted is not None:
        add("socialPosting", _YES if posted <= 30 else _NO,
            (f"Last Facebook post {posted:,.0f} days ago."))

    kw = _n(g("organic_search.num_keywords_ranked_for"))
    blog = _n(g("blog.blog_post_count"))
    if kw is not None:
        add("seo", _YES if (kw >= 50 or (blog or 0) >= 5) else _NO,
            (f"Ranking for {kw:,.0f} search terms"
             + (f" with {blog:,.0f} blog posts on the site." if blog else ".")))

    tel = _n(g("click_to_contact.tel_links_found_count"))
    if tel is not None and tel == 0:
        add("callTracking", _NO,
            "No click-to-call links on the site, so inbound calls are not "
            "being routed through anything that can count them.")

    return out


def discovery_note(answers: list[dict]) -> str:
    if not answers:
        return ("This audit could not answer any of the discovery questions "
                "for you — not measured rather than all-no.")
    return (f"{len(answers)} of the discovery questions have already been "
            f"measured on their own website. Each is a proposal with its "
            f"evidence beside it; nothing is filled in until you press.")


# ==========================================================================
# The whole audit
# ==========================================================================

def audit(domain: str, intake: dict | None = None) -> dict:
    """Everything worth showing about one website, spend first.

    `(payload)` rather than `(payload, error)` because the error rides inside:
    every screen reading this has to render "we could not look" differently
    from "nobody has scanned them", and both differently from a clean audit,
    so all three are keys rather than an exception the caller has to catch.
    """
    from hub import scan_facts
    key = canonical_domain(domain)
    if not key:
        return {"found": False, "measured": False, "domain": "",
                "error": "", "intake": intake or {},
                "note": "No website to read. A domain is the join key for "
                        "everything here."}
    try:
        report, meta, err = scan_facts.latest_report(domain)
    except Exception as exc:                                   # noqa: BLE001
        return {"found": False, "measured": False, "domain": key,
                "error": f"{type(exc).__name__}: {exc}", "intake": intake or {},
                "note": "This website could not be read, so none of it is "
                        "measured."}
    if err:
        return {"found": False, "measured": False, "domain": key,
                "error": err, "intake": intake or {},
                "note": "This website could not be read, so none of it is "
                        "measured."}
    if not meta:
        return {"found": False, "measured": False, "domain": key, "error": "",
                "intake": intake or {},
                "note": "Nothing has been read from this website yet. Running "
                        "an audit spends one credit and takes a few minutes."}

    # The rest of the audit, minus the spend group — this module prints its
    # own, richer one at the top and two panels answering the same question
    # differently on one page is worse than either alone.
    rest = scan_facts.facts(domain)
    groups = [gp for gp in (rest.get("groups") or [])
              if gp.get("title") != SPEND_GROUP_TITLE]

    return {
        "found": True,
        "measured": True,
        "domain": meta.get("domain") or key,
        "score": meta.get("score"),
        "tier": meta.get("tier") or "",
        "scanned_at": meta.get("scanned_at") or "",
        "scan_url": meta.get("scan_url") or "",
        "public_id": meta.get("public_id") or "",
        "age": staleness(meta.get("scanned_at") or ""),
        "spend": spend(report, intake),
        "headline": headline(report, meta),
        "opportunities": opportunities(report),
        "discovery": discovery_answers(report),
        "discovery_note": discovery_note(discovery_answers(report)),
        "groups": groups,
        "intake": intake or {},
        "error": "",
        "note": "",
    }


# ==========================================================================
# What a scan hands the Proposal Builder
# ==========================================================================

def proposal_prefill(payload: dict) -> dict:
    """The blob the Proposal Builder reads when a scan is carried into it.

    Everything in it is a **suggestion with its evidence**. Nothing is applied
    without a press, for the reason `modules/ads_builder` gives about its own
    researched competitor list: printing a researched claim on a proposal is
    us telling a client something on our say-so, and that is the paragraph a
    client checks hardest.

    A stale audit still prefills — refusing would leave a rep with nothing
    while they wait for a rescan — but `age` travels with it so the screen can
    say how old the evidence is beside every answer it came from.
    """
    payload = payload or {}
    intake = payload.get("intake") or {}
    out = {
        "domain": payload.get("domain") or "",
        "found": bool(payload.get("found")),
        "age": payload.get("age") or {},
        "scan_url": payload.get("scan_url") or "",
        "mkt": {a["key"]: a["answer"] for a in (payload.get("discovery") or [])},
        "evidence": {a["key"]: a["evidence"] for a in (payload.get("discovery") or [])},
        "opportunities": payload.get("opportunities") or [],
        "spend": payload.get("spend") or {},
        "intake": intake,
    }
    # The intake answers that map onto a proposal field by themselves.
    if intake.get("services"):
        out["services"] = _s(intake["services"], 600)
    if intake.get("areas"):
        out["areas_text"] = _s(intake["areas"], 600)
    if intake.get("competitors"):
        out["competitors_text"] = _s(intake["competitors"], 600)
    if intake.get("goal"):
        out["goal"] = _s(intake["goal"], 80)
    if intake.get("website_happy") in ("yes", "no"):
        out["mkt"]["websiteHappy"] = intake["website_happy"]
        out["evidence"]["websiteHappy"] = "They told us."
    if intake.get("traditional"):
        out["traditional"] = _s(intake["traditional"], 80)
    band = _s(intake.get("monthly_budget"), 40)
    if band and band not in ("Rather not say", "Nothing yet"):
        out["budget_band"] = band
        out["budget_midpoint"] = BUDGET_MIDPOINT.get(band)
    out["note"] = (
        "Everything here came off their own website or out of the intake "
        "form. It is offered, not applied — press to take an answer.")
    return out


# ==========================================================================
# What a scan hands hub/leads.py
# ==========================================================================

def lead_fields(payload: dict, contact: dict) -> dict:
    """The fields a lead carries when it came out of a website audit.

    Flat strings, because `hub/leads.py` cleans and truncates every value and
    a nested structure would arrive as `"{'a': 1}"` in the Suite. What goes on
    is what somebody picking the lead up would want in front of them: the
    score, the top two or three findings and what they said they spend — not
    the whole audit, which is on the record and one link away.
    """
    payload, contact = payload or {}, contact or {}
    sp = payload.get("spend") or {}
    fields = {
        "name": _s(contact.get("name"), 200),
        "email": _s(contact.get("email"), 320),
        "phone": _s(contact.get("phone"), 64),
        "company": _s(contact.get("company"), 200),
        "website": payload.get("domain") or _s(contact.get("website"), 300),
    }
    if payload.get("score") is not None:
        fields["audit_score"] = str(payload["score"])
    findings = [o["finding"] for o in (payload.get("opportunities") or [])][:3]
    if findings:
        fields["top_findings"] = " ".join(findings)
    if sp.get("total_display"):
        fields["estimated_ad_spend_monthly"] = sp["total_display"]
    stated = sp.get("stated") or {}
    if stated.get("band"):
        fields["stated_marketing_budget"] = stated["band"]
    intake = payload.get("intake") or {}
    for key in ("goal", "services", "areas", "traditional", "timeline"):
        if intake.get(key):
            fields[key] = _s(intake[key], 400)
    return fields
