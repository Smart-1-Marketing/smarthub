"""What a proposal commits us to: creative, launch tasks, monthly tasks.

`hub/proposal_execution.py` turns an uploaded proposal into a task graph the
scheduler drains. What it never produced is the three lists a person actually
wants the day the proposal is signed:

* **creative needed** -- every banner set, spot, video, image and piece of
  copy that has to exist before the campaign can run;
* **launch tasks** -- the one-time things that must happen before anything
  goes live;
* **monthly tasks** -- the promises the proposal makes on a recurring basis,
  which are the things a client notices when they stop.

This module builds those lists and nothing else. Three rules hold it up.

**Sizes come from the spec kit, never from here.** `hub/creative_specs.py`
is the transcription the upload manager judges every delivered file against
and `kit_drift()` holds it to the published page; a list of banner sizes
typed into this file would be the fourth copy of that table and the one no
check reads. Each recipe names the product string the kit already maps
(`kit_product`) and, where the kit's whole channel is wider than the buy,
the unit ids to keep -- the dimensions, formats and lengths are read through
`hub/creative_needs.required_units()`, the same reader the Proposal Builder's
creative gate uses, so the two cannot disagree about what a Meta buy needs.

**The model proposes, the rules decide, a person presses.** The rule-derived
items always exist, so the lists are complete and deterministic with no
OpenAI key at all. With one, the model is asked for the *specific* promises
-- "three :15/:30 commercials", "monthly delivery and banner-click report"
-- and every item it returns must quote the line it came from. A quote the
proposal does not contain is kept and **marked**, never silently trusted
and never silently dropped: a task nobody can find in the document is
exactly the one a person should look at before ticking. Nothing arrives
accepted. Each item is a proposal until somebody keeps or drops it, and
anything a person adds is theirs (`source: manual`) and is the one kind of
item that can be removed rather than merely dropped.

**What the proposal does not say is asked, not guessed.** A channel with no
dollar amount beside it, a campaign with no start date, a creative item
whose supplier the text does not name -- each becomes a question with the
reason it is being asked, and the answer lives beside the plan. A question
the text already answers (``"3 custom audio commercials"`` says who makes
the audio) is answered from the text and shown as such, because asking
somebody what the document in front of them says is how a question list
stops being read.

**A quote built in the Hub is read as data, and the creative is the gate's
own reading.** `hub/proposal_quote_facts.py` hands the analysis a `quote`
block -- the channels as rate-card line items, the start date, who
supplies each medium's files, the reporting cadence the document states.
Where it is present the creative for a channel comes from
`creative_needs.required_units()` over the quote's *actual* products rather
than from a recipe's representative one, and each of those facts arrives as
a question already answered, marked as the quote's, so a rep is not asked
what the document in front of them says.

**An answer is read by the work, or it was not worth asking.** `resolve()`
lays the answers over the plan at read time -- a launch date becomes a due
date on every launch task (each carries how many days before launch it has
to be done), a supplier becomes a mark on every creative item for that
channel, a reporting cadence lands on the report tasks -- and
`answers_for()` hands a brief or a packet the answers for its channel.
Derived, never stored: `hub/creative_evergreen.py`'s rule, because a date
written into the items would survive the answer changing and the two
gunicorn workers would disagree about which copy is current.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

LISTS = ("creative", "launch", "monthly")
LIST_LABELS = {"creative": "Creative needed", "launch": "Before launch",
               "monthly": "Every month"}

SOURCE_RULE = "rule"
SOURCE_AI = "ai"
SOURCE_MANUAL = "manual"

# Who supplies a creative item. Answered from the proposal's own wording
# where it says, asked otherwise.
SUPPLY_CHOICES = (
    ("smart1", "Smart 1 produces it"),
    ("client", "The client is supplying it"),
    ("mixed", "Some of each"),
)
_SUPPLY_SMART1 = ("custom", "we produce", "we will produce", "produced by smart 1",
                  "we create", "we will create", "we design", "we will design",
                  "production included", "creative included", "we write", "we will write")
_SUPPLY_CLIENT = ("client-provided", "client provided", "provided by the client",
                  "supplied by the client", "client to provide", "client to supply",
                  "client supplies", "client will provide", "existing creative")

MAX_AI_ITEMS = 25
MAX_TITLE = 160
MAX_DETAIL = 500
MAX_EVIDENCE = 300
MAX_ANSWER = 1000

# How a supplier answer and a cadence answer read on an item. One table,
# because the page, the brief and the handoff packet all print them.
SUPPLY_LABELS = {"smart1": "Smart 1 produces it", "client": "the client supplies it",
                 "mixed": "some of each"}
CADENCE_LABELS = {"monthly": "every month", "weekly": "every week",
                  "quarterly": "every quarter", "none": "no report is promised"}
# Creative has to be in hand before the launch tasks that traffic it can
# run. Two weeks is the house figure, not a platform's; a recipe's launch
# row carries its own lead time where one applies.
LEAD_DAYS_CREATIVE = 14

# ---------------------------------------------------------------------------
# What a monthly promise is, and what proves it landed.
#
# Every monthly recipe row names one of these. The kind is what joins a
# promise to the work log: `content` is proved by the SEO section writing a
# blog or filing schema, `social_post` by the planner exporting or pushing a
# batch, `video` by a commercial being approved -- each a module
# `hub/client_brand.WORK_KINDS` can already name, because a row the work log
# cannot attribute to a client is one this cannot read either. A kind with no
# evidence modules is **recorded by hand only**: nothing here logs that a
# report was sent to a client, so a person marks the month and the schedule
# says that is how it was recorded.
#
# `deliverable` is the line between what a client notices when it stops and
# what is our own housekeeping. A report, the month's content, a video, the
# posts, the sends: those are raised when a month goes by without them.
# Reviewing bids, checking frequency and confirming a game schedule are
# drawn on the plan and never raised as a finding -- a report that fires on
# "review search terms" for every client every month is the crying-wolf
# failure `QR_CODE_RULES` paid for, and it takes the real findings with it.
# Only spellings actually in use, the `ALIASES` rule: a kind is added here
# the day a recipe row or an evidence source needs it.
# ---------------------------------------------------------------------------
PROMISE_KINDS: dict[str, dict] = {
    "report": {"label": "Report to the client", "deliverable": True, "evidence": ()},
    "content": {"label": "SEO + AI work delivered", "deliverable": True,
                "evidence": (("seo", ("seo_blog_write", "seo_publish_instructions", "faq_page_saved",
                                      "schema_answers_saved", "seo_alt_write", "seo_task_created")),
                             ("seo_intelligence", ()),
                             ("suite", ("llms_txt_published",)))},
    "video": {"label": "Video produced", "deliverable": True,
              "evidence": (("commercial_builder", ("commercial_approved",)),
                           ("video_tools", ("_saved",)),
                           ("vox_explainer", ()), ("paint_animation", ()))},
    "social_plan": {"label": "Social calendar approved", "deliverable": True,
                    "evidence": (("social_planner", ("batch_approved", "sent_to_client")),)},
    "social_post": {"label": "Posts scheduled", "deliverable": True,
                    "evidence": (("social_planner", ("exported", "post_pushed")),)},
    "email": {"label": "Email sent", "deliverable": True,
              "evidence": (("skills360", ("email_batch_sent",)),)},
    "web": {"label": "Site maintenance", "deliverable": True, "evidence": ()},
    "creative_refresh": {"label": "Creative refresh", "deliverable": False,
                         "evidence": (("display_ads", ("creative_attached", "animation_attached")),
                                      ("magic_resize", ()), ("image_creator", ()))},
    "optimize": {"label": "Optimization", "deliverable": False, "evidence": ()},
    "review": {"label": "Promise review", "deliverable": False, "evidence": ()},
}
# A monthly item with no kind -- one the model found or a person typed -- is
# a promise somebody wrote down, so it is a deliverable recorded by hand: the
# Hub has no way to see it land and no grounds to call it housekeeping.
PROMISE_KIND_UNKNOWN = {"label": "Promise", "deliverable": True, "evidence": ()}


def promise_kind(kind: str) -> dict:
    """The table's entry for a kind, or the unknown-kind entry. Never raises."""
    return PROMISE_KINDS.get(str(kind or "")) or PROMISE_KIND_UNKNOWN


