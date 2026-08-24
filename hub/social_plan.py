"""What a month of social posts is made of — the spec, as data.

The Hub already had four tools that write client-facing copy and each one
carried its own idea of what the rules were. This follows the pattern
``hub/proposal_spec.py`` and ``hub/target_areas.py`` set instead: the channel
list, the post-type mix, the calendar arithmetic, the copy checks and the CSV
layout live here, and the module, the exporter and the AI prompt all read them.
Changing what a month of content looks like is one edit rather than four.

## Why the grid is built on the server and nowhere else

The Proposal Builder and the IO Builder both carry a JavaScript mirror of a
Python helper so a wizard can react as a rep types, and both need a test
asserting the two still agree (``test_target_areas.py``, ``test_proposal_spec.py``)
because when they drift the screen and the document disagree and nothing
errors. That is a real cost, paid twice.

So there is no mirror here. The calendar grid is one API call, and the browser
renders what comes back. It is a few milliseconds slower than computing it
locally and it cannot drift.

## The three rules that are checked rather than requested

A prompt is a request. "The model was told not to" is not evidence that it did
not, and unlike a proposal — read by a rep before a client ever sees it — a
social post is bulk work that gets skimmed. So the checks are code:

* **No invented commercial facts.** A price, a percentage off, a phone number
  or a deadline that is not in what the strategist typed is a `block`, not a
  suggestion. "20% off this Friday" on a client's Facebook page is a phone call
  from that client, and the model has no way to know it is false.
* **No unverifiable superlatives.** "Best in town", "#1", "guaranteed" are
  claims the client may not be able to substantiate, and in regulated trades
  they are a compliance problem rather than a style one.
* **Channel limits are hard.** A 400-character post silently truncated by X is
  a post that ends mid-sentence in public.

Absent data reads as absent: a slot with no copy is `empty`, never a plausible
filler sentence.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from datetime import date

# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------
# `limit` is the platform's hard ceiling — over it the post is rejected or
# truncated, so it is a block. `soft` is where copy stops reading like a social
# post and starts reading like a press release; that is a warning, because
# sometimes long is right.
#
# `asset`: "required" means the platform will not accept a post without one.
CHANNELS: dict[str, dict] = {
    "facebook": {
        "label": "Facebook", "limit": 5000, "soft": 500,
        "asset": "recommended", "hashtags": 3,
        "voice": "conversational, a little warmer than the website",
    },
    "instagram": {
        "label": "Instagram", "limit": 2200, "soft": 300,
        "asset": "required", "hashtags": 15,
        "voice": "visual-first — the caption supports the image, not the reverse",
    },
    "google_business": {
        "label": "Google Business Profile", "limit": 1500, "soft": 250,
        "asset": "recommended", "hashtags": 0,
        "voice": "plain and local; say the service and the area in the first line",
    },
    "linkedin": {
        "label": "LinkedIn", "limit": 3000, "soft": 600,
        "asset": "recommended", "hashtags": 5,
        "voice": "professional but not stiff; industry peers are reading",
    },
    "x": {
        "label": "X", "limit": 280, "soft": 220,
        "asset": "optional", "hashtags": 2,
        "voice": "one idea, said once",
    },
    "pinterest": {
        "label": "Pinterest", "limit": 500, "soft": 200,
        "asset": "required", "hashtags": 5,
        "voice": "descriptive and searchable — people arrive here from search",
    },
    "tiktok": {
        "label": "TikTok", "limit": 2200, "soft": 200,
        "asset": "required", "hashtags": 5,
        "voice": "spoken, not written",
    },
    "youtube": {
        "label": "YouTube", "limit": 5000, "soft": 400,
        "asset": "required", "hashtags": 3,
        "voice": "describes the video and what the viewer gets from it",
    },
}

# Defaults chosen for the trades and local-service clients that make up most of
# the book. A month that is all promo trains an audience to scroll past.
DEFAULT_CHANNELS = ("facebook", "instagram", "google_business")


# ---------------------------------------------------------------------------
# Post types
# ---------------------------------------------------------------------------
# `share` is a weight, not a percentage — they are normalised, so a strategist
# can zero one out without having to rebalance the rest by hand.
POST_TYPES: dict[str, dict] = {
    "educational": {
        "label": "Educational", "share": 22,
        "brief": "Teach one useful thing a customer could act on today. No "
                 "sell. The payoff is that they remember who told them.",
    },
    "service_spotlight": {
        "label": "Service spotlight", "share": 18,
        "brief": "One service, what it solves, who it is for. Concrete, not a "
                 "list of everything the business does.",
    },
    "promo": {
        "label": "Promotion", "share": 12,
        "brief": "An offer the client has actually authorised. If no offer was "
                 "supplied, write about the value of the service instead and "
                 "invent nothing.",
    },
    "seasonal": {
        "label": "Seasonal / timely", "share": 12,
        "brief": "Tie to the time of year or the weather in their market — "
                 "what people are dealing with this month.",
    },
    "testimonial": {
        "label": "Testimonial / results", "share": 10,
        "brief": "Only from a review the strategist supplied. With none "
                 "supplied, write a prompt inviting customers to share theirs.",
    },
    "faq": {
        "label": "FAQ", "share": 8,
        "brief": "One question the business is genuinely asked, answered "
                 "straight. Good for search as well as social.",
    },
    "behind_the_scenes": {
        "label": "Behind the scenes", "share": 8,
        "brief": "The team, the work, the process. Builds the familiarity that "
                 "makes the promotional posts land.",
    },
    "community": {
        "label": "Community / local", "share": 6,
        "brief": "The town, an event, a local cause. Says the business is from "
                 "here, which is most of why local buyers pick anyone.",
    },
    "review_request": {
        "label": "Review request", "share": 2,
        "brief": "Ask for a review, once, politely, with the place to leave it.",
    },
    "hiring": {
        "label": "Hiring", "share": 2,
        "brief": "Only when the strategist notes an open role. Otherwise this "
                 "type should not be in the mix at all.",
    },
}

# Fixed order, so two runs of the same plan produce the same calendar. A grid
# that reshuffles when you reload is one nobody trusts.
_TYPE_ORDER = tuple(POST_TYPES.keys())

DEFAULT_MIX = {k: v["share"] for k, v in POST_TYPES.items() if k != "hiring"}

# Spread through the day rather than every post at 9am, which reads as
# automated to anyone who follows more than one of our clients.
POST_TIMES = ("09:15", "12:30", "15:45", "18:00")

# Which weekdays get posts, by posts-per-week. Monday=0.
WEEKDAY_PATTERNS = {
    1: (2,), 2: (1, 3), 3: (0, 2, 4), 4: (0, 1, 3, 4),
    5: (0, 1, 2, 3, 4), 6: (0, 1, 2, 3, 4, 5), 7: (0, 1, 2, 3, 4, 5, 6),
}

STATUSES = ("empty", "drafted", "edited", "approved")


# ---------------------------------------------------------------------------
# The mix
# ---------------------------------------------------------------------------
def mix_counts(total: int, mix: dict | None = None) -> dict[str, int]:
    """Apportion `total` posts across post types, summing to exactly `total`.

    Largest-remainder rather than rounding each share independently: rounding
    gives you 19 or 21 posts in a 20-post month depending on the weights, and
    the person who asked for 20 has to work out which one to add.
    """
    weights = {k: max(0.0, float(v)) for k, v in (mix or DEFAULT_MIX).items()
               if k in POST_TYPES}
    weights = {k: v for k, v in weights.items() if v > 0}
    if total <= 0 or not weights:
        return {}
    scale = sum(weights.values())
    exact = {k: total * v / scale for k, v in weights.items()}
    out = {k: int(v) for k, v in exact.items()}
    short = total - sum(out.values())
    order = sorted(exact, key=lambda k: (-(exact[k] - int(exact[k])),
                                         _TYPE_ORDER.index(k)))
    for key in order[:short]:
        out[key] += 1
    return {k: v for k, v in out.items() if v > 0}


def type_sequence(total: int, mix: dict | None = None) -> list[str]:
    """The post types in calendar order, avoiding two of a kind back to back.

    Deterministic — same inputs, same sequence — so re-opening a plan shows the
    calendar the strategist left, not a fresh shuffle of it.
    """
    remaining = mix_counts(total, mix)
    seq: list[str] = []
    previous = ""
    for _ in range(total):
        live = [k for k, v in remaining.items() if v > 0]
        if not live:
            break
        pool = [k for k in live if k != previous] or live
        pool.sort(key=lambda k: (-remaining[k], _TYPE_ORDER.index(k)))
        pick = pool[0]
        seq.append(pick)
        remaining[pick] -= 1
        previous = pick
    return seq


# ---------------------------------------------------------------------------
# The calendar
# ---------------------------------------------------------------------------
def parse_month(month: str) -> tuple[int, int]:
    """'2026-09' -> (2026, 9). Raises ValueError on anything else."""
    m = re.match(r"^(\d{4})-(\d{1,2})$", str(month or "").strip())
    if not m:
        raise ValueError("Pick a month in YYYY-MM form.")
    year, mon = int(m.group(1)), int(m.group(2))
    if not 1 <= mon <= 12 or not 2000 <= year <= 2100:
        raise ValueError("That month is out of range.")
    return year, mon


def posting_dates(month: str, per_week: int = 3,
                  blackout: tuple | list = ()) -> list[date]:
    year, mon = parse_month(month)
    per_week = max(1, min(7, int(per_week or 3)))
    wanted = WEEKDAY_PATTERNS[per_week]
    skip = {str(d).strip() for d in (blackout or []) if str(d).strip()}
    days = calendar.monthrange(year, mon)[1]
    out = []
    for day in range(1, days + 1):
        when = date(year, mon, day)
        if when.weekday() in wanted and when.isoformat() not in skip:
            out.append(when)
    return out


def build_grid(month: str, *, channels=(), per_week: int = 3,
               mix: dict | None = None, blackout=()) -> list[dict]:
    """The month as a list of empty slots — dates, channels and post types.

    One slot per posting date, carrying every selected channel, because that is
    how the work is actually done: one idea, adapted. A slot per channel per
    date turns a 12-post month into a 36-row spreadsheet nobody reviews.
    """
    picked = [c for c in (channels or DEFAULT_CHANNELS) if c in CHANNELS]
    if not picked:
        picked = list(DEFAULT_CHANNELS)
    dates = posting_dates(month, per_week, blackout)
    types = type_sequence(len(dates), mix)
    slots = []
    for i, when in enumerate(dates):
        kind = types[i] if i < len(types) else _TYPE_ORDER[0]
        slots.append({
            "id": f"s{i + 1:02d}",
            "date": when.isoformat(),
            "time": POST_TIMES[i % len(POST_TIMES)],
            "channels": list(picked),
            "type": kind,
            "copy": "",
            "hashtags": [],
            "link": "",
            "image_url": "",
            "image_public_id": "",
            "image_source": "",
            "status": "empty",
            "flags": [],
        })
    return slots


def type_label(key: str) -> str:
    return POST_TYPES.get(key, {}).get("label") or str(key or "").title()


def channel_label(key: str) -> str:
    return CHANNELS.get(key, {}).get("label") or str(key or "").title()


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{1,2})?|\b\d{1,3}\s?%\s*(?:off|discount)\b", re.I)
PERCENT_RE = re.compile(r"\b\d{1,3}\s?%", re.I)
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")
DEADLINE_RE = re.compile(
    r"\b(?:today only|this (?:week|weekend|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|ends?\s+(?:today|tonight|tomorrow|soon|this\s+\w+)|limited[- ]time|while supplies last"
    r"|expires?|last chance|hurry|book by|offer ends)\b", re.I)
SUPERLATIVE_RE = re.compile(
    r"\b(?:#\s?1|number one|best in (?:town|the area|the business|class)|the only"
    r"|guarantee(?:d|s)?|cheapest|lowest price[sd]?|unbeatable|voted best"
    r"|award[- ]winning|top[- ]rated|fastest in|most trusted|world[- ]class)\b", re.I)
PLACEHOLDER_RE = re.compile(
    r"\[(?:insert|client|company|name|city|service|link)[^\]]*\]|\{\{|lorem ipsum"
    r"|as an ai|i'?m an ai|your business name here", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")

# Consistent with hub/proposal_spec.py: this name never reaches a client.
BANNED_PHRASES = ("smart 1 labs", "smart1 labs", "smart one labs")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _allowed_text(facts: dict | None) -> str:
    """Everything a human actually typed for this client, as one blob.

    A claim is permitted when it traces back to something here. That is a
    deliberately blunt test — it will occasionally flag copy that is fine — but
    the failure it prevents is publishing a discount the client never offered,
    and a strategist dismissing a flag costs seconds.
    """
    facts = facts or {}
    parts = [str(facts.get("offers") or ""), str(facts.get("notes") or ""),
             str(facts.get("phone") or ""), str(facts.get("url") or ""),
             str(facts.get("reviews") or ""), str(facts.get("hours") or "")]
    for extra in (facts.get("must_include") or []):
        parts.append(str(extra))
    return " \n".join(parts)


def validate_copy(text: str, *, channels=(), facts: dict | None = None) -> list[dict]:
    """Flags on one piece of copy. `block` must be resolved; `warn` is advice.

    Never raises and never rewrites — a check that edits the copy is a check
    nobody can audit afterwards.
    """
    text = str(text or "")
    flags: list[dict] = []
    if not text.strip():
        return flags
    allowed = _allowed_text(facts)
    allowed_digits = _digits(allowed)
    low = text.lower()

    for phrase in BANNED_PHRASES:
        if phrase in low:
            flags.append({"level": "block", "code": "banned",
                          "message": f"Mentions “{phrase}” — never goes to a client."})
            break

    for found in MONEY_RE.findall(text):
        token = found.strip()
        if token.lower() not in allowed.lower():
            flags.append({"level": "block", "code": "price",
                          "message": f"“{token}” isn't in anything you supplied — "
                                     "confirm the offer or take it out."})
            break
    else:
        # A bare percentage ("save 20") is the same problem without the $ sign.
        for found in PERCENT_RE.findall(text):
            if found.strip() not in allowed:
                flags.append({"level": "warn", "code": "percent",
                              "message": f"“{found.strip()}” isn't in anything you "
                                         "supplied — check it."})
                break

    for found in PHONE_RE.findall(text):
        if _digits(found) and _digits(found) not in allowed_digits:
            flags.append({"level": "block", "code": "phone",
                          "message": f"Phone number “{found.strip()}” doesn't match "
                                     "the one on file."})
            break

    hit = DEADLINE_RE.search(text)
    if hit and hit.group(0).lower() not in allowed.lower():
        flags.append({"level": "block", "code": "deadline",
                      "message": f"“{hit.group(0)}” promises a deadline nobody "
                                 "authorised."})

    hit = SUPERLATIVE_RE.search(text)
    if hit:
        flags.append({"level": "warn", "code": "superlative",
                      "message": f"“{hit.group(0)}” is a claim the client may not "
                                 "be able to substantiate."})

    hit = PLACEHOLDER_RE.search(text)
    if hit:
        flags.append({"level": "block", "code": "placeholder",
                      "message": f"Unfilled placeholder: “{hit.group(0)}”."})

    tags = HASHTAG_RE.findall(text)
    for key in (channels or []):
        spec = CHANNELS.get(key)
        if not spec:
            continue
        if len(text) > spec["limit"]:
            flags.append({"level": "block", "code": "length",
                          "message": f"{spec['label']} caps at {spec['limit']:,} "
                                     f"characters; this is {len(text):,}."})
        elif len(text) > spec["soft"]:
            flags.append({"level": "warn", "code": "long",
                          "message": f"Long for {spec['label']} — "
                                     f"{len(text):,} characters."})
        if tags and len(tags) > spec["hashtags"]:
            flags.append({"level": "warn", "code": "hashtags",
                          "message": f"{len(tags)} hashtags; {spec['label']} reads "
                                     f"best with {spec['hashtags']} or fewer."})
    return flags


def validate_slot(slot: dict, facts: dict | None = None) -> list[dict]:
    """validate_copy plus the things that are about the slot, not the words."""
    slot = slot or {}
    channels = slot.get("channels") or []
    flags = validate_copy(slot.get("copy", ""), channels=channels, facts=facts)
    if slot.get("copy", "").strip() and not slot.get("image_url"):
        for key in channels:
            if CHANNELS.get(key, {}).get("asset") == "required":
                flags.append({"level": "block", "code": "asset",
                              "message": f"{channel_label(key)} will not accept a "
                                         "post without an image or video."})
                break
    return flags


def validate_batch(batch: dict) -> dict:
    """Re-flag every slot in place. Returns a count by level."""
    facts = (batch or {}).get("brief") or {}
    blocks = warns = drafted = 0
    for slot in (batch or {}).get("slots") or []:
        slot["flags"] = validate_slot(slot, facts)
        blocks += sum(1 for f in slot["flags"] if f["level"] == "block")
        warns += sum(1 for f in slot["flags"] if f["level"] == "warn")
        if slot.get("status") in ("drafted", "edited", "approved"):
            drafted += 1
    return {"block": blocks, "warn": warns, "drafted": drafted,
            "slots": len((batch or {}).get("slots") or [])}


# ---------------------------------------------------------------------------
# The AI prompt
# ---------------------------------------------------------------------------
def draft_messages(batch: dict, slot: dict, context: dict | None = None) -> list[dict]:
    """One request per slot.

    The Proposal Builder learned this: writing thirteen sections in one call
    means one refusal or one timeout costs all thirteen, and the loader cannot
    say what it is working on. A month of posts has the same shape, more so —
    twenty slots, and the strategist is watching.
    """
    context = context or {}
    brief = (batch or {}).get("brief") or {}
    channels = slot.get("channels") or []
    spec = POST_TYPES.get(slot.get("type") or "", {})
    voices = [f"- {CHANNELS[c]['label']}: {CHANNELS[c]['voice']}"
              for c in channels if c in CHANNELS]
    tightest = min((CHANNELS[c]["soft"] for c in channels if c in CHANNELS),
                   default=300)

    system = (
        "You write organic social posts for a local business, on behalf of the "
        "business itself. You are never the agency and you never mention one.\n\n"
        "Hard rules — a post breaking any of these is discarded:\n"
        "1. Invent no prices, discounts, percentages, phone numbers, addresses, "
        "deadlines, awards or statistics. Use only facts given below. If a fact "
        "would make the post better and you do not have it, write the post "
        "without it.\n"
        "2. No superlatives or unprovable claims — no “best”, “#1”, "
        "“guaranteed”, “cheapest”, “award-winning”.\n"
        "3. No fabricated customer quotes or reviews.\n"
        "4. Leave no placeholders. If something is missing, rewrite around it.\n"
        "5. Write in the business's own voice, first person plural, plain "
        "language. No corporate filler and no emoji walls.\n"
    )

    facts = [f"Business: {context.get('client') or 'the client'}"]
    if context.get("industry"):
        facts.append(f"Industry: {context['industry']}")
    if context.get("description"):
        facts.append(f"What they do: {context['description']}")
    if context.get("areas"):
        facts.append(f"Areas served: {context['areas']}")
    if brief.get("offers"):
        facts.append(f"Authorised offers (the ONLY offers you may mention): "
                     f"{brief['offers']}")
    else:
        facts.append("Authorised offers: none. Mention no offer, discount or price.")
    if brief.get("notes"):
        facts.append(f"Strategist notes: {brief['notes']}")
    if brief.get("tone"):
        facts.append(f"Tone: {brief['tone']}")
    if brief.get("must_include"):
        facts.append("Must appear somewhere: " + "; ".join(brief["must_include"]))
    if brief.get("avoid"):
        facts.append(f"Never mention: {brief['avoid']}")

    user = (
        f"Write one post for {slot.get('date')}.\n\n"
        f"Post type — {type_label(slot.get('type'))}: {spec.get('brief', '')}\n\n"
        f"Channels it will run on:\n" + ("\n".join(voices) or "- Facebook") + "\n\n"
        f"Aim for about {tightest} characters so it reads well on all of them.\n\n"
        "Facts you may use:\n" + "\n".join(f"- {f}" for f in facts) + "\n\n"
        "Return JSON: {\"copy\": \"the post text, no hashtags\", "
        "\"hashtags\": [\"#one\", \"#two\"]}. "
        "Hashtags must be specific to the business, its services or its area — "
        "no generic filler."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
# Social Planner's bulk upload expects its own header row. These four columns
# are the documented basic set; the advanced sheet adds per-platform options we
# do not set. **Verify this row against the template Social Planner offers
# under Bulk Upload before the first real import** — if HighLevel's spelling
# differs, this tuple and _planner_row() below are the only two things that
# change, and nothing else in the Hub cares.
PLANNER_COLUMNS = ("date", "content", "og meta url", "media urls")
PLANNER_DATE_FORMAT = "%m/%d/%Y %H:%M"

REVIEW_COLUMNS = ("Date", "Time", "Channels", "Post type", "Copy", "Hashtags",
                  "Link", "Image", "Status", "Needs attention")


def post_text(slot: dict) -> str:
    """Copy and hashtags as one string, the way it will actually publish."""
    copy = str((slot or {}).get("copy") or "").strip()
    tags = [t for t in ((slot or {}).get("hashtags") or []) if str(t).strip()]
    if not tags:
        return copy
    joined = " ".join(t if str(t).startswith("#") else f"#{t}" for t in tags)
    return (copy + "\n\n" + joined).strip()


def _planner_row(slot: dict) -> list[str]:
    when = f"{slot.get('date', '')} {slot.get('time', '09:15')}"
    try:
        stamp = date.fromisoformat(slot.get("date", "")).strftime("%m/%d/%Y")
        when = f"{stamp} {slot.get('time', '09:15')}"
    except ValueError:
        pass
    return [when, post_text(slot), str(slot.get("link") or ""),
            str(slot.get("image_url") or "")]


def planner_csv(batch: dict) -> str:
    """The upload sheet. Only slots with copy — an empty row imports as an
    empty post, which is worse than a shorter month."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(PLANNER_COLUMNS)
    for slot in (batch or {}).get("slots") or []:
        if str(slot.get("copy") or "").strip():
            writer.writerow(_planner_row(slot))
    return buf.getvalue()


