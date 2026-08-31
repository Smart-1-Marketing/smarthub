"""Schema questions: ask a lot, answer what we can, block on what we can't.

The Schema Builder used to ask about ten fields and take whatever it found.
That produces valid JSON-LD and weak structured data — Google rewards the
specific fields (price range, service area, accepts reservations, founding
date) that a ten-field set never covers.

So: **35 questions across seven groups**, each answered from the Hub's own
records first and then, for whatever is left, one AI call. Only what still
cannot be found is put to a human.

Reading the client's own website and running a web search were both planned
and neither is built. That is said here, and the two confidence levels for
them are **not reported as zero**: `site: 0` on the panel reads as *their
website had nothing on it*, when nothing ever looked — the difference this
codebase treats as the whole point. `NOT_BUILT` names them.

## Why unanswered questions block approval

An empty field is honest; a plausible guess is not. Structured data is
consumed by machines that treat it as fact — a wrong opening time or a made-up
price range gets served to somebody deciding whether to drive over. So an
answer that couldn't be found is written **NEED ANSWER** in capitals, and
`can_approve()` refuses while any remain. The block is the feature.

## Confidence, and why it's recorded

Each answer carries where it came from:

    known     already on the client record — trusted
    site      read off their own website — trusted
    search    found on the web — worth a glance
    ai        inferred from context — always worth checking
    needed    NEED ANSWER, blocks approval

An answer inferred by AI from an industry is often right and occasionally
confidently wrong. Recording that distinction lets the reviewer spend their
attention where it matters rather than re-reading all 35.
"""
from __future__ import annotations

import json
import re

NEED = "NEED ANSWER"

# (key, question, group, applies_to)  — applies_to "" means every business.
QUESTIONS: tuple[tuple[str, str, str, str], ...] = (
    # --- identity -------------------------------------------------------
    ("legal_name",      "What is the business's full legal name, including any LLC or Inc?", "Identity", ""),
    ("also_known_as",   "Does the business trade under any other name or abbreviation?", "Identity", ""),
    ("founded",         "What year was the business founded?", "Identity", ""),
    ("description",     "In two sentences, what does this business do and for whom?", "Identity", ""),
    ("slogan",          "Does the business have a tagline or slogan?", "Identity", ""),
    ("logo",            "What is the URL of the business logo?", "Identity", ""),
    ("category",        "What is the single best schema.org type for this business (LocalBusiness, Restaurant, HVACBusiness, Attorney, Dentist…)?", "Identity", ""),

    # --- contact --------------------------------------------------------
    ("phone",           "What is the main public phone number?", "Contact", ""),
    ("email",           "What is the main public email address?", "Contact", ""),
    ("address",         "What is the street address?", "Contact", ""),
    ("city",            "Which city?", "Contact", ""),
    ("state",           "Which state?", "Contact", ""),
    ("zip",             "What is the postcode?", "Contact", ""),
    ("country",         "Which country?", "Contact", ""),
    ("latitude",        "What is the latitude of the premises?", "Contact", ""),
    ("longitude",       "What is the longitude of the premises?", "Contact", ""),

    # --- hours ----------------------------------------------------------
    ("hours",           "What are the opening hours, day by day?", "Hours", ""),
    ("hours_notes",     "Any seasonal hours, holiday closures or by-appointment-only periods?", "Hours", ""),
    ("open_24",         "Is the business open 24 hours on any day?", "Hours", ""),

    # --- service --------------------------------------------------------
    ("service_area",    "Which towns, counties or radius does the business serve?", "Service", ""),
    ("services",        "List the main services or product categories offered.", "Service", ""),
    ("price_range",     "What is the typical price range, in $ to $$$$?", "Service", ""),
    ("payment_accepted","Which payment methods are accepted?", "Service", ""),
    ("currencies",      "Which currency does the business price in?", "Service", ""),
    ("languages",       "Which languages does the business serve customers in?", "Service", ""),

    # --- credibility ----------------------------------------------------
    ("founder",         "Who founded the business, or who owns it now?", "Credibility", ""),
    ("employees",       "Roughly how many people work there?", "Credibility", ""),
    ("licenses",        "Does the business hold any licenses, certifications or accreditations worth naming?", "Credibility", ""),
    ("awards",          "Has the business won any awards or notable recognition?", "Credibility", ""),
    ("associations",    "Does it belong to any trade associations or chambers of commerce?", "Credibility", ""),

    # --- online ---------------------------------------------------------
    ("social_profiles", "List the business's social media profile URLs.", "Online", ""),
    ("booking_url",     "Is there a page where customers book, order or request a quote?", "Online", ""),
    ("menu_url",        "Is there a menu, price list or service catalog page?", "Online", "restaurant"),
    ("reservations",    "Does the business accept reservations?", "Online", "restaurant"),
    ("delivery",        "Does it offer delivery, takeaway or on-site service?", "Online", "restaurant"),
)

