"""Publishing instructions for the two CMS backends we actually work in.

Smart 1 Sites (the Simvoly whitelabel) and WordPress are where every blog post
and every JSON-LD block we produce has to be typed by a person. Neither has a
write API we can use here — Simvoly's public API covers projects, plans and
websites, not blog content, and a client's WordPress is somebody else's server
with somebody else's plugins on it — so the honest tool is not a publish
button. It is: open the right admin screen in a window, and put the exact
fields on the screen beside it, in the order the CMS asks for them, with a
copy button on each.

That is what this module builds. It knows nothing about how a post was
written; it turns a saved post (or a saved page schema) into the fields that
CMS wants, and it says out loud where a field has no equivalent — a tag list
handed to a builder with no tag field has to read as "there is nowhere to put
this", not vanish.

## Two rules the URLs here follow

**Nothing is invented.** With no site URL on the client there is no WordPress
admin to open, and this returns an empty `admin_url` with a warning naming
what is missing. A guessed `https://<clientname>.com/wp-admin` opens a
stranger's login page.

**Smart 1 Sites opens through the Hub, not through Simvoly.** The Sites Admin
module already holds every Simvoly project and already has the builder SSO, so
the project page is the address that gets a rep into the right builder without
a second password. Matching to a project is by **domain** — never by name —
for the reason `hub/sites_match.py` gives at length: "Riverside HVAC" and
"Riverside HVAC LLC" are two strings for one company, and a wrong match here
sends someone into another client's website.
"""
from __future__ import annotations

import json
import re

from .client_context import canonical_domain

SMART1 = "smart1"
WORDPRESS = "wordpress"
CMS_KEYS = (SMART1, WORDPRESS)
LABELS = {SMART1: "Smart 1 Sites", WORDPRESS: "WordPress"}

# Simvoly's blog editor is rich text with an embed/HTML element beside it, and
# it has categories but no separate tag field. Saying so is the point: the
# tags still travel, into the SEO keywords, rather than being dropped because
# there was no box for them.
_SMART1_HAS_TAGS = False


def label(cms: str) -> str:
    return LABELS.get(cms, cms or "")


def _origin(site_url: str) -> str:
    """https://domain.tld from anything that holds one, or ""."""
    domain = canonical_domain(site_url)
    if not domain:
        return ""
    scheme = "http://" if str(site_url or "").lower().startswith("http://") else "https://"
    return scheme + domain


def _simvoly_project(domain: str) -> dict:
    """The Sites Admin project whose website domain is this one, or {}.

    Read-only, domain-only, and it refuses an ambiguous answer: two projects
    on one domain is a real situation (a rebuild beside the live site) and
    picking one of them silently is how a rep edits the wrong one.
    """
    if not domain:
        return {}
    try:
        from modules.sites_admin import db as sdb
        rows, _total = sdb.query_projects(q=domain, page=1, per_page=25)
    except Exception:                                   # noqa: BLE001
        return {}
    hits = [r for r in (rows or [])
            if canonical_domain(r.get("domain") or "") == domain]
    if len(hits) != 1:
        return {"ambiguous": len(hits)} if len(hits) > 1 else {}
    row = hits[0]
    return {"project_id": str(row.get("project_id") or ""),
            "name": str(row.get("name") or ""),
            "domain": domain}


def admin_target(cms: str, site_url: str, kind: str = "blogs") -> dict:
    """Where the button should open, and what to say when it cannot.

    kind is "blogs" or "schema" — WordPress has a different first screen for
    each, and opening a rep on the wrong one costs two clicks every time.
    """
    domain = canonical_domain(site_url)
    warnings: list[str] = []
    if cms == WORDPRESS:
        origin = _origin(site_url)
        if not origin:
            warnings.append(
                "No website URL is saved for this client, so there is no "
                "WordPress admin to open. Add the site under Client Setup "
                "and this button will open it.")
            return {"url": "", "label": "", "warnings": warnings, "domain": ""}
        path = "/wp-admin/post-new.php" if kind == "blogs" else "/wp-admin/"
        return {"url": origin + path,
                "label": "WordPress admin — " + domain,
                "warnings": warnings, "domain": domain}

    project = _simvoly_project(domain)
    if project.get("project_id"):
        return {"url": "/sites/projects/" + project["project_id"],
                "label": "Smart 1 Sites — " + (project.get("name") or domain),
                "warnings": warnings, "domain": domain,
                "project_id": project["project_id"]}
    if project.get("ambiguous"):
        warnings.append(
            f"{project['ambiguous']} Smart 1 Sites projects share {domain}, so "
            "this opens the search rather than guessing which one to edit.")
    elif domain:
        warnings.append(
            f"No Smart 1 Sites project has {domain} on it. This opens the "
            "project search — if the site is ours, its domain has not been "
            "synced yet.")
    else:
        warnings.append("No website URL is saved for this client, so this "
                        "opens the Smart 1 Sites project list.")
    url = "/sites/" + (("?q=" + domain) if domain else "")
    return {"url": url, "label": "Smart 1 Sites — project list",
            "warnings": warnings, "domain": domain}


