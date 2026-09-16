"""Writing blog posts and image alt text straight into a client's WordPress.

`hub/cms_publish.py` covers the CMSes with no write API by handing a browser
agent a prompt. WordPress is not one of them: core has had `/wp-json/wp/v2/`
since 4.7 and **Application Passwords** since 5.6, so nothing has to be
installed on the client's site for this to work. `hub/cms_credentials.py`
holds what the calls are made with; this makes them.

**Page schema needs one thing installed, and that is the whole of what the
plugin buys.** Yoast and Rank Math keep their schema fields in postmeta that is
not `show_in_rest`, and JSON-LD inside post content is stripped by `wp_kses`
for any user without `unfiltered_html`, so core alone has nowhere to put a
block. `hub/wordpress_plugin/smart-1-hub.php` registers one `show_in_rest` post
meta and prints it from `wp_head` -- and that is deliberately the smaller of
the two ways past this. The other was authenticating as an administrator and
writing the block into the page *body*, which needs no plugin and edits a live
page a client wrote; a head block that never touches content is the one worth
having even where the credential would allow both.

**FAQ accordions stay on the Claude path, and not because REST cannot reach
them.** The accordion `hub/faq.py` produces carries its own FAQPage JSON-LD
inside the block that goes on the page. Writing that schema again from here
would put two copies of it on one page; writing it *without* the accordion
would be FAQPage markup for questions no visitor can see, which is a
structured-data violation rather than a shortcut. The deliverable there is the
visible accordion, and placing it is an edit to the page body that a person
makes.

## The rules, each of which is a way this goes quietly wrong

**Everything lands as a draft.** `status: "draft"`, always. It is the same rule
the Chrome prompt has carried since it was written -- *leave it unpublished and
tell me when it is ready* -- and the argument is stronger over an API, because
there is no human watching each step. Nothing here publishes and nothing here
schedules.

**A flagged post is refused by name.** `blog_spec.scan_forbidden()` reads the
finished copy against the client's own never-mention list, and `p["flags"]` is
its evidence. Publishing that copy unattended, to the client's live site, is
precisely what the flag exists to stop -- so the post is refused with the terms
quoted, rather than written as a draft somebody might not read. The refusal for
a mock render in `approve_render` is the same shape.

**A pending image is not a featured image.** `hub/blog_images.py` draws the
line: generated art is `pending` until somebody has looked at it, and approving
is what files it. Sending a pending one would put an unreviewed generated
storefront on the client's website.

**A second press updates the post it made, it does not make a second one.**
The WordPress post id is recorded back onto the post. Two identical drafts on a
client's blog with no way to tell which is current is what `upsert_from_ghl`
learned from GoHighLevel first.

**A term matches exactly or is created; it is never the nearest hit.**
`GET /wp/v2/categories?search=` is a search, so it answers "Roof Repair" to a
query for "Roofing" -- filing a post under a category nobody chose. Matching is
on the normalised name and nothing else, and what is genuinely missing is
created with the exact name, bounded by `blog_spec.MAX_NEW_CATEGORIES_PER_POST`
so a taxonomy cannot grow by surprise.

**An author who is not on the site is reported, never substituted.** The Chrome
prompt says this in words and the API has to keep it: publishing under the
wrong byline is worse than publishing under ours and saying so.

**Alt text is a property of the media item, not of the page.** One attachment
used on three pages has one alt, so two pages asking for two different strings
is a conflict this cannot resolve -- named and refused rather than last-one-
wins. An image that is not in the media library at all (a theme asset, a page
builder background, a hotlink) is named too: that is most of what a scan of a
built page finds, and reporting it as failed would bury the ones that are real.

**Every item reports its own outcome.** One number back hides the two that
failed -- `client_urls.accept_many()`'s rule. And the work is bounded on both
axes, count and wall clock, because these are HTTP calls to somebody else's
server made from a request thread; what was not reached is counted and said,
since a run that stops part-way and says nothing reads exactly like one that
finished.

**Nothing here logs a credential.** Not in an error, not in a note, not in the
activity row. `_call()` builds the Authorization header and it goes no further.
"""
from __future__ import annotations

import base64

import os
import re
import time
import urllib.parse

import requests

from . import audit, blog_spec, cms_credentials

TIMEOUT = 25
# Bounded on both axes. A post is up to three calls (terms, media, the post
# itself) against a host we do not control, from a request thread.
MAX_POSTS_PER_RUN = 10
MAX_IMAGES_PER_RUN = 40
BUDGET_SECONDS = 90

UA = {"User-Agent": "Smart1Hub/1.0 (+https://smart1.agency)"}

# Read-only namespaces that tell us which SEO plugin is on the site. Neither
# lets us write its schema or its meta description -- what knowing buys is a
# true sentence about where the meta description ended up instead.
SEO_NAMESPACES = {
    "yoast/v1": "Yoast SEO",
    "rankmath/v1": "Rank Math",
}

# ---------------------------------------------------------------- the plugin
# Blogs and alt text need nothing installed on the client's site. Schema does,
# and `hub/wordpress_plugin/smart-1-hub.php` is that one file: it registers a
# single `show_in_rest` post meta and prints it from `wp_head`. The names below
# are the wire contract between the two halves and are asserted against the PHP
# in `test_wordpress_schema.py`, because a key spelled one way here and another
# way there is a write that answers 200 and stores nothing.
PLUGIN_SLUG = "smart-1-hub"
PLUGIN_VERSION = "1.1.0"
PLUGIN_NAMESPACE = "s1hub/v1"
SCHEMA_META = "_s1hub_schema"
PLUGIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "wordpress_plugin", PLUGIN_SLUG + ".php")

# A schema run is one resolve and one write per page against a host we do not
# control, so it is bounded like the other two.
MAX_SCHEMA_PAGES_PER_RUN = 20

# Google truncates a search snippet around 155-160 characters. It is a
# DISPLAY limit and not a rejection -- nothing refuses a longer description --
# so a description over it is written and named rather than cut. Shortening
# the client's own approved copy to fit somebody else's snippet is the
# clamp `config.music_length_ms()` refuses one provider over.
META_DESCRIPTION_SNIPPET = 160

# The marker the plugin prints its block behind. Matched as well as the JSON,
# because a page can carry somebody else's JSON-LD and this has to be able to
# say whether OURS is on it.
SCHEMA_MARKER = "<!-- Smart 1 Hub structured data -->"
MAX_VERIFY_PAGES_PER_RUN = 20


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Refused(Exception):
    """A refusal with a sentence a person can act on.

    Carries no credential and no raw provider body: `modules/fan_radio.fail()`
    and the two file optimizers' rule, one provider further out.

    `status` is the HTTP status behind it where there was one, because one
    caller has to tell a 404 apart from every other refusal: a route that is
    not there means the plugin is not installed, which is a thing somebody
    fixes in two minutes, and reading it off the sentence would make the
    wording of an error message load-bearing.
    """

    def __init__(self, message: str = "", status: int = 0):
        super().__init__(message)
        self.status = int(status or 0)


