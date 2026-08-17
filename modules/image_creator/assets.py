"""Everything the editor can drop on a canvas that isn't a stock photo.

Icons come from Iconify, logos and brand colours from Brandfetch, and — the
part that makes this a Smart 1 tool rather than a generic editor — three
in-house asset sources the Hub already owns:

* **Gallery** — every image the SEO Image Pipeline has optimised, already
  named, already on Cloudinary, filterable by client.
* **Brand** — the Brandfetch logo and colour data cached per client.
* **Scans** — the screenshots and logos an Insites audit captured for a site.

That means opening the editor against a client gives you their real assets in
the sidebar instead of a blank canvas.
"""
from __future__ import annotations

import os
import re

import requests

TIMEOUT = 12
ICONIFY = "https://api.iconify.design"

ICON_CATEGORIES = [
    ("Business", "briefcase"), ("Automotive", "car"), ("Restaurant", "restaurant"),
    ("Home Services", "home-repair"), ("Medical", "medical"), ("Legal", "legal"),
    ("Weather", "weather"), ("Shopping", "shopping-cart"), ("Finance", "finance"),
    ("Communication", "phone"), ("Social Media", "social"), ("Arrows", "arrow"),
    ("People", "people"), ("Travel", "travel"),
]

POPULAR_FONTS = [
    "Inter", "Roboto", "Open Sans", "Montserrat", "Poppins", "Lato",
    "Oswald", "Playfair Display", "Raleway", "Nunito", "Merriweather",
    "Bebas Neue", "Anton", "Source Sans 3", "Work Sans", "Rubik",
    "DM Sans", "Archivo", "Barlow", "Manrope",
]