GROUPS = ("Identity", "Contact", "Hours", "Service", "Credibility", "Online")


def applies(q: tuple, industry: str) -> bool:
    """Skip questions that don't fit the business.

    Asking a law firm about reservations produces a NEED ANSWER that blocks
    approval for something nobody wants answered — which would train people to
    override the block, and then the block is worthless.
    """
    _, _, _, only = q
    if not only:
        return True
    return only.lower() in (industry or "").lower()


# Question keys don't always match the field names the Hub already stores.
# Without this the builder asks for the legal name when it has `name`, and for
# a category when it has `industry` — every needless NEED ANSWER makes the
# approval block feel like bureaucracy rather than a safeguard.
ALIASES = {
    "legal_name":      ("legal_name", "name", "business_name", "client"),
    "category":        ("category", "industry", "vertical", "business_type"),
    "description":     ("description", "about", "summary"),
    "logo":            ("logo", "logo_url", "brand_logo"),
    "phone":           ("phone", "telephone", "main_phone"),
    "email":           ("email", "contact_email"),
    "address":         ("address", "street", "street_address", "address1"),
    "zip":             ("zip", "postcode", "postal_code", "zip_code"),
    "hours":           ("hours", "opening_hours", "business_hours"),
    "service_area":    ("service_area", "areas_served", "radius"),
    "services":        ("services", "products", "offerings"),
    "social_profiles": ("social_profiles", "social", "profiles"),
    "founded":         ("founded", "founding_date", "established", "year_founded"),
    "booking_url":     ("booking_url", "book_url", "quote_url", "contact_url"),
}


def _lookup(known: dict, key: str, typed: dict | None = None):
    """First real value under the question key or any known alias.

    `typed` is what a person saved through `save_answers()`, and it is checked
    first and held to the looser test: their answer is the best source there
    is, and it is the one place "none" means "none".
    """
    typed = typed or {}
    for name in ALIASES.get(key, (key,)):
        v = typed.get(name)
        if not _blank(v, typed=True):
            return v
    for name in ALIASES.get(key, (key,)):
        v = known.get(name)
        if not _blank(v):
            return v
    return None


# Planned and not built. Named rather than left as two confidence levels that
# are always zero: `site: 0` on the panel reads as "their website had nothing
# on it", which is a claim about the client rather than about us.
NOT_BUILT = {
    "site": "Reading the answers off the client's own website is not built. "
            "Nothing here fetches their pages.",
    "search": "Answering from a web search is not built. The AI call is told "
              "to use only what it is given and not to look anything up.",
}

# A human typing "none" is answering the question. Asked whether the business
# has won any awards, holds any licenses or belongs to any trade association,
# "none" is the true answer for most small businesses -- and these strings
# were read as *nothing was filled in*, so the answer was stored, read back as
# blank, and marked NEED ANSWER again. The reviewer typed it, saved it,
# reloaded, and approval was still blocked, for ever, by a question they had
# answered. On the module whose docstring says "The block is the feature".
_JUNK = ("n/a", "none", "unknown", "-")


def _blank(v, typed: bool = False) -> bool:
    """Is there no answer here?

    `typed=True` for a value a person saved: only whitespace is missing then.
    The junk list is for values coming off a *record*, where "n/a" means
    nobody filled the field in -- the opposite of what it means when somebody
    types it into the question "does it hold any licenses?".
    """
    s = str(v or "").strip()
    if not s or s.upper() == NEED:
        return True
    return not typed and s.lower() in _JUNK


