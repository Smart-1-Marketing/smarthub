"""SEO section — clients with SEO products, schema scanning + generation.

Data sources:
  * private Knack products  -> live "Website SEO..." products + billing
  * private Knack websites  -> the client's site, platform, GA / GTM ids

Per-client working files live at /var/data/seo/<slug>.json and hold the
client-setup answers, business info, AI questions/answers, and every
generated + approved page schema, so work survives restarts and deploys.
"""
import html as _html
import json
import os
import re
import threading
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import requests

from . import dates
from . import jsonstore
from . import knack_data

_lock = threading.Lock()

UA = {"User-Agent": "Mozilla/5.0 (compatible; Smart1Hub-SEO/1.0; +https://smart1marketing.com)"}


# ------------------------------------------------------------------ storage
# Through hub.jsonstore rather than straight to the disk. These files are the
# only copy of a client's SEO setup answers and every approved page schema —
# months of work per client that exists nowhere else — and the Render disk they
# sat on is outside the database backup and does not survive being recreated.
def _store_base() -> str:
    return jsonstore.data_dir("seo")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80] or "client"


def load_store(client: str) -> dict:
    path = os.path.join(_store_base(), slugify(client) + ".json")
    return jsonstore.read_json(path, default={
        "client": client, "setup": {}, "business_info": {},
        "questions": [], "answers": {}, "pages": {}, "sitemap": []})


def save_store(client: str, data: dict):
    data["client"] = client
    path = os.path.join(_store_base(), slugify(client) + ".json")
    with _lock:
        jsonstore.write_json(path, data, indent=1)


# -------------------------------------------------- attached accounts
# A client can hold MULTIPLE attachments of each kind (two GA properties, two
# QuickBooks customers, several website records…), and the same resource can
# be attached to any number of clients. Hub-only — nothing is written to Knack.
LINK_KINDS = ("analytics", "gtm", "gsc", "gmb", "qb", "suite", "website")


def _link_key(item) -> str:
    if not isinstance(item, dict):
        return str(item)
    return str(item.get("resource_id") or item.get("id") or
               item.get("domain") or item.get("name") or "")


def get_links(client: str) -> dict:
    """{kind: [items]} — older single-dict saves are migrated to lists."""
    raw = load_store(client).get("attached", {})
    out = {}
    for kind, val in raw.items():
        if isinstance(val, list):
            out[kind] = val
        elif val:
            out[kind] = [val]
    return out


def set_link(client: str, kind: str, data, remove: str = "") -> dict:
    """Add one attachment (data), remove one (remove=<key>), or clear the
    kind (data=None, no remove)."""
    store = load_store(client)
    att = store.setdefault("attached", {})
    cur = att.get(kind)
    items = cur if isinstance(cur, list) else ([cur] if cur else [])
    if remove:
        items = [i for i in items if _link_key(i) != remove]
    elif data:
        key = _link_key(data)
        if not any(_link_key(i) == key for i in items):
            items.append(data)
    else:
        items = []
    if items:
        att[kind] = items
    else:
        att.pop(kind, None)
    save_store(client, store)
    return get_links(client)


# ------------------------------------------------------- status + socials
# The four status pills. Three of them are a tick somebody makes and are
# genuinely yes/no; `blogs` is derived, and it is the one with more than two
# answers.
#
# It was a bool, and `False` covered both "this client does not buy blogs" and
# "their plan is behind" -- so on this deployment's own book 16 of the 21 SEO
# clients carried a permanent red "Blogs -- not yet" for a product they have
# never bought, in the one column that says what to act on. The summary tile
# at the top of the same page counts the *product* and read "With blogs: 5",
# so the page contradicted itself: five clients have blogs, twenty-one are
# behind on them. Neither figure is wrong on its own, which is why it stood --
# the `/api/db/structure` versus `/api/integrity` trap, wearing a pill.
BLOGS_STATES = {
    "not_sold": "No blogs product",
    "none": "No plan yet",
    "behind": "Posts overdue",
    "current": "Up to date",
}


def _blogs_state(store: dict, sells: bool | None = None) -> str:
    """Which of BLOGS_STATES this client's blogs are in.

    `sells` is whether a live SEO product with "blog" in its name is on their
    book -- the fact `seo_clients()` and `_client_base()` each compute two
    functions away and never passed in.

    It is None where the caller could not look, and that is deliberately NOT
    read as "they do not buy blogs": `not_sold` is the state that takes a row
    out of the queue, and silencing a row on a guess is how a client who is
    genuinely behind stops being chased. Unknown owes a plan, like anybody
    else who has none -- a failed read never quiets the list.
    """
    posts = (store.get("blogs") or {}).get("posts") or []
    if not posts:
        return "not_sold" if sells is False else "none"
    today = _dt_date_today_iso()
    due = [p for p in posts if str(p.get("date", "")) <= today]
    if not due:
        return "current"                  # a plan exists and nothing is due yet
    return "current" if all(p.get("posted") for p in due) else "behind"


def _dt_date_today_iso() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


def client_status(store: dict, sells_blogs: bool | None = None) -> dict:
    """The four pills. `blogs` is a BLOGS_STATES key, not a bool -- see above.

    Every screen reads this one function rather than deciding for itself, so
    the client list and the client record cannot come to disagree about
    whether somebody is behind on blogs.
    """
    checks = store.get("checks", {})
    blogs = _blogs_state(store, sells_blogs)
    return {
        "setup": bool(store.get("setup", {}).get("completed")),
        "schema": bool(checks.get("schema")),
        "listings": bool(checks.get("listings")),
        "blogs": blogs,
        "blogs_label": BLOGS_STATES[blogs],
    }


_SOCIAL_KEYS = ("facebook", "instagram", "linkedin", "twitter", "youtube",
                "tiktok", "pinterest", "yelp", "gbp")


def _social_url(value: object) -> str:
    """Return a usable outbound social URL or reject unsafe schemes."""
    url = str(value or "").strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("https", "http") or not parts.hostname:
        raise ValueError("Social links must be complete http:// or https:// URLs.")
    return url


def get_social(client: str, domain: str = "") -> dict:
    """Social URLs for a client — saved values, seeded from Brandfetch."""
    store = load_store(client)
    # Older records and imported provider data predate URL validation. Filter
    # them on read as well, so a stale javascript: value can never become an
    # outbound link merely because it was saved before this validation existed.
    social = {}
    for key, value in (store.get("social") or {}).items():
        key = str(key).lower().strip()
        if key not in _SOCIAL_KEYS:
            continue
        try:
            social[key] = _social_url(value)
        except ValueError:
            continue
    if not social:
        b = brand_for(client, domain) or {}
        raw = b.get("social") or {}
        keymap = {"facebookUrl": "facebook", "twitter": "twitter",
                  "linkedIn": "linkedin", "instagram": "instagram",
                  "youtube": "youtube", "pinterest": "pinterest"}
        for k, v in raw.items():
            nk = keymap.get(k, k.lower())
            if v and nk in _SOCIAL_KEYS:
                try:
                    social[nk] = _social_url(v)
                except ValueError:
                    continue
    return social


def set_social(client: str, updates: dict) -> dict:
    store = load_store(client)
    social = store.setdefault("social", {})
    # seed first so a partial save doesn't wipe brandfetch-known urls
    if not social:
        webs = _client_websites(client)
        domain = str(webs[0].get("domain") or "") if webs else ""
        social.update(get_social(client, domain))
    for k, v in (updates or {}).items():
        k = str(k).lower().strip()
        v = str(v or "").strip()
        if k not in _SOCIAL_KEYS:
            continue
        if v:
            social[k] = _social_url(v)
        else:
            social.pop(k, None)
    save_store(client, store)
    return social


# -------------------------------------- master business info (one source)
def master_business_info(client: str, store: dict | None = None) -> dict:
    """Every fact the Hub knows about a client, merged into one dict so no
    form ever starts empty: saved business info, wins; then the client
    profile (contacts/address/category); then Brandfetch."""
    store = store if store is not None else load_store(client)
    bi = dict(store.get("business_info") or {})
    prof = store.get("profile") or {}
    contacts = prof.get("contacts") or []
    prim = next((c for c in contacts if c.get("primary")),
                contacts[0] if contacts else {})
    b = store.get("brandfetch") or {}
    if not b:
        webs = _client_websites(client)
        b = brand_for(client, str(webs[0].get("domain") or "") if webs else "") or {}
    loc = b.get("location") or {}

    def put(k, v):
        if v and not str(bi.get(k) or "").strip():
            bi[k] = str(v)

    put("name", b.get("name"))
    put("phone", prim.get("phone"))
    put("email", prim.get("email"))
    put("logo", b.get("logo"))
    put("city", loc.get("city"))
    put("state", loc.get("state"))
    put("category", prof.get("category"))
    put("address", prof.get("address"))
    return bi


# ------------------------------- fill the gaps: hub first, then the web
# Order matters. Everything the Hub already knows about the client is used
# BEFORE anything is looked up, so we never go hunting for a phone number we
# already have on file. Only the fields still blank after that trigger a
# lookup of the company's Google Business Profile.
SCHEMA_FIELDS = ("name", "phone", "email", "category", "address", "city",
                 "state", "zip", "hours", "logo")
# The fields worth going out to the web for — email and logo are cosmetic.
LOOKUP_FIELDS = ("name", "phone", "category", "address", "city", "state",
                 "zip", "hours")


def missing_business_fields(client: str, store: dict | None = None) -> list[str]:
    """Which schema fields are still blank after merging everything the Hub
    already holds for this client."""
    bi = master_business_info(client, store)
    return [f for f in LOOKUP_FIELDS if not str(bi.get(f) or "").strip()]


def _web_search(query: str, limit: int = 6) -> list[dict]:
    """Plain search results (title, url, snippet) without an API key."""
    out, seen = [], set()
    try:
        r = requests.post("https://html.duckduckgo.com/html/",
                          data={"q": query}, headers=UA, timeout=15)
        r.raise_for_status()
        html_text = r.text
    except Exception:                                # noqa: BLE001
        return out
    blocks = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="(.*?)".*?>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)',
        html_text, re.S | re.I)
    strip = lambda s: _html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()  # noqa: E731
    for href, title, tail in blocks:
        url = _html.unescape(href)
        m = re.search(r"uddg=([^&]+)", url)
        if m:
            from urllib.parse import unquote
            url = unquote(m.group(1))
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', tail, re.S | re.I)
        out.append({"url": url, "title": strip(title),
                    "snippet": strip(snip.group(1) if snip else "")[:400]})
        if len(out) >= limit:
            break
    return out


