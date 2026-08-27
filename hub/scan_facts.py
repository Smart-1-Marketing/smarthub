"""What the last Insites site scan knows about a client, for Client 360.

An Insites audit carries 440 fields. Client 360 read four of them — the
score, the broken-link count, the image count and the speed band — and every
other thing the scan had already paid for sat in a JSON blob nobody opened:
the business's own logo, its brand colours, its Google Business Profile and
review standing, whether a pixel is on the site at all, what their organic
traffic looks like, who their registrar is, what the site is built on.

That matters twice over. It answers questions Client 360 gets asked and had
no answer for. And it is a *second* source for two things the Hub already
tries to get elsewhere and often cannot — the logo (Brandfetch publishes
nothing for most local businesses) and the registrar (`hub/knack_websites.py`
already borrows `domain_age.registrar` on the same principle).

The rules, all of them ones this codebase has had to learn:

* **Observed is not recorded.** Everything here was seen on the client's
  website by a crawler on a date. It is labelled as observed, it is never
  written back over something a person entered, and the date travels with it.
  A logo scraped off a home page is a *candidate*, not the brand asset.

* **`(facts, error)`.** "This client has never been scanned" and "we could
  not read the scan table" are different answers and only the first means
  there is nothing to show. Absent is *not measured*, never zero.

* **Nothing here raises.** A card that cannot draw must not take the record
  down with it.

* **It reads, it does not scan.** No credit is spent here; the newest
  completed scan is whatever Site Scans last ran.

Read through the shared engine rather than by importing the scans module:
that module is dispatcher-mounted with its own session and teardown, and
reaching into it from a hub route is the `flask.g` trap in CLAUDE.md.
"""
from __future__ import annotations

import json
from typing import Any

from hub.client_context import canonical_domain


# --------------------------------------------------------------------- read
def _latest(domain: str) -> tuple[dict, dict, str]:
    """(report, row, error) for the newest completed scan of a domain."""
    key = canonical_domain(domain)
    if not key:
        return {}, {}, ""
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from hub.extensions import shared_engine
        engine = shared_engine()
        if not sa_inspect(engine).has_table("scans"):
            return {}, {}, "no scan table yet"
        sql = ("SELECT public_id, domain_key, overall_score, tier, raw_report, "
               "completed_at, created_at FROM scans "
               "WHERE domain_key = :k AND status = 'complete' "
               "ORDER BY id DESC LIMIT 1")
        with engine.connect() as conn:
            row = conn.execute(text(sql), {"k": key}).mappings().first()
    except Exception as exc:                              # noqa: BLE001
        return {}, {}, f"{type(exc).__name__}: {exc}"
    if not row:
        return {}, {}, ""
    row = dict(row)
    try:
        report = json.loads(row.get("raw_report") or "{}")
    except (TypeError, ValueError):
        report = {}
    return (report if isinstance(report, dict) else {}), row, ""


def _get(report: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = report
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _hex(value: Any) -> str:
    import re
    v = str(value or "").strip()
    if not v:
        return ""
    if not v.startswith("#"):
        v = "#" + v
    return v.upper() if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v) else ""


def _stamp(row: dict) -> str:
    for k in ("completed_at", "created_at"):
        v = row.get(k)
        if v:
            return str(v)[:19].replace("T", " ")
    return ""


# ------------------------------------------------------------------- brand
COLOUR_ROLES = [
    ("colour_scheme.primary_accent_colour", "Primary accent"),
    ("colour_scheme.secondary_accent_colour", "Secondary accent"),
    ("colour_scheme.primary_background_colour", "Background"),
    ("colour_scheme.secondary_background_colour", "Secondary background"),
    ("colour_scheme.primary_text_colour", "Text"),
    ("colour_scheme.secondary_text_colour", "Secondary text"),
]