def build(client: str, use_ai: bool = True) -> dict:
    """Answer every applicable question, from best source down to AI.

    Order matters: what we already hold beats what a model can infer, and an
    inference is marked as one.
    """
    from hub import seo
    from hub.client_context import context

    store = seo.load_store(client) or {}
    known = dict(store.get("business_info") or {})
    # Kept apart rather than merged in: what a person typed is the one source
    # where "none" is an answer instead of an empty field.
    typed = {k: v for k, v in (store.get("answers") or {}).items() if v}
    try:
        known.update({k: v for k, v in (context(client).get("fields") or {}).items() if v})
    except Exception:                                   # noqa: BLE001
        pass

    industry = str(typed.get("industry") or known.get("industry")
                   or typed.get("category") or known.get("category") or "")
    asked = [q for q in QUESTIONS if applies(q, industry)]

    rows, unknown = [], []
    for key, question, group, _only in asked:
        # `is None` rather than a second _blank(): _lookup has already asked
        # whether there is an answer, and asking again here -- with the strict
        # rule, because this call site cannot know the value came from a
        # person -- is what undid the fix the first time. One reading.
        val = _lookup(known, key, typed)
        if val is not None:
            rows.append({"key": key, "question": question, "group": group,
                         "answer": str(val).strip(), "confidence": "known",
                         "source": "Already on the client record."})
        else:
            unknown.append((key, question, group))

    # One AI call for everything still missing, rather than 35 calls.
    if use_ai and unknown:
        found = _ask_ai(client, known, unknown)
        for key, question, group in unknown:
            ans = (found.get(key) or "").strip()
            if _blank(ans):
                rows.append({"key": key, "question": question, "group": group,
                             "answer": NEED, "confidence": "needed",
                             "source": "Couldn't be found — needs a human answer."})
            else:
                rows.append({"key": key, "question": question, "group": group,
                             "answer": ans, "confidence": "ai",
                             "source": "Inferred from the client's site and "
                                       "record — worth checking."})
    else:
        for key, question, group in unknown:
            rows.append({"key": key, "question": question, "group": group,
                         "answer": NEED, "confidence": "needed",
                         "source": "Not answered yet."})

    rows.sort(key=lambda r: (GROUPS.index(r["group"]) if r["group"] in GROUPS else 9,
                             r["key"]))
    blocking = _blocking(rows)
    needed = [r for r in rows if r["confidence"] == "needed"]
    return {
        "client": client, "industry": industry,
        "questions": rows,
        "total": len(rows),
        "answered": len(rows) - len(blocking),
        "need_answer": len(blocking),
        "need_keys": [r["key"] for r in blocking],
        "can_approve": not blocking,
        # Only the levels this can actually produce. `site` and `search` were
        # reported as 0 by a build that never looks at either, which reads as
        # a fact about the client's website rather than about us; NOT_BUILT
        # names them instead.
        "by_confidence": {c: sum(1 for r in rows if r["confidence"] == c)
                          for c in ("known", "ai", "needed")},
        "not_built": dict(NOT_BUILT),
        "note": _note(blocking, needed),
    }


def _blocking(rows: list[dict]) -> list[dict]:
    """The rows that stop a schema being approved.

    An **AI answer blocks too**, and that is the fix rather than a
    tightening. `can_approve()` re-derived the whole set with `use_ai=False`,
    which turns every inference back into a NEED ANSWER -- so the panel's own
    GET said *"Every question answered. Ready to approve."* in green while the
    POST beside it answered *"N still marked NEED ANSWER, so approval stays
    blocked."* in red. Two readings of one question on one panel, which is the
    `/api/db/structure` versus `/api/integrity` trap.

    Blocking is the reading that matches what this module says it is for: an
    inference is "always worth checking", structured data is read by machines
    as fact, and a plausible guess is worse than an empty field. Saving an AI
    answer is the check -- it becomes one a person typed, and unblocks.
    """
    return [r for r in rows if r["confidence"] in ("needed", "ai")]


