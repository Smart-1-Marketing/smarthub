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
{context}
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


def _context_block(context: dict | None) -> str:
    """The facts a search should be anchored to, as lines for the prompt.

    Location and industry are what make "our team" return a Michigan
    marina's crew rather than an office nobody works in. Facts only, each
    one held by the Hub; nothing here is for the model to invent from.
    """
    context = context or {}
    lines = []
    if context.get("industry"):
        lines.append(f"Industry on file: {clean_text(context['industry'], 120)}")
    place = ", ".join(clean_text(context.get(k), 80) for k in ("city", "state")
                      if context.get(k))
    if place:
        lines.append(f"Where they are: {place}")
    if context.get("customer"):
        lines.append(f"Who their customers are: {clean_text(context['customer'], 300)}")
    if not lines:
        return ""
    return ("\nAlso on file (use these to anchor the terms to their customers, "
            "their location and their industry; never as place names inside a "
            "search term):\n" + "\n".join(lines) + "\n")


def build(*, category: str, profile: str, client_name: str = "",
          context: dict | None = None) -> tuple[dict, str]:
    """(collections, error). The error is for staff, never for the client.

    Returns collections either way — `source` says which. `(data, error)`
    rather than a bare dict for the reason `connected_accounts_result()` gives
    in Google Finder: "we built these from their own words" and "we could not
    ask the model" are different answers, and only one of them is worth
    somebody pressing the button again.

    `context` is what the Hub already holds -- industry, city, state, who
    their customers are -- so the terms relate to the customer, the business,
    the location and the industry rather than to the category alone.
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
                 context=_context_block(context),
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
        "context": {k: clean_text(v, 300) for k, v in (context or {}).items() if v},
    }, error)


# --------------------------------------------------------------------------- #
# Answering the two questions from what the Hub already knows
# --------------------------------------------------------------------------- #

_ANSWER_SYSTEM = (
    "You read what a marketing agency knows about a small business and "
    "answer two intake questions on the business's behalf, in plain words "
    "the owner would use. You reply with JSON and nothing else."
)

_ANSWER_PROMPT = """Business: {name}
{facts}
Answer these two questions about the business, from the facts above only.
Never invent a service, a product, a place or a claim that is not in them.
If the facts say almost nothing, answer as briefly as they allow rather than
guessing.

1. "What kind of business is this?" -- at most {cat_max} characters, a few
   words, e.g. "marine upholstery shop", "family law firm".
2. "What do you sell, or what's on your website?" -- one to three sentences,
   at most {prof_max} characters, naming the actual services and products
   and who they are for.

