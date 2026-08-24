"""What a GPT ad is made of — the ad-ops checklist, as data.

The requirement sheet ad operations work from lists five things, and a pack
missing any one of them comes straight back:

    1. Static square image     1:1 required, 256x256 recommended minimum
    2. Ad copy options         short headlines, concise body, CTA options
    3. Landing page URL        final destination, tracking, live and mobile
    4. Brand requirements      logo, colours, tone, claims, disclaimers, legal,
                               and who signs it off
    5. Offer details           promotion, pricing, eligibility, expiry,
                               restrictions

This module is that list, plus the checks that decide whether a pack is
actually ready to send. It follows ``hub/proposal_spec.py`` and
``hub/social_plan.py``: the module, the export and the AI prompt all read the
same definitions, so changing what a GPT ad pack contains is one edit rather
than three.

## What is checked rather than requested

A prompt is a request, and "the model was told not to" is not evidence that it
did not. Four things here are code:

* **The image is measured, not described.** ``creative_specs.check()`` reads
  the real pixels of the real file. A rep who types "1080x1080" into a form and
  attaches a 1200x628 crop is the ordinary way a rejected ad happens, and the
  form field would have said yes.

* **The landing page is fetched.** "Confirmation that the page is live and
  mobile-friendly" is on the sheet as a *deliverable*, so it is answered by
  requesting the page — status code, redirect chain, and whether the document
  declares a viewport — rather than by a tick box a rep can tick from memory.
  A check that could not run reports *not measured*; it never reports a pass.

* **Invented commercial facts block the pack.** A price, a percentage, a phone
  number or a deadline in the copy that is not in the offer or brand fields the
  human filled in is a block. This is the same rule and the same expressions as
  the Social Content Planner — imported from there rather than restated, so the
  next fix to those patterns lands once.

* **An expired offer blocks the pack.** An expiry date in the past shipped to
  ad ops is an ad that runs saying something false.

## What is deliberately not invented

The requirement sheet gives no character limits and no file-weight ceiling. The
limits in ``LIMITS`` below are *our* working guidance, labelled as ours
everywhere they are shown, and going over one is a warning — never a block,
because a block implies a platform rejection nobody has published. Where the
sheet is silent, the export says "not supplied" rather than filling a plausible
value in.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import re
from urllib.parse import urlparse

from hub import creative_specs

# The image rule lives in the shared spec kit, not here. This module names the
# unit; hub/creative_specs.py owns the numbers, so the gallery, the insertion
# order and this tool all judge the same file the same way.
IMAGE_UNIT = "gpt_ads_square"


# ---------------------------------------------------------------------------
# The five deliverables
# ---------------------------------------------------------------------------
# `requirement` is the sheet's own wording, near enough to be recognisable when
# a rep compares the screen with the email that asked for it.
DELIVERABLES: list[dict] = [
    {"key": "image", "label": "Static square image",
     "requirement": "1:1 ratio required; 256 x 256 px recommended. Provide the "
                    "highest-quality brand-approved version available."},
    {"key": "copy", "label": "Ad copy options",
     "requirement": "Short headlines / titles, concise body copy, and "
                    "call-to-action options."},
    {"key": "landing", "label": "Landing page URL",
     "requirement": "Final destination URL, tracking parameters if needed, and "
                    "confirmation that the page is live and mobile-friendly."},
    {"key": "brand", "label": "Brand requirements",
     "requirement": "Logo usage, colors, tone, claims, disclaimers, legal "
                    "language, and approval contacts."},
    {"key": "offer", "label": "Offer details",
     "requirement": "Promotion, pricing, product/service details, eligibility "
                    "rules, expiration dates, and restrictions."},
]

DELIVERABLE_KEYS = tuple(d["key"] for d in DELIVERABLES)
DELIVERABLE_LABELS = {d["key"]: d["label"] for d in DELIVERABLES}

STATUSES = ("draft", "review", "ready")


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------
# House guidance, not a published platform limit — see the note at the top.
# Every screen and the export label them as ours, because a number presented as
# a platform rule is one nobody argues with even when it is wrong.
LIMITS = {
    "headline": 40,
    "body": 90,
    "cta": 20,
}
LIMITS_SOURCE = ("Smart 1 house guidance — the requirement sheet publishes no "
                 "character limits. Over is a warning, not a rejection.")

# How many options ad ops expects to choose from. Fewer than this is a block:
# one headline is not "headline options", and the pack goes back.
MIN_OPTIONS = {"headlines": 3, "bodies": 2, "ctas": 2}
TARGET_OPTIONS = {"headlines": 5, "bodies": 3, "ctas": 3}
MAX_OPTIONS = 12

# A CTA is picked from this list rather than written, so ad ops gets a value
# their platform actually offers instead of a sentence.
CTA_OPTIONS = (
    "Learn More", "Get a Quote", "Get Started", "Book Now", "Schedule Service",
    "Shop Now", "Sign Up", "Call Now", "Contact Us", "See Pricing",
    "Request a Demo", "Find a Location",
)


# ---------------------------------------------------------------------------
# The copy checks
# ---------------------------------------------------------------------------
# Imported, not restated. hub/social_plan.py owns these patterns; a GPT ad has
# exactly the same failure mode as a social post — confident, plausible copy
# quoting an offer nobody authorised — and two copies of the expressions means
# the next fix lands in one of them.
from hub.social_plan import (  # noqa: E402  (after the docstring, by design)
    BANNED_PHRASES,
    DEADLINE_RE,
    MONEY_RE,
    PERCENT_RE,
    PHONE_RE,
    PLACEHOLDER_RE,
    SUPERLATIVE_RE,
)

_DIGITS = re.compile(r"\D")


def _digits(value: str) -> str:
    return _DIGITS.sub("", str(value or ""))


def authorised_text(ad: dict) -> str:
    """Everything a human actually typed for this ad, as one blob.

    A commercial claim in the copy is permitted when it traces back to
    something here. Blunt on purpose: it will occasionally flag copy that is
    fine, and the failure it prevents is an ad running a discount the client
    never offered.
    """
    ad = ad or {}
    offer = ad.get("offer") or {}
    brand = ad.get("brand") or {}
    landing = ad.get("landing") or {}
    parts = [
        offer.get("summary"), offer.get("pricing"), offer.get("product"),
        offer.get("eligibility"), offer.get("expires"), offer.get("restrictions"),
        brand.get("claims"), brand.get("disclaimer"), brand.get("legal"),
        brand.get("tone"), brand.get("phone"),
        landing.get("url"), landing.get("tracking"),
        ad.get("notes"),
    ]
    return " \n".join(str(p or "") for p in parts)


def validate_copy(text: str, kind: str = "headline", ad: dict | None = None) -> list[dict]:
    """Flags on one line of copy. `block` must be resolved; `warn` is advice.

    Never raises and never rewrites. A check that edits the copy is a check
    nobody can audit afterwards.
    """
    text = str(text or "").strip()
    flags: list[dict] = []
    if not text:
        return flags
    allowed = authorised_text(ad or {})
    allowed_low = allowed.lower()
    allowed_digits = _digits(allowed)
    low = text.lower()

    for phrase in BANNED_PHRASES:
        if phrase in low:
            flags.append({"level": "block", "code": "banned",
                          "message": f"Mentions “{phrase}” — never goes to a client."})
            break

    for found in MONEY_RE.findall(text):
        token = found.strip()
        if token.lower() not in allowed_low:
            flags.append({"level": "block", "code": "price",
                          "message": f"“{token}” isn't in the offer details you "
                                     "filled in — confirm it or take it out."})
            break
    else:
        for found in PERCENT_RE.findall(text):
            if found.strip() not in allowed:
                flags.append({"level": "warn", "code": "percent",
                              "message": f"“{found.strip()}” isn't in the offer "
                                         "details — check it."})
                break

    for found in PHONE_RE.findall(text):
        if _digits(found) and _digits(found) not in allowed_digits:
            flags.append({"level": "block", "code": "phone",
                          "message": f"Phone number “{found.strip()}” doesn't "
                                     "match anything on file."})
            break

    hit = DEADLINE_RE.search(text)
    if hit and hit.group(0).lower() not in allowed_low:
        flags.append({"level": "block", "code": "deadline",
                      "message": f"“{hit.group(0)}” promises a deadline that is "
                                 "not in the offer details."})

    hit = PLACEHOLDER_RE.search(text)
    if hit:
        flags.append({"level": "block", "code": "placeholder",
                      "message": f"Unfilled placeholder: “{hit.group(0)}”."})

    hit = SUPERLATIVE_RE.search(text)
    if hit:
        flags.append({"level": "warn", "code": "superlative",
                      "message": f"“{hit.group(0)}” is a claim the client may "
                                 "not be able to substantiate."})

    limit = LIMITS.get(kind)
    if limit and len(text) > limit:
        flags.append({"level": "warn", "code": "length",
                      "message": f"{len(text)} characters — our guidance for "
                                 f"{kind} copy is {limit}. No published limit "
                                 "says this is rejected."})
    return flags


# ---------------------------------------------------------------------------
# The landing page
# ---------------------------------------------------------------------------
VIEWPORT_RE = re.compile(r"""<meta[^>]+name\s*=\s*["']?viewport""", re.I)


