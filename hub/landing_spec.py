"""What a landing page is for, and what it is selling.

The maker asked two free-text questions -- "what should the page get people
to do?" and an optional "offer or hook" -- and a third it never asked at all:
what is actually being promoted. Three problems followed from that.

## A typed goal changes the page; a sentence does not

"Book a viewing", "Request a quote" and "Call today" want different pages.
The first needs a date and a property, the second needs enough detail to
price the work, the third needs a phone number above the fold and barely
needs a form. Typed into a box, all three produced the same page with
different words on the button.

So the goal is chosen from ``PAGE_GOALS`` and it decides the call to action,
the form, and what the page has to prove before asking. The renderer already
insists on ONE next step per page -- a page offering three converts on none
-- and this is what that one step is chosen from.

## What is being promoted is not the same as what the page does

The action is "request a quote". The subject is "ducted air conditioning
installation". A page that knows the action and not the subject writes
around the thing it is selling, which is how landing copy ends up saying
"quality service you can trust" four times. ``promoting`` is asked for
directly and carried into the copy brief.

## An offer is read or asked for -- never invented

This is the Smart 1 Labs rule again: a prompt is a request, and "the model
was told not to" is not evidence that it did not. A proposal often has no
offer in it, and the temptation is to write a plausible one -- "free
consultation", "10% off your first service" -- which is a discount the
client never agreed to fund, printed on a page a prospect may act on.

``offer_state()`` sorts what we have into read / unclear / none, and each
gets different handling. None means the page renders with no offer band at
all, which is a shorter page and an honest one.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# What the page should get people to do
# ---------------------------------------------------------------------------
#
# `fields` is the form the renderer builds. Deliberately short: every field
# added to a landing-page form costs completions, so each one here has to earn
# its place for THIS action. `proof` is what the page has to establish before
# it asks -- different for handing over a phone number than for booking a slot.

PAGE_GOALS: list[dict] = [
    {
        "id": "quote",
        "label": "Request a quote",
        "blurb": "They describe the job, we come back with a price.",
        "cta": "Get my quote",
        "fields": ["name", "phone", "email", "postcode", "details"],
        "proof": "that the price will be honest and there is no obligation",
        "kpi": "Form submissions · Cost per lead",
        "guidance": "The visitor wants a number. Say what the quote covers, "
                    "how fast it comes back, and that it costs nothing. Do "
                    "not promise a price range unless the material states one.",
    },
    {
        "id": "call",
        "label": "Call now",
        "blurb": "The phone rings. Best for urgent work.",
        "cta": "Call now",
        "fields": ["name", "phone"],
        "proof": "that someone will actually answer, and how quickly",
        "kpi": "Phone calls · Cost per call",
        "guidance": "The number is the page. Put opening hours and response "
                    "time near it. The form is a callback fallback for people "
                    "who will not ring, so keep it to two fields.",
    },
    {
        "id": "book",
        "label": "Book an appointment",
        "blurb": "A slot in the diary — a viewing, a fitting, a consultation.",
        "cta": "Book my visit",
        "fields": ["name", "phone", "email", "preferred_time"],
        "proof": "what happens at the appointment and how long it takes",
        "kpi": "Bookings · Booking rate",
        "guidance": "Uncertainty is what stops people booking. Say what the "
                    "appointment involves, who turns up, how long it lasts "
                    "and what it costs.",
    },
    {
        "id": "visit",
        "label": "Visit in person",
        "blurb": "Get them to the showroom, forecourt or store.",
        "cta": "Plan your visit",
        "fields": ["name", "email"],
        "proof": "that the trip is worth making — what is there to see",
        "kpi": "Store visits · Directions clicks",
        "guidance": "Address, opening hours and parking are the content. Give "
                    "a reason to come now rather than a general invitation.",
    },
    {
        "id": "apply",
        "label": "Apply for a role",
        "blurb": "Recruitment — get applications in.",
        "cta": "Apply now",
        "fields": ["name", "phone", "email", "role", "details"],
        "proof": "pay, hours and what the work is actually like",
        "kpi": "Applications · Cost per application",
        "guidance": "Applicants screen out on pay and hours. State them if "
                    "the material does. Write about the job, not about how "
                    "great the company is.",
    },
    {
        "id": "register",
        "label": "Register for an event",
        "blurb": "Sign-ups for an open day, webinar or sale.",
        "cta": "Save my place",
        "fields": ["name", "email", "phone"],
        "proof": "date, time, place and what they get out of attending",
        "kpi": "Registrations · Attendance rate",
        "guidance": "The date is the headline. If the material does not give "
                    "a date, time and place, say so rather than writing "
                    "around the gap.",
    },
    {
        "id": "download",
        "label": "Download a guide",
        "blurb": "Trade an email for something useful.",
        "cta": "Send me the guide",
        "fields": ["name", "email"],
        "proof": "that the guide is worth an email address",
        "kpi": "Downloads · Cost per download",
        "guidance": "Describe what is in the document specifically. Never "
                    "describe a guide the client has not actually produced.",
    },
    {
        "id": "enquire",
        "label": "General enquiry",
        "blurb": "When none of the above is quite it.",
        "cta": "Get in touch",
        "fields": ["name", "phone", "email", "details"],
        "proof": "that a real person reads it and replies",
        "kpi": "Form submissions",
        "guidance": "The weakest goal, because it asks for nothing specific. "
                    "Use it only when the campaign genuinely has no single "
                    "next step.",
    },
]

DEFAULT_GOAL = "enquire"

# The form fields the renderer knows how to draw. A goal naming anything
# outside this is a typo, and the check below turns it into a failing test
# rather than a field that silently never appears.
KNOWN_FIELDS = {"name", "phone", "email", "postcode", "details",
                "preferred_time", "role"}

FIELD_LABELS = {
    "name": "Your name",
    "phone": "Phone",
    "email": "Email",
    "postcode": "Postcode or suburb",
    "details": "What do you need?",
    "preferred_time": "Preferred day or time",
    "role": "Which role?",
}


def goal(goal_id: str) -> dict:
    """One goal by id, falling back to the general enquiry.

    Never raises and never returns None: this is read while rendering a page,
    and a KeyError here would be a blank page for a prospect.
    """
    want = _norm(goal_id)
    for g in PAGE_GOALS:
        if g["id"] == want:
            return g
    for g in PAGE_GOALS:
        if _norm(g["label"]) == want:
            return g
    return next(g for g in PAGE_GOALS if g["id"] == DEFAULT_GOAL)


def goal_choices() -> list[dict]:
    """The list the maker page draws, and the only place it comes from."""
    return [{"id": g["id"], "label": g["label"], "blurb": g["blurb"],
             "cta": g["cta"], "kpi": g["kpi"]} for g in PAGE_GOALS]


def form_fields(goal_id: str) -> list[dict]:
    """The form for this goal, as the renderer wants it."""
    return [{"name": f, "label": FIELD_LABELS.get(f, f.title()),
             "type": ("tel" if f == "phone" else
                      "email" if f == "email" else
                      "textarea" if f == "details" else "text"),
             "required": f in ("name", "phone")}
            for f in goal(goal_id)["fields"] if f in KNOWN_FIELDS]


# ---------------------------------------------------------------------------
# The offer
# ---------------------------------------------------------------------------
#
# Three states, because they need three different pages -- not one page with
# an empty string in it.

READ = "read"          # stated plainly; use it
UNCLEAR = "unclear"    # something is there, but not usable as written
NONE = "none"          # nothing; the page runs without an offer band

# Words that mean a discount exists without saying what it is. A hero band
# reading "Special offer available" is worse than no band: it promises the
# visitor something and then does not deliver it.
_VAGUE = re.compile(
    r"^\W*(special|great|amazing|exclusive|limited|seasonal|current)?\s*"
    r"(offer|offers|deal|deals|discount|discounts|promotion|promotions|"
    r"savings|specials?)\b\W*"
    r"(available|now|today|on\s+now|apply|applies)?\W*$", re.I)


def offer_state(offer: str) -> tuple[str, str]:
    """Sort an offer into read / unclear / none, with the reason.

    The reason is shown to the rep, not swallowed. "Unclear" is the state
    worth catching: it is where a page would otherwise print a promise it
    cannot keep.
    """
    text = (offer or "").strip()
    if not text:
        return NONE, ("No offer given. The page will run without an offer "
                      "band rather than inventing one.")
    # The number check comes first. "20% off" is seven characters, so a
    # length test would catch it and hand back the generic message when the
    # specific one -- off WHAT -- is the useful thing to say.
    if re.match(r"^\W*(\d+%|\$\d[\d,]*)\s*(off|discount)?\W*$", text, re.I):
        return UNCLEAR, ("“" + text + "” is a number with nothing attached. "
                         "Say what it applies to and any condition on it.")
    if len(text) < 8 or _VAGUE.match(text):
        return UNCLEAR, ("“" + text + "” says an offer exists without saying "
                         "what it is. Write what the client actually gives — "
                         "or leave it empty and the page will do without.")
    return READ, "Offer will be used as written."


def offer_guidance(offer: str) -> str:
    """What the copy writer is told about the offer, given its state."""
    state, _ = offer_state(offer)
    if state == READ:
        return ("The offer is: " + offer.strip() + ". State it plainly, once, "
                "near the call to action. Do not embellish it, do not add an "
                "expiry, and do not repeat it in every section.")
    return ("There is NO offer for this page. Do not write one. Do not imply "
            "a discount, a free trial, a limited-time deal or a price "
            "advantage of any kind. Sell on what the business does.")


# ---------------------------------------------------------------------------
# What is being promoted
# ---------------------------------------------------------------------------

def promoting_from(brief: dict, typed: str = "") -> str:
    """What this page is selling.

    What the rep typed wins. Otherwise the campaign's own products are the
    best available answer -- they are what the media is buying attention for.
    Returns "" when neither is known, which the caller surfaces as a question
    rather than papering over: a page that does not know what it sells writes
    around the subject, and that is what generic landing copy is.
    """
    if str(typed or "").strip():
        return str(typed).strip()
    products = [p for p in (brief.get("products") or []) if str(p).strip()]
    if products:
        return ", ".join(str(p).strip() for p in products[:4])
    return ""


def copy_brief(brief: dict, goal_id: str, offer: str, promoting: str = "") -> dict:
    """Everything the copy writer is given, assembled in one place.

    The module, the AI prompt and the renderer all read this, so changing
    what a page is written from is one edit rather than three.
    """
    g = goal(goal_id)
    subject = promoting_from(brief, promoting)
    state, reason = offer_state(offer)
    return {
        "business": brief.get("client", ""),
        "industry": brief.get("industry", ""),
        "area": brief.get("geo") or " ".join(
            x for x in (brief.get("city", ""), brief.get("state", "")) if x).strip(),
        "about": brief.get("description", ""),
        "promoting": subject,
        "campaign_objectives": brief.get("objectives", ""),
        "audience": brief.get("audience", ""),
        "conversion_goal": g["label"],
        "goal_id": g["id"],
        "default_cta": g["cta"],
        "must_establish": g["proof"],
        "goal_guidance": g["guidance"],
        "offer_state": state,
        "offer_guidance": offer_guidance(offer),
        "offer_note": reason,
        "measured_by": g["kpi"],
    }


def open_questions(brief: dict, goal_id: str, offer: str,
                   promoting: str = "") -> list[str]:
    """What the rep should answer before this page goes to a prospect.

    Asked, not guessed. Each of these is something a page will otherwise
    write around, and writing around a gap is what produces copy that could
    be about any business in the industry.
    """
    out = []
    if not promoting_from(brief, promoting):
        out.append("What is this page actually promoting? Name the product or "
                   "service — not the industry.")
    state, reason = offer_state(offer)
    if state == UNCLEAR:
        out.append(reason)
    if not (brief.get("geo") or brief.get("city")):
        out.append("Which area does this page serve? Without it the copy "
                   "cannot say where the business works.")
    if goal(goal_id)["id"] == DEFAULT_GOAL:
        out.append("Is a general enquiry really the goal? A page with one "
                   "specific next step converts better than one that just "
                   "invites contact.")
    if not brief.get("phone") and goal(goal_id)["id"] == "call":
        out.append("This page's goal is phone calls and there is no phone "
                   "number on the client record.")
    return out


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())
