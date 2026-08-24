"""Publishing through Claude in Chrome, for the two CMSes we actually work in.

Smart 1 Sites (the Simvoly whitelabel) and WordPress both hold content we
produce — blog posts, JSON-LD, FAQ accordions, image alt text — and neither
has a write API we can use. Simvoly's public API covers projects, plans and
websites, not page content; a client's WordPress is somebody else's server
with somebody else's plugins on it.

So the tool is not a publish button and it is not a list of fields to retype
either. **It is a prompt.** The rep opens Chrome with the Claude extension,
signs in to the CMS themselves, pastes what this module writes, and Claude
drives the CMS from there.

That changes what a good output looks like:

* **The prompt carries the content, not a description of it.** "Add the blog
  post" is useless; the whole body HTML, the slug, the categories and the
  author have to be in the text that gets pasted, because the browser agent
  has no access to this Hub.

* **It carries the rules that stop it improvising.** Approved copy must be
  reproduced, not paraphrased. A missing field must be reported, not guessed
  at. A category that does not exist must be created with the exact name
  rather than filed under the nearest match. An agent left to its own judgment
  on any of those produces something plausible that nobody approved.

* **It never carries a credential.** This Hub stores the site login and
  password under Client Setup, and interpolating them into a block of text
  destined for a chat window would be the easiest possible mistake to make.
  The human signs in; the prompt says so and tells the agent not to ask.

## Two rules the URLs here follow

**Nothing is invented.** With no site URL on the client there is no WordPress
admin to open, and this returns an empty `admin_url` with a warning naming
what is missing. A guessed `https://<clientname>.com/wp-admin` opens a
stranger's login page.

**Smart 1 Sites opens through the Hub, not through Simvoly.** Sites Admin
already holds every Simvoly project and already has the builder SSO, so the
project page is the address that gets a rep into the right builder without a
second password. Matching to a project is by **domain** — never by name — for
the reason `hub/sites_match.py` gives at length: "Riverside HVAC" and
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
KINDS = ("blogs", "schema", "faqs", "alt")
KIND_LABELS = {"blogs": "blog posts", "schema": "page schema",
               "faqs": "FAQ sections", "alt": "image alt text"}
# "add the 1 blog posts below" reads as a bug in the tool that wrote it, which
# is not the impression to give the thing about to edit a client's website. Each
# kind gets its own sentence rather than a count wedged into one noun phrase.
_TASK = {
    "blogs": lambda n: f"add the {n} blog post{'s' if n != 1 else ''} below to this site",
    "schema": lambda n: f"add the schema block below to {n} page{'s' if n != 1 else ''} of this site",
    "faqs": lambda n: f"add the FAQ section below to {n} page{'s' if n != 1 else ''} of this site",
    "alt": lambda n: f"update the image alt text on {n} page{'s' if n != 1 else ''} of this site",
}

# Simvoly's blog editor is rich text with an embed/HTML element beside it, and
# it has categories but no separate tag field. Saying so is the point: the tags
# still travel, into the SEO keywords, rather than being dropped because there
# was no box for them.
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


def _short_url(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "")) or "/"


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
    """Where the button opens, and what to say when it cannot.

    WordPress has a different first screen per kind and landing a rep on the
    wrong one costs two clicks every time.
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


# ------------------------------------------------------- the Chrome recipe
def chrome_steps(cms: str, kind: str) -> list[dict]:
    """The four things the person does. The fifth is Claude's.

    Identical for every CMS and every kind on purpose — the variation belongs
    inside the prompt, where the agent reads it, not in a checklist that
    changes shape every time somebody uses it.
    """
    name = label(cms)
    return [
        {"title": "Open Chrome with the Claude extension",
         "detail": "Claude for Chrome has to be installed and signed in on "
                   "this browser. Nothing below works in another browser, and "
                   "nothing below is done by this Hub."},
        {"title": f"Sign in to {name} yourself",
         "detail": "The window that just opened is the login. Use the "
                   "credentials saved on this client under Client Setup. Sign "
                   "in BEFORE you paste anything — the prompt tells Claude you "
                   "are already signed in, and it never contains a password."},
        {"title": "Paste the prompt below into Claude, in that tab",
         "detail": "Open the Claude side panel on the CMS tab and paste it "
                   "whole. It carries the finished content, so nothing else "
                   "has to be copied across afterwards."},
        {"title": "Approve each step, and check before it goes live",
         "detail": "Claude asks before it acts on a page. Read what it is "
                   "about to do. The prompt tells it to leave everything as a "
                   "draft and to stop and ask rather than guess — if it asks a "
                   "question, answer it here rather than letting it choose."},
    ]


