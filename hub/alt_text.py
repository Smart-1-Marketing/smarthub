"""Alt text: read what is on the site, rewrite it for search and for AI answers.

The Schema Builder and the FAQ Builder both work the same way — read the
client's own pages, produce something better, and hand it to a CMS. Alt text
was the gap. It is also the one an audit flags most often and the one nobody
gets round to, because fixing it means opening every page, finding every image
and writing a sentence about each.

## Why the first five pages, and not the whole sitemap

A sitemap crawl is one HTTP request per page against somebody else's server,
and a 200-page site is 200 requests before a single word is written. The first
five sitemap entries are the home page and the top-level service pages on
almost every site we build — the pages that carry the images worth describing.
`scan()` takes a limit so a second pass can go deeper deliberately, rather
than a default that hammers a client's host by accident.

## What "rewritten for SEO/AEO" has to mean in code

An AI answer engine reads alt text as a description of what the image *shows*,
in the context of the page it sits on. A search engine reads it the same way
and additionally penalises stuffing. Both of those are judgment calls, but
three things are not, and a model asked politely gets them wrong often enough
to matter — so they are enforced after the fact in `_clean_alt()`:

  * **Length.** Screen readers cut off around 125 characters and so do most
    engines. A 300-character alt is not more descriptive, it is truncated.
  * **The "image of" preamble.** Every screen reader already announces that it
    is an image. "Image of a technician" is read aloud as "image, image of a
    technician".
  * **Keyword stuffing.** A repeated town name in six alts on one page is the
    exact pattern that gets a page discounted.

A decorative image — a spacer, a divider, a background flourish — should carry
an EMPTY alt, not a description. That is a real answer, and `is_decorative`
carries it through so a well-meaning rewrite does not give a 1px shim a
sentence about air conditioning.
"""
from __future__ import annotations

import html as _html
import json
import os
import re

# Both engines truncate around here, and a screen reader stops listening.
MAX_ALT = 125
# One page, one crawl each. Five is the home page plus the service pages on
# nearly every site we build.
DEFAULT_PAGES = 5
MAX_PAGES = 25
# An image gallery page can carry a hundred images; a payload that size is
# neither reviewable nor affordable to rewrite.
MAX_IMAGES_PER_PAGE = 40

_PREAMBLE = re.compile(
    r"^\s*(an?\s+)?(image|photo(graph)?|picture|graphic|icon|pic)\s+"
    r"(of|showing|depicting|that shows|is)\b[:,\s]+", re.I)
# Filenames a builder emits for spacers, dividers and tracking pixels.
_DECORATIVE_HINT = re.compile(
    r"(spacer|divider|shim|pixel|blank|transparent|bg[-_]?pattern|"
    r"decoration|swirl|squiggle|1x1|placeholder)", re.I)