# ---------------------------------------------------------------- fields
def _field(fname: str, value, multiline: bool = False, note: str = "") -> dict:
    return {"label": fname, "value": "" if value is None else str(value),
            "multiline": bool(multiline), "note": note}


def _post_fields(post: dict, settings: dict, cms: str) -> list[dict]:
    from . import blog_spec
    author = blog_spec.normalise_author(settings.get("author"))
    cats = list(post.get("categories") or [])
    tags = list(post.get("tags") or [])
    slug = post.get("slug") or blog_spec.slugify_title(post.get("title", ""))
    out = [
        _field("Title", post.get("title", "")),
        _field("URL slug", slug),
        _field("Meta description", post.get("meta_description", ""),
               note="" if post.get("meta_description")
                    else "Not written — the writer produces one when the post "
                         "is written with AI."),
        _field("Categories", ", ".join(cats),
               note="" if cats else "None set — add one before publishing so "
                                    "the post is not filed as Uncategorised."),
    ]
    if cms == WORDPRESS or _SMART1_HAS_TAGS:
        out.append(_field("Tags", ", ".join(tags)))
    else:
        out.append(_field("Tags (no tag field here)", ", ".join(tags),
                          note="Smart 1 Sites blog posts have categories but "
                               "no separate tag field. Put these in the post's "
                               "SEO keywords, or skip them."))
    out.append(_field("Author", author.get("name") or "",
                      note="" if author.get("name")
                           else "No default author is set for this client — "
                                "set one in Blog settings."))
    if (post.get("image") or {}).get("url"):
        out.append(_field("Featured image", post["image"]["url"],
                          note="Upload this file as the post's featured image."))
    out.append(_field("Publish date", post.get("date", "")))
    out.append(_field("Post body (HTML)", post.get("content") or "", multiline=True,
                      note="" if post.get("content")
                           else "This post has not been written yet."))
    return out


def _schema_fields(page: dict) -> list[dict]:
    block = ('<script type="application/ld+json">\n'
             + json.dumps(page.get("schema") or {}, indent=1)
             + "\n</script>")
    return [_field("Page", page.get("url", "")),
            _field("Schema block", block, multiline=True)]


# ------------------------------------------------------------------ steps
def _blog_steps(cms: str) -> list[dict]:
    if cms == WORDPRESS:
        return [
            {"title": "Log in to the WordPress admin",
             "detail": "The window that just opened lands on Posts → Add New "
                       "once you are logged in. If it shows the login screen, "
                       "sign in with the site credentials saved under Client "
                       "Setup."},
            {"title": "Paste the title, then fix the slug",
             "detail": "WordPress generates a slug from the first title you "
                       "type and keeps it if you edit the title afterwards. "
                       "Open the Post tab in the right sidebar → URL and set "
                       "the slug given here."},
            {"title": "Paste the body as HTML, not as text",
             "detail": "Options (⋮, top right) → Code editor, paste the post "
                       "body, then switch back to the Visual editor to check "
                       "it. Pasting HTML into the visual editor publishes the "
                       "tags as visible text."},
            {"title": "Categories and tags",
             "detail": "Post tab → Categories: tick the categories listed "
                       "(use “Add New Category” for one that does not exist "
                       "yet). Tags: paste the tag list — WordPress splits it "
                       "on the commas."},
            {"title": "Author, featured image, meta description",
             "detail": "Post tab → Author: choose the author named here; the "
                       "person has to exist as a user on the site first. "
                       "Featured image: upload the image linked here. Meta "
                       "description: the Yoast or Rank Math box below the "
                       "editor, or the Excerpt field if neither plugin is "
                       "installed."},
            {"title": "Publish or schedule",
             "detail": "Schedule it for the date shown rather than publishing "
                       "everything today — a month of posts published in one "
                       "afternoon reads as what it is."},
        ]
    return [
        {"title": "Open the site in the builder",
         "detail": "The window that just opened is this client's project in "
                   "Smart 1 Sites. Use the builder button on that page — it "
                   "signs you in, so there is no second password."},
        {"title": "Blog → Posts → New post",
         "detail": "The blog panel is in the builder's left-hand menu. If the "
                   "site has no blog yet it has to be added to the site once "
                   "before any post can be created."},
        {"title": "Title and URL",
         "detail": "Paste the title, then set the post's URL slug in the "
                   "post settings panel rather than leaving the generated one."},
        {"title": "Paste the body",
         "detail": "The post editor is rich text. Paste the body HTML into an "
                   "HTML/embed element to keep the exact markup (headings and "
                   "lists are what the schema and the search snippet read), "
                   "or paste it as formatted text and re-apply the headings."},
        {"title": "Category, image and SEO",
         "detail": "Set the category in the post settings. Upload the "
                   "featured image. The meta description goes in the post's "
                   "SEO panel — the tag list has no field of its own here, so "
                   "it belongs in the SEO keywords or nowhere."},
        {"title": "Author and publish date",
         "detail": "Set the author to the name given here and schedule the "
                   "post for its date."},
    ]