# ---------------------------------------------------------------------------
# The recipes: what each channel the analyzer can detect always needs.
#
# `kit_product` is the string `hub.creative_specs.channels_for_product()` is
# asked with, so the sizes come from the kit's own mapping rather than a
# second one here; `kit_units` narrows a channel whose kit section is wider
# than this buy (YouTube sells six formats and an in-market video buy is one
# skippable spot). `copy` is creative the kit has no unit for -- search ad
# copy, post captions -- described in words with no number to drift.
# ---------------------------------------------------------------------------
RECIPES: dict[str, dict] = {
    "retargeting": {
        "kit_product": "Website Retargeting",
        "kit_units": None,                       # the whole display size set
        "creative_title": "Retargeting banner set",
        "copy": [],
        "launch": [
            ("Confirm the retargeting pixel or tag is on every page of the site",
             "Retargeting cannot build an audience until the tag fires; check it before the flight starts.", 14),
            ("Build the site-visitor audience and set the lookback window", ""),
            ("Traffic the retargeting campaign with the approved banners and tagged destination links", ""),
            ("Click every banner size through to the landing page before launch", ""),
        ],
        "monthly": [
            ("Report retargeting delivery and click-through to the client", "", "report"),
            ("Check frequency and refresh the banners if the audience is seeing them too often", "", "creative_refresh"),
        ],
    },
    "paid_search": {
        "kit_product": "",
        "kit_units": None,
        "copy": [
            ("Paid Search ad copy and extensions",
             "Headlines and descriptions for each ad group, plus sitelinks, callouts and structured snippets, written to the primary CTA and the landing page."),
        ],
        "launch": [
            ("Build the Paid Search campaign structure: campaigns, ad groups, keywords and negatives", ""),
            ("Confirm conversion tracking fires on the form, call or booking the campaign is measured on", ""),
            ("Set the daily budget and bidding to match the proposal's monthly spend", ""),
            ("Get the ad copy approved before the campaign is enabled", "", 5),
        ],
        "monthly": [
            ("Review search terms, add negatives and adjust bids", "", "optimize"),
            ("Report Paid Search spend, clicks, conversions and cost per lead", "", "report"),
        ],
    },
    "seo_ai": {
        "kit_product": "",
        "kit_units": None,
        "copy": [],
        "launch": [
            ("Run the SEO + AI discovery audit and agree the prioritized workplan", ""),
            ("Confirm access to Search Console, Analytics and the website CMS", ""),
            ("Record the baseline rankings and traffic the monthly work is measured against", ""),
        ],
        "monthly": [
            ("Deliver the month's SEO + AI work: on-page fixes, schema, content and AI-search optimization", "", "content"),
            ("Report rankings, organic traffic and what was changed this month", "", "report"),
        ],
    },
    "stadium_audio": {
        "kit_product": "Stadium to Screen streaming audio",
        "kit_units": ["radio_audio", "radio_companion"],
        "copy": [],
        "launch": [
            ("Write the audio scripts and get them approved before voice production", "", 14),
            ("Produce and approve the finished audio spots", "", 7),
            ("Confirm the venue geo-fence, the game schedule and the flight dates", ""),
            ("Traffic the audio and companion banners with tagged destination links", ""),
        ],
        "monthly": [
            ("Confirm the coming month's game schedule and adjust the flight", "", "optimize"),
            ("Report audio delivery, completion rate and companion banner clicks", "", "report"),
        ],
    },
    "meta": {
        "kit_product": "Facebook | Instagram In-Market Home Buyers",
        "kit_units": ["facebook_image", "facebook_carousel_image", "stories_image"],
        "copy": [
            ("Meta ad copy: primary text, headlines and descriptions",
             "One set per ad, matched to the creative and the landing destination, within Meta's text limits."),
        ],
        "launch": [
            ("Confirm the Meta pixel or Conversions API is installed and firing", ""),
            ("Build the in-market audience and the geography in Ads Manager", ""),
            ("Get the carousel and image creative approved before the campaign is enabled", "", 7),
        ],
        "monthly": [
            ("Report Meta reach, clicks, leads and cost per lead", "", "report"),
            ("Refresh creative that is fatiguing and pause the weakest ads", "", "creative_refresh"),
        ],
    },
    "youtube_ads": {
        "kit_product": "YouTube In-Market Home Buyers",
        "kit_units": ["youtube_trueview"],
        "copy": [],
        "launch": [
            ("Link the YouTube channel to Google Ads and upload the approved spot", "", 3),
            ("Build the in-market audience and the geography", ""),
            ("Confirm view and conversion tracking before the campaign is enabled", ""),
        ],
        "monthly": [
            ("Report YouTube views, view rate, clicks and cost per view", "", "report"),
        ],
    },
    "social": {
        "kit_product": "",
        "kit_units": None,
        # The product is us making the posts; nobody has to be asked.
        "supplier": "smart1",
        "copy": [
            ("Social post graphics and captions for every scheduled post",
             "Built in Image Creator's Social sizes; captions written to the client's voice and approved before scheduling."),
        ],
        "launch": [
            ("Confirm access to the client's social accounts or their Smart 1 Suite sub-account", ""),
            ("Agree the posting channels, the mix and the first month's calendar", ""),
        ],
        "monthly": [
            ("Build next month's social content calendar and get it approved", "", "social_plan"),
            ("Schedule the approved posts", "", "social_post"),
        ],
    },
    "youtube_video": {
        "kit_product": "Monthly YouTube Sales Video",
        "kit_units": ["youtube_trueview"],
        "creative_title": "Monthly YouTube sales video",
        # The product is a video we produce every month.
        "supplier": "smart1",
        "copy": [],
        "launch": [
            ("Agree the sales video format, length and who appears on camera", ""),
        ],
        "monthly": [
            ("Script, produce and publish this month's YouTube sales video", "", "video"),
        ],
    },
    "youtube_optimization": {
        "kit_product": "",
        "kit_units": None,
        "copy": [],
        "launch": [
            ("Optimize the YouTube channel: art, about section, playlists, titles and descriptions",
             "A one-time option in the proposal; confirm the client took it before doing the work."),
        ],
        "monthly": [],
    },
    "ai_ads": {
        "kit_product": "ChatGPT / AI Advertising",
        "kit_units": ["gpt_ads_square"],
        "copy": [
            ("AI advertising ad copy",
             "Headline, body and call to action for the AI placement, written to the approved offer and landing page."),
        ],
        "launch": [
            ("Set up the AI advertising test with the approved budget, destination and tracking", ""),
        ],
        "monthly": [
            ("Report AI advertising delivery and results, and decide whether the test continues", "", "report"),
        ],
    },
    # ----------------------------------------------------------------------
    # The rate card's other families. A quote built in the Hub lands its
    # lines on these through hub/proposal_quote_facts.channel_for_item();
    # the text analyzer does not detect them yet, so `kit_product` here is
    # a representative product the kit maps, for the day it does. On the
    # quote path the channel's own products are what the kit is asked about.
    # ----------------------------------------------------------------------
    "display": {
        "kit_product": "Display - Category",
        "kit_units": None,
        "creative_title": "Display banner set",
        "copy": [],
        "launch": [
            ("Build the display audience, the geography and the frequency cap", ""),
            ("Get the banner set approved and click every size through to the landing page", "", 7),
            ("Traffic the display campaign with tagged destination links", ""),
        ],
        "monthly": [
            ("Report display delivery, viewability and click-through to the client", "", "report"),
            ("Rotate or refresh the banners where a size is under-performing", "", "creative_refresh"),
        ],
    },
    "ctv": {
        "kit_product": "Connected TV - Targeted",
        "kit_units": None,
        "creative_title": "Connected TV spot",
        "copy": [],
        "launch": [
            ("Confirm the spot lengths and formats against the buy before anything is trafficked", "", 14),
            ("Get the finished spot approved and QC'd for broadcast", "", 7),
            ("Build the household audience and the geography", ""),
            ("Traffic the spot and confirm the completion tracking", ""),
        ],
        "monthly": [
            ("Report impressions, completion rate and the households reached", "", "report"),
            ("Check the spot is not wearing out and plan the next cut if it is", "", "optimize"),
        ],
    },
    "digital_radio": {
        "kit_product": "Programmatic - Targeted digital radio",
        "kit_units": None,
        "creative_title": "Digital radio spot",
        "copy": [],
        "launch": [
            ("Write the audio script and get it approved before voice production", "", 14),
            ("Produce and approve the finished audio spot", "", 7),
            ("Build the audience, the geography and the daypart plan", ""),
            ("Traffic the audio and any companion banner with tagged destination links", ""),
        ],
        "monthly": [
            ("Report audio delivery, completion rate and companion banner clicks", "", "report"),
        ],
    },
    "paid_social": {
        "kit_product": "Facebook | Instagram - Paid Social Media Video Advertising",
        "kit_units": None,
        "copy": [
            ("Paid social ad copy: primary text, headlines and descriptions",
             "One set per ad, matched to the creative and the landing destination, within each platform's text limits."),
        ],
        "launch": [
            ("Confirm the platform pixel or conversions API is installed and firing", "", 14),
            ("Build the audience and the geography in the platform's ads manager", ""),
            ("Get the creative approved before the campaign is enabled", "", 7),
        ],
        "monthly": [
            ("Report paid social reach, clicks, leads and cost per lead", "", "report"),
            ("Refresh creative that is fatiguing and pause the weakest ads", "", "creative_refresh"),
        ],
    },
    "email": {
        "kit_product": "List Provided Email",
        "kit_units": None,
        "copy": [
            ("Email subject line, preheader and body copy",
             "Written to the approved offer and landing page, with the unsubscribe and sender details the send requires."),
        ],
        "launch": [
            ("Confirm the list source and that it may be mailed", "", 7),
            ("Get the email creative approved and test-render it on phone and desktop", "", 5),
            ("Schedule the send and confirm the tracking on every link", ""),
        ],
        "monthly": [
            ("Send the month's email and report delivered, opened and clicked", "", "email"),
        ],
    },
    "dooh": {
        "kit_product": "Digital Outdoor & Indoor Signage",
        "kit_units": None,
        "copy": [],
        "launch": [
            ("Confirm the screens, the venues and the dayparts on the buy", ""),
            ("Get the signage artwork approved for every screen size on the buy", "", 7),
            ("Traffic the artwork and confirm the flight dates with the network", ""),
        ],
        "monthly": [
            ("Report plays, venues and estimated impressions to the client", "", "report"),
        ],
    },
    "web": {
        "kit_product": "",
        "kit_units": None,
        "copy": [
            ("Website content: page copy, photography and logo files",
             "Everything the build needs from the client before design starts; the site cannot launch on placeholder copy."),
        ],
        "launch": [
            ("Agree the sitemap, the pages and who supplies the content", "", 14),
            ("Build the site, review it with the client and get sign-off", "", 3),
            ("Point the domain, confirm analytics and forms, and launch", ""),
        ],
        "monthly": [
            ("Confirm hosting, backups and updates ran, and report any site changes made", "", "web"),
        ],
    },
}