def _note(blocking: list[dict], needed: list[dict]) -> str:
    if not blocking:
        return "Every question answered. Ready to approve."
    unchecked = len(blocking) - len(needed)
    parts = []
    if needed:
        parts.append(f"{len(needed)} question(s) marked {NEED}")
    if unchecked:
        parts.append(f"{unchecked} answered by AI and not yet checked")
    return (" and ".join(parts) + ". Schema can't be approved until each has "
            "a real answer — structured data is read by machines as fact, so "
            "a plausible guess is worse than an empty field. Saving an AI "
            "answer is how you confirm it.")


def _ask_ai(client: str, known: dict, unknown: list) -> dict:
    """One call for all outstanding questions. Returns {key: answer}."""
    try:
        from hub import ai
    except Exception:                                   # noqa: BLE001
        return {}
    payload = {
        "business": client,
        "what_we_know": {k: v for k, v in known.items() if v and k != "products"},
        "questions": {k: q for k, q, _g in unknown},
    }
    system = (
        "You answer factual questions about a business so its schema.org "
        "structured data can be filled in. Use ONLY what you are given plus "
        "what is clearly and safely inferable from it.\n\n"
        "This matters more than being helpful: structured data is consumed by "
        "search engines as fact. A wrong phone number, opening time or price "
        "range gets shown to someone deciding whether to travel there.\n\n"
        f"If you cannot answer from the information given, return exactly "
        f"\"{NEED}\" for that key. Do not guess. Do not invent awards, "
        "licenses, founding dates, coordinates or staff numbers — these are "
        "the fields people most often fabricate and the ones most damaging "
        "when wrong.\n\n"
        "Safe inferences: a schema.org type from the industry; a country from "
        "a US state; a currency from a country; a plausible price range band "
        "from the industry IF the services are known.\n\n"
        "Return JSON only: {\"key\": \"answer\"} for every key asked."
    )
    try:
        raw = ai.chat([{"role": "system", "content": system},
                       {"role": "user", "content": json.dumps(payload)}],
                      module="seo", purpose="schema_questions",
                      max_tokens=1600, json_mode=True)
        data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M))
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except Exception:                                   # noqa: BLE001
        return {}


def regenerate_one(client: str, key: str) -> dict:
    """Ask AI again for a single question — the 'New AI' button."""
    q = next((x for x in QUESTIONS if x[0] == key), None)
    if not q:
        return {"error": "No such question."}
    from hub import seo
    store = seo.load_store(client) or {}
    known = dict(store.get("business_info") or {})
    known.update({k: v for k, v in (store.get("answers") or {}).items() if v})
    known.pop(key, None)                    # don't feed it back its own answer
    found = _ask_ai(client, known, [(q[0], q[1], q[2])])
    ans = (found.get(key) or "").strip()
    return {"key": key, "question": q[1],
            "answer": ans or NEED,
            "confidence": "needed" if _blank(ans) else "ai",
            "source": ("Still couldn't be found." if _blank(ans)
                       else "Regenerated — worth checking.")}


def save_answers(client: str, answers: dict, actor: str = "") -> dict:
    """Store edited or approved answers against the client."""
    from hub import audit, seo
    store = seo.load_store(client) or {}
    current = dict(store.get("answers") or {})
    for k, v in (answers or {}).items():
        v = str(v or "").strip()
        if v and v.upper() != NEED:
            current[k] = v
        else:
            current.pop(k, None)            # removing clears rather than storing NEED
    store["answers"] = current
    seo.save_store(client, store)
    audit.log("seo", "schema_answers_saved", actor=actor or None,
              client=client, answered=len(current))
    return {"ok": True, "answered": len(current)}


def can_approve(client: str) -> dict:
    """Whether the schema may be approved, and what's blocking it.

    `use_ai=False` because this runs on every save and a model call there is
    a cost nobody asked for — and it now gives the *same verdict* as the
    panel either way, which is the point: without AI those rows are `needed`,
    with it they are `ai`, and `_blocking()` counts both. The two readings
    could not agree before, so the same screen said "Ready to approve" on
    load and "approval stays blocked" on save.
    """
    d = build(client, use_ai=False)
    return {"can_approve": d["can_approve"], "need_answer": d["need_answer"],
            "blocking": [r["question"] for r in _blocking(d["questions"])][:10],
            "note": d["note"]}