def _schema_steps(cms: str) -> list[dict]:
    if cms == WORDPRESS:
        return [
            {"title": "Log in to the WordPress admin",
             "detail": "The window that just opened is this site's dashboard."},
            {"title": "Find where header code goes on this site",
             "detail": "In order of preference: an SEO plugin that takes "
                       "custom schema (Rank Math → Schema, Yoast → Schema), a "
                       "header-scripts plugin (WPCode, Insert Headers and "
                       "Footers), or the theme's own “header scripts” option. "
                       "Do not paste into Appearance → Theme File Editor — a "
                       "theme update overwrites it."},
            {"title": "Paste one block per page, on that page only",
             "detail": "Each block below belongs on the page named above it. "
                       "A per-page tool applies it to that page; a site-wide "
                       "header box needs the plugin's page condition set, or "
                       "every page claims to be every page."},
            {"title": "Check it",
             "detail": "Open the live page in Google's Rich Results Test. "
                       "Two conflicting blocks on one page is the usual "
                       "finding — if the theme already outputs schema, keep "
                       "one of them."},
        ]
    return [
        {"title": "Open the site in the builder",
         "detail": "The window that just opened is this client's project in "
                   "Smart 1 Sites. Use the builder button on that page."},
        {"title": "Open the page the block belongs to",
         "detail": "Schema is per page. Each block below names its page."},
        {"title": "Page settings → SEO / custom code",
         "detail": "Paste the block into that page's header/custom code box. "
                   "The site-wide settings box puts the same markup on every "
                   "page, which is wrong for anything but the Organization "
                   "block."},
        {"title": "Publish the site, then check it",
         "detail": "Builder changes are not live until the site is published. "
                   "Then run the page through Google's Rich Results Test."},
    ]


# ---------------------------------------------------------------- public
def blog_instructions(cms: str, posts: list, settings: dict,
                      site_url: str = "") -> dict:
    """Everything the publish panel shows for a set of blog posts."""
    if cms not in CMS_KEYS:
        return {"error": f"Unknown CMS '{cms}'."}
    target = admin_target(cms, site_url, "blogs")
    warnings = list(target["warnings"])
    items = []
    for p in posts or []:
        notes = []
        if p.get("status") != "written" or not p.get("content"):
            notes.append("Not written yet — write it before publishing.")
        for flag in (p.get("flags") or []):
            notes.append("Never-mention check: the copy contains "
                         f"“{flag.get('term')}”. Fix it before this goes live.")
        items.append({"id": p.get("id"), "title": p.get("title", ""),
                      "subtitle": p.get("date", ""),
                      "fields": _post_fields(p, settings or {}, cms),
                      "notes": notes})
    if not items:
        warnings.append("Nothing selected — tick the posts you want to publish.")
    return {"cms": cms, "label": label(cms), "kind": "blogs",
            "admin_url": target["url"], "admin_label": target["label"],
            "steps": _blog_steps(cms), "items": items, "warnings": warnings}


def schema_instructions(cms: str, pages: list, site_url: str = "") -> dict:
    """The same panel for JSON-LD page schema."""
    if cms not in CMS_KEYS:
        return {"error": f"Unknown CMS '{cms}'."}
    target = admin_target(cms, site_url, "schema")
    warnings = list(target["warnings"])
    items = []
    for page in pages or []:
        notes = []
        if not page.get("approved"):
            notes.append("Not approved yet — approve it in the Schema Builder "
                         "before it goes on the site.")
        items.append({"id": page.get("url"), "title": _short_url(page.get("url", "")),
                      "subtitle": ", ".join(page.get("types") or []),
                      "fields": _schema_fields(page), "notes": notes})
    if not items:
        warnings.append("Nothing selected — tick the pages you want to publish.")
    return {"cms": cms, "label": label(cms), "kind": "schema",
            "admin_url": target["url"], "admin_label": target["label"],
            "steps": _schema_steps(cms), "items": items, "warnings": warnings}


def _short_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "")) or "/"