def brand_observed(domain: str) -> dict:
    """The logo and colours seen on the client's own website.

    Offered beside stored brand data, never merged into it: a logo lifted off
    a home page and a logo the client gave us are different claims, and the
    only one safe to put on a document is the second. Same shape as
    `hub/knack_websites.registrar_for()` — a person copies it across.

    **The wording carries none of the plumbing.** Where this came from is a
    site audit, and the person reading the brand card is looking for the
    client's logo — "the last Insites scan" tells them which of our tools
    answered, which is not a fact they can act on and is the note
    `modules/ads_builder/logo.py` already carries about naming Brandfetch to
    a rep. What a screen shows is *where the logo came from*: their own
    website, a lookup, or the client record. The date still travels with it,
    because a sighting with no date on it reads as today's.
    """
    report, row, err = _latest(domain)
    if err:
        return {"found": False, "error": err,
                "note": "We could not read what has been seen on this website, "
                        "so whether there is a logo on it is not measured."}
    if not row:
        return {"found": False,
                "note": "Nothing has been read from this website yet."}

    logo = str(_get(report, "logo.logo_url") or "").strip()
    has_logo = _get(report, "logo.has_detected_logo")
    colours = []
    for path, label in COLOUR_ROLES:
        hx = _hex(_get(report, path))
        if hx and hx not in [c["hex"] for c in colours]:
            colours.append({"hex": hx, "type": label})

    return {
        "found": bool(logo or colours),
        "logo_url": logo,
        "has_detected_logo": has_logo if isinstance(has_logo, bool) else None,
        "colors": colours,
        "screenshot": str(_get(report, "website_screenshot.desktop_screenshot_url")
                          or _get(report, "mobile.mobile_screenshot_url") or ""),
        "detected_name": str(_get(report, "meta.detected_name") or ""),
        "domain": row.get("domain_key") or canonical_domain(domain),
        "scanned_at": _stamp(row),
        "scan_url": f"/scans/scan/{row.get('public_id')}" if row.get("public_id") else "",
        "note": ("Seen on the client's own website — a candidate, not an "
                 "approved brand asset."
                 if (logo or colours) else
                 "Nothing on their website read as a logo or a colour scheme."),
    }



# ----------------------------------------------------------------- contact
# The order each field is taken in. First non-empty wins, and the ordering is
# the point: `meta` is what the crawler read off the client's own site, which
# is the business describing itself; `local_presence` is the same details as
# the directories carry them; the Google listing is last because a listing
# address is the one a customer is driven to and is the most often out of
# date. Nothing here is a guess — a field nobody published stays absent.
CONTACT_SOURCES = {
    "name":     ("meta.detected_name", "local_presence.business_name"),
    "address":  ("meta.detected_address", "local_presence.business_address",
                 "google_business_profile.google_address"),
    "phone":    ("meta.detected_phone", "local_presence.business_phone",
                 "google_business_profile.google_phone"),
    "email":    ("meta.detected_email", "local_presence.business_email"),
    "category": ("meta.primary_industry", "google_business_profile.gmb_industries"),
}


def contact_observed(domain: str) -> dict:
    """Name, address, phone and category read off the client's own website.

    Client 360 asked every rep to type these in by hand into the client info
    strip, on a record where they had already been read off the site and were
    sitting three cards further down under a heading about our own tooling.
    Nobody types a client's address in twice, so the strip said "No contact
    info on file yet" about businesses whose address was on the same page.

    Three rules, each one this codebase has had to learn:

    * **Suggested is not saved.** This never writes anything. It is offered
      into the empty fields of `hub/seo.get_profile`, a person presses Save,
      and from that moment the profile wins — the `hub/client_urls.py`
      overlay rule, and the reason `knack_websites.registrar_for()` offers a
      registrar rather than writing one.
    * **`(values, error)`, never a bare dict.** "Nothing has been read from
      this website" and "we could not look" are different answers and only
      the first means there is nothing to offer.
    * **No plumbing in the wording.** Where it came from is their website;
      which of our tools read it is not something the rep can act on.
    """
    report, row, err = _latest(domain)
    if err:
        return {"found": False, "fields": {}, "error": err,
                "note": "We could not read this website, so there is nothing "
                        "to offer — not measured rather than nothing found."}
    if not row:
        return {"found": False, "fields": {},
                "note": "Nothing has been read from this website yet."}

    fields: dict[str, str] = {}
    for field, paths in CONTACT_SOURCES.items():
        for path in paths:
            value = _s(_get(report, path))
            if value:
                fields[field] = value
                break

    return {
        "found": bool(fields),
        "fields": fields,
        "domain": row.get("domain_key") or canonical_domain(domain),
        "observed_at": _stamp(row),
        "note": ("Read from the client's own website."
                 if fields else
                 "Their website carried no contact details we could read."),
    }