Reply as {{"category": "...", "profile": "..."}}"""

AUTO_MARK = "auto_attempted_at"


def hub_context(client_name: str, domain: str = "") -> tuple[str, dict]:
    """``(facts block, context)`` from the Hub's own records. Never raises.

    The facts block is `hub.client_brief.for_prompt()` -- everything the
    last site scan, the brand record and the industry resolver hold, each
    line attributed -- and the context dict is the handful of fields the
    topic prompt anchors to. Both empty when nothing is on file, so the
    caller can tell "we know nothing" from "we could not look".
    """
    facts, context = "", {}
    try:
        from hub import client_brief
        facts = client_brief.for_prompt(client_name, domain) or ""
    except Exception as exc:                            # noqa: BLE001
        log.warning("image_picker: client brief unavailable for %s: %s", client_name, exc)
    try:
        from hub import industry as _industry
        res = _industry.resolve_industry(client_name, domain) or {}
        entry = _industry.industry(res.get("key")) if res.get("key") else None
        if entry and res.get("key") != "general":
            context["industry"] = entry.get("label") or res.get("key")
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub import client_context
        ctx = client_context.context(client_name, domain)
        for key in ("city", "state"):
            if ctx.get(key):
                context[key] = ctx[key]
        if not domain and ctx.get("website"):
            context["website"] = client_context.canonical_domain(ctx["website"])
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub import audience_spec
        # The audience somebody confirmed on Client 360, and only that: a
        # confirmed answer is a fact, a proposal is not.
        who = audience_spec.for_prompt(client_name)
        if who:
            context["customer"] = who
    except Exception:                                   # noqa: BLE001
        pass
    return facts, context


def answer_from_hub(client_name: str, domain: str = "") -> tuple[dict, str]:
    """``({category, profile, context}, error)`` -- the two answers, written
    by the model from the Hub's facts about the business.

    Empty when the Hub holds nothing to write from: a picker that asks the
    client is better than one that invents a business for them. The error
    is for staff, never shown to a client.
    """
    facts, context = hub_context(client_name, domain)
    if not facts.strip():
        return ({}, "Nothing is on file about this business yet, so the "
                    "questions were left for the client.")
    try:
        from hub import ai
        data = ai.chat_json(
            [{"role": "system", "content": _ANSWER_SYSTEM},
             {"role": "user", "content": _ANSWER_PROMPT.format(
                 name=clean_text(client_name, 200), facts=facts,
                 cat_max=QUESTIONS[0]["max"], prof_max=QUESTIONS[1]["max"])}],
            module="image_picker", purpose="business_profile_autofill",
            max_tokens=600, temperature=0.3)
    except Exception as exc:                            # noqa: BLE001
        log.warning("image_picker autofill failed for %s: %s", client_name, exc)
        return ({}, "We couldn't reach the writing model just now.")
    if not isinstance(data, dict):
        return ({}, "The model answered, but nothing in it was usable.")
    category = clean_text(data.get("category"), QUESTIONS[0]["max"])
    profile = clean_text(data.get("profile"), QUESTIONS[1]["max"])
    if not category and not profile:
        return ({}, "The model answered, but nothing in it was usable.")
    return ({"category": category, "profile": profile, "context": context}, "")


def autofill(db, client, *, domain: str = "", force: bool = False) -> dict:
    """Answer the two questions for a General Business gallery, once.

    Called when the picker opens and the gallery has nothing described yet.
    The attempt is marked on the row BEFORE the model is asked, so a failure
    -- or two workers opening the same link at once -- does not spend a call
    on every page load for ever; "Change this" on the page is the way to ask
    again. Never raises. Returns what happened, for the staff view.
    """
    out = {"attempted": False, "filled": False, "note": ""}
    try:
        if not force:
            if (client.industry_key or "general") != "general":
                return out
            stored_now = stored(client)
            if stored_now.get("topics") or stored_now.get("services"):
                return out
            if stored_now.get(AUTO_MARK):
                out["note"] = "Already tried; edit the answers to try again."
                return out
        marker = dict(stored(client) or {})
        marker[AUTO_MARK] = _now()
        client.ai_collections = dumps(marker)
        db.commit()
        out["attempted"] = True

        answers, error = answer_from_hub(client.name, domain)
        if not answers:
            out["note"] = error
            return out
        built, build_error = build(category=answers["category"],
                                   profile=answers["profile"],
                                   client_name=client.name,
                                   context=answers.get("context"))
        if not built:
            out["note"] = build_error or "Nothing to build from."
            return out
        built[AUTO_MARK] = marker[AUTO_MARK]
        built["auto"] = True
        client.business_category = answers["category"] or None
        client.business_profile = answers["profile"] or None
        client.ai_collections = dumps(built)
        db.commit()
        out["filled"] = True
        out["note"] = build_error or ""
        try:
            from hub import audit
            audit.log("image_picker", "business_profile", actor="system",
                      client=client.name, category=answers["category"],
                      built=built.get("source"), by="auto")
        except Exception:                               # noqa: BLE001
            pass
    except Exception as exc:                            # noqa: BLE001
        log.warning("image_picker autofill errored for %s: %s",
                    getattr(client, "name", "?"), exc)
        try:
            db.rollback()
        except Exception:                               # noqa: BLE001
            pass
        out["note"] = "The answers could not be worked out just now."
    return out


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
        # Whether the answers were worked out from the Hub's own records
        # rather than typed, so the page can say so and offer to change them.
        "auto": bool(data.get("auto")),
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
