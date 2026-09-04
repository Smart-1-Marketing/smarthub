"""The derived health strip on Client 360 -- test harness.

    python3 test_client360_health.py

The SEO record got a strip computed by hub/seo.record_health() from the
stores, never from the hand ticks. Client 360 -- nineteen cards, header
badges, a Site Health card -- had nothing that said what needs doing.
hub/record_health.client360() is that strip, its own reading because a 360
record spans products, billing, the website and its audit, the Google
accounts, open proposals and the outstanding work beside the SEO half; the
SEO half is seo.record_health() called rather than restated, and the blogs
rule is seo.blogs_health(), which both strips read.

What this file holds, worst first:

  * A client with no data renders as unknown, never as clear: every pill is
    `idle` or `unread`, none is `ok`, and the whole payload is honest about
    whether it measured.
  * A source that refuses is its own state (`unread`) with the reason, and
    the payload's `measured` goes False -- "nothing open" and "the store
    would not answer" are different answers.
  * The values are derived: live products, ending terms, the audit's age,
    what Google is recorded, open proposals by signal, outstanding issues,
    schema pages, and the blogs rule with its three parts -- overdue, a
    plan that has run out against the recorded cadence, and the fact that
    no publish date is recorded.
  * Every pill links where the work is: a rail section the template
    actually declares, or the SEO record's own section.
  * The renderer draws the three empties differently, driven in node the
    way test_client360_layout.py drives the section map.
  * The route is under /api/client/ and refuses a stranger.

test_client360_layout.py covers the rail, the card grouping and the owner
block; nothing there touches this strip.
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1c360health_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "c360-health-test"
os.environ["PANEL_PASSWORD"] = "c360-health-pass"
os.environ["REPORT_CACHE"] = "off"

_passed = _failed = 0


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


import importlib                                                 # noqa: E402

from hub import record_health as rh                              # noqa: E402
from hub import seo                                              # noqa: E402

TODAY = dt.date(2026, 9, 3)


def day(n):
    return (TODAY + dt.timedelta(days=n)).isoformat()


def mdy(n):
    return (TODAY + dt.timedelta(days=n)).strftime("%m/%d/%Y")


def pills(out):
    return {p["key"]: p for p in out["pills"]}


class Stub:
    """Swap module attributes for the duration of a block, always restored."""

    def __init__(self, **targets):
        self.targets, self.saved = targets, {}

    def __enter__(self):
        for dotted, value in self.targets.items():
            mod, attr = dotted.rsplit(".", 1)
            m = importlib.import_module("hub." + mod)
            self.saved[dotted] = getattr(m, attr)
            setattr(m, attr, value)
        return self

    def __exit__(self, *exc):
        for dotted, value in self.saved.items():
            mod, attr = dotted.rsplit(".", 1)
            setattr(sys.modules["hub." + mod], attr, value)


GROUP = {
    "client": "Acme Boats", "io_only": False, "billing_monthly": 1850,
    "products": [
        {"product": "Connected TV", "status": "Live", "end": mdy(60)},
        {"product": "Display", "status": "Live", "end": mdy(10)},
        {"product": "Old Radio", "status": "Complete", "end": mdy(-200)},
    ],
    "websites": [{"domain": "acmeboats.com", "liveUrl": "https://acmeboats.com",
                  "ga": "G-ABC123", "gtm": ""}],
}


def audit(age_days, score=71):
    from hub import website_audit
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age_days)).strftime("%Y-%m-%d %H:%M:%S")
    return {"acmeboats.com": {"score": score, "when": when,
                              "age": website_audit.staleness(when)}}


def store_with(posts, setup=None):
    return {"client": "Acme Boats", "setup": setup or {}, "pages": {"/": {}},
            "sitemap": ["/", "/services", "/about"], "blogs": {"posts": posts}}


BASE_SEO = {"client": "Acme Boats", "products": ["SEO Blogs"], "blogs": True}
ISSUES_OK = {"ok": True, "measured": True, "complete": True, "issues": [],
             "issue_count": 0, "missing_sources": [], "warning": ""}
NO_QUOTES = {"measured": True, "error": "", "clients": {}}

# ------------------------------------------------------------ 1. derived values
section("1. Every pill is derived from the stores")

with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [GROUP],
    "upsell.audits_for": lambda domains: (audit(12), ""),
    "sales_status.by_client": lambda: {"measured": True, "error": "", "clients": {
        "Acme Boats": [{"signal": "unopened"}, {"signal": "expiring"}]}},
    "client_health.issues_for_client": lambda name: dict(ISSUES_OK, issue_count=2, issues=[
        {"kind": "asset_ask", "title": "Waiting on banners"}, {"kind": "io_ending", "title": "IO ends"}]),
    "seo._client_base": lambda name: BASE_SEO,
    "seo.load_store": lambda name: store_with(
        [{"date": day(-20), "posted": True}, {"date": day(-13), "posted": True},
         {"date": day(-6), "posted": False}, {"date": day(1), "posted": False}],
        setup={"blogs_frequency": "weekly"}),
}):
    out = rh.client360("Acme Boats", today=TODAY)

p = pills(out)
check("the strip measured", out["measured"], True)
check("nothing is named unread", out["unread"], [])
check("products: the live count, with the one ending soon named",
      (p["products"]["state"], p["products"]["value"]), ("warn", "2 live · 1 ending in 10d"))
check("billing: the active monthly figure", (p["billing"]["state"], p["billing"]["value"]),
      ("ok", "$1,850/mo active"))
check("site: the audit's score and age", p["site"]["state"], "ok")
check("...with the age in the value", "12d ago" in p["site"]["value"] and "score 71" in p["site"]["value"])
check("google: GTM missing from the website record is a finding",
      (p["google"]["state"], p["google"]["value"]), ("warn", "No GTM recorded"))
check("proposals: two waiting, by signal",
      (p["proposals"]["state"], p["proposals"]["value"]), ("warn", "2 waiting: not opened, price lapsing"))
check("outstanding: the /my-clients count", (p["work"]["state"], p["work"]["value"]), ("warn", "2 issues"))
check("seo: schema against the sitemap", (p["seo"]["state"], p["seo"]["value"]),
      ("warn", "schema 1 of 3 pages"))
check("blogs: one planned post past its date and not marked posted",
      (p["blogs"]["state"], p["blogs"]["value"]), ("bad", "1 overdue"))
check("the queue is worst first", [q["level"] for q in out["queue"]][:1], ["bad"])
check("and the SEO queue rows carry the record's own section link",
      any(q.get("href", "").endswith("#blogs") for q in out["queue"]))

section("1b. Deep links")
REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
SECTION_KEYS = set(re.findall(r"\{key:'([a-z]+)'", REC[REC.find("const C360_SECTIONS"):REC.find("/* ---- end c360 sections")]))
check("the template declares rail sections", len(SECTION_KEYS) >= 6)
for key, go in rh.SECTIONS.items():
    check(f"{key} -> {go} is a rail section the record draws", go in SECTION_KEYS)
for key, pill in p.items():
    check(f"{key} links somewhere: a section or the SEO record",
          bool(pill["href"]) or pill["go"] in SECTION_KEYS)
check("the SEO pill opens the SEO record's schema section", p["seo"]["href"],
      "/seo/client?name=Acme%20Boats#schema")
check("and the blogs pill its blogs section", p["blogs"]["href"].endswith("#blogs"))

# ------------------------------------------------------------ 2. the blogs rule
section("2. The blogs rule: overdue, a plan that ran out, and no publish date")

b = seo.blogs_health(store_with(
    [{"date": day(-30), "posted": True}, {"date": day(-9), "posted": False}],
    setup={"blogs_frequency": "weekly"}), True, today=TODAY.isoformat())
check("overdue = planned date passed and not marked posted", (b["state"], b["overdue"], b["overdue_days"]),
      ("behind", 1, 9))
check("the cadence comes from the setup", (b["cadence_days"], b["cadence_source"]), (7, "setup"))
check("last posted is the planned date of the latest tick", b["last_posted"], day(-30))
check("and says no publish date is recorded", b["published_dates_recorded"], False)

b = seo.blogs_health(store_with(
    [{"date": day(-40), "posted": True}, {"date": day(-33), "posted": True}],
    setup={"blogs_per_month": 4}), True, today=TODAY.isoformat())
check("every planned post posted reads current to the old rule", b["state"], "current")
check("but the plan has run out against the recorded cadence",
      (b["plan_exhausted"], b["plan_ran_out_days"]), (True, 33))
with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [GROUP],
    "upsell.audits_for": lambda domains: (audit(12), ""),
    "sales_status.by_client": lambda: NO_QUOTES,
    "client_health.issues_for_client": lambda name: ISSUES_OK,
    "seo._client_base": lambda name: BASE_SEO,
    "seo.load_store": lambda name: store_with(
        [{"date": day(-40), "posted": True}, {"date": day(-33), "posted": True}],
        setup={"blogs_per_month": 4}),
}):
    out = rh.client360("Acme Boats", today=TODAY)
check("...and the strip draws that as a warning, not up to date",
      (pills(out)["blogs"]["state"], pills(out)["blogs"]["value"]), ("warn", "plan has run out"))
check("...with the run-out in the queue", any("run out" in q["title"] for q in out["queue"]))

b = seo.blogs_health(store_with(
    [{"date": day(-40), "posted": True}], setup={}), True)
check("no cadence in the setup is said, not defaulted to weekly",
      (b["cadence_days"], b["cadence_source"], b["plan_exhausted"]), (None, "not recorded", False))
with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [GROUP],
    "upsell.audits_for": lambda domains: (audit(12), ""),
    "sales_status.by_client": lambda: NO_QUOTES,
    "client_health.issues_for_client": lambda name: ISSUES_OK,
    "seo._client_base": lambda name: BASE_SEO,
    "seo.load_store": lambda name: store_with([{"date": day(-40), "posted": True}], setup={}),
}):
    out = rh.client360("Acme Boats", today=TODAY)
check("...and the pill's detail says the cadence is not recorded",
      "No cadence is recorded" in pills(out)["blogs"]["detail"])
check("...and that the date shown is a planned one",
      "no publish date is recorded" in pills(out)["blogs"]["detail"])

b = seo.blogs_health(store_with([], setup={"blogs_frequency": "weekly"}), False)
check("a client who does not buy blogs is not_sold and never exhausted",
      (b["state"], b["plan_exhausted"]), ("not_sold", False))
check("record_health reads the same rule",
      seo.record_health("Acme Boats", store_with(
          [{"date": day(-9), "posted": False}], setup={"blogs_frequency": "weekly"}),
          sells=True, faq_pages=[])["blogs"]["overdue"], 1)

# ---------------------------------------------------- 3. not measured is not clear
section("3. A source that refuses is named, never a clean pill")


def boom(*a, **k):
    raise RuntimeError("down")


with Stub(**{
    "knack_data.search_client": boom,
    "upsell.audits_for": lambda domains: ({}, "OperationalError: scans"),
    "sales_status.by_client": lambda: {"measured": False, "error": "quotes table gone", "clients": {}},
    "client_health.issues_for_client": lambda name: {"ok": True, "measured": False, "error": "report failed",
                                                     "issues": [], "issue_count": 0, "missing_sources": []},
    "seo._client_base": boom,
}):
    out = rh.client360("Acme Boats", today=TODAY)
p = pills(out)
check("the payload is not measured", out["measured"], False)
check("every pill is unread", sorted({x["state"] for x in p.values()}), ["unread"])
check("...none is ok", not any(x["state"] == "ok" for x in p.values()))
check("...and none claims to be measured", not any(x["measured"] for x in p.values()))
check("the proposal store's own reason travels", "quotes table gone" in p["proposals"]["detail"])
check("the client record's failure is named", any("client record" in u for u in out["unread"]))
check("the strip still answers rather than raising", out["ok"], True)

with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [GROUP],
    "upsell.audits_for": lambda domains: ({}, ""),
    "sales_status.by_client": lambda: NO_QUOTES,
    "client_health.issues_for_client": lambda name: dict(ISSUES_OK, missing_sources=["proofs"],
                                                         warning="proofs not read"),
    "seo._client_base": lambda name: BASE_SEO,
    "seo.load_store": lambda name: store_with([], setup={}),
}):
    out = rh.client360("Acme Boats", today=TODAY)
p = pills(out)
check("a website never audited is a warning, not a clean bill",
      (p["site"]["state"], p["site"]["value"]), ("warn", "Never audited"))
check("no outstanding issues with a source unread is not a clean bill either",
      (p["work"]["state"], "not read" in p["work"]["value"]), ("warn", True))

# --------------------------------------------------- 4. nothing on file is unknown
section("4. A client with no data renders as unknown, never as clear")

with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [],
    "knack_data.products_error": lambda: "",
    "upsell.audits_for": lambda domains: ({}, ""),
    "sales_status.by_client": lambda: NO_QUOTES,
    "client_health.issues_for_client": lambda name: ISSUES_OK,
    "seo._client_base": lambda name: {"client": "Nobody", "products": [], "blogs": False},
    "seo.load_store": lambda name: {"client": "Nobody", "setup": {}, "pages": {}, "sitemap": [], "blogs": {}},
}):
    out = rh.client360("Nobody Co", today=TODAY)
p = pills(out)
check("no pill reads ok except the one source that was genuinely measured clean",
      [k for k, x in p.items() if x["state"] == "ok"], ["work"])
check("products, billing, site, google, proposals, seo and blogs are idle",
      sorted(k for k, x in p.items() if x["state"] == "idle"),
      ["billing", "blogs", "google", "products", "proposals", "seo", "site"])
check("...each saying what is absent", p["site"]["value"], "No website on file")
with Stub(**{
    "knack_data.search_client": lambda q, limit=8: [],
    "knack_data.products_error": lambda: "the products export could not be read",
    "upsell.audits_for": lambda domains: ({}, ""),
    "sales_status.by_client": lambda: NO_QUOTES,
    "client_health.issues_for_client": lambda name: ISSUES_OK,
    "seo._client_base": lambda name: {"client": "Nobody", "products": [], "blogs": False},
    "seo.load_store": lambda name: {"client": "Nobody", "setup": {}, "pages": {}, "sitemap": [], "blogs": {}},
}):
    out = rh.client360("Nobody Co", today=TODAY)
check("an unreadable product book reads unread, not 'nothing on file'",
      (pills(out)["products"]["state"], out["measured"]), ("unread", False))

check("an empty name is refused", rh.client360("")["ok"], False)

# --------------------------------------------------------- 5. the renderer, in node
section("5. The renderer draws the three empties differently")

a = REC.find("/* ---- c360 health (lifted")
b_ = REC.find("/* ---- end c360 health ----")
SRC = REC[a:b_] if a > -1 and b_ > a else ""
check("the health block is still marked for lifting", bool(SRC))
check("the strip container is on the record", 'id="c360Health"' in REC)
check("the record fetches the strip from /api/client/", "/api/client/health?name=" in REC)
check("and a pill's data-go opens the rail section", ".c360-hp[data-go]" in REC and "showC360Section(go.dataset.go)" in REC)

ESC = REC[REC.find("const esc="):REC.find("\n", REC.find("const esc="))]
PAYLOAD = {"pills": [
    {"key": "products", "label": "Products", "state": "ok", "value": "2 live", "measured": True, "detail": "", "go": "overview", "href": ""},
    {"key": "site", "label": "Website audit", "state": "idle", "value": "No website on file", "measured": True, "detail": "d", "go": "website", "href": ""},
    {"key": "proposals", "label": "Proposals", "state": "unread", "value": "Could not read", "measured": False, "detail": "quotes gone", "go": "overview", "href": ""},
    {"key": "blogs", "label": "Blogs", "state": "bad", "value": "1 overdue", "measured": True, "detail": "", "go": "", "href": "/seo/client?name=X#blogs"},
], "unread": ["proposals: quotes gone"], "queue": []}
script = ESC + "\n" + SRC + "\n" + f"""
const out = renderC360Health({json.dumps(PAYLOAD)});
const err = renderC360Health({{error:'boom <b>'}});
console.log(JSON.stringify({{out, err}}));
"""
node = subprocess.run(["node", "-e", script], capture_output=True, text=True)
check("the lifted block runs on its own", node.returncode, 0)
try:
    drawn = json.loads(node.stdout.strip().splitlines()[-1])
except Exception:                                                # noqa: BLE001
    drawn = {"out": "", "err": ""}
html = drawn["out"]
check("a measured-clear pill is drawn ok", 'class="c360-hp ok"' in html)
check("a measured-empty pill is drawn idle, not ok", 'class="c360-hp idle"' in html)
check("a source that refused is drawn unread, not ok and not idle", 'class="c360-hp unread"' in html)
check("...and the strip names what was not measured", "Not measured: proposals: quotes gone" in html)
check("a section pill is a button carrying data-go", 'data-go="overview"' in html)
check("an SEO pill is a link to the SEO record's section", 'href="/seo/client?name=X#blogs"' in html)
check("an error renders as a note, escaped", "boom &lt;b&gt;" in drawn["err"])

# -------------------------------------------------------------- 6. the route
section("6. The route is under /api/client/ and refuses a stranger")

from wsgi import application                                     # noqa: E402
from werkzeug.test import Client                                 # noqa: E402

anon = Client(application)
r = anon.get("/api/client/health?name=Acme%20Boats")
check("anonymous is refused", r.status_code, 401)
staff = Client(application)
staff.post("/login", data={"password": "c360-health-pass"})
r = staff.get("/api/client/health?name=Acme%20Boats")
check("staff gets the strip", r.status_code, 200)
body = r.get_json() or {}
check("...with the pill keys the renderer expects",
      sorted(p["key"] for p in body.get("pills", [])),
      ["billing", "blogs", "google", "products", "proposals", "seo", "site", "work"])
check("...every pill in a state the renderer knows",
      all(p["state"] in rh.STATES for p in body.get("pills", [])))
check("...and none of them ok on a client nobody has heard of",
      not any(p["state"] == "ok" for p in body.get("pills", []) if p["key"] != "work"))
r = staff.get("/api/client/health")
check("a missing name is a 400, not a strip about nobody", r.status_code, 400)
from hub import suite_embed                                      # noqa: E402
check("the path is one the Suite frame may fetch",
      suite_embed.embeddable("/api/client/health"), True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