def contact_suggestions(profile: dict, domain: str) -> dict:
    """`contact_observed`, minus everything already on the client record.

    Only the empty fields are offered. A rep who typed an address is the
    better source than anything read off a home page — the overlay rule
    `hub/client_urls.py` works to — and re-offering a value already recorded
    is how somebody comes to press a button that appears to do nothing.

    Contact fields are gated on the record having *no* contact at all rather
    than field by field: a contact row is a person, and dropping a phone
    number read off a home page into the row holding the owner's name is us
    inventing who answers it.

    Never raises, and never writes. `error` rides through so the strip can say
    it could not look rather than drawing a clean nothing.
    """
    if not domain:
        return {"values": {}, "note": "", "error": ""}
    try:
        seen = contact_observed(domain)
    except Exception as exc:                              # noqa: BLE001
        return {"values": {}, "note": "", "error": f"{type(exc).__name__}: {exc}"}

    profile = profile or {}
    have_contact = any((c or {}).get("name") or (c or {}).get("phone")
                       or (c or {}).get("email")
                       for c in (profile.get("contacts") or []))
    values = {}
    for field, value in (seen.get("fields") or {}).items():
        if field in ("address", "category"):
            if not str(profile.get(field) or "").strip():
                values[field] = value
        elif not have_contact:
            values[field] = value
    return {"values": values, "note": seen.get("note") or "",
            "observed_at": seen.get("observed_at") or "",
            "error": seen.get("error") or ""}