def normalise_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    return url[:600]


def check_landing_page(url: str, timeout: int = 12) -> dict:
    """Fetch the destination and report what came back.

    Everything absent reads as absent. A page we could not reach is
    ``checked: True, ok: False`` with the reason; a check that never ran is
    ``checked: False`` — and neither is ever rendered as a tick. Never raises:
    a network failure here must not cost a rep the rest of the pack.
    """
    out = {"checked": False, "url": normalise_url(url), "final_url": "",
           "status": 0, "ok": False, "https": False, "mobile": None,
           "note": "", "at": ""}
    if not out["url"]:
        out["note"] = "No landing page URL yet."
        return out
    out["https"] = out["url"].lower().startswith("https://")

    try:
        import requests
        r = requests.get(out["url"], timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "Smart1Hub-GPTAds/1.0"})
    except Exception as exc:                          # noqa: BLE001
        out["checked"] = True
        out["at"] = _now()
        out["note"] = (f"Could not reach the page ({type(exc).__name__}). "
                       "Ad ops will hit the same wall — check the URL.")
        return out

    out["checked"] = True
    out["at"] = _now()
    out["status"] = int(getattr(r, "status_code", 0) or 0)
    out["final_url"] = str(getattr(r, "url", "") or "")[:600]
    out["ok"] = 200 <= out["status"] < 400

    body = ""
    try:
        body = (r.text or "")[:200000]
    except Exception:                                 # noqa: BLE001
        body = ""
    if body:
        # A viewport meta tag is not proof a page is mobile-friendly, and its
        # absence is close to proof that it is not. So a hit is reported as
        # "declares a viewport", not as "mobile-friendly" — the sheet asks a
        # human to confirm that, and this narrows what they have to look at.
        out["mobile"] = bool(VIEWPORT_RE.search(body))
    else:
        out["mobile"] = None

    if not out["ok"]:
        out["note"] = f"The page returned HTTP {out['status']}."
    elif out["final_url"] and out["final_url"].rstrip("/") != out["url"].rstrip("/"):
        out["note"] = f"Redirects to {out['final_url']} — send ad ops that one."
    return out


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The readiness gate
# ---------------------------------------------------------------------------
def _expiry_date(value: str):
    """The offer's expiry as a date, or None. Accepts what people type."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d %B %Y", "%B %d, %Y",
                "%b %d, %Y", "%m-%d-%Y"):
        try:
            return _dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def image_verdict(image: dict | None) -> dict:
    """The shared spec kit's judgement on the attached file.

    Uses the measured pixels stored when the file was attached, never the size
    a form said it was.
    """
    image = image or {}
    if not image.get("url"):
        return {"result": "missing", "summary": "No square image attached yet.",
                "checks": [], "unit": None}
    width, height = int(image.get("width") or 0), int(image.get("height") or 0)
    if not (width and height):
        # Absent data must read as "not measured", not as a pass.
        return {"result": "unknown",
                "summary": "This file's dimensions were never measured, so "
                           "whether it is 1:1 is unknown.",
                "checks": [], "unit": None}
    return creative_specs.check(
        width=width, height=height, size_bytes=int(image.get("bytes") or 0),
        fmt=str(image.get("format") or ""), unit_id=IMAGE_UNIT)


def copy_options(ad: dict, kind: str) -> list[dict]:
    rows = ((ad or {}).get("copy") or {}).get(kind) or []
    return [r for r in rows if isinstance(r, dict) and str(r.get("text") or "").strip()]


def revalidate(ad: dict) -> dict:
    """Re-flag every line of copy in place. Returns the ad."""
    copy = (ad or {}).setdefault("copy", {})
    for kind, singular in (("headlines", "headline"), ("bodies", "body"),
                           ("ctas", "cta")):
        rows = copy.get(kind) or []
        for row in rows:
            if isinstance(row, dict):
                row["flags"] = validate_copy(row.get("text", ""), singular, ad)
    return ad


def readiness(ad: dict) -> dict:
    """Per-deliverable state, and whether the pack can be sent.

    `block` means ad ops would send it back or something false would run.
    `warn` means it will go, with something worth a second look. Nothing here
    silently downgrades a missing thing into a present one.
    """
    ad = revalidate(ad or {})
    sections: list[dict] = []

    def section(key, flags):
        blocks = [f for f in flags if f["level"] == "block"]
        warns = [f for f in flags if f["level"] == "warn"]
        return {"key": key, "label": DELIVERABLE_LABELS[key],
                "state": "block" if blocks else ("warn" if warns else "ok"),
                "flags": flags}

    # ---- 1. the image ----
    flags: list[dict] = []
    image = ad.get("image") or {}
    verdict = image_verdict(image)
    if verdict["result"] == "missing":
        flags.append({"level": "block", "code": "image_missing",
                      "message": "No square image attached."})
    elif verdict["result"] == "fail":
        flags.append({"level": "block", "code": "image_spec",
                      "message": verdict["summary"]})
    elif verdict["result"] == "unknown":
        flags.append({"level": "block", "code": "image_unmeasured",
                      "message": verdict["summary"]})
    elif verdict["result"] == "warn":
        flags.append({"level": "warn", "code": "image_soft",
                      "message": verdict["summary"]})
    if image.get("url") and not image.get("alt"):
        flags.append({"level": "warn", "code": "image_alt",
                      "message": "No description of the image for the handoff."})
    if image.get("mirror_failed"):
        flags.append({"level": "warn", "code": "image_mirror",
                      "message": "This image is still on the provider's own "
                                 "URL — that link expires. Re-save it."})
    sections.append(section("image", flags))

    # ---- 2. the copy ----
    flags = []
    counts = {k: len(copy_options(ad, k)) for k in ("headlines", "bodies", "ctas")}
    names = {"headlines": "headline", "bodies": "body copy", "ctas": "CTA"}
    for kind, minimum in MIN_OPTIONS.items():
        if counts[kind] < minimum:
            flags.append({"level": "block", "code": f"copy_{kind}",
                          "message": f"{counts[kind]} {names[kind]} option(s) — "
                                     f"ad ops needs at least {minimum} to "
                                     "choose from."})
    for kind, singular in (("headlines", "headline"), ("bodies", "body"),
                           ("ctas", "cta")):
        for row in copy_options(ad, kind):
            for flag in row.get("flags") or []:
                flags.append({"level": flag["level"], "code": "copy_" + flag["code"],
                              "message": f"{names[kind].title()} “"
                                         f"{str(row.get('text'))[:40]}”: "
                                         f"{flag['message']}"})
    sections.append(section("copy", flags))

    # ---- 3. the landing page ----
    flags = []
    landing = ad.get("landing") or {}
    url = normalise_url(landing.get("url"))
    check = landing.get("check") or {}
    if not url:
        flags.append({"level": "block", "code": "landing_missing",
                      "message": "No landing page URL."})
    else:
        if not url.lower().startswith("https://"):
            flags.append({"level": "block", "code": "landing_http",
                          "message": "The destination is http:// — ad platforms "
                                     "reject a non-secure landing page."})
        if not check.get("checked"):
            flags.append({"level": "warn", "code": "landing_unchecked",
                          "message": "Live and mobile-friendly is not measured — "
                                     "run the page check."})
        elif not check.get("ok"):
            flags.append({"level": "block", "code": "landing_down",
                          "message": check.get("note") or "The page did not "
                                     "answer when we requested it."})
        else:
            if check.get("mobile") is False:
                flags.append({"level": "warn", "code": "landing_mobile",
                              "message": "The page declares no viewport, so it "
                                         "is probably not mobile-friendly."})
            elif check.get("mobile") is None:
                flags.append({"level": "warn", "code": "landing_mobile_unknown",
                              "message": "Mobile-friendliness not measured — we "
                                         "could not read the page's markup."})
            if check.get("note") and check.get("ok"):
                flags.append({"level": "warn", "code": "landing_redirect",
                              "message": check["note"]})
    sections.append(section("landing", flags))

    # ---- 4. brand requirements ----
    flags = []
    brand = ad.get("brand") or {}
    if not str(brand.get("approver_name") or "").strip() and \
            not str(brand.get("approver_email") or "").strip():
        flags.append({"level": "block", "code": "brand_approver",
                      "message": "No approval contact. Ad ops cannot get "
                                 "creative signed off without a name."})
    if not str(brand.get("logo_url") or "").strip():
        flags.append({"level": "warn", "code": "brand_logo",
                      "message": "No logo on file for this pack."})
    if not (brand.get("colors") or []):
        flags.append({"level": "warn", "code": "brand_colors",
                      "message": "No brand colours recorded."})
    if not str(brand.get("tone") or "").strip():
        flags.append({"level": "warn", "code": "brand_tone",
                      "message": "No tone guidance for whoever adapts the copy."})
    sections.append(section("brand", flags))

    # ---- 5. offer details ----
    flags = []
    offer = ad.get("offer") or {}
    has_offer = any(str(offer.get(k) or "").strip()
                    for k in ("summary", "pricing", "product"))
    if not has_offer:
        flags.append({"level": "block", "code": "offer_missing",
                      "message": "Nothing describing what is being advertised."})
    expires_raw = str(offer.get("expires") or "").strip()
    expiry = _expiry_date(expires_raw)
    if expires_raw and expiry is None:
        flags.append({"level": "warn", "code": "offer_expiry_unparsed",
                      "message": f"“{expires_raw}” isn't a date we can read, so "
                                 "we cannot tell you whether it has passed."})
    elif expiry and expiry < _dt.date.today():
        flags.append({"level": "block", "code": "offer_expired",
                      "message": f"This offer expired on {expiry.isoformat()}. "
                                 "Shipping it means running something false."})
    if str(offer.get("pricing") or "").strip() and \
            not str(offer.get("restrictions") or "").strip() and \
            not str((ad.get("brand") or {}).get("disclaimer") or "").strip():
        flags.append({"level": "warn", "code": "offer_smallprint",
                      "message": "A price with no restrictions or disclaimer — "
                                 "check whether one is required."})
    sections.append(section("offer", flags))

    blocks = sum(len([f for f in s["flags"] if f["level"] == "block"]) for s in sections)
    warns = sum(len([f for f in s["flags"] if f["level"] == "warn"]) for s in sections)
    return {"sections": sections, "block": blocks, "warn": warns,
            "ready": blocks == 0,
            "complete": sum(1 for s in sections if s["state"] == "ok"),
            "total": len(sections)}


# ---------------------------------------------------------------------------
# The AI prompt — one request per copy kind
# ---------------------------------------------------------------------------
def _facts(ad: dict, context: dict | None = None) -> list[str]:
    context = context or {}
    offer = ad.get("offer") or {}
    brand = ad.get("brand") or {}
    landing = ad.get("landing") or {}
    facts = [f"Business: {ad.get('client') or context.get('client') or 'the client'}"]
    if context.get("description"):
        facts.append(f"What they do: {context['description']}")
    if context.get("products"):
        facts.append("Products running with us: " + ", ".join(context["products"][:8]))
    if offer.get("product"):
        facts.append(f"Product or service being advertised: {offer['product']}")
    if offer.get("summary"):
        facts.append(f"The promotion (the ONLY offer you may mention): {offer['summary']}")
    else:
        facts.append("No promotion supplied. Mention no offer, discount or price.")
    if offer.get("pricing"):
        facts.append(f"Pricing you may state exactly as written: {offer['pricing']}")
    if offer.get("eligibility"):
        facts.append(f"Who is eligible: {offer['eligibility']}")
    if offer.get("expires"):
        facts.append(f"Offer ends: {offer['expires']}")
    if offer.get("restrictions"):
        facts.append(f"Restrictions: {offer['restrictions']}")
    if brand.get("tone"):
        facts.append(f"Tone: {brand['tone']}")
    if brand.get("claims"):
        facts.append(f"Claims the client has approved: {brand['claims']}")
    if brand.get("legal"):
        facts.append(f"Required legal language: {brand['legal']}")
    if brand.get("avoid"):
        facts.append(f"Never mention: {brand['avoid']}")
    if landing.get("url"):
        facts.append(f"Where the ad goes: {landing['url']}")
    if ad.get("notes"):
        facts.append(f"Notes from the rep: {ad['notes']}")
    return facts


_KIND_BRIEF = {
    "headlines": ("headline", "Short titles. Each names the business's service "
                  "or the offer and gives one reason to click. No sentence "
                  "fragments that could be about any business."),
    "bodies": ("body", "One or two lines of body copy supporting a headline. "
               "Concrete about what the customer gets."),
    "ctas": ("cta", "Call-to-action labels, two to four words, imperative."),
}


def copy_messages(ad: dict, kind: str, context: dict | None = None,
                  count: int = 0) -> list[dict]:
    """One request per copy kind.

    Split for the same reason the Proposal Builder splits its sections: one
    refusal or one timeout costs one kind rather than all three, and the loader
    can name what it is working on.
    """
    singular, brief = _KIND_BRIEF.get(kind, _KIND_BRIEF["headlines"])
    count = count or TARGET_OPTIONS.get(kind, 5)
    limit = LIMITS[singular]

    system = (
        "You write short paid-ad copy for a local business, on behalf of the "
        "business itself. You are never the agency and you never mention one.\n\n"
        "Hard rules — copy breaking any of these is discarded before a person "
        "sees it:\n"
        "1. Invent no prices, discounts, percentages, phone numbers, addresses, "
        "deadlines, awards or statistics. Use only the facts given below. If a "
        "fact would make the line better and you do not have it, write the line "
        "without it.\n"
        "2. No superlatives or unprovable claims — no “best”, “#1”, "
        "“guaranteed”, “cheapest”, “award-winning”.\n"
        "3. Leave no placeholders. If something is missing, write around it.\n"
        "4. Plain language, the business's own voice. No corporate filler.\n"
        "5. Every option must be usable on its own — these are alternatives for "
        "a media buyer to choose between, not a sequence.\n"
    )
    if kind == "ctas":
        system += ("6. Return only labels from this list, choosing the ones that "
                   "fit: " + ", ".join(CTA_OPTIONS) + ".\n")

    user = (
        f"Write {count} {singular} options for a GPT ad.\n\n"
        f"What this is: {brief}\n\n"
        f"Aim for {limit} characters or fewer per option — that is our own "
        "guidance, not a platform limit, so a slightly longer line that reads "
        "better is acceptable.\n\n"
        "Facts you may use:\n" + "\n".join(f"- {f}" for f in _facts(ad, context)) +
        "\n\nReturn JSON: {\"options\": [\"first\", \"second\", …]}."
    )
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def image_prompt(ad: dict, context: dict | None = None) -> str:
    """The prompt for a generated square. Brand-aware, and it never draws text.

    Generated lettering arrives misspelled often enough that a headline baked
    into the image is a rejected ad; the copy is a separate deliverable on the
    sheet for exactly that reason.
    """
    context = context or {}
    offer = ad.get("offer") or {}
    brand = ad.get("brand") or {}
    bits = [
        f"A square 1:1 advertising image for {ad.get('client') or 'a local business'}",
    ]
    if offer.get("product"):
        bits.append(f"advertising {offer['product']}")
    elif context.get("description"):
        bits.append(f"whose business is: {context['description'][:300]}")
    if brand.get("colors"):
        bits.append("using the brand colours " + ", ".join(list(brand["colors"])[:4]))
    if brand.get("tone"):
        bits.append(f"tone: {brand['tone']}")
    if ad.get("image_brief"):
        bits.append(str(ad["image_brief"])[:400])
    bits.append(
        "Photographic and realistic, clean composition with the subject "
        "centred and clear space around it so it survives being downsized to "
        "256x256. Absolutely no text, letters, numbers, logos, watermarks or "
        "signage anywhere in the image."
    )
    return ". ".join(bits)


# ---------------------------------------------------------------------------
# The handoff pack
# ---------------------------------------------------------------------------
COPY_COLUMNS = ("Type", "Option", "Characters", "Needs attention")

NOT_SUPPLIED = "not supplied"


def _v(value) -> str:
    text = str(value or "").strip()
    return text or NOT_SUPPLIED


def copy_csv(ad: dict) -> str:
    """The copy options as a sheet ad ops can paste into a trafficking form."""
    ad = revalidate(ad or {})
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(COPY_COLUMNS)
    for kind, label in (("headlines", "Headline"), ("bodies", "Body"),
                        ("ctas", "CTA")):
        for row in copy_options(ad, kind):
            text = str(row.get("text") or "").strip()
            flags = "; ".join(f"{f['level']}: {f['message']}"
                              for f in (row.get("flags") or []))
            writer.writerow([label, text, len(text), flags])
    return buf.getvalue()


def manifest(ad: dict, *, image_filename: str = "") -> dict:
    """The machine-readable half of the pack.

    What is absent is named as absent — a key that quietly disappears reads, to
    whoever opens this next, as a thing that was never required.
    """
    ad = ad or {}
    state = readiness(ad)
    image = ad.get("image") or {}
    verdict = image_verdict(image)
    landing = ad.get("landing") or {}
    return {
        "generated": _now(),
        "generated_by": ad.get("updated_by") or ad.get("created_by") or "",
        "pack": "GPT ad",
        "client": ad.get("client") or "",
        "client_domain": ad.get("domain") or "",
        "campaign": ad.get("campaign") or "",
        "status": ad.get("status") or "draft",
        "ready_to_traffic": state["ready"],
        "outstanding": [
            {"deliverable": s["label"], "level": f["level"], "note": f["message"]}
            for s in state["sections"] for f in s["flags"]
        ],
        "image": {
            "file": image_filename or "",
            "url": image.get("url") or "",
            "width": image.get("width") or 0,
            "height": image.get("height") or 0,
            "bytes": image.get("bytes") or 0,
            "format": image.get("format") or "",
            "source": image.get("source") or "",
            "alt": image.get("alt") or "",
            "spec_result": verdict.get("result"),
            "spec_summary": verdict.get("summary"),
        },
        "copy": {
            kind: [str(r.get("text") or "") for r in copy_options(ad, kind)]
            for kind in ("headlines", "bodies", "ctas")
        },
        "copy_guidance": {"limits": dict(LIMITS), "source": LIMITS_SOURCE},
        "landing_page": {
            "url": landing.get("url") or "",
            "tracking": landing.get("tracking") or "",
            "checked": bool((landing.get("check") or {}).get("checked")),
            "live": (landing.get("check") or {}).get("ok"),
            "http_status": (landing.get("check") or {}).get("status") or 0,
            "declares_viewport": (landing.get("check") or {}).get("mobile"),
            "checked_at": (landing.get("check") or {}).get("at") or "",
            "note": (landing.get("check") or {}).get("note") or "",
        },
        "brand": dict(ad.get("brand") or {}),
        "offer": dict(ad.get("offer") or {}),
        "notes": ad.get("notes") or "",
    }


def _flag_lines(state: dict) -> list[str]:
    lines = []
    for section in state["sections"]:
        for flag in section["flags"]:
            mark = "MUST FIX" if flag["level"] == "block" else "check"
            lines.append(f"  [{mark}] {section['label']}: {flag['message']}")
    return lines


def handoff_brief(ad: dict, *, image_filename: str = "") -> str:
    """The human half of the pack — one page ad ops reads before they traffic.

    Deliberately plain text rather than a PDF: it is read in an email client,
    pasted into a ticket and searched, and every one of those is worse with a
    PDF. The section order is the requirement sheet's order, so it can be
    checked against the sheet line by line.
    """
    ad = ad or {}
    state = readiness(ad)
    image = ad.get("image") or {}
    verdict = image_verdict(image)
    landing = ad.get("landing") or {}
    check = landing.get("check") or {}
    brand = ad.get("brand") or {}
    offer = ad.get("offer") or {}

    L: list[str] = []
    L.append("GPT AD — CREATIVE HANDOFF")
    L.append("=" * 60)
    L.append(f"Client:    {_v(ad.get('client'))}")
    L.append(f"Campaign:  {_v(ad.get('campaign'))}")
    L.append(f"Prepared:  {_now()} by {_v(ad.get('updated_by') or ad.get('created_by'))}")
    L.append(f"Status:    {'READY TO TRAFFIC' if state['ready'] else 'NOT READY — see below'}")
    L.append("")

    if state["block"] or state["warn"]:
        L.append("OUTSTANDING")
        L.append("-" * 60)
        L.extend(_flag_lines(state))
        L.append("")

    L.append("1. STATIC SQUARE IMAGE")
    L.append("-" * 60)
    if image.get("url"):
        L.append(f"File:        {image_filename or '(link only — see URL below)'}")
        L.append(f"URL:         {image['url']}")
        L.append(f"Dimensions:  {image.get('width') or '?'} x {image.get('height') or '?'}"
                 f"   ({image.get('format') or '?'}, {image.get('bytes') or 0} bytes)")
        L.append(f"Spec check:  {verdict.get('result', 'unknown')} — {verdict.get('summary', '')}")
        L.append(f"Describes:   {_v(image.get('alt'))}")
        L.append(f"Source:      {_v(image.get('source'))}")
    else:
        L.append("No image attached. " + DELIVERABLES[0]["requirement"])
    L.append("")

    L.append("2. AD COPY OPTIONS")
    L.append("-" * 60)
    L.append(f"({LIMITS_SOURCE})")
    for kind, label in (("headlines", "Headlines"), ("bodies", "Body copy"),
                        ("ctas", "Calls to action")):
        rows = copy_options(ad, kind)
        L.append(f"{label} ({len(rows)}):")
        if not rows:
            L.append(f"  {NOT_SUPPLIED}")
        for row in rows:
            text = str(row.get("text") or "").strip()
            L.append(f"  - {text}   [{len(text)} chars]")
        L.append("")

    L.append("3. LANDING PAGE")
    L.append("-" * 60)
    L.append(f"Destination: {_v(landing.get('url'))}")
    L.append(f"Tracking:    {_v(landing.get('tracking'))}")
    if not check.get("checked"):
        L.append("Live check:  not measured")
        L.append("Mobile:      not measured")
    else:
        L.append(f"Live check:  HTTP {check.get('status')} at {check.get('at')}"
                 f" — {'reachable' if check.get('ok') else 'NOT reachable'}")
        mobile = check.get("mobile")
        L.append("Mobile:      " + (
            "declares a viewport (spot-check before launch)" if mobile is True
            else "no viewport declared — probably not mobile-friendly"
            if mobile is False else "not measured"))
        if check.get("final_url") and check["final_url"].rstrip("/") != \
                normalise_url(landing.get("url")).rstrip("/"):
            L.append(f"Redirects:   {check['final_url']}")
    L.append("")

    L.append("4. BRAND REQUIREMENTS")
    L.append("-" * 60)
    L.append(f"Logo:        {_v(brand.get('logo_url'))}")
    L.append(f"Logo usage:  {_v(brand.get('logo_usage'))}")
    L.append(f"Colours:     {', '.join(brand.get('colors') or []) or NOT_SUPPLIED}")
    L.append(f"Tone:        {_v(brand.get('tone'))}")
    L.append(f"Claims:      {_v(brand.get('claims'))}")
    L.append(f"Disclaimer:  {_v(brand.get('disclaimer'))}")
    L.append(f"Legal:       {_v(brand.get('legal'))}")
    L.append(f"Never say:   {_v(brand.get('avoid'))}")
    L.append(f"Approved by: {_v(brand.get('approver_name'))}"
             f"  {brand.get('approver_email') or ''}".rstrip())
    L.append("")

    L.append("5. OFFER DETAILS")
    L.append("-" * 60)
    L.append(f"Promotion:    {_v(offer.get('summary'))}")
    L.append(f"Pricing:      {_v(offer.get('pricing'))}")
    L.append(f"Product:      {_v(offer.get('product'))}")
    L.append(f"Eligibility:  {_v(offer.get('eligibility'))}")
    L.append(f"Expires:      {_v(offer.get('expires'))}")
    L.append(f"Restrictions: {_v(offer.get('restrictions'))}")
    L.append("")

    if ad.get("notes"):
        L.append("NOTES")
        L.append("-" * 60)
        L.append(str(ad["notes"]))
        L.append("")

    L.append("-" * 60)
    L.append("Everything marked “not supplied” was left blank on purpose or "
             "was never provided — it is not a blank we filled in.")
    return "\n".join(L) + "\n"


def pack_filename(ad: dict) -> str:
    """A filename that says what is inside without opening it."""
    slug = re.sub(r"[^a-z0-9]+", "-",
                  str((ad or {}).get("client") or "client").lower()).strip("-")
    stamp = _dt.date.today().strftime("%Y-%m-%d")
    return f"{slug or 'client'}-gpt-ad-{stamp}"


def image_extension(image: dict | None) -> str:
    image = image or {}
    fmt = str(image.get("format") or "").lower().lstrip(".")
    if fmt in ("jpg", "jpeg", "png", "webp", "gif"):
        return "jpg" if fmt == "jpeg" else fmt
    path = urlparse(str(image.get("url") or "")).path
    ext = (path.rsplit(".", 1)[-1] if "." in path else "").lower()
    return ext if ext in ("jpg", "jpeg", "png", "webp") else "png"


def spec_payload() -> dict:
    """Everything the browser needs to draw the tool, in one call.

    The browser gets the vocabulary; it does not get the checks. There is no
    JavaScript mirror of the gate here — the target-area helpers and the
    creative classifier each carry one, and each needs a test proving the two
    halves still agree. That cost is paid twice already. Every save returns the
    server's own readiness, and the page renders what comes back.
    """
    unit = creative_specs.BY_ID.get(IMAGE_UNIT, {})
    return {
        "deliverables": DELIVERABLES,
        "limits": dict(LIMITS),
        "limits_source": LIMITS_SOURCE,
        "min_options": dict(MIN_OPTIONS),
        "target_options": dict(TARGET_OPTIONS),
        "max_options": MAX_OPTIONS,
        "ctas": list(CTA_OPTIONS),
        "statuses": list(STATUSES),
        "image_unit": {
            "id": IMAGE_UNIT,
            "name": unit.get("name", "Static Square Image"),
            "ratios": [list(r) for r in unit.get("ratios") or []],
            "min_size": list(unit.get("min_size") or ()),
            "formats": list(unit.get("formats") or []),
            "notes": list(unit.get("notes") or []),
            "source": unit.get("source", ""),
        },
    }


def as_json(data) -> str:
    return json.dumps(data, indent=2, sort_keys=False, default=str)