# -------------------------------------------------------------------- icons
def search_icons(q: str, limit: int = 60) -> dict:
    """Iconify needs no API key. Results are returned as icon names; the
    front end pulls each SVG from /api/icons/svg so nothing is rasterised."""
    q = (q or "").strip()
    if not q:
        return {"icons": []}
    try:
        r = requests.get(f"{ICONIFY}/search",
                         params={"query": q, "limit": max(32, min(int(limit), 999))},
                         timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:                          # noqa: BLE001
        return {"icons": [], "error": str(exc)}
    icons = []
    for name in data.get("icons", []):
        if ":" not in name:
            continue
        prefix, short = name.split(":", 1)
        icons.append({"name": name, "prefix": prefix, "icon": short,
                      "preview": f"{ICONIFY}/{prefix}/{short}.svg?height=48"})
    return {"icons": icons, "total": data.get("total", len(icons))}


def icon_svg(prefix: str, name: str, color: str = "", size: int = 256) -> str | None:
    """Raw SVG for one icon, recoloured server-side when asked."""
    if not re.match(r"^[a-z0-9-]+$", prefix or "") or not re.match(r"^[a-zA-Z0-9-]+$", name or ""):
        return None
    params = {"height": max(16, min(int(size), 1024))}
    if color and re.match(r"^#?[0-9a-fA-F]{3,8}$", color):
        params["color"] = color if color.startswith("#") else "#" + color
    try:
        r = requests.get(f"{ICONIFY}/{prefix}/{name}.svg", params=params, timeout=TIMEOUT)
        if r.ok and r.text.lstrip().startswith("<svg"):
            return r.text
    except Exception:                                 # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------- brands
def brand_lookup(query: str) -> dict:
    """Logos and brand colours for a company or domain, via Brandfetch.

    Falls back to whatever the Hub already cached for that domain, so a
    client whose brand was fetched during SEO setup works even without the
    key present.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "Enter a company name or domain."}
    domain = re.sub(r"^https?://", "", query.lower()).removeprefix("www.").split("/")[0]
    if "." not in domain:                             # a name, not a domain
        domain = ""

    key = (os.environ.get("BRANDFETCH_API_KEY") or "").strip()
    payload = None

    if domain and key:
        try:
            r = requests.get(f"https://api.brandfetch.io/v2/brands/{domain}",
                             headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
            # Counted against the monthly Brandfetch allowance. Only a real
            # HTTP call is billable — the Hub cache below is free, and is
            # recorded separately so you can see what the cache is saving.
            try:
                from hub import quotas as _q
                _q.record("brandfetch", module="image_creator", detail=domain)
            except Exception:                         # noqa: BLE001
                pass
            if r.ok:
                payload = r.json()
        except Exception:                             # noqa: BLE001
            payload = None

    if payload is None and domain:                    # the Hub's own cache
        try:
            from hub import seo
            cached = seo.brand_for("", domain)
            if cached:
                payload = cached
                try:
                    from hub import quotas as _q
                    _q.record("brandfetch", module="image_creator",
                              detail=domain, cached=True)
                except Exception:                     # noqa: BLE001
                    pass
        except Exception:                             # noqa: BLE001
            pass

    if payload is None:
        if not key:
            return {"error": "Brandfetch isn't configured (BRANDFETCH_API_KEY), "
                             "and nothing is cached for that domain."}
        return {"error": f"No brand found for “{query}”."}

    return {"name": payload.get("name") or domain, "domain": domain,
            "logos": _brand_logos(payload), "colors": _brand_colors(payload)}


def _brand_logos(payload: dict) -> list[dict]:
    """Flatten Brandfetch's nested logo/format structure, preferring SVG."""
    out = []
    for logo in payload.get("logos") or []:
        kind = logo.get("type") or "logo"            # logo | symbol | icon
        theme = logo.get("theme") or ""              # light | dark
        for fmt in logo.get("formats") or []:
            url = fmt.get("src")
            if not url:
                continue
            out.append({"url": url, "format": fmt.get("format", ""),
                        "type": kind, "theme": theme,
                        "width": fmt.get("width"), "height": fmt.get("height"),
                        "label": " ".join(x for x in (kind, theme) if x).title()})
    # a single flat logo url (the Hub's cached shape)
    if not out and payload.get("logo"):
        out.append({"url": payload["logo"], "format": "", "type": "logo",
                    "theme": "", "label": "Logo"})
    out.sort(key=lambda l: (l.get("format") != "svg", l.get("type") != "logo"))
    return out[:24]


def _brand_colors(payload: dict) -> list[dict]:
    out, seen = [], set()
    for c in payload.get("colors") or []:
        hexv = (c.get("hex") or "").strip() if isinstance(c, dict) else str(c)
        if not hexv:
            continue
        if not hexv.startswith("#"):
            hexv = "#" + hexv
        if hexv.lower() in seen:
            continue
        seen.add(hexv.lower())
        out.append({"hex": hexv,
                    "type": (c.get("type") if isinstance(c, dict) else "") or ""})
    return out[:12]


# ------------------------------------------------- the Hub's own assets
def gallery_assets(client: str = "", q: str = "", limit: int = 60) -> list[dict]:
    """Images already optimised by the SEO Image Pipeline."""
    try:
        from modules.seo_images import app as seo_images
    except Exception:                                 # noqa: BLE001
        return []
    rows = seo_images.load_archive()
    if client:
        rows = [r for r in rows if str(r.get("company", "")).lower() == client.lower()]
    if q:
        ql = q.lower()
        rows = [r for r in rows if any(
            ql in str(r.get(k, "")).lower()
            for k in ("seo_filename", "alt_text", "project", "page", "company"))]
    return [{"url": r.get("url", ""), "thumbnail": r.get("url", ""),
             "name": r.get("filename", ""), "alt": r.get("alt_text", ""),
             "client": r.get("company", ""), "project": r.get("project", ""),
             "width": r.get("width"), "height": r.get("height"),
             "source": "gallery"} for r in rows[:limit] if r.get("url")]


def scan_assets(client: str = "", domain: str = "", limit: int = 40) -> list[dict]:
    """Screenshots and imagery captured by an Insites audit."""
    try:
        from modules.scans import app as scans
    except Exception:                                 # noqa: BLE001
        return []
    out = []
    db = scans.SessionLocal()
    try:
        query = db.query(scans.Scan).filter(scans.Scan.status == "complete")
        if domain:
            query = query.filter(scans.Scan.domain_key == scans.domain_key(domain))
        elif client:
            like = f"%{client.lower()}%"
            from sqlalchemy import func, or_
            query = query.filter(or_(
                func.lower(scans.Scan.business_name).like(like),
                func.lower(scans.Scan.detected_name).like(like)))
        for s in query.order_by(scans.Scan.created_at.desc()).limit(12).all():
            try:
                import json as _json
                report = _json.loads(s.raw_report) if s.raw_report else {}
            except (TypeError, ValueError):
                continue
            for url in _harvest_images(report)[:limit]:
                out.append({"url": url, "thumbnail": url, "source": "scan",
                            "name": s.domain_key, "client": s.business_name or "",
                            "alt": f"From the {s.domain_key} site audit"})
    finally:
        db.close()
    seen, uniq = set(), []
    for a in out:
        if a["url"] in seen:
            continue
        seen.add(a["url"])
        uniq.append(a)
    return uniq[:limit]


def _harvest_images(node, found=None, depth=0) -> list[str]:
    """Pull image URLs out of an arbitrarily-shaped audit payload."""
    if found is None:
        found = []
    if depth > 6 or len(found) > 60:
        return found
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and _looks_like_image(v, k):
                found.append(v)
            else:
                _harvest_images(v, found, depth + 1)
    elif isinstance(node, list):
        for v in node[:40]:
            _harvest_images(v, found, depth + 1)
    return found


def _looks_like_image(value: str, key: str = "") -> bool:
    if not value.startswith("http") or len(value) > 600:
        return False
    if re.search(r"\.(png|jpe?g|webp|gif|svg)(\?|$)", value, re.I):
        return True
    return bool(re.search(r"screenshot|thumbnail|image|logo|preview", key, re.I)) \
        and "//" in value


# -------------------------------------------------------------------- fonts
def font_list(q: str = "") -> list[str]:
    """Google Fonts. With a key we return the live catalogue; without one the
    curated list still covers everything the outline asks for."""
    key = (os.environ.get("GOOGLE_FONTS_API_KEY") or "").strip()
    fonts = list(POPULAR_FONTS)
    if key:
        try:
            r = requests.get("https://www.googleapis.com/webfonts/v1/webfonts",
                             params={"key": key, "sort": "popularity"}, timeout=TIMEOUT)
            if r.ok:
                fonts = [f["family"] for f in r.json().get("items", [])][:400]
        except Exception:                             # noqa: BLE001
            pass
    if q:
        ql = q.lower()
        fonts = [f for f in fonts if ql in f.lower()]
    return fonts[:200]