_GMB_EXTRACT_PROMPT = """You are researching one specific local business to complete its record.

You are given: the fields we already know (do NOT change these), the fields that are
still missing, text scraped from the company's own website, and search-engine results
for the company's Google Business Profile / business listings.

Return JSON only: {"fields": {<field>: <value>}, "source": str, "confidence": "high"|"medium"|"low", "notes": str}

Rules:
- Fill ONLY the fields listed as missing. Never return a field we already know.
- Use ONLY facts visible in the supplied website text or search results. If a field
  cannot be confirmed, OMIT it entirely. An omitted field is always better than a guess.
- "phone": digits formatted like (555) 555-5555. "address": street line only.
  "city"/"state"/"zip": separate. "state": 2-letter code.
- "hours": schema.org openingHours style, e.g. "Mo-Fr 08:00-17:00, Sa 09:00-13:00".
- "category": the specific business type, e.g. "HVAC contractor", "personal injury
  attorney", "insurance agency" — not a generic word like "company" or "service".
- The business must MATCH the name and website given. If the results are clearly about
  a different company, return {"fields": {}} and say so in "notes".
- "source": where the facts came from, e.g. "Google Business Profile listing" or
  "company website contact page"."""


def _contact_pages(site_url: str, sitemap: list[str]) -> list[str]:
    """The pages most likely to carry address, phone and hours."""
    origin = _origin(site_url)
    picks, seen = [], set()
    for u in list(sitemap or []) + [origin + "/contact", origin + "/contact-us",
                                    origin + "/about", origin + "/locations"]:
        low = str(u).lower()
        if any(k in low for k in ("contact", "about", "location", "hours", "find-us")):
            key = low.rstrip("/")
            if key not in seen:
                seen.add(key)
                picks.append(u)
        if len(picks) >= 3:
            break
    return picks


def enrich_business_info(client: str, force: bool = False) -> dict:
    """Complete a client's business info.

    1. Use everything the Hub already has (saved info, client profile,
       Brandfetch, website record) — that alone often finishes the job.
    2. Only if fields are still blank, read the client's own site (homepage +
       contact/about pages) and search the web for their Google Business
       Profile, then let the AI pull the missing values out of what it finds.

    Nothing already on file is ever overwritten.
    """
    store = load_store(client)
    known = master_business_info(client, store)
    missing = [f for f in LOOKUP_FIELDS if not str(known.get(f) or "").strip()]

    result = {"known": known, "missing": missing, "filled": {},
              "source": "Hub records", "searched": False, "notes": "", "ai_error": ""}
    if not missing and not force:
        result["notes"] = "Everything the schema needs was already on file for this client."
        return result

    webs = _client_websites(client)
    site_url = _site_url(webs[0]) if webs else ""
    if not site_url:
        result["notes"] = ("No website on file for this client, so there was nothing to "
                           "read. Add the website record, or fill the fields by hand.")
        return result

    # --- the client's own site first: it is the most trustworthy source ---
    pages = []
    home = _page_facts(site_url)
    if not home.get("error"):
        pages.append({"url": site_url, "title": home.get("title", ""),
                      "text": str(home.get("text", ""))[:2500],
                      "phone": home.get("phone", ""), "email": home.get("email", ""),
                      "socials": home.get("socials", [])})
    for u in _contact_pages(site_url, store.get("sitemap") or []):
        f = _page_facts(u)
        if not f.get("error"):
            pages.append({"url": u, "title": f.get("title", ""),
                          "text": str(f.get("text", ""))[:2000],
                          "phone": f.get("phone", ""), "email": f.get("email", "")})

    # --- then the Google Business Profile via search ---
    biz_name = known.get("name") or client
    domain = _norm_domain(site_url)
    city = known.get("city") or ""
    searches = []
    for query in (f'"{biz_name}" {city} google business profile address phone hours'.strip(),
                  f'{biz_name} {domain} address phone hours'.strip()):
        hits = _web_search(query, limit=5)
        if hits:
            searches.append({"query": query, "results": hits})
        if len(searches) >= 2:
            break
    result["searched"] = bool(searches)

    payload = {"business_name": biz_name, "website": site_url, "domain": domain,
               "known_fields": {k: v for k, v in known.items() if v},
               "missing_fields": missing,
               "website_pages": pages, "search_results": searches}

    fields = {}
    try:
        out = _openai_json(_GMB_EXTRACT_PROMPT, payload, timeout=90) or {}
        raw = out.get("fields") if isinstance(out.get("fields"), dict) else {}
        for k, v in (raw or {}).items():
            k = str(k).strip().lower()
            v = str(v or "").strip()
            if k in missing and v and v.lower() not in ("unknown", "n/a", "none"):
                fields[k] = v[:300]
        result["source"] = str(out.get("source") or "web lookup")[:120]
        result["notes"] = str(out.get("notes") or "")[:400]
        result["confidence"] = str(out.get("confidence") or "")
    except Exception as exc:                         # noqa: BLE001
        result["ai_error"] = str(exc)

    # A phone number scraped straight off the client's own site beats anything
    # a search result claims, so use it when the AI came back empty.
    if "phone" in missing and "phone" not in fields:
        scraped = next((p.get("phone") for p in pages if p.get("phone")), "")
        if scraped:
            fields["phone"] = re.sub(r"^tel:", "", scraped).strip()[:40]
            result["source"] = result["source"] or "company website"

    if fields:
        bi = store.setdefault("business_info", {})
        for k, v in fields.items():
            if not str(bi.get(k) or "").strip():     # never overwrite the team
                bi[k] = v
        save_store(client, store)

    result["filled"] = fields
    result["known"] = master_business_info(client, store)
    result["missing"] = [f for f in LOOKUP_FIELDS
                         if not str(result["known"].get(f) or "").strip()]
    return result


# ---------------------------------------- website record overrides (hub-only)
def _norm_domain(d: str) -> str:
    return re.sub(r"^https?://", "", str(d or "").lower()).removeprefix("www.").split("/")[0]


def website_overrides(client: str) -> dict:
    return load_store(client).get("website_overrides", {})


def set_website_override(client: str, domain: str, updates: dict) -> dict:
    store = load_store(client)
    ov = store.setdefault("website_overrides", {})
    d = _norm_domain(domain)
    if not d:
        raise ValueError("A domain is required.")
    entry = ov.setdefault(d, {})
    # An allowlist, so a typo'd key can't quietly become a stored field — but
    # it has to list every override we actually support. "platform" alone meant
    # s1m_hosted was accepted, reported saved, and silently discarded.
    for k in ("platform", "s1m_hosted"):
        if k not in updates:
            continue
        val = str(updates[k]).strip()
        if val:
            entry[k] = val
        else:
            entry.pop(k, None)          # blank clears rather than being ignored
    if not entry:
        ov.pop(d, None)
    save_store(client, store)
    return ov


def apply_website_overrides(client: str, websites: list[dict]) -> list[dict]:
    """Overlay hub-side corrections (e.g. platform) onto website dicts.
    Mutates the given display dicts — never Knack data."""
    ov = website_overrides(client)
    if not ov:
        return websites
    for w in websites:
        d = _norm_domain(w.get("domain"))
        hit = ov.get(d)
        if hit:
            w.update({k: v for k, v in hit.items() if v})
            w["platform_overridden"] = "platform" in hit
            # Explicit yes/no only. A blank means "not recorded", which should
            # read as an empty dropdown rather than an implied "no".
            if hit.get("s1m_hosted") in ("yes", "no"):
                w["s1m_hosted"] = hit["s1m_hosted"]
    return websites


# -------------------------------------------------- client profile & notes
def get_profile(client: str) -> dict:
    """Editable client profile shared across the whole Hub:
    {contacts:[{name,email,phone,primary}], address, category, notes:[...]}
    Seeded from Brandfetch / business info when empty."""
    store = load_store(client)
    prof = dict(store.get("profile") or {})
    prof.setdefault("contacts", [])
    prof.setdefault("address", "")
    prof.setdefault("category", "")
    prof.setdefault("notes", [])
    if not prof["address"] or not prof["category"]:
        bi = store.get("business_info", {})
        b = store.get("brandfetch") or {}
        loc = b.get("location") or {}
        if not prof["address"]:
            parts = [bi.get("address") or "", bi.get("city") or loc.get("city") or "",
                     bi.get("state") or loc.get("state") or "", bi.get("zip") or ""]
            prof["address"] = ", ".join(p for p in parts if p)
        if not prof["category"]:
            prof["category"] = bi.get("category") or ""
    return prof


def set_profile(client: str, updates: dict) -> dict:
    store = load_store(client)
    prof = store.setdefault("profile", {})
    if isinstance(updates.get("contacts"), list):
        clean = []
        for c in updates["contacts"][:10]:
            if not isinstance(c, dict):
                continue
            entry = {k: str(c.get(k) or "").strip()
                     for k in ("name", "email", "phone")}
            entry["primary"] = bool(c.get("primary"))
            if any(entry[k] for k in ("name", "email", "phone")):
                clean.append(entry)
        if clean and not any(c["primary"] for c in clean):
            clean[0]["primary"] = True
        prof["contacts"] = clean
    for k in ("address", "category"):
        if k in updates:
            prof[k] = str(updates[k] or "").strip()
    save_store(client, store)
    return get_profile(client)


def add_note(client: str, text: str, author: str = "") -> dict:
    import datetime as _dt
    store = load_store(client)
    prof = store.setdefault("profile", {})
    notes = prof.setdefault("notes", [])
    _now = _dt.datetime.now()
    notes.insert(0, {"time": dates.fmt(_now) + _now.strftime(" %I:%M %p"),
                     "author": author or "Team", "text": str(text or "").strip()[:2000]})
    del notes[100:]                       # keep the latest 100
    save_store(client, store)
    return get_profile(client)


# ------------------------------------------------------- brandfetch storage
def _brand_cache_path() -> str:
    return os.path.join(_store_base(), "_brand_by_domain.json")


def save_brandfetch(domain: str, payload: dict, client: str = ""):
    """Persist a Brandfetch result so every client form can autofill from it.

    Backed up rather than treated as a cache, even though Brandfetch could be
    asked again. The plan allows 100 lookups a month (BRANDFETCH_MONTHLY_LIMIT)
    and this file holds one per client domain, so a lost disk would refill it
    by spending a quota that /diagnostics already warns about at 80. A cache
    you cannot afford to rebuild is not a cache.
    """
    domain = str(domain or "").lower().removeprefix("www.")
    if domain:
        cache = jsonstore.read_json(_brand_cache_path(), default={})
        if not isinstance(cache, dict):
            cache = {}
        cache[domain] = payload
        with _lock:
            jsonstore.write_json(_brand_cache_path(), cache)
    if client:
        store = load_store(client)
        store["brandfetch"] = payload
        save_store(client, store)


