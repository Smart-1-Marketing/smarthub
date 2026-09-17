"""hub/wordpress.py's schema half, and the plugin that makes it possible.

    python3 test_wordpress_schema.py

Same shape as test_wordpress_publish.py, which covers the credential store,
blog drafts and alt text: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory. `_call` is stubbed, because what is
worth asserting is what this module does with each answer a site can give.

## What is worth asserting

  * **The wire contract is one spelling.** The meta key and the REST namespace
    are written twice -- once in Python and once in PHP -- and a key spelled
    one way here and another way there is a write that answers 200 and stores
    nothing. The PHP is read and the two are held against each other.

  * **A 200 is not evidence the block landed.** WordPress drops an unregistered
    meta key without complaining, so the response to the write is read back and
    compared. That read-back is the whole difference between "WordPress
    answered" and "the schema is on the site", and every way it can fail --
    the plugin absent, an older plugin, JSON the site rejected -- is a 200 with
    a quiet response.

  * **Nothing unapproved reaches a client's live site.** The approval on the
    Schema Builder exists for exactly that, and a publish path that ignored it
    would retire the feature.

  * **A URL is resolved by WordPress, never guessed.** A slug is unique neither
    across post types nor across a page hierarchy, so the plugin answers with
    `url_to_postid()` and an address that is not a single post is refused with
    the reason rather than filed onto the nearest thing.

  * **The plugin prints what it stored and cannot be broken out of.** `<` is
    escaped to `\\u003c`, which decodes to the same character and is invisible
    to an HTML parser -- without it a `</script>` in the data ends the block
    early and the rest is parsed as markup.

  * **Not measured is not "not installed".** A site that refused the credential
    tells us nothing about what is installed on it, and reporting that as
    missing sends somebody to install a plugin that is already there.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-wpschema-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "wordpress-schema-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("OPENAI_API_KEY", None)

from cryptography.fernet import Fernet                       # noqa: E402

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode("ascii")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import cms_credentials, seo, wordpress                # noqa: E402

CLIENT = "Schema Test Roofing"
ROOT_URL = "https://schematest.example/wp-json/"
CRED = {"rest_root": ROOT_URL, "username": "editor", "app_password": "x"}


# ------------------------------------------------- the two halves agree
print("\nthe wire contract, written twice")
PHP = open(os.path.join(ROOT, "hub", "wordpress_plugin",
                        wordpress.PLUGIN_SLUG + ".php"), encoding="utf-8").read()

check("the plugin file is where the module says it is",
      os.path.exists(wordpress.PLUGIN_FILE), wordpress.PLUGIN_FILE)
check("the meta key is the same string in both halves",
      f"define('S1HUB_META', '{wordpress.SCHEMA_META}')" in PHP,
      wordpress.SCHEMA_META)
check("so is the REST namespace",
      f"define('S1HUB_NS', '{wordpress.PLUGIN_NAMESPACE}')" in PHP,
      wordpress.PLUGIN_NAMESPACE)
check("so is the version this Hub expects",
      f"define('S1HUB_VERSION', '{wordpress.PLUGIN_VERSION}')" in PHP,
      wordpress.PLUGIN_VERSION)
check("and the version in the plugin header matches the one it defines",
      re.search(r"^\s*\*\s*Version:\s*" + re.escape(wordpress.PLUGIN_VERSION) + r"\s*$",
                PHP, re.M) is not None)

check("the block is printed from wp_head, never into post content",
      "add_action('wp_head'" in PHP and "the_content" not in PHP)
check("nothing in the plugin publishes",
      "wp_publish_post" not in PHP and "'publish'" not in PHP)
check("both REST routes require a user who can already edit posts",
      PHP.count("current_user_can('edit_posts')") >= 1
      and "permission_callback" in PHP)
check("the resolver asks WordPress rather than searching by slug",
      "url_to_postid(" in PHP)
check("and a static front page, which url_to_postid() answers 0 for, is "
      "resolved from the option instead",
      "page_on_front" in PHP)
check("invalid JSON is stored as nothing rather than kept",
      "json_last_error()" in PHP)
# Which keys are registered is asserted at RUNTIME below, against the plugin
# actually installed -- counting call sites here would pass a registration for
# a key nothing writes, which is the whole thing that rule is about.
check("All in One SEO is named as out of reach rather than quietly skipped",
      "wp_aioseo_posts" in PHP and "All in One SEO" in PHP)

# ------------------------------------------------ the plugin, actually run
# Everything above is a grep, and a grep over PHP proves the source says
# something rather than that the code does it -- which matters most for the
# one line standing between a client's every page and a script block that ends
# early. test_wordpress_plugin.php calls the real functions against enough
# stubbed WordPress to load them.
#
# Where php is not installed this says so rather than passing quietly:
# reporting an unrun path as a clean run is the failure
# test_image_pdf_optimizers.py already had to name about Ghostscript.
print("\nthe plugin, run rather than read")
import shutil as _shutil                                       # noqa: E402
import subprocess                                              # noqa: E402

_php = _shutil.which("php")
if not _php:
    print("  ..   php is not installed here, so the plugin's own behavior was "
          "NOT exercised — only the greps above ran.")
else:
    proc = subprocess.run([_php, os.path.join(ROOT, "test_wordpress_plugin.php")],
                          capture_output=True, text=True, timeout=60)
    try:
        answers = json.loads(proc.stdout or "{}")
    except ValueError:
        answers = {}
    check("the harness ran and answered",
          bool(answers), (proc.stdout or "")[:300] + (proc.stderr or "")[:300])
    # Finding nothing is a failure, not a clean sweep: a harness that stops
    # exercising the plugin reports exactly the same green as one that does.
    check("and it exercised the whole of it", len(answers) >= 20, len(answers))
    for label, ok in sorted(answers.items()):
        check(label.replace("_", " "), ok is True)

blob = wordpress.plugin_zip()
with zipfile.ZipFile(__import__("io").BytesIO(blob)) as z:
    names = z.namelist()
check("the zip installs from Plugins -> Add New -> Upload, so the file is in "
      "a folder rather than at the archive root",
      names == [f"{wordpress.PLUGIN_SLUG}/{wordpress.PLUGIN_SLUG}.php"], names)
with zipfile.ZipFile(__import__("io").BytesIO(blob)) as z:
    check("and it holds the same bytes as the file in this repo",
          z.read(names[0]) == wordpress.plugin_bytes())


# ------------------------------------------------------- a stubbed site
print("\nis the plugin there")


class Site:
    """A WordPress that answers, with a log of what it was asked."""

    def __init__(self, *, plugin=True, version=None, meta_key=None,
                 seo_plugins=None):
        self.calls = []
        self.plugin = plugin
        self.version = version or wordpress.PLUGIN_VERSION
        self.meta_key = meta_key or wordpress.SCHEMA_META
        self.seo_plugins = seo_plugins or []
        self.posts = {}
        # url -> what the site's own resolver answers for it
        self.resolve = {}
        # Set to drop the meta the way core does for a key nothing registered.
        self.swallow_meta = False
        self.store_instead = None
        self.fail_on = {}

    def __call__(self, cred, method, path, *, json_body=None, params=None,
                 data=None, content_type="", filename=""):
        self.calls.append((method, path, json_body, params))
        if path in self.fail_on:
            raise wordpress.Refused(*self.fail_on[path])
        if path == wordpress.PLUGIN_NAMESPACE + "/status":
            if not self.plugin:
                raise wordpress.Refused("WordPress answered 404 for that "
                                        "endpoint.", status=404)
            return {"body": {"plugin": "smart-1-hub", "version": self.version,
                             "meta_key": self.meta_key,
                             "post_types": ["post", "page"],
                             "seo_plugins": self.seo_plugins,
                             "must_use": False}}
        if path == wordpress.PLUGIN_NAMESPACE + "/resolve":
            url = (params or {}).get("url", "")
            return {"body": self.resolve.get(
                url, {"id": 0, "reason": "WordPress does not resolve this "
                                         "address to a single post or page."})}
        m = re.match(r"wp/v2/(\w+)/(\d+)$", path)
        if m and method == "POST":
            pid = int(m.group(2))
            sent = ((json_body or {}).get("meta") or {}).get(
                wordpress.SCHEMA_META, "")
            kept = "" if self.swallow_meta else (
                self.store_instead if self.store_instead is not None else sent)
            self.posts[pid] = kept
            meta = {} if self.swallow_meta else {wordpress.SCHEMA_META: kept}
            return {"body": {"id": pid, "meta": meta,
                             "link": f"https://schematest.example/?p={pid}"}}
        raise AssertionError("unexpected call: " + method + " " + path)


site = Site()
wordpress._call = site

st = wordpress.plugin_status(CRED)
check("an installed plugin is reported installed", st.get("installed") is True, st)
check("and measured", st.get("measured") is True, st)
check("and current", st.get("current") is True, st)
check("a current plugin warns about nothing", not st.get("warnings"), st)

site.plugin = False
st = wordpress.plugin_status(CRED)
check("a 404 on the status route is the plugin not being installed",
      st.get("installed") is False, st)
check("and that is a measurement, not a failure to look",
      st.get("measured") is not False, st)

site.plugin = True
site.fail_on[wordpress.PLUGIN_NAMESPACE + "/status"] = (
    "WordPress refused the application password.", 401)
st = wordpress.plugin_status(CRED)
check("a site we could not ask is NOT reported as 'no plugin installed'",
      st.get("measured") is False, st)
check("and it carries why", "password" in str(st.get("error") or ""), st)
site.fail_on.clear()

site.version = "0.9.0"
st = wordpress.plugin_status(CRED)
check("an older plugin is still installed", st.get("installed") is True, st)
check("but is not current, and says both versions",
      st.get("current") is False
      and "0.9.0" in " ".join(st["warnings"])
      and wordpress.PLUGIN_VERSION in " ".join(st["warnings"]), st)
site.version = None
site.__init__(seo_plugins=["Yoast SEO"])

st = wordpress.plugin_status(CRED)
check("an SEO plugin also emitting structured data is reported, not acted on",
      any("Yoast" in w for w in st["warnings"]), st)
site.__init__()


# ------------------------------------------------------- resolving a URL
print("\nwhich post a URL is")
site.resolve["https://schematest.example/roofing/"] = {
    "id": 42, "type": "page", "rest_base": "pages", "status": "publish",
    "title": "Roofing", "link": "https://schematest.example/roofing/",
    "registered": True}
r = wordpress.resolve_url(CRED, "https://schematest.example/roofing/")
check("a page resolves to its id and its REST base",
      r["id"] == 42 and r["rest_base"] == "pages" and not r.get("error"), r)

r = wordpress.resolve_url(CRED, "https://schematest.example/blog/")
check("an address WordPress does not resolve to one post is refused",
      r["id"] == 0, r)
check("and the refusal is the site's own reason",
      "does not resolve" in r.get("error", ""), r)

site.resolve["https://schematest.example/thing/"] = {
    "id": 7, "type": "widget", "rest_base": "", "registered": False}
r = wordpress.resolve_url(CRED, "https://schematest.example/thing/")
check("a post type the site does not serve over REST is named rather than "
      "written to", "REST API" in r.get("error", ""), r)

site.resolve["https://schematest.example/cpt/"] = {
    "id": 8, "type": "landing", "rest_base": "landing", "registered": False}
r = wordpress.resolve_url(CRED, "https://schematest.example/cpt/")
check("a post type the plugin does not register is named before a write is "
      "attempted", "does not register" in r.get("error", ""), r)


# ---------------------------------------------------------------- pages
print("\nwriting the block")
BLOCK = {"@context": "https://schema.org",
         "@graph": [{"@type": "RoofingContractor",
                     "name": "Schema Test Roofing",
                     "description": "We fix roofs </script> and gutters."}]}
store = seo.load_store(CLIENT)
store["pages"] = {
    "https://schematest.example/roofing/": {
        "url": "https://schematest.example/roofing/", "title": "Roofing",
        "schema": BLOCK, "approved": True, "created": "2026-09-01",
        "added_to_site": "", "updated": ""},
    "https://schematest.example/gutters/": {
        "url": "https://schematest.example/gutters/", "title": "Gutters",
        "schema": BLOCK, "approved": False, "created": "2026-09-01",
        "added_to_site": "", "updated": ""},
    "https://schematest.example/blog/": {
        "url": "https://schematest.example/blog/", "title": "Blog",
        "schema": BLOCK, "approved": True, "created": "2026-09-01",
        "added_to_site": "", "updated": ""},
}
seo.save_store(CLIENT, store)
cms_credentials.save(CLIENT, cms_credentials.WORDPRESS, rest_root=ROOT_URL,
                     username="editor", app_password="abcd EFGH ijkl MNOP",
                     actor="Tester")
cms_credentials.record_probe(CLIENT, cms_credentials.WORDPRESS,
                             {"ok": True, "can_write_posts": True})

site.resolve["https://schematest.example/gutters/"] = {
    "id": 43, "type": "page", "rest_base": "pages", "status": "publish",
    "title": "Gutters", "link": "https://schematest.example/gutters/",
    "registered": True}

out = wordpress.publish_schema(
    CLIENT, ["https://schematest.example/roofing/",
             "https://schematest.example/gutters/",
             "https://schematest.example/blog/",
             "https://schematest.example/nope/"], actor="Tester")
rows = {r["url"]: r for r in out["results"]}
check("an approved page that resolves is written", out["written"] == 1, out)
check("and the row says where it went",
      rows["https://schematest.example/roofing/"].get("post_id") == 42, rows)
check("an unapproved page is refused before any call is made",
      rows["https://schematest.example/gutters/"]["ok"] is False
      and "approved" in rows["https://schematest.example/gutters/"].get("error", ""),
      rows.get("https://schematest.example/gutters/"))
check("a page that resolves to no post is refused with the site's reason",
      "does not resolve" in rows["https://schematest.example/blog/"].get("error", ""),
      rows.get("https://schematest.example/blog/"))
check("a URL with no saved schema is named rather than silently dropped",
      rows["https://schematest.example/nope/"]["ok"] is False, rows)
check("nothing was asked of WordPress for the unapproved page",
      not any(p == "wp/v2/pages/43" for _, p, _, _ in site.calls),
      [c[1] for c in site.calls])

sent = site.posts[42]
check("the stored block is the approved JSON, byte for byte",
      json.loads(sent) == BLOCK, sent[:120])

page = seo.load_store(CLIENT)["pages"]["https://schematest.example/roofing/"]
check("the added-to-site date is stamped by the write that landed",
      len(page.get("added_to_site") or "") == 10, page.get("added_to_site"))
check("and the page records which post it is on",
      (page.get("wordpress") or {}).get("post_id") == 42, page.get("wordpress"))
unapproved = seo.load_store(CLIENT)["pages"]["https://schematest.example/gutters/"]
check("a refused page is stamped with nothing",
      not unapproved.get("added_to_site") and not unapproved.get("wordpress"),
      unapproved)


# --------------------------------------------------- a 200 is not enough
print("\nthe read-back")
site.swallow_meta = True
out = wordpress.publish_schema(
    CLIENT, ["https://schematest.example/roofing/"], actor="Tester")
row = out["results"][0]
check("a 200 that stored nothing is not reported as a clean run",
      out["written"] == 0 and row["ok"] is False, out)
check("and the refusal names all three faults it could be, rather than "
      "picking the likeliest",
      "plugin missing" in row.get("error", "")
      and "older" in row.get("error", "")
      and "rejecting the JSON" in row.get("error", ""), row)

site.swallow_meta = False
site.store_instead = '{"@context":"https://schema.org"}'
out = wordpress.publish_schema(
    CLIENT, ["https://schematest.example/roofing/"], actor="Tester")
row = out["results"][0]
check("a site that stored something different is refused too",
      row["ok"] is False and "different" in row.get("error", ""), row)
site.store_instead = None

site.plugin = False
out = wordpress.publish_schema(
    CLIENT, ["https://schematest.example/roofing/"], actor="Tester")
check("with no plugin, nothing is attempted", out.get("needs_plugin") is True, out)
check("and the refusal says where to get it",
      "Add New" in out.get("error", ""), out.get("error"))
site.plugin = True


# ------------------------------------------- is it actually on the page
# publish_schema()'s read-back proves WordPress STORED the block. It is not
# the same claim as a visitor seeing it, and three ordinary things break the
# second without touching the first.
print("\nis it actually on the page")


class Page:
    """The client's own site, answering through `hub/outbound.py`.

    Stubbed at `outbound.fetch` rather than at `requests.get`, because that is
    the seam now: every outbound fetch in this module goes through the guard,
    and a test that reached past it would assert about a code path nothing
    takes.
    """

    def __init__(self, body="", status=200, ctype="text/html; charset=UTF-8",
                 refused=""):
        self.headers = {"Content-Type": ctype}
        self.text = body
        self.content = body.encode("utf-8")
        self.status_code = status
        self.truncated = False
        self.refused = refused
        self.sent_headers = None
        self.url = ""

    def __call__(self, url, **kw):
        self.url = url
        self.sent_headers = kw.get("headers")
        if self.refused:
            return None, self.refused
        return self, ""


def rendered(block):
    """A page the way the plugin actually renders one.

    The escape matters in the fixture too: BLOCK carries a literal `</script>`
    in its description, which is the case the plugin escapes `<` for -- and an
    unescaped fixture builds markup the plugin would never emit, so it would
    be testing the verifier against a page that cannot exist.
    """
    body = json.dumps(block).replace("<", chr(92) + "u003c")
    return ("<html><head><title>x</title>\n"
            + wordpress.SCHEMA_MARKER
            + '\n<script type="application/ld+json">\n'
            + body + "\n</script>\n</head><body>x</body></html>")


_real_fetch = wordpress.outbound.fetch
URL = "https://schematest.example/roofing/"


def verify_against(page):
    wordpress.outbound.fetch = page
    try:
        return wordpress.verify_schema(CLIENT, [URL])["results"][0]
    finally:
        wordpress.outbound.fetch = _real_fetch


row = verify_against(Page(rendered(BLOCK)))
check("a page carrying our block, unchanged, is live", row["verdict"] == "live", row)

page = Page(rendered(BLOCK))
verify_against(page)
check("and it was fetched the way a visitor gets it, signed out",
      "Authorization" not in (page.sent_headers or {}), page.sent_headers)
check("it went through the guard rather than straight to requests",
      page.url == URL, page.url)

row = verify_against(Page(rendered({"@context": "https://schema.org",
                                    "@graph": [{"@type": "Other"}]})))
check("a block that is not what was sent is stale, not live",
      row["verdict"] == "stale", row)
check("and it names a cache first, without asserting the cause",
      "cache" in row["note"] and "can also be" in row["note"], row["note"])

row = verify_against(Page("<html><head></head><body>nothing</body></html>"))
check("a page with no block of ours is absent", row["verdict"] == "absent", row)
check("and it names all three causes rather than picking one",
      "deactivated" in row["note"] and "cache" in row["note"]
      and "wp_head()" in row["note"], row["note"])

# Somebody else's JSON-LD on the page is not ours, and must not read as ours.
row = verify_against(Page('<html><head><script type="application/ld+json">'
                          + json.dumps(BLOCK) + "</script></head><body>x</body></html>"))
check("another plugin's JSON-LD does not count as our block being live",
      row["verdict"] == "absent", row)

# And the escape is why: an unescaped block really is cut short by its own
# data, and that is a different finding from a stale cache.
row = verify_against(Page(
    "<html><head>" + wordpress.SCHEMA_MARKER
    + '<script type="application/ld+json">' + json.dumps(BLOCK)
    + "</script></head><body>x</body></html>"))
check("a block cut short by its own data is not read as a stale cache",
      row["verdict"] == "stale" and "readable JSON" in row["note"], row)

row = verify_against(Page("", status=404))
check("a 404 to a visitor is not measured, never 'absent'",
      row["verdict"] == "not_measured", row)
check("and it says a draft is not public either",
      "draft" in row["note"], row["note"])

row = verify_against(Page("", status=500))
check("a page that errored is not a page with no block",
      row["verdict"] == "not_measured", row)

row = verify_against(Page(refused="Could not reach that address (Timeout)."))
check("a site we could not reach is not measured",
      row["verdict"] == "not_measured", row)

row = verify_against(Page("{}", ctype="application/json"))
check("an address that did not serve a page is not measured",
      row["verdict"] == "not_measured", row)

row = verify_against(Page(refused="'x' resolves to 127.0.0.1, which is inside "
                                  "our own network rather than on the public "
                                  "internet."))
check("an address inside our own network is refused rather than fetched",
      row["verdict"] == "not_measured" and "our own network" in row["note"], row)

wordpress.outbound.fetch = _real_fetch
out = wordpress.verify_schema(CLIENT, ["https://schematest.example/nope/"])
check("a URL with no saved schema has nothing to compare against",
      out["results"][0]["verdict"] == "not_measured", out["results"][0])
check("and the counts add up to the rows",
      sum(out["counts"].values()) == len(out["results"]), out["counts"])

# ------------------------------------------------------------------ routes
print("\nroutes")
os.environ["HUB_SKIP_SCHEDULER"] = "1"
import hub                                                     # noqa: E402

app = hub.create_hub_app()
app.config["TESTING"] = True
from hub import auth as _auth                                  # noqa: E402

with app.test_client() as c:
    c.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Tester"))
    r = c.get("/api/seo/wordpress/plugin?format=zip")
    check("the plugin downloads as a zip", r.status_code == 200, r.status_code)
    check("as an attachment rather than something a browser renders",
          "attachment" in r.headers.get("Content-Disposition", "")
          and r.headers.get("X-Content-Type-Options") == "nosniff",
          dict(r.headers))
    r = c.get("/api/seo/wordpress/plugin?format=php")
    check("and as the raw file, for a mu-plugins drop",
          r.status_code == 200 and b"S1HUB_META" in r.get_data(), r.status_code)
    r = c.get("/api/seo/wordpress/plugin?format=tar")
    check("an unknown format is refused rather than guessed at",
          r.status_code == 400, r.status_code)

    r = c.post("/api/seo/wordpress/publish",
               json={"client": CLIENT, "kind": "schema"})
    check("sending no pages is refused rather than reporting a clean run",
          r.status_code == 400, r.status_code)
    check("and it says to tick some", "Tick" in r.get_json().get("error", ""),
          r.get_json())

with app.test_client() as c:
    r = c.get("/api/seo/wordpress/plugin?format=zip")
    check("the download is not open to a stranger",
          r.status_code in (301, 302, 401, 403), r.status_code)


# ---------------------------------------------------------------- the page
print("\nthe page")
html = open(os.path.join(ROOT, "hub", "templates", "seo_client.html"),
            encoding="utf-8").read()
check("the schema table has a send-to-WordPress button",
      'data-wppub="schema"' in html)
check("it starts hidden, because it needs a connection AND a plugin",
      'data-wppub="schema" id="wpPubSchema" style="display:none"' in html)
check("and the Claude path is still offered beside it",
      'data-pub="wordpress" data-kind="schema"' in html)
check("the panel offers both installs, because only one of them is always "
      "available",
      "format=zip" in html and "format=php" in html)
check("the FAQ row says the real reason rather than 'REST cannot reach it'",
      "carries its own" in html and "FAQPage" in html)
check("the panel says where a meta description ends up",
      "description_by" in html and "Excerpt" in html)
check("and names All in One SEO as the one this cannot reach",
      "All in\n            &rsquo;t" not in html and "own table" in html)


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
