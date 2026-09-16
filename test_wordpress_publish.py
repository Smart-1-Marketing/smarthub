"""hub/cms_credentials.py and hub/wordpress.py — test harness.

    python3 test_wordpress_publish.py

Same shape as test_blog_publish.py: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Nothing here reaches a real WordPress: `_call` is
stubbed, because what is worth asserting is what this module does with each
answer a site can give — which is the arrangement `test_ghl_forms.py` settled
on for the same reason.

## What is worth asserting

  * **The secret is sealed, and the three states it can be found in are kept
    apart.** Sealed-and-readable, stored-in-the-clear (no key was configured)
    and cannot-be-decrypted (the key was rotated) send somebody to three
    different places, and the third must never read as "no credential" — that
    is the failure `connected_accounts_result()` cost Google Finder months
    over. The file on disk is read back directly, because "we encrypt it" is a
    claim about bytes.

  * **Nothing hands the secret back.** `state()` is what every route answers
    with and it is built as a subset rather than by deleting a key, so the
    assertion is over every value it returns rather than over the one name
    somebody remembered to strip.

  * **Everything lands as a draft.** Asserted on the payload rather than on
    the prose that promises it.

  * **A flagged post is refused by name.** `blog_spec.scan_forbidden()` is the
    client's own never-mention list, and the whole reason it exists is that
    this copy must not reach their site unread.

  * **A term matches exactly or is created.** `?search=` is a search: asked for
    "Roofing" WordPress answers "Roof Repair" too, and taking it would file a
    post under a category nobody chose.

  * **Two pages asking for two different alt texts on one image is a
    conflict.** Alt belongs to the attachment, so last-one-wins would change a
    page nobody was looking at.

  * **A 401 has two meanings and they are different jobs.** A revoked password
    and a host stripping the Authorization header are indistinguishable from
    the status code alone, and only one of them is fixed by generating a new
    password.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-wppub-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "wordpress-publish-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("OPENAI_API_KEY", None)

from cryptography.fernet import Fernet                      # noqa: E402

KEY_A = Fernet.generate_key().decode("ascii")
KEY_B = Fernet.generate_key().decode("ascii")
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import blog_spec, cms_credentials, seo, wordpress   # noqa: E402

CLIENT = "WP Test Roofing"
SECRET = "abcd EFGH ijkl MNOP qrst UVWX"
ROOT_URL = "https://wptest.example/wp-json/"


# ------------------------------------------------------------ the credential
print("\nthe credential store")
saved = cms_credentials.save(CLIENT, cms_credentials.WORDPRESS,
                             rest_root=ROOT_URL, username="editor",
                             app_password=SECRET, actor="Tester")
check("saving reports that it sealed", saved.get("sealed") is True, saved)

raw = open(cms_credentials._path(CLIENT, cms_credentials.WORDPRESS)).read()
check("the secret is not on disk in the clear",
      "abcdEFGH" not in raw and SECRET not in raw, raw[:200])
check("and neither is the spaced form it was pasted in", "qrst UVWX" not in raw)

got = cms_credentials.get(CLIENT)
check("get() gives back the password with the spaces stripped",
      got.get("app_password") == "abcdEFGHijklMNOPqrstUVWX", got.get("app_password"))
check("WordPress's own spacing round-trips to the same secret",
      cms_credentials.normalise_app_password("abcd EFGH ijkl MNOP qrst UVWX")
      == cms_credentials.normalise_app_password("abcdEFGHijklMNOPqrstUVWX"))

st = cms_credentials.state(CLIENT)
blob = json.dumps(st)
check("state() carries no secret in any of its values",
      "abcdEFGH" not in blob and SECRET not in blob, blob[:300])
check("state() says it is connected and readable",
      st["connected"] and st["readable"] and st["sealed"], st)
check("state() names who saved it and when",
      st["saved_by"] == "Tester" and st["saved_at"], st)

# A rotated key is a state with a fix, never an absence.
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_B
rot = cms_credentials.state(CLIENT)
check("a rotated key still reads as connected", rot["connected"] is True, rot)
check("but not as readable", rot["readable"] is False, rot)
check("and the refusal names the variable",
      "TOKEN_ENCRYPTION_KEY" in rot["error"], rot["error"])
check("get() refuses rather than answering an empty password",
      cms_credentials.get(CLIENT).get("error") and
      not cms_credentials.get(CLIENT).get("app_password"))
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A

# No key at all: stored, and said out loud rather than passing as encrypted.
os.environ.pop("TOKEN_ENCRYPTION_KEY")
check("with no key, encryption_state says so",
      cms_credentials.encryption_state()["configured"] is False)
check("and names the variable that would fix it",
      "TOKEN_ENCRYPTION_KEY" in cms_credentials.encryption_state()["note"])
cms_credentials.save("Clear Text Co", cms_credentials.WORDPRESS,
                     rest_root=ROOT_URL, username="u", app_password="p",
                     actor="Tester")
clear = cms_credentials.state("Clear Text Co")
check("a credential saved with no key reports sealed=False",
      clear["sealed"] is False and clear["readable"] is True, clear)
os.environ["TOKEN_ENCRYPTION_KEY"] = KEY_A
check("a clear-text credential is still readable once a key appears",
      cms_credentials.get("Clear Text Co").get("app_password") == "p")

check("a save with no password is refused by name",
      "password" in (cms_credentials.save(
          CLIENT, cms_credentials.WORDPRESS, rest_root=ROOT_URL,
          username="u", app_password="  ").get("error") or "").lower())
check("an unknown CMS is refused",
      "Unknown CMS" in (cms_credentials.save(
          CLIENT, "squarespace", rest_root=ROOT_URL, username="u",
          app_password="x").get("error") or ""))


# ------------------------------------------------------------- the refusals
print("\nwhat a 401 means")
htaccess = wordpress._explain(401, {"code": "rest_not_logged_in"}, sent_auth=True)
revoked = wordpress._explain(401, {"code": "incorrect_password"}, sent_auth=True)
check("a stripped Authorization header is named as one",
      "Authorization" in htaccess and "htaccess" in htaccess, htaccess)
check("a revoked password is a different sentence", htaccess != revoked)
check("and it points at where a new one is generated",
      "Application Passwords" in revoked, revoked)
check("403 names the role rather than the password",
      "role" in wordpress._explain(403, {}, True).lower())
check("404 does not read as a bad credential",
      "password" not in wordpress._explain(404, {}, True).lower())


print("\nendpoints and discovery")
check("a /wp-json/ root joins a path",
      wordpress._endpoint("https://x.test/wp-json/", "wp/v2/posts")
      == "https://x.test/wp-json/wp/v2/posts")
check("a plain-permalinks root joins it too",
      wordpress._endpoint("https://x.test/?rest_route=/", "wp/v2/posts")
      == "https://x.test/?rest_route=/wp/v2/posts",
      wordpress._endpoint("https://x.test/?rest_route=/", "wp/v2/posts"))
check("http is refused by name before anything is sent",
      "HTTPS" in (wordpress.discover("http://x.test").get("error") or ""),
      wordpress.discover("http://x.test"))
check("no site URL is its own refusal",
      "Client Setup" in (wordpress.discover("").get("error") or ""))


# ----------------------------------------------------------- a stubbed site
print("\nposts")


class Site:
    """A WordPress that answers, with a log of what it was asked."""

    def __init__(self):
        self.calls = []
        # "Heating & Cooling" is the trap: WordPress's `?search=` is a LIKE
        # over the name, so asking for "Heating" answers this row as well.
        # Taking it files the post under a category nobody chose.
        self.categories = [{"id": 5, "name": "Roof Repair"},
                           {"id": 6, "name": "Heating & Cooling"}]
        self.tags = []
        self.users = [{"id": 1, "name": "Editor Person"}]
        self.posts = {}
        self.media = {}
        self.next_id = 100
        self.fail_on = {}

    def __call__(self, cred, method, path, *, json_body=None, params=None,
                 data=None, content_type="", filename=""):
        self.calls.append((method, path, json_body, params))
        if path in self.fail_on:
            raise wordpress.Refused(self.fail_on[path])
        if path == wordpress.PLUGIN_NAMESPACE + "/status":
            # This site has no Smart 1 Hub plugin, which is the ordinary case
            # for blogs and alt text: neither needs one. What it asserts is
            # that the blog path still publishes without it, and still falls
            # back to the Excerpt for the meta description.
            raise wordpress.Refused("WordPress answered 404 for that endpoint.",
                                    status=404)
        if path == "wp/v2/categories" and method == "GET":
            q = (params or {}).get("search", "").lower()
            return {"body": [c for c in self.categories if q in c["name"].lower()]}
        if path == "wp/v2/categories" and method == "POST":
            self.next_id += 1
            row = {"id": self.next_id, "name": json_body["name"]}
            self.categories.append(row)
            return {"body": row}
        if path == "wp/v2/tags" and method == "GET":
            q = (params or {}).get("search", "").lower()
            return {"body": [t for t in self.tags if q in t["name"].lower()]}
        if path == "wp/v2/tags" and method == "POST":
            self.next_id += 1
            row = {"id": self.next_id, "name": json_body["name"]}
            self.tags.append(row)
            return {"body": row}
        if path == "wp/v2/users" and method == "GET":
            q = (params or {}).get("search", "").lower()
            return {"body": [u for u in self.users if q in u["name"].lower()]}
        if path == "wp/v2/media" and method == "GET":
            return {"body": list(self.media.values())}
        if path == "wp/v2/media" and method == "POST":
            self.next_id += 1
            return {"body": {"id": self.next_id}}
        if path.startswith("wp/v2/media/"):
            mid = int(path.rsplit("/", 1)[1])
            self.media.setdefault(mid, {"id": mid})["alt_text"] = json_body["alt_text"]
            return {"body": self.media[mid]}
        if path == "wp/v2/posts" and method == "POST":
            self.next_id += 1
            row = dict(json_body, id=self.next_id,
                       link=f"https://wptest.example/?p={self.next_id}")
            self.posts[self.next_id] = row
            return {"body": row}
        if path.startswith("wp/v2/posts/"):
            pid = int(path.rsplit("/", 1)[1])
            self.posts[pid] = dict(self.posts.get(pid, {}), **(json_body or {}),
                                   id=pid,
                                   link=f"https://wptest.example/?p={pid}")
            return {"body": self.posts[pid]}
        raise AssertionError("unexpected call: " + method + " " + path)


site = Site()
wordpress._call = site

CRED = {"rest_root": ROOT_URL, "username": "editor", "app_password": "x"}

ids, notes = wordpress._term_ids(CRED, "categories", ["Heating"], may_create=1)
check("'Heating' does not take the search hit 'Heating & Cooling'",
      6 not in ids, (ids, site.categories))
check("it is created with the exact name",
      any(c["name"] == "Heating" for c in site.categories), site.categories)
check("and the creation is said out loud",
      any("Created" in n for n in notes), notes)

ids2, notes2 = wordpress._term_ids(CRED, "categories", ["Roof Repair"], may_create=1)
check("an exact existing category is matched rather than duplicated",
      ids2 == [5], ids2)

ids3, notes3 = wordpress._term_ids(CRED, "categories", ["Brand New A", "Brand New B"],
                                   may_create=1)
check("the create budget is honored", len(ids3) == 1, ids3)
check("and what it would not create is named",
      any("Brand New B" in n for n in notes3), notes3)

aid, note = wordpress._author_id(CRED, "Somebody Else", {"user_name": "Editor Person"})
check("an author who is not on the site gets no id", aid is None)
check("and the note names who it went under instead",
      "Editor Person" in note, note)
aid2, note2 = wordpress._author_id(CRED, "Editor Person", {})
check("an exact author match resolves", aid2 == 1 and not note2, (aid2, note2))
site.users.append({"id": 9, "name": "Editor Person"})
aid3, note3 = wordpress._author_id(CRED, "Editor Person", {"user_name": "Editor Person"})
check("two users with one name resolve to neither", aid3 is None, aid3)
check("and it says how many", "2 users" in note3, note3)
site.users = [{"id": 1, "name": "Editor Person"}]


print("\nwhat is refused before a call is made")
check("an unwritten post is refused by name",
      "not been written" in wordpress._post_blockers({"status": "planned"}))
check("a post with no content is refused even when marked written",
      wordpress._post_blockers({"status": "written", "content": "  "}))
flagged = wordpress._post_blockers(
    {"status": "written", "content": "<p>x</p>",
     "flags": [{"term": "guarantee"}]})
check("a flagged post is refused", bool(flagged))
check("and the refusal quotes the term", "guarantee" in flagged, flagged)

payload = wordpress._post_payload(
    {"title": "T", "content": "<p>c</p>", "slug": "t",
     "meta_description": "m"},
    category_ids=[5], tag_ids=[], author_id=None, media_id=None)
check("every post is a draft", payload["status"] == "draft", payload)
check("nothing carries a publish date", "date" not in payload, payload)
check("the meta description goes to the excerpt", payload["excerpt"] == "m")

pending = wordpress._upload_featured(
    CRED, {"image": {"url": "https://x/y.jpg", "status": "pending"}})
check("a pending featured image is not uploaded", pending[0] is None)
check("and it says why", "approval" in pending[1], pending[1])


# ------------------------------------------------------------- end to end
print("\npublishing posts")
store = seo.load_store(CLIENT)
store["blogs"] = {"posts": [
    {"id": 1, "title": "Roof Care In Winter", "slug": "roof-care-in-winter",
     "content": "<p>Body.</p>", "meta_description": "How to care for a roof.",
     "categories": ["Roof Repair"], "tags": ["winter"], "status": "written",
     "flags": [], "date": "2026-01-05"},
    {"id": 2, "title": "Never Say This", "content": "<p>We guarantee it.</p>",
     "categories": [], "tags": [], "status": "written",
     "flags": [{"term": "guarantee"}]},
    {"id": 3, "title": "Not Written Yet", "content": "", "status": "planned",
     "categories": [], "tags": []},
]}
seo.save_store(CLIENT, store)
cms_credentials.record_probe(CLIENT, cms_credentials.WORDPRESS, {
    "ok": True, "user_id": 1, "user_name": "Editor Person",
    "can_write_posts": True, "can_upload_media": True,
    "unfiltered_html": False, "seo_plugin": "Yoast SEO", "warnings": []})

out = wordpress.publish_posts(CLIENT, [1, 2, 3], actor="Tester")
rows = {r["id"]: r for r in out["results"]}
check("the written post went in", rows[1]["ok"] is True, rows[1])
check("as a draft", site.posts[rows[1]["post_id"]]["status"] == "draft")
check("the flagged post did not", rows[2]["ok"] is False, rows[2])
check("and the unwritten one did not", rows[3]["ok"] is False, rows[3])
check("every item reports its own outcome", len(out["results"]) == 3)
check("the count is of what actually went", out["published"] == 1, out)
check("and the panel is reminded nothing was published",
      "DRAFT" in out["reminder"], out["reminder"])
check("with no plugin on the site, the description falls back to the Excerpt",
      any("Excerpt" in n for n in rows[1]["notes"]), rows[1]["notes"])
check("and it still says to copy it across, because on this site it is true",
      any("copy it across" in n for n in rows[1]["notes"]), rows[1]["notes"])
check("the Excerpt really carries it",
      site.posts[rows[1]["post_id"]]["excerpt"] == "How to care for a roof.")
check("and nothing was written to a meta key nothing reads",
      "meta" not in site.posts[rows[1]["post_id"]], site.posts[rows[1]["post_id"]])
check("the draft carries a link back into wp-admin",
      "post.php?post=" in rows[1]["edit_url"], rows[1]["edit_url"])

after = seo.load_store(CLIENT)
p1 = [p for p in after["blogs"]["posts"] if p["id"] == 1][0]
check("the WordPress id is recorded on the post",
      p1["wordpress"]["post_id"] == rows[1]["post_id"], p1.get("wordpress"))

before_count = len(site.posts)
again = wordpress.publish_posts(CLIENT, [1], actor="Tester")
check("a second press updates rather than creating a second draft",
      len(site.posts) == before_count, (before_count, len(site.posts)))
check("and says so on the row", again["results"][0]["updated"] is True)

# Post 1 now carries a WordPress id, so the second press addresses the post
# it made. The refusal is set on that path rather than on the create one.
site.fail_on["wp/v2/posts/" + str(p1["wordpress"]["post_id"])] = (
    "WordPress refused the application password.")
broke = wordpress.publish_posts(CLIENT, [1])
check("a post that fails carries the reason rather than a status code",
      "application password" in (broke["results"][0].get("error") or ""),
      broke["results"][0])
site.fail_on.clear()


# ------------------------------------------- the description, where it can go
# The last step of publishing a blog post that a person still had to do twice.
print("\nthe meta description, once the plugin makes the field writable")


class SiteWithSEO(Site):
    """The same site, with Yoast and the current plugin on it."""

    def __init__(self, *, key="_yoast_wpseo_metadesc", by="Yoast SEO",
                 swallow=False, version=None):
        super().__init__()
        self.key, self.by, self.swallow = key, by, swallow
        self.version = version or wordpress.PLUGIN_VERSION

    def __call__(self, cred, method, path, **kw):
        if path == wordpress.PLUGIN_NAMESPACE + "/status":
            self.calls.append((method, path, None, None))
            return {"body": {"plugin": "smart-1-hub", "version": self.version,
                             "meta_key": wordpress.SCHEMA_META,
                             "post_types": ["post", "page"],
                             "seo_plugins": [self.by] if self.by else [],
                             "description_key": self.key,
                             "description_by": self.by, "must_use": False}}
        out = super().__call__(cred, method, path, **kw)
        if path.startswith("wp/v2/posts") and method == "POST":
            body = kw.get("json_body") or {}
            sent = (body.get("meta") or {})
            # Core drops an unregistered meta key without complaining, which
            # is what an older plugin on the site looks like from here.
            out["body"]["meta"] = {} if self.swallow else dict(sent)
        return out


def publish_one(site_obj, description="How to care for a roof."):
    wordpress._call = site_obj
    st = seo.load_store(CLIENT)
    post = st["blogs"]["posts"][0]
    post["meta_description"] = description
    post.pop("wordpress", None)
    seo.save_store(CLIENT, st)
    res = wordpress.publish_posts(CLIENT, [1], actor="Tester")
    return res["results"][0], site_obj


row, s_ok = publish_one(SiteWithSEO())
sent = [c for c in s_ok.calls if c[1].startswith("wp/v2/posts")][0][2]
check("the description goes into the SEO plugin's own field",
      (sent.get("meta") or {}).get("_yoast_wpseo_metadesc")
      == "How to care for a roof.", sent.get("meta"))
check("and into the Excerpt as well, because a theme prints that on the index",
      sent.get("excerpt") == "How to care for a roof.", sent.get("excerpt"))
check("the row names whose field it was", row.get("meta_description_field")
      == "Yoast SEO", row)
check("and says there is nothing to copy across",
      any("Nothing to copy across" in n for n in row["notes"]), row["notes"])
check("the plugin is asked once for the run, not once per post",
      sum(1 for c in s_ok.calls
          if c[1] == wordpress.PLUGIN_NAMESPACE + "/status") == 1,
      [c[1] for c in s_ok.calls])

row, _ = publish_one(SiteWithSEO(swallow=True))
check("a 200 that stored no description is not reported as written",
      row.get("meta_description_field") is None, row)
check("and it names an older plugin as the cause, with what to do",
      any("older than this Hub" in n for n in row["notes"]), row["notes"])
check("the post itself still went in — the description is an improvement on "
      "this path, not a precondition for it", row["ok"] is True, row)

row, _ = publish_one(SiteWithSEO(key="", by="All in One SEO"))
check("a plugin whose field this cannot reach falls back to the Excerpt",
      any("Excerpt" in n for n in row["notes"]), row["notes"])
check("and names it rather than saying nothing",
      any("All in One SEO" in n for n in row["notes"]), row["notes"])

class SiteThatBreaksOnStatus(SiteWithSEO):
    """A status route that raises something other than a Refused.

    plugin_status() catches a Refused, so the guard around it in
    publish_posts() only earns its keep against everything else -- and what it
    protects is somebody's blog post. The description is an improvement on
    this path, not a precondition for it.
    """

    def __call__(self, cred, method, path, **kw):
        if path == wordpress.PLUGIN_NAMESPACE + "/status":
            raise ValueError("something nobody anticipated")
        return Site.__call__(self, cred, method, path, **kw)


row, _ = publish_one(SiteThatBreaksOnStatus())
check("a status route that raises does not cost somebody the blog post",
      row["ok"] is True, row)
check("and the description still falls back to the Excerpt",
      any("Excerpt" in n for n in row["notes"]), row["notes"])

LONG = "A roof is a thing. " * 12
row, _ = publish_one(SiteWithSEO(), description=LONG)
sent = [c for c in _.calls if c[1].startswith("wp/v2/posts")][0][2]
check("a description past the snippet length is written as approved",
      (sent.get("meta") or {}).get("_yoast_wpseo_metadesc") == LONG)
check("...and reported rather than cut",
      any("truncates a snippet" in n for n in row["notes"]), row["notes"])

wordpress._call = site

# ---------------------------------------------------------------- alt text
print("\nalt text")
plan, conflicts = wordpress._alt_plan([
    {"url": "/a", "images": [{"src": "https://s/img/hero.jpg", "new_alt": "A roof"}]},
    {"url": "/b", "images": [{"src": "https://s/img/hero.jpg", "new_alt": "A roof"}]},
    {"url": "/c", "images": [{"src": "https://s/img/two.jpg", "new_alt": "One"}]},
    {"url": "/d", "images": [{"src": "https://s/img/two.jpg", "new_alt": "Other"}]},
    {"url": "/e", "images": [{"src": "https://s/img/spacer.gif", "decorative": True,
                              "new_alt": ""}]},
])
by_src = {r["src"]: r for r in plan}
check("one image used on two pages is one row",
      len(by_src["https://s/img/hero.jpg"]["pages"]) == 2, plan)
check("two pages wanting two different alts is a conflict",
      any("two.jpg" in c["src"] for c in conflicts), conflicts)
check("the conflict says why it cannot be resolved",
      "belongs to the image" in conflicts[0]["error"], conflicts[0]["error"])
check("a decorative image keeps its empty alt as a real change",
      by_src["https://s/img/spacer.gif"]["alt"] == "", by_src)

site.media = {
    7: {"id": 7, "source_url": "https://s/img/hero.jpg",
        "media_details": {"sizes": {"large": {"source_url":
                                              "https://s/img/hero-1024x768.jpg"}}}},
}
check("a generated size resolves to its attachment",
      wordpress._find_media(CRED, "https://s/img/hero-1024x768.jpg")[0] == 7)
check("www is not a different image",
      wordpress._find_media(CRED, "https://www.s/img/hero.jpg")[0] == 7)
missing = wordpress._find_media(CRED, "https://s/img/theme-bg.png")
check("an image that is not an attachment is named, not failed",
      missing[0] is None and "media library" in missing[1], missing)

import hub.alt_text as _alt                                   # noqa: E402
_alt.selected_pages = lambda client, urls=None: [
    {"url": "/a", "images": [{"src": "https://s/img/hero.jpg",
                              "alt": "", "new_alt": "A tiled roof"}]},
    {"url": "/b", "images": [{"src": "https://s/img/hero.jpg",
                              "alt": "", "new_alt": "A tiled roof"}]},
    {"url": "/c", "images": [{"src": "https://s/img/theme-bg.png",
                              "alt": "", "new_alt": "Nowhere"}]},
]
alt_out = wordpress.publish_alt(CLIENT, None, actor="Tester")
ok_rows = [r for r in alt_out["results"] if r["ok"]]
check("the attachment's alt was written", site.media[7]["alt_text"] == "A tiled roof")
check("one row for the image, not one per page", len(ok_rows) == 1, alt_out)
check("and it says the change reaches every page it is on",
      "2 pages" in (ok_rows[0].get("note") or ""), ok_rows[0])
check("the template image is reported rather than counted as a failure to fix",
      any("media library" in (r.get("error") or "") for r in alt_out["results"]))
check("the reminder says alt is a property of the media item",
      "media library item" in alt_out["reminder"])


# ------------------------------------------------------------------ routes
print("\nroutes")
os.environ["HUB_SKIP_SCHEDULER"] = "1"
import hub                                                    # noqa: E402

app = hub.create_hub_app()
app.config["TESTING"] = True
from hub import auth as _auth                                  # noqa: E402

with app.test_client() as c:
    # current_user() reads the signed cookie, not the Flask session — the
    # middleware in wsgi.py has to answer with no application context, which
    # is why it is a cookie in the first place.
    c.set_cookie(_auth.COOKIE_NAME, _auth.issue_cookie_value("Tester"))
    r = c.get("/api/seo/wordpress?name=" + CLIENT)
    body = r.get_data(as_text=True)
    check("the state route answers", r.status_code == 200, r.status_code)
    check("and carries no secret",
          "abcdEFGH" not in body and SECRET not in body, body[:200])
    # Schema moved onto the plugin path and is asserted in
    # test_wordpress_schema.py. What stays here is the FAQ refusal, because it
    # is the one that is NOT about what REST can reach: the accordion carries
    # its own FAQPage markup, so there is nothing separate to send.
    r = c.post("/api/seo/wordpress/publish", json={"client": CLIENT, "kind": "faqs"})
    msg = r.get_json().get("error", "")
    check("faqs are refused by name, not as an unknown kind",
          "FAQPage" in msg, msg)
    check("and the refusal is the real reason rather than 'REST cannot'",
          "two copies" in msg and "cannot see" in msg, msg)
    check("faqs point at the path that does work", "Claude" in msg, msg)
    r = c.post("/api/seo/wordpress/publish", json={"client": CLIENT, "kind": "blogs"})
    check("publishing nothing is refused rather than reporting a clean run",
          r.status_code == 400, r.status_code)


# ---------------------------------------------------------------- the page
print("\nthe page")
html = open(os.path.join(ROOT, "hub", "templates", "seo_client.html")).read()
check("the send-to-WordPress button exists for blogs",
      'data-wppub="blogs"' in html)
check("and for alt text", 'data-wppub="alt"' in html)
check("both start hidden, so a button cannot be pressed before a connection "
      "check has passed",
      html.count('data-wppub="blogs" id="wpPubBlogs" style="display:none"') == 1
      and html.count('data-wppub="alt" id="wpPubAlt" style="display:none"') == 1)
check("the Claude path is still offered beside it",
      'data-pub="wordpress" data-kind="blogs"' in html)
check("the connection panel says an application password, not the site login",
      "Application Passwords" in html)
check("and says the client can revoke it themselves", "revoke" in html)
check("nothing on the page asks the server for the stored password",
      "app_password:" in html and "d.app_password" not in html)


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
