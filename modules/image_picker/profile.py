"""
What a "General Business" client actually does, and the photo topics that
follow from it.

The industry dropdown is the good half of this tool: nineteen trades, each with
topics and services somebody sat down and wrote curated search terms for. The
bad half is the last entry. **General Business** is what a client picks when
none of the others fit, and it hands them four generic topics — a team, a
counter, a storefront, a handshake — which is the stock-photo equivalent of
shrugging. It is also the busiest entry on the list, because "none of the
above" always is.

So a client on General Business is asked two questions before they browse: what
kind of business is this, and what do you sell or show on your website. The
answers do three things, and all three matter:

* they become **their own topic and service chips**, written by the model
  against what they typed rather than against a category nobody chose;
* they are blended into every **free-text search** from then on, so "our team"
  returns their trade rather than an office nobody works in;
* they are **kept**, so the next visit — and the next person from Smart 1
  picking on their behalf — starts from the same answers.

Rules, each of which is a way to be wrong quietly:

**An answer that was captured must be used.** The proposal builder learned this
one: four discovery questions were asked and never read, and the document came
out identical whatever was typed. If a client describes a marine upholstery
shop and still gets "Our team / Customer service / Local business", the form
was a waste of their time and they will not fill the next one in either.

**The model writes search terms, and nothing else it returns is trusted.**
Whatever comes back is clamped here — a fixed number of collections, a fixed
number of queries each, a length cap, and characters cut back to what a stock
search accepts. These strings reach a provider API and a page; a prompt is a
request, and "the model was told to return six" is not evidence that it did.

**"The model was not available" is not "this business has no topics".** When
OpenAI cannot be reached the chips are still built — from the words the client
typed, blended into the General Business queries — and the row records that
that is what happened, so a staff screen can tell the difference between copy
somebody wrote for this client and copy we fell back to. A clean-looking set of
generic chips presented as though a model had chosen them is the confident
wrong answer this codebase keeps having to undo.

**Regenerating is asked for, never automatic.** The chips are what the client
browses by; silently rewriting them under somebody mid-session is the
target-area input bug in another costume.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from . import taxonomy

log = logging.getLogger(__name__)

# The two questions. Held as data because the form, the prompt and the staff
# view all have to ask the same thing — the same reason spec.py exists in
# Smart 1 Ads.
QUESTIONS = [
    {
        "key": "category",
        "label": "What kind of business is this?",
        "hint": "A few words is plenty — \"marine upholstery\", \"family law "
                "firm\", \"wood-fired pizza\".",
        "placeholder": "e.g. marine upholstery shop",
        "max": 120,
    },
    {
        "key": "profile",
        "label": "What do you sell, or what's on your website?",
        "hint": "The services and products you want pictures of. The more "
                "specific, the better the photos.",
        "placeholder": "e.g. we re-cover boat seats and canvas tops, make "
                       "custom cushions, and repair biminis for lake boats",
        "max": 1200,
    },
]

MAX_COLLECTIONS = 6         # per kind
MAX_QUERIES = 3             # per collection
MAX_QUERY_CHARS = 90
MAX_LABEL_CHARS = 34

_SAFE_QUERY = re.compile(r"[^A-Za-z0-9 &'\-]+")
_SPACES = re.compile(r"\s+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_text(value: Any, limit: int) -> str:
    return _SPACES.sub(" ", str(value or "")).strip()[:limit]


def clean_query(value: Any) -> str:
    """A stock-photo search term, and only that.

    Punctuation a provider treats as syntax is stripped rather than escaped
    per provider: three providers, three quoting rules, and the one that gets
    it wrong returns nothing while looking like a search that found nothing.
    """
    q = _SAFE_QUERY.sub(" ", str(value or ""))
    return _SPACES.sub(" ", q).strip().lower()[:MAX_QUERY_CHARS]


def _key_for(label: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (label or "").lower()).strip("_")[:40] or "topic"
    key, n = base, 2
    while key in taken:
        key = f"{base}_{n}"
        n += 1
    taken.add(key)
    return key


def clamp(raw: Any) -> list[dict]:
    """Whatever the model returned, shaped into collections this module can use.

    Anything that does not survive the clamp is dropped rather than repaired:
    a collection with no usable query is a chip that returns an empty grid, and
    an empty grid reads to a client as "there are no photos of my business".
    """
    out: list[dict] = []
    taken: set[str] = set()
    for item in (raw if isinstance(raw, list) else [])[: MAX_COLLECTIONS * 2]:
        if not isinstance(item, dict):
            continue
        label = clean_text(item.get("label"), MAX_LABEL_CHARS)
        queries, seen = [], set()
        for q in (item.get("queries") if isinstance(item.get("queries"), list) else [])[:6]:
            cleaned = clean_query(q)
            if len(cleaned) < 3 or cleaned in seen:
                continue
            seen.add(cleaned)
            queries.append(cleaned)
            if len(queries) >= MAX_QUERIES:
                break
        if not label or not queries:
            continue
        negative = []
        for n in (item.get("negative") if isinstance(item.get("negative"), list) else [])[:4]:
            cleaned = clean_query(n)
            if cleaned:
                negative.append(cleaned)
        out.append({"key": _key_for(label, taken), "label": label,
                    "queries": queries, "negative": negative})
        if len(out) >= MAX_COLLECTIONS:
            break
    return out


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #

_SYSTEM = (
    "You choose stock-photo search terms for a small business's marketing "
    "pictures. You reply with JSON and nothing else."
)

_PROMPT = """This business describes itself as: {category}

