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

  6. **An editor rebuilt underneath somebody keeps what was typed.** The
     alt-text list and the FAQ draft are both redrawn with `innerHTML` by a
     *sibling row's* button and by a fetch landing tens of seconds later. The
     FAQ inputs carried no change handler at all, so half-writing an answer on
     one question and pressing Approve on another discarded it in silence.
     Only a field somebody typed in is read back — harvesting on a difference
     against the model would revert a page of AI-written alt text to what the
     boxes held before the write.

  7. **SEO work has to reach the client's record.** Every route in the section
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
section("7. An editor rebuilt underneath somebody keeps what was typed")

# Both editors are a container of live inputs redrawn with innerHTML -- by a
# *sibling row's* button, and by a fetch that lands tens of seconds later.
# The logic that reads them back is lifted out and driven in node against a
# stub DOM, the arrangement test_menu_layout.py uses on hub-crumbs.js: a copy
# restated here would be a third thing to keep in step.
import json                                                    # noqa: E402
import subprocess                                              # noqa: E402


def lift(marker):
    a = REC.find("/* ---- %s (lifted" % marker)
    b = REC.find("/* ---- end %s ----" % marker)
    return (REC[a:b] if a > 0 and b > a else "")


FAQ_SRC, ALT_SRC = lift("faq harvest"), lift("alt harvest")
check("the faq harvest block is still marked for lifting", bool(FAQ_SRC))
check("and the alt harvest block too", bool(ALT_SRC))

# A stub of only what the two blocks touch: querySelectorAll over a flat list
# of fields, `dataset`, `value`, `tagName`. Nothing here models a browser --
# it models the four things the harvest reads.
STUB = """
function El(tag, data, value){
  this.tagName=tag; this.dataset=data||{}; this.value=value;
  this.className=(tag==='INPUT'?'qedit':'altNew'); this.isConnected=true;
}
function Box(fields){ this.fields=fields; }
Box.prototype.querySelectorAll=function(sel){
  var wantInput=sel.indexOf('input')>=0, wantArea=sel.indexOf('textarea')>=0;
  var wantAlt=sel.indexOf('.altNew')>=0;
  return this.fields.filter(function(f){
    if(wantAlt) return f.className==='altNew';
    if(f.tagName==='INPUT') return wantInput;
    return wantArea;
  });
};
var POSTED=[];
function post(u,b){ POSTED.push(b); return Promise.resolve({image:{new_alt:b.alt,decorative:false}}); }
function alert(){}
var CLIENT='Acme';
var BOX=null;
function $(id){ return BOX; }
"""

_driver = STUB + FAQ_SRC + ALT_SRC + """
var out={};

// --- FAQ: half-write row 0's answer, then press Approve on row 2.
var draft={items:[{question:'Q1',answer:'A1'},{question:'Q2',answer:'A2'},
                  {question:'Q3',answer:'A3'}]};
BOX=new Box([new El('TEXTAREA',{i:'0',dirty:'1'},'half written answer')]);
faqHarvest(-1);
out.approve_kept=draft.items[0].answer;

// --- FAQ: Cancel on the row being edited must discard it, not keep it.
draft={items:[{question:'Q1',answer:'A1'}]};
BOX=new Box([new El('TEXTAREA',{i:'0',dirty:'1'},'typed then cancelled')]);
faqHarvest(0);
out.cancel_discarded=draft.items[0].answer;

// --- FAQ: a field nobody typed in is never harvested, so a fetch that
// replaces the model is not reverted by stale markup.
draft={items:[{question:'Q1',answer:'FRESH FROM THE MODEL'}]};
BOX=new Box([new El('TEXTAREA',{i:'0'},'stale markup')]);
faqHarvest(-1);
out.clean_not_reverted=draft.items[0].answer;

// --- FAQ: clearing a box does not delete the question.
draft={items:[{question:'Q1',answer:'A1'}]};
BOX=new Box([new El('INPUT',{i:'0',dirty:'1'},'   ')]);
faqHarvest(-1);
out.empty_keeps=draft.items[0].question;

// --- ALT: typing, then the AI write lands and rebuilds the list.
altPages=[{url:'/p',images:[{src:'a.jpg',new_alt:''}]}];
BOX=new Box([new El('TEXTAREA',{url:'/p',src:'a.jpg',dirty:'1'},'a red van')]);
altHarvest();
out.alt_kept=altPages[0].images[0].new_alt;
out.alt_posted=POSTED.length;

// --- ALT: an untouched box is not written back, so a page of AI-written alt
// text is not reverted to what the boxes held before the write.
POSTED=[];
altPages=[{url:'/p',images:[{src:'a.jpg',new_alt:'AI WROTE THIS'}]}];
BOX=new Box([new El('TEXTAREA',{url:'/p',src:'a.jpg'},'')]);
altHarvest();
out.alt_clean_kept=altPages[0].images[0].new_alt;
out.alt_clean_posted=POSTED.length;

console.log(JSON.stringify(out));
"""