def review_csv(batch: dict) -> str:
    """The working sheet — every slot including the empty ones, plus the flags,
    because the point of this one is to show what still needs doing."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(REVIEW_COLUMNS)
    for slot in (batch or {}).get("slots") or []:
        flags = "; ".join(f"{f['level']}: {f['message']}"
                          for f in (slot.get("flags") or []))
        writer.writerow([
            slot.get("date", ""), slot.get("time", ""),
            ", ".join(channel_label(c) for c in slot.get("channels") or []),
            type_label(slot.get("type")),
            str(slot.get("copy") or ""),
            " ".join(str(t) for t in slot.get("hashtags") or []),
            str(slot.get("link") or ""),
            str(slot.get("image_url") or ""),
            slot.get("status", "empty"),
            flags,
        ])
    return buf.getvalue()


def spec_payload() -> dict:
    """Everything the browser needs to render the planner, in one call.

    The browser gets the vocabulary; it does not get the arithmetic. See the
    note at the top about why there is no JavaScript mirror here.
    """
    return {
        "channels": [{"key": k, "label": v["label"], "limit": v["limit"],
                      "soft": v["soft"], "asset": v["asset"],
                      "hashtags": v["hashtags"]}
                     for k, v in CHANNELS.items()],
        "types": [{"key": k, "label": v["label"], "brief": v["brief"],
                   "share": v["share"]} for k, v in POST_TYPES.items()],
        "default_channels": list(DEFAULT_CHANNELS),
        "default_mix": dict(DEFAULT_MIX),
        "times": list(POST_TIMES),
        "statuses": list(STATUSES),
    }