def brand_for(client: str, domain: str = "") -> dict | None:
    """Stored Brandfetch data for a client (direct save or by domain)."""
    store = load_store(client)
    if store.get("brandfetch"):
        return store["brandfetch"]
    domain = re.sub(r"^https?://", "", str(domain or "").lower()).removeprefix("www.").split("/")[0]
    if not domain:
        return None
    cache = jsonstore.read_json(_brand_cache_path(), default=None)
    if not isinstance(cache, dict):
        return None
    return cache.get(domain)


# ------------------------------------------------------------- client lists
def _client_websites(client: str) -> list[dict]:
    """Websites whose name/domain aligns with the client name (same loose
    match Client 360 uses), plus any website records manually attached to
    the client in the Hub."""
    ck = str(client).strip().lower()
    if not ck:
        # `ck in wk` is true of every string when ck is empty, so a nameless
        # client matched the WHOLE registry -- 610 rows on this deployment,
        # and webs[0] then supplied that client's "website", its GA id and the
        # domain its Brandfetch is looked up under. A name nobody gave matches
        # nobody, the rule `client_key.resolve()` refuses a substring for.
        return []
    out = []
    for w in knack_data.websites():
        wk = str(w.get("name", "")).strip().lower()
        dk = str(w.get("domain", "")).strip().lower()
        if wk and (wk in ck or ck in wk):
            out.append(w)
        elif dk and re.sub(r"[^a-z0-9]", "", ck)[:12] and re.sub(r"[^a-z0-9]", "", ck)[:12] in re.sub(r"[^a-z0-9]", "", dk):
            out.append(w)
    # manually attached website records (matched by domain or name)
    attached = get_links(client).get("website", [])
    if attached:
        have = {str(w.get("domain") or "").lower() for w in out}
        by_dom = {}
        for w in knack_data.websites():
            d = str(w.get("domain") or "").lower().replace("https://", "").replace("http://", "").strip("/")
            if d:
                by_dom[d] = w
        for a in attached:
            d = str(a.get("domain") or "").lower().replace("https://", "").replace("http://", "").strip("/")
            hit = by_dom.get(d)
            if hit is None:
                hit = next((w for w in knack_data.websites()
                            if str(w.get("name") or "").strip().lower() == str(a.get("name") or "").strip().lower()), None)
            if hit is not None and str(hit.get("domain") or "").lower() not in have:
                out.append(hit)
                have.add(str(hit.get("domain") or "").lower())
    return out


def _site_url(w: dict) -> str:
    u = str(w.get("liveUrl") or "").strip()
    if u:
        return u if u.startswith("http") else "https://" + u
    d = str(w.get("domain") or "").strip()
    return ("https://" + d) if d else ""


def _parse_ga(value) -> dict:
    """websites.json 'ga' strings are sometimes 'G-XXXXXXXXXX' and sometimes
    'G-XXXXXXXXXXGT-YYYYYYY' (measurement id + Google tag glued together)."""
    s = str(value or "").strip()
    if not s:
        return {}
    m = re.match(r"(G-[A-Z0-9]+?)(GT-[A-Z0-9]+)?$", s)
    if m:
        return {"measurement_id": m.group(1), "google_tag": m.group(2) or ""}
    return {"measurement_id": s, "google_tag": ""}