# Every run gets these whatever the channels are.
GENERIC_LAUNCH = [
    ("Confirm the signed proposal, the budget and the launch date with the client", ""),
    ("Confirm the landing page is live, loads on a phone and carries the primary call to action", "", 7),
    ("Confirm conversion tracking is in place before any spend starts", "", 7),
    ("Send the client a launch confirmation saying what goes live and when", ""),
]
GENERIC_MONTHLY = [
    ("Send the client the monthly performance report covering every channel", "", "report"),
    ("Check that spend is pacing to the monthly budget in the proposal", "", "optimize"),
    ("Review what the proposal promised for this month against what was delivered", "", "review"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:60]


def _norm(text: str) -> str:
    """Lowercase, one space, letters and digits only -- the comparison key
    for two titles that mean one thing, and for finding a quote in the
    proposal whatever its line breaks and punctuation were."""
    return re.sub(r"[^a-z0-9 ]+", " ", str(text or "").lower())


def _squash(text: str) -> str:
    return " ".join(_norm(text).split())


def _item(list_name: str, title: str, detail: str = "", *, channel: str = "",
          channel_name: str = "", source: str = SOURCE_RULE, evidence: str = "",
          grounded=None, key: str = "", kind: str = "", lead_days: int = 0) -> dict:
    title = " ".join(str(title or "").split())[:MAX_TITLE]
    ident = key or f"{list_name}:{channel or 'all'}:{_slug(title)}"
    row = {
        "id": ident, "list": list_name, "title": title,
        "detail": " ".join(str(detail or "").split())[:MAX_DETAIL],
        "channel": channel, "channel_name": channel_name,
        "source": source, "accepted": None,
        "evidence": str(evidence or "").strip()[:MAX_EVIDENCE],
        "grounded": grounded,
    }
    if list_name == "launch":
        # How many days before launch this has to be done. Zero is launch
        # day; `resolve()` turns it into a date once a launch date is known.
        try:
            row["lead_days"] = max(0, int(lead_days or 0))
        except (TypeError, ValueError):
            row["lead_days"] = 0
    if list_name == "creative":
        # A file (image, video, audio) has a supplier to ask about; copy is
        # always ours to write, and asking who supplies the ad copy is the
        # question that teaches people to stop reading the list.
        row["kind"] = kind or "file"
    if list_name == "monthly":
        # Which promise this is -- a report, the month's content, a video --
        # read by `hub/proposal_promises.py` to decide what proves it landed.
        # Blank for an item the model found or a person typed.
        row["kind"] = kind if kind in PROMISE_KINDS else ""
    return row


# ---------------------------------------------------------------------------
# Creative from the kit
# ---------------------------------------------------------------------------
def _launch_rows(rows) -> list[tuple[str, str, int]]:
    """A recipe's launch rows as (title, detail, lead_days) -- a row may be
    written with or without its lead time."""
    out = []
    for row in rows or []:
        title, detail = row[0], row[1] if len(row) > 1 else ""
        lead = row[2] if len(row) > 2 else 0
        out.append((title, detail, lead))
    return out


def _monthly_rows(rows) -> list[tuple[str, str, str]]:
    """A recipe's monthly rows as (title, detail, kind) -- a row may be
    written without its kind, and an unknown kind reads as none."""
    out = []
    for row in rows or []:
        title, detail = row[0], row[1] if len(row) > 1 else ""
        kind = row[2] if len(row) > 2 else ""
        out.append((title, detail, kind if kind in PROMISE_KINDS else ""))
    return out


def _kit_creative(key: str, recipe: dict, channel_name: str, *, state: dict | None = None,
                  medium: str = "") -> tuple[list[dict], str]:
    """The kit's units for one channel, as plan items. `(items, note)` --
    the note says when the kit maps nothing, so an empty creative list can
    be told from a channel that genuinely needs no file.

    Handed a `state` (the quote's own line items for this channel) the kit
    is asked about those products -- the Proposal Builder's creative gate's
    own reading. Without one the recipe's representative product stands in,
    which is the text path.
    """
    if state is None:
        product = recipe.get("kit_product") or ""
        if not product:
            return [], ""
        state = {"items": [{"product": product, "category": ""}]}
    try:
        from hub import creative_needs
        # A channel's lines are asked about medium by medium, because the
        # gate files a Snapchat buy under the card's video heading and a
        # display family carries a video product or two: asking for one
        # medium would find none of the lines of the other.
        media: list[str] = []
        for item in state.get("items") or [{}]:
            m = creative_needs.medium_of(item)
            if m not in media:
                media.append(m)
        units, notes = [], []
        for m in media:
            result = creative_needs.required_units(state, m)
            if result.get("measured"):
                for u in result["units"]:
                    if all(u["id"] != x["id"] for x in units):
                        units.append(u)
            elif result.get("note"):
                notes.append(result["note"])
        medium = media[0] if media else (medium or "other")
    except Exception as exc:                            # noqa: BLE001
        return [], f"The creative spec kit could not be read for {channel_name} ({type(exc).__name__})."
    if not units:
        return [], f"The spec kit maps no unit for {channel_name}" + (f" ({'; '.join(notes)})" if notes else ".")
    wanted = recipe.get("kit_units")
    if wanted:
        by_id = {u["id"]: u for u in units}
        units = [by_id[u] for u in wanted if u in by_id]
    if not units:
        return [], f"The spec kit maps no unit for {channel_name}."
    # A size-set channel (display) is one ask -- nine labels beside nine
    # sizes is the wall `units_line()` exists to avoid -- so it is one item
    # carrying the run. Everywhere else the format is the ask and each unit
    # is its own line somebody can keep or drop.
    try:
        from hub import creative_specs
        size_set = all(u.get("channel_id") in creative_specs.SIZE_SET_CHANNELS for u in units)
    except Exception:                                   # noqa: BLE001
        size_set = False
    items = []
    if size_set and wanted is None:
        line = creative_needs.units_line(state, medium)
        title = recipe.get("creative_title") or f"{channel_name} creative"
        items.append(_item("creative", title, line, channel=key, channel_name=channel_name,
                           kind="image"))
        return items, ""
    for unit in units:
        label = unit.get("label") or unit["id"]
        detail = creative_needs._describe_unit(unit)
        # One unit is the channel's one ask and is named for itself -- the
        # channel is on the tag beside it, and "Connected TV: Connected TV"
        # says one thing twice. Several are told apart by the channel.
        if len(units) == 1:
            title = recipe.get("creative_title") or label
        else:
            title = f"{channel_name}: {label}"
        items.append(_item("creative", title, detail, channel=key, channel_name=channel_name,
                           key=f"creative:{key}:{unit['id']}", kind=unit.get("kind") or "image"))
    return items, ""


def _social_sizes_note() -> str:
    """Image Creator's own Social presets, read rather than retyped."""
    try:
        from modules.image_creator.app import CANVAS_PRESETS
        sizes = [f"{p['label']} {p['w']}x{p['h']}" for p in CANVAS_PRESETS
                 if p.get("group") == "Social" and p.get("w")]
        if sizes:
            return "Sizes from Image Creator: " + ", ".join(sizes) + "."
    except Exception:                                   # noqa: BLE001
        pass
    return ""


def rule_items(analysis: dict, client: str = "") -> tuple[dict, list[str]]:
    """The deterministic half: every list built from the channels detected.

    Returns `({"creative": [...], "launch": [...], "monthly": [...]}, notes)`.
    """
    channels = [c for c in (analysis or {}).get("channels") or [] if isinstance(c, dict)]
    quote = (analysis or {}).get("quote") if isinstance((analysis or {}).get("quote"), dict) else None
    out = {name: [] for name in LISTS}
    notes: list[str] = []
    for title, detail, lead in _launch_rows(GENERIC_LAUNCH):
        out["launch"].append(_item("launch", title, detail, lead_days=lead))
    for ch in channels:
        key = str(ch.get("key") or "other")
        name = str(ch.get("name") or key.replace("_", " ").title())
        recipe = RECIPES.get(key)
        if not recipe and not quote:
            notes.append(f"{name} is not a channel this Hub has a recipe for, so its creative "
                         f"and tasks are asked about rather than listed.")
            continue
        recipe = recipe or {}
        if quote and ch.get("products"):
            # The quote's own lines for this channel are what the kit is
            # asked about -- a Connected TV buy gets the kit's CTV units
            # whatever representative product the recipe names, and a
            # channel the gate treats as copy-only (search, SEO) gets none.
            if recipe.get("kit_product") or not recipe:
                kit_items, kit_note = _kit_creative(
                    key, recipe, name, state={"items": list(ch["products"])},
                    medium=str(ch.get("medium") or ""))
            else:
                kit_items, kit_note = [], ""
        else:
            kit_items, kit_note = _kit_creative(key, recipe, name)
        out["creative"].extend(kit_items)
        if kit_note:
            notes.append(kit_note)
        for title, detail in recipe.get("copy") or []:
            kind = "copy"
            if key == "social":
                # Post graphics are files somebody has to make or hand over,
                # so this one is asked about like a banner is.
                detail = f"{detail} {_social_sizes_note()}".strip()
                kind = "image"
            elif key == "web":
                kind = "image"
            out["creative"].append(_item("creative", title, detail, channel=key,
                                         channel_name=name, kind=kind))
        for title, detail, lead in _launch_rows(recipe.get("launch")):
            out["launch"].append(_item("launch", title, detail, channel=key, channel_name=name,
                                       lead_days=lead))
        for title, detail, kind in _monthly_rows(recipe.get("monthly")):
            out["monthly"].append(_item("monthly", title, detail, channel=key, channel_name=name,
                                        kind=kind))
    if quote:
        # Lines on the quote that are not a campaign -- a production line,
        # a tracking number, a list purchase -- are still work somebody does
        # before launch. Named rather than folded into a channel; a fee is
        # left alone, because nobody sets up a management fee.
        for line in quote.get("other_lines") or []:
            category = str(line.get("category") or "").upper()
            label = str(line.get("label") or line.get("product") or "").strip()
            if not label or category in ("MANAGEMENT", "CONSULTING"):
                continue
            if category == "CREATIVE / DESIGN SERVICES":
                out["launch"].append(_item("launch", f"Produce the {label} the quote sells",
                                           line.get("description") or "", lead_days=7))
            elif category == "ADD-ON PRODUCT":
                out["launch"].append(_item("launch", f"Set up {label}",
                                           line.get("description") or "A line on the quote that is not a campaign of its own.",
                                           lead_days=3))
    for title, detail, kind in _monthly_rows(GENERIC_MONTHLY):
        out["monthly"].append(_item("monthly", title, detail, kind=kind))
    return out, notes


# ---------------------------------------------------------------------------
# The model's half
# ---------------------------------------------------------------------------
def _extract_json(text: str):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except ValueError:
        return {}


def _prompt(text: str, client: str, channel_keys: list[str]) -> str:
    keys = "|".join(channel_keys + ["other"]) if channel_keys else "other"
    return f"""Read this marketing proposal and return ONLY JSON, no prose.
Schema:
{{"creative":[{{"title":"","detail":"","channel_key":"{keys}","evidence":""}}],
"launch":[{{"title":"","detail":"","channel_key":"","evidence":""}}],
"monthly":[{{"title":"","detail":"","channel_key":"","evidence":""}}],
"supply":[{{"channel_key":"","who":"smart1|client|mixed","evidence":""}}],
"unclear":[{{"question":"","why":""}}]}}
Rules:
- creative: every ad, video, audio spot, banner, image, script or piece of copy the proposal says will exist, with the count, length or size exactly as the proposal states it.
- launch: one-time things that must happen before the campaign goes live, as the proposal promises them.
- monthly: anything the proposal promises every month or on a recurring schedule (reports, videos, posts, optimization, maintenance).
- Each title is one short imperative sentence a person can act on. Keep it under 120 characters.
- evidence is the exact line from the proposal that says so, copied verbatim. Never invent a promise, a number, a date or a deliverable. If the proposal does not say, put the open question in "unclear" with why it matters instead.
- supply: for each channel where the proposal says who makes the creative (Smart 1 produces it, the client provides it, or some of each), say so with the line that says it. Leave a channel out when the proposal does not say.
- Do not list generic agency housekeeping; list what THIS proposal commits to.
Client: {client}
Proposal:
{text[:50000]}"""


def _grounded(evidence: str, text_key: str) -> bool:
    quote = _squash(evidence)
    if len(quote) < 12:
        return False
    if quote in text_key:
        return True
    # A model trims a long line; the first clause is still the quote.
    head = " ".join(quote.split()[:8])
    return len(head) >= 12 and head in text_key


def ai_items(text: str, client: str, channel_keys: list[str], *, ask=None) -> tuple[dict, list[dict], dict, str]:
    """Ask the model for the proposal's specific promises.

    Returns `(items_by_list, unclear, supply, note)`. `supply` maps a
    channel key to `{"who", "evidence"}` for the channels whose supplier
    the proposal names in a line the text actually contains. Never raises:
    a model that is unavailable costs the specific promises and not the
    plan, and the note says so in words.
    """
    empty = {name: [] for name in LISTS}
    if ask is None:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            return empty, [], {}, ("AI review of the proposal's promises was not run (OPENAI_API_KEY is not set). "
                                   "The lists come from the channels detected; add anything the proposal promises that is missing.")
        try:
            from hub.openai_responses import ask as _ask
            ask = _ask
        except Exception as exc:                        # noqa: BLE001
            return empty, [], {}, f"AI review of the proposal's promises was not run ({type(exc).__name__})."
    try:
        raw = ask(_prompt(text, client, channel_keys), module="proposal_execution",
                  purpose="proposal_plan", max_output_tokens=6000)
        parsed = _extract_json(raw)
    except Exception as exc:                            # noqa: BLE001
        return empty, [], {}, f"AI review of the proposal's promises was not run ({type(exc).__name__})."
    if not isinstance(parsed, dict):
        return empty, [], {}, "AI review of the proposal's promises returned nothing readable."
    text_key = _squash(text)
    allowed = set(channel_keys) | {"other"}
    out = {name: [] for name in LISTS}
    for name in LISTS:
        rows = parsed.get(name) or []
        if not isinstance(rows, list):
            continue
        for row in rows[:MAX_AI_ITEMS]:
            if not isinstance(row, dict):
                continue
            title = " ".join(str(row.get("title") or "").split())
            if not title:
                continue
            key = str(row.get("channel_key") or "").strip()
            if key not in allowed:
                key = "other"
            evidence = str(row.get("evidence") or "")
            out[name].append(_item(name, title, row.get("detail") or "", channel=key,
                                   channel_name="", source=SOURCE_AI, evidence=evidence,
                                   grounded=_grounded(evidence, text_key),
                                   key=f"{name}:ai:{_slug(title)}"))
    unclear = []
    for row in (parsed.get("unclear") or [])[:MAX_AI_ITEMS]:
        if isinstance(row, dict) and str(row.get("question") or "").strip():
            unclear.append({"question": " ".join(str(row["question"]).split())[:MAX_TITLE],
                            "why": " ".join(str(row.get("why") or "").split())[:MAX_DETAIL]})
    # A supplier is believed only when the quote behind it is in the text:
    # "the client is supplying the video" on the model's say-so is the
    # question this exists to ask, answered by a guess.
    supply = {}
    valid = {v for v, _ in SUPPLY_CHOICES}
    for row in (parsed.get("supply") or [])[:MAX_AI_ITEMS]:
        if not isinstance(row, dict):
            continue
        key, who = str(row.get("channel_key") or ""), str(row.get("who") or "").strip().lower()
        evidence = str(row.get("evidence") or "")
        if key in allowed and key != "other" and who in valid and _grounded(evidence, text_key):
            supply[key] = {"who": who, "evidence": evidence.strip()[:MAX_EVIDENCE]}
    return out, unclear, supply, ""


def _merge(rule: dict, ai: dict) -> dict:
    """Rule items first, AI items after, one item per title.

    An AI item whose title matches a rule item is folded into it -- the rule
    item gains the quote, which is the better outcome: the list stays the
    same length and the item now says where the proposal promised it.
    """
    merged = {}
    for name in LISTS:
        seen = {}
        rows = []
        for it in rule.get(name) or []:
            seen[_squash(it["title"])] = it
            rows.append(it)
        for it in ai.get(name) or []:
            k = _squash(it["title"])
            if k in seen:
                dup = seen[k]
                if it.get("evidence") and not dup.get("evidence"):
                    dup["evidence"] = it["evidence"]
                    dup["grounded"] = it.get("grounded")
                    dup["also_ai"] = True
                continue
            if it["id"] in {r["id"] for r in rows}:
                it["id"] = it["id"] + "-" + hashlib.sha1(k.encode()).hexdigest()[:6]
            seen[k] = it
            rows.append(it)
        merged[name] = rows
    return merged


# ---------------------------------------------------------------------------
# What the proposal does not say
# ---------------------------------------------------------------------------
def _channel_lines(text: str, channel: dict) -> list[str]:
    """The proposal's own lines about one channel, for reading who supplies
    the creative and for quoting back."""
    try:
        from hub.proposal_execution import CHANNEL_PATTERNS
        patterns = CHANNEL_PATTERNS.get(channel.get("key") or "", ())
    except Exception:                                   # noqa: BLE001
        patterns = ()
    name = str(channel.get("name") or "").lower()
    needles = tuple(p for p in patterns) + ((name,) if name else ())
    out = []
    for line in str(text or "").splitlines():
        low = line.lower()
        if any(n and n in low for n in needles):
            out.append(line.strip())
    return out


def _supply_from_text(lines: list[str]) -> tuple[str, str]:
    for line in lines:
        low = line.lower()
        if any(w in low for w in _SUPPLY_CLIENT):
            return "client", line
        if any(w in low for w in _SUPPLY_SMART1):
            return "smart1", line
    return "", ""


def questions(analysis: dict, items: dict, text: str, client: str,
              unclear: list[dict] | None = None, answers: dict | None = None,
              supply: dict | None = None) -> list[dict]:
    """Everything the plan needs that the proposal does not state.

    Each carries `why`, because a question with no reason on it is one
    people answer with a guess. An answer already held on the plan rides
    along as `answer`; one read off the proposal's own wording is marked
    `from_text` so the screen shows it as the document's answer rather than
    ours.
    """
    answers = answers or {}
    supply = supply or {}
    out: list[dict] = []
    channels = [c for c in (analysis or {}).get("channels") or [] if isinstance(c, dict)]
    low = str(text or "").lower()
    quote = (analysis or {}).get("quote") if isinstance((analysis or {}).get("quote"), dict) else None
    quote_supply = (quote or {}).get("supply") or {}
    document = "quote" if quote else "proposal"

    def add(key, question, why, *, type_="text", options=None, from_text="", evidence="",
            source=""):
        row = {"key": key, "question": question, "why": why, "type": type_,
               "answer": answers.get(key, "") if key in answers else (from_text or ""),
               "from_text": bool(from_text) and key not in answers, "evidence": evidence,
               # Which document answered, so the screen can say "from the
               # quote" rather than "from the proposal" about a quote.
               "source_label": (source or document) if from_text else ""}
        if options:
            row["options"] = [{"value": v, "label": l} for v, l in options]
        out.append(row)

    # The launch date is always asked: every launch task is measured from
    # it. A quote's own start date answers it, marked as the quote's; a
    # text proposal carrying flight dates is answered from the first one.
    start = str((quote or {}).get("start_date") or "").strip()
    dates = [str(d) for d in (analysis or {}).get("flight_dates") or [] if str(d).strip()]
    if start:
        add("launch_date", "When does the campaign launch?",
            "Every launch task is measured from the start date.",
            from_text=start, evidence=f"Start date on the quote: {start}.", source="quote")
    elif dates:
        add("launch_date", "When does the campaign launch?",
            "Every launch task is measured from the start date.",
            from_text=dates[0], evidence=f"The proposal's flight dates: {', '.join(dates[:3])}.")
    else:
        add("launch_date", "When does the campaign launch?",
            f"The {document} carries no start date, and every launch task is measured from one.")

    creative_channels = {}
    for it in items.get("creative") or []:
        if it.get("channel") and it["channel"] != "other" and it.get("kind") != "copy":
            creative_channels.setdefault(it["channel"], it.get("channel_name") or it["channel"])
    by_key = {c.get("key"): c for c in channels}
    for key, name in creative_channels.items():
        # Three readings, in order of how much they can be trusted: the
        # product itself includes production (the recipe says so), the
        # model quoted a line that is really in the text, or a line naming
        # the channel says who makes it. Any of them is the document's
        # answer; none of them stops a person changing it.
        recipe = RECIPES.get(key) or {}
        source = ""
        if key in quote_supply:
            # The quote's creative step, or a production line on it: the
            # rep answered this on the proposal, and asking again is asking
            # what the document in front of them says.
            inferred, line = quote_supply[key]["who"], quote_supply[key]["evidence"]
            source = "quote"
        elif recipe.get("supplier"):
            inferred, line = recipe["supplier"], "This product includes production."
        elif key in supply:
            inferred, line = supply[key]["who"], supply[key]["evidence"]
        else:
            lines = _channel_lines(text, by_key.get(key) or {"key": key, "name": name})
            inferred, line = _supply_from_text(lines)
        add(f"creative_supply:{key}",
            f"Who is supplying the {name} creative?",
            "The plan lists what has to exist; whether the client hands it over or Smart 1 produces it "
            "decides whether the launch tasks include production.",
            type_="choice", options=SUPPLY_CHOICES, from_text=inferred, evidence=line, source=source)

    for ch in channels:
        key = str(ch.get("key") or "other")
        name = str(ch.get("name") or key)
        if not ch.get("budgets"):
            add(f"budget:{key}", f"What is the monthly budget for {name}?",
                f"The {document} names the channel and no dollar amount was found beside it.")
        if key == "other" or key not in RECIPES:
            add(f"creative_for:{_slug(name)}", f"What creative does {name} need?",
                "This Hub has no recipe for that channel, so nothing was listed for it rather than guessing.")

    cadence_options = (("monthly", "Monthly"), ("weekly", "Weekly"),
                       ("quarterly", "Quarterly"), ("none", "No report is promised"))
    reporting = (quote or {}).get("reporting") or {}
    if quote:
        # A quote always has a Reporting section, so "the word report
        # appears" proves nothing; what counts is whether the section names
        # a cadence. One it names is the quote's answer; none is a question.
        if reporting.get("cadence"):
            add("reporting_cadence", f"How often does {client or 'the client'} get a performance report?",
                "The monthly report is on the task list; the cadence decides how often.",
                type_="choice", options=cadence_options, from_text=reporting["cadence"],
                evidence=reporting.get("evidence") or "", source="quote")
        else:
            add("reporting_cadence", f"How often does {client or 'the client'} get a performance report?",
                "The quote's Reporting section names no cadence, and a monthly report is on the task list by default.",
                type_="choice", options=cadence_options)
    elif "report" not in low:
        add("reporting_cadence", f"How often does {client or 'the client'} get a performance report?",
            "The proposal does not mention reporting, and a monthly report is on the task list by default.",
            type_="choice", options=cadence_options)

    for row in unclear or []:
        key = f"ai:{_slug(row.get('question') or '')}"
        if any(q["key"] == key for q in out):
            continue
        add(key, row.get("question") or "", row.get("why") or "The proposal does not say.")
    return out


# ---------------------------------------------------------------------------
# Build, decide, summarize
# ---------------------------------------------------------------------------
def build_plan(analysis: dict, text: str, client: str = "", *, ask=None, use_ai: bool = True) -> dict:
    """The whole plan for one run. Deterministic without a model; the model
    adds the proposal's specific promises where it is available."""
    rule, notes = rule_items(analysis, client)
    channel_keys = [str(c.get("key")) for c in (analysis or {}).get("channels") or []
                    if isinstance(c, dict) and c.get("key") and c.get("key") != "other"]
    ai = {name: [] for name in LISTS}
    unclear: list[dict] = []
    supply: dict = {}
    source = "rules"
    if use_ai:
        ai, unclear, supply, note = ai_items(text, client, channel_keys, ask=ask)
        if note:
            notes.append(note)
        elif any(ai.values()) or unclear or supply:
            source = "rules+ai"
    items = _merge(rule, ai)
    plan = {
        "creative": items["creative"], "launch": items["launch"], "monthly": items["monthly"],
        "answers": {}, "source": source, "notes": notes, "built_at": _now_iso(),
    }
    plan["questions"] = questions(analysis, items, text, client, unclear, plan["answers"], supply)
    plan["summary"] = summarize(plan)
    return plan


def _all_items(plan: dict) -> list[dict]:
    return [it for name in LISTS for it in (plan.get(name) or [])]


def apply_decisions(plan: dict, decisions: dict, *, known_owners=None, actor: str = "") -> dict:
    """A person's review of the plan, applied in one press.

    `decisions` may carry `accept` ({id: true|false|null}), `add`
    ([{list, title, detail}]), `remove` ([ids], manual items only),
    `answers` ({key: value}), `owners` ({id: email}, blank to follow
    the client's owner again) and `done` ({id: true|false}, launch and
    creative items only -- a monthly promise is marked month by month).
    Anything it cannot apply is refused by name with a ValueError, and
    nothing is half-applied. `actor` goes onto a done mark, because a
    mark nobody can attribute is one nobody can revisit.

    `known_owners` is the set of account emails an item may be given to.
    Handed in by the caller that can read the account table, because this
    module reads no database; `None` means the table could not be read,
    and a well-formed address is then taken as typed rather than every
    assignment being refused over a table that blinked.
    """
    plan = json.loads(json.dumps(plan or {}))
    decisions = decisions or {}
    by_id = {it["id"]: it for it in _all_items(plan)}

    accept = decisions.get("accept") or {}
    if not isinstance(accept, dict):
        raise ValueError("accept must map item ids to true, false or null.")
    for ident, value in accept.items():
        it = by_id.get(str(ident))
        if not it:
            raise ValueError(f"No plan item has the id {ident!r}.")
        if value not in (True, False, None):
            raise ValueError(f"{ident!r}: accepted must be true, false or null.")
        it["accepted"] = value

    for row in decisions.get("add") or []:
        if not isinstance(row, dict):
            raise ValueError("Each added item must be an object with list and title.")
        list_name = str(row.get("list") or "").strip()
        if list_name not in LISTS:
            raise ValueError(f"Unknown list {list_name!r}; choose one of {', '.join(LISTS)}.")
        title = " ".join(str(row.get("title") or "").split())
        if not title:
            raise ValueError("An added item needs a title.")
        it = _item(list_name, title, row.get("detail") or "", channel=str(row.get("channel") or ""),
                   source=SOURCE_MANUAL,
                   key=f"{list_name}:manual:{hashlib.sha1(_squash(title).encode()).hexdigest()[:10]}")
        if it["id"] in by_id:
            raise ValueError(f"{title!r} is already on the {LIST_LABELS[list_name]} list.")
        it["accepted"] = True
        plan.setdefault(list_name, []).append(it)
        by_id[it["id"]] = it

    for ident in decisions.get("remove") or []:
        it = by_id.get(str(ident))
        if not it:
            raise ValueError(f"No plan item has the id {ident!r}.")
        if it.get("source") != SOURCE_MANUAL:
            raise ValueError(f"Only an item you added can be removed. Mark {it['title']!r} as not needed instead.")
        plan[it["list"]] = [r for r in plan.get(it["list"]) or [] if r["id"] != it["id"]]
        by_id.pop(it["id"], None)

    answers = decisions.get("answers") or {}
    if not isinstance(answers, dict):
        raise ValueError("answers must map question keys to values.")
    known = {q["key"] for q in plan.get("questions") or []}
    stored = dict(plan.get("answers") or {})
    for key, value in answers.items():
        key = str(key)
        if key not in known:
            raise ValueError(f"{key!r} is not a question on this plan.")
        value = " ".join(str(value if value is not None else "").split())[:MAX_ANSWER]
        if value:
            stored[key] = value
        else:
            stored.pop(key, None)
    plan["answers"] = stored
    for q in plan.get("questions") or []:
        if q["key"] in stored:
            q["answer"], q["from_text"] = stored[q["key"]], False
        elif not q.get("from_text"):
            q["answer"] = ""

    # An owner set on the item itself. Stored as `owner_override` and
    # nothing else: the default -- the client's owner, through
    # hub/client_owner.py -- is laid over on read by `resolve()`, so a
    # handover of the client moves every item that was following them and
    # leaves the ones somebody named by hand exactly where they were.
    owners = decisions.get("owners") or {}
    if not isinstance(owners, dict):
        raise ValueError("owners must map item ids to an email address, or blank to follow the client's owner.")
    for ident, value in owners.items():
        it = by_id.get(str(ident))
        if not it:
            raise ValueError(f"No plan item has the id {ident!r}.")
        addr = " ".join(str(value if value is not None else "").split()).lower()
        if addr and "@" not in addr:
            raise ValueError(f"{addr!r} is not an email address.")
        if addr and known_owners is not None and addr not in known_owners:
            raise ValueError(f"{addr} is not a Hub account this can be given to.")
        if addr:
            it["owner_override"] = addr
        else:
            it.pop("owner_override", None)

    # A done mark on a launch task or a creative item: who and when, stored
    # on the item and the one thing hub/proposal_progress.py ever writes.
    # Applied after `accept`, so keeping and finishing an item is one
    # press. Only a kept item can be done -- a tick on an item nobody kept
    # is a tick on nothing -- and a monthly promise is refused by name,
    # because that one is done month by month on its own strip.
    done = decisions.get("done") or {}
    if not isinstance(done, dict):
        raise ValueError("done must map item ids to true or false.")
    for ident, value in done.items():
        it = by_id.get(str(ident))
        if not it:
            raise ValueError(f"No plan item has the id {ident!r}.")
        if value not in (True, False):
            raise ValueError(f"{ident!r}: done must be true or false.")
        if it.get("list") == "monthly":
            raise ValueError(f"{it['title']!r} is a monthly promise; mark the month done on its strip instead.")
        if value:
            if it.get("accepted") is not True:
                raise ValueError(f"Keep {it['title']!r} before marking it done; a done mark on an item nobody kept is a tick on nothing.")
            it["done"] = {"by": " ".join(str(actor or "").split())[:120], "at": _now_iso()}
        else:
            it.pop("done", None)
    plan["reviewed_at"] = _now_iso()
    plan["summary"] = summarize(plan)
    return plan


def carry_forward(new_plan: dict, old_plan: dict) -> dict:
    """What a person decided on the run being superseded, laid over the plan
    built from the new document.

    A kept or dropped verdict follows an item that is still proposed (same
    id); an item the new document no longer proposes takes its verdict with
    it, because a decision about a promise that is gone is about nothing.
    Items a person added come across whole -- they were never the
    analyzer's to lose. Answers follow the questions still being asked.
    """
    if not old_plan:
        return new_plan
    plan = json.loads(json.dumps(new_plan or {}))
    old_by_id = {it["id"]: it for it in _all_items(old_plan)}
    for name in LISTS:
        ids = {it["id"] for it in plan.get(name) or []}
        for it in plan.get(name) or []:
            prior = old_by_id.get(it["id"])
            if prior is not None and prior.get("accepted") is not None:
                it["accepted"] = prior["accepted"]
            # An owner named on the item travels with the verdict: it was a
            # decision about this piece of work, and the work is still here.
            if prior is not None and prior.get("owner_override"):
                it["owner_override"] = prior["owner_override"]
            # So does a done mark: the work was finished, and it is the
            # same work. A mark on an item the new document no longer
            # proposes goes with the item, like its verdict.
            if prior is not None and prior.get("done"):
                it["done"] = json.loads(json.dumps(prior["done"]))
        for prior in old_plan.get(name) or []:
            if prior.get("source") == SOURCE_MANUAL and prior["id"] not in ids:
                plan.setdefault(name, []).append(json.loads(json.dumps(prior)))
    asked = {q["key"] for q in plan.get("questions") or []}
    carried = {k: v for k, v in (old_plan.get("answers") or {}).items() if k in asked}
    plan["answers"] = carried
    for q in plan.get("questions") or []:
        if q["key"] in carried:
            q["answer"], q["from_text"] = carried[q["key"]], False
    # What the client told us follows the question it answered, for the
    # same reason: a launch date they gave on their page is about this
    # campaign, and the new document has not changed who they are.
    plan["client_answers"] = {k: json.loads(json.dumps(v))
                              for k, v in (old_plan.get("client_answers") or {}).items() if k in asked}
    # And the link they were sent. It is in an email on their side, so it
    # has to open the plan that replaced this one rather than one marked
    # superseded -- and a second link minted for the new run would be two
    # live addresses for one client. Carried as it stands, revoked state
    # included: a link somebody took back stays taken back.
    if old_plan.get("client_link"):
        plan["client_link"] = json.loads(json.dumps(old_plan["client_link"]))
    plan["summary"] = summarize(plan)
    return plan


def summarize(plan: dict) -> dict:
    """Counts for the headline, per list and overall -- kept, dropped and
    still to review are three numbers because they are three questions."""
    out = {"lists": {}, "open_questions": 0, "to_review": 0}
    for name in LISTS:
        rows = plan.get(name) or []
        kept = sum(1 for r in rows if r.get("accepted") is True)
        dropped = sum(1 for r in rows if r.get("accepted") is False)
        review = sum(1 for r in rows if r.get("accepted") is None)
        out["lists"][name] = {"label": LIST_LABELS[name], "total": len(rows),
                              "kept": kept, "dropped": dropped, "to_review": review}
        out["to_review"] += review
    out["open_questions"] = sum(1 for q in plan.get("questions") or [] if not str(q.get("answer") or "").strip())
    out["unverified"] = sum(1 for it in _all_items(plan)
                            if it.get("source") == SOURCE_AI and it.get("grounded") is False)
    # What the client answered on their page and nobody has taken onto the
    # plan yet. A reply that arrived and was read by nothing is the form
    # field failure one audience further out.
    out["client_answers_pending"] = sum(1 for row in client_answers_view(plan) if not row["taken"])
    return out


def kept_items(plan: dict, list_name: str) -> list[dict]:
    """What a person has kept on one list -- the only half a downstream tool
    may read. An item nobody has reviewed is still a proposal."""
    return [it for it in (plan or {}).get(list_name) or [] if it.get("accepted") is True]


# ---------------------------------------------------------------------------
# Answers, read by the work
# ---------------------------------------------------------------------------
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y",
                 "%b %d %Y", "%Y-%m-%dT%H:%M:%S", "%m-%d-%Y", "%d %B %Y")


