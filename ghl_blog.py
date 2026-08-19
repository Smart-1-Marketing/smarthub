"""Publish generated content to Smart 1 Suite as a blog post.

## Why a blog post rather than a website page

The Funnels API is read-only — `GET /funnels`, `GET /funnels/page` and a page
count, nothing that creates or edits page content. So "add a row, add a rich
text block" cannot be automated: HighLevel simply doesn't expose the builder.

The Blogs API does support writes: `blogs/post.write` creates a post and
`blogs/post-update.write` edits one. A post gives us everything the page
approach was for — a title, rich content, a controllable slug, a publish
state, and a public URL on the blog's own domain. The existing sample at
news.smart1marketing.com is itself a blog post, so this is the same shape you
already publish.

## Scopes the Private Integration Token needs

    blogs/list.readonly        find the blog site
    blogs/author.readonly      posts require an author id
    blogs/category.readonly    posts require at least one category
    blogs/check-slug.readonly  avoid colliding with an existing post
    blogs/post.write           create
    blogs/post-update.write    update an existing one instead of duplicating

Missing any of these produces a 401 from HighLevel that looks like a bad
token, so `check_access()` reports which specific scope failed rather than
leaving you guessing.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

import requests

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
TIMEOUT = 25


class BlogError(RuntimeError):
    """Message is safe to show a user — never contains the token."""


def _token() -> str:
    for name in ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"):
        v = (os.environ.get(name) or "").strip().strip('"').strip("'")
        # A placeholder is worse than nothing: it looks configured and 401s.
        if v and v != "pit-...":
            return v
    return ""


def _location() -> str:
    for name in ("GHL_BLOG_LOCATION_ID", "SMART1_LOCATION_ID",
                 "GHL_COMPANY_ID", "SUITE_COMPANY_ID"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def _headers() -> dict:
    tok = _token()
    if not tok:
        raise BlogError("No Smart 1 Suite token is configured. Set "
                        "GHL_PRIVATE_TOKEN in Render.")
    return {"Authorization": f"Bearer {tok}", "Version": VERSION,
            "Accept": "application/json", "Content-Type": "application/json"}


def _call(method: str, path: str, **kw):
    try:
        r = requests.request(method, f"{BASE}{path}", headers=_headers(),
                             timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise BlogError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
    if r.status_code in (401, 403):
        # Deliberately does not echo the body: HighLevel errors have included
        # token fragments, and this response reaches a browser.
        raise BlogError(
            f"Smart 1 Suite rejected the request ({r.status_code}). The "
            f"Private Integration Token is probably missing a blogs scope — "
            f"it needs blogs/list.readonly, blogs/author.readonly, "
            f"blogs/category.readonly, blogs/check-slug.readonly, "
            f"blogs/post.write and blogs/post-update.write.")
    if not r.ok:
        raise BlogError(f"Smart 1 Suite returned HTTP {r.status_code} for "
                        f"{path}.")
    try:
        return r.json()
    except ValueError:
        return {}


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s[:80] or "llm-text"


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def list_blogs(location_id: str = "") -> list[dict]:
    loc = location_id or _location()
    if not loc:
        raise BlogError("No Suite location id. Set GHL_BLOG_LOCATION_ID to the "
                        "sub-account that owns the blog.")
    data = _call("GET", "/blogs/site/all",
                 params={"locationId": loc, "limit": 50, "skip": 0})
    return data.get("data") or data.get("blogs") or []


def authors(location_id: str = "") -> list[dict]:
    loc = location_id or _location()
    data = _call("GET", "/blogs/authors",
                 params={"locationId": loc, "limit": 50, "offset": 0})
    return data.get("authors") or data.get("data") or []


def categories(location_id: str = "") -> list[dict]:
    loc = location_id or _location()
    data = _call("GET", "/blogs/categories",
                 params={"locationId": loc, "limit": 50, "offset": 0})
    return data.get("categories") or data.get("data") or []


def slug_taken(slug: str, location_id: str = "", post_id: str = "") -> bool:
    loc = location_id or _location()
    params = {"locationId": loc, "urlSlug": slug}
    if post_id:
        params["postId"] = post_id
    try:
        data = _call("GET", "/blogs/posts/url-slug-exists", params=params)
    except BlogError:
        return False        # if the check is unavailable, let create decide
    return bool(data.get("exists"))


def check_access(location_id: str = "") -> dict:
    """Confirm every scope this needs, naming the one that fails.

    A 401 from HighLevel is identical whether the token is wrong or merely
    missing one scope, so testing each read separately is the only way to
    give an actionable answer.
    """
    loc = location_id or _location()
    out = {"token_set": bool(_token()), "location_id": loc, "checks": {}}
    if not out["token_set"] or not loc:
        out["ok"] = False
        out["problem"] = ("Set GHL_PRIVATE_TOKEN and GHL_BLOG_LOCATION_ID "
                          "before testing.")
        return out
    for label, fn in (("blogs/list.readonly", list_blogs),
                      ("blogs/author.readonly", authors),
                      ("blogs/category.readonly", categories)):
        try:
            out["checks"][label] = {"ok": True, "count": len(fn(loc))}
        except BlogError as exc:
            out["checks"][label] = {"ok": False, "detail": str(exc)[:200]}
    out["ok"] = all(c.get("ok") for c in out["checks"].values())
    out["note"] = ("Write scopes (blogs/post.write, blogs/post-update.write) "
                   "can't be verified without creating a post — publish one "
                   "draft to confirm them.")
    return out


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def _as_html(text: str) -> str:
    """Wrap plain text so it renders readably in the blog's rich-text field.

    <pre> rather than <p>: an llms.txt is whitespace-significant, and the
    markdown-ish structure is part of what makes it parseable. Reflowing it
    into paragraphs would destroy the thing being published.
    """
    esc = (str(text or "").replace("&", "&amp;")
           .replace("<", "&lt;").replace(">", "&gt;"))
    return ('<pre style="white-space:pre-wrap;word-wrap:break-word;'
            'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;'
            'font-size:14px;line-height:1.6">' + esc + "</pre>")


def publish_llms_txt(client: str, text: str, blog_id: str = "",
                     location_id: str = "", post_id: str = "",
                     status: str = "PUBLISHED") -> dict:
    """Create or update the LLM Text post for a client, and return its URL."""
    loc = location_id or _location()
    if not text.strip():
        raise BlogError("There's nothing to publish.")

    blogs = list_blogs(loc)
    if not blogs:
        raise BlogError("No blog site found on that Suite location.")
    blog = next((b for b in blogs if b.get("_id") == blog_id or b.get("id") == blog_id),
                blogs[0])
    bid = blog.get("_id") or blog.get("id")

    auth_list = authors(loc)
    cat_list = categories(loc)
    if not auth_list:
        raise BlogError("That blog has no authors. Add one in Suite first — "
                        "a post can't be created without an author.")
    if not cat_list:
        raise BlogError("That blog has no categories. Add one in Suite first.")

    title = f"LLM Text ({client})"
    slug = slugify(f"llm-text-{client}")
    if not post_id and slug_taken(slug, loc):
        # Don't silently create a second post under a suffixed slug — that
        # leaves two files claiming to describe the same client.
        raise BlogError(
            f"A post already exists at '{slug}'. Open it in Suite and pass its "
            f"post id to update it, rather than publishing a duplicate.")

    body = {
        "title": title,
        "locationId": loc,
        "blogId": bid,
        "rawHTML": _as_html(text),
        "status": status,
        "urlSlug": slug,
        "author": (auth_list[0].get("_id") or auth_list[0].get("id")),
        "categories": [cat_list[0].get("_id") or cat_list[0].get("id")],
        "description": (f"Structured business information about {client} for "
                        f"AI systems and language models."),
        "publishedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    if post_id:
        data = _call("PUT", f"/blogs/posts/{post_id}", json=body)
    else:
        data = _call("POST", "/blogs/posts", json=body)

    post = data.get("data") or data
    pid = post.get("_id") or post.get("id") or post_id
    domain = (blog.get("domain") or blog.get("blogDomain") or "").rstrip("/")
    url = f"https://{domain}/{slug}" if domain else ""

    try:
        from hub import audit
        audit.log("suite", "llms_txt_published", client=client,
                  post=pid, status=status)
    except Exception:                                   # noqa: BLE001
        pass

    return {"ok": True, "post_id": pid, "blog_id": bid, "slug": slug,
            "url": url, "title": title, "status": status,
            "note": "" if url else
                    "Published, but the blog has no custom domain set so no "
                    "public URL could be built. Add one in Suite."}
