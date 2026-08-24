"""hub/blog_spec.py, hub/cms_publish.py and the SEO blog section — test harness.

    python3 test_blog_publish.py

Same shape as test_social_plan.py and test_proposal_spec.py: no pytest, no new
dependencies, a throwaway SQLite database and a temporary data directory, so it
never touches /var/data or the real one. No OpenAI key, so the planner and the
writer run their fallback paths — which is the point: everything asserted here
has to hold whether or not the model answered.

## What is worth asserting

  * **The never-mention check is a check.** A client's "do not say this" list
    is usually there for a legal reason. The prompt carries it and the code
    verifies it, and this file asserts the *verification* — a post whose copy
    says a forbidden thing must carry a flag through the store, the client
    document and the publish panel. It also asserts the inverse: a term that
    appears only in a class name is not a flag, or the check cries wolf until
    people stop reading it.

  * **Approved topics are reproduced, not paraphrased.** A client who signed
    off a topic list in advance must find those titles, in that order, in the
    plan. "Tell the model to use them" is not that.

  * **The taxonomy is clamped.** Categories are a site's structure. Left to a
    model, twelve posts arrive under twelve categories.

  * **No URL is invented.** With no site on the client there is no WordPress
    admin to open, and the publish panel has to say so rather than sending a
    rep to a stranger's login page.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-blogpub-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "blog-publish-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.pop("OPENAI_API_KEY", None)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


from hub import blog_spec, cms_publish, seo          # noqa: E402

CLIENT = "Blog Test HVAC"


# --------------------------------------------------------------- taxonomy
print("\ntaxonomy")
tax = blog_spec.clamp_taxonomy(
    ["heating", "Cooling", "Ductwork", "Indoor Air"], ["#Furnace", "furnace", "Fall"],
    ["Heating", "Maintenance"])
check("keeps an existing category, matched case-insensitively",
      tax["categories"][0] == "Heating", tax)
check("allows at most one brand-new category per post",
      len(tax["new_categories"]) <= blog_spec.MAX_NEW_CATEGORIES_PER_POST, tax)
check("caps the categories on a post",
      len(tax["categories"]) <= blog_spec.MAX_CATEGORIES, tax)
check("says what it dropped rather than dropping it silently",
      "Ductwork" in tax["dropped"], tax)
check("tags are lower case, hash-free and deduped",
      tax["tags"] == ["furnace", "fall"], tax["tags"])
check("a post with no usable category still gets one",
      blog_spec.clamp_taxonomy([], [], ["Heating"])["categories"] == ["Heating"])
check("HVAC is not title-cased into Hvac",
      blog_spec.normalise_category("HVAC repair") == "HVAC Repair",
      blog_spec.normalise_category("HVAC repair"))
check("a slug stops on a word boundary",
      not blog_spec.slugify_title("x " + "word " * 40).endswith("wo"),
      blog_spec.slugify_title("x " + "word " * 40))

many = blog_spec.clamp_taxonomy([], ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"], [])
check("tags are capped at MAX_TAGS", len(many["tags"]) == blog_spec.MAX_TAGS, many)


# -------------------------------------------------------- approved topics
print("\napproved topics")
DOC = """APPROVED BLOG TOPICS
Smart 1 Marketing
1. Furnace tune-up checklist for fall
Cover the filter, the thermostat and the flue, and mention the maintenance plan
2) Heat pump vs furnace: what Ohio winters actually need
- Why your AC freezes in July
3. Furnace tune-up checklist for fall
Page 2
"""
topics = blog_spec.parse_approved_topics(DOC)
check("the document heading is not read as a topic",
      all("APPROVED" not in t["title"].upper() for t in topics), topics)
check("numbered and bulleted lines both parse",
      [t["title"] for t in topics][:3] ==
      ["Furnace tune-up checklist for fall",
       "Heat pump vs furnace: what Ohio winters actually need",
       "Why your AC freezes in July"], topics)
check("an unmarked line becomes notes on the topic above it",
      topics[0]["notes"].startswith("Cover the filter"), topics[0])
check("a repeated topic is listed once", len(topics) == 3, topics)
check("page furniture is dropped",
      all("Page 2" != t["title"] for t in topics), topics)


# ------------------------------------------------------ never-mention check
print("\nthe never-mention check")
copy_html = ("<div class='guarantee-band'><h1>Fall furnace care</h1>"
             "<p>We beat Acme Plumbing on every call and offer a "
             "lifetime warranty on parts.</p></div>")
hits = blog_spec.scan_forbidden(copy_html, ["Acme Plumbing", "lifetime warranty", "boiler"])
terms = sorted(h["term"] for h in hits)
check("a forbidden phrase in the copy is caught",
      terms == ["Acme Plumbing", "lifetime warranty"], terms)
check("the flag carries the sentence around it",
      "Acme Plumbing" in hits[0]["context"], hits[0])
check("a term that appears only in a class name is NOT a flag",
      not blog_spec.scan_forbidden(copy_html, ["guarantee"]),
      blog_spec.scan_forbidden(copy_html, ["guarantee"]))
check("a whole-word term does not fire inside another word",
      not blog_spec.scan_forbidden("<p>We serve Canada.</p>", ["ada"]))


# ------------------------------------------------------------ the settings
print("\nblog settings survive a save")
seo.save_blog_settings(CLIENT, {
    "author": {"name": "Dana Reyes", "title": "Service Manager"},
    "guidance": "Family owned since 1994. Licensed in Ohio only — never imply "
                "we work in Kentucky. Financing is through a third party.",
    "avoid": "Acme Plumbing\nlifetime warranty",
    "categories": ["Heating", "Cooling"],
    "approved_only": True,
})
s = seo.blog_settings(CLIENT)
check("the default author is stored", s["author"]["name"] == "Dana Reyes", s["author"])
check("the guidance text is stored", "Licensed in Ohio only" in s["guidance"])
check("the never-mention list is a list", s["avoid"] == ["Acme Plumbing", "lifetime warranty"], s["avoid"])
check("approved-only is stored", s["approved_only"] is True)

seo.save_blog_settings(CLIENT, {"guidance": ""})
check("a blank guidance box clears it rather than being ignored",
      seo.blog_settings(CLIENT)["guidance"] == "")
check("a key that was not sent is left alone",
      seo.blog_settings(CLIENT)["author"]["name"] == "Dana Reyes")
seo.save_blog_settings(CLIENT, {"guidance": "Licensed in Ohio only."})

loaded = seo.set_approved_topics(CLIENT, DOC, "topics.docx")
check("the upload returns the parsed list, not just a count",
      len(loaded["topics"]) == 3 and loaded["found"] == 3, loaded)
check("the source document is recorded",
      loaded["source"]["filename"] == "topics.docx", loaded["source"])

check("the guidance payload reaches the AI context in one block",
      set(blog_spec.guidance_payload(seo.blog_settings(CLIENT))) >=
      {"company_guidance", "never_mention", "existing_categories", "taxonomy_rules"})


# ------------------------------------------------------------- the planner
print("\nthe planner, with no AI key")
plan = seo.blog_plan(CLIENT, "fall tune-ups", months=3)
posts = plan["posts"]
check("approved-only stops the schedule at the approved list",
      len(posts) == 3, len(posts))
check("the approved titles are reproduced exactly, in order",
      [p["title"] for p in posts] == [t["title"] for t in loaded["topics"]],
      [p["title"] for p in posts])
check("each approved post is labelled as approved",
      all(p["source"] == "approved" for p in posts), posts[0])
check("the plan reports how much of the approved list it used",
      plan["approved_used"] == 3 and plan["approved_available"] == 3, plan.get("approved_used"))
check("every post carries a slug", all(p["slug"] for p in posts), posts[0])
check("every post is inside the category cap",
      all(len(p["categories"]) <= blog_spec.MAX_CATEGORIES for p in posts))

seo.save_blog_settings(CLIENT, {"approved_only": False})
plan2 = seo.blog_plan(CLIENT, "", months=1)
check("without approved-only the schedule runs to its dates",
      len(plan2["posts"]) > 3, len(plan2["posts"]))
check("the approved topics still come first",
      [p["title"] for p in plan2["posts"][:3]] == [t["title"] for t in loaded["topics"]],
      [p["title"] for p in plan2["posts"][:3]])


# -------------------------------------------------------------- the writer
print("\nthe writer flags what it should not have said")
seo.blog_write(CLIENT, [p["id"] for p in plan2["posts"][:2]], limit=2)
store = seo.load_store(CLIENT)
by_id = {p["id"]: p for p in store["blogs"]["posts"]}
first = by_id[1]
check("the fallback body is written when there is no AI key",
      bool(first.get("content")) and first["status"] == "written")
check("a clean post carries no flags", first.get("flags") == [], first.get("flags"))

first["content"] = ("<h1>Fall care</h1><p>Unlike Acme Plumbing we offer a "
                    "lifetime warranty.</p>")
seo.save_store(CLIENT, store)
seo.blog_write(CLIENT, [1], limit=1)     # already written — must not rewrite it
check("a written post is not rewritten by a second write call",
      "Acme Plumbing" in seo.load_store(CLIENT)["blogs"]["posts"][0]["content"])

# The route re-runs the check on every content edit; do the same here.
edited = seo.load_store(CLIENT)
post1 = edited["blogs"]["posts"][0]
post1["flags"] = blog_spec.scan_forbidden(post1["content"], seo.blog_settings(CLIENT)["avoid"])
seo.save_store(CLIENT, edited)
check("editing copy back into a forbidden phrase re-flags the post",
      len(seo.load_store(CLIENT)["blogs"]["posts"][0]["flags"]) == 2,
      seo.load_store(CLIENT)["blogs"]["posts"][0]["flags"])

doc = seo.blogs_doc(CLIENT, [1])
check("the client document names the author", "Dana Reyes" in doc)
check("the client document shows the categories", "category:" in doc)
check("the client document warns about a flagged post, in words",
      "Check before publishing" in doc and "Acme Plumbing" in doc)
check("the document marks a post that came off the approved list",
      "from the approved topic list" in doc)


# ------------------------------------------------------- publish instructions
print("\npublish instructions")
settings = seo.blog_settings(CLIENT)
flagged_post = seo.load_store(CLIENT)["blogs"]["posts"][0]

wp = cms_publish.blog_instructions("wordpress", [flagged_post], settings,
                                   "https://www.example-hvac.com/services")
check("the WordPress admin is derived from the client's own site",
      wp["admin_url"] == "https://example-hvac.com/wp-admin/post-new.php", wp["admin_url"])
labels = [f["label"] for f in wp["items"][0]["fields"]]
check("every field a WordPress post needs is handed over",
      all(x in labels for x in ["Title", "URL slug", "Meta description",
                                "Categories", "Tags", "Author", "Publish date",
                                "Post body (HTML)"]), labels)
author_field = [f for f in wp["items"][0]["fields"] if f["label"] == "Author"][0]
check("the default author travels to the CMS panel",
      author_field["value"] == "Dana Reyes", author_field)
check("a flagged post is called out in the publish panel",
      any("never-mention" in n.lower() for n in wp["items"][0]["notes"]),
      wp["items"][0]["notes"])
check("the CMS quirk that costs a post reaches the agent, in the prompt",
      "Code editor" in wp["prompt"], wp["prompt"][:200])
check("the prompt carries the finished body, not a reference to it",
      "Fall care" in wp["prompt"] or "<h1>" in wp["prompt"], wp["prompt"][-400:])
check("the prompt tells the agent the human is already signed in",
      "already signed in" in wp["prompt"])
check("and never carries a credential",
      "password" not in wp["prompt"].lower().replace("ask me for a password", ""),
      [ln for ln in wp["prompt"].splitlines() if "password" in ln.lower()])
check("the prompt forbids paraphrasing approved copy",
      "EXACTLY" in wp["prompt"] and "improve" in wp["prompt"])
check("and forbids publishing without a human looking",
      "Do not publish" in wp["prompt"])
check("the steps are the Chrome recipe, not a retyping checklist",
      any("Claude extension" in s["title"] or "Claude extension" in s["detail"]
          for s in wp["steps"]), [s["title"] for s in wp["steps"]])

nourl = cms_publish.blog_instructions("wordpress", [flagged_post], settings, "")
check("with no site URL there is no invented WordPress admin",
      nourl["admin_url"] == "", nourl["admin_url"])
check("and it says why",
      any("no website url is saved" in w.lower() for w in nourl["warnings"]),
      nourl["warnings"])

s1 = cms_publish.blog_instructions("smart1", [flagged_post], settings,
                                   "https://example-hvac.com")
check("Smart 1 Sites opens through the Hub, never a guessed Simvoly URL",
      s1["admin_url"].startswith("/sites"), s1["admin_url"])
s1_labels = [f["label"] for f in s1["items"][0]["fields"]]
check("a builder with no tag field says so instead of dropping the tags",
      any("no tag field" in x for x in s1_labels), s1_labels)

empty = cms_publish.blog_instructions("wordpress", [], settings, "https://example-hvac.com")
check("selecting nothing is a warning, not an empty panel",
      any("Nothing selected" in w for w in empty["warnings"]), empty["warnings"])
check("an unknown CMS is refused",
      "error" in cms_publish.blog_instructions("squarespace", [], settings, ""))

sch = cms_publish.schema_instructions(
    "wordpress",
    [{"url": "https://example-hvac.com/ac-repair", "approved": False,
      "types": ["LocalBusiness"], "schema": {"@type": "LocalBusiness"}}],
    "https://example-hvac.com")
check("the schema panel hands over a ready script block",
      '<script type="application/ld+json">' in sch["items"][0]["fields"][1]["value"])
check("an unapproved page is called out before it goes on a site",
      any("not approved" in n.lower() for n in sch["items"][0]["notes"]),
      sch["items"][0]["notes"])
check("the schema prompt warns the agent off the theme file editor",
      "Theme File Editor" in sch["prompt"])
check("the schema prompt carries the JSON-LD itself",
      "application/ld+json" in sch["prompt"])
check("schema opens the dashboard, not the new-post screen",
      sch["admin_url"].endswith("/wp-admin/"), sch["admin_url"])


# ----------------------------------------------------------------- the routes
print("\nthe HTTP routes")
os.environ["PANEL_PASSWORD"] = "test"
from hub import create_hub_app                        # noqa: E402
app = create_hub_app()
c = app.test_client()
c.post("/login", data={"password": "test", "name": "Tester"})

r = c.post("/api/seo/blogs/settings",
           json={"client": CLIENT, "author": {"name": "Dana Reyes"}})
check("the settings route answers", r.status_code == 200 and r.get_json().get("ok"),
      r.status_code)
r = c.post("/api/seo/blogs/settings", json={})
check("the settings route needs a client", r.status_code == 400)

r = c.post("/api/seo/blogs/topics",
           json={"client": CLIENT, "text": "- One approved topic here\n- And another one\n"})
check("topics can be pasted as well as uploaded",
      r.status_code == 200 and r.get_json()["found"] == 2, r.get_json())
r = c.post("/api/seo/blogs/topics", json={"client": CLIENT})
check("an empty topic post is refused with a reason",
      r.status_code == 400 and "error" in r.get_json())

r = c.post("/api/seo/publish/instructions",
           json={"client": CLIENT, "cms": "wordpress", "kind": "blogs", "ids": [1]})
body = r.get_json()
check("the publish route returns a panel", r.status_code == 200 and body["items"], body)
r = c.post("/api/seo/publish/instructions",
           json={"client": CLIENT, "cms": "wix", "kind": "blogs", "ids": [1]})
check("the publish route refuses a CMS it has no instructions for",
      r.status_code == 400, r.status_code)

r = c.post("/api/seo/blogs/update",
           json={"client": CLIENT, "id": 1, "categories": ["Heating", "Cooling", "Extra"],
                 "tags": ["Furnace", "furnace", "fall"]})
check("the update route clamps what it is sent", r.status_code == 200)
saved = seo.load_store(CLIENT)["blogs"]["posts"][0]
check("the clamp holds through the route",
      len(saved["categories"]) <= blog_spec.MAX_CATEGORIES and saved["tags"] == ["furnace", "fall"],
      saved)

r = c.get("/seo/client?name=" + CLIENT.replace(" ", "%20"))
check("the SEO client page still renders", r.status_code == 200, r.status_code)


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