# ---------------------------------------------------------------- discovery
def _origin(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    p = urllib.parse.urlsplit(raw)
    if not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


_REST_LINK = re.compile(
    r'<link[^>]+rel=["\']https://api\.w\.org/["\'][^>]*href=["\']([^"\']+)["\']',
    re.I)


def discover(site_url: str) -> dict:
    """Find the REST root rather than composing one.

    `<origin>/wp-json/` is right on most sites and wrong on a site with plain
    permalinks, which answers at `/?rest_route=/`. WordPress advertises the
    real address in a `Link` header and in a `<link>` in the head, so both are
    read before anything is assumed -- the one-hop-at-a-time reading
    `hub/llms_hosting.verify()` settled on for the same reason.
    """
    origin = _origin(site_url)
    if not origin:
        return {"error": "No website URL is saved for this client, so there is "
                         "nothing to connect to. Add the site under Client Setup."}
    if origin.startswith("http://"):
        return {"error": "This site is on plain http. WordPress refuses "
                         "application passwords over an unencrypted connection, "
                         "so the site needs HTTPS before it can be connected."}
    candidates: list[tuple[str, str]] = []
    try:
        r = requests.get(origin + "/", headers=UA, timeout=TIMEOUT,
                         allow_redirects=True)
        link = (r.links.get("https://api.w.org/") or {}).get("url")
        if link:
            candidates.append((link, "advertised in the site's own Link header"))
        else:
            m = _REST_LINK.search(r.text[:200000] or "")
            if m:
                candidates.append((m.group(1), "advertised in the page head"))
    except requests.RequestException:
        pass
    candidates.append((origin + "/wp-json/", "the default address"))
    candidates.append((origin + "/?rest_route=/", "the plain-permalinks address"))

    tried = []
    for url, how in candidates:
        root = url if url.endswith("/") else url + "/"
        try:
            r = requests.get(root, headers=UA, timeout=TIMEOUT)
        except requests.RequestException as exc:            # noqa: BLE001
            tried.append(f"{root} ({type(exc).__name__})")
            continue
        if r.status_code != 200:
            tried.append(f"{root} ({r.status_code})")
            continue
        try:
            data = r.json()
        except ValueError:
            tried.append(f"{root} (not JSON)")
            continue
        namespaces = list(data.get("namespaces") or [])
        if "wp/v2" not in namespaces:
            tried.append(f"{root} (no wp/v2 namespace)")
            continue
        return {"rest_root": root, "how": how,
                "name": str(data.get("name") or ""),
                "namespaces": namespaces,
                "seo_plugin": next((SEO_NAMESPACES[n] for n in namespaces
                                    if n in SEO_NAMESPACES), ""),
                "note": f"REST API found at {root} — {how}."}
    return {"error": "No WordPress REST API answered at this domain. Tried: "
                     + "; ".join(tried) + ". Either the site is not WordPress, "
                     "or the REST API has been disabled by a plugin or a "
                     "security rule."}


# --------------------------------------------------------------------- http
def _auth_header(username: str, app_password: str) -> str:
    raw = f"{username}:{app_password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _endpoint(root: str, path: str) -> str:
    """`/?rest_route=/` and `/wp-json/` join a path differently."""
    root = str(root or "")
    path = str(path or "").lstrip("/")
    if "rest_route=" in root:
        sep = "" if root.endswith("/") else "/"
        return root + sep + path
    return (root if root.endswith("/") else root + "/") + path


def _explain(status: int, body: dict, sent_auth: bool) -> str:
    """Turn a WordPress refusal into a sentence that names the next step.

    The distinction that matters is at 401. `incorrect_password` is a bad or
    revoked application password. `rest_not_logged_in` **while we sent an
    Authorization header** is the host having stripped it -- ordinary on
    CGI/FastCGI setups, fixed with one `.htaccess` line, and indistinguishable
    from a wrong password to anybody reading a bare 401.
    """
    code = str((body or {}).get("code") or "")
    msg = str((body or {}).get("message") or "").strip()
    if status == 401:
        if code == "rest_not_logged_in" and sent_auth:
            return ("WordPress did not see the credentials we sent. This is "
                    "almost always the host stripping the Authorization header "
                    "(common on CGI/FastCGI). Adding "
                    "`SetEnvIf Authorization \"(.*)\" HTTP_AUTHORIZATION=$1` to "
                    "the site's .htaccess fixes it.")
        return ("WordPress refused the application password. It has been "
                "revoked, mistyped, or belongs to a different user — generate "
                "a new one under Users → Profile → Application Passwords.")
    if status == 403:
        return ("This WordPress user is not allowed to do that"
                + (f" ({msg})" if msg else "")
                + ". The application password inherits the user's role, so it "
                  "needs an Author or Editor account for posts and an Editor "
                  "for media.")
    if status == 404:
        return ("WordPress answered 404 for that endpoint. The REST API may be "
                "disabled, or the address saved for this site is wrong — run "
                "the connection check again.")
    if status == 413:
        return "The site refused the upload as too large."
    return (f"WordPress answered {status}" + (f": {msg}" if msg else "") + ".")


def _call(cred: dict, method: str, path: str, *, json_body=None,
          params=None, data: bytes | None = None,
          content_type: str = "", filename: str = "") -> dict:
    """One authenticated call. Answers, never raises through to a route."""
    root = cred.get("rest_root") or ""
    if not root:
        raise Refused("No REST root is saved for this client. Run the "
                      "connection check.")
    headers = dict(UA)
    headers["Authorization"] = _auth_header(cred.get("username") or "",
                                            cred.get("app_password") or "")
    if content_type:
        headers["Content-Type"] = content_type
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    url = _endpoint(root, path)
    try:
        r = requests.request(method, url, headers=headers, params=params,
                             json=json_body, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:                # noqa: BLE001
        raise Refused("Could not reach this WordPress site "
                      f"({type(exc).__name__}). It may be down, or blocking "
                      "requests from outside.") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        raise Refused(_explain(r.status_code, body if isinstance(body, dict) else {},
                               sent_auth=True),
                      status=r.status_code)
    return {"body": body, "headers": r.headers, "status": r.status_code}


def _credential(client: str) -> dict:
    cred = cms_credentials.get(client, cms_credentials.WORDPRESS)
    if cred.get("error"):
        raise Refused(cred["error"])
    return cred


# -------------------------------------------------------------------- probe
def probe(client: str) -> dict:
    """One round trip that answers what this credential may actually do.

    Asked at connect and re-asked on a button, never on a page load. Four
    things come out of it and each decides something: who we are (the default
    author), what the role may do, whether `unfiltered_html` is there (which is
    what would make schema-in-content possible later), and which SEO plugin is
    installed (which is where a meta description can go).
    """
    try:
        cred = _credential(client)
        me = _call(cred, "GET", "wp/v2/users/me",
                   params={"context": "edit"})["body"]
    except Refused as exc:
        return {"ok": False, "error": str(exc)}
    caps = me.get("capabilities") or {}
    roles = [str(r) for r in (me.get("roles") or [])]
    can_post = bool(caps.get("publish_posts") or caps.get("edit_posts"))
    can_media = bool(caps.get("upload_files"))
    namespaces: list[str] = []
    try:
        root = requests.get(cred["rest_root"], headers=UA, timeout=TIMEOUT)
        namespaces = list((root.json() or {}).get("namespaces") or [])
    except Exception:                                       # noqa: BLE001
        namespaces = []
    plugin = next((SEO_NAMESPACES[n] for n in namespaces if n in SEO_NAMESPACES), "")
    out = {
        "ok": True,
        "user_id": me.get("id"),
        "user_name": str(me.get("name") or ""),
        "roles": roles,
        "can_write_posts": can_post,
        "can_upload_media": can_media,
        "unfiltered_html": bool(caps.get("unfiltered_html")),
        "seo_plugin": plugin,
        "warnings": [],
    }
    if not can_post:
        out["warnings"].append(
            "This user cannot create posts. The application password inherits "
            "the user's role — it needs Author or above.")
    if not can_media:
        out["warnings"].append(
            "This user cannot upload files, so featured images will be skipped "
            "and the post still written.")
    # The meta description sentence is decided AFTER the plugin has been asked,
    # below, because what is true about it depends on whether the Smart 1 Hub
    # plugin is there to make that field writable.
    # Schema does not ride on `unfiltered_html` any more -- it goes in the head
    # through our own plugin and never into post content -- so the capability
    # is still reported, because it is the difference between a post body we
    # can write verbatim and one WordPress will filter, and it no longer says
    # anything about schema.
    if not out["unfiltered_html"]:
        out["warnings"].append(
            "This user does not have unfiltered_html, so anything unusual in a "
            "post body may be filtered on the way in. Schema is unaffected: it "
            "goes in the page head through the Smart 1 Hub plugin.")
    try:
        out["plugin"] = plugin_status(cred)
    except Exception as exc:                                # noqa: BLE001
        # A probe that raises over an optional half is worse than one that
        # cannot answer about it. Nothing below the plugin depends on this.
        out["plugin"] = {"installed": False, "measured": False,
                         "error": f"{type(exc).__name__}"}
    if not out["plugin"].get("installed"):
        out["warnings"].append(
            "The Smart 1 Hub plugin is not installed, so page schema cannot be "
            "written to this site yet. Blogs and alt text do not need it."
            if out["plugin"].get("measured") is not False else
            "We could not tell whether the Smart 1 Hub plugin is installed.")
    else:
        out["warnings"] += list(out["plugin"].get("warnings") or [])

    # Four true sentences about where a blog post's meta description ends up,
    # and which one holds depends on two facts rather than one. Collapsing them
    # would put "copy it across by hand" in front of somebody on a site where
    # it now goes across by itself.
    described_by = str(out["plugin"].get("description_by") or "")
    out["description_field"] = described_by
    if described_by:
        out["warnings"].append(
            f"A blog post's meta description goes into {described_by}'s own "
            "field, and into the Excerpt. Nothing has to be copied across.")
    elif plugin and out["plugin"].get("installed"):
        out["warnings"].append(
            f"{plugin} is installed, and it keeps its meta description "
            "somewhere this cannot write to — the description goes into the "
            "Excerpt field and should be copied across in WordPress.")
    elif plugin:
        out["warnings"].append(
            f"{plugin} is installed. Its meta description field becomes "
            "writable once the Smart 1 Hub plugin is on the site; until then "
            "the description goes into the Excerpt and is copied across by "
            "hand.")
    else:
        out["warnings"].append(
            "No SEO plugin was found on this site, so a blog post's meta "
            "description goes into the post's Excerpt field.")
    return out


# -------------------------------------------------------------- the plugin
def plugin_bytes() -> bytes:
    """The plugin file as it sits in this repo."""
    with open(PLUGIN_FILE, "rb") as fh:
        return fh.read()


def plugin_zip() -> bytes:
    """The same file, wrapped so it installs from Plugins -> Add New -> Upload.

    Two ways in, because the two have different costs and only one of them is
    always available. A must-use plugin cannot be deactivated by anybody, which
    is not ours to decide on a client's site, and installing one needs SFTP or
    a file manager. The zip needs neither: a webmaster uploads it and can turn
    it off again. WordPress requires the file inside a folder, so it is written
    at `<slug>/<slug>.php` rather than at the root of the archive.
    """
    import io as _io
    import zipfile
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{PLUGIN_SLUG}/{PLUGIN_SLUG}.php", plugin_bytes())
    return buf.getvalue()


def plugin_status(cred: dict) -> dict:
    """Is our plugin on this site, and is it the version this Hub expects.

    Asked before anything is written, because core **drops an unregistered meta
    key without complaining**: the write answers 200, the response carries no
    such key, and nothing anywhere says the schema is not on the site. That is
    the silent success this whole module is written against, so the absence is
    established up front and confirmed again by reading the value back.
    """
    try:
        body = _call(cred, "GET", PLUGIN_NAMESPACE + "/status")["body"]
    except Refused as exc:
        if getattr(exc, "status", 0) == 404:
            return {"installed": False,
                    "note": "The Smart 1 Hub plugin is not on this site."}
        # Not determinable is not the same answer as not installed: a site that
        # refused the credential or did not answer tells us nothing about what
        # is installed on it, and reporting it as missing sends somebody to
        # install a plugin that may already be there.
        return {"installed": False, "measured": False, "error": str(exc)}
    if not isinstance(body, dict) or body.get("plugin") != PLUGIN_SLUG:
        return {"installed": False, "measured": False,
                "error": "Something else is answering at "
                         f"{PLUGIN_NAMESPACE}/status on this site."}
    version = str(body.get("version") or "")
    out = {
        "installed": True,
        "measured": True,
        "version": version,
        "current": version == PLUGIN_VERSION,
        "expected": PLUGIN_VERSION,
        "meta_key": str(body.get("meta_key") or ""),
        "post_types": [str(t) for t in (body.get("post_types") or [])],
        "seo_plugins": [str(x) for x in (body.get("seo_plugins") or [])],
        # Which SEO plugin's description field this site accepts a write to,
        # and whose it is. Empty is a real answer and not a failure: the
        # Excerpt fallback is what the module did before any of this.
        "description_key": str(body.get("description_key") or ""),
        "description_by": str(body.get("description_by") or ""),
        "must_use": bool(body.get("must_use")),
        "warnings": [],
    }
    if not out["current"]:
        out["warnings"].append(
            f"This site has version {version or '(unknown)'} of the plugin and "
            f"this Hub expects {PLUGIN_VERSION}. Schema still writes; install "
            "the current file when convenient.")
    # The key is the whole wire contract. An older plugin that spells it
    # differently would take the write, store nothing under the name we read
    # back, and the run would report every page as rejected with no reason a
    # rep could act on -- so it is named here instead.
    if out["meta_key"] and out["meta_key"] != SCHEMA_META:
        out["warnings"].append(
            f"The plugin on this site stores schema under "
            f"'{out['meta_key']}' and this Hub writes '{SCHEMA_META}'. "
            "Install the current plugin file.")
    if out["seo_plugins"]:
        out["warnings"].append(
            ", ".join(out["seo_plugins"])
            + " also emits structured data on this site. Two blocks on one page "
              "is legal, and two that describe the business differently is not "
              "something either of them can resolve — worth a look at a page "
              "once the first block is live.")
    return out


def resolve_url(cred: dict, url: str) -> dict:
    """Which post this URL is, asked of WordPress rather than guessed.

    Core's REST API has no resolver, so the alternative is searching by slug --
    and a slug is unique neither across post types nor across a page hierarchy.
    The plugin calls `url_to_postid()`, which is the site's own answer.
    """
    try:
        body = _call(cred, "GET", PLUGIN_NAMESPACE + "/resolve",
                     params={"url": url})["body"]
    except Refused as exc:
        return {"id": 0, "error": str(exc)}
    if not isinstance(body, dict):
        return {"id": 0, "error": "The site answered the resolver with "
                                  "something that is not a record."}
    out = {"id": int(body.get("id") or 0),
           "type": str(body.get("type") or ""),
           "rest_base": str(body.get("rest_base") or ""),
           "status": str(body.get("status") or ""),
           "title": str(body.get("title") or ""),
           "link": str(body.get("link") or ""),
           "registered": bool(body.get("registered"))}
    if not out["id"]:
        out["error"] = str(body.get("reason") or
                           "WordPress does not resolve that address to a post.")
    elif not out["rest_base"]:
        out["error"] = (f"That address is a '{out['type']}', which this site "
                        "does not serve over the REST API, so nothing can be "
                        "written to it.")
    elif not out["registered"]:
        out["error"] = (f"The plugin does not register the schema field for "
                        f"'{out['type']}'. Install the current plugin file.")
    return out


def _schema_blockers(page: dict) -> str:
    """Why this saved page must not be written, before a call is made."""
    if not page.get("approved"):
        return ("This page's schema has not been approved yet. Approve it on "
                "the Schema Builder first — the whole point of the approval is "
                "that nothing unreviewed reaches the client's live site.")
    block = page.get("schema")
    if not isinstance(block, (dict, list)) or not block:
        return "There is no schema saved for this page."
    return ""


def _schema_text(block) -> str:
    import json as _json
    return _json.dumps(block, ensure_ascii=False, separators=(",", ":"))


def publish_schema(client: str, urls: list[str] | None = None, *,
                   actor: str = "") -> dict:
    """Write approved page schema onto the site through the plugin.

    Every page reports its own outcome, and each one is **read back**: the
    response to the write carries the registered meta, so comparing it to what
    was sent is what separates "WordPress answered 200" from "the block is on
    the site". Without that a missing plugin, a post type the plugin does not
    cover and JSON the site rejected all look like a clean run.
    """
    from . import seo
    try:
        cred = _credential(client)
    except Refused as exc:
        return {"error": str(exc)}
    state = cms_credentials.state(client)
    probe_row = state.get("probe") or {}
    if probe_row.get("ok") is False:
        return {"error": "The last connection check on this site failed: "
                         + str(probe_row.get("error") or "")}

    plugin = plugin_status(cred)
    if not plugin.get("installed"):
        return {"error": (str(plugin.get("error") or "")
                          or "Schema needs the Smart 1 Hub plugin on the "
                             "client's site — core WordPress keeps schema "
                             "fields out of the REST API, so there is nothing "
                             "for this to write to until it is installed.")
                          + " Download it from the WordPress connection panel "
                            "and install it under Plugins → Add New → Upload.",
                "plugin": plugin, "needs_plugin": True}

    store = seo.load_store(client)
    pages = store.get("pages") or {}
    wanted = [u for u in (urls or []) if u in pages]
    missing = [u for u in (urls or []) if u not in pages]
    todo, deferred = (wanted[:MAX_SCHEMA_PAGES_PER_RUN],
                      wanted[MAX_SCHEMA_PAGES_PER_RUN:])

    started = time.time()
    results, written = [], 0
    for url in missing:
        results.append({"url": url, "ok": False,
                        "error": "No schema is saved for this page."})
    for url in todo:
        page = pages[url]
        row = {"url": url, "title": str(page.get("title") or ""), "ok": False,
               "notes": []}
        if time.time() - started > BUDGET_SECONDS:
            deferred.append(url)
            continue
        blocker = _schema_blockers(page)
        if blocker:
            row["error"] = blocker
            results.append(row)
            continue
        target = resolve_url(cred, url)
        if target.get("error"):
            row["error"] = target["error"]
            results.append(row)
            continue
        text = _schema_text(page.get("schema"))
        try:
            made = _call(cred, "POST",
                         f"wp/v2/{target['rest_base']}/{int(target['id'])}",
                         json_body={"meta": {SCHEMA_META: text}})["body"]
        except Refused as exc:
            row["error"] = str(exc)
            results.append(row)
            continue
        landed = ""
        if isinstance(made, dict) and isinstance(made.get("meta"), dict):
            landed = str(made["meta"].get(SCHEMA_META) or "")
        if landed != text:
            # Three different faults land here and the response cannot tell
            # them apart, so the sentence names all three rather than picking
            # the likeliest and sending somebody to the wrong one.
            row["error"] = (
                "WordPress took the request and did not store the block. That "
                "is the plugin missing on this post type, a plugin older than "
                "this Hub, or the site rejecting the JSON. Run the connection "
                "check, then install the current plugin file."
                if landed == "" else
                "WordPress stored something different from what was sent, so "
                "the block on the page is not the one that was approved.")
            results.append(row)
            continue
        row.update({"ok": True, "post_id": target["id"],
                    "post_type": target["type"],
                    "link": target.get("link") or url,
                    "edit_url": _edit_link(cred, target["id"]),
                    "bytes": len(text)})
        if target.get("status") and target["status"] != "publish":
            row["notes"].append(
                f"That page is '{target['status']}' rather than published, so "
                "the block is on it but nothing is serving it yet.")
        page["wordpress"] = {"post_id": target["id"],
                             "post_type": target["type"],
                             "link": target.get("link") or "",
                             "at": _now(), "by": str(actor or ""),
                             "bytes": len(text)}
        # The table's own column for this. Stamped only where the write landed,
        # because a date saying the schema is on the site is exactly the field
        # somebody reads instead of going to look.
        if not page.get("added_to_site"):
            page["added_to_site"] = time.strftime("%Y-%m-%d")
        page["updated"] = time.strftime("%Y-%m-%d %H:%M")
        written += 1
        results.append(row)

    if written:
        seo.save_store(client, store)
        audit.log("seo", "wordpress_schema_published", actor=actor or None,
                  client=client, detail=f"{written} page(s)")
    note = _left_note(len(deferred), "page")
    for warning in plugin.get("warnings") or []:
        note = (note + " " if note else "") + warning
    return {"ok": True, "results": results, "written": written,
            "left": len(deferred), "note": note, "plugin": plugin,
            "reminder": "The block goes in the page head. No page content was "
                        "changed and nothing was published."}


# ------------------------------------------------- is it actually on the page
_LD_BLOCK = re.compile(
    re.escape(SCHEMA_MARKER)
    + r"\s*<script[^>]*application/ld\+json[^>]*>(.*?)</script>",
    re.I | re.S)


def _fetch_public(url: str) -> tuple[str, str]:
    """The page as a stranger gets it. Returns (html, why-not).

    Deliberately unauthenticated: the whole question is what a visitor -- and
    therefore a crawler -- sees, and sending the credential would ask a
    different one. A logged-in request also bypasses most page caches, which
    is exactly the fault this is looking for.
    """
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:                # noqa: BLE001
        return "", (f"Could not fetch that page ({type(exc).__name__}).")
    if r.status_code == 404:
        return "", ("That address answers 404 to somebody not signed in. A "
                    "draft or private page is not public, so nothing on it is "
                    "visible to a crawler either.")
    if r.status_code >= 400:
        return "", f"That page answered {r.status_code} to a visitor."
    ctype = str(r.headers.get("Content-Type") or "")
    if "html" not in ctype.lower():
        return "", f"That address served {ctype or 'something that is not a page'}."
    return r.text or "", ""


def verify_schema(client: str, urls: list[str] | None = None) -> dict:
    """Fetch each page as a visitor and say whether our block is really on it.

    `publish_schema()` reads the value back out of the write, which proves
    **WordPress stored it** -- and that is not the same claim as a visitor
    seeing it. Three ordinary things break the second without touching the
    first: the plugin deactivated, a caching plugin still serving HTML from
    before the write, and a theme that never calls `wp_head()`. All three
    leave the Hub saying the schema is on the site, with an added-to-site date
    against it, and nothing anywhere disagreeing.

    So this is the hop `hub/llms_hosting.verify()` already makes one tool over:
    ask the page itself, unauthenticated, and report what came back.

    ## The verdicts

    **live** -- our block is on the page and is the JSON we stored.

    **stale** -- our block is on the page and is *different* from what we
    stored. Almost always a page cache serving HTML from before the last
    write; occasionally somebody editing the field in WordPress. Named as a
    difference rather than as either cause, because the page cannot tell us
    which.

    **absent** -- the page came back and our block is not in it. The plugin
    deactivated, a theme with no `wp_head()`, or a cache old enough to predate
    the plugin.

    **not_measured** -- we could not fetch the page, or there is nothing
    stored to compare against. Never a verdict about the page.
    """
    from . import seo
    store = seo.load_store(client)
    pages = store.get("pages") or {}
    wanted = [u for u in (urls or []) if u in pages]
    todo, deferred = (wanted[:MAX_VERIFY_PAGES_PER_RUN],
                      wanted[MAX_VERIFY_PAGES_PER_RUN:])

    started = time.time()
    results = {"live": 0, "stale": 0, "absent": 0, "not_measured": 0}
    rows = []
    for url in (u for u in (urls or []) if u not in pages):
        rows.append({"url": url, "verdict": "not_measured",
                     "note": "No schema is saved for this page, so there is "
                             "nothing to compare what is on it against."})
        results["not_measured"] += 1
    for url in todo:
        page = pages[url]
        if time.time() - started > BUDGET_SECONDS:
            deferred.append(url)
            continue
        row = {"url": url, "title": str(page.get("title") or ""),
               "verdict": "not_measured", "note": ""}
        stored = page.get("schema")
        if not isinstance(stored, (dict, list)) or not stored:
            row["note"] = "There is no schema saved for this page."
            rows.append(row)
            results["not_measured"] += 1
            continue
        html, why = _fetch_public(url)
        if why:
            row["note"] = why
            rows.append(row)
            results["not_measured"] += 1
            continue
        found = _LD_BLOCK.search(html)
        if not found:
            row["verdict"] = "absent"
            row["note"] = (
                "The page loaded and our block is not in it. That is the "
                "plugin deactivated, a page cache still serving HTML from "
                "before it was written, or a theme that does not call "
                "wp_head(). Nothing here can tell those apart from outside.")
            rows.append(row)
            results["absent"] += 1
            continue
        try:
            import json as _json
            on_page = _json.loads(found.group(1))
        except ValueError:
            # Not the same finding as a stale cache. The plugin escapes `<` to
            # its \u003c JSON escape precisely so a `</script>` in the data
            # cannot end the block early, so unreadable JSON here means the
            # block on the page was not written by a plugin that does that --
            # an old one, or somebody's optimizer rewriting the head.
            row["verdict"] = "stale"
            row["note"] = (
                "Our marker is on the page and what follows it is not readable "
                "JSON. The block was cut short, which is a plugin older than "
                "the one that escapes it, or something on the site rewriting "
                "the page head.")
            rows.append(row)
            results["stale"] += 1
            continue
        if on_page == stored:
            row["verdict"] = "live"
            row["note"] = "Our block is on the page, and it is what was sent."
        else:
            row["verdict"] = "stale"
            row["note"] = (
                "Our block is on the page and is not what was last sent. "
                "Usually a page cache serving HTML from before the last write "
                "— clear the site's cache and check again. It can also be "
                "somebody editing the field in WordPress.")
        rows.append(row)
        results[row["verdict"]] += 1

    return {"ok": True, "results": rows, "counts": results,
            "left": len(deferred), "note": _left_note(len(deferred), "page"),
            "reminder": "Each page was fetched the way a visitor gets it — "
                        "signed out, and through whatever cache the site has "
                        "in front of it."}


def connect(client: str, *, site_url: str, username: str, app_password: str,
            actor: str = "") -> dict:
    """Discover, save, probe — in that order, because each needs the last.

    The probe runs against the credential as stored, so what the panel reports
    is what a publish will actually be made with rather than what was typed
    into the form a moment earlier.
    """
    found = discover(site_url)
    if found.get("error"):
        return {"error": found["error"]}
    saved = cms_credentials.save(
        client, cms_credentials.WORDPRESS, rest_root=found["rest_root"],
        username=username, app_password=app_password, actor=actor)
    if saved.get("error"):
        return saved
    result = probe(client)
    if not result.get("ok"):
        # Kept rather than discarded: a probe that could not run is a fact
        # about the site or the header, and throwing the credential away would
        # make the .htaccess case unfixable without retyping the password.
        cms_credentials.record_probe(client, cms_credentials.WORDPRESS,
                                     {"ok": False, "error": result.get("error", "")})
        return {"ok": False, "error": result.get("error", ""),
                "discovered": found,
                "state": cms_credentials.state(client)}
    cms_credentials.record_probe(client, cms_credentials.WORDPRESS, result)
    audit.log("seo", "wordpress_connected", actor=actor or None, client=client,
              detail=found["rest_root"])
    return {"ok": True, "discovered": found, "probe": result,
            "state": cms_credentials.state(client)}


# ------------------------------------------------------------------- terms
def _term_ids(cred: dict, taxonomy: str, names: list[str], *,
              may_create: int) -> tuple[list[int], list[str]]:
    """Resolve names to term ids, creating at most `may_create` of them.

    `search` is a search: asked for "Roofing" it will happily answer "Roof
    Repair", so the match is on the normalised name and nothing else. What was
    not matched and could not be created is returned rather than dropped.
    """
    ids: list[int] = []
    notes: list[str] = []
    created = 0
    norm = (blog_spec.normalise_category if taxonomy == "categories"
            else blog_spec.normalise_tag)
    for raw in names:
        want = norm(raw)
        if not want:
            continue
        found = _call(cred, "GET", f"wp/v2/{taxonomy}",
                      params={"search": want, "per_page": 100})["body"]
        hit = next((t for t in (found or [])
                    if norm(str(t.get("name") or "")).lower() == want.lower()), None)
        if hit:
            ids.append(int(hit["id"]))
            continue
        if created >= may_create:
            notes.append(f"'{want}' is not on the site and was not created — "
                         f"only {may_create} new {taxonomy[:-3]}y/ies may be "
                         "added per post. Add it in WordPress and re-publish.")
            continue
        try:
            made = _call(cred, "POST", f"wp/v2/{taxonomy}",
                         json_body={"name": want})["body"]
            ids.append(int(made["id"]))
            created += 1
            notes.append(f"Created the {taxonomy[:-3]}y '{want}' on the site.")
        except Refused as exc:
            notes.append(f"'{want}' is not on the site and could not be "
                         f"created: {exc}")
    return ids, notes


def _author_id(cred: dict, name: str, probe_row: dict) -> tuple[int | None, str]:
    """The site's user with exactly this name, or ours with a note.

    Never a near match. Publishing under the wrong byline is worse than
    publishing under the account we authenticated as and saying so.
    """
    want = str(name or "").strip()
    if not want:
        return None, ""
    try:
        users = _call(cred, "GET", "wp/v2/users",
                      params={"search": want, "per_page": 100})["body"]
    except Refused as exc:
        return None, f"Could not look up the author '{want}': {exc}"
    hits = [u for u in (users or [])
            if str(u.get("name") or "").strip().lower() == want.lower()]
    if len(hits) == 1:
        return int(hits[0]["id"]), ""
    if len(hits) > 1:
        return None, (f"{len(hits)} users on this site are called '{want}', so "
                      "the byline was left as "
                      f"{probe_row.get('user_name') or 'the connected user'}.")
    return None, (f"No user called '{want}' exists on this site, so the post "
                  f"is under {probe_row.get('user_name') or 'the connected user'}. "
                  "Create the user in WordPress and re-publish to change it.")


# ------------------------------------------------------------------- media
def _fetch_bytes(url: str) -> tuple[bytes, str]:
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content, (r.headers.get("Content-Type") or "").split(";")[0].strip()


def _upload_featured(cred: dict, post: dict) -> tuple[int | None, str]:
    """Put the approved featured image in the media library.

    Only an **approved** one. `hub/blog_images.py` holds generated art at
    `pending` until somebody has looked at it, and approving is the press that
    files it — sending a pending image would put an unreviewed generated
    storefront on the client's live website.
    """
    img = post.get("image") or {}
    url = str(img.get("url") or "")
    if not url:
        return None, ""
    if str(img.get("status") or "") != "approved":
        return None, ("The featured image is still waiting for approval, so it "
                      "was not uploaded. Approve it and re-publish.")
    try:
        raw, ctype = _fetch_bytes(url)
    except Exception as exc:                                # noqa: BLE001
        return None, f"The featured image could not be fetched ({type(exc).__name__})."
    name = os.path.basename(urllib.parse.urlsplit(url).path) or "featured.jpg"
    if "." not in name:
        name += {"image/png": ".png", "image/webp": ".webp"}.get(ctype, ".jpg")
    try:
        made = _call(cred, "POST", "wp/v2/media", data=raw,
                     content_type=ctype or "application/octet-stream",
                     filename=name)["body"]
    except Refused as exc:
        return None, f"The featured image could not be uploaded: {exc}"
    media_id = int(made.get("id") or 0) or None
    if media_id and post.get("title"):
        # The alt on a featured image is what a screen reader announces for the
        # post's own hero, and WordPress leaves it empty unless it is set.
        try:
            _call(cred, "POST", f"wp/v2/media/{media_id}",
                  json_body={"alt_text": str(post.get("title") or "")[:125]})
        except Refused:
            pass
    return media_id, ""


# -------------------------------------------------------------------- posts
def _post_payload(post: dict, *, category_ids, tag_ids, author_id,
                  media_id, description_key: str = "") -> dict:
    body = {
        "title": str(post.get("title") or ""),
        "content": str(post.get("content") or ""),
        "slug": str(post.get("slug") or blog_spec.slugify_title(post.get("title", ""))),
        # Always. Nothing here publishes and nothing here schedules -- the
        # rule the Chrome prompt has carried since it was written.
        "status": "draft",
    }
    if post.get("meta_description"):
        # The Excerpt is written either way, and that is deliberate. It was
        # only ever a CARRIER for the meta description, but it is also a real
        # field a theme prints under the title on a blog index -- so dropping
        # it now that the description has somewhere better to go would change
        # how the client's own blog index renders, which nobody asked for.
        body["excerpt"] = str(post["meta_description"])
        if description_key:
            body["meta"] = {description_key: str(post["meta_description"])}
    if category_ids:
        body["categories"] = category_ids
    if tag_ids:
        body["tags"] = tag_ids
    if author_id:
        body["author"] = author_id
    if media_id:
        body["featured_media"] = media_id
    return body


def _post_blockers(post: dict) -> str:
    if str(post.get("status") or "") != "written" or not str(post.get("content") or "").strip():
        return ("This post has not been written yet, so there is nothing to "
                "publish. Write it first.")
    flags = post.get("flags") or []
    if flags:
        terms = ", ".join(sorted({str(f.get("term") or "") for f in flags if f.get("term")}))
        return ("This post trips the client's never-mention list"
                + (f" ({terms})" if terms else "")
                + ", so it was not sent. Rewrite it, or clear the flag, and "
                  "publish again.")
    return ""


def publish_posts(client: str, ids: list, *, actor: str = "") -> dict:
    """Write the chosen posts into WordPress as drafts.

    Every post reports its own outcome. Bounded on count and wall clock, and
    what was not reached is said rather than left to look like a clean run.
    """
    from . import seo
    try:
        cred = _credential(client)
    except Refused as exc:
        return {"error": str(exc)}
    state = cms_credentials.state(client)
    probe_row = state.get("probe") or {}
    if probe_row.get("ok") is False:
        return {"error": "The last connection check on this site failed: "
                         + str(probe_row.get("error") or "")}
    if probe_row and not probe_row.get("can_write_posts", True):
        return {"error": "The connected WordPress user cannot create posts. "
                         "Connect an Author or Editor account."}

    store = seo.load_store(client)
    settings = seo.blog_settings(client, store)
    posts = {p.get("id"): p for p in (store.get("blogs") or {}).get("posts") or []}
    wanted = [i for i in ids if i in posts]
    todo, deferred = wanted[:MAX_POSTS_PER_RUN], wanted[MAX_POSTS_PER_RUN:]

    # Asked ONCE for the run rather than per post: it is one round trip, and
    # the answer cannot change between two posts in the same press.
    #
    # And nothing about it may cost somebody a blog post. Writing the
    # description into the SEO plugin's own field is an improvement on this
    # path rather than a precondition for it -- a site with no plugin, or one
    # that answered this oddly, publishes exactly as it did before and falls
    # back to the Excerpt.
    try:
        plugin = plugin_status(cred)
    except Exception:                                       # noqa: BLE001
        plugin = {}
    description_key = str(plugin.get("description_key") or "")
    description_by = str(plugin.get("description_by") or "")

    author_name = (settings.get("author") or {}).get("name") or ""
    author_id, author_note = (None, "")
    if author_name:
        try:
            author_id, author_note = _author_id(cred, author_name, probe_row)
        except Refused as exc:
            author_note = str(exc)

    started = time.time()
    results, published = [], 0
    for pid in todo:
        post = posts[pid]
        row = {"id": pid, "title": str(post.get("title") or ""), "ok": False,
               "notes": []}
        if time.time() - started > BUDGET_SECONDS:
            deferred.append(pid)
            continue
        blocker = _post_blockers(post)
        if blocker:
            row["error"] = blocker
            results.append(row)
            continue
        try:
            cats, cat_notes = _term_ids(
                cred, "categories", post.get("categories") or [],
                may_create=blog_spec.MAX_NEW_CATEGORIES_PER_POST)
            tags, tag_notes = _term_ids(
                cred, "tags", post.get("tags") or [],
                may_create=blog_spec.MAX_TAGS)
            row["notes"] += cat_notes + tag_notes
            media_id, media_note = (None, "")
            if probe_row.get("can_upload_media", True):
                media_id, media_note = _upload_featured(cred, post)
            elif (post.get("image") or {}).get("url"):
                media_note = ("The connected user cannot upload files, so the "
                              "featured image was skipped.")
            if media_note:
                row["notes"].append(media_note)
            if author_note:
                row["notes"].append(author_note)
            body = _post_payload(post, category_ids=cats, tag_ids=tags,
                                 author_id=author_id, media_id=media_id,
                                 description_key=description_key)
            existing = (post.get("wordpress") or {}).get("post_id")
            path = f"wp/v2/posts/{int(existing)}" if existing else "wp/v2/posts"
            made = _call(cred, "POST", path, json_body=body)["body"]
        except Refused as exc:
            row["error"] = str(exc)
            results.append(row)
            continue
        wp_id = int(made.get("id") or 0)
        link = str(made.get("link") or "")
        edit = _edit_link(cred, wp_id)
        post["wordpress"] = {"post_id": wp_id, "link": link, "edit_url": edit,
                             "status": str(made.get("status") or "draft"),
                             "at": _now(), "by": str(actor or ""),
                             "updated": bool(existing)}
        row.update({"ok": True, "post_id": wp_id, "link": link,
                    "edit_url": edit, "updated": bool(existing)})
        description = str(post.get("meta_description") or "")
        if not description:
            row["notes"].append("No meta description has been written for this "
                                "post, so the Excerpt is empty.")
        elif description_key:
            # A 200 is not evidence a meta key landed: core drops one nothing
            # registered without complaining, which on this path would be an
            # older plugin on the site. Read it back off the same response.
            landed = ""
            if isinstance(made.get("meta"), dict):
                landed = str(made["meta"].get(description_key) or "")
            if landed == description:
                row["meta_description_field"] = description_by
                row["notes"].append(
                    f"The meta description went into {description_by}'s own "
                    "field, and into the Excerpt. Nothing to copy across.")
            else:
                row["notes"].append(
                    f"The Excerpt has the meta description, but {description_by}'s "
                    "own field would not take it — that is a plugin on the site "
                    "older than this Hub. Install the current plugin file, or "
                    "copy the description across in WordPress.")
        elif plugin.get("seo_plugins") or probe_row.get("seo_plugin"):
            # The plugin's answer is read live, this press; the probe row is
            # what was stored whenever somebody last pressed Check. Where they
            # disagree the live one is the newer fact.
            named = (", ".join(plugin.get("seo_plugins") or [])
                     or probe_row.get("seo_plugin"))
            row["notes"].append(
                f"The meta description went into the Excerpt field. {named} "
                "keeps its own description somewhere this cannot write to, so "
                "copy it across in WordPress.")
        if len(description) > META_DESCRIPTION_SNIPPET:
            # Reported, never cut. Google truncates the snippet; it does not
            # refuse the page, and shortening the client's approved copy to fit
            # is not this module's call.
            row["notes"].append(
                f"That description is {len(description)} characters. Google "
                f"truncates a snippet around {META_DESCRIPTION_SNIPPET}, so the "
                "end of it will not show. It was written as approved rather "
                "than cut.")
        published += 1
        results.append(row)

    if published:
        seo.save_store(client, store)
        audit.log("seo", "wordpress_posts_published", actor=actor or None,
                  client=client, detail=f"{published} draft(s)")
    return {"ok": True, "results": results, "published": published,
            "left": len(deferred),
            "note": _left_note(len(deferred), "post"),
            "reminder": "Every post was written as a DRAFT. Nothing was "
                        "published and nothing was scheduled."}


def _edit_link(cred: dict, post_id: int) -> str:
    root = cred.get("rest_root") or ""
    origin = _origin(root)
    if not origin or not post_id:
        return ""
    return f"{origin}/wp-admin/post.php?post={post_id}&action=edit"


def _left_note(left: int, noun: str) -> str:
    if not left:
        return ""
    return (f"{left} {noun}{'s' if left != 1 else ''} were not reached in this "
            "run — press again to carry on. They are untouched, not failed.")


# ----------------------------------------------------------------- alt text
def _norm_src(url: str) -> str:
    """Compare two image URLs without the parts that do not identify a file."""
    p = urllib.parse.urlsplit(str(url or "").strip())
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host + p.path


_SIZE_SUFFIX = re.compile(r"-\d{2,5}x\d{2,5}(?=\.[A-Za-z0-9]+$)")


def _stem(url: str) -> str:
    name = os.path.basename(urllib.parse.urlsplit(str(url or "")).path)
    name = _SIZE_SUFFIX.sub("", name)
    name = re.sub(r"-scaled(?=\.[A-Za-z0-9]+$)", "", name)
    return os.path.splitext(name)[0]


def _find_media(cred: dict, src: str) -> tuple[int | None, str]:
    """The attachment this URL is, or why it is not one.

    WordPress publishes no "attachment by URL" endpoint, so this searches on
    the filename and then matches the **full source URL** — against the
    original and against every generated size, because a built page very often
    carries `photo-1024x768.jpg` where the library holds `photo.jpg`. Never the
    first search hit: that is the substring guess `hub/client_key.py` refuses.
    """
    stem = _stem(src)
    if not stem:
        return None, "That image URL has no filename in it."
    try:
        found = _call(cred, "GET", "wp/v2/media",
                      params={"search": stem, "per_page": 100})["body"]
    except Refused as exc:
        return None, str(exc)
    want = _norm_src(src)
    hits = []
    for item in (found or []):
        urls = [str(item.get("source_url") or "")]
        sizes = ((item.get("media_details") or {}).get("sizes") or {})
        for size in sizes.values():
            urls.append(str((size or {}).get("source_url") or ""))
        if any(_norm_src(u) == want for u in urls if u):
            hits.append(item)
    if len(hits) == 1:
        return int(hits[0]["id"]), ""
    if len(hits) > 1:
        return None, (f"{len(hits)} media items on this site claim that exact "
                      "URL, so nothing was changed.")
    return None, ("This image is not in the WordPress media library — it is a "
                  "theme asset, a page-builder background or a hotlink, so its "
                  "alt text lives in the template rather than in an "
                  "attachment. Use the Claude → WordPress path for it.")


def _alt_plan(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """One row per image, and the conflicts named.

    Alt text belongs to the **attachment**, not to the page: one photo used on
    three pages has one alt. So two pages asking for two different strings is a
    conflict this cannot resolve, and taking the last one silently would change
    a page nobody was looking at.
    """
    by_src: dict[str, dict] = {}
    for page in pages:
        for img in page.get("images") or []:
            src = str(img.get("src") or "").strip()
            if not src:
                continue
            new_alt = "" if img.get("decorative") else str(img.get("new_alt") or "")
            if not new_alt and not img.get("decorative"):
                continue
            key = _norm_src(src)
            row = by_src.setdefault(key, {"src": src, "alt": new_alt,
                                          "pages": [], "conflict": False,
                                          "others": set()})
            row["pages"].append(page.get("url", ""))
            if row["alt"] != new_alt:
                row["conflict"] = True
                row["others"].add(new_alt)
    plan, conflicts = [], []
    for row in by_src.values():
        row["pages"] = sorted({p for p in row["pages"] if p})
        if row["conflict"]:
            conflicts.append({
                "src": row["src"], "pages": row["pages"], "ok": False,
                "error": "Two pages ask for different alt text on this same "
                         "image, and the alt belongs to the image rather than "
                         "to the page. Settle on one before publishing it."})
            continue
        row.pop("others", None)
        row.pop("conflict", None)
        plan.append(row)
    return plan, conflicts


def publish_alt(client: str, urls: list[str] | None = None, *,
                actor: str = "") -> dict:
    """Write rewritten alt text onto the media library items it belongs to."""
    from . import alt_text
    try:
        cred = _credential(client)
    except Refused as exc:
        return {"error": str(exc)}
    state = cms_credentials.state(client)
    probe_row = state.get("probe") or {}
    if probe_row.get("ok") is False:
        return {"error": "The last connection check on this site failed: "
                         + str(probe_row.get("error") or "")}

    pages = alt_text.selected_pages(client, urls or None)
    plan, results = _alt_plan(pages)
    todo, deferred = plan[:MAX_IMAGES_PER_RUN], plan[MAX_IMAGES_PER_RUN:]

    started = time.time()
    changed = 0
    for row in todo:
        if time.time() - started > BUDGET_SECONDS:
            deferred.append(row)
            continue
        out = {"src": row["src"], "alt": row["alt"], "pages": row["pages"],
               "ok": False}
        media_id, why = _find_media(cred, row["src"])
        if not media_id:
            out["error"] = why
            results.append(out)
            continue
        try:
            _call(cred, "POST", f"wp/v2/media/{media_id}",
                  json_body={"alt_text": row["alt"]})
        except Refused as exc:
            out["error"] = str(exc)
            results.append(out)
            continue
        out.update({"ok": True, "media_id": media_id})
        if len(row["pages"]) > 1:
            out["note"] = ("This image is used on "
                           f"{len(row['pages'])} pages, and the alt applies to "
                           "all of them.")
        changed += 1
        results.append(out)

    if changed:
        audit.log("seo", "wordpress_alt_published", actor=actor or None,
                  client=client, detail=f"{changed} image(s)")
    return {"ok": True, "results": results, "changed": changed,
            "left": len(deferred),
            "note": _left_note(len(deferred), "image"),
            "reminder": "Alt text is a property of the media library item, so "
                        "the change applies everywhere that image is used."}