# ------------------------------------------------------------------- facts
def _n(value: Any) -> Any:
    """A number, or None. A string "12" is a number; True is not."""
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def _b(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _s(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)[:300]
    return str(value).strip()[:300]


def _row(label: str, value: Any, *, link: str = "", hint: str = "") -> dict | None:
    """One line, or nothing at all.

    Nothing, rather than "not measured", for a field the account's plan does
    not include: forty rows of "not measured" is not a report, it is a wall
    somebody stops reading. What *is* measured and empty says so — a False
    boolean is an answer and is kept.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        value = "Yes" if value else "No"
    return {"label": label, "value": value, "link": link, "hint": hint}


def facts(domain: str) -> dict:
    """Everything worth putting on Client 360, grouped by the question asked."""
    report, row, err = _latest(domain)
    if err:
        return {"found": False, "measured": False, "error": err,
                "note": "This website could not be read, so none of this is "
                        "measured."}
    if not row:
        return {"found": False, "measured": False,
                "domain": canonical_domain(domain),
                "note": "Nothing has been read from this website yet."}

    g = lambda p, d=None: _get(report, p, d)                    # noqa: E731
    groups = []

    def group(title, why, rows):
        rows = [r for r in rows if r]
        if rows:
            groups.append({"title": title, "why": why, "rows": rows})

    gbp_url = _s(g("google_business_profile.listing_url"))
    rating = _n(g("google_business_profile.review_rating"))
    group("Google Business Profile",
          "The listing a local customer actually sees, and the one thing a "
          "client can lose without anybody noticing.",
          [_row("Listing found", _b(g("google_business_profile.is_listing_found")),
                link=gbp_url),
           _row("Claimed", _b(g("google_business_profile.is_listing_claimed")),
                hint="An unclaimed listing can be edited by anybody."),
           _row("Complete", _b(g("google_business_profile.is_listing_complete"))),
           _row("Rating", f"{rating} ★" if rating is not None else None),
           _row("Google reviews", _n(g("google_business_profile.review_count"))),
           _row("Photos on the listing", _b(g("google_business_profile.has_photos"))),
           _row("Opening hours", _b(g("google_business_profile.has_opening_hours"))),
           _row("Category", _s(g("google_business_profile.gmb_industries"))),
           _row("Listing address", _s(g("google_business_profile.google_address")))])

    group("Reviews and directory listings",
          "Where the business appears beyond its own site, and whether the "
          "details agree.",
          [_row("Reviews across directories", _n(g("reviews.total_reviews_count"))),
           _row("Average rating", _n(g("reviews.average_review_rating"))),
           _row("Directories checked", _n(g("local_presence.directories_checked_count"))),
           _row("Missing from", _n(g("local_presence.directory_listings_missing_count")),
                hint="Listings that do not exist yet — each one is a job."),
           _row("Inconsistent details", _n(g("local_presence.inconsistent_details_listing_count")),
                hint="Name, address or phone disagreeing between listings."),
           _row("Name on file", _s(g("local_presence.business_name"))),
           _row("Phone on file", _s(g("local_presence.business_phone")))])

    fb_link = _s(g("facebook_page.page_link"))
    group("Social presence",
          "What is linked from their site, with the numbers behind "
          "each — a page with 40 followers and a page with 4,000 are not the "
          "same finding.",
          [_row("Facebook", _s(g("facebook_page.page_name")) or _b(g("facebook_page.found")),
                link=fb_link),
           _row("Facebook followers", _n(g("facebook_page.page_follows"))),
           _row("Days since last Facebook post", _n(g("facebook_page.days_since_last_post"))),
           _row("Instagram", _s(g("instagram_account.instagram_username"))
                or _b(g("instagram_account.has_instagram")),
                link=_s(g("instagram_account.instagram_link"))),
           _row("LinkedIn", _b(g("linkedin.has_linkedin")),
                link=_s(g("linkedin.linkedin_profile_url"))),
           _row("X", _s(g("x_(formerly_twitter).account_name"))
                or _b(g("x_(formerly_twitter).found")),
                link=_s(g("x_(formerly_twitter).account_link"))),
           _row("YouTube", _b(g("youtube.has_youtube")),
                link=_s(g("youtube.youtube_link"))),
           _row("TikTok", _b(g("tiktok_account.has_tiktok")),
                link=_s(g("tiktok_account.tiktok_url")))])

    group("What they are already spending",
          "The competitive picture a proposal is written against — and the "
          "answer to “are they running anything already?” without "
          "having to ask them.",
          [_row("Running Google Ads", _b(g("paid_search.has_adwords_spend"))),
           _row("Estimated monthly Google Ads spend",
                (lambda v: f"${v:,.0f}" if v is not None else None)(
                    _n(g("paid_search.average_adspend"))),
                hint="A third-party estimate, not a billed figure."),
           _row("Estimated monthly paid visitors", _n(g("paid_search.average_adtraffic"))),
           _row("Active Facebook ads", _n(g("facebook_ads.fb_ads_currently_active")),
                link=_s(g("facebook_ads.fb_ad_library_url"))),
           _row("Running display ads", _b(g("display_ads.uses_display_ads")),
                link=_s(g("display_ads.ad_transparency_centre_url")))])

    group("Can we run a campaign to this site",
          "Everything that has to be true before a paid campaign can be "
          "measured — each of these is a launch blocker found late otherwise.",
          [_row("Google Ads ready", _b(g("google_ads_readiness.is_google_ads_ready"))),
           _row("Analytics", _s(g("analytics.analytics_tool"))
                or _b(g("analytics.has_analytics"))),
           _row("Still on Universal Analytics", _b(g("analytics.uses_universal_ga")),
                hint="Universal Analytics stopped collecting data in 2023."),
           _row("Tag Manager", _b(g("google_ads_readiness.has_google_tag"))),
           _row("Consent Mode v2", _b(g("google_ads_readiness.uses_consent_mode_v2"))),
           _row("Meta pixel", _b(g("retargeting.has_facebook_pixel"))),
           _row("Google remarketing tag", _b(g("retargeting.has_google_pixel"))),
           _row("Click-to-call links", _n(g("click_to_contact.tel_links_found_count"))),
           _row("Booking widget", _s(g("booking_widget.booking_widget_apps"))
                or _b(g("booking_widget.has_booking_widget"))),
           _row("Live chat", _s(g("live_chat.live_chat_apps"))
                or _b(g("live_chat.has_live_chat")))])

    group("Organic search",
          "What the site earns without paying for it.",
          [_row("Estimated monthly organic traffic",
                _n(g("organic_search.average_monthly_traffic"))),
           _row("Keywords ranked for", _n(g("organic_search.num_keywords_ranked_for"))),
           _row("Top terms", _s(g("organic_search.top_keywords_ranked_for_detail")
                                or g("organic_search.top_keywords_ranked_for"))),
           _row("Appears in the local pack", _b(g("local_pack.appears_in_local_pack"))),
           _row("Blog", (lambda n: f"{n} posts" if n else None)(_n(g("blog.blog_post_count")))
                or _b(g("blog.has_blog"))),
           _row("Pages found", _n(g("page_count.pages_discovered_count"))),
           _row("Has a sitemap", _b(g("sitemap.has_sitemap")))])

    group("Content the Hub can fix",
          "Each of these has a tool in this Hub behind it.",
          [_row("Images with no alt text", _n(g("alternative_text.images_no_alt_count")),
                hint="The SEO Image Pipeline and the alt-text writer do these."),
           _row("Images to optimise", _n(g("image_optimisation.images_to_optimise_count"))),
           _row("Pages missing a title", _n(g("page_titles_and_descriptions.pages_missing_title_count"))),
           _row("Pages missing a description",
                _n(g("page_titles_and_descriptions.pages_missing_description_count"))),
           _row("Pages missing an H1", _n(g("headings.pages_missing_h1_count"))),
           _row("Missing schema items", _n(g("structured_data.count_missing_schema_items")),
                hint="The Schema Builder writes these."),
           _row("Spelling errors", _n(g("spelling.spelling_error_count"))),
           _row("Days since the site was last updated", _n(g("last_updated.days_since_update")))])

    group("The site itself",
          "What it is built on, and who to talk to when it needs changing.",
          [_row("Built by us", _b(g("built_by_us.is_own_vendor"))),
           _row("Platform", _s(g("technology_profile.vendor")
                               or g("technology_detection.vendor"))),
           _row("CMS", _s(g("technology_profile.cms_solution")
                          or g("technology_detection.cms_solution"))),
           _row("Ecommerce", _s(g("ecommerce.ecommerce_name"))
                or _b(g("ecommerce.has_ecommerce"))),
           _row("Email provider", _s(g("email_provider.email_providers"))),
           _row("Mobile optimised", _b(g("mobile.is_mobile"))),
           _row("WCAG AA issues", _n(g("accessibility.wcag_aa_issues_count")),
                hint="Level " + (_s(g("accessibility.wcag_level")) or "AA")
                     + " accessibility failures found on the pages tested.")])

    group("Domain and security",
          "The renewal facts, observed rather than recorded — "
          "hub/knack_websites.py reads the same registrar for the domain record.",
          [_row("Registrar", _s(g("domain_age.registrar")),
                hint="Observed on the domain itself. The recorded registrar is on the "
                     "domain record; where they disagree, the record wins."),
           _row("Registered", _s(g("domain_age.registered_date"))[:10]),
           _row("Domain expires", _s(g("domain_age.expiry_date"))[:10]),
           _row("SSL valid", _b(g("ssl.ssl_valid"))),
           _row("SSL expires", _s(g("ssl.ssl_expiry_date"))[:10]),
           _row("Mixed content", _b(g("ssl.mixed_content"))),
           _row("Redirects to HTTPS", _b(g("ssl.ssl_redirect"))),
           _row("Has a privacy policy", _b(g("ccpa.has_privacy_policy")))])

    group("Business details on their website",
          "The name, address and phone number their own site publishes — "
          "worth an eye when the client record disagrees with it. These are "
          "offered into the client info strip at the top of this record.",
          [_row("Name", _s(g("meta.detected_name"))),
           _row("Address", _s(g("meta.detected_address"))),
           _row("Phone", _s(g("meta.detected_phone"))),
           _row("Primary industry", _s(g("meta.primary_industry")))])

    return {
        "found": True,
        "measured": bool(groups),
        "domain": row.get("domain_key") or canonical_domain(domain),
        "score": row.get("overall_score"),
        "tier": row.get("tier") or "",
        "scanned_at": _stamp(row),
        "scan_url": f"/scans/scan/{row.get('public_id')}" if row.get("public_id") else "",
        "groups": groups,
        "note": ("" if groups else
                 "None of these sections came back for this website — that is "
                 "a plan or a site that could not be read, not a clean bill."),
    }