def _collapse(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_alt(value: str) -> str:
    """The three rules a model gets wrong often enough to enforce here."""
    text = _collapse(_html.unescape(str(value or "")))
    text = re.sub(r"<[^>]+>", "", text)
    prev = None
    while prev != text:                       # "photo of an image of ..."
        prev = text
        text = _PREAMBLE.sub("", text).strip()
    text = text.strip(" \"'`")
    if len(text) > MAX_ALT:
        # On a word boundary — a mid-word cut reads as a typo, and the point
        # of the cap is that everything after it is unread anyway.
        text = text[:MAX_ALT].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return text


def _filename_of(src: str) -> str:
    path = str(src or "").split("?")[0].split("#")[0]
    return path.rsplit("/", 1)[-1]


def is_decorative(img: dict) -> bool:
    """A spacer deserves an empty alt, not a sentence.

    `role="presentation"` and an explicitly empty alt are the author saying so
    outright; the filename hints and the tiny declared size are the cases where
    they did not but meant to.
    """
    if str(img.get("role") or "").lower() in ("presentation", "none"):
        return True
    if _DECORATIVE_HINT.search(_filename_of(img.get("src", ""))):
        return True
    try:
        w = int(str(img.get("width") or 0) or 0)
        h = int(str(img.get("height") or 0) or 0)
        if 0 < w <= 4 and 0 < h <= 4:
            return True
    except (TypeError, ValueError):
        pass
    return False


# ------------------------------------------------------------------ read
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")


def _attrs(tag: str) -> dict:
    out = {}
    for m in _ATTR.finditer(tag):
        out[m.group(1).lower()] = _html.unescape(
            m.group(3) if m.group(3) is not None
            else m.group(4) if m.group(4) is not None else (m.group(5) or ""))
    return out


def _absolute(src: str, page_url: str) -> str:
    from urllib.parse import urljoin
    src = str(src or "").strip()
    if not src or src.startswith("data:"):
        return src
    return urljoin(page_url, src)


def images_on(html: str, page_url: str = "") -> list[dict]:
    """Every <img> on the page, with what it already says about itself.

    `alt` absent and `alt=""` are different answers and are kept apart:
    an empty alt is a decision (this image is decorative), a missing one is an
    omission. Reporting both as "" would hide every genuinely missing alt in
    a list of images that were already handled correctly.
    """
    out = []
    for tag in _IMG_TAG.findall(html or ""):
        a = _attrs(tag)
        src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
        # A srcset-only image is real; take its first candidate.
        if not src and a.get("srcset"):
            src = a["srcset"].split(",")[0].strip().split(" ")[0]
        if not src or src.startswith("data:"):
            continue
        out.append({
            "src": _absolute(src, page_url),
            "filename": _filename_of(src),
            "alt": a.get("alt", ""),
            "has_alt": "alt" in a,
            "title": a.get("title", ""),
            "role": a.get("role", ""),
            "width": a.get("width", ""),
            "height": a.get("height", ""),
            "class": a.get("class", ""),
        })
        if len(out) >= MAX_IMAGES_PER_PAGE:
            break
    return out


def _page_text(html: str) -> dict:
    """Enough of the page for the writer to know what the image is FOR."""
    title = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    h1 = re.findall(r"<h1[^>]*>(.*?)</h1>", html or "", re.S | re.I)
    h2 = re.findall(r"<h2[^>]*>(.*?)</h2>", html or "", re.S | re.I)
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    body = _collapse(_html.unescape(re.sub(r"<[^>]+>", " ", body)))
    strip = lambda s: _collapse(_html.unescape(re.sub(r"<[^>]+>", " ", s)))  # noqa: E731
    return {"title": strip(title.group(1)) if title else "",
            "h1": [strip(x) for x in h1[:3]],
            "h2": [strip(x) for x in h2[:8]],
            "text": body[:1800]}


def scan(client: str, limit: int = DEFAULT_PAGES,
         urls: list[str] | None = None) -> dict:
    """Read the first N sitemap pages and list every image with its alt text.

    Rewrites nothing — this is the "here is what the site actually has" half,
    and it is worth having on its own: the count of images with no alt at all
    is the number an audit reports.
    """
    from . import seo
    limit = max(1, min(int(limit or DEFAULT_PAGES), MAX_PAGES))
    site = seo.client_site_url(client)
    store = seo.load_store(client)

    if urls:
        pages = [u for u in urls if str(u).startswith("http")][:limit]
    else:
        pages = list(store.get("sitemap") or [])[:limit]
        if not pages and site:
            try:
                found = seo.sitemap_pages(site)
                if found:
                    store["sitemap"] = found
                    seo.save_store(client, store)
                pages = found[:limit]
            except Exception:                           # noqa: BLE001
                pages = []
        if not pages and site:
            pages = [site]                              # at least the home page

    scanned, errors = [], []
    for url in pages:
        try:
            html = seo._fetch(url)
        except Exception as exc:                        # noqa: BLE001
            errors.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        imgs = images_on(html, url)
        for img in imgs:
            img["decorative"] = is_decorative(img)
            img["new_alt"] = ""
        scanned.append(dict(_page_text(html), url=url, images=imgs))

    total = sum(len(p["images"]) for p in scanned)
    missing = sum(1 for p in scanned for i in p["images"]
                  if not i["has_alt"] or not _collapse(i["alt"]))
    out = {"pages": scanned, "errors": errors, "site": site,
           "total_images": total, "missing_alt": missing,
           "scanned_at": _today()}
    alt = store.setdefault("alt_text", {})
    alt.update(out)
    seo.save_store(client, store)
    return out


def _today() -> str:
    import datetime as _dt
    return _dt.date.today().isoformat()


def load(client: str) -> dict:
    from . import seo
    data = seo.load_store(client).get("alt_text") or {}
    return {"pages": data.get("pages", []), "errors": data.get("errors", []),
            "site": data.get("site", ""),
            "total_images": data.get("total_images", 0),
            "missing_alt": data.get("missing_alt", 0),
            "scanned_at": data.get("scanned_at", ""),
            "written_at": data.get("written_at", "")}


# --------------------------------------------------------------- rewrite
_PROMPT = """You are an SEO and AEO (AI answer engine) specialist rewriting image alt text
for a local business website.

You are given one page — its title, headings and body text — and every image on it with the
alt text it currently has. Rewrite the alt text so it works for a screen reader, for image
search, and for an AI answer engine describing the page.

Rules:
- Output JSON only: {"images": [{"src": str, "alt": str, "decorative": bool, "why": str}]}.
- Return one entry per image given, with the SAME src, in the same order.
- Describe what the image actually shows, in the context of this page's subject. Lead with the
  subject, not with the business name.
- 8 to 16 words. Never more than 125 characters. Plain sentence case, no trailing full stop.
- NEVER begin with "image of", "photo of", "picture of" or "graphic of" — a screen reader
  already says it is an image.
- Work the page's real topic and, where it is genuinely visible or clearly implied, the service
  and the place into the wording — but only once across the whole page. Repeating the town name
  in every alt on one page is keyword stuffing and gets the page discounted.
- NEVER invent what is not evidently there: no prices, no certifications, no brand names, no
  claims about people (names, roles, qualifications) unless the page text states them.
- A purely decorative image — a spacer, a divider, a background flourish, a bullet — gets
  "decorative": true and an EMPTY alt. That is the correct answer for those, not a description.
- A logo's alt is the organisation's name plus "logo", nothing more.
- "why" is at most 12 words saying what you based it on. It is for the reviewer, not the site."""


def _openai_json(system: str, payload: dict, timeout: int = 90):
    """Same seam and the same spend accounting as hub/seo.py's writer."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    import requests
    r = requests.post("https://api.openai.com/v1/chat/completions",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json={"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                            "response_format": {"type": "json_object"},
                            "temperature": 0.4,
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": json.dumps(payload)}]},
                      timeout=timeout)
    r.raise_for_status()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("seo", r.json(), purpose="alt_text")
    except Exception:                                   # noqa: BLE001
        pass
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _fallback_alt(img: dict, page: dict) -> str:
    """Something honest when there is no AI key.

    Built from the filename and the page's own H1, because a filename is
    usually what somebody typed about the picture — and it is a starting point
    a person edits, never something to publish unread.
    """
    stem = re.sub(r"[-_]+", " ", os.path.splitext(img.get("filename", ""))[0])
    stem = _collapse(re.sub(r"\b(img|image|photo|dsc|final|copy|\d{3,})\b", " ",
                            stem, flags=re.I))
    topic = (page.get("h1") or [page.get("title", "")])[0] if page else ""
    if stem and topic:
        return _clean_alt(f"{stem} — {topic}")
    return _clean_alt(stem or topic or "")


def rewrite(client: str, urls: list[str] | None = None) -> dict:
    """Write new alt text for the scanned pages. One request per page.

    Per page rather than per site, so a page that fails costs its own images
    and not the other four — the same reason the blog writer runs one request
    per post.
    """
    from . import seo
    store = seo.load_store(client)
    data = store.get("alt_text") or {}
    pages = data.get("pages") or []
    if not pages:
        return {"error": "Nothing scanned yet — run the scan first."}
    wanted = {str(u) for u in (urls or [])}
    business = seo.master_business_info(client, store)

    written, ai_error = 0, ""
    for page in pages:
        if wanted and page.get("url") not in wanted:
            continue
        images = page.get("images") or []
        if not images:
            continue
        payload = {"page_url": page.get("url", ""),
                   "page_title": page.get("title", ""),
                   "headings": (page.get("h1") or []) + (page.get("h2") or []),
                   "page_text": page.get("text", ""),
                   "business": business,
                   "images": [{"src": i["src"], "filename": i.get("filename", ""),
                               "current_alt": i.get("alt", ""),
                               "looks_decorative": bool(i.get("decorative"))}
                              for i in images]}
        out = None
        try:
            out = _openai_json(_PROMPT, payload)
        except Exception as exc:                        # noqa: BLE001
            ai_error = str(exc)

        by_src = {}
        for row in (out or {}).get("images", []) or []:
            if isinstance(row, dict) and row.get("src"):
                by_src[str(row["src"])] = row

        for img in images:
            row = by_src.get(img["src"]) or {}
            decorative = bool(row.get("decorative")) or bool(img.get("decorative"))
            if decorative:
                # An empty alt is the right answer here, and it has to survive
                # the "if not value, fall back" reflex below.
                img["new_alt"] = ""
                img["decorative"] = True
                img["why"] = str(row.get("why") or "decorative — empty alt is correct")[:90]
                written += 1
                continue
            new = _clean_alt(row.get("alt") or "")
            if not new:
                new = _fallback_alt(img, page)
            img["new_alt"] = new
            img["why"] = str(row.get("why") or "")[:90]
            img["unchanged"] = bool(new) and new == _collapse(img.get("alt", ""))
            written += 1

    data["written_at"] = _today()
    store["alt_text"] = data
    seo.save_store(client, store)
    return {"pages": pages, "written": written,
            "ai": bool(os.environ.get("OPENAI_API_KEY")) and not ai_error,
            "ai_error": ai_error}


def set_alt(client: str, url: str, src: str, new_alt: str) -> dict | None:
    """Edit one rewritten alt by hand. Clamped exactly as the AI's is."""
    from . import seo
    store = seo.load_store(client)
    data = store.get("alt_text") or {}
    for page in data.get("pages", []):
        if page.get("url") != url:
            continue
        for img in page.get("images", []):
            if img.get("src") != src:
                continue
            img["new_alt"] = _clean_alt(new_alt)
            img["decorative"] = not img["new_alt"]
            store["alt_text"] = data
            seo.save_store(client, store)
            return img
    return None


# ----------------------------------------------------------------- output
def selected_pages(client: str, urls: list[str] | None = None) -> list[dict]:
    """Scanned pages, optionally narrowed, carrying only rewritten images.

    An image with no new alt is not part of a hand-off — sending it would ask
    somebody to paste an empty change over a real one.
    """
    data = load(client)
    wanted = {str(u) for u in (urls or [])}
    out = []
    for page in data.get("pages", []):
        if wanted and page.get("url") not in wanted:
            continue
        imgs = [i for i in page.get("images", [])
                if i.get("new_alt") or i.get("decorative")]
        if imgs:
            out.append({"url": page.get("url", ""), "title": page.get("title", ""),
                        "images": imgs})
    return out


def code_view(client: str, urls: list[str] | None = None) -> str:
    """The change as markup, for whoever edits the template directly.

    Both the old and the new tag, because a find-and-replace needs the string
    that is actually in the file, not a description of it.
    """
    pages = selected_pages(client, urls)
    if not pages:
        return "<!-- Nothing rewritten yet. Scan the pages, then write the alt text. -->"
    esc = _html.escape
    out = [f"<!-- Alt text for {esc(client)} — generated by Smart 1 Hub -->"]
    for page in pages:
        out.append(f"\n<!-- ===== {esc(page['url'])} ===== -->")
        for img in page["images"]:
            src = esc(img.get("src", ""), quote=True)
            was = esc(img.get("alt", ""), quote=True)
            now = esc(img.get("new_alt", ""), quote=True)
            out.append(f'\n<!-- was: <img src="{src}" alt="{was}"> -->')
            out.append(f'<img src="{src}" alt="{now}">'
                       + ("   <!-- decorative: an empty alt is correct -->"
                          if img.get("decorative") else ""))
    return "\n".join(out)
