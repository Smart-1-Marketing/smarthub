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
def _blogs_current(store: dict) -> bool:
    """Green when a plan exists and every post due by today is checked as
    posted on the site."""
    posts = store.get("blogs", {}).get("posts", [])
    if not posts:
        return False
    today = _dt_date_today_iso()
    due = [p for p in posts if str(p.get("date", "")) <= today]
    return all(p.get("posted") for p in due) if due else True


def _dt_date_today_iso() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


def client_status(store: dict) -> dict:
    checks = store.get("checks", {})
    return {
        "setup": bool(store.get("setup", {}).get("completed")),
        "schema": bool(checks.get("schema")),
        "listings": bool(checks.get("listings")),
        "blogs": _blogs_current(store),
    }


_SOCIAL_KEYS = ("facebook", "instagram", "linkedin", "twitter", "youtube",
                "tiktok", "pinterest", "yelp", "gbp")


def get_social(client: str, domain: str = "") -> dict:
    """Social URLs for a client — saved values, seeded from Brandfetch."""
    store = load_store(client)
    social = dict(store.get("social") or {})
    if not social:
        b = brand_for(client, domain) or {}
        raw = b.get("social") or {}
        keymap = {"facebookUrl": "facebook", "twitter": "twitter",
                  "linkedIn": "linkedin", "instagram": "instagram",
                  "youtube": "youtube", "pinterest": "pinterest"}
        for k, v in raw.items():
            nk = keymap.get(k, k.lower())
            if v and nk in _SOCIAL_KEYS:
                social[nk] = v
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
        if not k:
            continue
        if v:
            social[k] = v
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
    for k in ("platform",):
        if k in updates and str(updates[k]).strip():
            entry[k] = str(updates[k]).strip()
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
    notes.insert(0, {"time": _dt.datetime.now().strftime("%m/%d/%Y %I:%M %p"),
                     "author": author or "Team", "text": str(text or "").strip()[:2000]})
    del notes[100:]                       # keep the latest 100
    save_store(client, store)
    return get_profile(client)


# ------------------------------------------------------- brandfetch storage
def _brand_cache_path() -> str:
    return os.path.join(_store_base(), "_brand_by_domain.json")