def parse_day(value) -> date | None:
    """A typed or stored date as a `date`, or None. Never raises: an answer
    nothing can read is an answer, and a launch date the page cannot place
    costs the due dates rather than the plan."""
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text[:len(text)], fmt).date()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if m:
        try:
            return datetime.strptime(m.group(0), "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _day_label(day: date) -> str:
    return day.strftime("%b ") + str(day.day) + (day.strftime(", %Y") if day.year != date.today().year else "")


def _answer_of(plan: dict, key: str) -> str:
    """A question's answer as it stands: what a person typed, else what the
    document itself said. The same reading the screen shows."""
    stored = (plan or {}).get("answers") or {}
    if key in stored:
        return str(stored[key] or "")
    for q in (plan or {}).get("questions") or []:
        if q.get("key") == key:
            return str(q.get("answer") or "")
    return ""


def owner_label(email: str, names: dict | None = None) -> str:
    """The name to print for an owner, or the address where no account is
    known -- `hub/client_owner.display_name()`'s rule, read from a
    `{email: name}` index handed in so this module opens no table. Never
    invents a name from the address: `todd@` is not "Todd"."""
    email = str(email or "").strip().lower()
    if not email:
        return ""
    return str((names or {}).get(email) or "") or email


def resolve(plan: dict, *, owner: dict | None = None, names: dict | None = None) -> dict:
    """The plan with its answers applied, for reading -- never for storing.

    A launch date becomes a due date on every launch task and creative item
    (each launch row carries the days before launch it needs; creative is
    wanted `LEAD_DAYS_CREATIVE` ahead); a supplier answer becomes a mark on
    every creative item of its channel; a reporting cadence lands on the
    report tasks. `resolved` carries the answers themselves so a brief or a
    packet reads one dict rather than walking the questions.

    `owner` is the client's owner as `hub/client_owner.owner_of()` answers
    it (or None), and `names` an `{email: name}` index; both are handed in
    because this module reads no table. Every item then carries `owner`,
    `owner_label` and `owner_source` -- `item` where somebody named one on
    the item, `client` where it follows the client's owner -- and an item
    with neither carries no owner at all rather than a guess.

    Derived on every read and written nowhere: a date baked into the items
    would outlive the answer that produced it, and there are two gunicorn
    workers to disagree about which copy is current.
    """
    plan = json.loads(json.dumps(plan or {}))
    default = str((owner or {}).get("email") or "").strip().lower()
    launch_raw = _answer_of(plan, "launch_date")
    launch = parse_day(launch_raw)
    cadence = _answer_of(plan, "reporting_cadence")
    resolved = {"launch_date": launch.isoformat() if launch else "",
                "launch_date_raw": launch_raw, "launch_date_label": _day_label(launch) if launch else "",
                "reporting_cadence": cadence,
                "reporting_cadence_label": CADENCE_LABELS.get(cadence, cadence),
                "supply": {}, "budgets": {}, "unreadable_launch_date": bool(launch_raw and not launch)}
    for q in plan.get("questions") or []:
        key = str(q.get("key") or "")
        value = _answer_of(plan, key)
        if not value:
            continue
        if key.startswith("creative_supply:"):
            resolved["supply"][key.split(":", 1)[1]] = value
        elif key.startswith("budget:"):
            resolved["budgets"][key.split(":", 1)[1]] = value
    for it in plan.get("creative") or []:
        who = resolved["supply"].get(it.get("channel") or "")
        if who and it.get("kind") != "copy":
            it["supplier"] = who
            it["supplier_label"] = SUPPLY_LABELS.get(who, who)
        if launch:
            due = launch - timedelta(days=LEAD_DAYS_CREATIVE)
            it["due"] = due.isoformat()
            it["due_label"] = f"in hand by {_day_label(due)}, {LEAD_DAYS_CREATIVE} days before launch"
    for it in plan.get("launch") or []:
        if not launch:
            continue
        lead = int(it.get("lead_days") or 0)
        due = launch - timedelta(days=lead)
        it["due"] = due.isoformat()
        it["due_label"] = f"by {_day_label(due)}" + (f", {lead} days before launch" if lead else " (launch day)")
    for it in plan.get("monthly") or []:
        if launch:
            first = (launch.replace(day=1) + timedelta(days=32)).replace(day=1)
            it["starts"] = first.isoformat()
            it["due_label"] = f"first due {first.strftime('%B %Y')}"
        if cadence and "report" in str(it.get("title") or "").lower():
            it["cadence"] = cadence
            it["cadence_label"] = CADENCE_LABELS.get(cadence, cadence)
    # The client's own answer beside the question it answers -- a proposal
    # until a person takes it, and marked `taken` once the plan carries the
    # same value, so the screen offers a press rather than a second box.
    proposed = {row["key"]: row for row in client_answers_view(plan)}
    for q in plan.get("questions") or []:
        row = proposed.get(str(q.get("key") or ""))
        if row:
            q["client_proposed"] = {k: row[k] for k in ("value", "label", "by", "at", "taken")}
    resolved["client_answers_pending"] = sum(1 for row in proposed.values() if not row["taken"])
    resolved["owner"] = ({"email": default, "label": owner_label(default, names),
                          "source": str((owner or {}).get("source") or ""),
                          "partner": str((owner or {}).get("partner") or "")}
                         if default else {})
    for it in _all_items(plan):
        over = str(it.get("owner_override") or "").strip().lower()
        if over:
            it["owner"], it["owner_label"], it["owner_source"] = over, owner_label(over, names), "item"
        elif default:
            it["owner"], it["owner_label"], it["owner_source"] = default, resolved["owner"]["label"], "client"
    plan["resolved"] = resolved
    return plan


# ---------------------------------------------------------------------------
# The questions that are the client's to answer
# ---------------------------------------------------------------------------
# The plan asks about what the proposal does not say, and most of it is ours:
# a budget the parser missed, a channel with no recipe, whatever the model
# was unsure of. Three are genuinely the client's call and are the only ones
# the page a client reads may carry, each reworded for the person being
# asked -- "who is supplying the display creative?" is a question about the
# client and "who is producing the display creative, your team or Smart 1?"
# is a question to them.
CLIENT_QUESTION_WORDING = {
    "launch_date": "When would you like the campaign to launch?",
    "creative_supply": "Who is producing the {name} creative -- your team, or Smart 1?",
    "reporting_cadence": "How often would you like a performance report?",
}


# The same choices, worded for the person being asked: "the client is
# supplying it" is a sentence about them and "our team is supplying it" is
# one they would say. Same values, so an answer lands on the plan unchanged.
CLIENT_SUPPLY_LABELS = {"smart1": "Smart 1 produces it", "client": "Our team is supplying it",
                        "mixed": "Some of each"}
CLIENT_CADENCE_LABELS = {"monthly": "Monthly", "weekly": "Weekly", "quarterly": "Quarterly",
                         "none": "No regular report needed"}
# How many answers a client's page may hold on one plan. A page reached at
# a token anybody holding the link can post to; the cap is what keeps a
# script from filling the column, and it is far above what a plan asks.
MAX_CLIENT_ANSWERS = 40
MAX_CLIENT_NAME = 120


def _client_options(key: str, q: dict) -> list[dict]:
    """The choices a client is offered for one question, in their words."""
    if key.startswith("creative_supply:"):
        return [{"value": v, "label": CLIENT_SUPPLY_LABELS.get(v, l)} for v, l in SUPPLY_CHOICES]
    if key == "reporting_cadence":
        return [{"value": o.get("value"), "label": CLIENT_CADENCE_LABELS.get(o.get("value"), o.get("label"))}
                for o in q.get("options") or []]
    return []


def client_questions(plan: dict) -> list[dict]:
    """The open questions a client can answer, in the client's own words.
    Anything answered is left out; anything not on `CLIENT_QUESTION_WORDING`
    is ours to answer and never reaches them. Each row carries the control
    to draw (`type`, `options`) and `proposed` -- what the client already
    told us, so their page shows their own answer rather than asking again
    while a person is still to take it onto the plan."""
    out: list[dict] = []
    said = {row["key"]: row for row in client_answers_view(plan)}
    for q in (plan or {}).get("questions") or []:
        key = str(q.get("key") or "")
        if _answer_of(plan, key):
            continue
        if key == "launch_date" or key == "reporting_cadence":
            row = {"key": key, "question": CLIENT_QUESTION_WORDING[key],
                   "type": "date" if key == "launch_date" else "choice",
                   "options": _client_options(key, q)}
        elif key.startswith("creative_supply:"):
            channel = key.split(":", 1)[1]
            name = next((it.get("channel_name") or channel for it in (plan or {}).get("creative") or []
                         if it.get("channel") == channel), channel)
            row = {"key": key, "question": CLIENT_QUESTION_WORDING["creative_supply"].format(name=name),
                   "type": "choice", "options": _client_options(key, q)}
        else:
            continue
        row["proposed"] = str((said.get(key) or {}).get("value") or "")
        out.append(row)
    return out


def client_answerable(plan: dict) -> dict:
    """The keys a client may answer, each with its choices.

    The open client questions, plus the supply question for every kept
    creative item that is theirs to supply -- a client who agreed to send
    the banners and now wants Smart 1 to make them answers the same key
    with `smart1`, which is a hand-back rather than a new question. Every
    other key on the plan is ours to answer, and a client naming one is
    refused by name: the page a stranger can post to must not be able to
    set a budget or answer what the model was unsure of.

    Every key carries its `type`, and there are two -- `date` and
    `choice` -- because those are the only two forms a client's answer is
    ever kept in (`_client_value`). A key of neither kind is one the page
    refuses, so a question added to the client's page later cannot open a
    free-text door by default.
    """
    out: dict = {}
    for row in client_questions(plan):
        out[row["key"]] = {"type": row.get("type") or "choice",
                           "options": [o["value"] for o in row.get("options") or []], "handback": False}
    kept = {it.get("channel") for it in (plan or {}).get("creative") or []
            if it.get("accepted") is True and it.get("kind") != "copy"}
    for q in (plan or {}).get("questions") or []:
        key = str(q.get("key") or "")
        if not key.startswith("creative_supply:") or key in out:
            continue
        if key.split(":", 1)[1] in kept and _answer_of(plan, key) in ("client", "mixed"):
            out[key] = {"type": "choice", "options": [v for v, _l in SUPPLY_CHOICES], "handback": True}
    return out


def _said(text, cap: int = 60) -> str:
    """A client-typed string as a refusal quotes it back: capped, so a
    refusal cannot echo a kilobyte of whatever was posted at the page."""
    text = str(text or "")
    return repr(text if len(text) <= cap else text[:cap] + "...")


def _client_value(key: str, value: str, rule: dict) -> str:
    """The one form a client's answer may take, or a ValueError by name.

    Two kinds and no third. A **date** is read the way the plan reads one
    (`parse_day`) and stored as ISO; a **choice** is one of the offered
    values, verbatim. Nothing a client posts is ever kept as free text:
    the value is drawn on the staff plan page, on the kickoff document and
    on Client 360, and a string typed at a link anybody can post to must
    arrive on those screens as a date or a known word rather than as
    whatever was typed. The first version kept the launch date as typed,
    because the form's calendar control only ever posts ISO -- and a form
    is a courtesy to somebody typing, not a rule; a hand-made POST put a
    script in a staff page's `onclick`. A key of neither kind is refused.
    """
    kind = rule.get("type")
    if kind == "date":
        day = parse_day(value)
        if day is None:
            raise ValueError("That date could not be read -- pick it from the calendar.")
        return day.isoformat()
    options = rule.get("options") or []
    if kind == "choice" and options:
        if value not in options:
            raise ValueError(f"{_said(value)} is not one of the choices offered for {_said(key)}.")
        return value
    raise ValueError(f"{_said(key)} is not a question the client can answer on this plan.")


def record_client_answers(plan: dict, answers: dict, *, name: str, email: str = "") -> tuple[dict, int]:
    """What the client answered on their page, kept **apart** from the
    plan's own answers.

    Nothing here writes `plan["answers"]`: the client's reply is a proposal
    a person takes onto the plan with one press (`apply_decisions` with
    `answers`), for the reason a researched competitor stays unticked until
    a rep ticks it -- a value posted at a token anybody holding the link
    can post to must not move a due date on every task by arriving. A name
    is required, because an answer nobody can attribute is one nobody can
    ring back about; the email is kept where given and never required. A
    key the client may not answer, or a choice that is not one of the
    offered ones, is refused by name and nothing is half-recorded. Returns
    the plan and how many answers were recorded.
    """
    plan = json.loads(json.dumps(plan or {}))
    who = " ".join(str(name or "").split())[:MAX_CLIENT_NAME]
    if not who:
        raise ValueError("Please tell us your name, so we know who answered.")
    addr = " ".join(str(email or "").split())[:200].lower()
    if addr and "@" not in addr:
        raise ValueError("That email address does not look right -- check it, or leave it blank.")
    if not isinstance(answers, dict):
        raise ValueError("answers must map question keys to values.")
    allowed = client_answerable(plan)
    stored = dict(plan.get("client_answers") or {})
    taken = 0
    stamp = _now_iso()
    for key, value in answers.items():
        key = str(key)
        value = " ".join(str(value if value is not None else "").split())[:MAX_ANSWER]
        if not value:
            continue
        if key not in allowed:
            raise ValueError(f"{_said(key)} is not a question the client can answer on this plan.")
        value = _client_value(key, value, allowed[key])
        stored[key] = {"value": value, "by": who, "email": addr, "at": stamp}
        taken += 1
    if not taken:
        raise ValueError("Nothing was filled in -- answer at least one question before sending.")
    if len(stored) > MAX_CLIENT_ANSWERS:
        raise ValueError("This page cannot hold any more answers; reply to your Smart 1 contact instead.")
    plan["client_answers"] = stored
    plan["summary"] = summarize(plan)
    return plan, taken


def client_answers_view(plan: dict) -> list[dict]:
    """Every answer the client gave, with whether the plan now carries it.

    `taken` is the plan answering the same value -- whether a person kept
    it or the document already said it -- so a proposal that agrees with
    the plan is not offered as a press. `label` is the choice in words
    where the key has choices, else the value itself."""
    out: list[dict] = []
    questions = {str(q.get("key") or ""): q for q in (plan or {}).get("questions") or []}
    for key, row in ((plan or {}).get("client_answers") or {}).items():
        if not isinstance(row, dict):
            continue
        value = str(row.get("value") or "")
        q = questions.get(key) or {}
        label = value
        if key.startswith("creative_supply:"):
            label = SUPPLY_LABELS.get(value, value)
        elif key == "reporting_cadence":
            label = CADENCE_LABELS.get(value, value)
        out.append({"key": key, "question": str(q.get("question") or key), "value": value, "label": label,
                    "by": str(row.get("by") or ""), "email": str(row.get("email") or ""),
                    "at": str(row.get("at") or ""), "taken": _answer_of(plan, key) == value})
    return out


def answers_for(plan: dict, channel: str = "") -> dict:
    """The answers a downstream draft for one channel may read, as
    `{question: answer}` -- the run-wide ones (launch date, reporting
    cadence, anything the model asked) and the ones keyed on this channel.
    An unanswered question is left out rather than handed over blank."""
    out = {}
    for q in (plan or {}).get("questions") or []:
        key = str(q.get("key") or "")
        value = _answer_of(plan, key)
        if not value:
            continue
        scoped = key.split(":", 1)[1] if ":" in key else ""
        if key.startswith(("creative_supply:", "budget:")):
            if channel and scoped != channel:
                continue
        elif key.startswith("creative_for:") and channel:
            continue
        label = str(q.get("question") or key)
        if key.startswith("creative_supply:"):
            value = SUPPLY_LABELS.get(value, value)
        elif key == "reporting_cadence":
            value = CADENCE_LABELS.get(value, value)
        out[label] = value
    return out


# ---------------------------------------------------------------------------
# What to do about a creative item
# ---------------------------------------------------------------------------
# A creative item that names a set and offers nothing to do about it sends a
# rep through two screens to find the tool. The tools already exist -- Stale
# Creative sends a rep to the Display Ad Builder's start form with the client
# filled in, and this is the same press one screen earlier -- so each item
# carries the action its kind and its supplier decide, from this table and
# never from a copy of it in the page.
CREATIVE_TOOLS = {
    "display": {"key": "display", "label": "Display Ad Builder", "href": "/tools/display-ads/_hub/start?client={client}"},
    "video": {"key": "video", "label": "Commercial Builder", "href": "/tools/commercial-builder/new"},
    "audio": {"key": "audio", "label": "Radio Ad Creator", "href": "/tools/radio-promo/"},
    "image": {"key": "image", "label": "Image Creator", "href": "/tools/image-creator/"},
    "social": {"key": "social", "label": "Social Content Planner", "href": "/tools/social/"},
    "gpt": {"key": "gpt", "label": "GPT Ads Builder", "href": "/tools/gpt-ads/"},
}
# Each entry carries its own key because hub/proposal_progress.py reads the
# evidence for an item off the tool that makes it -- the same reading the
# item's own Make-it-in button is drawn from, so a display pack cannot close
# a social graphic.
# Which tool makes an image for which channel. Banners -- a display buy, a
# retargeting set, a companion banner beside a spot -- are the Display Ad
# Builder's; a post graphic is the planner's; the AI placement's square is
# GPT Ads'; anything else is an Image Creator canvas.
_IMAGE_TOOL_BY_CHANNEL = {"display": "display", "retargeting": "display",
                          "stadium_audio": "display", "digital_radio": "display",
                          "social": "social", "ai_ads": "gpt"}
# Copy is written by a task on the board rather than in a tool; the action
# points at that task where the run has one.
COPY_TASKS = {"paid_search": "paid_search_ads", "meta": "meta_carousel",
              "ai_ads": "ai_ads_plan", "social": "social_posts"}


def tool_for(item: dict) -> dict | None:
    """The tool that makes this item's kind of file, or None for copy."""
    kind = str((item or {}).get("kind") or "")
    if kind == "copy":
        return None
    if kind == "video":
        return CREATIVE_TOOLS["video"]
    if kind == "audio":
        return CREATIVE_TOOLS["audio"]
    if kind == "package":
        return CREATIVE_TOOLS["display"]
    key = _IMAGE_TOOL_BY_CHANNEL.get(str((item or {}).get("channel") or ""), "image")
    return CREATIVE_TOOLS[key]


def item_actions(item: dict, *, client: str = "", task_keys=(), upload: dict | None = None) -> list[dict]:
    """The presses a creative item offers, decided by its kind and supplier.

    Smart 1 produces it -> make it in the tool, with the client filled in
    where the tool takes one. The client supplies it -> the upload link,
    which is the gallery's own share link where one exists and a press that
    creates one where it does not (creating is asked for, never assumed --
    `modules/image_picker/provisioning.py`'s rule). Nobody has said ->
    both are offered, because the item is still somebody's to act on.
    Copy points at the board task that drafts it, where the run has one.
    """
    from urllib.parse import quote_plus
    item = item or {}
    upload = upload or {}
    out: list[dict] = []
    if item.get("kind") == "copy":
        task = COPY_TASKS.get(str(item.get("channel") or ""))
        if task and task in set(task_keys or ()):
            out.append({"kind": "task", "label": "Drafted by the board", "task_key": task,
                        "href": f"#task-{task}"})
        return out
    who = str(item.get("supplier") or "")
    tool = tool_for(item)
    if who in ("smart1", "mixed", "") and tool:
        out.append({"kind": "create", "label": f"Make it in {tool['label']}", "tool": tool["label"],
                    "href": tool["href"].format(client=quote_plus(client or ""))})
    if who in ("client", "mixed", ""):
        share = str(upload.get("share_url") or "")
        act: dict = {"kind": "request", "label": "Request from the client", "href": share}
        if not share:
            # No link yet: the press creates the gallery. Two galleries that
            # could be this client is the one case nothing may be created,
            # because the wrong one collects their photographs.
            act["provision"] = not upload.get("ambiguous") and not upload.get("error")
            if upload.get("error"):
                act["note"] = str(upload["error"])
        elif upload.get("share_enabled") is False:
            act["note"] = str(upload.get("note") or "This gallery's link is switched off.")
        out.append(act)
    return out


def with_actions(plan: dict, *, client: str = "", task_keys=(), upload: dict | None = None) -> dict:
    """The resolved plan with an `actions` list on every creative item, and
    the client's upload link on `resolved` so the page can show it once.
    Never raises: an item whose action cannot be decided carries none."""
    plan = json.loads(json.dumps(plan or {}))
    upload = upload or {}
    resolved = plan.setdefault("resolved", {})
    resolved["upload_link"] = {k: upload.get(k) for k in
                               ("ok", "share_url", "exists", "created", "ambiguous", "error",
                                "note", "share_enabled", "can_create") if k in upload}
    keys = set(task_keys or ())
    for it in plan.get("creative") or []:
        try:
            it["actions"] = item_actions(it, client=client, task_keys=keys, upload=upload)
        except Exception:                               # noqa: BLE001
            it["actions"] = []
    return plan


__all__ = ["LISTS", "LIST_LABELS", "RECIPES", "SUPPLY_CHOICES", "SUPPLY_LABELS", "CADENCE_LABELS",
           "LEAD_DAYS_CREATIVE", "CREATIVE_TOOLS", "COPY_TASKS", "build_plan", "rule_items",
           "ai_items", "questions", "apply_decisions", "carry_forward", "summarize", "kept_items",
           "resolve", "answers_for", "parse_day", "tool_for", "item_actions", "with_actions",
           "owner_label", "client_questions", "CLIENT_QUESTION_WORDING", "client_answerable",
           "record_client_answers", "client_answers_view", "MAX_CLIENT_ANSWERS"]
