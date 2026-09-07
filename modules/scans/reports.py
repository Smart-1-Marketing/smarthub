"""Client-facing audit reports built from a stored Insites payload.

The scan record holds ~440 raw fields. Nobody reads that. This module turns it
into the eight reports the Hub offers — SEO & AEO, Content & UX, Ads, Social
Media, Speed & Performance, Accessibility, Compliance, and a Complete Audit
that stacks them all — in a shape a rep can hand to a client.

Three rules run through the whole file:

* **Never invent a pass.** A section the account's plan didn't return, or a
  check that couldn't run, reads as "not measured". Zero broken links and
  "we didn't look for broken links" are different sentences, and only one of
  them is a selling point.
* **Every finding names the fix.** A check carries a plain sentence saying what
  to do, not just a red dot. That sentence is what ends up in the client PDF.
* **Insites' own words where they exist.** Most sections ship a ``datasets``
  block whose entries already carry a title and an explanation written for a
  business owner. Those are used verbatim rather than paraphrased, so the Hub
  report and the Insites PDF never contradict each other.

Entry points:
    ``report(payload, key)``      -> one report dict, ready for a template
    ``available(payload)``        -> the menu, with per-report severity
    ``REPORTS``                   -> the catalogue (key, title, blurb)
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from .audit_fields import get_field, tier_for_score
from . import site_health

# ==========================================================================
# Small helpers
# ==========================================================================

_OK, _BAD, _WARN, _INFO = "ok", "bad", "warn", "info"
_ORDER = {_BAD: 0, _WARN: 1, _INFO: 2, _OK: 3}


def _sec(payload: dict, name: str) -> dict:
    """One top-level namespace as a dict — {} when absent."""
    v = payload.get(name) if isinstance(payload, dict) else None
    return v if isinstance(v, dict) else {}


def _has(payload: dict, name: str) -> bool:
    """Did this scan return the namespace at all?"""
    return isinstance(payload, dict) and isinstance(payload.get(name), dict)


def _b(sec: dict, key: str) -> Optional[bool]:
    v = sec.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str) and v.strip().lower() in ("true", "false", "yes", "no"):
        return v.strip().lower() in ("true", "yes")
    return None


def _i(sec: dict, key: str) -> Optional[int]:
    v = sec.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _f(sec: dict, key: str) -> Optional[float]:
    v = sec.get(key)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _s(sec: dict, key: str) -> str:
    v = sec.get(key)
    return "" if v is None or isinstance(v, (dict, list)) else str(v).strip()


def _list(sec: dict, key: str) -> list:
    v = sec.get(key)
    return v if isinstance(v, list) else []


def _ds(sec: dict, name: str) -> list:
    """A named dataset list from a section's ``datasets`` block."""
    d = sec.get("datasets")
    if not isinstance(d, dict):
        return []
    v = d.get(name)
    return v if isinstance(v, list) else []


_PLURALS = {"property": "properties", "page": "pages", "image": "images",
            "issue": "issues", "link": "links", "review": "reviews",
            "post": "posts", "keyword": "keywords", "cookie": "cookies",
            "thing": "things", "ad": "ads", "day": "days"}


def _plural(one: str) -> str:
    """'required property' -> 'required properties'. Only the last word
    inflects, which is what every phrase in this file needs."""
    head, _, last = one.rpartition(" ")
    tail = _PLURALS.get(last.lower())
    if tail is None:
        tail = last + ("es" if last.endswith(("s", "x", "ch", "sh")) else "s")
    elif last[:1].isupper():
        tail = tail.capitalize()
    return (head + " " + tail).strip()


def _verb(n: Optional[int], singular: str = "is", plural: str = "are") -> str:
    return singular if n == 1 else plural


def _num(n: Optional[int], one: str, many: str = "") -> str:
    if n is None:
        return f"— {many or _plural(one)}"
    return f"{n:,} {one if n == 1 else (many or _plural(one))}"


def chk(label: str, state: str, detail: str = "", value: str = "",
        link: str = "") -> dict:
    """One checkpoint on a report."""
    return {"label": label, "state": state, "detail": detail,
            "value": value, "link": link}


# Insites writes British English in the callback but American English in its
# own PDF. Smart 1 sells in the US, so the copy we lift gets normalized.
# Stem patterns are lookahead-guarded: a bare "analys" -> "analyz" also
# rewrites "analysis" into "analyzis", which is how this bit gets embarrassing.
_UK_US = [
    # "analyses" is the plural noun far more often than the verb here, so it
    # is left alone; everything else in the family converts.
    (r"analys(?=e(?!s\b)|ing|er)", "analyz"),
    (r"organis(?=e|a|ing|ed)", "organiz"),
    (r"optimis(?=e|a|ing|ed)", "optimiz"),
    (r"summaris(?=e|a|ing|ed)", "summariz"),
    (r"recognis(?=e|a|ing|ed)", "recogniz"),
    (r"prioritis(?=e|a|ing|ed)", "prioritiz"),
    (r"utilis(?=e|a|ing|ed)", "utiliz"),
    (r"personalis(?=e|a|ing|ed)", "personaliz"),
    (r"categoris(?=e|a|ing|ed)", "categoriz"),
    (r"favour", "favor"),
    (r"colour", "color"),
    (r"behaviour", "behavior"),
    (r"labelled", "labeled"),
    (r"modelled", "modeled"),
    (r"cancelled", "canceled"),
    (r"travelling", "traveling"),
    (r"practise", "practice"),
    (r"\bcentre\b", "center"),
    (r"\bcatalogue\b", "catalog"),
    (r"\blicence\b", "license"),
    (r"\bwhilst\b", "while"),
]

_UK_US_RE = [(re.compile(pat, re.IGNORECASE), rep) for pat, rep in _UK_US]


def _match_case(original: str, replacement: str) -> str:
    return replacement.capitalize() if original[:1].isupper() else replacement


def _us(text: str) -> str:
    """British -> American, preserving the leading capital of each match."""
    if not text:
        return ""
    for rx, rep in _UK_US_RE:
        text = rx.sub(lambda m, rep=rep: _match_case(m.group(0), rep), text)
    return text


def _auto_checks(sec: dict) -> list[dict]:
    """Checkpoints Insites already wrote, lifted out of the datasets block.

    Three shapes appear across the payload and all three mean the same thing:

        {"was_success": "Yes", "details": {"title", "summary"}}
        {"was_found":   "Yes", "detail":  {"title"|"identifier", "summary"}}
        {"value": "320",       "details": {"title", "summary"}}

    Anything that doesn't match is left alone — it belongs in a table, not in
    the pass/fail list.
    """
    out: list[dict] = []
    d = sec.get("datasets")
    if not isinstance(d, dict):
        return out
    for rows in d.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            det = row.get("details") if isinstance(row.get("details"), dict) \
                else (row.get("detail") if isinstance(row.get("detail"), dict) else None)
            if det is None:
                continue
            title = (det.get("title") or "").strip()
            if not title and det.get("identifier"):
                title = _humanise(str(det.get("identifier")))
            summary = (det.get("summary") or "").strip()
            if not title and not summary:
                continue
            flag = row.get("was_success", row.get("was_found"))
            value = row.get("value")
            if flag is None and value is not None:
                state = _INFO
            else:
                yes = str(flag).strip().lower() in ("yes", "true", "1")
                unknown = str(flag).strip().lower() in ("", "none", "unknown", "n/a")
                state = _INFO if unknown else (_OK if yes else _BAD)
            out.append(chk(_us(title) or "Check", state, _us(summary),
                           "" if value is None else str(value),
                           det.get("title_url") or ""))
    return out


# Some datasets label a checkpoint with a machine identifier ("openingHours")
# instead of a sentence. Nobody wants that on a client PDF.
_IDENTIFIER_LABELS = {
    "claimed": "Profile claimed", "name": "Business name listed",
    "website": "Website on the profile", "phone": "Phone number listed",
    "address": "Address listed", "reviews": "Reviews on the profile",
    "rating": "Star rating", "openinghours": "Opening hours",
    "images": "Photos", "posts": "Google posts",
    "categories": "Category set", "service_options": "Service options",
    "booking_link": "Booking link", "listing_attributes": "Profile attributes",
    "description": "Business description",
}


def _humanise(identifier: str) -> str:
    """'openingHours' / 'booking_link' -> a label a client can read."""
    key = (identifier or "").strip()
    known = _IDENTIFIER_LABELS.get(key.lower())
    if known:
        return known
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", key).replace("_", " ")
    return spaced.strip().capitalize() or key


def _add(checks: list[dict], check: dict, *keywords: str) -> None:
    """Append a hand-written check unless Insites already supplied one on the
    same subject. Two lines saying the same thing in different words reads as
    a bug to the client, and one of them is always the weaker sentence."""
    hay = " | ".join(c["label"].lower() for c in checks)
    for k in keywords or (check["label"],):
        if k.lower() in hay:
            return
    checks.append(check)