What they sell, in their own words:
{profile}

Write the photo categories THIS business would want pictures of. Two lists:

  "topics"   — {n} moods and moments that sell what they do: the customer's
               problem, the season, the reason somebody calls, the result.
  "services" — {n} of the actual things they sell or show on their website.

Rules:
- Use their words. A marine upholstery shop gets "boat seat re-covering", not
  "professional services".
- Each entry carries 2-3 search terms written the way stock libraries are
  captioned: plain nouns describing what is in the photograph. No brand names,
  no place names, no camera jargon, no quotation marks.
- A term must describe a photograph that could exist. Nobody has stock of an
  abstract idea.
- Labels are at most {label_chars} characters and read like a button.
- Add "negative" terms only where the obvious search returns the wrong
  industry — for example a term for car air conditioning under a home HVAC
  entry.

Reply as {{"topics": [{{"label": "...", "queries": ["...", "..."],
"negative": []}}], "services": [ ... same shape ... ]}}
"""


def _fallback(category: str, profile: str) -> dict:
    """Chips built from what they typed, when the model cannot be reached.

    The General Business collections with the client's own category folded into
    every query. Generic, and honestly labelled as generic — `source` says
    "typed", and the staff view prints it.
    """
    words = clean_query(f"{category} {profile}")
    lead = " ".join(words.split()[:4])
    base = taxonomy.industry("general") or {"topics": [], "services": []}
    built: dict[str, list[dict]] = {}
    for kind in ("topics", "services"):
        rows = []
        for coll in base.get(kind, [])[:MAX_COLLECTIONS]:
            queries = [clean_query(f"{lead} {q}")[:MAX_QUERY_CHARS]
                       for q in coll["queries"][:MAX_QUERIES]] if lead else \
                      [clean_query(q) for q in coll["queries"][:MAX_QUERIES]]
            rows.append({"key": coll["key"], "label": coll["label"],
                         "queries": [q for q in queries if q],
                         "negative": list(coll.get("negative") or [])})
        built[kind] = rows
    return built


def build(*, category: str, profile: str, client_name: str = "") -> tuple[dict, str]:
    """(collections, error). The error is for staff, never for the client.

    Returns collections either way — `source` says which. `(data, error)`
    rather than a bare dict for the reason `connected_accounts_result()` gives
    in Google Finder: "we built these from their own words" and "we could not
    ask the model" are different answers, and only one of them is worth
    somebody pressing the button again.
    """
    category = clean_text(category, QUESTIONS[0]["max"])
    profile = clean_text(profile, QUESTIONS[1]["max"])
    if not category and not profile:
        return ({}, "Nothing was described.")

    error = ""
    topics: list[dict] = []
    services: list[dict] = []
    try:
        from hub import ai
        data = ai.chat_json(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": _PROMPT.format(
                 category=category or "(not given)",
                 profile=profile or "(not given)",
                 n=MAX_COLLECTIONS, label_chars=MAX_LABEL_CHARS)}],
            module="image_picker", purpose="business_profile_topics",
            max_tokens=1400, temperature=0.5,
        )
        topics = clamp(data.get("topics"))
        services = clamp(data.get("services"))
        if not topics and not services:
            error = "The model answered, but nothing in it was usable."
    except Exception as exc:                            # noqa: BLE001
        # Never the exception text: this reaches a page a client can be looking
        # at, and an OpenAI 401 prints a key prefix.
        log.warning("image_picker profile build failed: %s", exc)
        error = "We couldn't reach the writing model just now."

    if topics or services:
        source = "ai"
    else:
        built = _fallback(category, profile)
        topics, services = built["topics"], built["services"]
        source = "typed"

    return ({
        "category": category,
        "profile": profile,
        "topics": topics,
        "services": services,
        "source": source,
        "generated_at": _now(),
        "for_name": clean_text(client_name, 200),
    }, error)


# --------------------------------------------------------------------------- #
# Reading what was stored
# --------------------------------------------------------------------------- #

def stored(client) -> dict:
    """The collections saved against a gallery, or {} — never an exception.

    A blob written by an older release, or half-written by a crash, must cost
    the client the chips and nothing else: the picker falls back to the stock
    General Business list, which is a usable picker rather than an error.
    """
    raw = getattr(client, "ai_collections", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    data["topics"] = [c for c in (data.get("topics") or []) if isinstance(c, dict)]
    data["services"] = [c for c in (data.get("services") or []) if isinstance(c, dict)]
    return data


def dumps(data: dict) -> str:
    return json.dumps(data, separators=(",", ":"))


def applies(client, industry_key: str) -> bool:
    """Whether this gallery's own collections should be used for this browse.

    Only under General Business. Staff can switch the industry selector to a
    real trade to see what that trade's curated chips look like, and a client's
    own description must not silently override the trade they picked.
    """
    return (str(industry_key or "").lower() == "general"
            and bool(stored(client).get("topics") or stored(client).get("services")))


def collection(client, kind: str, key: str) -> dict | None:
    data = stored(client)
    for coll in data.get("topics" if kind == "topic" else "services", []):
        if coll.get("key") == key:
            return coll
    return None


def public(client) -> dict:
    """Shaped for the browser: labels and keys, no queries.

    Same rule `taxonomy.public_industries()` follows — the curated search terms
    are the part of this that took work, and there is no reason to ship them to
    a page where they would be read straight off the wire.
    """
    data = stored(client)
    if not data:
        return {}
    return {
        "category": data.get("category") or "",
        "profile": data.get("profile") or "",
        "source": data.get("source") or "",
        "generated_at": data.get("generated_at") or "",
        "topics": [{"key": c.get("key"), "label": c.get("label")}
                   for c in data.get("topics", []) if c.get("key")],
        "services": [{"key": c.get("key"), "label": c.get("label")}
                     for c in data.get("services", []) if c.get("key")],
    }


def search_hint(client) -> str:
    """What to add to a free-text search so it lands in this client's world.

    The category, not the whole description: appending a paragraph to a stock
    query narrows it to nothing, which reads on the page as a search that found
    no photographs of their business.
    """
    return clean_query(stored(client).get("category") or "")