_RULES = [
    "Work through the items below one at a time, in the order given.",
    "Reproduce every value EXACTLY as written. Do not rewrite, shorten, "
    "re-title, summarise or otherwise improve any of it — a human has "
    "approved this copy as it stands.",
    "Do not invent anything. If a value is not given below, leave that field "
    "as the site already has it.",
    "If a field below has no equivalent on this site, tell me it has nowhere "
    "to go. Do not put it in a different field instead.",
    "Leave everything as a draft or unpublished, and tell me when it is ready "
    "for me to review. Do not publish, and do not push the site live.",
    "Stop and ask me if what is on screen does not match what you expected, "
    "or if you would have to guess to continue.",
    "I am already signed in. Do not sign in, sign out, change any account "
    "setting, or ask me for a password.",
]


def _cms_notes(cms: str, kind: str) -> list[str]:
    """What this particular CMS needs the agent to know."""
    if cms == WORDPRESS:
        base = {
            "blogs": [
                "This is WordPress. New posts are at Posts -> Add New.",
                "WordPress builds the slug from the FIRST title typed and "
                "keeps it if the title changes later, so set the slug "
                "explicitly in the Post tab -> URL after typing the title.",
                "Paste the body through Options (the three dots, top right) "
                "-> Code editor. Pasting HTML into the visual editor publishes "
                "the tags as visible text.",
                "Categories and tags are in the Post tab of the right-hand "
                "sidebar. Use 'Add New Category' for a category that does not "
                "exist yet, with exactly the name given.",
                "The meta description belongs in the Yoast or Rank Math box "
                "below the editor. If neither plugin is installed, put it in "
                "the Excerpt field and tell me you did.",
                "The author has to already exist as a user on the site. If the "
                "name given is not in the Author dropdown, tell me — do not "
                "create a user and do not pick somebody else.",
            ],
            "schema": [
                "This is WordPress. JSON-LD goes in, in this order of "
                "preference: an SEO plugin that accepts custom schema (Rank "
                "Math -> Schema, Yoast -> Schema), then a header-scripts "
                "plugin (WPCode, Insert Headers and Footers), then the "
                "theme's own header-scripts option.",
                "Never paste into Appearance -> Theme File Editor. A theme "
                "update overwrites it and the markup disappears silently.",
                "Each block belongs on ONE page. If you are using a site-wide "
                "header box, set that tool's page condition so the block only "
                "loads on the page named. Without a condition every page "
                "claims to be every page.",
                "If the theme or an SEO plugin already outputs schema for a "
                "page, tell me before adding a second block rather than "
                "leaving two conflicting graphs on it.",
            ],
            "faqs": [
                "This is WordPress. Edit the page named for each FAQ section.",
                "Add the accordion with a Custom HTML block, or through "
                "Options -> Code editor. The block is self-contained — it "
                "carries its own CSS and needs no plugin and no JavaScript.",
                "Put it where the questions belong on the page, usually below "
                "the main content and above the footer. Ask me if that is not "
                "obvious on a given page.",
                "The block already contains its FAQPage JSON-LD. Do not add "
                "the schema separately as well, and tell me if an SEO plugin "
                "on this site is already producing FAQ schema for the page.",
            ],
            "alt": [
                "This is WordPress. Alt text can be changed in two places and "
                "they are not the same thing: the Media Library entry (the "
                "default for future uses) and the individual image block on "
                "the page (this use only).",
                "Update the image block on the page named, and update the "
                "Media Library entry too when the file is used only there.",
                "Match each image by the filename in its src. If more than one "
                "image on the page matches, or none does, tell me rather than "
                "picking one.",
                "Change nothing else: not the image, not the file, not its "
                "size, caption, title or link.",
            ],
        }
        return base.get(kind, base["blogs"])

    base = {
        "blogs": [
            "This is Smart 1 Sites, a Simvoly whitelabel. The page that just "
            "opened is the client's project in our admin — use the builder "
            "button on it to open the site editor.",
            "Blog posts are under Blog -> Posts -> New post in the builder's "
            "left-hand menu. If this site has no blog section yet, stop and "
            "tell me — adding one changes the site's structure and is my call.",
            "Set the post's URL slug in the post settings rather than "
            "accepting the one generated from the title.",
            "The post editor is rich text. Paste the body into an HTML or "
            "embed element so the headings and lists survive exactly; if you "
            "paste it as text instead, re-apply every heading level and tell "
            "me you did.",
            "This builder has categories but NO tag field. Put the tags in the "
            "post's SEO keywords if there is such a box, and tell me if there "
            "is not — do not put them in the body or the category.",
            "The meta description is in the post's own SEO panel.",
            "Builder changes are not live until the site is published. Do not "
            "publish the site.",
        ],
        "schema": [
            "This is Smart 1 Sites, a Simvoly whitelabel. Open the project "
            "that just opened in our admin, then the site builder.",
            "Schema is per page: open the page each block names, then that "
            "page's settings -> SEO / custom code, and paste the block into "
            "its header code box.",
            "Do NOT use the site-wide code box. It puts the same markup on "
            "every page, which is wrong for everything except a single "
            "Organization block.",
            "Builder changes are not live until the site is published. Do not "
            "publish the site — tell me when the blocks are in.",
        ],
        "faqs": [
            "This is Smart 1 Sites, a Simvoly whitelabel. Open the project "
            "that just opened in our admin, then the site builder.",
            "Open the page named for each FAQ section and add an HTML or embed "
            "element where the questions belong, usually below the main "
            "content.",
            "Paste the block in as it is. It is self-contained — its own CSS, "
            "no JavaScript, no plugin — and it already carries its FAQPage "
            "JSON-LD, so do not also paste the schema separately.",
            "Builder changes are not live until the site is published. Do not "
            "publish the site.",
        ],
        "alt": [
            "This is Smart 1 Sites, a Simvoly whitelabel. Open the project "
            "that just opened in our admin, then the site builder.",
            "Open the page named, select each image element, and change its "
            "alt text in the image settings panel.",
            "Match each image by the filename in its src. If more than one "
            "image on the page matches, or none does, tell me rather than "
            "picking one.",
            "Change nothing else: not the image, not the file, not its size, "
            "caption or link.",
            "Builder changes are not live until the site is published. Do not "
            "publish the site.",
        ],
    }
    return base.get(kind, base["blogs"])