def _parse_mdY(s):
    import datetime as _dt
    try:
        return _dt.datetime.strptime(str(s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None


# Which source answered, in one sentence, so no screen words it its own way.
# The comment above this used to say "the wording is knack_data's" while the
# string lived here, which is how a second copy starts: `/qa`'s client reports
# describe the same two sources, and a third screen wording it again is the
# drift `hub/storage.py` exists to stop. It is knack_data's now in fact as
# well as in the comment, and this stays as the name three call sites here
# already import.
def products_note(source: str, age_minutes: int | None) -> str:
    return knack_data.products_note(source, age_minutes)


def seo_clients_result() -> tuple[list[dict], str, int | None]:
    """The SEO client list, with which source answered beside it.

    The `(rows, source, age)` shape rather than a bare list, for the reason
    `connected_accounts_result()` gives in Google Finder: a stale export looks
    exactly like live data on screen, and this list decides who is on the SEO
    book at all. It read `knack_data.products()` — the private fallback export
    that nothing refreshes — while Client 360 read the same object live, so
    the Hub held a current answer and a stale one to "who are our SEO clients"
    and this screen took the stale one.
    """
    rows, source, age = knack_data._product_source()
    return _seo_clients_from(rows), source, age


def seo_clients() -> list[dict]:
    """The list alone, for the callers that do not draw a staleness note."""
    return seo_clients_result()[0]


def _seo_clients_from(product_rows: list[dict]) -> list[dict]:
    """Clients with a live SEO product: url, billing, blog flag.

    Knack pre-creates renewal IOs marked Live (future start dates), so billing
    only counts the IO whose date range covers today — one per product."""
    import datetime as _dt
    today = _dt.date.today()
    live_rows: dict[str, list[dict]] = {}
    for r in product_rows:
        pname = str(r.get("product", "")).lower()
        if "seo" not in pname:
            continue
        if not knack_data.is_running(r):
            continue
        client = str(r.get("client", "")).strip()
        if client:
            live_rows.setdefault(client, []).append(r)

    groups: dict[str, dict] = {}
    for client, rows in live_rows.items():
        # currently-running rows (or undated ones); fallback: earliest per product
        def _current(r):
            s, e = _parse_mdY(r.get("start")), _parse_mdY(r.get("end"))
            if s and s > today:
                return False
            if e and e < today:
                return False
            return True

        by_product: dict[str, dict] = {}
        for r in rows:
            key = str(r.get("product"))
            cur = by_product.get(key)
            if cur is None:
                by_product[key] = r
            elif _current(r) and not _current(cur):
                by_product[key] = r
            elif _current(r) == _current(cur):
                # prefer the earlier start (the one running now, not the renewal)
                rs, cs = _parse_mdY(r.get("start")), _parse_mdY(cur.get("start"))
                if rs and cs and rs < cs:
                    by_product[key] = r

        g = groups.setdefault(client, {"client": client, "products": set(),
                                       "billing": 0.0, "blogs": False,
                                       "sales": set(), "partner": set()})
        for pname_key, r in by_product.items():
            g["products"].add(pname_key)
            g["billing"] += knack_data._num(r.get("monthly"))
            if "blog" in pname_key.lower():
                g["blogs"] = True
        for r in rows:
            if r.get("sales"):
                g["sales"].add(str(r["sales"]))
            if r.get("partner"):
                g["partner"].add(str(r["partner"]))

    out = []
    for client in sorted(groups, key=str.lower):
        g = groups[client]
        webs = _client_websites(client)
        primary = next((w for w in webs if _site_url(w)), None)
        store = load_store(client)
        approved = sum(1 for p in store.get("pages", {}).values() if p.get("approved"))
        out.append({
            "client": client,
            "slug": slugify(client),
            "url": _site_url(primary) if primary else "",
            "platform": (primary or {}).get("platform", "") if primary else "",
            "products": sorted(g["products"]),
            "billing": round(g["billing"]),
            "blogs": g["blogs"],
            "sales": ", ".join(sorted(g["sales"])),
            "partner": ", ".join(sorted(g["partner"])),
            "setup_done": bool(store.get("setup", {}).get("completed")),
            "schema_pages": approved,
            # The product fact goes in, or the Blogs pill cannot tell a client
            # who does not buy blogs from one who is behind on them.
            "status": client_status(store, g["blogs"]),
        })
    return out



# ------------------------------------------------------- webmaster dashboard
def webmaster_roster() -> list[dict]:
    """Every SEO client with the Analytics property their numbers come from.

    Only the roster — no Google call happens here. The page renders the whole
    list at once and fills the numbers in per row, because a single request
    that fetches forty properties before it answers is a request that either
    times out on Render or leaves the reader staring at a spinner with no idea
    how far along it is.

    A client with nothing attached is reported as such, with somewhere to go
    and fix it. It is never reported as zero traffic: a clean-looking zero is
    a wrong answer presented confidently, and on this page it would read as a
    client whose SEO has died.
    """
    out = []
    for row in seo_clients():
        client = row["client"]
        analytics = (get_links(client).get("analytics") or [])
        prop = next((a for a in analytics
                     if str(a.get("resource_id") or "").strip()), None)
        out.append({
            "client": client,
            "slug": row["slug"],
            "url": row.get("url", ""),
            "partner": row.get("partner", ""),
            "billing": row.get("billing", 0),
            "property_id": str((prop or {}).get("resource_id") or ""),
            "property_name": str((prop or {}).get("name") or ""),
            "google_login": str((prop or {}).get("google_login") or ""),
        })
    return out


def _client_base(client: str) -> dict:
    """The seo_clients() row for ONE client without building the whole list —
    keeps /api/seo/detail fast."""
    import datetime as _dt
    today = _dt.date.today()
    all_rows, psource, page = knack_data._product_source()
    rows = [r for r in all_rows
            if str(r.get("client", "")).strip().lower() == client.lower()
            and "seo" in str(r.get("product", "")).lower()
            and knack_data.is_running(r)]
    products, billing, blogs = set(), 0.0, False

    def _current(r):
        s, e = _parse_mdY(r.get("start")), _parse_mdY(r.get("end"))
        if s and s > today:
            return False
        if e and e < today:
            return False
        return True

    by_product: dict[str, dict] = {}
    for r in rows:
        key = str(r.get("product"))
        cur = by_product.get(key)
        if cur is None or (_current(r) and not _current(cur)):
            by_product[key] = r
    for key, r in by_product.items():
        products.add(key)
        billing += knack_data._num(r.get("monthly"))
        if "blog" in key.lower():
            blogs = True
    real = str(rows[0]["client"]).strip() if rows else client
    webs = _client_websites(real)
    primary = next((w for w in webs if _site_url(w)), None)
    return {"client": real, "slug": slugify(real),
            "url": _site_url(primary) if primary else "",
            "platform": (primary or {}).get("platform", "") if primary else "",
            "products": sorted(products), "billing": round(billing),
            "blogs": blogs,
            # The record says which source answered, exactly as the products
            # card on Client 360 does — the same two sources, the same risk.
            "products_source": psource, "products_age_minutes": page,
            "products_note": products_note(psource, page)}


def sells_blogs(client: str) -> bool:
    """Whether a live SEO product with "blog" in its name is on this client's
    book -- for a caller holding only the store, so it can hand the fact to
    `client_status()` rather than letting the Blogs pill fall back to
    "no plan yet". Without it, ticking *Setup* would silently move a
    no-blogs-product client's Blogs pill from gray to amber, which reads as
    the tick having done something it did not do.
    """
    return bool(_client_base(client).get("blogs"))



# ------------------------------------------------------------- record health
# What is outstanding on this one client, derived rather than ticked. The
# record used to answer this with four hand-typed checkboxes at the top of the
# page — so a client could have fourteen pages with no schema and still read
# "Schema updated", because the tick is a claim somebody made and the pages
# are a fact the store already holds. This is the same question `/my-clients`
# answers at book level (hub/client_health.py), asked of one client's own
# stores: the schema pages, the blog plan, the alt-text scan, the FAQ pages
# and the llms.txt record, each of which this module already owns.
#
# Three rules, all of them this codebase's own:
#   * Nothing in here may raise — this rides on the record's one detail fetch,
#     and a health block that can 500 the page it summarizes is worse than
#     none. A source that cannot be read is NAMED in `unread` and contributes
#     nothing, never a zero: "no images are missing alt" and "the alt scan
#     could not be read" are different answers.
#   * Absent is not zero. An alt scan that never ran is `measured: False`, not
#     "0 missing"; a sitemap nobody has fetched leaves the schema total None
#     rather than claiming every page is covered.
#   * The queue only ever carries what was measured. A row derived from a
#     source that failed would be a confident claim about data nobody read.

LLMS_STALE_DAYS = 90   # house guidance, not a published rule: past this the
                       # file predates a season of site changes and is worth
                       # rebuilding. Advisory — it never blocks anything.


def _days_since(stamp: str, today: str = "") -> int | None:
    """Whole days since an ISO-ish timestamp, or None if it will not parse.

    `today` is the day to count back from, defaulting to the real one. A
    caller that pinned the day and then called this without it got a count
    measured against the wall clock instead -- so the day it decided and the
    day it counted from were the same until midnight and one apart after it.
    """
    import datetime as _dt
    s = str(stamp or "")[:10]
    try:
        now = (_dt.date.fromisoformat(today[:10]) if today
               else _dt.date.today())
        return (now - _dt.date.fromisoformat(s)).days
    except Exception:  # noqa: BLE001 — a bad stamp is "not measured", never a crash
        return None


BLOG_CADENCE_SLACK = 1.5   # house guidance: a plan that has nothing planned
                           # within one and a half intervals of today has run
                           # out, whatever the setup says the cadence is.


def blogs_health(store: dict, sells: bool | None = None, *,
                 today: str = "") -> dict:
    """The one rule for "behind on blogs", read by the SEO record's strip and
    by Client 360's -- two readings of it is how the two screens come to
    disagree about who is behind.

    What is knowable here, and what is not, written down rather than
    inferred:

      * **Overdue** is a planned post whose date has passed and that nobody
        has marked posted. That is the rule `_blogs_state` already applies
        for the Blogs pill, so `state` is the same BLOGS_STATES key every
        other screen reads.
      * **The cadence is stored** -- `setup.blogs_frequency` or
        `setup.blogs_per_month`, the same answers `_blog_schedule()` spaces
        the plan by. `_freq_interval_days()` defaults to weekly when neither
        is set; here that default is *not* taken as a fact, so
        `cadence_days` is None and `cadence_source` says "not recorded"
        rather than judging a client against a number nobody chose.
      * **A plan can run out.** Once the last planned date is more than
        `BLOG_CADENCE_SLACK` intervals behind today there is nothing left to
        be overdue, and the client reads as up to date for ever -- so
        `plan_exhausted` is its own flag, raised only where the cadence is
        recorded and blogs are sold, and it carries the days.
      * **No publish date is recorded.** A post carries its planned `date`
        and a `posted` tick, and nothing writes when it actually went live.
        So `last_posted` is the planned date of the latest post marked
        posted, said in those words, and `published_dates_recorded` is
        False so a screen cannot print it as "last published".
    """
    posts = (store.get("blogs") or {}).get("posts") or []
    setup = store.get("setup") or {}
    # Injected or the real clock -- never both. Every date in here is "how far
    # behind is this plan", so a caller that pins the day (a test, or a strip
    # rendering a client's record as of a date) and a rule that reads the wall
    # clock disagree by one every midnight, and the pill beside it does not.
    today = today or _dt_date_today_iso()
    state = _blogs_state(store, sells)

    overdue = [p for p in posts
               if str(p.get("date", "")) <= today and not p.get("posted")]
    oldest = min((str(p.get("date", "")) for p in overdue), default="")

    stated = (str(setup.get("blogs_frequency") or "").strip()
              or str(setup.get("blogs_per_month") or "").strip())
    cadence_days = _freq_interval_days(setup) if stated else None

    last_planned = max((str(p.get("date", "")) for p in posts), default="")
    last_posted = max((str(p.get("date", "")) for p in posts if p.get("posted")),
                      default="")
    plan_exhausted = False
    behind_by = None
    if cadence_days and sells is not False and state != "not_sold":
        since = _days_since(last_planned, today) if last_planned else None
        if since is None:
            # No planned date at all: a plan that was never made has run out
            # by definition, and a date that will not parse is not judged.
            plan_exhausted = not posts
        elif since > cadence_days * BLOG_CADENCE_SLACK:
            plan_exhausted = True
            behind_by = since

    return {
        "state": state, "label": BLOGS_STATES[state],
        "overdue": len(overdue),
        "overdue_days": _days_since(oldest, today) if oldest else None,
        "cadence_days": cadence_days,
        "cadence_source": ("setup" if stated else "not recorded"),
        "plan_exhausted": plan_exhausted,
        "plan_ran_out_days": behind_by,
        "last_planned": last_planned,
        "last_posted": last_posted,
        "published_dates_recorded": False,
        "rule": ("overdue = planned date passed and not marked posted; "
                 "plan exhausted = last planned date more than "
                 f"{BLOG_CADENCE_SLACK:g} cadence intervals ago"),
    }


def record_health(client: str, store: dict | None = None, *,
                  sells: bool | None = None,
                  faq_pages: list[dict] | None = None,
                  today: str = "") -> dict:
    """The derived health block the record's strip and queue draw.

    `store`, `sells` and `faq_pages` are passed in by `client_detail`, which
    has already read all three — a second read here would be a second answer
    to the same question taken a moment apart.
    """
    unread: list[str] = []
    queue: list[dict] = []

    if store is None:
        try:
            store = load_store(client)
        except Exception:  # noqa: BLE001
            unread.append("the client's SEO store could not be read")
            store = {}

    checks = client_status(store, sells)

    # ---- schema: built / approved against the sitemap the scan found ----
    pages = store.get("pages", {}) or {}
    sitemap = store.get("sitemap", []) or []
    built = len(pages)
    remaining = len([u for u in sitemap if u not in pages]) if sitemap else None
    schema = {
        "measured": bool(sitemap or pages),
        "built": built,
        "approved": sum(1 for p in pages.values() if p.get("approved")),
        "total": len(sitemap) if sitemap else None,
        "remaining": remaining,
    }
    if remaining:
        queue.append({
            "level": "warn", "section": "schema",
            "title": f"{remaining} page{'s' if remaining != 1 else ''} still have no schema",
            "detail": f"{built} of {len(sitemap)} sitemap pages are built.",
        })

    # ---- blogs: the plan's own dates, the one rule blogs_health() holds ----
    blogs = blogs_health(store, sells, today=today)
    if blogs["overdue"]:
        n = blogs["overdue"]
        queue.append({
            "level": "bad", "section": "blogs",
            "title": f"{n} blog post{'s are' if n != 1 else ' is'} past due",
            "detail": "Planned, and not marked posted on the site.",
            "age_days": blogs["overdue_days"],
        })
    if blogs["plan_exhausted"]:
        ran = blogs["plan_ran_out_days"]
        queue.append({
            "level": "warn", "section": "blogs",
            "title": "The blog plan has run out",
            "detail": (f"Nothing is planned within {blogs['cadence_days']:g} days "
                       "of today, which is the cadence the setup records."
                       if ran is None else
                       f"The last planned post was {ran} days ago against a "
                       f"{blogs['cadence_days']:g}-day cadence."),
            "age_days": ran,
        })

    # ---- alt text: the stored scan, never a fresh crawl ----
    alt_store = store.get("alt_text") or {}
    if alt_store.get("scanned_at"):
        missing = int(alt_store.get("missing_alt") or 0)
        alt = {"measured": True, "missing": missing,
               "scanned_at": alt_store.get("scanned_at", "")}
        if missing:
            queue.append({
                "level": "warn", "section": "alt",
                "title": f"{missing} image{'s are' if missing != 1 else ' is'} missing alt text",
                "detail": f"From the scan on {alt['scanned_at']}.",
            })
    else:
        alt = {"measured": False, "note": "The site has not been scanned for alt text yet."}

    # ---- FAQs: built, and built-but-not-on-the-site ----
    if faq_pages is None:
        try:
            from . import faq as _faq
            faq_pages = _faq.list_pages(client)
        except Exception:  # noqa: BLE001
            unread.append("the FAQ pages could not be read")
            faq_pages = None
    if faq_pages is None:
        faqs = {"measured": False}
    else:
        waiting = sum(1 for p in faq_pages if not p.get("added_to_site"))
        faqs = {"measured": True, "built": len(faq_pages),
                "live": len(faq_pages) - waiting, "waiting": waiting}
        if waiting:
            queue.append({
                "level": "info", "section": "faqs",
                "title": f"{waiting} FAQ set{'s are' if waiting != 1 else ' is'} built but not on the site yet",
                "detail": "Waiting to be added to the client's pages.",
            })

    # ---- llms.txt: staleness of the live copy ----
    llms: dict = {"measured": False}
    try:
        from . import llms_hosting as _lh
        pub = _lh.published(client)
        if pub:
            days = _days_since(pub.get("at", ""), today)
            llms = {"measured": True, "state": "live",
                    "published_at": str(pub.get("at", ""))[:10], "days": days}
            if days is not None and days >= LLMS_STALE_DAYS:
                llms["state"] = "stale"
                queue.append({
                    "level": "info", "section": "llms",
                    "title": f"llms.txt is {days} days old",
                    "detail": "Worth rebuilding if pages have been added since.",
                    "age_days": days,
                })
        else:
            llms = {"measured": True, "state": "none"}
    except Exception:  # noqa: BLE001
        unread.append("the llms.txt record could not be read")

    order = {"bad": 0, "warn": 1, "info": 2}
    queue.sort(key=lambda q: order.get(q["level"], 3))
    return {"checks": checks, "schema": schema, "blogs": blogs, "alt": alt,
            "faqs": faqs, "llms": llms, "queue": queue, "unread": unread}


def client_detail(client: str, full: bool = False) -> dict:
    base = _client_base(client)
    client = base["client"]
    webs = []
    for w in _client_websites(client):
        ga = _parse_ga(w.get("ga"))
        gtm = knack_data._parse_gtm(w.get("gtm")) or {}
        webs.append({
            "name": w.get("name"), "domain": w.get("domain"),
            "url": _site_url(w), "platform": w.get("platform") or "Unknown",
            "status": w.get("status"),
            "ga_measurement": ga.get("measurement_id", ""),
            "google_tag": ga.get("google_tag", ""),
            "gtm_id": gtm.get("id", ""), "gtm_login": gtm.get("login", ""),
        })
    apply_website_overrides(client, webs)
    store = load_store(client)
    pages = store.get("pages", {})
    base.update({
        "websites": webs,
        "setup": {k: v for k, v in store.get("setup", {}).items() if k != "password"},
        "setup_has_password": bool(store.get("setup", {}).get("password")),
        "business_info": master_business_info(client, store),
        "questions": store.get("questions", []),
        "answers": store.get("answers", {}),
        "sitemap_total": len(store.get("sitemap", [])),
        "pages_generated": len(pages),
        "pages_approved": sum(1 for p in pages.values() if p.get("approved")),
        "attached": get_links(client),
        "last_scan": store.get("last_scan"),
        "brandfetch": brand_for(client, (webs[0]["domain"] if webs else "")),
        "checks": store.get("checks", {}),
        "status": client_status(store, base["blogs"]),
        "social": get_social(client, (webs[0]["domain"] if webs else "")),
    })
    if full:                      # one round-trip for the whole SEO page
        remaining = [p for p in store.get("sitemap", [])
                     if p not in store.get("pages", {})]
        base["schema_pages_list"] = list(pages.values())
        base["schema_remaining"] = len(remaining)
        base["schema_table"] = schema_pages_table(client)
        base["missing_business_fields"] = [
            f for f in LOOKUP_FIELDS
            if not str(base["business_info"].get(f) or "").strip()]
        from . import faq as _faq
        base["faq_pages"] = _faq.list_pages(client)
        blogs = store.get("blogs", {})
        base["blog_posts"] = blogs.get("posts", [])
        base["blog_questions"] = blogs.get("questions", [])
        base["blog_settings"] = blog_settings(client, store)
        # The strip and the queue. Guarded again here even though
        # record_health guards each source, because this rides on the
        # record's one detail fetch and a health bug must cost the strip,
        # never the page.
        try:
            base["health"] = record_health(
                client, store, sells=base["blogs"],
                faq_pages=base["faq_pages"])
        except Exception:  # noqa: BLE001
            base["health"] = {"error": "The health summary could not be built."}
    return base


# ------------------------------------------------------------- schema scan
def _fetch(url: str, timeout: int = 15) -> str:
    r = requests.get(url, headers=UA, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r.text


def scan_schema(url: str) -> dict:
    """Fetch a page and report structured data found on it."""
    if not url.startswith("http"):
        url = "https://" + url
    html = _fetch(url)
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I)
    ld_types, valid = [], 0
    for b in blocks:
        try:
            data = json.loads(_html.unescape(b.strip()))
        except ValueError:
            continue
        valid += 1
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                graph = item.get("@graph")
                nodes = graph if isinstance(graph, list) else [item]
                for n in nodes:
                    t = n.get("@type") if isinstance(n, dict) else None
                    if isinstance(t, list):
                        ld_types.extend(str(x) for x in t)
                    elif t:
                        ld_types.append(str(t))
    micro = len(re.findall(r"\bitemscope\b", html))
    micro_types = re.findall(r'itemtype=["\']https?://schema\.org/([\w]+)', html)
    return {
        "url": url,
        "has_schema": bool(valid or micro),
        "jsonld_blocks": valid,
        "types": sorted(set(ld_types)),
        "microdata_items": micro,
        "microdata_types": sorted(set(micro_types)),
    }


# ---------------------------------------------------------------- sitemap
def _origin(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url if url.startswith("http") else "https://" + url)
    return m.group(1) if m else url


def _sitemap_locs(xml_text: str) -> tuple[list[str], bool]:
    """Return (<loc> urls, is_index)."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return [], False
    tag = root.tag.lower()
    locs = [el.text.strip() for el in root.iter()
            if el.tag.lower().endswith("loc") and el.text and el.text.strip()]
    return locs, tag.endswith("sitemapindex")


def sitemap_pages(url: str, cap: int = 300) -> list[str]:
    origin = _origin(url)
    candidates = [origin + "/sitemap.xml", origin + "/sitemap_index.xml",
                  origin + "/sitemap-index.xml", origin + "/page-sitemap.xml"]
    pages, seen = [], set()

    def add(u):
        u = u.split("#")[0]
        if u not in seen and u.startswith("http"):
            seen.add(u)
            pages.append(u)

    for cand in candidates:
        try:
            locs, is_index = _sitemap_locs(_fetch(cand))
        except Exception:  # noqa: BLE001 — try the next candidate
            continue
        if not locs:
            continue
        if is_index:
            for child in locs[:20]:
                try:
                    clocs, _ = _sitemap_locs(_fetch(child))
                except Exception:  # noqa: BLE001
                    continue
                for u in clocs:
                    add(u)
                if len(pages) >= cap:
                    break
        else:
            for u in locs:
                add(u)
        if pages:
            break
    if not pages:            # no sitemap — at least do the homepage
        add(origin + "/")
    # homepage first, then original order
    pages.sort(key=lambda u: 0 if u.rstrip("/") == origin else 1)
    return pages[:cap]


# ------------------------------------------------------- page content pull
def _page_facts(url: str) -> dict:
    try:
        html = _fetch(url)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": str(exc)}

    def meta(name):
        m = re.search(
            r'<meta[^>]+(?:name|property)=["\']' + re.escape(name) + r'["\'][^>]+content=["\'](.*?)["\']',
            html, re.I) or re.search(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+(?:name|property)=["\']' + re.escape(name) + r'["\']',
            html, re.I)
        return _html.unescape(m.group(1)) if m else ""

    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
    h2 = re.findall(r"<h2[^>]*>(.*?)</h2>", html, re.S | re.I)
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(re.sub(r"\s+", " ", text)).strip()
    tel = re.search(r'href=["\']tel:([^"\']+)', html, re.I)
    mail = re.search(r'href=["\']mailto:([^"\']+)', html, re.I)
    socials = sorted(set(re.findall(
        r'href=["\'](https?://(?:www\.)?(?:facebook|instagram|linkedin|youtube|x|twitter|tiktok|yelp)\.com/[^"\']+)',
        html, re.I)))[:8]
    strip = lambda s: _html.unescape(re.sub(r"<[^>]+>", " ", s)).strip()  # noqa: E731
    return {
        "url": url,
        "title": _html.unescape(title.group(1)).strip() if title else "",
        "description": meta("description") or meta("og:description"),
        "og_image": meta("og:image"),
        "og_type": meta("og:type"),
        "h1": [strip(x) for x in h1[:3]],
        "h2": [strip(x) for x in h2[:8]],
        "phone": tel.group(1).strip() if tel else "",
        "email": mail.group(1).strip() if mail else "",
        "socials": socials,
        "text": text[:3500],
    }


# ------------------------------------------------------- schema generation
_SYSTEM_PROMPT = """You are an expert in schema.org structured data for local-business and service websites.
Given facts scraped from ONE web page plus business information, produce the MOST COMPLETE valid JSON-LD
for that page. Rules:
- Output a single JSON object: {"schema": <JSON-LD object>, "questions": [<strings>]}.
- The JSON-LD must use "@context": "https://schema.org" and an "@graph" array.
- Always include: WebSite, WebPage (correct subtype if apparent: AboutPage, ContactPage, FAQPage, CollectionPage...),
  BreadcrumbList, and Organization or LocalBusiness (pick the most specific LocalBusiness subtype the business fits,
  e.g. HVACBusiness, Plumber, InsuranceAgency, AutoRepair, MedicalClinic, RoofingContractor, Electrician).
- Add page-specific types when the content supports them: Service, Product, FAQPage with mainEntity Questions,
  Article/BlogPosting for blog pages, Person for team pages, Review/AggregateRating ONLY if real ratings appear.
- Use @id cross-references between nodes (e.g. "@id": "<site>#organization") so the graph is linked.
- Fill name, url, description, telephone, email, address, geo, openingHoursSpecification, sameAs, logo, image,
  areaServed, priceRange whenever the facts or business info provide them. NEVER invent addresses, phone numbers,
  ratings, prices or hours — if a valuable property is unknown, omit it AND add a short plain-English question
  asking for it in "questions" (e.g. "What is the full street address?", "What are the business hours?").
- Keep questions to the 3-6 most valuable missing facts. No markdown, no commentary — JSON only."""


def _ai_schema(facts: dict, business: dict, answers: dict, client: str):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    payload = {
        "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps({
                "client_name": client,
                "page_facts": facts,
                "business_info": business,
                "answered_questions": answers,
            })},
        ],
    }
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json=payload, timeout=90)
    r.raise_for_status()
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("seo", r.json(), purpose="schema")
    except Exception:  # noqa: BLE001
        pass
    out = json.loads(r.json()["choices"][0]["message"]["content"])
    if isinstance(out, dict) and "schema" in out:
        return out
    return {"schema": out, "questions": []}


def _template_schema(facts: dict, business: dict, client: str) -> dict:
    """No-AI fallback: solid WebSite/WebPage/Organization graph from scraped
    facts + whatever business info we have."""
    origin = _origin(facts["url"])
    org_id, site_id = origin + "/#organization", origin + "/#website"
    org = {"@type": "Organization", "@id": org_id,
           "name": business.get("name") or client, "url": origin + "/"}
    for src, key in ((facts.get("phone") or business.get("phone"), "telephone"),
                     (facts.get("email") or business.get("email"), "email"),
                     (business.get("logo") or facts.get("og_image"), "logo")):
        if src:
            org[key] = src
    if facts.get("socials"):
        org["sameAs"] = facts["socials"]
    addr = business.get("address")
    if addr:
        org["address"] = {"@type": "PostalAddress", "streetAddress": addr,
                          **({"addressLocality": business["city"]} if business.get("city") else {}),
                          **({"addressRegion": business["state"]} if business.get("state") else {}),
                          **({"postalCode": business["zip"]} if business.get("zip") else {})}
    graph = [
        org,
        {"@type": "WebSite", "@id": site_id, "url": origin + "/",
         "name": business.get("name") or client, "publisher": {"@id": org_id}},
        {"@type": "WebPage", "@id": facts["url"] + "#webpage", "url": facts["url"],
         "name": facts.get("title") or (business.get("name") or client),
         **({"description": facts["description"]} if facts.get("description") else {}),
         "isPartOf": {"@id": site_id}, "about": {"@id": org_id}},
        {"@type": "BreadcrumbList", "@id": facts["url"] + "#breadcrumb",
         "itemListElement": [{"@type": "ListItem", "position": 1, "name": "Home",
                              "item": origin + "/"}] + (
             [] if facts["url"].rstrip("/") == origin else
             [{"@type": "ListItem", "position": 2,
               "name": facts.get("title") or facts["url"], "item": facts["url"]}])},
    ]
    questions = []
    if not (facts.get("phone") or business.get("phone")):
        questions.append("What is the business phone number?")
    if not business.get("address"):
        questions.append("What is the full street address (street, city, state, zip)?")
    if not business.get("hours"):
        questions.append("What are the business hours?")
    if not business.get("category"):
        questions.append("What type of business is this (e.g. HVAC, plumber, insurance agency)?")
    return {"schema": {"@context": "https://schema.org", "@graph": graph},
            "questions": questions}


def generate_for_pages(client: str, urls: list[str], enrich: bool = True) -> dict:
    """Generate (or regenerate) schema for the given page urls.

    Before anything is generated we make sure the business info is as complete
    as we can get it — first from what the Hub already holds, then from the
    company's Google Business Profile — so the generator asks the team for far
    less than it used to."""
    enriched = None
    if enrich:
        try:
            enriched = enrich_business_info(client)
        except Exception as exc:                     # noqa: BLE001 — never block
            enriched = {"filled": {}, "ai_error": str(exc)}

    store = load_store(client)
    business = master_business_info(client, store)
    answers = store.get("answers", {})
    results, questions = [], list(store.get("questions", []))
    ai_error = ""
    for url in urls:
        facts = _page_facts(url)
        if facts.get("error"):
            results.append({"url": url, "error": facts["error"]})
            continue
        out = None
        try:
            out = _ai_schema(facts, business, answers, client)
        except Exception as exc:  # noqa: BLE001 — fall back to template
            ai_error = str(exc)
        if out is None:
            out = _template_schema(facts, business, client)
        for q in out.get("questions", []):
            if q and q not in questions and q not in answers:
                questions.append(q)
        prior = store.get("pages", {}).get(url) or {}
        page = {"url": url, "title": facts.get("title", ""),
                "schema": out["schema"], "approved": False,
                "created": prior.get("created") or _dt_date_today_iso(),
                "added_to_site": prior.get("added_to_site", ""),
                "updated": _dt_now_stamp()}
        store.setdefault("pages", {})[url] = page
        results.append(page)
    store["questions"] = questions[:20]
    save_store(client, store)
    out = {"pages": results, "questions": store["questions"],
           "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
           "ai_error": ai_error}
    if enriched:
        out["enriched"] = {"filled": enriched.get("filled", {}),
                           "source": enriched.get("source", ""),
                           "missing": enriched.get("missing", []),
                           "notes": enriched.get("notes", "")}
        out["business_info"] = master_business_info(client, store)
    return out


def _dt_now_stamp() -> str:
    import datetime as _dt
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------- saved schema page table
def _schema_types(schema) -> list[str]:
    """Every @type in a generated graph, for the table's Types column."""
    types = []

    def walk(node):
        if isinstance(node, dict):
            t = node.get("@type")
            if isinstance(t, list):
                types.extend(str(x) for x in t)
            elif t:
                types.append(str(t))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    seen, out = set(), []
    for t in types:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def schema_pages_table(client: str) -> list[dict]:
    """Rows for the Schema Builder's saved-pages table."""
    store = load_store(client)
    rows = []
    for url, page in store.get("pages", {}).items():
        types = _schema_types(page.get("schema"))
        rows.append({
            "url": url,
            "title": page.get("title", ""),
            "created": page.get("created", ""),
            "added_to_site": page.get("added_to_site", ""),
            "updated": page.get("updated", ""),
            "approved": bool(page.get("approved")),
            "types": types,
            "type_count": len(types),
        })
    rows.sort(key=lambda r: (str(r.get("created") or ""), r["url"]), reverse=True)
    return rows


def update_page_meta(client: str, url: str, updates: dict) -> dict | None:
    """Edit a saved schema page — the added-to-site date, the approval flag,
    or the JSON-LD itself after a customer asks for a change."""
    store = load_store(client)
    page = store.get("pages", {}).get(url)
    if page is None:
        return None
    if "added_to_site" in updates:
        page["added_to_site"] = _iso_date(updates["added_to_site"])
    if "approved" in updates:
        page["approved"] = bool(updates["approved"])
    if isinstance(updates.get("schema"), (dict, list)):
        page["schema"] = updates["schema"]
    page.setdefault("created", _dt_date_today_iso())
    page["updated"] = _dt_now_stamp()
    save_store(client, store)
    return page


def delete_page(client: str, url: str) -> bool:
    store = load_store(client)
    pages = store.get("pages", {})
    if url not in pages:
        return False
    pages.pop(url)
    save_store(client, store)
    return True


def _iso_date(value) -> str:
    """Normalise a typed date to YYYY-MM-DD; blank clears it."""
    import datetime as _dt
    s = str(value or "").strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def schema_doc(client: str, urls: list[str] | None = None) -> str:
    """Word-openable document of the selected schema pages, laid out for a
    customer (or a developer) to review before it goes on the site."""
    import datetime as _dt
    store = load_store(client)
    pages = store.get("pages", {})
    if urls:
        wanted = {str(u).rstrip("/") for u in urls}
        items = [(u, p) for u, p in pages.items() if str(u).rstrip("/") in wanted]
    else:
        items = list(pages.items())
    esc = _html.escape
    parts = ["""<html xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8">
<title>Schema markup &mdash; """ + esc(client) + """</title>
<style>
body{font-family:Calibri,Segoe UI,sans-serif;max-width:820px;margin:24px auto;color:#1e293b;line-height:1.5}
h1{color:#1a2e58;font-size:22pt;margin-bottom:2px}
.meta{color:#64748b;font-size:10pt}
.pagehead{background:#f4f6fa;border-left:4px solid #1a2e58;padding:10px 14px;margin:26px 0 10px}
.pagehead .u{font-size:10.5pt;color:#2563eb;word-break:break-all}
pre{background:#f8fafc;border:1px solid #e2e8f0;padding:12px;font:9pt Consolas,monospace;
white-space:pre-wrap;word-break:break-word}
.pagebreak{page-break-before:always}
</style></head><body>"""]
    parts.append(f"<h1>Schema markup</h1><p class='meta'><b>{esc(client)}</b> &middot; "
                 f"prepared by Smart 1 Marketing &middot; "
                 f"{esc(_dt.date.today().strftime('%B %-d, %Y'))}</p>"
                 f"<p class='meta'>Each block below is the JSON-LD for one page. "
                 f"Paste it into that page's &lt;head&gt; exactly as shown.</p>")
    if not items:
        parts.append("<p><i>No schema pages saved yet.</i></p>")
    for i, (url, page) in enumerate(items):
        cls = " pagebreak" if i else ""
        types = ", ".join(_schema_types(page.get("schema"))) or "—"
        parts.append(f"<div class='pagehead{cls}'><b>{esc(page.get('title') or url)}</b>"
                     f"<div class='u'>{esc(url)}</div>"
                     f"<div class='meta'>{esc(types)}"
                     + (f" &middot; added to site {esc(page.get('added_to_site'))}"
                        if page.get("added_to_site") else "") + "</div></div>")
        parts.append('<pre>&lt;script type="application/ld+json"&gt;\n'
                     + esc(json.dumps(page.get("schema"), indent=1))
                     + "\n&lt;/script&gt;</pre>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------------ blogs
# ---- blog settings: the author, the guardrails and the approved topics ----
# All four of these were things the account manager knew and the writer never
# saw. `hub/blog_spec.py` owns the rules; this is where they are stored,
# beside the posts they govern, in the same durable per-client file.
def blog_settings(client: str, store: dict | None = None) -> dict:
    store = store if store is not None else load_store(client)
    blogs = store.get("blogs", {})
    from . import blog_spec
    return {
        "author": blog_spec.normalise_author(blogs.get("author")),
        "guidance": str(blogs.get("guidance") or ""),
        "avoid": blog_spec.normalise_avoid(blogs.get("avoid")),
        "categories": [blog_spec.normalise_category(c)
                       for c in (blogs.get("categories") or []) if str(c).strip()],
        "approved_topics": list(blogs.get("approved_topics") or []),
        "approved_only": bool(blogs.get("approved_only")),
        "topics_source": dict(blogs.get("topics_source") or {}),
    }


def save_blog_settings(client: str, updates: dict) -> dict:
    """Partial update — only the keys present are touched.

    A blank guidance box is a real answer ("nothing special about this
    client"), so an empty string is saved rather than skipped; a key that was
    not sent at all is what leaves the stored value alone.
    """
    from . import blog_spec
    store = load_store(client)
    blogs = store.setdefault("blogs", {})
    if "author" in updates:
        blogs["author"] = blog_spec.normalise_author(updates["author"])
    if "guidance" in updates:
        blogs["guidance"] = str(updates["guidance"] or "")[:8000]
    if "avoid" in updates:
        blogs["avoid"] = blog_spec.normalise_avoid(updates["avoid"])
    if "categories" in updates:
        blogs["categories"] = blog_spec.merge_categories([], updates["categories"])
    if "approved_only" in updates:
        blogs["approved_only"] = bool(updates["approved_only"])
    save_store(client, store)
    return blog_settings(client)


def set_approved_topics(client: str, text: str, filename: str = "",
                        append: bool = False) -> dict:
    """Store the topic list out of a document the client already approved.

    Returns the parsed list rather than a count, because a document that
    parsed into three topics when it holds thirty needs to be *seen* to be
    caught — a number on its own reads as success.
    """
    from . import blog_spec
    parsed = blog_spec.parse_approved_topics(text)
    store = load_store(client)
    blogs = store.setdefault("blogs", {})
    existing = list(blogs.get("approved_topics") or []) if append else []
    seen = {t["title"].lower() for t in existing if isinstance(t, dict)}
    for t in parsed:
        if t["title"].lower() not in seen:
            existing.append(t)
            seen.add(t["title"].lower())
    blogs["approved_topics"] = existing[:blog_spec.MAX_APPROVED_TOPICS]
    blogs["topics_source"] = {"filename": str(filename or "")[:160],
                              "uploaded": _dt_date_today_iso(),
                              "found": len(parsed)}
    save_store(client, store)
    return {"topics": blogs["approved_topics"], "found": len(parsed),
            "source": blogs["topics_source"]}


def clear_approved_topics(client: str) -> dict:
    store = load_store(client)
    blogs = store.setdefault("blogs", {})
    blogs["approved_topics"] = []
    blogs["topics_source"] = {}
    save_store(client, store)
    return {"topics": [], "found": 0, "source": {}}


def _freq_interval_days(setup: dict) -> float:
    """Days between posts, from the setup answers."""
    freq = str(setup.get("blogs_frequency") or "").strip().lower()
    table = {"weekly": 7, "bi-weekly": 14, "biweekly": 14,
             "twice a month": 15, "monthly": 30}
    if freq in table:
        return table[freq]
    try:
        per_month = float(setup.get("blogs_per_month") or 0)
        if per_month > 0:
            return max(3.0, 30.0 / per_month)
    except (TypeError, ValueError):
        pass
    return 7.0  # sensible default: weekly


def _blog_schedule(setup: dict, months: int = 3, start_date: str = "") -> list[dict]:
    """[{date, week}] for the next N months, spaced by the blog frequency.
    Starts next Monday, or at start_date when the client already has blogs
    and you want to pick up from a specific point."""
    import datetime as _dt
    today = _dt.date.today()
    start = today + _dt.timedelta(days=(7 - today.weekday()) % 7 or 7)  # next Monday
    if start_date:
        try:
            requested = _dt.date.fromisoformat(str(start_date)[:10])
            if requested > today:
                start = requested
        except ValueError:
            pass
    interval = _freq_interval_days(setup)
    end = today + _dt.timedelta(days=months * 30 + 6)
    out, d, i = [], start, 0
    while d <= end:
        monday = d - _dt.timedelta(days=d.weekday())
        out.append({"date": d.isoformat(),
                    "week": "Week of " + monday.strftime("%b %-d, %Y")})
        i += 1
        d = start + _dt.timedelta(days=round(interval * i))
    return out


_BLOG_PLAN_PROMPT = """You are an SEO content strategist for a local-business marketing agency.
Given information about a client (their website facts, business info, and any focus areas the account
manager provided), produce blog post topics for their upcoming schedule.
Rules:
- Output JSON only:
  {"posts": [{"title": str, "summary": str, "categories": [str], "tags": [str]}], "questions": [str]}.
- Produce EXACTLY the number of posts requested, in publish order.
- Titles must be specific, locally relevant, search-intent driven (how-to, cost guides, seasonal,
  comparisons, FAQs) — never generic filler like "Welcome to our blog".
- Respect the requested focus areas first; spread remaining posts across the client's services.
- Match topics to the season of the given publish dates when relevant.
- Any titles under "approved_topics" were signed off by the client IN ADVANCE. Use them, in the
  order given, before inventing anything, and reproduce each title as written.
- Categories are the site's structure, so reuse "existing_categories" wherever one fits and only
  invent a category when none of them does. Tags are per-post detail. Follow "taxonomy_rules".
- "company_guidance" is what the client has told us about themselves — treat it as fact and let it
  shape the topics. Never propose a topic that would require mentioning anything in "never_mention".
- If information that would make the topics stronger is missing (services offered, service area,
  specials, target customers), add up to 4 short questions in "questions". Do not block on them."""

_BLOG_WRITE_PROMPT = """You are an SEO content writer for a local-business marketing agency.
Write ONE complete blog post for the client's website.
Rules:
- Output JSON only: {"html": str, "meta_description": str, "categories": [str], "tags": [str]}.
- "html" is the post BODY only (no <html>/<head>): an <h1> title, short intro, <h2> sections,
  <ul> lists where useful, and a closing call-to-action paragraph mentioning the business name
  and phone/city when known. 600-900 words.
- Write naturally for humans first; work the topic's obvious search phrases into headings.
- NEVER invent facts, prices, certifications, awards or service claims not present in the
  provided information. Keep claims general when specifics are unknown.
- "company_guidance" describes how this company actually operates, and its legal and compliance
  position. Follow it exactly — it outranks anything you would otherwise assume about the trade.
- Do not mention, imply or work around anything listed in "never_mention", in the body, the
  headings or the meta description.
- Keep the categories and tags supplied with the post unless the finished copy makes one plainly
  wrong; follow "taxonomy_rules" if you change them."""


def _openai_json(system: str, user_payload: dict, timeout: int = 120):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                            "response_format": {"type": "json_object"},
                            "temperature": 0.5,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": json.dumps(user_payload)}]},
                      timeout=timeout)
    r.raise_for_status()
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("seo", r.json(), purpose="content")
    except Exception:  # noqa: BLE001
        pass
    return json.loads(r.json()["choices"][0]["message"]["content"])


def client_site_url(client: str, store: dict | None = None) -> str:
    """The client's website, from the store override or the client roster.

    One place, because the publish buttons, the AI context and the CMS admin
    URL must agree on which site this is — a rep sent to a different site than
    the one the copy was written for is the failure that matters here.
    """
    store = store if store is not None else load_store(client)
    site = str(store.get("site_url") or "")
    if site:
        return site
    try:
        rows = [c for c in seo_clients() if c["client"].lower() == client.lower()]
    except Exception:                                   # noqa: BLE001
        rows = []
    return rows[0]["url"] if rows else ""


def _client_context(client: str, store: dict) -> dict:
    """Everything the AI should know about the client."""
    site = client_site_url(client, store)
    facts = {}
    if site:
        try:
            facts = _page_facts(site)
            facts["text"] = str(facts.get("text", ""))[:2500]
        except Exception:  # noqa: BLE001
            facts = {}
    ctx = {"client_name": client, "website": site,
           "homepage_facts": {k: v for k, v in facts.items() if k != "error"},
           "business_info": master_business_info(client, store),
           "answered_questions": store.get("answers", {}),
           "blog_answers": store.get("blogs", {}).get("answers", {})}
    # ...and what the rest of the Hub holds: the industry on their Knack
    # record, the city they trade in, their live products, the palette and
    # contact details read off their own site by the last Insites scan. The
    # writer had the home page and the answers somebody typed, so a post for a
    # client of eleven years read like a post for a business we met yesterday.
    # One reader — hub/client_context.for_prompt() — so a fact added there
    # reaches the blog writer, the campaign generator and the ad copy alike.
    try:
        from .client_context import for_prompt
        known = for_prompt(client, site)
        if known:
            ctx["hub_record"] = known
    except Exception:                                   # noqa: BLE001
        pass
    # What the account manager knows about the client that the site does not
    # say: how they operate, what they may not claim, who signs the posts.
    from . import blog_spec
    ctx.update(blog_spec.guidance_payload(blog_settings(client, store)))
    return ctx


def blog_plan(client: str, focus: str, months: int = 3, start_date: str = "") -> dict:
    """Create (or replace) the next-N-months blog schedule with AI titles.

    Approved topics come first and are reproduced verbatim. A client who was
    sent a topic list in advance and signed it off must not open the blog and
    find twelve topics nobody showed them — so an approved title is used as
    written, and `source` on the post records which list it came from. With
    `approved_only` set, the schedule stops when the approved list runs out
    rather than topping itself up with invented topics.
    """
    from . import blog_spec
    store = load_store(client)
    setup = store.get("setup", {})
    settings = blog_settings(client, store)
    approved = [t for t in settings["approved_topics"] if str(t.get("title") or "").strip()]
    slots = _blog_schedule(setup, months, start_date)
    if settings["approved_only"] and approved:
        slots = slots[:len(approved)]
    ctx = _client_context(client, store)
    ctx["focus_areas"] = focus
    ctx["post_count"] = len(slots)
    ctx["publish_dates"] = [s["date"] for s in slots]
    ctx["approved_topics"] = approved[:len(slots)]

    posts_meta, questions, ai_error = [], [], ""
    try:
        out = _openai_json(_BLOG_PLAN_PROMPT, ctx) or {}
        posts_meta = out.get("posts") or []
        questions = [q for q in (out.get("questions") or []) if isinstance(q, str)][:6]
    except Exception as exc:  # noqa: BLE001
        ai_error = str(exc)

    # The approved titles are placed here, in code, rather than trusted to the
    # model: "use these titles as written" is a request, and a paraphrased
    # title is a topic the client did not approve.
    for i, topic in enumerate(approved[:len(slots)]):
        entry = dict(posts_meta[i]) if i < len(posts_meta) else {}
        entry["title"] = topic["title"]
        if topic.get("notes") and not entry.get("summary"):
            entry["summary"] = topic["notes"][:400]
        entry["source"] = "approved"
        if i < len(posts_meta):
            posts_meta[i] = entry
        else:
            posts_meta.append(entry)

    if len(posts_meta) < len(slots):        # fallback / top-up titles
        base = ctx.get("business_info", {}).get("category") or "your services"
        fillers = [
            f"How to choose the right {base} provider",
            f"5 questions to ask before hiring a {base} company",
            f"Seasonal {base} checklist",
            f"The real cost of {base}: what to expect",
            f"Common {base} mistakes and how to avoid them",
            f"{base.title()} FAQs answered by the pros",
        ]
        i = 0
        while len(posts_meta) < len(slots):
            posts_meta.append({"title": fillers[i % len(fillers)], "summary": ""})
            i += 1

    posts, known = [], list(settings["categories"])
    for i, slot in enumerate(slots):
        meta = posts_meta[i]
        title = str(meta.get("title") or f"Blog post {i+1}")
        tax = blog_spec.clamp_taxonomy(meta.get("categories"), meta.get("tags"), known)
        known = blog_spec.merge_categories(known, tax["categories"])
        posts.append({"id": i + 1, "date": slot["date"], "week": slot["week"],
                      "title": title,
                      "summary": str(meta.get("summary") or ""),
                      "slug": blog_spec.slugify_title(title),
                      "categories": tax["categories"], "tags": tax["tags"],
                      "source": meta.get("source") or "ai",
                      "content": "", "status": "planned"})
    blogs = store.setdefault("blogs", {})
    blogs.update({"focus": focus, "months": months, "posts": posts,
                  "questions": questions, "categories": known,
                  "answers": blogs.get("answers", {})})
    save_store(client, store)
    return {"posts": posts, "questions": questions, "categories": known,
            "approved_used": sum(1 for p in posts if p["source"] == "approved"),
            "approved_available": len(approved),
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


def blog_write(client: str, ids: list[int], limit: int = 3) -> dict:
    """Write full content for up to `limit` posts; call repeatedly for more.

    Every finished post is scanned against the client's never-mention list
    before it is saved. The prompt carries the list too, but a prompt is a
    request — the flags on the post are the evidence, and a flagged post
    reads as not ready to publish everywhere it appears.
    """
    from . import blog_spec
    store = load_store(client)
    blogs = store.get("blogs", {})
    settings = blog_settings(client, store)
    posts = blogs.get("posts", [])
    by_id = {p["id"]: p for p in posts}
    todo = [i for i in ids if i in by_id and by_id[i].get("status") != "written"][:limit]
    ctx = _client_context(client, store)
    ctx["focus_areas"] = blogs.get("focus", "")
    written, questions, ai_error = [], list(blogs.get("questions", [])), ""
    known = list(settings["categories"])
    flagged = 0
    for pid in todo:
        p = by_id[pid]
        payload = dict(ctx)
        payload["post_title"] = p["title"]
        payload["post_summary"] = p.get("summary", "")
        payload["publish_date"] = p["date"]
        payload["post_categories"] = p.get("categories") or []
        payload["post_tags"] = p.get("tags") or []
        out = None
        try:
            out = _openai_json(_BLOG_WRITE_PROMPT, payload)
        except Exception as exc:  # noqa: BLE001
            ai_error = str(exc)
        if out and out.get("html"):
            p["content"] = out["html"]
            p["meta_description"] = out.get("meta_description", "")
        else:                                   # fallback body
            bi = ctx.get("business_info", {})
            p["content"] = (
                f"<h1>{_html.escape(p['title'])}</h1>"
                f"<p>{_html.escape(p.get('summary') or 'Draft outline — add content here.')}</p>"
                "<h2>What to know</h2><p>(Write section content…)</p>"
                "<h2>Why it matters</h2><p>(Write section content…)</p>"
                f"<p>Contact {_html.escape(bi.get('name') or client)}"
                + (f" at {_html.escape(bi['phone'])}" if bi.get("phone") else "")
                + " to learn more.</p>")
        tax = blog_spec.clamp_taxonomy(
            (out or {}).get("categories") or p.get("categories"),
            (out or {}).get("tags") or p.get("tags"), known)
        known = blog_spec.merge_categories(known, tax["categories"])
        p["categories"], p["tags"] = tax["categories"], tax["tags"]
        p.setdefault("slug", blog_spec.slugify_title(p["title"]))
        # The check, not the request. Scanned over the meta description as
        # well: a forbidden claim in the search snippet is the one a customer
        # reads before they ever open the page.
        p["flags"] = blog_spec.scan_forbidden(
            (p["content"] or "") + " " + str(p.get("meta_description") or ""),
            settings["avoid"])
        flagged += 1 if p["flags"] else 0
        p["status"] = "written"
        written.append(p)
    blogs = store.setdefault("blogs", {})
    blogs["categories"] = known
    save_store(client, store)
    remaining = [i for i in ids if i in by_id and by_id[i].get("status") != "written"]
    return {"written": written, "remaining": remaining, "questions": questions,
            "categories": known, "flagged": flagged,
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


def blog_tag_posts(client: str, ids: list[int] | None = None) -> dict:
    """Fill in categories and tags on posts that have none.

    Re-planning would do it, but re-planning replaces every title and throws
    away written content — so a plan made before this existed had no way to
    gain a taxonomy at all, and the table read "not set" on every row with
    nothing to do about it. This touches ONLY the categories and tags.
    """
    from . import blog_spec
    store = load_store(client)
    blogs = store.setdefault("blogs", {})
    posts = blogs.get("posts", [])
    settings = blog_settings(client, store)
    wanted = set(ids or [])
    todo = [p for p in posts
            if (not wanted or p["id"] in wanted)
            and not p.get("archived")
            and not (p.get("categories") and p.get("tags"))]
    if not todo:
        return {"tagged": 0, "categories": settings["categories"],
                "posts": posts, "ai": False}

    known = list(settings["categories"])
    ctx = _client_context(client, store)
    ai_error, tagged = "", 0
    out = None
    try:
        # One request for the batch, not one per post: this is a short answer
        # per title and the failure mode of a slow loop is a rep watching a
        # spinner for twelve posts' worth of round trips.
        out = _openai_json(_BLOG_TAG_PROMPT, {
            "business_info": ctx.get("business_info", {}),
            "company_guidance": ctx.get("company_guidance", ""),
            "existing_categories": known,
            "taxonomy_rules": ctx.get("taxonomy_rules", ""),
            "posts": [{"id": p["id"], "title": p["title"],
                       "summary": p.get("summary", "")} for p in todo]})
    except Exception as exc:  # noqa: BLE001
        ai_error = str(exc)

    by_id = {}
    for row in (out or {}).get("posts", []) or []:
        if isinstance(row, dict) and str(row.get("id", "")).isdigit():
            by_id[int(row["id"])] = row
    for post in todo:
        row = by_id.get(post["id"], {})
        tax = blog_spec.clamp_taxonomy(
            row.get("categories") or post.get("categories"),
            row.get("tags") or post.get("tags"), known)
        if not tax["tags"]:
            # No AI and nothing stored: the title's own words beat an empty
            # column, and a person edits them in the modal.
            tax["tags"] = blog_spec.clamp_taxonomy(
                [], _title_keywords(post["title"]), known)["tags"]
        known = blog_spec.merge_categories(known, tax["categories"])
        post["categories"], post["tags"] = tax["categories"], tax["tags"]
        post.setdefault("slug", blog_spec.slugify_title(post["title"]))
        tagged += 1
    blogs["categories"] = known
    save_store(client, store)
    return {"tagged": tagged, "categories": known, "posts": posts,
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


_STOPWORDS = {"the", "and", "for", "with", "your", "you", "how", "what", "why",
              "when", "from", "that", "this", "are", "can", "should", "before",
              "after", "does", "did", "will", "into", "out", "about", "not",
              "its", "it", "a", "an", "of", "to", "in", "on", "is", "vs"}


def _title_keywords(title: str, limit: int = 4) -> list[str]:
    words = re.findall(r"[a-z][a-z0-9'-]{2,}", str(title or "").lower())
    return [w for w in dict.fromkeys(words) if w not in _STOPWORDS][:limit]


_BLOG_TAG_PROMPT = """You are an SEO content strategist filing a client's existing blog posts.
Given a list of planned post titles, assign each one categories and tags.
Rules:
- Output JSON only: {"posts": [{"id": int, "categories": [str], "tags": [str]}]}.
- Return one entry per post given, with the SAME id.
- Categories are the site's structure: reuse "existing_categories" wherever one fits and only
  invent a category when none of them does. Tags are per-post detail. Follow "taxonomy_rules".
- Do not rewrite, re-title or comment on the posts. Categories and tags only."""


def blogs_doc(client: str, ids=None) -> str:
    """Single Word-openable HTML document with the selected blog posts.

    Carries the author, the categories and the tags: this document is what a
    client approves and what a webmaster publishes from, and a post arriving
    at the CMS without them is filed as Uncategorised by whoever is quickest.
    """
    store = load_store(client)
    settings = blog_settings(client, store)
    author = settings["author"].get("name") or ""
    posts = store.get("blogs", {}).get("posts", [])
    if ids:
        posts = [p for p in posts if p["id"] in ids]
    parts = ["""<html xmlns:w="urn:schemas-microsoft-com:office:word"><head><meta charset="utf-8">
<title>Blog posts — """ + _html.escape(client) + """</title>
<style>body{font-family:Calibri,Segoe UI,sans-serif;max-width:760px;margin:24px auto;color:#1e293b;line-height:1.55}
h1{color:#1a2e58;font-size:22pt} h2{color:#1a2e58;font-size:14pt}
.blogmeta{color:#64748b;font-size:10pt;margin-bottom:14px}
.postbreak{page-break-before:always;border-top:2px solid #e2e8f0;margin-top:30px;padding-top:20px}</style></head><body>"""]
    parts.append(f"<p class='blogmeta'>Blog posts for <b>{_html.escape(client)}</b> — "
                 f"prepared by Smart 1 Marketing"
                 + (f" · author {_html.escape(author)}" if author else "") + "</p>")
    for i, p in enumerate(posts):
        wrap = "<div class='postbreak'>" if i else "<div>"
        body = p.get("content") or f"<h1>{_html.escape(p['title'])}</h1><p><i>Not written yet.</i></p>"
        meta = [_html.escape(p["week"]), "scheduled " + _html.escape(p["date"])]
        if p.get("categories"):
            meta.append("category: " + _html.escape(", ".join(p["categories"])))
        if p.get("tags"):
            meta.append("tags: " + _html.escape(", ".join(p["tags"])))
        if p.get("source") == "approved":
            meta.append("from the approved topic list")
        parts.append(f"{wrap}<p class='blogmeta'>{' · '.join(meta)}</p>")
        if p.get("flags"):
            # Never silently. This document is read by people who will publish
            # what is in front of them.
            terms = ", ".join(_html.escape(str(f.get("term"))) for f in p["flags"])
            parts.append("<p class='blogmeta' style='color:#b91c1c'><b>Check before "
                         f"publishing:</b> this copy mentions {terms}, which this "
                         "client asked us not to say.</p>")
        parts.append(f"{body}</div>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ------------------------------------------------------------ compiled file
def compiled_json(client: str) -> dict:
    store = load_store(client)
    approved = {u: p["schema"] for u, p in store.get("pages", {}).items()
                if p.get("approved")}
    return {"client": client, "generated_pages": len(store.get("pages", {})),
            "approved_pages": len(approved), "schemas": approved}


def compiled_html(client: str) -> str:
    """Copy-paste ready: one <script type="application/ld+json"> block per page,
    including the FAQPage markup from every saved FAQ page."""
    store = load_store(client)
    out = [f"<!-- JSON-LD schema for {client} — generated by Smart 1 Hub -->"]
    for url, p in store.get("pages", {}).items():
        if not p.get("approved"):
            continue
        out.append(f"\n<!-- ===== {url} ===== -->")
        out.append('<script type="application/ld+json">')
        out.append(json.dumps(p["schema"], indent=1))
        out.append("</script>")
    try:
        from . import faq as _faq
        faq_pages = _faq.list_pages(client)
    except Exception:                             # noqa: BLE001
        faq_pages = []
    if faq_pages:
        out.append("\n<!-- ===== FAQ pages ===== -->")
        for p in faq_pages:
            full = _faq.get_page(client, p["url"]) or p
            out.append(f"\n<!-- ===== {p['url']} (FAQ) ===== -->")
            out.append('<script type="application/ld+json">')
            out.append(json.dumps(full.get("schema") or _faq.faq_schema(full), indent=1))
            out.append("</script>")
    return "\n".join(out)
