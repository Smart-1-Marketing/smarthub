"""One client brief every AI feature in the Hub reads.

An Insites scan carries 432 fields per client. Before this file, 146 were read
anywhere in the codebase and only 12 of those ever reached a prompt, through
`hub/client_context.for_prompt()`. Three of fourteen OpenAI call sites used
even those twelve. Image Creator, SEO Images, Page Image Optimizer, the
alt-text writer, Fan Radio and the landing apps wrote copy and images for a
named client from the name alone — a model told nothing about a business
writes plausible, generic copy, which is the hardest kind of wrong to spot on
a finished page.

`build()` is the one assembly: every source this Hub already holds, read
independently so a failing provider costs only its own section, every value
carrying where it came from and when it was observed. `render()` turns that
into the prompt block `hub/client_context.for_prompt()` already produced, cut
to what a given audience should see — an image model does not need to know a
client's ad spend, and a copy model does not need their keyword rankings.

Three rules, all of them ones this codebase has already paid for elsewhere:

* **Never raises.** A brief that cannot be built must not cost the caller the
  page or the render it was going to inform.
* **A section the scan did not return is `{"measured": False}`, never
  zeros.** A False boolean is a real answer and is kept; an absent one is not
  invented as one.
* **Source, never vendor.** A tile says "seen on their website" or "the
  client record", not "Insites" or "Brandfetch" — the note
  `modules/ads_builder/logo.py` already makes about naming a provider to
  somebody who cannot rotate its key.

Dotted Insites paths live in `hub/scan_facts.py` and `modules/scans/
audit_fields.py` and nowhere else — `hub/client_context.py`'s own "2a" block
explains at length what happens when a second file guesses at where a field
lives. This file reads through `scan_facts.latest_report()` and
`modules.scans.audit_fields.get_field`, never by re-typing a path.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from typing import Any

# Words a screen may print for where a fact came from. Never a vendor name —
# `hub/client_context.SOURCE_LABELS` is the precedent this mirrors exactly,
# because a form and a prompt should not disagree about how a source reads.
SOURCE_LABELS = {
    "knack": "the client record",
    "scan": "their website",
    "brand": "their brand kit",
    "store": "this client's profile",
    "form": "the form",
}


def _clean(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get(report: dict, dotted: str, default: Any = None) -> Any:
    from modules.scans.audit_fields import get_field
    return get_field(report, dotted, default)


def _s(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)[:400]
    return str(value).strip()[:400]


def _n(value: Any) -> Any:
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


def _fact(value: Any, source: str, observed_at: str = "") -> dict | None:
    """One valued fact, or None. A False boolean is kept; "" and None are not."""
    if value is None or value == "":
        return None
    return {"value": value, "source": source, "observed_at": observed_at or ""}


# --------------------------------------------------------------------- build
def build(client: str, domain: str = "") -> dict:
    """Every fact the Hub holds about a client, sectioned and attributed.

    Never raises. Every source is read independently so a failing provider
    loses only its own section. A section the scan did not return reads
    ``{"measured": False}`` rather than a wall of zeros or blanks.
    """
    client = _clean(client)
    out: dict[str, Any] = {
        "client": client, "domain": _clean(domain), "built_at": _now(),
    }

    report: dict = {}
    scan_meta: dict = {}
    scan_err = ""
    try:
        from hub import scan_facts
        report, scan_meta, scan_err = scan_facts.latest_report(domain or client)
    except Exception as exc:                              # noqa: BLE001
        scan_err = f"{type(exc).__name__}: {exc}"

    knack: dict = {}
    try:
        from hub import clients_registry
        knack = clients_registry.find_client(client) or {}
    except Exception:                                     # noqa: BLE001
        knack = {}

    kit: dict = {}
    try:
        from hub.client_brand import brand_kit
        kit = brand_kit(client, domain) or {}
    except Exception:                                     # noqa: BLE001
        kit = {}

    seo_profile: dict = {}
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        seo_profile = store.get("business_info") or store.get("profile") or {}
    except Exception:                                     # noqa: BLE001
        seo_profile = {}

    scanned_at = scan_meta.get("scanned_at", "")
    g = lambda p, d=None: _get(report, p, d)              # noqa: E731

    # ---- identity ----
    try:
        industry_key = ""
        industry_label = _s(g("meta.primary_industry")) or _s(knack.get("industry") or knack.get("vertical"))
        industry_subtype = ""
        try:
            from hub import industry as _industry
            resolved = _industry.resolve_industry(client=client, domain=domain,
                                                    hint=industry_label)
            industry_key = resolved.get("key", "")
            industry_label = resolved.get("label", industry_label)
            industry_subtype = resolved.get("subtype", "")
        except Exception:                                 # noqa: BLE001
            # SEAM (WO-2): hub/industry.py does not exist on this branch yet.
            # Until it lands, industry is whatever tool_context() already
            # produces — a bare string with no key/subtype, which is the
            # shape every existing caller already tolerates.
            pass

        identity = {
            # The caller's own input is not a lookup -- `for_prompt()`'s
            # whole rule. A client Knack does not recognise gets no
            # attributed "name" fact at all, so a brief built from nothing
            # but the string somebody typed cannot make render() believe a
            # lookup happened.
            "name": _fact(knack.get("name"), "knack") if knack.get("name") else None,
            "site_name": _fact(_s(g("meta.detected_name")), "scan", scanned_at),
            "website": _fact(_clean(domain) or _s(knack.get("url") or knack.get("domain")),
                             "scan" if domain else "knack"),
            "domain": _fact(_clean(domain), "form") if domain else None,
            "industry_key": _fact(industry_key, "scan") if industry_key else None,
            "industry_label": _fact(industry_label, "scan" if _s(g("meta.primary_industry")) else "knack"),
            "industry_subtype": _fact(industry_subtype, "scan") if industry_subtype else None,
        }
        out["identity"] = {k: v for k, v in identity.items() if v} or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["identity"] = {"measured": False}

    # ---- location ----
    try:
        city = _s(g("local_presence.business_city"))
        state = _s(g("local_presence.business_state"))
        zip_ = _s(g("local_presence.business_zip"))
        street = _s(g("local_presence.business_street"))
        loc_source = "scan"
        if not (city or state or zip_):
            # Parsed from the one-line address the crawler read off the page.
            addr = _s(g("meta.detected_address"))
            parts = [p.strip() for p in addr.split(",") if p.strip()]
            if len(parts) >= 2:
                city = city or parts[-2]
                tail = parts[-1].split()
                if tail:
                    state = state or tail[0]
                    if len(tail) > 1:
                        zip_ = zip_ or tail[-1]
        if not (city or state or zip_):
            city = _s(knack.get("city"))
            state = _s(knack.get("state"))
            loc_source = "knack" if (city or state) else loc_source
        if not (city or state or zip_):
            city = _s(seo_profile.get("city"))
            state = _s(seo_profile.get("state"))
            zip_ = _s(seo_profile.get("zip"))
            loc_source = "store" if (city or state or zip_) else loc_source

        location = {
            "city": _fact(city, loc_source, scanned_at) if city else None,
            "state": _fact(state, loc_source, scanned_at) if state else None,
            "zip": _fact(zip_, loc_source, scanned_at) if zip_ else None,
            "street": _fact(street, "scan", scanned_at) if street else None,
            "place_id": _fact(_s(g("meta.google_maps_place_id")), "scan", scanned_at)
                        if _s(g("meta.google_maps_place_id")) else None,
            "gbp_address": _fact(_s(g("google_business_profile.google_address")),
                                 "scan", scanned_at)
                           if _s(g("google_business_profile.google_address")) else None,
        }
        out["location"] = {k: v for k, v in location.items() if v} or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["location"] = {"measured": False}

    # ---- contact ----
    try:
        from hub import scan_facts
        seen = scan_facts.contact_observed(domain or client)
        fields = seen.get("fields") or {}
        contact = {
            "phone": _fact(fields.get("phone"), "scan", scanned_at) if fields.get("phone") else None,
            "email": _fact(fields.get("email"), "scan", scanned_at) if fields.get("email") else None,
        }
        if not contact["phone"] and knack.get("phone"):
            contact["phone"] = _fact(_s(knack.get("phone")), "knack")
        out["contact"] = {k: v for k, v in contact.items() if v} or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["contact"] = {"measured": False}

    # ---- brand ----
    try:
        logos = []
        for logo in (kit.get("logos") or []):
            logos.append({"url": logo.get("url"), "origin": "file"})
        observed_logo = _s(g("logo.logo_url"))
        if observed_logo:
            logos.append({"url": observed_logo, "origin": "site"})

        palette = []
        seen_hex = set()
        for c in (kit.get("colors") or []):
            hx = _s(c.get("hex")).upper()
            if hx and hx not in seen_hex:
                palette.append({"hex": hx, "role": c.get("type") or "", "origin": "file"})
                seen_hex.add(hx)
        for path, role in (
            ("colour_scheme.primary_accent_colour", "Primary accent"),
            ("colour_scheme.secondary_accent_colour", "Secondary accent"),
            ("colour_scheme.primary_background_colour", "Background"),
            ("colour_scheme.secondary_background_colour", "Secondary background"),
            ("colour_scheme.primary_text_colour", "Text"),
            ("colour_scheme.secondary_text_colour", "Secondary text"),
        ):
            v = _s(g(path))
            hx = ("#" + v.lstrip("#")).upper() if v else ""
            if hx and re.fullmatch(r"#[0-9A-F]{3,8}", hx) and hx not in seen_hex:
                palette.append({"hex": hx, "role": role, "origin": "site"})
                seen_hex.add(hx)

        fonts = [f.get("name") for f in (kit.get("fonts") or []) if f.get("name")]
        favicon = _s(g("favicon.favicon_location"))
        screenshots = {
            "desktop": _s(g("website_screenshot.desktop_screenshot_url")),
            "mobile": _s(g("mobile.mobile_screenshot_url")),
        }
        screenshots = {k: v for k, v in screenshots.items() if v}

        brand = {
            "logos": logos or None,
            "palette": palette or None,
            "fonts": fonts or None,
            "favicon": _fact(favicon, "scan", scanned_at) if favicon else None,
            "screenshots": _fact(screenshots, "scan", scanned_at) if screenshots else None,
        }
        out["brand"] = {k: v for k, v in brand.items() if v} or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["brand"] = {"measured": False}

    # ---- voice ----
    try:
        voice = {
            "h1": _fact(_s(g("headings.homepage_h1_content")), "scan", scanned_at)
                 if _s(g("headings.homepage_h1_content")) else None,
            "title_tag": _fact(_s(g("page_titles_and_descriptions.homepage_title_tag")),
                               "scan", scanned_at)
                        if _s(g("page_titles_and_descriptions.homepage_title_tag")) else None,
            "meta_description": _fact(_s(g("homepage_meta_description")), "scan", scanned_at)
                                if _s(g("homepage_meta_description")) else None,
            "description": _fact(_s(kit.get("description")), "brand") if kit.get("description") else None,
        }
        brand_record = seo_profile.get("brand") or {}
        for k in ("tagline", "voice", "cta", "pronunciation"):
            if brand_record.get(k):
                voice[k] = _fact(_s(brand_record.get(k)), "store")
        out["voice"] = {k: v for k, v in voice.items() if v} or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["voice"] = {"measured": False}

    # ---- proof ----
    try:
        proof = {}

        def take_num(key, path):
            n = _n(g(path))
            if n is not None:
                proof[key] = _fact(n, "scan", scanned_at)

        def take_txt(key, path):
            v = _s(g(path))
            if v:
                proof[key] = _fact(v, "scan", scanned_at)

        def take_flag(key, path):
            v = _b(g(path))
            if v is not None:
                proof[key] = _fact(v, "scan", scanned_at)

        take_num("review_rating", "google_business_profile.review_rating")
        take_num("review_count", "google_business_profile.review_count")
        take_txt("gbp_industries", "google_business_profile.gmb_industries")
        take_flag("listing_claimed", "google_business_profile.is_listing_claimed")
        take_flag("listing_complete", "google_business_profile.is_listing_complete")
        take_txt("listing_url", "google_business_profile.listing_url")
        take_num("total_reviews", "reviews.total_reviews_count")
        take_num("average_rating", "reviews.average_review_rating")
        take_num("competitors_more_reviews_pct", "reviews.competitors_more_reviews_percentage")
        take_num("competitors_higher_rating_pct", "reviews.competitors_higher_rating_percentage")
        take_num("competitors_better_reviews_pct", "reviews.competitors_better_reviews_percentage")
        out["proof"] = proof or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["proof"] = {"measured": False}

    # ---- digital ----
    try:
        digital = {}

        def take_bool(key, path):
            v = _b(g(path))
            if v is not None:
                digital[key] = _fact(v, "scan", scanned_at)

        def take_str(key, path):
            v = _s(g(path))
            if v:
                digital[key] = _fact(v, "scan", scanned_at)

        take_str("analytics_tool", "analytics.analytics_tool")
        take_bool("uses_universal_ga", "analytics.uses_universal_ga")
        take_bool("has_google_tag", "google_ads_readiness.has_google_tag")
        take_bool("uses_consent_mode_v2", "google_ads_readiness.uses_consent_mode_v2")
        take_bool("is_google_ads_ready", "google_ads_readiness.is_google_ads_ready")
        take_bool("has_facebook_pixel", "retargeting.has_facebook_pixel")
        take_bool("has_google_pixel", "retargeting.has_google_pixel")
        take_bool("has_adwords_spend", "paid_search.has_adwords_spend")
        n = _n(g("paid_search.average_adspend"))
        if n is not None:
            digital["average_adspend"] = _fact(n, "scan", scanned_at)
        n = _n(g("paid_search.average_adtraffic"))
        if n is not None:
            digital["average_adtraffic"] = _fact(n, "scan", scanned_at)
        n = _n(g("facebook_ads.fb_ads_currently_active"))
        if n is not None:
            digital["fb_ads_active"] = _fact(n, "scan", scanned_at)
        take_str("fb_ad_library_url", "facebook_ads.fb_ad_library_url")
        take_bool("uses_display_ads", "display_ads.uses_display_ads")
        take_bool("has_ecommerce", "ecommerce.has_ecommerce")
        take_str("ecommerce_name", "ecommerce.ecommerce_name")
        take_bool("has_booking_widget", "booking_widget.has_booking_widget")
        take_bool("has_live_chat", "live_chat.has_live_chat")
        take_str("cms_vendor", "technology_profile.vendor")
        take_str("cms_solution", "technology_profile.cms_solution")
        take_bool("built_by_us", "built_by_us.is_own_vendor")
        out["digital"] = digital or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["digital"] = {"measured": False}

    # ---- seo ----
    try:
        seo_facts = {}

        def take_n(key, path):
            n = _n(g(path))
            if n is not None:
                seo_facts[key] = _fact(n, "scan", scanned_at)

        def take_s(key, path):
            v = _s(g(path))
            if v:
                seo_facts[key] = _fact(v, "scan", scanned_at)

        def take_b(key, path):
            v = _b(g(path))
            if v is not None:
                seo_facts[key] = _fact(v, "scan", scanned_at)

        take_n("average_monthly_traffic", "organic_search.average_monthly_traffic")
        take_n("num_keywords_ranked_for", "organic_search.num_keywords_ranked_for")
        take_s("top_keywords", "organic_search.top_keywords_ranked_for_detail")
        take_s("best_keyword_opportunities", "organic_search.best_keyword_opportunities")
        take_s("target_keywords", "organic_search.target_keywords_detail")
        take_n("total_backlinks", "backlinks.total_backlinks")
        take_n("total_websites_linking", "backlinks.total_websites_linking")
        take_n("images_no_alt_count", "alternative_text.images_no_alt_count")
        take_n("images_to_optimise_count", "image_optimisation.images_to_optimise_count")
        take_n("pages_missing_title_count", "page_titles_and_descriptions.pages_missing_title_count")
        take_n("pages_missing_description_count", "page_titles_and_descriptions.pages_missing_description_count")
        take_n("pages_missing_h1_count", "headings.pages_missing_h1_count")
        take_n("missing_schema_items", "structured_data.count_missing_schema_items")
        take_b("has_sitemap", "sitemap.has_sitemap")
        take_b("has_og_tags", "open_graph.has_og_tags")
        take_b("is_voice_search_optimised", "voice_search.is_voice_search_optimised")
        take_b("appears_in_local_pack", "local_pack.appears_in_local_pack")
        take_b("has_blog", "blog.has_blog")
        take_n("blog_post_count", "blog.blog_post_count")
        out["seo"] = seo_facts or {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["seo"] = {"measured": False}

    # ---- social ----
    try:
        from hub import scan_facts
        snap = scan_facts.social_snapshot(domain or client)
        out["social"] = snap if snap.get("found") else {"measured": False,
                                                          "error": snap.get("error", "")}
    except Exception:                                     # noqa: BLE001
        out["social"] = {"measured": False}

    # ---- products ----
    try:
        products = sorted(str(p) for p in (knack.get("products") or knack.get("running") or []))
        out["products"] = {"running": _fact(products, "knack")} if products else {"measured": False}
    except Exception:                                     # noqa: BLE001
        out["products"] = {"measured": False}

    # ---- scan meta ----
    if scan_err:
        out["scan"] = {"measured": False, "error": scan_err}
    elif scan_meta:
        out["scan"] = {
            "score": _fact(scan_meta.get("score"), "scan") if scan_meta.get("score") is not None else None,
            "tier": _fact(scan_meta.get("tier"), "scan") if scan_meta.get("tier") else None,
            "scanned_at": _fact(scan_meta.get("scanned_at"), "scan") if scan_meta.get("scanned_at") else None,
            "scan_url": _fact(scan_meta.get("scan_url"), "scan") if scan_meta.get("scan_url") else None,
        }
        out["scan"] = {k: v for k, v in out["scan"].items() if v} or {"measured": False}
    else:
        out["scan"] = {"measured": False}

    # ---- unknown: what a model must not invent ----
    # Every named field this function asked for, so a field with no value is
    # still named rather than only a section whose whole read failed.
    expected = {
        "identity": ("name", "site_name", "website", "domain", "industry_key",
                     "industry_label", "industry_subtype"),
        "location": ("city", "state", "zip", "street", "place_id", "gbp_address"),
        "contact": ("phone", "email"),
        "brand": ("logos", "palette", "fonts", "favicon", "screenshots"),
        "voice": ("h1", "title_tag", "meta_description", "description",
                  "tagline", "voice", "cta", "pronunciation"),
        "proof": ("review_rating", "review_count", "gbp_industries",
                  "listing_claimed", "listing_complete", "listing_url",
                  "total_reviews", "average_rating",
                  "competitors_more_reviews_pct",
                  "competitors_higher_rating_pct",
                  "competitors_better_reviews_pct"),
        "digital": ("analytics_tool", "uses_universal_ga", "has_google_tag",
                    "uses_consent_mode_v2", "is_google_ads_ready",
                    "has_facebook_pixel", "has_google_pixel",
                    "has_adwords_spend", "average_adspend", "average_adtraffic",
                    "fb_ads_active", "fb_ad_library_url", "uses_display_ads",
                    "has_ecommerce", "ecommerce_name", "has_booking_widget",
                    "has_live_chat", "cms_vendor", "cms_solution", "built_by_us"),
        "seo": ("average_monthly_traffic", "num_keywords_ranked_for",
                "top_keywords", "best_keyword_opportunities",
                "target_keywords", "total_backlinks", "total_websites_linking",
                "images_no_alt_count", "images_to_optimise_count",
                "pages_missing_title_count", "pages_missing_description_count",
                "pages_missing_h1_count", "missing_schema_items",
                "has_sitemap", "has_og_tags", "is_voice_search_optimised",
                "appears_in_local_pack", "has_blog", "blog_post_count"),
    }
    unknown = []
    for section, fields in expected.items():
        block = out.get(section) or {}
        for field in fields:
            if field not in block:
                unknown.append([section, field])
    out["unknown"] = unknown

    return out


# -------------------------------------------------------------------- render
# Which sections an audience is shown, and how much of each. "copy" is the
# default writer's cut; "image" is deliberately the narrowest -- a model
# generating a picture has no use for spend figures, pixel presence, review
# counts or keyword rankings, and handing them over is how a generated hero
# image ends up captioned with a number nobody asked for. "audio" adds the
# pronunciation dictionary and the phone number spoken out loud, the way
# hub/radio_spec.py already does for both radio builders. "strategy" is
# everything, unabridged -- proposals and QA reports need the whole picture.
_AUDIENCE_SECTIONS = {
    "copy": ("identity", "location", "contact", "brand", "voice", "proof", "products"),
    "image": ("identity", "location", "brand", "voice"),
    "audio": ("identity", "location", "contact", "brand", "voice", "proof", "products"),
    "strategy": ("identity", "location", "contact", "brand", "voice", "proof",
                 "digital", "seo", "social", "products"),
}

# Fields dropped even inside a section that is otherwise shown, for an
# audience where a number would be a distraction rather than a fact worth
# stating. "image" gets brand names and hex values, never spend or counts.
_IMAGE_BRAND_ONLY = {"logos", "palette", "fonts"}

_ROW_LABELS = {
    "name": "Business name", "site_name": "Name as it appears on their own site",
    "website": "Website", "domain": "Domain", "industry_key": "Industry key",
    "industry_label": "Industry", "industry_subtype": "Industry subtype",
    "city": "City", "state": "State", "zip": "ZIP", "street": "Street",
    "place_id": "Google Maps place id", "gbp_address": "Google listing address",
    "phone": "Phone", "email": "Email",
    "h1": "Homepage headline", "title_tag": "Homepage title tag",
    "meta_description": "Homepage meta description", "description": "Description",
    "tagline": "Tagline", "voice": "Brand voice", "cta": "Call to action",
    "pronunciation": "Pronunciation notes",
    "review_rating": "Google rating", "review_count": "Google review count",
    "gbp_industries": "Google Business categories",
    "listing_claimed": "Google listing claimed", "listing_complete": "Google listing complete",
    "listing_url": "Google listing URL", "total_reviews": "Total reviews across directories",
    "average_rating": "Average review rating",
    "competitors_more_reviews_pct": "Competitors with more reviews",
    "competitors_higher_rating_pct": "Competitors with a higher rating",
    "competitors_better_reviews_pct": "Competitors with better reviews overall",
    "analytics_tool": "Analytics tool", "uses_universal_ga": "Still on Universal Analytics",
    "has_google_tag": "Google tag present", "uses_consent_mode_v2": "Consent Mode v2",
    "is_google_ads_ready": "Google Ads ready", "has_facebook_pixel": "Meta pixel present",
    "has_google_pixel": "Google remarketing tag present",
    "has_adwords_spend": "Already running Google Ads",
    "average_adspend": "Estimated monthly Google Ads spend",
    "average_adtraffic": "Estimated monthly paid traffic",
    "fb_ads_active": "Active Facebook ads", "fb_ad_library_url": "Facebook Ad Library",
    "uses_display_ads": "Already running display ads", "has_ecommerce": "Has ecommerce",
    "ecommerce_name": "Ecommerce platform", "has_booking_widget": "Booking widget",
    "has_live_chat": "Live chat", "cms_vendor": "Website platform", "cms_solution": "CMS",
    "built_by_us": "Site built by Smart 1",
    "average_monthly_traffic": "Estimated monthly organic traffic",
    "num_keywords_ranked_for": "Keywords ranked for", "top_keywords": "Top keywords",
    "best_keyword_opportunities": "Best keyword opportunities",
    "target_keywords": "Target keywords", "total_backlinks": "Total backlinks",
    "total_websites_linking": "Sites linking in", "images_no_alt_count": "Images missing alt text",
    "images_to_optimise_count": "Images to optimize",
    "pages_missing_title_count": "Pages missing a title",
    "pages_missing_description_count": "Pages missing a description",
    "pages_missing_h1_count": "Pages missing an H1", "missing_schema_items": "Missing schema items",
    "has_sitemap": "Has a sitemap", "has_og_tags": "Has Open Graph tags",
    "is_voice_search_optimised": "Voice-search optimized",
    "appears_in_local_pack": "Appears in the local pack", "has_blog": "Has a blog",
    "blog_post_count": "Blog post count", "running": "Currently running with us",
}


def render(brief: dict, audience: str = "copy", max_chars: int = 6000) -> str:
    """The prompt block for `brief`, cut to what `audience` should see.

    Same shape `hub/client_context.for_prompt()` already produces: a heading,
    one attributed line per fact, a "Not on file, and not to be invented"
    line, and the closing instruction not to state anything not listed.
    Returns "" when nothing was looked up — the `for_prompt()` rule that the
    caller's own input is not a lookup.
    """
    if not isinstance(brief, dict):
        return ""
    sections = _AUDIENCE_SECTIONS.get(audience, _AUDIENCE_SECTIONS["copy"])
    client = _clean(brief.get("client") or "")

    lines: list[str] = []
    unknown_labels: list[str] = []
    looked_up = 0

    for section in sections:
        block = brief.get(section) or {}
        if block.get("measured") is False:
            continue
        for field, fact in block.items():
            if field == "measured":
                continue
            if audience == "image" and section == "brand" and field not in _IMAGE_BRAND_ONLY:
                continue
            if audience == "copy" and section == "digital" and field != "has_adwords_spend":
                continue
            if not isinstance(fact, dict) or "value" not in fact:
                # A composite (logos/palette/screenshots list) — describe it
                # in one line rather than dumping structure into a prompt.
                if isinstance(fact, list) and fact:
                    label = _ROW_LABELS.get(field, field)
                    if field == "logos":
                        urls = ", ".join(f"{x.get('url')} ({x.get('origin')})"
                                          for x in fact if x.get("url"))
                        if urls:
                            lines.append(f"- {label}: {urls}")
                            looked_up += 1
                    elif field == "palette":
                        hexes = ", ".join(f"{x.get('hex')}"
                                          + (f" ({x.get('role')})" if x.get("role") else "")
                                          for x in fact if x.get("hex"))
                        if hexes:
                            lines.append(f"- {label}: {hexes}")
                            looked_up += 1
                    elif field == "fonts":
                        lines.append(f"- {label}: " + ", ".join(str(v) for v in fact))
                        looked_up += 1
                continue
            value = fact.get("value")
            if value in (None, ""):
                continue
            if isinstance(value, dict):
                value = ", ".join(f"{k}: {v}" for k, v in value.items())
            src = SOURCE_LABELS.get(fact.get("source", ""), "")
            looked_up += 1
            label = _ROW_LABELS.get(field, field.replace("_", " "))
            lines.append(f"- {label}: {value}" + (f" (from {src})" if src else ""))

    for section, field in brief.get("unknown") or []:
        if section not in sections:
            continue
        if audience == "image" and section == "brand" and field not in _IMAGE_BRAND_ONLY:
            continue
        unknown_labels.append(_ROW_LABELS.get(field, field.replace("_", " ")))

    if audience == "audio":
        pron = None
        voice_block = brief.get("voice") or {}
        pron_fact = voice_block.get("pronunciation")
        if isinstance(pron_fact, dict) and pron_fact.get("value"):
            pron = pron_fact.get("value")
        phone_fact = (brief.get("contact") or {}).get("phone")
        if isinstance(phone_fact, dict) and phone_fact.get("value") and not any(
                "Phone" in ln for ln in lines):
            lines.append(f"- Phone (spoken form): {phone_fact['value']}")
            looked_up += 1
        if pron:
            lines.append(f"- Pronunciation notes: {pron}")

    # The caller's own input is not a lookup -- render() must not manufacture
    # a "what we know" heading over a brief that found nothing at all.
    if not looked_up:
        return ""

    out = [f"What we know about {client or 'this business'}:"]
    out += lines
    if unknown_labels:
        out.append("Not on file, and not to be invented: "
                   + ", ".join(sorted(set(unknown_labels))).lower() + ".")
    out.append("Use these facts where they are relevant. Do not state any "
               "fact about this business that is not listed above.")
    text = "\n".join(out)
    return text[:max_chars]


def build_from_fields(fields: dict) -> dict:
    """The same brief shape, from a landing-page intake form.

    Used for prospects: company, website, zip, an industry description, a
    budget -- whatever a rv/stadium/tourism/boat/hvac/legal/recruit/ski/
    restaurant form actually collects. No lookups run, no scan is read; every
    source is labelled "the form", because a prospect's own answer is the
    only fact this Hub holds about them.
    """
    fields = fields or {}
    client = _clean(fields.get("company") or fields.get("business")
                    or fields.get("business_name") or fields.get("name") or "")
    domain = _clean(fields.get("website") or fields.get("url") or "")
    out: dict[str, Any] = {"client": client, "domain": domain, "built_at": _now()}

    identity = {}
    if client:
        identity["name"] = _fact(client, "form")
    if domain:
        identity["website"] = _fact(domain, "form")
    industry = _clean(fields.get("industry") or fields.get("industry_text")
                      or fields.get("category") or "")
    if industry:
        identity["industry_label"] = _fact(industry, "form")
    out["identity"] = identity or {"measured": False}

    location = {}
    zip_ = _clean(fields.get("zip") or fields.get("postcode") or "")
    city = _clean(fields.get("city") or "")
    state = _clean(fields.get("state") or "")
    if zip_:
        location["zip"] = _fact(zip_, "form")
    if city:
        location["city"] = _fact(city, "form")
    if state:
        location["state"] = _fact(state, "form")
    out["location"] = location or {"measured": False}

    contact = {}
    phone = _clean(fields.get("phone") or "")
    email = _clean(fields.get("email") or "")
    if phone:
        contact["phone"] = _fact(phone, "form")
    if email:
        contact["email"] = _fact(email, "form")
    out["contact"] = contact or {"measured": False}

    budget = _clean(fields.get("budget") or "")
    digital = {}
    if budget:
        digital["budget"] = _fact(budget, "form")
    out["digital"] = digital or {"measured": False}

    for section in ("brand", "voice", "proof", "seo", "social", "products", "scan"):
        out.setdefault(section, {"measured": False})

    out["unknown"] = []
    return out


def for_prompt(client: str, domain: str = "", *, heading: str = "") -> str:
    """`hub.client_context.for_prompt()`'s own signature, over the new brief.

    Kept so nothing that imports `for_prompt` has to change. It is now
    ``render(build(client, domain), "copy")`` with the caller's own heading
    applied — the same output shape, assembled from the one client brief
    rather than a second, narrower reader of the same sources.
    """
    try:
        brief = build(client, domain)
    except Exception:                                     # noqa: BLE001
        return ""
    text = render(brief, "copy")
    if not text:
        return ""
    if heading:
        lines = text.split("\n")
        if lines and lines[0].startswith("What we know about"):
            lines[0] = heading
            text = "\n".join(lines)
    return text


# --------------------------------------------------------------- CI integrity
# Files that legitimately mention an OpenAI call shape without making one:
# hub/ai.py *is* the wrapper, hub/openai_responses.py is the one documented
# second reader (the hosted-tool Responses API, a different transport with
# different failure modes -- CLAUDE.md's own section on it), and
# hub/integrity.py / hub/quotas.py name these patterns as *strings* to detect
# them elsewhere, which this file's own detector would otherwise flag as the
# very thing it exists to find. Every entry carries its reason -- an entry
# without one is itself a finding, the `tools/spellcheck.py` ALLOW rule.
ALLOW = {
    "hub/ai.py": "is the wrapper",
    "hub/client_brief.py": "names these call shapes as strings to detect "
        "them elsewhere (this file), and does not call OpenAI itself",
    "hub/openai_responses.py": "the documented second reader for the "
        "Responses API's hosted tools, a different transport with its own "
        "failure modes; CLAUDE.md names it as the deliberate exception",
    "hub/integrity.py": "names these call shapes as strings to detect them "
        "elsewhere, and does not call OpenAI itself",
    "hub/quotas.py": "names these call shapes as strings to detect them "
        "elsewhere, and does not call OpenAI itself",
    "hub/diagnostics.py": "pings /v1/models with a bare GET to check a key, "
        "never a completion or an image",
    "hub/ai_models.py": "reads the published model list from /v1/models, "
        "never a completion or an image",
    "modules/commercial_builder/services/provider_check.py": "pings "
        "/v1/models with a bare GET to check a key, never a completion or "
        "an image -- the services/provider_check.py rule that a health "
        "probe is not a spend",
    # Identified by the WO-1 sweep and confirmed as real, live OpenAI call
    # sites -- none of these were in the work order's named list (rv,
    # stadium, tourism only), and each poses more risk to migrate quickly
    # than the time available for this pass allows: six of them post a
    # `text.format: json_schema` structured-output payload to the Responses
    # API with a market-report schema unique to each trade, and the other
    # two are a chat-completions call and an images/generations call behind
    # an `OPENAI_BASE_URL` override neither hub.ai nor hub.openai_responses
    # currently honours. Follow-up work, not a decision that these should
    # stay outside the wrapper permanently -- see the delivery report.
    "modules/boat/app.py": "landing-page market report, /v1/responses with "
        "a json_schema payload -- not migrated in this pass, follow-up work",
    "modules/legal/app.py": "landing-page market report, /v1/responses with "
        "a json_schema payload -- not migrated in this pass, follow-up work",
    "modules/restaurant/app.py": "landing-page market report, /v1/responses "
        "with a json_schema payload -- not migrated in this pass, follow-up work",
    "modules/ski/app.py": "landing-page market report, /v1/responses with a "
        "json_schema payload -- not migrated in this pass, follow-up work",
    "modules/hvac/app.py": "landing-page market report, /v1/responses with a "
        "json_schema payload -- not migrated in this pass, follow-up work",
    "modules/recruit/app.py": "landing-page market report, /v1/responses "
        "with a json_schema payload -- not migrated in this pass, follow-up work",
    "modules/ads_builder/pmax_images.py": "images/generations for PMax asset "
        "groups -- not migrated in this pass, follow-up work",
    "modules/landing_ads/ai.py": "chat/completions behind an "
        "OPENAI_BASE_URL override -- not migrated in this pass, follow-up work",
    "modules/radio_promo/ai.py": "chat/completions and images/generations "
        "behind an OPENAI_BASE_URL override -- not migrated in this pass, "
        "follow-up work",
}

_SKIP_DIRS = {"_attic", "__pycache__", ".git", "node_modules", ".venv",
             "venv", "env", "site-packages", ".tox", "build", "dist", "seed"}
_SKIP_ROOTS = {"tools"}

_ENDPOINT_MARKERS = ("/v1/chat/completions", "api.openai.com")


def _file_calls_openai_directly(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "openai":
            return True
        if isinstance(node, ast.Import) and any(
                a.name == "openai" for a in node.names):
            return True
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "OpenAI":
                return True
            if isinstance(fn, ast.Attribute):
                chain = []
                cur = fn
                while isinstance(cur, ast.Attribute):
                    chain.append(cur.attr)
                    cur = cur.value
                tail = ".".join(reversed(chain))
                if tail.endswith("chat.completions.create") or \
                   tail.endswith("responses.create") or \
                   tail.endswith("images.generate"):
                    return True
            # A string naming the endpoint is a call site only where it is
            # actually *handed to something* -- an argument, a call, an
            # assignment a call later reads. Anywhere else (a docstring, a
            # comment-shaped string, prose explaining the fix) is not one:
            # `hub/config.py`'s drift check makes the same distinction about
            # a docstring quoting a call to explain it, and several modules
            # here do exactly that about these endpoints.
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(m in arg.value for m in _ENDPOINT_MARKERS):
                        return True
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            if any(m in node.value.value for m in _ENDPOINT_MARKERS):
                return True
    return False


def check_ai_callers(root=None) -> list[dict]:
    """Every file that reaches OpenAI without going through hub.ai.

    A caller that builds its own client, or posts straight to the API, gets
    no client brief injected and writes no usage row -- the exact gap this
    file exists to close. Copies the AST-walk pattern of
    `hub/client_brand._log_call_sites()`: read the source, not the running
    app, and read it as a syntax tree rather than by matching text, or a
    module quoting one of these calls in a docstring to explain the fix (as
    several already do) gets reported for explaining it.

    Walks only ``hub/`` and ``modules/`` -- the same two folders
    `client_brand._log_call_sites()` walks. The top-level ``test_*.py``
    files are not callers of anything, and at least one embeds a string
    like ``"api.openai.com"`` inside a fixture to test a *different* check
    (``quotas.openai_spend_unrecorded()``); a fixture string is prose, not a
    call site, for the same reason a docstring quoting one of these calls to
    explain a fix is not one.
    """
    import pathlib

    base = pathlib.Path(root) if root else pathlib.Path(__file__).resolve().parent.parent
    out: list[dict] = []

    for folder in ("hub", "modules"):
        scan_root = base / folder
        if not scan_root.is_dir():
            continue
        for p in scan_root.rglob("*.py"):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                rel_parts = p.relative_to(base).parts
            except ValueError:
                continue
            if rel_parts and rel_parts[0] in _SKIP_ROOTS:
                continue
            rel = "/".join(rel_parts)
            if rel in ALLOW:
                continue
            try:
                src = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            if _file_calls_openai_directly(tree):
                mod = rel_parts[1] if rel_parts[0] == "modules" and len(rel_parts) > 1 else "hub"
                out.append({
                    "file": rel,
                    "module": mod,
                    "detail": "calls OpenAI directly, so the client brief is not "
                              "injected and no usage row is written",
                    "fix": "route through hub.ai.chat()/image() with client= or brief=",
                })

    for path, reason in ALLOW.items():
        if not reason or not reason.strip():
            out.append({
                "file": path, "module": "", "allow_entry": True,
                "detail": "ALLOW entry has no reason recorded",
                "fix": "name why this file is exempt, or remove the entry",
            })

    return out