def _dedupe(checks: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in checks:
        k = (c["label"] or "").strip().lower()
        if k and k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def _severity(checks: list[dict]) -> str:
    states = {c["state"] for c in checks}
    if _BAD in states:
        return _BAD
    if _WARN in states:
        return _WARN
    if _OK in states:
        return _OK
    return "unknown"


def section(key: str, title: str, present: bool, verdict: str,
            checks: list[dict] | None = None, stats: list[dict] | None = None,
            tables: list[dict] | None = None, note: str = "",
            severity: str = "") -> dict:
    checks = _dedupe([c for c in (checks or []) if c])
    return {
        "key": key,
        "title": title,
        "measured": bool(present),
        "verdict": verdict,
        "severity": (severity or _severity(checks)) if present else "unknown",
        "checks": checks,
        "stats": [s for s in (stats or []) if s],
        "tables": [t for t in (tables or []) if t and t.get("rows")],
        "note": note,
        "passed": sum(1 for c in checks if c["state"] == _OK),
        "failed": sum(1 for c in checks if c["state"] == _BAD),
        "warned": sum(1 for c in checks if c["state"] == _WARN),
    }


def stat(label: str, value: Any, band: str = "", note: str = "") -> dict:
    return {"label": label, "value": "—" if value in (None, "") else str(value),
            "band": band, "note": note}


def table(title: str, columns: list[str], rows: list[list],
          note: str = "", limit: int = 25) -> dict:
    clean = [[("" if c is None else str(c)) for c in r] for r in rows if r]
    more = len(clean) - limit
    return {"title": title, "columns": columns, "rows": clean[:limit],
            "note": note + (f"  Showing {limit} of {len(clean)}."
                            if more > 0 else "")}


def _missing(key: str, title: str, why: str = "") -> dict:
    return section(key, title, False,
                   why or "This scan didn't return this check. Re-run the "
                          "audit, or check the plan covers it.")


# ==========================================================================
# SEO & AEO sections
# ==========================================================================

def s_ai_readiness(p: dict) -> dict:
    sec = _sec(p, "ai_readiness")
    if not sec:
        return _missing("ai_readiness", "AI search readiness")
    checks = _auto_checks(sec)
    if not checks:
        for key, label, good, bad in (
            ("ai_user_agents_allowed", "AI crawlers allowed",
             "ChatGPT, Perplexity and Gemini are allowed to read this site.",
             "robots.txt blocks AI crawlers, so AI assistants can't quote this "
             "business."),
            ("sitemap_found", "Sitemap found",
             "A sitemap is published, so AI and search engines can find every page.",
             "No sitemap — AI and search engines have to guess which pages matter."),
            ("local_structured_data_found", "Local structured data",
             "LocalBusiness markup tells AI where this business is and what it does.",
             "No LocalBusiness markup. Adding it is the single cheapest AI-visibility win."),
            ("faq_structured_data_found", "FAQ structured data",
             "FAQ markup is in place, so answers can be quoted directly.",
             "No FAQ markup. AI answers are assembled from Q&A — without it this "
             "site is skipped."),
            ("content_recently_updated", "Content freshness",
             "Content has been updated recently, which AI treats as a live site.",
             "Content looks stale. AI models favour recently updated pages."),
            ("website_has_enough_content", "Enough content",
             "There's enough written content for AI to summarize.",
             "Too little written content for AI to summarize confidently."),
            ("website_has_enough_pages", "Enough pages",
             "The site has enough pages to be treated as a real source.",
             "Very few pages. More topic pages means more chances to be cited."),
            ("reading_difficulty_optimised", "Reading level",
             "The writing is clear enough for AI to summarize accurately.",
             "The writing is complex enough that AI may summarize it wrongly."),
        ):
            v = _b(sec, key)
            if v is None:
                continue
            checks.append(chk(label, _OK if v else _BAD, good if v else bad))
    llms = _b(sec, "llms_txt_found")
    if llms is not None and not any("llms" in c["label"].lower() for c in checks):
        checks.append(chk(
            "llms.txt", _OK if llms else _WARN,
            "An llms.txt file is published — a sitemap for large language models."
            if llms else
            "No llms.txt. It doesn't affect the score, but it's the emerging "
            "standard for telling AI what to use. The Hub can generate one."))
    issues = _i(sec, "ai_issue_count")
    optimised = _b(sec, "is_ai_optimised")
    partial = _b(sec, "is_partially_ai_optimised")
    if optimised:
        verdict = "This site is set up to be found and quoted by AI search."
    elif partial:
        verdict = ("This site is partly optimized for AI search — "
                   f"{_num(issues, 'issue')} standing between it and the rest.")
    else:
        verdict = ("AI assistants are unlikely to surface this business. "
                   f"{_num(issues, 'issue')} found.")
    return section("ai_readiness", "AI search readiness", True, verdict,
                   checks, [stat("Issues found", issues,
                                 _OK if not issues else _BAD)])


def _assistant_row(row: dict) -> list:
    name = str(row.get("voice_assistant", "")).replace("_", " ").title()
    def y(k):
        return "Yes" if row.get(k) else "No"
    return [name, y("directory_business_name"), y("directory_address"),
            y("directory_telephone"), y("directory_hours"), y("directory_website")]


def s_voice_search(p: dict) -> dict:
    sec = _sec(p, "voice_search")
    if not sec:
        return _missing("voice_search", "Voice search")
    if not _b(sec, "has_result"):
        return section("voice_search", "Voice search", True,
                       "Voice search couldn't be checked for this business.",
                       [chk("Voice search", _INFO,
                            "Usually means the listing data needed to query the "
                            "assistants wasn't available.")])
    ok = bool(_b(sec, "is_voice_search_optimised"))
    found = _list(sec, "voice_assistants_found")
    tested = _list(sec, "voice_assistants_tested")
    complete = _list(sec, "voice_assistants_complete")
    rows = [_assistant_row(r) for r in _ds(sec, "Summary") if isinstance(r, dict)]
    checks = [chk(
        "Found by voice assistants",
        _OK if found else _BAD,
        f"Listed on {len(found)} of {len(tested)} assistants checked."
        if found else
        "Not found on any assistant checked. Voice results come from directory "
        "listings — fix the listings and this follows.")]
    checks.append(chk(
        "Complete listings", _OK if complete else _WARN,
        "Every listing carries name, address, phone, hours and website."
        if complete else
        "Listings are missing fields. Hours and website are the two most often "
        "left blank, and both are asked for by voice."))
    return section(
        "voice_search", "Voice search", True,
        "This business is optimized for voice search." if ok else
        "This business is not fully set up for voice search.",
        checks,
        [stat("Assistants found on", f"{len(found)}/{len(tested)}",
              _OK if found and len(found) == len(tested) else _WARN)],
        [table("What each assistant can see",
               ["Assistant", "Name", "Address", "Phone", "Hours", "Website"],
               rows)],
        "Voice results are read from directory listings, not from the website.")


def _ai_engine(p: dict, ns: str, title: str, brand: str) -> dict:
    sec = _sec(p, ns)
    if not sec:
        return _missing(ns, title)
    missing = (_b(sec, "missing_required_data")
               or _b(sec, f"missing_required_data_{ns}"))
    ran = (_b(sec, f"ran_{ns}_test") or _b(sec, "ran_gpt_v2_test")
           or _b(sec, f"ran_{ns}_v2_test"))
    if missing:
        return section(
            ns, title, True,
            f"{brand} couldn't be checked — the business name or location "
            "needed for the query is missing from the record.",
            [chk(f"{brand} check", _INFO,
                 "Fill in the business name, address and phone on the client "
                 f"record, then re-run the scan to get a real {brand} reading.")])
    if ran is False:
        return section(ns, title, True,
                       f"The {brand} check didn't run on this scan.",
                       [chk(f"{brand} check", _INFO,
                            "Re-run the audit to include it.")])
    checks = _auto_checks(sec)
    return section(ns, title, True,
                   f"{brand} visibility for this business." if checks else
                   f"{brand} returned no findings on this scan.",
                   checks)


def s_chatgpt(p):     return _ai_engine(p, "chatgpt", "ChatGPT", "ChatGPT")
def s_perplexity(p):  return _ai_engine(p, "perplexity", "Perplexity", "Perplexity")
def s_gemini(p):      return _ai_engine(p, "gemini", "Gemini mentions", "Gemini")


def s_gemini_opt(p: dict) -> dict:
    sec = _sec(p, "gemini_optimisation")
    if not sec:
        return _missing("gemini_optimisation", "Google Gemini optimization")
    checks = _auto_checks(sec)
    issues = _i(sec, "issues_found_count")
    return section(
        "gemini_optimisation", "Google Gemini optimization", True,
        "This business follows best practice for visibility in Google Gemini."
        if not issues else
        f"Gemini may overlook this business — {_num(issues, 'issue')} found.",
        checks, [stat("Issues", issues, _OK if not issues else _BAD)],
        note="Gemini leans on Google Business Profile data, so most fixes here "
             "happen in the Google profile rather than on the website.")


def s_eeat(p: dict) -> dict:
    sec = _sec(p, "eeat")
    if not sec:
        return _missing("eeat", "E-E-A-T (trust signals)")
    overall = _i(sec, "eeat_overall_score")
    parts = [("Experience", _i(sec, "eeat_experience_score")),
             ("Expertise", _i(sec, "eeat_expertise_score")),
             ("Authoritativeness", _i(sec, "eeat_authoritativeness_score")),
             ("Trustworthiness", _i(sec, "eeat_trustworthiness_score"))]
    def band(v):
        return "" if v is None else (_OK if v >= 75 else (_WARN if v >= 50 else _BAD))
    checks = [chk(label, band(v) or _INFO,
                  {"Experience": "First-hand proof: case studies, photos of real "
                                 "work, named projects.",
                   "Expertise": "Credentials, author bios, depth on the service pages.",
                   "Authoritativeness": "Being cited elsewhere — press, directories, "
                                        "partner sites, reviews.",
                   "Trustworthiness": "Contact details, policies, HTTPS, and "
                                      "consistent business information."}[label],
                  f"{v}%" if v is not None else "—")
              for label, v in parts]
    rows = [[r.get("page", ""), r.get("priority", ""), r.get("improves", ""),
             r.get("impact", ""), r.get("effort", "")]
            for r in _ds(sec, "Test_Page_EEAT_priorities") if isinstance(r, dict)]
    return section(
        "eeat", "E-E-A-T (trust signals)", True,
        f"Overall E-E-A-T performance is {overall}%." if overall is not None
        else "E-E-A-T was assessed for this site.",
        checks, [stat("Overall E-E-A-T", f"{overall}%" if overall is not None else None,
                      band(overall))],
        [table("Top pages to improve",
               ["Page", "Priority", "What it improves", "Impact", "Effort"], rows)],
        "Google uses E-E-A-T to decide whether a site deserves to rank. AI "
        "assistants use the same signals to decide whether to quote it.")


def _indexability(p: dict, ns: str, title: str, engine: str, count_key: str,
                  ds_name: str) -> dict:
    sec = _sec(p, ns)
    if not sec:
        return _missing(ns, title)
    pages = _i(sec, count_key)
    blocked = _b(sec, "blocks_all_routes")
    bots = _list(sec, "blocked_seo_bots")
    staging = _b(sec, "is_staging_domain")
    checks = [chk(f"Pages indexed by {engine}",
                  _OK if pages else _BAD,
                  f"{engine} has discovered {pages:,} pages." if pages
                  else f"{engine} has not indexed this site.",
                  f"{pages:,}" if pages is not None else "—")]
    checks.append(chk("Crawlers allowed", _BAD if blocked else _OK,
                      "robots.txt blocks the whole site — nothing can be indexed."
                      if blocked else
                      "Nothing in robots.txt is blocking the site."))
    if bots:
        checks.append(chk("Blocked bots", _BAD,
                          "Blocked in robots.txt: " + ", ".join(str(b) for b in bots)))
    if staging:
        checks.append(chk("Staging domain", _WARN,
                          "This looks like a staging domain — check the live site "
                          "is the one being audited."))
    rows = [[r.get("page_url", ""), r.get("serp_title", "")]
            for r in _ds(sec, "indexed_pages") if isinstance(r, dict)]
    return section(ns, title, True,
                   f"{engine} has indexed {pages:,} pages from this site."
                   if pages else f"{engine} has not indexed this site.",
                   checks, [stat(f"{engine} indexed pages", pages,
                                 _OK if pages else _BAD)],
                   [table("Top indexed pages", ["Indexed URL", "Search result title"],
                          rows)])


def s_google_index(p):
    return _indexability(p, "google_indexability", "Google indexability",
                         "Google", "estimated_indexed_pages", "indexed_pages")


def s_bing_index(p):
    return _indexability(p, "bing_indexability", "Bing indexability", "Bing",
                         "bing_estimated_indexed_pages", "")


def s_sitemap(p: dict) -> dict:
    sec = _sec(p, "sitemap")
    if not sec:
        return _missing("sitemap", "Sitemap")
    has = _b(sec, "has_sitemap")
    issues = _b(sec, "sitemap_issues")
    rows = [[r.get("sitemap", ""), r.get("sitemap_status", ""), r.get("page_count", "")]
            for r in _ds(sec, "discovered_sitemaps") if isinstance(r, dict)]
    checks = [chk("Sitemap published", _OK if has else _BAD,
                  "A sitemap is published at the expected location." if has else
                  "No sitemap found. Search engines are left to discover pages "
                  "by following links.")]
    if issues is not None:
        checks.append(chk("Sitemap valid", _BAD if issues else _OK,
                          "The sitemap has problems — see the status column below."
                          if issues else "The sitemap parses cleanly."))
    return section("sitemap", "Sitemap", True,
                   "Sitemap found and valid." if has and not issues else
                   ("Sitemap found, but it has problems." if has else
                    "No sitemap found."),
                   checks, [], [table("Sitemaps found",
                                      ["Sitemap", "Status", "Pages listed"], rows)])


def s_structured_data(p: dict) -> dict:
    sec = _sec(p, "structured_data")
    if not sec:
        return _missing("structured_data", "Structured data (schema)")
    if _b(sec, "error_with_schema_check"):
        return section("structured_data", "Structured data (schema)", True,
                       "The schema check couldn't complete on this site.",
                       [chk("Schema check", _INFO, "Re-run the audit.")])
    found = _b(sec, "has_found_schema_items")
    miss_pct = _i(sec, "percent_missing_schema_items")
    miss_n = _i(sec, "count_missing_schema_items")
    incon_n = _i(sec, "count_inconsistent_schema_items")
    opt_n = _i(sec, "count_missing_optional_schema_items")
    checks = [chk("Schema present", _OK if found else _BAD,
                  "Structured data was found on the site." if found else
                  "No structured data at all. This is the biggest single miss "
                  "for both Google rich results and AI answers.")]
    if miss_n is not None:
        checks.append(chk("Required properties", _OK if not miss_n else _BAD,
                          "All required schema properties are present."
                          if not miss_n else
                          f"{_num(miss_n, 'required property')} missing "
                          f"({miss_pct}% of what Google expects).",
                          f"{miss_n}"))
    if incon_n is not None:
        checks.append(chk("Consistency", _OK if not incon_n else _WARN,
                          "Schema values match what's on the page." if not incon_n
                          else f"{_num(incon_n, 'property')} disagrees with the "
                               "visible page content. Google discounts schema it "
                               "can't verify on the page."))
    if opt_n:
        checks.append(chk("Recommended properties", _WARN,
                          f"{_num(opt_n, 'recommended property')} missing. Optional "
                          "for validity, but they're what earn the rich result."))
    rows = [[r.get("schema_name", ""), r.get("details", "")]
            for r in _ds(sec, "SchemaData") if isinstance(r, dict)]
    return section("structured_data", "Structured data (schema)", True,
                   "Schema is installed and complete." if found and not miss_n else
                   (f"Schema is installed but {miss_pct}% of the expected "
                    "properties are missing." if found else
                    "No structured data found on this site."),
                   checks,
                   [stat("Missing properties", miss_n, _OK if not miss_n else _BAD),
                    stat("Inconsistent", incon_n, _OK if not incon_n else _WARN)],
                   [table("Schema found", ["Item", "Value"], rows)],
                   "The Hub's Schema Builder writes this markup page by page.")


def s_titles(p: dict) -> dict:
    sec = _sec(p, "page_titles_and_descriptions")
    if not sec:
        return _missing("page_titles_and_descriptions", "Titles & meta descriptions")
    miss_t = _i(sec, "pages_missing_title_count")
    miss_d = _i(sec, "pages_missing_description_count")
    dup_t = _i(sec, "pages_duplicated_title_count")
    dup_d = _i(sec, "pages_duplicated_description_count")
    bad_len = _b(sec, "titles_or_descriptions_bad_length")
    checks = [
        chk("Every page has a title", _OK if not miss_t else _BAD,
            "No page is missing a title tag." if not miss_t else
            f"{_num(miss_t, 'page')} missing a title tag. Google writes its own "
            "when you don't, and it rarely picks well."),
        chk("Every page has a description", _OK if not miss_d else _BAD,
            "No page is missing a meta description." if not miss_d else
            f"{_num(miss_d, 'page')} missing a meta description — that's the "
            "sales line under the search result."),
        chk("No duplicates", _OK if not (dup_t or dup_d) else _BAD,
            "Titles and descriptions are unique across the site."
            if not (dup_t or dup_d) else
            f"Duplicated on {_num(dup_t or 0, 'page')} (titles) and "
            f"{_num(dup_d or 0, 'page')} (descriptions)."),
    ]
    if bad_len is not None:
        checks.append(chk("Sensible lengths", _WARN if bad_len else _OK,
                          "Some titles or descriptions are too long or too short "
                          "and get cut off in results." if bad_len else
                          "Lengths sit inside what Google displays."))
    home_t = _s(sec, "homepage_title_tag")
    home_d = _s(sec, "homepage_meta_description")
    rows = []
    if home_t:
        rows.append(["Homepage title", home_t, f"{len(home_t)} chars"])
    if home_d:
        rows.append(["Homepage description", home_d, f"{len(home_d)} chars"])
    worst = _BAD if (miss_t or miss_d or dup_t or dup_d) else (_WARN if bad_len else _OK)
    return section("page_titles_and_descriptions", "Titles & meta descriptions",
                   True,
                   "Titles and descriptions are complete." if worst == _OK else
                   "Titles and descriptions need work.",
                   checks, [], [table("Homepage", ["Field", "Value", "Length"], rows)])


def s_faq(p: dict) -> dict:
    sec = _sec(p, "faq")
    if not sec:
        return _missing("faq", "FAQ content")
    on_page = _b(sec, "has_on_page_faq")
    schema = _b(sec, "has_faq_structured_data")
    checks = _auto_checks(sec) or []
    checks.append(chk("FAQ page", _OK if on_page else _BAD,
                      "An FAQ page was found." if on_page else
                      "No FAQ page. FAQ content is the format AI assistants quote "
                      "most often — the Hub's FAQ Builder writes one from the site."))
    checks.append(chk("FAQ schema", _OK if schema else _BAD,
                      "FAQPage markup is installed." if schema else
                      "No FAQPage markup. Without it the questions are invisible "
                      "to rich results and AI answers."))
    rows = [[r.get("faq_page", "")] for r in _ds(sec, "detected_faq_pages")
            if isinstance(r, dict)]
    return section("faq", "FAQ content", True,
                   "FAQ content is present and marked up." if on_page and schema else
                   ("FAQ content exists but isn't marked up for search."
                    if on_page else "No FAQ content found."),
                   checks, [], [table("FAQ pages found", ["Page"], rows)])


def s_backlinks(p: dict) -> dict:
    sec = _sec(p, "backlinks")
    if not sec or _b(sec, "backlinks_check_success") is False:
        return _missing("backlinks", "Backlinks")
    total = _i(sec, "total_backlinks")
    sites = _i(sec, "total_websites_linking")
    spam = _b(sec, "has_spammy_backlinks")
    ratio = (total / sites) if total and sites else None
    checks = [chk("Backlink volume",
                  _OK if (total or 0) >= 40 else (_WARN if (total or 0) >= 5 else _BAD),
                  f"{total:,} links point at this site." if total else
                  "Almost no sites link here. Links are still one of Google's "
                  "strongest ranking signals.",
                  f"{total:,}" if total is not None else "—")]
    checks.append(chk("Link diversity",
                      _OK if sites and 5 <= (ratio or 0) <= 10 else _WARN,
                      f"{sites:,} different websites link here"
                      + (f" ({ratio:.0f} links per site)." if ratio else ".")
                      + ("" if sites and 5 <= (ratio or 0) <= 10 else
                         " Links concentrated on a few domains carry less weight "
                         "than the same number spread wide."),
                      f"{sites:,}" if sites is not None else "—"))
    if spam is not None:
        checks.append(chk("Link quality", _BAD if spam else _OK,
                          "Spammy backlinks detected — worth reviewing and "
                          "disavowing the worst." if spam else
                          "No obviously spammy backlinks."))
    rows = [[r.get("url_from", ""), r.get("url_to", "")]
            for r in _ds(sec, "spammybacklinks") if isinstance(r, dict)]
    return section("backlinks", "Backlinks", True,
                   f"{total:,} backlinks from {sites:,} websites."
                   if total and sites else "Backlink profile is very thin.",
                   checks,
                   [stat("Total backlinks", total), stat("Linking websites", sites)],
                   [table("Spammy links to review", ["Linking from", "Pointing at"],
                          rows)])


def s_organic(p: dict) -> dict:
    sec = _sec(p, "organic_search")
    if not sec:
        return _missing("organic_search", "Organic search performance")
    traffic = _i(sec, "average_monthly_traffic")
    kw = _i(sec, "num_keywords_ranked_for")
    rows = [[r.get("term", ""), r.get("position", ""),
             r.get("monthly_queries_for_term", ""),
             r.get("position_change", "")]
            for r in _list(sec, "top_keywords_ranked_for_detail")
            if isinstance(r, dict)]
    checks = [
        chk("Ranking keywords", _OK if (kw or 0) >= 50 else _WARN,
            f"This site ranks for {kw:,} keywords." if kw else
            "This site barely ranks for anything yet.",
            f"{kw:,}" if kw is not None else "—"),
        chk("Organic traffic", _OK if (traffic or 0) >= 250 else _WARN,
            f"Roughly {traffic:,} organic visits a month." if traffic else
            "Almost no organic traffic. Everything else in this report is "
            "upstream of that number.",
            f"{traffic:,}/mo" if traffic is not None else "—"),
    ]
    return section("organic_search", "Organic search performance", True,
                   f"About {traffic:,} organic visits a month from "
                   f"{kw:,} ranking keywords." if traffic and kw else
                   "Organic search is not yet producing meaningful traffic.",
                   checks,
                   [stat("Monthly organic visits", traffic),
                    stat("Keywords ranked", kw)],
                   [table("Top ranking keywords",
                          ["Keyword", "Position", "Monthly searches", "Change"],
                          rows)])


def s_indexing_errors(p: dict) -> dict:
    if not _has(p, "indexing_errors"):
        return _missing("indexing_errors", "Indexing errors")
    sec = _sec(p, "indexing_errors")
    has = _b(sec, "has_indexing_errors")
    if has is None:
        has = bool([v for v in sec.values() if v])
    return section("indexing_errors", "Indexing errors", True,
                   "No indexing errors found." if not has else
                   "Indexing errors were found.",
                   [chk("Indexing", _OK if not has else _BAD,
                        "Nothing is stopping pages being indexed." if not has else
                        "Some pages can't be indexed — fix these before "
                        "investing in content.")])


def s_last_updated(p: dict) -> dict:
    sec = _sec(p, "last_updated")
    if not sec:
        return _missing("last_updated", "Content freshness")
    days = _i(sec, "days_since_update")
    band = _OK if (days is not None and days <= 60) else (
        _WARN if (days is not None and days <= 180) else _BAD)
    rows = [[r.get("date", ""), r.get("type", ""), r.get("url", "")]
            for r in _ds(sec, "recentUpdates") if isinstance(r, dict)]
    return section("last_updated", "Content freshness", True,
                   f"The site was last updated {days} days ago."
                   if days is not None else "Update history couldn't be read.",
                   [chk("Recently updated", band,
                        "Content is current." if band == _OK else
                        ("Content is getting stale — Google and AI both favour "
                         "sites that change." if band == _WARN else
                         "Nothing has changed here in a long time. This is the "
                         "cheapest ranking signal to fix."),
                        f"{days} days" if days is not None else "—")],
                   [stat("Days since update", days, band)],
                   [table("Recent changes", ["Date", "What changed", "Page"], rows)])


# ==========================================================================
# Content & UX sections
# ==========================================================================

def s_content(p: dict) -> dict:
    sec = _sec(p, "amount_of_content")
    if not sec:
        return _missing("amount_of_content", "Amount of content")
    words = _i(sec, "total_visible_word_count") or _i(sec, "total_word_count")
    avg = _i(sec, "average_visible_words_per_page") or _i(sec, "average_words_per_page")
    thin = _i(sec, "num_pages_thin_content")
    tested = _i(sec, "pages_tested")
    found = _i(sec, "pages_found")
    checks = [
        chk("Words per page", _OK if (avg or 0) >= 300 else _BAD,
            f"Averaging {avg:,} visible words a page." if avg else
            "Pages are very light on text.",
            f"{avg:,}" if avg is not None else "—"),
        chk("Thin pages", _OK if not thin else _WARN,
            "No page is too thin to rank." if not thin else
            f"{_num(thin, 'page')} {_verb(thin, 'has', 'have')} too little "
            "content to rank on its own. "
            "Either expand it or fold it into a stronger page."),
    ]
    rows = [[r.get("url", ""), r.get("visible_word_count", ""),
             "Yes" if r.get("has_enough_content") else "No"]
            for r in _ds(sec, "contentamounts") if isinstance(r, dict)]
    return section("amount_of_content", "Amount of content", True,
                   f"{words:,} visible words across {tested or 0} pages tested "
                   f"({found or 0} pages found)." if words else
                   "There isn't much written content on this site.",
                   checks,
                   [stat("Visible words", words), stat("Average per page", avg),
                    stat("Thin pages", thin, _OK if not thin else _WARN)],
                   [table("Pages checked",
                          ["Page", "Visible words", "Enough content?"], rows)])


def s_reading(p: dict) -> dict:
    sec = _sec(p, "reading_ease")
    if not sec or _b(sec, "reading_ease_check_success") is False:
        return _missing("reading_ease", "Readability")
    ease = _i(sec, "reading_ease")
    age = _i(sec, "reading_age")
    grade = _i(sec, "reading_grade")
    band = _OK if (ease or 0) >= 60 else (_WARN if (ease or 0) >= 40 else _BAD)
    rows = [[r.get("page", ""), r.get("reading_ease", ""), r.get("reading_age", "")]
            for r in _ds(sec, "readingEaseData") if isinstance(r, dict)]
    return section("reading_ease", "Readability", True,
                   f"Reading ease scores {ease}/100 — about a {age}-year-old "
                   "reading level." if ease is not None else
                   "Readability couldn't be measured.",
                   [chk("Reading ease", band,
                        "Clear, easy to read. Good for customers and for AI "
                        "summarizing the page." if band == _OK else
                        ("A bit dense. Shorter sentences and plainer words lift "
                         "both conversion and AI comprehension." if band == _WARN
                         else "Hard to read. Long sentences and jargon lose "
                              "visitors and confuse AI summaries."),
                        f"{ease}/100" if ease is not None else "—")],
                   [stat("Reading ease", f"{ease}/100" if ease is not None else None,
                         band),
                    stat("Reading age", age), stat("US grade level", grade)],
                   [table("Page by page", ["Page", "Ease", "Reading age"], rows)])


def s_images(p: dict) -> dict:
    sec = _sec(p, "images")
    opt = _sec(p, "image_optimisation")
    if not sec and not opt:
        return _missing("images", "Images")
    count = _i(sec, "image_count")
    stretched = _i(sec, "stretched_image_count")
    nonweb = _i(sec, "non_web_friendly_count")
    sized = _i(sec, "percent_images_sized")
    heavy = _i(opt, "images_to_optimise_count")
    checks = []
    if count is not None:
        checks.append(chk("Images found", _INFO,
                          f"{count:,} images across the pages tested.",
                          f"{count:,}"))
    if stretched is not None:
        checks.append(chk("Correctly sized", _OK if not stretched else _WARN,
                          "No image is being stretched or squashed by the browser."
                          if not stretched else
                          f"{_num(stretched, 'image')} {_verb(stretched)} being "
                          "stretched or squashed by the browser — soft on screen "
                          "and heavier to download than needed."))
    if nonweb is not None:
        checks.append(chk("Web-friendly formats", _OK if not nonweb else _BAD,
                          "All images use web formats." if not nonweb else
                          f"{_num(nonweb, 'image')} {_verb(nonweb, 'uses', 'use')} "
                          "a format browsers have to work to display. Convert to WebP."))
    if heavy is not None:
        checks.append(chk("File sizes", _OK if not heavy else _BAD,
                          "Image weight is under control." if not heavy else
                          f"{_num(heavy, 'image')} {_verb(heavy)} bigger than "
                          "needed. "
                          "Image weight is the usual reason a page loads slowly — "
                          "the Hub's Image Optimizer fixes this in a batch."))
    if sized is not None:
        checks.append(chk("Width and height set",
                          _OK if sized >= 90 else _WARN,
                          f"{sized}% of images declare their dimensions. Images "
                          "without them make the page jump about while it loads.",
                          f"{sized}%"))
    rows = [[r.get("foundon", ""),
             (r.get("image") or {}).get("original", "")
             if isinstance(r.get("image"), dict) else r.get("image", "")]
            for r in _ds(opt, "imagesToOptimise") if isinstance(r, dict)]
    return section("images", "Images", True,
                   "Images are in good shape." if not (heavy or nonweb or stretched)
                   else "Some images need attention.",
                   checks,
                   [stat("Images", count), stat("To optimize", heavy,
                                                _OK if not heavy else _BAD)],
                   [table("Images to optimize", ["Found on", "Image"], rows)])


def s_blog(p: dict) -> dict:
    sec = _sec(p, "blog")
    if not sec:
        return _missing("blog", "Blog")
    has = _b(sec, "has_blog")
    n = _i(sec, "blog_post_count")
    rows = [[r.get("blog_page", "")] for r in _ds(sec, "detected_blog_pages")
            if isinstance(r, dict)]
    return section("blog", "Blog", True,
                   f"{_num(n, 'blog post')} found." if has else
                   "No blog found on this site.",
                   [chk("Blog", _OK if has and (n or 0) >= 10 else
                        (_WARN if has else _BAD),
                        f"{_num(n, 'post')} published." if has else
                        "No blog. A blog is how a site earns rankings for the "
                        "questions customers actually type.")],
                   [stat("Posts", n)],
                   [table("Posts found", ["Post"], rows, limit=15)])


def s_video(p: dict) -> dict:
    sec = _sec(p, "video")
    if not sec:
        return _missing("video", "Video")
    has = _b(sec, "has_video")
    vendor = _s(sec, "vendor")
    return section("video", "Video", True,
                   f"Video is embedded ({vendor})." if has else
                   "No video found on the site.",
                   [chk("Video on site", _OK if has else _WARN,
                        f"Video hosted by {vendor}." if has else
                        "No video. Video keeps visitors on the page longer, which "
                        "Google reads as a quality signal.")])


def s_contact(p: dict) -> dict:
    sec = _sec(p, "click_to_contact")
    chat = _sec(p, "live_chat")
    book = _sec(p, "booking_widget")
    if not (sec or chat or book):
        return _missing("click_to_contact", "Getting in touch")
    tel = _i(sec, "tel_links_found_count")
    email = _i(sec, "email_links_found_count")
    checks = []
    if tel is not None:
        checks.append(chk("Tap-to-call", _OK if tel else _BAD,
                          f"{_num(tel, 'tap-to-call link')} found." if tel else
                          "No tap-to-call links. On a phone a number that isn't a "
                          "link is a number that doesn't get dialled.",
                          f"{tel}"))
    if email is not None:
        checks.append(chk("Email links", _OK if email else _WARN,
                          f"{_num(email, 'email link')} found." if email else
                          "No clickable email links."))
    lc = _b(chat, "has_live_chat")
    if lc is not None:
        checks.append(chk("Live chat", _OK if lc else _WARN,
                          "Live chat is installed." if lc else
                          "No live chat. It's the cheapest way to catch the "
                          "visitors who won't fill in a form."))
    bw = _b(book, "has_booking_widget")
    if bw is not None:
        checks.append(chk("Online booking", _OK if bw else _INFO,
                          "Online booking is available." if bw else
                          "No online booking widget — worth it if this business "
                          "takes appointments."))
    return section("click_to_contact", "Getting in touch", True,
                   "Visitors have clear ways to make contact."
                   if tel else "Contact routes need work.",
                   checks)


def s_pages_found(p: dict) -> dict:
    sec = _sec(p, "pages_found")
    pc = _sec(p, "page_count")
    if not (sec or pc):
        return _missing("pages_found", "Site structure")
    links = _i(sec, "internal_links_count")
    enough = _b(sec, "has_enough_internal_links")
    pages = _i(pc, "pages_discovered_count")
    return section("pages_found", "Site structure", True,
                   f"{_num(pages, 'page')} discovered, with {links or 0} internal "
                   "links between them." if pages else
                   "Site structure couldn't be mapped.",
                   [chk("Pages found", _OK if (pages or 0) >= 10 else _WARN,
                        f"{_num(pages, 'page')} on the site." if pages else
                        "Very few pages. Each page is a chance to rank.",
                        f"{pages:,}" if pages is not None else "—"),
                    chk("Internal linking", _OK if enough else _WARN,
                        "Pages link to each other well, which spreads ranking "
                        "strength around the site." if enough else
                        "Thin internal linking. Pages that nothing links to "
                        "rarely rank.")],
                   [stat("Pages", pages), stat("Internal links", links)])


def s_ecommerce(p: dict) -> dict:
    sec = _sec(p, "ecommerce")
    if not sec:
        return _missing("ecommerce", "Ecommerce")
    has = _b(sec, "has_ecommerce")
    name = _s(sec, "ecommerce_name")
    return section("ecommerce", "Ecommerce", True,
                   f"Ecommerce detected ({name})." if has else
                   "No ecommerce detected.",
                   [chk("Online selling", _INFO,
                        f"Running {name}." if has else
                        "Nothing on this site sells directly.")])


def s_colour(p: dict) -> dict:
    sec = _sec(p, "colour_scheme")
    if not sec:
        return _missing("colour_scheme", "Brand colors")
    rows = [[k.replace("_", " ").title(), v] for k, v in sec.items()
            if isinstance(v, str) and v.startswith("#")]
    return section("colour_scheme", "Brand colors", True,
                   "The site's palette was read from the live pages.",
                   [chk("Palette detected", _INFO,
                        "Used by the Hub when it generates images, accordions and "
                        "landing pages so they match the client's site.")],
                   [], [table("Detected colors", ["Role", "Hex"], rows)])


# ==========================================================================
# Accessibility
# ==========================================================================

def s_alt_text(p: dict) -> dict:
    sec = _sec(p, "alternative_text")
    if not sec:
        return _missing("alternative_text", "Image alt text")
    pct = _i(sec, "percentage_has_alt")
    no_alt = _i(sec, "images_no_alt_count")
    empty = _i(sec, "number_empty_alt")
    issues = _i(sec, "alt_issues_count")
    band = _OK if (pct or 0) >= 95 else (_WARN if (pct or 0) >= 75 else _BAD)
    checks = [chk("Images have alt text", band,
                  f"{pct}% of images carry alt text." if pct is not None else
                  "Alt text coverage couldn't be measured.",
                  f"{pct}%" if pct is not None else "—")]
    if no_alt is not None:
        checks.append(chk("Missing alt attributes", _OK if not no_alt else _BAD,
                          "Every image has an alt attribute." if not no_alt else
                          f"{_num(no_alt, 'image')} {_verb(no_alt, 'has', 'have')} "
                          "no alt attribute at all. "
                          "Screen readers announce the file name instead."))
    if empty is not None:
        checks.append(chk("Empty alt text", _OK if not empty else _WARN,
                          "No image has an empty alt attribute." if not empty else
                          f"{_num(empty, 'image')} {_verb(empty, 'has', 'have')} "
                          "an empty alt attribute. "
                          "That's correct for decoration and wrong for anything "
                          "carrying meaning."))
    if issues is not None:
        checks.append(chk("Alt text quality", _OK if not issues else _WARN,
                          "No alt text problems flagged." if not issues else
                          f"{_num(issues, 'issue')} with the alt text that is "
                          "there — usually a file name used as a description."))
    return section("alternative_text", "Image alt text", True,
                   f"{pct}% of images have alt text." if pct is not None else
                   "Alt text wasn't measured.",
                   checks, [stat("Alt text coverage",
                                 f"{pct}%" if pct is not None else None, band)],
                   note="Alt text is a legal accessibility requirement in most "
                        "markets, and it's also how image search and AI read a "
                        "picture. The Hub's SEO Image Pipeline writes it.")


def s_contrast(p: dict) -> dict:
    sec = _sec(p, "colour_scheme")
    if not sec:
        return _missing("colour_scheme", "Color contrast")
    from hub.contrast import contrast_ratio

    pairs = [("Body text on background", _s(sec, "primary_text_colour"),
              _s(sec, "primary_background_colour")),
             ("Secondary text on secondary background",
              _s(sec, "secondary_text_colour"),
              _s(sec, "secondary_background_colour"))]
    checks, rows = [], []
    for label, fg, bg in pairs:
        ratio = contrast_ratio(fg, bg)
        if ratio is None:
            continue
        state = _OK if ratio >= 4.5 else (_WARN if ratio >= 3 else _BAD)
        checks.append(chk(label, state,
                          f"Contrast ratio {ratio:.1f}:1. WCAG AA asks for 4.5:1 "
                          "on body text." + ("" if state == _OK else
                          " Below that, some visitors can't read it — and it's the "
                          "single most common accessibility complaint."),
                          f"{ratio:.1f}:1"))
        rows.append([label, fg, bg, f"{ratio:.1f}:1",
                     "Pass" if ratio >= 4.5 else "Fail"])
    if not checks:
        return _missing("colour_scheme", "Color contrast",
                        "The site's colours couldn't be read on this scan.")
    return section("colour_contrast", "Color contrast", True,
                   "Text contrast meets WCAG AA."
                   if all(c["state"] == _OK for c in checks) else
                   "Some text doesn't have enough contrast against its background.",
                   checks, [], [table("Contrast measured",
                                      ["Pair", "Text", "Background", "Ratio",
                                       "WCAG AA"], rows)],
                   "Measured from the palette detected on the live site. It covers "
                   "the main text colours, not every element.")


# ==========================================================================
# Compliance
# ==========================================================================

def s_gdpr(p: dict) -> dict:
    sec = _sec(p, "gdpr")
    if not sec:
        return _missing("gdpr", "Privacy & cookie compliance")
    checks = _auto_checks(sec)
    ssl = _b(sec, "ssl_detected")
    if ssl is not None:
        _add(checks, chk("HTTPS", _OK if ssl else _BAD,
                          "The site is served over HTTPS." if ssl else
                          "No valid HTTPS. Browsers mark the site as insecure and "
                          "Google will not rank it."))
    # The *_pass_fail booleans disagree with the *_found_percent numbers on
    # some payloads, so trust the percentages first and fall back to the flag.
    def _found(pct_key: str, flag_key: str):
        pct = _i(sec, pct_key)
        return (pct > 0) if pct is not None else _b(sec, flag_key)

    priv = _found("privacy_policy_found_percent", "privacy_policy_pass_fail")
    if priv is not None:
        _add(checks, chk("Privacy policy", _OK if priv else _BAD,
                         "A privacy policy is linked from the site." if priv else
                         "No privacy policy found. Required almost everywhere, "
                         "and required by Google and Meta to run ads."),
             "privacy policy")
    cook = _found("cookie_policy_found_percent", "cookie_policy_pass_fail")
    if cook is not None:
        _add(checks, chk("Cookie policy", _OK if cook else _BAD,
                         "A cookie policy is linked from the site." if cook else
                         "No cookie policy found."), "cookie policy")
    banner = _b(sec, "has_cookie_banner_on_homepage")
    if banner is None:
        banner = _found("cookie_banner_found_percent", "cookie_banner_pass_fail")
    before = _i(sec, "cookies_before_banner_count")
    track = _i(sec, "tracking_cookies_before_banner_count")
    if banner is not None:
        _add(checks, chk("Cookie banner", _OK if banner else _BAD,
                         "A consent banner is shown before tracking starts."
                         if banner else
                         "No cookie consent banner on the homepage."),
             "cookie banner")
    if track:
        checks.append(chk("Tracking before consent", _BAD,
                          f"{_num(track, 'tracking cookie')} "
                          f"{_verb(track)} set before the visitor consents to "
                          "anything. This is the finding that actually gets "
                          "businesses fined.",
                          f"{track}"))
    elif before is not None:
        checks.append(chk("Tracking before consent", _OK if not before else _WARN,
                          "Nothing is set before consent." if not before else
                          f"{_num(before, 'cookie')} {_verb(before)} set before "
                          "consent, though none of them are known trackers."))
    yt = _b(sec, "has_embedded_youtube_videos_with_cookies")
    if yt:
        checks.append(chk("YouTube embeds", _WARN,
                          "Embedded YouTube videos set cookies on load. Switching "
                          "the embeds to youtube-nocookie.com is a one-line fix."))
    return section("gdpr", "Privacy & cookie compliance", True,
                   "No privacy or cookie compliance issues found."
                   if not _b(sec, "has_potential_gdpr_compliance_issues")
                   and not track else
                   "This site has privacy and cookie compliance problems.",
                   checks,
                   [stat("Cookies before consent", before,
                         _OK if not before else _WARN),
                    stat("Known trackers before consent", track,
                         _OK if not track else _BAD)],
                   note="General guidance from an automated scan, not legal advice. "
                        "Rules differ by jurisdiction.")


def s_impressum(p: dict) -> dict:
    sec = _sec(p, "technology_profile")
    if "has_impressum" not in sec:
        return _missing("technology_profile", "Business identity disclosure")
    has = _b(sec, "has_impressum")
    return section("technology_profile", "Business identity disclosure", True,
                   "A business identity page (impressum) was found." if has else
                   "No business identity / impressum page found.",
                   [chk("Impressum", _OK if has else _INFO,
                        "Legal identity details are published." if has else
                        "Required in Germany, Austria and Switzerland; good "
                        "practice everywhere. Publish company name, address and "
                        "contact details on one page.")])


def s_consent_mode(p: dict) -> dict:
    ga = _sec(p, "google_ads_readiness")
    an = _sec(p, "analytics")
    if not (ga or an):
        return _missing("google_ads_readiness", "Consent mode")
    v2 = _b(ga, "uses_consent_mode_v2")
    basic = _b(ga, "uses_consent_mode") or _b(an, "uses_ga_consent_mode")
    adv = _b(ga, "uses_consent_mode_advanced") or _b(an, "uses_ga_consent_mode_advanced")
    checks = []
    if basic is not None:
        checks.append(chk("Google Consent Mode", _OK if basic else _BAD,
                          "Consent Mode is implemented." if basic else
                          "No Consent Mode. Google requires it for ads and "
                          "measurement in the EEA and UK."))
    if v2 is not None:
        checks.append(chk("Consent Mode v2", _OK if v2 else _BAD,
                          "Running v2, which is what Google now requires."
                          if v2 else
                          "Consent Mode v2 is not in place. Without it, Google "
                          "stops collecting remarketing and conversion data from "
                          "EEA and UK visitors."))
    if adv is not None:
        checks.append(chk("Advanced consent mode", _OK if adv else _INFO,
                          "Advanced mode is on, so modeled conversions still "
                          "work when a visitor declines." if adv else
                          "Basic mode only — declined visitors disappear from "
                          "reporting entirely."))
    return section("consent_mode", "Consent mode", True,
                   "Consent handling is configured correctly." if v2 else
                   "Consent handling is incomplete for Google's current rules.",
                   checks)


# ==========================================================================
# Ads sections
# ==========================================================================

def s_google_ads_ready(p: dict) -> dict:
    sec = _sec(p, "google_ads_readiness")
    if not sec:
        return _missing("google_ads_readiness", "Google Ads readiness")
    checks = _auto_checks(sec)
    ready = _b(sec, "is_google_ads_ready")
    for key, label, good, bad in (
        ("has_google_tag", "Google tag installed",
         "The Google tag is on the site, so conversions can be measured.",
         "No Google tag. Ads can run, but nothing can be measured — which means "
         "nothing can be optimized."),
        ("has_google_analytics", "Google Analytics",
         "Analytics is installed.",
         "No Google Analytics. There's no way to see what the ads did."),
        ("uses_consent_mode_v2", "Consent Mode v2",
         "Consent Mode v2 is configured.",
         "Consent Mode v2 missing — required by Google for ads in the EEA and UK, "
         "and it costs conversion data everywhere."),
    ):
        v = _b(sec, key)
        if v is None:
            continue
        _add(checks, chk(label, _OK if v else _BAD, good if v else bad), label)
    rows = [[r.get("type", ""), r.get("default", "") or "—",
             r.get("after_consent", "") or "—"]
            for r in _ds(sec, "googleTagConsentState") if isinstance(r, dict)]
    return section("google_ads_readiness", "Google Ads readiness", True,
                   "This site is ready to run and measure Google Ads." if ready
                   else "This site is not fully ready to run Google Ads.",
                   checks, [], [table("Consent state by storage type",
                                      ["Storage", "Default", "After consent"], rows)])


def s_paid_search(p: dict) -> dict:
    sec = _sec(p, "paid_search")
    if not sec:
        return _missing("paid_search", "Paid search activity")
    spend = _b(sec, "has_adwords_spend")
    kws = _list(sec, "adwords_keywords")
    rows = [[k] if isinstance(k, str) else [str(k)] for k in kws]
    return section("paid_search", "Paid search activity", True,
                   f"Paid search detected on {_num(len(kws), 'keyword')}."
                   if spend or kws else
                   "No paid search activity detected for this business.",
                   [chk("Running Google Ads", _INFO if spend else _WARN,
                        "This business is bidding on search terms." if spend else
                        "Nothing running on search. Competitors bidding on this "
                        "business's own brand name will take the click.")],
                   [stat("Paid keywords", len(kws) if kws else 0)],
                   [table("Keywords being bid on", ["Keyword"], rows)])


def s_facebook_ads(p: dict) -> dict:
    sec = _sec(p, "facebook_ads")
    if not sec or _b(sec, "fb_ads_error"):
        return _missing("facebook_ads", "Meta (Facebook) ads")
    total = _i(sec, "fb_ads_total")
    active = _i(sec, "fb_ads_currently_active")
    url = _s(sec, "fb_ad_library_url")
    return section("facebook_ads", "Meta (Facebook) ads", True,
                   f"{_num(active, 'ad')} currently running on Meta "
                   f"({_num(total, 'ad')} in the library)." if total else
                   "No Meta ads found for this business.",
                   [chk("Meta ads running", _INFO if active else _WARN,
                        f"{_num(active, 'active ad')} in the Meta Ad Library."
                        if active else
                        "Nothing running on Facebook or Instagram. The Ad Library "
                        "is public, so competitors' activity is visible too.",
                        f"{active}" if active is not None else "—", url)],
                   [stat("Active ads", active), stat("Total ads seen", total)])


def s_display_ads(p: dict) -> dict:
    sec = _sec(p, "display_ads")
    if not sec:
        return _missing("display_ads", "Display advertising")
    uses = _b(sec, "uses_display_ads")
    return section("display_ads", "Display advertising", True,
                   "Display advertising detected." if uses else
                   "No display advertising detected.",
                   [chk("Display ads", _INFO if uses else _WARN,
                        "This business runs display advertising." if uses else
                        "No display activity found in Google's Ads Transparency "
                        "Centre.", "", _s(sec, "ad_transparency_centre_url"))])


def s_retargeting(p: dict) -> dict:
    sec = _sec(p, "retargeting")
    if not sec:
        return _missing("retargeting", "Retargeting pixels")
    g = _b(sec, "has_google_pixel")
    f = _b(sec, "has_facebook_pixel")
    tech = _b(sec, "has_ads_technology")
    checks = []
    if g is not None:
        checks.append(chk("Google remarketing tag", _OK if g else _BAD,
                          "The Google remarketing tag is firing." if g else
                          "No Google remarketing tag. Every visitor who leaves "
                          "without converting is gone for good."))
    if f is not None:
        checks.append(chk("Meta pixel", _OK if f else _BAD,
                          "The Meta pixel is installed." if f else
                          "No Meta pixel. Without it there's no retargeting "
                          "audience and no conversion tracking on Meta."))
    if tech is not None:
        checks.append(chk("Any ad technology", _OK if tech else _WARN,
                          "Advertising technology is present on the site."
                          if tech else
                          "No advertising technology at all on this site."))
    return section("retargeting", "Retargeting pixels", True,
                   "Retargeting is set up." if (g or f) else
                   "No retargeting pixels are installed — the cheapest audience "
                   "this business has is being thrown away.",
                   checks)


def s_analytics(p: dict) -> dict:
    sec = _sec(p, "analytics")
    if not sec:
        return _missing("analytics", "Analytics")
    has = _b(sec, "has_analytics")
    tool = _s(sec, "analytics_tool")
    tools = _list(sec, "all_analytics_tools")
    ua = _b(sec, "uses_universal_ga")
    checks = [chk("Analytics installed", _OK if has else _BAD,
                  f"Running {tool or 'analytics'}." if has else
                  "No analytics at all. Nothing on this site can be measured.")]
    if ua:
        checks.append(chk("Universal Analytics", _BAD,
                          "Universal Analytics is still on the page. It stopped "
                          "collecting data — this is dead code reporting nothing."))
    if len(tools) > 1:
        checks.append(chk("Multiple tools", _WARN,
                          "More than one analytics tool is installed: "
                          + ", ".join(str(t) for t in tools)
                          + ". Duplicate tracking usually means duplicate "
                            "conversions."))
    return section("analytics", "Analytics", True,
                   f"Analytics is installed ({tool})." if has else
                   "No analytics installed.",
                   checks, [stat("Tools found", len(tools) if tools else 0)])


def s_local_pack(p: dict) -> dict:
    sec = _sec(p, "local_pack")
    if not sec:
        return _missing("local_pack", "Local pack (map results)")
    if not _b(sec, "location_was_provided"):
        return section("local_pack", "Local pack (map results)", True,
                       "No location was supplied, so map rankings couldn't be "
                       "checked.",
                       [chk("Local pack", _INFO,
                            "Add the client's city and target keywords to the "
                            "record, then re-run the scan.")])
    appears = _b(sec, "appears_in_local_pack")
    rows = [[r.get("term", ""), r.get("position") or "Not ranking"]
            for r in _list(sec, "local_keywords_detail") if isinstance(r, dict)]
    return section("local_pack", "Local pack (map results)", True,
                   "This business appears in the Google map pack." if appears else
                   "This business does not appear in the Google map pack for its "
                   "target terms.",
                   [chk("Map pack presence", _OK if appears else _BAD,
                        "Showing in the three-result map block." if appears else
                        "Not in the map block. That block takes most of the "
                        "clicks on a local search, and it's won through the "
                        "Google Business Profile, not the website.")],
                   [], [table("Target terms", ["Search term", "Position"], rows)])


def s_local_grid(p: dict) -> dict:
    sec = _sec(p, "local_grid")
    if not sec or not _b(sec, "ran_local_grid_test"):
        return _missing("local_grid", "Local ranking grid")
    found = _i(sec, "spots_found_in")
    missing = _i(sec, "spots_missing_from")
    total = (found or 0) + (missing or 0)
    metrics = sec.get("keyword_metrics")
    rows = []
    if isinstance(metrics, dict):
        for kw, m in metrics.items():
            if isinstance(m, dict):
                rows.append([kw.replace("_", " "), m.get("top_positions", 0),
                             m.get("average_position", 0),
                             f"{m.get('coverage', 0)}%",
                             m.get("competitiveness", "")])
    return section("local_grid", "Local ranking grid", True,
                   f"Visible in {found} of {total} map points checked."
                   if total else "The local grid was run for this business.",
                   [chk("Grid visibility",
                        _OK if total and (found or 0) / total > 0.5 else _BAD,
                        f"Found in {found} of {total} points around the business."
                        if total else "Grid coverage couldn't be measured.",
                        f"{found}/{total}" if total else "—")],
                   [stat("Points visible", f"{found}/{total}" if total else None,
                         _OK if total and (found or 0) / total > 0.5 else _BAD)],
                   [table("By keyword",
                          ["Keyword", "Top-3 spots", "Average position",
                           "Coverage", "Competition"], rows)],
                   "The grid checks rankings from a spread of points around the "
                   "business address, not just the centre.")


# ==========================================================================
# Social sections
# ==========================================================================

def s_gbp(p: dict) -> dict:
    sec = _sec(p, "google_business_profile")
    if not sec:
        return _missing("google_business_profile", "Google Business Profile")
    checks = _auto_checks(sec)
    for key, label, good, bad in (
        ("is_listing_found", "Profile exists",
         "A Google Business Profile was found.",
         "No Google Business Profile. For a local business this is the single "
         "highest-value thing on this list."),
        ("is_listing_claimed", "Claimed",
         "The profile is claimed and under the business's control.",
         "The profile isn't claimed — anyone can suggest edits to it."),
        ("is_listing_complete", "Profile complete",
         "Every key field is filled in.",
         "The profile is incomplete. Complete profiles get more calls, and Gemini "
         "and voice assistants read this data directly."),
        ("has_opening_hours", "Hours published",
         "Opening hours are published.",
         "No opening hours. 'Open now' searches skip businesses without them."),
        ("has_photos", "Photos on the profile",
         "Photos are on the profile.",
         "No photos. Profiles with photos get materially more clicks."),
        ("has_reviews", "Has reviews",
         "The profile has reviews.",
         "No reviews on the profile. Reviews drive both ranking and conversion."),
    ):
        v = _b(sec, key)
        if v is None:
            continue
        _add(checks, chk(label, _OK if v else _BAD, good if v else bad),
             *{"Claimed": ("claimed",), "Hours published": ("opening hours",),
               "Photos on the profile": ("photos",),
               "Has reviews": ("reviews on the profile",),
               }.get(label, (label,)))
    rating = _f(sec, "review_rating")
    count = _i(sec, "review_count")
    return section("google_business_profile", "Google Business Profile", True,
                   "The Google Business Profile is complete."
                   if _b(sec, "is_listing_complete") else
                   ("The Google Business Profile needs work." if
                    _b(sec, "is_listing_found") else
                    "No Google Business Profile found."),
                   checks,
                   [stat("Listing name", _s(sec, "listing_name") or None),
                    stat("Reviews", count, _OK if count else _BAD),
                    stat("Rating", f"{rating:.1f}" if rating else None,
                         _OK if (rating or 0) >= 4 else _BAD)],
                   note="Listing: " + (_s(sec, "listing_url") or "not found"))


def s_facebook_page(p: dict) -> dict:
    sec = _sec(p, "facebook_page")
    if not sec or _b(sec, "error"):
        return _missing("facebook_page", "Facebook page")
    found = _b(sec, "found")
    likes = _i(sec, "page_likes")
    days = _i(sec, "days_since_last_post")
    verified = _b(sec, "verified")
    checks = [chk("Facebook page", _OK if found else _BAD,
                  f"Page found: {_s(sec, 'page_name')}." if found else
                  "No Facebook page found for this business.")]
    if days is not None:
        band = _OK if days <= 14 else (_WARN if days <= 60 else _BAD)
        checks.append(chk("Posting recently", band,
                          f"Last post was {days} days ago."
                          + ("" if band == _OK else
                             " A page that hasn't posted in months reads as a "
                             "closed business."),
                          f"{days} days"))
    if verified is not None:
        checks.append(chk("Verified", _OK if verified else _INFO,
                          "The page is verified." if verified else
                          "Not verified. Worth doing — it's free and it shows."))
    if _b(sec, "fb_link_missing"):
        checks.append(chk("Linked from the website", _BAD,
                          "The website doesn't link to the Facebook page."))
    return section("facebook_page", "Facebook page", True,
                   f"{likes:,} likes." if likes else
                   ("Facebook page found." if found else "No Facebook page found."),
                   checks, [stat("Likes", likes),
                            stat("Followers", _i(sec, "page_follows")),
                            stat("Days since last post", days)],
                   note=_s(sec, "page_link"))


def _social_simple(p: dict, ns: str, title: str, flag: str, link_key: str = "",
                   unconfirmed: str = "") -> dict:
    sec = _sec(p, ns)
    if not sec:
        return _missing(ns, title)
    has = _b(sec, flag)
    unc = _b(sec, unconfirmed) if unconfirmed else None
    link = _s(sec, link_key) if link_key else ""
    if has:
        state, detail = _OK, f"{title} found."
    elif unc:
        state, detail = (_WARN,
                         f"A possible {title} was found but couldn't be "
                         "confirmed as this business's. Worth checking the link "
                         "on the website points at the right profile.")
    else:
        state, detail = _WARN, f"No {title} found for this business."
    return section(ns, title, True, detail,
                   [chk(title, state, detail, "", link)],
                   note=link)


def s_instagram(p): return _social_simple(p, "instagram_account", "Instagram", "has_instagram", "instagram_link")
def s_linkedin(p):  return _social_simple(p, "linkedin", "LinkedIn", "has_linkedin", "linkedin_profile_url", "has_unconfirmed_linkedin")
def s_youtube(p):   return _social_simple(p, "youtube", "YouTube", "has_youtube", "youtube_link", "has_unconfirmed_youtube")
def s_tiktok(p):    return _social_simple(p, "tiktok_account", "TikTok", "has_tiktok")
def s_pinterest(p): return _social_simple(p, "pinterest", "Pinterest", "has_pinterest", "", "has_unconfirmed_pinterest")
def s_snapchat(p):  return _social_simple(p, "snapchat", "Snapchat", "has_snapchat")
def s_twitter(p):   return _social_simple(p, "x_(formerly_twitter)", "X (Twitter)", "found", "account_link")


def s_reviews(p: dict) -> dict:
    sec = _sec(p, "reviews") or _sec(p, "reviews_normalised")
    integ = _sec(p, "review_integration")
    if not sec:
        return _missing("reviews", "Reviews")
    count = _i(sec, "reviews_found_count")
    avg = _f(sec, "average_review")
    on_site = _b(integ, "has_reviews_integration")
    checks = [chk("Reviews found", _OK if count else _BAD,
                  f"{_num(count, 'review')} found across directories." if count
                  else "No reviews found anywhere. Reviews are the strongest "
                       "local ranking signal there is, and the first thing a "
                       "customer looks at.",
                  f"{count:,}" if count is not None else "—")]
    if avg is not None:
        checks.append(chk("Average rating",
                          _OK if avg >= 4 else (_WARN if avg >= 3 else _BAD),
                          f"Average rating {avg:.1f}." if avg else
                          "No rating data to average.",
                          f"{avg:.1f}" if avg else "—"))
    if on_site is not None:
        checks.append(chk("Reviews shown on the site", _OK if on_site else _WARN,
                          "Reviews are displayed on the website." if on_site else
                          "Reviews aren't shown on the website. Pulling them onto "
                          "the site lifts conversion and adds Review schema."))
    rows = [[r.get("directory_image", ""), r.get("directory_name", ""),
             r.get("directory_rating", "")]
            for r in _ds(sec, "DirektoriReviewsOverview") if isinstance(r, dict)]
    return section("reviews", "Reviews", True,
                   f"{_num(count, 'review')}, averaging {avg:.1f}."
                   if count and avg else
                   (f"{_num(count, 'review')} found." if count else
                    "No reviews found."),
                   checks, [stat("Reviews", count), stat("Average", 
                                 f"{avg:.1f}" if avg else None)],
                   [table("By directory", ["Directory", "Profile", "Rating"], rows)])


# ==========================================================================
# Speed & technical
# ==========================================================================

def s_speed(p: dict) -> dict:
    sp = site_health.speed(p)
    if not sp.get("available"):
        return section("speed", "Page speed", False,
                       sp.get("headline") or "Speed wasn't measured on this scan.",
                       note="Lighthouse speed data isn't part of every Insites "
                            "plan. If it should be here, check the plan covers "
                            "website_speed / web_vitals and re-run the scan.")
    checks = [chk(m["label"], m["band"] or _INFO, m["note"], m["value"])
              for m in sp["metrics"] if m["measured"]]
    checks += [chk(n["label"], n["band"] or _INFO, n["note"], n["value"])
               for n in sp["notes"]]
    stats = [stat("Performance score",
                  f"{sp['score']}/100" if sp["score"] is not None else None,
                  sp["severity"]),
             stat("Main content painted", sp["lcp"])]
    if sp.get("vitals_pass") is not None:
        stats.append(stat("Core Web Vitals",
                          "Pass" if sp["vitals_pass"] else "Fail",
                          _OK if sp["vitals_pass"] else _BAD))
    return section("speed", "Page speed", True, sp["headline"], checks, stats,
                   severity=sp["severity"],
                   note=f"Measured on {sp['device']}." if sp.get("device") else "")


def s_fixes(p: dict) -> dict:
    fx = site_health.fixes(p)
    if not fx.get("measured"):
        return section("fixes", "Broken links & image problems", False,
                       "This audit didn't return link or image checks.")
    items = fx.get("items") or []
    checks = [chk(i["label"], i["severity"] if i["measured"] else _INFO,
                  i["action"], str(i["count"]) if i["measured"] else "—")
              for i in items]
    total = fx.get("total")
    return section("fixes", "Broken links & image problems", True,
                   "Nothing broken — no broken links and no image problems."
                   if not total else
                   f"{_num(total, 'thing')} to fix across links and images.",
                   checks, [stat("Total to fix", total,
                                 fx.get("worst") or "")],
                   note="The audit counts these but doesn't name the URLs. "
                        "Run “Find the URLs” on the scan or the client "
                        "record to get the addresses and a CSV.")


def s_page_errors(p: dict) -> dict:
    sec = _sec(p, "page_errors")
    if not sec:
        return _missing("page_errors", "Page errors")
    has = _b(sec, "has_errors")
    return section("page_errors", "Page errors", True,
                   "No page errors found." if not has else
                   "Pages on this site are returning errors.",
                   [chk("Server errors", _OK if not has else _BAD,
                        "Every page tested returned cleanly." if not has else
                        "Some pages return errors. Broken pages waste crawl "
                        "budget and lose customers.")])


def s_tech(p: dict) -> dict:
    sec = _sec(p, "technology_detection")
    if not sec:
        return _missing("technology_detection", "Technology")
    detected = _s(sec, "detected_technologies")
    cats = _list(sec, "technology_categories")
    rows = [[c] for c in cats if isinstance(c, str)]
    return section("technology_detection", "Technology", True,
                   "The stack this site is built on was detected.",
                   [chk("Detected", _INFO, detected[:600] or "Nothing detected.")],
                   [], [table("Categories", ["Category"], rows, limit=30)])


def s_spider(p: dict) -> dict:
    sec = _sec(p, "spider")
    if not sec:
        return _missing("spider", "Crawlability")
    err = _b(sec, "error_downloading_pages")
    msg = _s(sec, "error_message")
    return section("spider", "Crawlability", True,
                   "The crawler read the site without trouble." if not err else
                   "The crawler had trouble reading this site.",
                   [chk("Crawl", _OK if not err else _BAD,
                        "Pages downloaded cleanly." if not err else
                        (msg or "Pages failed to download. Usually bot protection "
                                "or an overloaded host — and Google's crawler "
                                "hits the same wall."))])


# ==========================================================================
# The catalogue
# ==========================================================================

BUILDERS: dict[str, Callable[[dict], dict]] = {
    "ai_readiness": s_ai_readiness, "voice_search": s_voice_search,
    "chatgpt": s_chatgpt, "perplexity": s_perplexity, "gemini": s_gemini,
    "gemini_optimisation": s_gemini_opt, "eeat": s_eeat,
    "google_indexability": s_google_index, "bing_indexability": s_bing_index,
    "sitemap": s_sitemap, "structured_data": s_structured_data,
    "page_titles_and_descriptions": s_titles, "faq": s_faq,
    "backlinks": s_backlinks, "organic_search": s_organic,
    "indexing_errors": s_indexing_errors, "last_updated": s_last_updated,
    "amount_of_content": s_content, "reading_ease": s_reading,
    "images": s_images, "blog": s_blog, "video": s_video,
    "click_to_contact": s_contact, "pages_found": s_pages_found,
    "ecommerce": s_ecommerce, "colour_scheme": s_colour,
    "alternative_text": s_alt_text, "colour_contrast": s_contrast,
    "gdpr": s_gdpr, "technology_profile": s_impressum,
    "consent_mode": s_consent_mode,
    "google_ads_readiness": s_google_ads_ready, "paid_search": s_paid_search,
    "facebook_ads": s_facebook_ads, "display_ads": s_display_ads,
    "retargeting": s_retargeting, "analytics": s_analytics,
    "local_pack": s_local_pack, "local_grid": s_local_grid,
    "google_business_profile": s_gbp, "facebook_page": s_facebook_page,
    "instagram_account": s_instagram, "linkedin": s_linkedin,
    "youtube": s_youtube, "tiktok_account": s_tiktok,
    "pinterest": s_pinterest, "snapchat": s_snapchat,
    "x_(formerly_twitter)": s_twitter, "reviews": s_reviews,
    "speed": s_speed, "fixes": s_fixes, "page_errors": s_page_errors,
    "technology_detection": s_tech, "spider": s_spider,
}

REPORTS: list[dict] = [
    {
        "key": "seo_aeo",
        "title": "SEO & AEO Audit",
        "blurb": "How findable this business is in Google — and now in ChatGPT, "
                 "Gemini and Perplexity, where the answer is written for the "
                 "customer instead of a list of links.",
        "sections": ["ai_readiness", "gemini_optimisation", "chatgpt",
                     "perplexity", "gemini", "voice_search", "eeat",
                     "google_indexability", "bing_indexability", "sitemap",
                     "structured_data", "faq", "page_titles_and_descriptions",
                     "backlinks", "organic_search", "last_updated",
                     "indexing_errors"],
    },
    {
        "key": "content_ux",
        "title": "Content & UX Audit",
        "blurb": "Whether there's enough on this site to rank, whether a real "
                 "person can read it, and whether they can act once they have.",
        "sections": ["amount_of_content", "reading_ease", "pages_found",
                     "images", "blog", "faq", "video", "click_to_contact",
                     "ecommerce", "colour_scheme"],
    },
    {
        "key": "ads",
        "title": "Ads Audit",
        "blurb": "What this business is running, what it's measuring, and what "
                 "it's paying for twice. Nothing here needs access to the ad "
                 "accounts.",
        "sections": ["google_ads_readiness", "analytics", "retargeting",
                     "paid_search", "facebook_ads", "display_ads",
                     "local_pack"],
    },
    {
        "key": "social",
        "title": "Social Media Audit",
        "blurb": "Every profile this business has, every one it's missing, and "
                 "the Google Business Profile that matters more than all of them.",
        "sections": ["google_business_profile", "reviews", "facebook_page",
                     "instagram_account", "linkedin", "youtube",
                     "x_(formerly_twitter)", "tiktok_account", "pinterest",
                     "snapchat", "local_grid"],
    },
    {
        "key": "speed",
        "title": "Speed & Performance",
        "blurb": "How fast the site is, what's slowing it down, and what's "
                 "broken on it.",
        "sections": ["speed", "fixes", "images", "page_errors", "spider",
                     "technology_detection"],
    },
    {
        "key": "accessibility",
        "title": "Accessibility",
        "blurb": "The checks an automated scan can make against WCAG — alt text, "
                 "contrast, and whether the page can be used at all without a "
                 "mouse or a screen.",
        "sections": ["alternative_text", "colour_contrast", "images",
                     "reading_ease", "click_to_contact"],
    },
    {
        "key": "compliance",
        "title": "Compliance",
        "blurb": "Privacy policy, cookie consent, tracking before consent, and "
                 "the Google consent requirements that quietly switch ad "
                 "measurement off.",
        "sections": ["gdpr", "consent_mode", "technology_profile"],
    },
]

_COMPLETE_ORDER = ["seo_aeo", "content_ux", "speed", "accessibility",
                   "ads", "social", "compliance"]

REPORTS.append({
    "key": "complete",
    "title": "Complete Audit",
    "blurb": "Everything above in one document — the full picture of this "
             "business's digital presence.",
    "sections": [],          # assembled from the other reports, in order
})

REPORT_INDEX = {r["key"]: r for r in REPORTS}


def _report_sections(key: str) -> list[str]:
    if key != "complete":
        return REPORT_INDEX.get(key, {}).get("sections", [])
    seen, out = set(), []
    for rk in _COMPLETE_ORDER:
        for s in REPORT_INDEX[rk]["sections"]:
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _priorities(sections: list[dict], limit: int = 8) -> list[dict]:
    """The failing checks worth leading with, worst first.

    Ordered by how much they move the needle rather than by where they appear
    in the report, so the first page of a client PDF says something useful.
    """
    weight = {
        "google_business_profile": 0, "reviews": 1, "gdpr": 2, "speed": 3,
        "fixes": 4, "structured_data": 5, "ai_readiness": 6,
        "retargeting": 7, "analytics": 8, "alternative_text": 9,
        "page_titles_and_descriptions": 10, "google_ads_readiness": 11,
    }
    items = []
    for s in sections:
        if not s["measured"]:
            continue
        for c in s["checks"]:
            if c["state"] != _BAD:
                continue
            items.append({"section": s["title"], "label": c["label"],
                          "detail": c["detail"],
                          "_w": weight.get(s["key"], 50)})
    items.sort(key=lambda i: i["_w"])
    for i in items:
        i.pop("_w", None)
    return items[:limit]


def _score(sections: list[dict]) -> Optional[int]:
    ok = sum(s["passed"] for s in sections if s["measured"])
    bad = sum(s["failed"] for s in sections if s["measured"])
    warn = sum(s["warned"] for s in sections if s["measured"])
    total = ok + bad + warn
    if not total:
        return None
    return int(round(100.0 * (ok + 0.5 * warn) / total))


def report(payload: dict, key: str) -> dict:
    """One finished report, ready to render or print."""
    if not isinstance(payload, dict):
        payload = {}
    meta = _sec(payload, "meta")
    spec = REPORT_INDEX.get(key)
    if spec is None:
        raise KeyError(key)

    sections = []
    for name in _report_sections(key):
        builder = BUILDERS.get(name)
        if builder is None:
            continue
        try:
            sections.append(builder(payload))
        except Exception as exc:                    # noqa: BLE001
            sections.append(section(name, name.replace("_", " ").title(), False,
                                    f"This section couldn't be read: "
                                    f"{type(exc).__name__}."))

    measured = [s for s in sections if s["measured"]]
    score = _score(sections)
    overall = get_field(payload, "overall_score")
    try:
        overall = int(round(float(overall))) if overall is not None else None
    except (TypeError, ValueError):
        overall = None

    worst = "unknown"
    for level in (_BAD, _WARN, _OK):
        if any(s["severity"] == level for s in measured):
            worst = level
            break

    passed = sum(s["passed"] for s in measured)
    failed = sum(s["failed"] for s in measured)
    warned = sum(s["warned"] for s in measured)

    if not measured:
        headline = ("This scan didn't return the data this report needs. "
                    "Re-run the audit, or check the plan covers these checks.")
    elif not failed and not warned:
        headline = "Everything checked here passed."
    elif not failed:
        headline = (f"{passed} of {passed + warned + failed} checks pass; "
                    f"{warned} need attention but nothing is broken.")
    else:
        headline = (f"{failed} problem{'' if failed == 1 else 's'} to fix, "
                    f"{warned} worth improving, {passed} already right.")

    return {
        "key": key,
        "title": spec["title"],
        "blurb": spec["blurb"],
        "domain": get_field(payload, "domain") or "",
        "business": (meta.get("detected_name")
                     or get_field(payload, "local_presence.detected_name") or ""),
        "address": meta.get("detected_address") or "",
        "phone": meta.get("detected_phone") or "",
        "industry": meta.get("primary_industry") or "",
        "scanned_at": meta.get("report_completed_at") or "",
        "pages_analyzed": get_field(payload, "analyzed_page_count"),
        "overall_score": overall,
        "tier": tier_for_score(overall),
        "score": score,
        "severity": worst,
        "headline": headline,
        "passed": passed, "failed": failed, "warned": warned,
        "sections": sections,
        "measured_sections": len(measured),
        "priorities": _priorities(sections),
        "screenshot": get_field(payload, "website_screenshot.desktop_screenshot_url") or "",
    }


def available(payload: dict) -> list[dict]:
    """The report menu with a live severity and count on each entry.

    Built by running every report, which is cheap (pure dict reads) and means
    the menu can never promise a report that turns out to be empty.
    """
    out = []
    for spec in REPORTS:
        try:
            r = report(payload, spec["key"])
        except Exception:                            # noqa: BLE001
            continue
        out.append({
            "key": spec["key"], "title": spec["title"], "blurb": spec["blurb"],
            "severity": r["severity"], "score": r["score"],
            "failed": r["failed"], "warned": r["warned"], "passed": r["passed"],
            "measured": bool(r["measured_sections"]),
        })
    return out


# ---------------------------------------------------------------------------
# Social profiles found by a scan
# ---------------------------------------------------------------------------

# (payload namespace, the key we use on a client record, the link field)
SOCIAL_LINK_FIELDS = (
    ("facebook_page",        "facebook",  "page_link"),
    ("instagram_account",    "instagram", "instagram_link"),
    ("linkedin",             "linkedin",  "linkedin_profile_url"),
    ("youtube",              "youtube",   "youtube_link"),
    ("x_(formerly_twitter)", "twitter",   "account_link"),
    ("tiktok_account",       "tiktok",    "tiktok_link"),
    ("pinterest",            "pinterest", "pinterest_link"),
)


def social_profiles(payload: dict) -> dict:
    """Social URLs an Insites scan actually found.

    Brandfetch returns whatever a brand publishes about itself, which is often
    two or three profiles. A site scan reads the client's own pages and
    frequently finds more — a TikTok in the footer, a LinkedIn nobody
    registered with Brandfetch. Both are worth having; neither is complete on
    its own.

    Only real URLs are returned. A section that reports "found" without a link
    is left out: a platform name with no address is a fact, not something the
    card can link to, and filling it with a guessed URL would be worse.
    """
    out: dict[str, str] = {}
    for ns, key, link_key in SOCIAL_LINK_FIELDS:
        sec = _sec(payload, ns)
        if not sec:
            continue
        url = _s(sec, link_key)
        if not url:
            # A few namespaces use a differently-named link field depending on
            # the plan; try the obvious alternatives before giving up.
            for alt in ("link", "url", "profile_url", "page_url"):
                url = _s(sec, alt)
                if url:
                    break
        url = (url or "").strip()
        if url.startswith("http"):
            out[key] = url
    return out