_r = subprocess.run(["node", "-e", _driver], capture_output=True, text=True)
check("the lifted blocks run on their own", _r.returncode, 0)
if _r.returncode:
    print("   " + (_r.stderr or "").strip()[:500])
    OUT = {}
else:
    OUT = json.loads(_r.stdout)

check("a half-written FAQ answer survives Approve on another row",
      OUT.get("approve_kept"), "half written answer")
check("but Cancel still discards the row it is cancelling",
      OUT.get("cancel_discarded"), "A1")
check("a box nobody typed in never overwrites a freshly fetched answer",
      OUT.get("clean_not_reverted"), "FRESH FROM THE MODEL")
check("and clearing a box does not delete the question",
      OUT.get("empty_keeps"), "Q1")
check("alt text typed during the AI write survives the rebuild",
      OUT.get("alt_kept"), "a red van")
check("and reaches the server rather than only the model",
      OUT.get("alt_posted"), 1)
check("an untouched alt box never reverts what the AI just wrote",
      OUT.get("alt_clean_kept"), "AI WROTE THIS")
check("and writes nothing", OUT.get("alt_clean_posted"), 0)

# The wiring the blocks above depend on.
check("the alt editor marks its fields dirty on input", "dirty(t);" in REC)
check("and the FAQ editor marks its own", "{ dirty(el); }" in REC)
check("the redraw goes through the harvest, not straight to innerHTML",
      "faqHarvest(typeof skip==='number'" in REC and "altHarvest();" in REC)
check("Cancel passes its row so the harvest skips it",
      "renderDraft(i); return; }" in REC)
check("and the caret is put back after a rebuild",
      REC.count("keepCaret(") >= 2)


# ------------------------------------------------------------------------
section("8. The SEO book is read live, and says which source answered")

from hub import knack_data, knack_products                     # noqa: E402

LIVE_ROW = {"client": "Zeta Live Only", "product": "Website SEO and Blogs",
            "status": "Live", "start": "01/01/2026", "end": "12/31/2026",
            "monthly": "900", "partner": "Smart 1 Marketing", "sales": "Todd"}
_real_rows = knack_products.rows


def with_live(payload):
    knack_products.rows = lambda *a, **k: payload
    try:
        return seo.seo_clients_result()
    finally:
        knack_products.rows = _real_rows


rows, source, age = with_live({"source": "knack", "rows": [LIVE_ROW],
                               "age_minutes": 4})
check("a live pull is what the SEO list is built from", source, "knack")
check("and the client on it is the live one",
      [r["client"] for r in rows], ["Zeta Live Only"])
check("its age travels with the rows", age, 4)
check("the note says live", seo.products_note(source, age),
      "Live from Knack, 4 min old.")

# The two ways a live pull must never empty a good export -- the knack_products
# rule. A client list that came back empty would read as "we have no SEO
# clients", which is a confident wrong answer rather than an error.
_, s_empty, _ = with_live({"source": "knack", "rows": [], "age_minutes": 1})
check("a live pull that answers with nothing falls back", s_empty, "export")


