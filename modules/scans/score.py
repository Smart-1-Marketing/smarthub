"""Tag every scan-widget lead hot/warm/cold from what the paid audit found.

A lead who ran the free AI-visibility check and then the full paid audit is
one this Hub already knows more about than a name and a phone number: how
bad the site's score is, whether they already spend on Google, how many
reviews they carry, whether Meta ads are running, and whether the site was
built by us. `lead_temperature()` turns that into one of three bands and the
reasons behind it -- read by `modules/scans/app.py` the moment a paid audit
lands, so the Suite contact carries the temperature the moment the report
does, without a second pass or a second webhook.

Thresholds are named constants, not tucked into the branches that use them,
because "why is this one warm and not hot" has to be answerable by reading
the top of the file rather than tracing the function.
"""
from __future__ import annotations

from typing import Any

from . import audit_fields

_get = audit_fields.get_field


def _n(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _b(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    return None


# A lower score is worse, i.e. more to sell -- everything below this line
# counts as "there is real work here" for the score half of the band.
SCORE_HOT_MAX = 40      # badly broken site
SCORE_WARM_MAX = 65     # meaningfully behind

# Reviews: fewer than this and there's an obvious reputation gap to sell.
REVIEW_COUNT_LOW = 10

# A site already spending real money on Google Ads has budget and appetite
# -- that alone is worth naming even on an otherwise middling score.
ADSPEND_MEANINGFUL = 500


def lead_temperature(report: dict) -> tuple[str, list[str]]:
    """("hot"|"warm"|"cold", reasons) from one completed audit.

    Reasons are named, in the order they were found, so a rep (or the note
    field on the Suite contact) can see why a lead landed where it did
    rather than trusting a single word. Never raises -- a report this
    cannot read scores as "cold" with no reasons, which is the safe
    direction to be wrong in: a lead nobody flags as hot still gets worked,
    just without the extra push.
    """
    report = report if isinstance(report, dict) else {}
    reasons: list[str] = []
    hot = False
    warm = False

    score = _n(_get(report, "overall_score"))
    if score is not None:
        if score <= SCORE_HOT_MAX:
            hot = True
            reasons.append(f"Overall audit score is {int(score)} — badly broken.")
        elif score <= SCORE_WARM_MAX:
            warm = True
            reasons.append(f"Overall audit score is {int(score)} — meaningfully behind.")
    # score is None -- not measured, contributes nothing, never treated as 0.

    adspend = _n(_get(report, "paid_search.average_adspend"))
    has_spend = _b(_get(report, "paid_search.has_adwords_spend"))
    if adspend is not None and adspend >= ADSPEND_MEANINGFUL:
        hot = True
        reasons.append(f"Already spending roughly ${adspend:,.0f}/mo on Google Ads "
                       "— budget and appetite already exist.")
    elif has_spend is True:
        warm = True
        reasons.append("Already running Google Ads.")

    reviews = _n(_get(report, "reviews.total_reviews_count"))
    if reviews is None:
        reviews = _n(_get(report, "google_business_profile.review_count"))
    if reviews is not None and reviews < REVIEW_COUNT_LOW:
        warm = True
        reasons.append(f"Only {int(reviews)} reviews — an easy reputation gap to sell.")

    fb_active = _b(_get(report, "facebook_ads.fb_ads_currently_active"))
    if fb_active is None:
        fb_n = _n(_get(report, "facebook_ads.fb_ads_currently_active"))
        fb_active = bool(fb_n) if fb_n is not None else None
    if fb_active is True:
        warm = True
        reasons.append("Currently running Facebook ads.")

    built_by_us = _b(_get(report, "built_by_us.is_own_vendor"))
    if built_by_us is True:
        # A site we already built is a warmer relationship, not a colder
        # one -- there's a standing reason to call.
        warm = True
        reasons.append("Site was built by Smart 1.")

    if adspend is None and has_spend is None:
        reasons.append("Paid search spend was not measured.")

    if hot:
        temperature = "hot"
    elif warm:
        temperature = "warm"
    else:
        temperature = "cold"
    return temperature, reasons
