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
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

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
             "Retargeting cannot build an audience until the tag fires; check it before the flight starts."),
            ("Build the site-visitor audience and set the lookback window", ""),
            ("Traffic the retargeting campaign with the approved banners and tagged destination links", ""),
            ("Click every banner size through to the landing page before launch", ""),
        ],
        "monthly": [
            ("Report retargeting delivery and click-through to the client", ""),
            ("Check frequency and refresh the banners if the audience is seeing them too often", ""),
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
            ("Get the ad copy approved before the campaign is enabled", ""),
        ],
        "monthly": [
            ("Review search terms, add negatives and adjust bids", ""),
            ("Report Paid Search spend, clicks, conversions and cost per lead", ""),
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
            ("Deliver the month's SEO + AI work: on-page fixes, schema, content and AI-search optimization", ""),
            ("Report rankings, organic traffic and what was changed this month", ""),
        ],
    },
    "stadium_audio": {
        "kit_product": "Stadium to Screen streaming audio",
        "kit_units": ["radio_audio", "radio_companion"],
        "copy": [],
        "launch": [
            ("Write the audio scripts and get them approved before voice production", ""),
            ("Produce and approve the finished audio spots", ""),
            ("Confirm the venue geo-fence, the game schedule and the flight dates", ""),
            ("Traffic the audio and companion banners with tagged destination links", ""),
        ],
        "monthly": [
            ("Confirm the coming month's game schedule and adjust the flight", ""),
            ("Report audio delivery, completion rate and companion banner clicks", ""),
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
            ("Get the carousel and image creative approved before the campaign is enabled", ""),
        ],
        "monthly": [
            ("Report Meta reach, clicks, leads and cost per lead", ""),
            ("Refresh creative that is fatiguing and pause the weakest ads", ""),
        ],
    },
    "youtube_ads": {
        "kit_product": "YouTube In-Market Home Buyers",
        "kit_units": ["youtube_trueview"],
        "copy": [],
        "launch": [
            ("Link the YouTube channel to Google Ads and upload the approved spot", ""),
            ("Build the in-market audience and the geography", ""),
            ("Confirm view and conversion tracking before the campaign is enabled", ""),
        ],
        "monthly": [
            ("Report YouTube views, view rate, clicks and cost per view", ""),
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
            ("Build next month's social content calendar and get it approved", ""),
            ("Schedule the approved posts", ""),
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
            ("Script, produce and publish this month's YouTube sales video", ""),
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
            ("Report AI advertising delivery and results, and decide whether the test continues", ""),
        ],
    },
}

# Every run gets these whatever the channels are.
GENERIC_LAUNCH = [
    ("Confirm the signed proposal, the budget and the launch date with the client", ""),
    ("Confirm the landing page is live, loads on a phone and carries the primary call to action", ""),
    ("Confirm conversion tracking is in place before any spend starts", ""),
    ("Send the client a launch confirmation saying what goes live and when", ""),
]
GENERIC_MONTHLY = [
    ("Send the client the monthly performance report covering every channel", ""),
    ("Check that spend is pacing to the monthly budget in the proposal", ""),
    ("Review what the proposal promised for this month against what was delivered", ""),
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
          grounded=None, key: str = "", kind: str = "") -> dict:
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
    if list_name == "creative":
        # A file (image, video, audio) has a supplier to ask about; copy is
        # always ours to write, and asking who supplies the ad copy is the
        # question that teaches people to stop reading the list.
        row["kind"] = kind or "file"
    return row