def _boom(*a, **k):
    raise RuntimeError("knack down")


knack_products.rows = _boom
try:
    r_err, s_err, _ = seo.seo_clients_result()
finally:
    knack_products.rows = _real_rows
check("and one that raises falls back too", s_err, "export")
check("with the export's rows still on it", len(r_err) > 0)
check("the export note says it may be out of date",
      "may be out of date" in seo.products_note("export", None))

check("seo_clients() keeps its list-only signature for the other callers",
      isinstance(seo.seo_clients(), list))
check("and the webmaster roster still builds off it",
      isinstance(seo.webmaster_roster(), list))

# The record reads the same source as the list, or the two screens quote one
# client different products with nothing saying why.
knack_products.rows = lambda *a, **k: {"source": "knack", "rows": [LIVE_ROW],
                                       "age_minutes": 4}
try:
    rec = seo.client_detail("Zeta Live Only")
finally:
    knack_products.rows = _real_rows
check("the client record is built from the live pull too",
      rec["products_source"], "knack")
check("and carries the same one sentence about it",
      rec["products_note"], seo.products_note("knack", 4))
check("with the live product on it", rec["products"], ["Website SEO and Blogs"])
check("and its billing", rec["billing"], 900)

r = signed_in.get("/api/seo/clients")
body = r.get_json() or {}
check("the route hands the source to the page", "products_source" in body)
check("and the sentence to print", bool(body.get("products_note")))

check("the list draws it", "products_source" in LIST and "seo-srcnote" in LIST)
check("and says nothing at all rather than 'live' when it cannot tell",
      "not measured -- say nothing" in LIST)
check("the record draws it", "seocSrc" in REC and "products_note" in REC)
check("one sentence, written once, read by both",
      LIST.count("Committed export") + REC.count("Committed export"), 1)


# ------------------------------------------------------------------------
section("9. The two pages pagecheck could not reach")

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

# ------------------------------------------------------------------------
section("10. The derived health block, and the rail that draws it")

# The record used to open on four hand-typed checkboxes: a client could have
# fourteen pages with no schema and still read "Schema updated", because the
# tick is a claim somebody made and the stores hold the facts. record_health()
# derives the strip and the what-needs-doing queue from the stores, and these
# drive it with each state the queue has to tell apart.

STORE = {
    "sitemap": ["/a", "/b", "/c"],
    "pages": {"/a": {"approved": True}},
    "blogs": {"posts": [
        {"date": "2020-01-05", "posted": False},
        {"date": "2020-01-12", "posted": True},
        {"date": "2099-01-01", "posted": False},
    ]},
    "alt_text": {"scanned_at": "2026-08-01", "missing_alt": 5, "pages": []},
    "checks": {"schema": True},
    "setup": {"completed": True},
}
FAQS = [{"url": "/a", "added_to_site": ""}, {"url": "/b", "added_to_site": "2026-08-04"}]
h = seo.record_health("Health Test Client", STORE, sells=True, faq_pages=FAQS)

check("an overdue post is counted", h["blogs"]["overdue"], 1)
check("and a post due in the future is not", h["blogs"]["state"], "behind")
check("schema counts the sitemap pages still unbuilt", h["schema"]["remaining"], 2)
check("alt text carries the scan's own missing count", h["alt"]["missing"], 5)
check("a built-not-live FAQ set is waiting", h["faqs"]["waiting"], 1)
levels = [q["level"] for q in h["queue"]]
check("the queue carries all four findings", sorted(levels),
      sorted(["bad", "warn", "warn", "info"]))
check("worst first, always", levels, sorted(levels, key=["bad", "warn", "info"].index))
sections_named = {q["section"] for q in h["queue"]}
check("every row names the section it is fixed on",
      sections_named, {"blogs", "schema", "alt", "faqs"})