def save_brandfetch(domain: str, payload: dict, client: str = ""):
    """Persist a Brandfetch result so every client form can autofill from it."""
    domain = str(domain or "").lower().removeprefix("www.")
    if domain:
        try:
            with open(_brand_cache_path(), encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
        cache[domain] = payload
        with _lock:
            with open(_brand_cache_path(), "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
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
    try:
        with open(_brand_cache_path(), encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        return None
    return cache.get(domain)


# ------------------------------------------------------------- client lists
def _client_websites(client: str) -> list[dict]:
    """Websites whose name/domain aligns with the client name (same loose
    match Client 360 uses), plus any website records manually attached to
    the client in the Hub."""
    ck = str(client).strip().lower()
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
            "status": client_status(store),
        })
    return out


def _client_base(client: str) -> dict:
    """The seo_clients() row for ONE client without building the whole list —
    keeps /api/seo/detail fast."""
    import datetime as _dt
    today = _dt.date.today()
    rows = [r for r in knack_data.products()
            if str(r.get("client", "")).strip().lower() == client.lower()
            and "seo" in str(r.get("product", "")).lower()
            and str(r.get("status", "")).strip().lower() == "live"]
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
            "blogs": blogs}


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
        "status": client_status(store),
        "social": get_social(client, (webs[0]["domain"] if webs else "")),
    })
    if full:                      # one round-trip for the whole SEO page
        remaining = [p for p in store.get("sitemap", [])
                     if p not in store.get("pages", {})]
        base["schema_pages_list"] = list(pages.values())
        base["schema_remaining"] = len(remaining)
        blogs = store.get("blogs", {})
        base["blog_posts"] = blogs.get("posts", [])
        base["blog_questions"] = blogs.get("questions", [])
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
        page = {"url": url, "title": facts.get("title", ""),
                "schema": out["schema"], "approved": False}
        store.setdefault("pages", {})[url] = page
        results.append(page)
    store["questions"] = questions[:20]
    save_store(client, store)
    return {"pages": results, "questions": store["questions"],
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


# ------------------------------------------------------------------ blogs
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
- Output JSON only: {"posts": [{"title": str, "summary": str}], "questions": [str]}.
- Produce EXACTLY the number of posts requested, in publish order.
- Titles must be specific, locally relevant, search-intent driven (how-to, cost guides, seasonal,
  comparisons, FAQs) — never generic filler like "Welcome to our blog".
- Respect the requested focus areas first; spread remaining posts across the client's services.
- Match topics to the season of the given publish dates when relevant.
- If information that would make the topics stronger is missing (services offered, service area,
  specials, target customers), add up to 4 short questions in "questions". Do not block on them."""

_BLOG_WRITE_PROMPT = """You are an SEO content writer for a local-business marketing agency.
Write ONE complete blog post for the client's website.
Rules:
- Output JSON only: {"html": str, "meta_description": str}.
- "html" is the post BODY only (no <html>/<head>): an <h1> title, short intro, <h2> sections,
  <ul> lists where useful, and a closing call-to-action paragraph mentioning the business name
  and phone/city when known. 600-900 words.
- Write naturally for humans first; work the topic's obvious search phrases into headings.
- NEVER invent facts, prices, certifications, awards or service claims not present in the
  provided information. Keep claims general when specifics are unknown."""


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
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _client_context(client: str, store: dict) -> dict:
    """Everything the AI should know about the client."""
    site = store.get("site_url") or ""
    if not site:
        rows = [c for c in seo_clients() if c["client"].lower() == client.lower()]
        site = rows[0]["url"] if rows else ""
    facts = {}
    if site:
        try:
            facts = _page_facts(site)
            facts["text"] = str(facts.get("text", ""))[:2500]
        except Exception:  # noqa: BLE001
            facts = {}
    return {"client_name": client, "website": site,
            "homepage_facts": {k: v for k, v in facts.items() if k != "error"},
            "business_info": master_business_info(client, store),
            "answered_questions": store.get("answers", {}),
            "blog_answers": store.get("blogs", {}).get("answers", {})}


def blog_plan(client: str, focus: str, months: int = 3, start_date: str = "") -> dict:
    """Create (or replace) the next-N-months blog schedule with AI titles."""
    store = load_store(client)
    setup = store.get("setup", {})
    slots = _blog_schedule(setup, months, start_date)
    ctx = _client_context(client, store)
    ctx["focus_areas"] = focus
    ctx["post_count"] = len(slots)
    ctx["publish_dates"] = [s["date"] for s in slots]

    posts_meta, questions, ai_error = [], [], ""
    try:
        out = _openai_json(_BLOG_PLAN_PROMPT, ctx) or {}
        posts_meta = out.get("posts") or []
        questions = [q for q in (out.get("questions") or []) if isinstance(q, str)][:6]
    except Exception as exc:  # noqa: BLE001
        ai_error = str(exc)

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

    posts = []
    for i, slot in enumerate(slots):
        posts.append({"id": i + 1, "date": slot["date"], "week": slot["week"],
                      "title": str(posts_meta[i].get("title") or f"Blog post {i+1}"),
                      "summary": str(posts_meta[i].get("summary") or ""),
                      "content": "", "status": "planned"})
    blogs = store.setdefault("blogs", {})
    blogs.update({"focus": focus, "months": months, "posts": posts,
                  "questions": questions,
                  "answers": blogs.get("answers", {})})
    save_store(client, store)
    return {"posts": posts, "questions": questions,
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


def blog_write(client: str, ids: list[int], limit: int = 3) -> dict:
    """Write full content for up to `limit` posts; call repeatedly for more."""
    store = load_store(client)
    blogs = store.get("blogs", {})
    posts = blogs.get("posts", [])
    by_id = {p["id"]: p for p in posts}
    todo = [i for i in ids if i in by_id and by_id[i].get("status") != "written"][:limit]
    ctx = _client_context(client, store)
    ctx["focus_areas"] = blogs.get("focus", "")
    written, questions, ai_error = [], list(blogs.get("questions", [])), ""
    for pid in todo:
        p = by_id[pid]
        payload = dict(ctx)
        payload["post_title"] = p["title"]
        payload["post_summary"] = p.get("summary", "")
        payload["publish_date"] = p["date"]
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
        p["status"] = "written"
        written.append(p)
    save_store(client, store)
    remaining = [i for i in ids if i in by_id and by_id[i].get("status") != "written"]
    return {"written": written, "remaining": remaining, "questions": questions,
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


def blogs_doc(client: str, ids=None) -> str:
    """Single Word-openable HTML document with the selected blog posts."""
    store = load_store(client)
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
                 f"prepared by Smart 1 Marketing</p>")
    for i, p in enumerate(posts):
        wrap = "<div class='postbreak'>" if i else "<div>"
        body = p.get("content") or f"<h1>{_html.escape(p['title'])}</h1><p><i>Not written yet.</i></p>"
        parts.append(f"{wrap}<p class='blogmeta'>{_html.escape(p['week'])} · scheduled {_html.escape(p['date'])}</p>{body}</div>")
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