def _block(title: str, value: str) -> str:
    value = str(value or "").strip()
    return f"{title}:\n{value}\n" if "\n" in value else f"{title}: {value}\n"


def _blog_body(post: dict, settings: dict) -> str:
    from . import blog_spec
    author = blog_spec.normalise_author((settings or {}).get("author"))
    slug = post.get("slug") or blog_spec.slugify_title(post.get("title", ""))
    out = [_block("Title", post.get("title", "")),
           _block("URL slug", slug),
           _block("Meta description", post.get("meta_description", "")),
           _block("Categories", ", ".join(post.get("categories") or [])),
           _block("Tags", ", ".join(post.get("tags") or [])),
           _block("Author", author.get("name", "")),
           _block("Schedule for", post.get("date", ""))]
    image = (post.get("image") or {}).get("url")
    if image:
        out.append(_block("Featured image (download this URL and upload it as "
                          "the featured image)", image))
    out.append("Body HTML — paste exactly, do not edit:\n"
               + str(post.get("content") or "").strip() + "\n")
    return "".join(out)


def _schema_body(page: dict) -> str:
    block = ('<script type="application/ld+json">\n'
             + json.dumps(page.get("schema") or {}, indent=1)
             + "\n</script>")
    return (_block("Page", page.get("url", ""))
            + "Paste this block, exactly as it is:\n" + block + "\n")


def _faq_body(page: dict) -> str:
    out = [_block("Page", page.get("url", ""))]
    questions = page.get("questions") or []
    if questions:
        out.append(f"{len(questions)} questions go on this page:\n")
        for i, q in enumerate(questions, 1):
            out.append(f"  {i}. {str(q.get('q') or q.get('question') or '').strip()}\n")
    if page.get("html"):
        out.append("\nPaste this block into an HTML element on that page, "
                   "exactly as it is:\n" + str(page["html"]).strip() + "\n")
    return "".join(out)


def _alt_body(page: dict) -> str:
    out = [_block("Page", page.get("url", ""))]
    images = page.get("images") or []
    out.append(f"{len(images)} image(s) on this page:\n")
    for i, img in enumerate(images, 1):
        out.append(f"\n  Image {i}\n"
                   f"    src:     {str(img.get('src') or '').strip()}\n"
                   f"    current: {str(img.get('alt') or '(empty)').strip()}\n"
                   f"    new alt: {str(img.get('new_alt') or '').strip()}\n")
    return "".join(out)


_BODY = {"blogs": _blog_body, "schema": _schema_body,
         "faqs": _faq_body, "alt": _alt_body}


def claude_prompt(cms: str, kind: str, items: list, *, client: str = "",
                  domain: str = "", settings: dict | None = None) -> str:
    """The text the rep pastes into Claude in Chrome.

    Everything the agent needs is in here, because it cannot see this Hub:
    the rules, how this CMS behaves, and the finished content itself.
    """
    task = _TASK.get(kind, _TASK["blogs"])(len(items))
    lines = [
        f"You are working in this Chrome tab, in the {label(cms)} admin for "
        f"{client or 'this client'}"
        + (f" ({domain})" if domain else "") + ".",
        "",
        f"Your task: {task}.",
        "",
        "RULES",
    ]
    lines += [f"- {r}" for r in _RULES]
    lines += ["", "HOW THIS CMS WORKS"]
    lines += [f"- {n}" for n in _cms_notes(cms, kind)]
    lines += ["", "=" * 60, ""]

    body = _BODY.get(kind, _blog_body)
    for i, item in enumerate(items, 1):
        title = (item.get("title") or _short_url(item.get("url", "")) or f"item {i}")
        lines.append(f"--- {i} of {len(items)}: {title} ---")
        lines.append("")
        if kind == "blogs":
            lines.append(body(item, settings or {}))
        else:
            lines.append(body(item))
        lines.append("=" * 60)
        lines.append("")
    lines.append("When every item is in, list what you did and what is still "
                 "waiting on me. Do not publish anything.")
    return "\n".join(lines)