# Absent is not zero: a client with no stores has nothing measured, and the
# queue must be empty rather than full of confident noughts.
h0 = seo.record_health("Health Empty Client", {}, sells=False, faq_pages=[])
check("an unscanned site is 'not measured', never zero missing",
      h0["alt"]["measured"], False)
check("no sitemap leaves the schema total unknown", h0["schema"]["total"], None)
check("and the empty client's queue is empty", h0["queue"], [])
check("no blogs product reads as its own state, not as behind",
      h0["blogs"]["state"], "not_sold")

# A source that cannot be read is NAMED, and contributes nothing — "no images
# are missing alt" and "the alt scan could not be read" are different answers.
import hub.faq as _faqmod                                       # noqa: E402
_real_list = _faqmod.list_pages
_faqmod.list_pages = lambda c: (_ for _ in ()).throw(RuntimeError("store gone"))
try:
    hb = seo.record_health("Health Test Client", STORE, sells=True)
finally:
    _faqmod.list_pages = _real_list
check("a failed FAQ read is named", "the FAQ pages could not be read" in hb["unread"])
check("and raises nothing into the queue",
      all(q["section"] != "faqs" for q in hb["queue"]))
check("while the other sources still answer", hb["blogs"]["overdue"], 1)

# The block rides the record's one detail fetch, and a health bug must cost
# the strip, never the page.
d_full = seo.client_detail(CLIENT, full=True)
check("client_detail(full) carries the health block", "health" in d_full)
_real_health = seo.record_health
seo.record_health = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
try:
    d_broken = seo.client_detail(CLIENT, full=True)
finally:
    seo.record_health = _real_health
check("a health build that raises does not take the record down",
      d_broken.get("health", {}).get("error"),
      "The health summary could not be built.")

# The workspace template: one section on screen at a time, and the rail's
# names resolve. Re-read rather than reusing REC — this section edits nothing,
# but the assertions below are about the file as it stands now.
import re as _re                                                # noqa: E402
rail_secs = set(_re.findall(r'data-sec="([a-z]+)"', REC))
view_ids = set(_re.findall(r'id="view-([a-z]+)"', REC))
check("every rail entry has a view", rail_secs - view_ids, set())
check("and every view has a rail entry", view_ids - rail_secs, set())
check("the record asks for the icon rail the way the wizard tools do",
      'data-s1hub-collapse="1"' in REC)
BASE = (ROOT / "hub" / "templates" / "base.html").read_text(encoding="utf-8")
# The line that starts with <body, not the first "<body" in the file — the
# comment above the tag says "<body>" in prose, and prose is not a call site.
_body_line = next((l for l in BASE.splitlines() if l.lstrip().startswith("<body")), "")
check("and base.html renders that block inside its body tag",
      "body_attrs" in _body_line)
# hub-accordion.js reorders EVERY .card into the first card's parent on
# /seo* and /client360 — on a page whose cards live in per-section views
# that tears all of them into the first view. The workspace root carries the
# opt-out marker and the accordion honors it before touching anything.
ACC = (ROOT / "hub" / "static" / "hub-accordion.js").read_text(encoding="utf-8")
check("the workspace opts out of the card accordion",
      'data-s1-workspace="1"' in REC)
check("and the accordion honors the marker before it reorders anything",
      "[data-s1-workspace]" in ACC and "if (workspace()) return;" in ACC)
check("traffic no longer loads at boot",
      "safe('traffic', function(){ loadTraffic(); });" not in REC)
check("nor does the alt table", "safe('alt', function(){ loadAlt(); });" not in REC)
check("the health strip renders from the one detail payload",
      "renderHealth(d.health)" in REC)

# On the SERVED page, not just the template — every call site contains the
# markup whether or not the block was emitted.
r = signed_in.get("/seo/client?name=" + CLIENT.replace(" ", "%20"))
check("the served record carries the rail", b"seocRail" in r.data)
check("and the health strip", b"seocHealth" in r.data)
check("and the queue card", b"cardNeeds" in r.data)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