# ---------------------------------------------------------------------------
# Creative from the kit
# ---------------------------------------------------------------------------
def _kit_creative(key: str, recipe: dict, channel_name: str) -> tuple[list[dict], str]:
    """The kit's units for one channel, as plan items. `(items, note)` --
    the note says when the kit maps nothing, so an empty creative list can
    be told from a channel that genuinely needs no file."""
    product = recipe.get("kit_product") or ""
    if not product:
        return [], ""
    try:
        from hub import creative_needs
        state = {"items": [{"product": product, "category": ""}]}
        medium = creative_needs.medium_of({"product": product, "category": ""})
        result = creative_needs.required_units(state, medium)
    except Exception as exc:                            # noqa: BLE001
        return [], f"The creative spec kit could not be read for {channel_name} ({type(exc).__name__})."
    if not result.get("measured"):
        return [], result.get("note") or f"The spec kit maps no unit for {channel_name}."
    units = result["units"]
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
        title = recipe.get("creative_title") if len(units) == 1 and recipe.get("creative_title") else f"{channel_name}: {label}"
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
    out = {name: [] for name in LISTS}
    notes: list[str] = []
    for title, detail in GENERIC_LAUNCH:
        out["launch"].append(_item("launch", title, detail))
    for ch in channels:
        key = str(ch.get("key") or "other")
        name = str(ch.get("name") or key.replace("_", " ").title())
        recipe = RECIPES.get(key)
        if not recipe:
            notes.append(f"{name} is not a channel this Hub has a recipe for, so its creative "
                         f"and tasks are asked about rather than listed.")
            continue
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
            out["creative"].append(_item("creative", title, detail, channel=key,
                                         channel_name=name, kind=kind))
        for title, detail in recipe.get("launch") or []:
            out["launch"].append(_item("launch", title, detail, channel=key, channel_name=name))
        for title, detail in recipe.get("monthly") or []:
            out["monthly"].append(_item("monthly", title, detail, channel=key, channel_name=name))
    for title, detail in GENERIC_MONTHLY:
        out["monthly"].append(_item("monthly", title, detail))
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

    def add(key, question, why, *, type_="text", options=None, from_text="", evidence=""):
        row = {"key": key, "question": question, "why": why, "type": type_,
               "answer": answers.get(key, "") if key in answers else (from_text or ""),
               "from_text": bool(from_text) and key not in answers, "evidence": evidence}
        if options:
            row["options"] = [{"value": v, "label": l} for v, l in options]
        out.append(row)

    if not (analysis or {}).get("flight_dates"):
        add("launch_date", "When does the campaign launch?",
            "The proposal carries no start date, and every launch task is measured from one.")

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
        if recipe.get("supplier"):
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
            type_="choice", options=SUPPLY_CHOICES, from_text=inferred, evidence=line)

    for ch in channels:
        key = str(ch.get("key") or "other")
        name = str(ch.get("name") or key)
        if not ch.get("budgets"):
            add(f"budget:{key}", f"What is the monthly budget for {name}?",
                "The proposal names the channel and no dollar amount was found beside it.")
        if key == "other" or key not in RECIPES:
            add(f"creative_for:{_slug(name)}", f"What creative does {name} need?",
                "This Hub has no recipe for that channel, so nothing was listed for it rather than guessing.")

    if "report" not in low:
        add("reporting_cadence", f"How often does {client or 'the client'} get a performance report?",
            "The proposal does not mention reporting, and a monthly report is on the task list by default.",
            type_="choice", options=(("monthly", "Monthly"), ("weekly", "Weekly"),
                                      ("quarterly", "Quarterly"), ("none", "No report is promised")))

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


def apply_decisions(plan: dict, decisions: dict) -> dict:
    """A person's review of the plan, applied in one press.

    `decisions` may carry `accept` ({id: true|false|null}), `add`
    ([{list, title, detail}]), `remove` ([ids], manual items only) and
    `answers` ({key: value}). Anything it cannot apply is refused by name
    with a ValueError, and nothing is half-applied.
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
        for prior in old_plan.get(name) or []:
            if prior.get("source") == SOURCE_MANUAL and prior["id"] not in ids:
                plan.setdefault(name, []).append(json.loads(json.dumps(prior)))
    asked = {q["key"] for q in plan.get("questions") or []}
    carried = {k: v for k, v in (old_plan.get("answers") or {}).items() if k in asked}
    plan["answers"] = carried
    for q in plan.get("questions") or []:
        if q["key"] in carried:
            q["answer"], q["from_text"] = carried[q["key"]], False
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
    return out


def kept_items(plan: dict, list_name: str) -> list[dict]:
    """What a person has kept on one list -- the only half a downstream tool
    may read. An item nobody has reviewed is still a proposal."""
    return [it for it in (plan or {}).get(list_name) or [] if it.get("accepted") is True]


__all__ = ["LISTS", "LIST_LABELS", "RECIPES", "SUPPLY_CHOICES", "build_plan", "rule_items",
           "ai_items", "questions", "apply_decisions", "carry_forward", "summarize", "kept_items"]
