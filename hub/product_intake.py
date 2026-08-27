"""Turning what a proposal *says* into a line an insertion order can bill.

## The problem this closes

A proposal we sent last quarter is prose. It says "Connected TV — $2,500/mo",
or "Reputation package", or "Strategy retainer, first 90 days". An insertion
order needs a rate-card product, a monthly figure, and a term. Somebody has
always bridged that gap by hand, and the conversion flow made it worse by
*listing* what it could not resolve and then moving on:

    Not on the rate card, so not selected: Reputation package, Strategy
    retainer. Add the matching product yourself if it belongs on this IO.

That reads like information. It is actually the whole decision, handed back to
the rep as a sentence in a chat log they have already scrolled past. The
product was quoted, the client agreed to it, and it silently did not reach the
IO — which is the one document that bills.

So the rule here is: **never state an unresolved product, ask about it.** Every
name the reader pulls out lands in one of three states, and each has a question
attached rather than a note:

    matched   the card has this product, by name or by an anchored prefix.
              Confirm the budget and move on.

    near      the card has candidates and no one of them is certain. Offer
              them, plus "none of these" -- a wrong product on an IO is the
              error a rep cannot spot later, so this never auto-picks.

    unknown   nothing on the card resembles it. Offer to write it as
              Consulting & Strategic Services, described in the client's own
              words, rather than dropping it.

## Cost basis is asked, not assumed

The other half of the gap. A proposal that says "$3,000" has not said whether
that is $3,000 a month for six months or $3,000 once. The IO bills the
difference, and the reader cannot tell them apart from the document, so both
builders ask: monthly or one-time, and for how long.

A one-time cost still has to appear somewhere in a monthly media plan, so
`monthly_equivalent()` spreads it across the flight and says that it did. It is
never folded in silently -- a production fee amortised into a media line is a
media budget that is wrong every month of the campaign.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# The fallback line.
#
# Deliberately NOT added to data/rate_card.json. That file is the wholesale
# card, `check_drift()` compares it against the IO template's embedded copy,
# and inventing a product inside it would make both of those lie. This is a
# line the Hub writes, so the Hub is where it is defined.
# ---------------------------------------------------------------------------
CONSULTING = {
    "product": "Consulting & Strategic Services",
    "category": "ADD-ON PRODUCT",
    "listed_rate": "Custom Quote Required",
    "rate_type": "",
    "rate_value": None,
    "description": "Strategic and consulting work quoted per engagement. Used "
                   "when a proposal committed to something the rate card does "
                   "not name, so the commitment reaches the insertion order "
                   "rather than being dropped for lack of a product code.",
}

MATCHED, NEAR, UNKNOWN = "matched", "near", "unknown"

MONTHLY, ONE_TIME = "monthly", "one_time"
BASIS_LABEL = {MONTHLY: "Monthly, for a set number of months",
               ONE_TIME: "One time"}

# How many candidates are worth offering. Past a handful the list stops being a
# choice and becomes the catalogue the rep was trying to avoid reading.
MAX_CANDIDATES = 5


def _card():
    try:
        from . import rate_card
        return rate_card
    except Exception:                                       # noqa: BLE001
        return None


def _norm(text) -> str:
    return " ".join(str(text or "").strip().lower().split())


def classify(name: str) -> dict:
    """What the card makes of one product name a proposal used.

    Returns {status, product, category, candidates, query}. `product` is set
    only when the status is `matched` -- a near match names its candidates and
    commits to none of them, because the wrong product on an insertion order
    is the error nobody catches until it bills.
    """
    query = str(name or "").strip()
    out = {"status": UNKNOWN, "product": "", "category": "",
           "candidates": [], "query": query}
    rc = _card()
    if not query or rc is None:
        return out

    hit = rc.find(query)
    if hit:
        out.update(status=MATCHED, product=hit.get("product", ""),
                   category=hit.get("category", ""))
        return out

    # Nothing exact or anchored. Offer what the card has that shares the
    # words -- scored per word rather than by searching the whole phrase.
    # A proposal writes "Facebook ads" and "Podcast advertising"; the card
    # says "Facebook | Instagram - Targeted Paid Social Media" and "Podcasts
    # - Targeted". Neither phrase appears in the other, so a substring search
    # for the whole term found nothing and every one of those became an
    # "unknown" the rep had to resolve from scratch.
    near = _by_word(rc, query)
    if near:
        out["status"] = NEAR
        out["candidates"] = [{"product": p.get("product", ""),
                              "category": p.get("category", ""),
                              "listed_rate": p.get("listed_rate", "")}
                             for p in near[:MAX_CANDIDATES]]
    return out


# Words that appear all over the card and identify nothing.
_STOPWORDS = {"ads", "ad", "advertising", "campaign", "campaigns", "marketing",
              "media", "services", "service", "package", "packages", "targeted",
              "target", "monthly", "management", "the", "and", "for", "with"}

# What a proposal is likely to call something, mapped to a word the card uses.
# Only where the two vocabularies genuinely differ -- this is not a synonym
# dictionary, it is the handful of names a rep types that the card spells
# another way.
_ALIASES = {
    "facebook": "facebook", "instagram": "instagram", "meta": "facebook",
    "podcast": "podcasts", "podcasts": "podcasts",
    "preroll": "premium", "pre-roll": "premium",
    "banner": "display", "banners": "display",
    "ctv": "connected", "streaming": "connected",
    "seo": "optimization", "ppc": "click", "sem": "click",
    "retargeting": "retargeting", "remarketing": "retargeting",
    "email": "email", "youtube": "youtube", "radio": "radio",
    "listings": "listing", "reputation": "reputation",
}


def _words(text) -> set:
    return {w for w in _norm(text).replace("|", " ").replace("-", " ").split()
            if len(w) > 2 and w not in _STOPWORDS}


def _by_word(rc, query: str) -> list[dict]:
    """Card products sharing a meaningful word with what was asked for."""
    want = _words(query)
    want |= {_ALIASES[w] for w in list(want) if w in _ALIASES}
    if not want:
        return []
    scored = []
    for prod in rc.products():
        have = _words(prod.get("product", "")) | _words(prod.get("category", ""))
        overlap = len(want & have)
        if overlap:
            # Longer product names match more words by accident, so the score
            # rewards how much of the *query* was accounted for, not how much
            # of the product was.
            scored.append((-overlap / len(want), len(prod.get("product", "")), prod))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in scored[:MAX_CANDIDATES]]


def short_name(product: str) -> str:
    """The name a rep would recognise.

    Several card products carry their whole description in the product field,
    and three of those in one sentence is unreadable. The IO template has had
    its own copy of this for the same reason; this is the shared one.
    """
    name = str(product or "").split(" - This is")[0]
    name = name.split("  *If you")[0].split(" - channels may include")[0]
    name = " ".join(name.split())
    return name if len(name) <= 60 else name[:57] + "\u2026"


def question_for(entry: dict) -> str:
    """The question this line still needs answered, in plain words.

    Empty when nothing is outstanding. One question at a time -- the whole
    point is that the rep is asked rather than shown a list.
    """
    entry = entry or {}
    status = entry.get("status") or classify(entry.get("query", ""))["status"]
    name = entry.get("query") or entry.get("product") or "that product"

    if status == NEAR and not entry.get("product"):
        return (f'The proposal quoted "{name}". Which rate-card product is '
                f'that — or is it none of these?')
    if status == UNKNOWN and not entry.get("product"):
        return (f'"{name}" is not on the rate card. Add it as '
                f'{CONSULTING["product"]}, or pick the product it matches?')
    if not entry.get("basis"):
        return (f'Is {name} a monthly cost or a one-time cost?')
    if entry.get("basis") == MONTHLY and not entry.get("term_months"):
        return f'How many months does {name} run for?'
    if not entry.get("description") and entry.get("consulting"):
        # Only for the catch-all. "Consulting & Strategic Services" on an IO
        # with no description is a line the trafficking team cannot action.
        return f'Describe what {name} covers, in the words the client agreed to.'
    return ""


def resolved(entry: dict) -> bool:
    """Whether this line is ready to go onto an insertion order."""
    return not question_for(entry)


def as_consulting(query: str, description: str = "") -> dict:
    """The catch-all line, for a product the card does not name."""
    return {"query": query, "status": UNKNOWN, "consulting": True,
            "product": CONSULTING["product"], "category": CONSULTING["category"],
            "description": description or query,
            "listed_rate": CONSULTING["listed_rate"]}


def monthly_equivalent(entry: dict, months: int = 1) -> float:
    """What this line contributes to a monthly media plan.

    A one-time cost is spread across the flight rather than dropped or counted
    whole every month. Both of those are wrong in a way that survives review:
    dropped, the plan under-reports what the client owes; counted whole, every
    month of the campaign is overstated by the same amount.
    """
    entry = entry or {}
    try:
        amount = float(entry.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0
    if entry.get("basis") == ONE_TIME:
        term = max(1, int(months or 1))
        return round(amount / term, 2)
    return round(amount, 2)


def campaign_total(entry: dict, months: int = 1) -> float:
    """What this line costs across the whole flight."""
    entry = entry or {}
    try:
        amount = float(entry.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0
    if entry.get("basis") == ONE_TIME:
        return round(amount, 2)
    term = int(entry.get("term_months") or months or 1)
    return round(amount * max(1, term), 2)


def read_products(products: list, months: int = 1) -> list[dict]:
    """Everything a proposal reader pulled out, classified and ready to ask.

    `products` is the reader's own shape -- {product, monthly, note} -- so this
    is the one call the conversion flow makes to turn a document into an
    interview.
    """
    out = []
    for raw in products or []:
        if isinstance(raw, str):
            raw = {"product": raw}
        name = str((raw or {}).get("product") or "").strip()
        if not name:
            continue
        found = classify(name)
        entry = {"query": name, "status": found["status"],
                 "product": found["product"], "category": found["category"],
                 "candidates": found["candidates"],
                 "note": (raw or {}).get("note", ""),
                 "amount": (raw or {}).get("monthly") or 0,
                 # The reader calls its figure "monthly" because that is what a
                 # proposal usually quotes -- but it read a dollar sign, not a
                 # billing cadence, so nothing here assumes one.
                 "basis": "", "term_months": "", "description": "",
                 # What the document said about WHEN this line runs. A
                 # proposal writes "(September - November)" beside a product
                 # and means that product only; the campaign header above it
                 # says something else. Carried verbatim as well as parsed,
                 # because a month with no year is not a date and must not be
                 # turned into one here.
                 "start": (raw or {}).get("start", ""),
                 "end": (raw or {}).get("end", ""),
                 "dates_note": (raw or {}).get("dates_note", "")}
        entry["question"] = question_for(entry)
        out.append(entry)
    return out


def summary(entries: list) -> dict:
    """Where the interview stands: what is settled, what is still being asked."""
    entries = entries or []
    unresolved = [e for e in entries if not resolved(e)]
    return {
        "total": len(entries),
        "matched": len([e for e in entries if e.get("status") == MATCHED]),
        "near": len([e for e in entries if e.get("status") == NEAR]),
        "unknown": len([e for e in entries if e.get("status") == UNKNOWN]),
        "consulting": len([e for e in entries if e.get("consulting")]),
        "unresolved": [e.get("query") or e.get("product") for e in unresolved],
        "ok": not unresolved,
    }


# ---------------------------------------------------------------------------
# The model as a second matcher
#
# `classify()` above is word overlap against the rate card. It is right about
# the products a proposal names the way the card names them, and it is exactly
# as good as the vocabulary the two happen to share -- which on a real document
# is not very. "Stadium to Screen", "Monthly YouTube Sales Video", "ChatGPT /
# AI Advertising", "Website SEO & Listings": a proposal writes what was sold,
# and the card writes a product code with its selling copy stapled to it.
#
# Every one of those arrives as `near` or `unknown`, and the rep then picks the
# product out of a candidate list assembled by counting shared words. That is a
# decision per product, ten times, on the document that bills.
#
# So the model gets a look at whatever the card could not settle. Four rules
# make that safe, and each one is a way this goes quietly wrong without it:
#
#   1. **It never sets `product`.** The suggestion lands in `suggested`, is
#      offered first in the question, and the rep confirms it. A model-chosen
#      product written straight onto an insertion order is the error nobody
#      catches until it bills -- exactly what `classify()` refuses to do with
#      its own best candidate, for the same reason.
#   2. **Only a name the card actually holds survives.** Anything the model
#      returns goes back through `rc.find()`, and a product that does not
#      resolve is dropped rather than shown. A plausible invented product name
#      is worse than no suggestion.
#   3. **One call for the whole list.** Ten products is ten round trips
#      otherwise, on a screen a rep is waiting at.
#   4. **It never raises and never blocks.** No key, no model, a timeout, bad
#      JSON: the result is the card's own matching, exactly as today.
# ---------------------------------------------------------------------------

AI_MAX_PRODUCTS = 25


def _catalogue_names() -> list[str]:
    """Every product the card holds, short enough to put in a prompt."""
    rc = _card()
    if rc is None:
        return []
    names, seen = [], set()
    for prod in rc.products():
        name = short_name(prod.get("product", ""))
        key = _norm(name)
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


_AI_MATCH_SYSTEM = (
    "You map advertising products a proposal named onto a fixed rate card. "
    "You only ever answer with a product that appears on the card given to "
    "you, copied exactly as it is written there. When nothing on the card is "
    "the same product, you answer with an empty string rather than the "
    "nearest-looking row -- a wrong product goes onto an insertion order and "
    "bills. Reply with JSON only."
)


def ai_match(entries: list, catalogue: list | None = None) -> list:
    """Ask the model about the lines the card could not settle.

    Mutates and returns `entries`. A line the card matched outright is left
    alone: the card is the authority on its own products, and re-asking is
    both a wasted decision and a chance to disagree with it.
    """
    rows = [e for e in (entries or [])
            if e and not e.get("product") and (e.get("query") or "")]
    if not rows:
        return entries
    rows = rows[:AI_MAX_PRODUCTS]

    try:
        from . import ai
    except Exception:                                       # noqa: BLE001
        return entries
    if not ai.ready():
        return entries

    names = catalogue if catalogue is not None else _catalogue_names()
    if not names:
        return entries

    asked = [{"quoted": e.get("query", ""),
              "note": str(e.get("note") or "")[:160],
              "card_suggested": [c.get("product", "")
                                 for c in (e.get("candidates") or [])[:MAX_CANDIDATES]]}
             for e in rows]

    prompt = (
        "Here is the rate card, one product per line:\n"
        + "\n".join(f"  - {n}" for n in names)
        + "\n\nHere are the products a proposal named. For each one, give the "
          "rate-card product that is the same thing.\n\n"
        + _json_dumps(asked)
        + "\n\nReturn:\n"
          '{"matches": [{"quoted": "", "product": "", "confidence": "high|'
          'medium|low", "why": ""}]}\n\n'
          "Rules:\n"
          "- `product` must be copied character for character from the card "
          "above, or be an empty string.\n"
          "- Match on what the product IS, not on shared words. A proposal's "
          "name is written for the client; the card's is written for billing.\n"
          "- Empty string when the card has nothing that does the same job. "
          "Saying so is useful; guessing is not.\n"
          "- `why` is one short clause a salesperson would recognize.\n"
          "- Answer for every item, in the order given."
    )

    try:
        out = ai.chat_json(
            [{"role": "system", "content": _AI_MATCH_SYSTEM},
             {"role": "user", "content": prompt}],
            module="io_builder", purpose="product_match",
            max_tokens=1200,
            # Matching a name to a catalogue is a lookup, not a composition:
            # the same proposal has to map the same way twice.
            temperature=0)
    except Exception:                                       # noqa: BLE001
        return entries
    if not isinstance(out, dict):
        return entries

    by_query = {}
    for item in (out.get("matches") or [])[:AI_MAX_PRODUCTS]:
        if not isinstance(item, dict):
            continue
        by_query[_norm(item.get("quoted"))] = item

    rc = _card()
    for pos, entry in enumerate(rows):
        item = by_query.get(_norm(entry.get("query")))
        if item is None and pos < len(out.get("matches") or []):
            # The model answered in order but renamed the key. Positional
            # fallback, because dropping a whole batch over a paraphrase is
            # the wrong trade -- and every name still has to resolve below.
            cand = (out.get("matches") or [])[pos]
            item = cand if isinstance(cand, dict) else None
        if not item:
            continue
        picked = str(item.get("product") or "").strip()
        if not picked:
            continue
        hit = rc.find(picked) if rc is not None else None
        if not hit or not hit.get("product"):
            # Not on the card. This is the guard that matters: a model that
            # invents "Connected TV - Local" produces a product code nothing
            # downstream recognises, on a document nobody re-reads.
            continue
        entry["suggested"] = hit.get("product", "")
        entry["suggested_category"] = hit.get("category", "")
        entry["suggested_by"] = "ai"
        entry["suggested_confidence"] = str(
            item.get("confidence") or "").strip().lower()[:10]
        entry["suggested_why"] = str(item.get("why") or "").strip()[:160]
        # Offered first in the question. Still a candidate, still confirmed --
        # the ordering is the only thing the model buys the rep here.
        cands = [c for c in (entry.get("candidates") or [])
                 if _norm(c.get("product")) != _norm(hit.get("product"))]
        entry["candidates"] = [{"product": hit.get("product", ""),
                                "category": hit.get("category", ""),
                                "listed_rate": hit.get("listed_rate", "")}
                               ] + cands
        entry["status"] = NEAR
        entry["question"] = question_for(entry)
    return entries


def _json_dumps(value) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)