# ---------------------------------------------------------------- fields
def _field(fname: str, value, multiline: bool = False, note: str = "") -> dict:
    return {"label": fname, "value": "" if value is None else str(value),
            "multiline": bool(multiline), "note": note}


def _post_fields(post: dict, settings: dict, cms: str) -> list[dict]:
    """The same values, one at a time, for anyone doing it by hand.

    Kept beside the prompt rather than replaced by it: Claude in Chrome is not
    installed on every machine, and a rep who has to type one field should not
    have to dig it out of a wall of prompt text.
    """
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


def _faq_fields(page: dict) -> list[dict]:
    out = [_field("Page", page.get("url", "")),
           _field("Questions", str(len(page.get("questions") or [])))]
    if page.get("html"):
        out.append(_field("Accordion block (HTML)", page["html"], multiline=True,
                          note="Self-contained — its own CSS, no JavaScript, "
                               "and it carries the FAQPage schema with it."))
    return out


def _alt_fields(page: dict) -> list[dict]:
    out = [_field("Page", page.get("url", ""))]
    for i, img in enumerate(page.get("images") or [], 1):
        out.append(_field(f"Image {i} — src", img.get("src", "")))
        out.append(_field(f"Image {i} — new alt", img.get("new_alt", ""),
                          note="Was: " + (img.get("alt") or "(empty)")))
    return out


# ---------------------------------------------------------------- public
def instructions(cms: str, kind: str, items: list, *, client: str = "",
                 site_url: str = "", settings: dict | None = None) -> dict:
    """Everything the publish panel shows, for any of the four content kinds."""
    if cms not in CMS_KEYS:
        return {"error": f"Unknown CMS '{cms}'."}
    if kind not in KINDS:
        return {"error": f"Unknown content kind '{kind}'."}
    target = admin_target(cms, site_url, kind)
    warnings = list(target["warnings"])
    items = list(items or [])

    rendered = []
    for item in items:
        notes = list(item.get("_notes") or [])
        if kind == "blogs":
            if item.get("status") != "written" or not item.get("content"):
                notes.append("Not written yet — write it before publishing.")
            for flag in (item.get("flags") or []):
                notes.append("Never-mention check: the copy contains "
                             f"“{flag.get('term')}”. Fix it before "
                             "this goes live.")
            fields = _post_fields(item, settings or {}, cms)
            title = item.get("title", "")
            subtitle = item.get("date", "")
        elif kind == "schema":
            if not item.get("approved"):
                notes.append("Not approved yet — approve it in the Schema "
                             "Builder before it goes on the site.")
            fields = _schema_fields(item)
            title = _short_url(item.get("url", ""))
            subtitle = ", ".join(item.get("types") or [])
        elif kind == "faqs":
            fields = _faq_fields(item)
            title = _short_url(item.get("url", ""))
            subtitle = f"{len(item.get('questions') or [])} questions"
        else:
            fields = _alt_fields(item)
            title = _short_url(item.get("url", ""))
            subtitle = f"{len(item.get('images') or [])} images"
        rendered.append({"id": item.get("id", item.get("url")), "title": title,
                         "subtitle": subtitle, "fields": fields, "notes": notes})

    if not rendered:
        warnings.append("Nothing selected — tick what you want to publish.")

    return {
        "cms": cms, "label": label(cms), "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "admin_url": target["url"], "admin_label": target["label"],
        "steps": chrome_steps(cms, kind),
        "prompt": claude_prompt(cms, kind, items, client=client,
                                domain=target.get("domain", ""),
                                settings=settings) if items else "",
        "items": rendered, "warnings": warnings,
    }


# Thin wrappers — the two the SEO page called before there were four kinds.
def blog_instructions(cms: str, posts: list, settings: dict,
                      site_url: str = "", client: str = "") -> dict:
    return instructions(cms, "blogs", posts, client=client,
                        site_url=site_url, settings=settings)


def schema_instructions(cms: str, pages: list, site_url: str = "",
                        client: str = "") -> dict:
    return instructions(cms, "schema", pages, client=client, site_url=site_url)
