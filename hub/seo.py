"""SEO section — clients with SEO products, schema scanning + generation.

Data sources:
  * clients_app/data/products.json  -> live "Website SEO..." products + billing
  * clients_app/data/websites.json  -> the client's site, platform, GA / GTM ids

Per-client working files live at /var/data/seo/<slug>.json and hold the
client-setup answers, business info, AI questions/answers, and every
generated + approved page schema, so work survives restarts and deploys.
"""
import html as _html
import json
import os
import re
import threading
import xml.etree.ElementTree as ET

import requests

from . import knack_data

_lock = threading.Lock()

UA = {"User-Agent": "Mozilla/5.0 (compatible; Smart1Hub-SEO/1.0; +https://smart1marketing.com)"}


# ------------------------------------------------------------------ storage
def _store_base() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    path = os.path.join(base, "seo")
    os.makedirs(path, exist_ok=True)
    return path


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    return s[:80] or "client"


def load_store(client: str) -> dict:
    path = os.path.join(_store_base(), slugify(client) + ".json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"client": client, "setup": {}, "business_info": {},
                "questions": [], "answers": {}, "pages": {}, "sitemap": []}


def save_store(client: str, data: dict):
    data["client"] = client
    path = os.path.join(_store_base(), slugify(client) + ".json")
    with _lock:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)


# ------------------------------------------------------------- client lists
def _client_websites(client: str) -> list[dict]:
    """Websites whose name/domain aligns with the client name (same loose
    match Client 360 uses)."""
    ck = str(client).strip().lower()
    out = []
    for w in knack_data.websites():
        wk = str(w.get("name", "")).strip().lower()
        dk = str(w.get("domain", "")).strip().lower()
        if wk and (wk in ck or ck in wk):
            out.append(w)
        elif dk and re.sub(r"[^a-z0-9]", "", ck)[:12] and re.sub(r"[^a-z0-9]", "", ck)[:12] in re.sub(r"[^a-z0-9]", "", dk):
            out.append(w)
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


def seo_clients() -> list[dict]:
    """Clients with a live SEO product: url, billing, blog flag.

    Knack pre-creates renewal IOs marked Live (future start dates), so billing
    only counts the IO whose date range covers today — one per product."""
    import datetime as _dt
    today = _dt.date.today()
    live_rows: dict[str, list[dict]] = {}
    for r in knack_data.products():
        pname = str(r.get("product", "")).lower()
        if "seo" not in pname:
            continue
        if str(r.get("status", "")).strip().lower() != "live":
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
        })
    return out


def client_detail(client: str) -> dict:
    rows = [c for c in seo_clients() if c["client"].lower() == client.lower()]
    base = rows[0] if rows else {"client": client, "slug": slugify(client),
                                 "url": "", "platform": "", "products": [],
                                 "billing": 0, "blogs": False}
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
    store = load_store(client)
    pages = store.get("pages", {})
    base.update({
        "websites": webs,
        "setup": {k: v for k, v in store.get("setup", {}).items() if k != "password"},
        "setup_has_password": bool(store.get("setup", {}).get("password")),
        "business_info": store.get("business_info", {}),
        "questions": store.get("questions", []),
        "answers": store.get("answers", {}),
        "sitemap_total": len(store.get("sitemap", [])),
        "pages_generated": len(pages),
        "pages_approved": sum(1 for p in pages.values() if p.get("approved")),
    })
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


def generate_for_pages(client: str, urls: list[str]) -> dict:
    """Generate (or regenerate) schema for the given page urls."""
    store = load_store(client)
    business = store.get("business_info", {})
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
        page = {"url": url, "title": facts.get("title", ""),
                "schema": out["schema"], "approved": False}
        store.setdefault("pages", {})[url] = page
        results.append(page)
    store["questions"] = questions[:20]
    save_store(client, store)
    return {"pages": results, "questions": store["questions"],
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


# ------------------------------------------------------------ compiled file
def compiled_json(client: str) -> dict:
    store = load_store(client)
    approved = {u: p["schema"] for u, p in store.get("pages", {}).items()
                if p.get("approved")}
    return {"client": client, "generated_pages": len(store.get("pages", {})),
            "approved_pages": len(approved), "schemas": approved}


def compiled_html(client: str) -> str:
    """Copy-paste ready: one <script type="application/ld+json"> block per page."""
    store = load_store(client)
    out = [f"<!-- JSON-LD schema for {client} — generated by Smart 1 Hub -->"]
    for url, p in store.get("pages", {}).items():
        if not p.get("approved"):
            continue
        out.append(f"\n<!-- ===== {url} ===== -->")
        out.append('<script type="application/ld+json">')
        out.append(json.dumps(p["schema"], indent=1))
        out.append("</script>")
    return "\n".join(out)
