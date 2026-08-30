"""The SEO client list and the client record: what the four pills claim.

    python3 test_seo_page.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

The SEO section is one of the largest in the Hub — ~40 API routes, 1,800 lines
of `hub/seo.py`, and a 3,600-line client record — and until this file nothing
asked it a question. `tools/pagecheck.py` did not reach either of its two
detail screens: `/seo/client` **redirects to /seo** without a `?name=`, so a
sweep of bare paths lands on the list, reports it green, and the record page
goes unchecked while reading as covered. It is named in `HUB_PAGES` now, the
way `/prospect/none` already is, and this file holds the rest.

Every failure below is one where a screen would go on looking healthy:

  1. **A pill with four answers must not be a bool.** `client_status()`
     returned `blogs` as one, and `False` covered both *this client does not
     buy blogs* and *their plan is behind*. On this deployment's own book that
     drew a permanent red "Blogs — not yet" on **16 of 21** SEO clients, for a
     product they have never bought, in the one column that says what to act
     on — while the summary tile at the top of the same page counts the
     product and read "With blogs: 5". Five clients have blogs; twenty-one are
     behind on them. Neither figure is wrong on its own, which is why it stood.

  2. **`not_sold` is never reached on a guess.** It is the state that takes a
     row out of the queue, so a caller that could not look must not be able to
     silence one: unknown owes a plan, like anybody else who has none.

  3. **One reader, two screens.** The list and the record both draw from
     `client_status()`, so they cannot come to disagree about who is behind —
     and `/api/seo/checks` echoes the *same* answer after a tick, or ticking
     *Setup* would move a no-blogs client's pill from gray to amber and read
     as the tick having done something it did not do.

  4. **A name nobody gave matches nobody.** `_client_websites("")` tested
     `ck in wk`, which is true of every string when `ck` is empty — so a
     nameless client was handed the **whole 610-row registry**, and `webs[0]`
     then supplied its "website", its GA id and the domain its Brandfetch is
     looked up under. The `client_key.resolve()` rule, wearing a website.

  5. **A failed record load is not an empty record.** `/api/seo/detail`
     answered **200** with an `error` key, and `fetch()` resolves for 4xx and
     5xx alike — so the page's `.catch()` never saw it and every card rendered
     its own empty state. A client with months of work behind them, drawn as a
     client with none.

  6. **SEO work has to reach the client's record.** Every route in the section
     logged under module `"hub"`, which `client_brand.NOT_WORK` calls
     housekeeping — so `work_log()` dropped all of it and a client who had
     just had schema, blogs and FAQs built read as a client nobody had done
     any work for. The `display_ads` failure, one section later, and
     `check_work_kinds()` cannot see it: it only flags a module in *neither*
     table, and `hub` is in one.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1seo_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "seo-test-secret"

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import audit, auth, client_brand, create_hub_app, seo   # noqa: E402

app = create_hub_app()
signed_in = app.test_client()
signed_in.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test Rep"))

TODAY = seo._dt_date_today_iso()
YESTERDAY = "2000-01-01"
TOMORROW = "2999-01-01"


def store(posts=None):
    return {"blogs": {"posts": list(posts or [])}} if posts is not None else {}


# ------------------------------------------------------------------------
section("1. The Blogs pill has four answers, not two")

check("a client who buys no blogs is 'not_sold', not 'behind'",
      seo._blogs_state({}, sells=False), "not_sold")
check("a client who buys blogs and has no plan owes one",
      seo._blogs_state({}, sells=True), "none")
check("a plan whose due posts are all posted is current",
      seo._blogs_state(store([{"date": YESTERDAY, "posted": True}]), True),
      "current")
check("a due post nobody has posted is behind",
      seo._blogs_state(store([{"date": YESTERDAY, "posted": False}]), True),
      "behind")
check("a plan with nothing due yet is current, not behind",
      seo._blogs_state(store([{"date": TOMORROW, "posted": False}]), True),
      "current")
check("every state has a label the page can print",
      sorted(seo.BLOGS_STATES) == ["behind", "current", "none", "not_sold"])

# The one that must never be reached by inference.
section("2. 'not_sold' is the state that silences a row, so never guess it")

check("a caller that could not look does NOT silence the row",
      seo._blogs_state({}, sells=None), "none")
check("and only an explicit False silences it",
      seo._blogs_state({}, sells=False), "not_sold")
check("a client behind on blogs is never silenced by an unknown product read",
      seo._blogs_state(store([{"date": YESTERDAY, "posted": False}]), None),
      "behind")

# ------------------------------------------------------------------------
section("3. One reader, so the two screens cannot disagree")

st_sold = seo.client_status({}, sells_blogs=True)
st_not = seo.client_status({}, sells_blogs=False)
check("client_status carries the state, not a bool",
      isinstance(st_sold["blogs"], str))
check("and the label beside it, so no screen invents its own wording",
      st_not["blogs_label"], seo.BLOGS_STATES["not_sold"])
check("the other three pills stay genuinely yes/no",
      [isinstance(st_sold[k], bool) for k in ("setup", "schema", "listings")],
      [True, True, True])
check("a ticked check reads back true",
      seo.client_status({"checks": {"schema": True}})["schema"], True)

LIST = (ROOT / "hub" / "templates" / "seo.html").read_text(encoding="utf-8")
REC = (ROOT / "hub" / "templates" / "seo_client.html").read_text(encoding="utf-8")

check("the list draws Blogs from the state, not from truthiness",
      "st.blogs_label" in LIST and "not_sold" in LIST)
check("and gives 'no blogs product' its own muted class, never `bad`",
      ".seo-pill.off" in LIST and "not_sold:'off'" in LIST)
check("the record draws the same state rather than deciding for itself",
      "st.blogs_label" in REC and ".seoc-dot.not_sold" in REC)
check("the record no longer tests the old bool", "st.blogs?" not in REC)
check("and the list draws Blogs outside the three yes/no pills",
      "['blogs','Blogs']" not in LIST)

# ------------------------------------------------------------------------
section("4. A name nobody gave matches nobody")

check("an empty client name owns no websites", seo._client_websites(""), [])
check("and neither does whitespace", seo._client_websites("   "), [])
detail = seo.client_detail("")
check("so a nameless client gets no website registry",
      len(detail["websites"]), 0)
check("and no site url lifted off somebody else's record", detail["url"], "")

r = signed_in.get("/api/seo/detail?name=")
check("the route refuses a nameless client rather than answering for one",
      r.status_code, 400)
check("and says which field", (r.get_json() or {}).get("error"),
      "client is required.")

# ------------------------------------------------------------------------
section("5. A failed record load is not an empty record")

# .find() rather than .index() throughout: an assertion that raises on the
# missing thing takes every check after it out of the run, which is how a
# failing file comes to report fewer failures than it found.
_guard, _assign = REC.find("if(d&&d.error){"), REC.find("detail=d; slug=d.slug")
check("the page reads `error` off the detail body", _guard != -1)
check("before it assigns any of the record",
      _guard != -1 and _assign != -1 and _guard < _assign)

_real_detail = seo.client_detail
seo.client_detail = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("knack gone"))
try:
    r = signed_in.get("/api/seo/detail?name=Acme%20Plumbing")
    body = r.get_json() or {}
    check("a detail that could not be built is not a 200", r.status_code, 502)
    check("and names the failure rather than answering with an empty record",
          "knack gone" in str(body.get("error")))
    check("nothing invents a website for it", "websites" not in body)
finally:
    seo.client_detail = _real_detail

# ------------------------------------------------------------------------
section("6. SEO work reaches the client's own record")

CLIENT = "Seo Test Client"
audit.log("seo", "seo_blog_write", actor="Test Rep", client=CLIENT,
          detail="3 posts")
audit.log("seo", "faq_page_saved", actor="Test Rep", client=CLIENT, url="/x")
work = client_brand.work_log(CLIENT)
actions = {i["action"] for i in work["items"]}
check("a blog write shows on the record", "seo_blog_write" in actions)
check("so does a saved FAQ page", "faq_page_saved" in actions)
check("filed under a source the record can name",
      {i["source"] for i in work["items"]}, {"SEO"})

check("`seo` is a module the work log knows",
      "seo" in client_brand.WORK_KINDS)
check("and `hub` is still housekeeping, so it could never have carried this",
      "hub" in client_brand.NOT_WORK)

# The regression this section exists for: no SEO route may go back to
# logging client work under the housekeeping bucket.
import ast                                                     # noqa: E402

hub_src = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
tasks_src = (ROOT / "hub" / "seo_tasks.py").read_text(encoding="utf-8")
strays = []
for name, src in (("hub/__init__.py", hub_src), ("hub/seo_tasks.py", tasks_src)):
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "log"
                and getattr(node.func.value, "id", "") == "audit"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        module = node.args[0].value
        event = (node.args[1].value
                 if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                 else "")
        if str(event).startswith(("seo", "faq")) and module not in client_brand.WORK_KINDS:
            strays.append(f"{name}:{node.lineno} logs {event!r} under {module!r}")
check("no SEO event is logged under a module the record cannot name",
      strays, [])

# ------------------------------------------------------------------------
section("7. The two pages pagecheck could not reach")

PC = (ROOT / "tools" / "pagecheck.py").read_text(encoding="utf-8")
check("the client record is named in pagecheck, with a ?name=",
      "/seo/client?name=" in PC)
check("and the webmaster dashboard beside it", '"/seo/webmaster"' in PC)

r = signed_in.get("/seo/client?name=" + CLIENT.replace(" ", "%20"))
check("the record renders for a client with nothing on file", r.status_code, 200)
check("without a redirect to the list", b"seoc-check" in r.data)
r = signed_in.get("/seo/client")
check("and no ?name= still goes back to the list rather than 500ing",
      r.status_code, 302)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
