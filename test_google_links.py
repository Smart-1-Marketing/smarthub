"""Orphaned Google accounts, and attaching one to a client.

    python3 test_google_links.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. The Google index is written as a fixture rather than
swept, so this needs no connected Google account and reaches no third party.

## What is worth asserting

**An index that has never been built is not a book with no orphans.** The bug
this whole area exists to stop repeating is a confident blank: `_live_google()`
looped over a key that never existed and every client read "no Google access"
including the ones we were administering that afternoon. An empty answer has to
say which kind of empty it is.

**A recorded id is not a guess.** object_153 carries the GA and GTM ids the
client actually uses whether or not anybody connected the account, so an
orphaned property whose id is already recorded against a client is that
client's — and it is the one suggestion here that is evidence rather than
resemblance.

**Never match on a substring, and never pick one of several.** A domain two
clients share cannot say which, so both are offered as *possible* rather than
one being awarded it. That is the guess the billing audit used to make.

**Attaching writes three systems and reports each.** The client record (which
is the index's own strongest rule), the stored index (so the row leaves the
orphan list now rather than at the next sweep) and the Knack website record.

**A recorded id that disagrees is a finding, not a typo.** Overwriting it would
destroy the only evidence that the site may be running a property we do not
administer.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googlelinks-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "google-links-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


from hub import google_index, google_links, jsonstore, knack_websites  # noqa: E402


# ---------------------------------------------------------------------------
section("An index that was never built says so, rather than 'no orphans'")
# ---------------------------------------------------------------------------
empty = google_links.orphans()
check("nothing is reported as an orphan", empty["count"] == 0, empty["count"])
check("...because the index has never been built",
      empty["never_built"] is True, empty)
check("and the note says that rather than implying a clean book",
      "never been built" in empty["note"], empty["note"])
check("attaching against an index with no rows is refused",
      not google_links.attach("G-NOTHERE", "Someone")["ok"])


# ---------------------------------------------------------------------------
section("The fixture index")
# ---------------------------------------------------------------------------
ITEMS = [
    # Recorded in Knack against a client, and joined to nobody by the sweep —
    # a GA4 property summary carries no URL, so the index could not.
    {"platform": "Google Analytics", "type": "property",
     "name": "Buckeye Marina GA4", "resource_id": "G-BUCKEYE1",
     "account_name": "Smart 1 Marketing", "google_login": "ops@smart1.example",
     "domains": [], "client": "", "match": "", "match_detail": ""},
    # Carries a domain two client records share.
    {"platform": "Google Tag Manager", "type": "container",
     "name": "Shared container", "resource_id": "GTM-SHARED1",
     "account_name": "Smart 1", "google_login": "ops@smart1.example",
     "domains": ["shared.example"], "client": "", "match": ""},
    # A Search Console property on the same registrable name, different TLD.
    {"platform": "Search Console", "type": "site",
     "name": "riverstoneheating.net", "resource_id": "sc-domain:riverstoneheating.net",
     "account_name": "", "google_login": "seo@smart1.example",
     "domains": ["riverstoneheating.net"], "client": "", "match": ""},
    # Business Profile — swept, but not one of the three this page is about.
    {"platform": "Google Business Profile", "type": "location",
     "name": "Some Location", "resource_id": "locations/9911",
     "account_name": "", "google_login": "ops@smart1.example",
     "domains": [], "client": "", "match": ""},
    # Already mapped: must never appear in the orphan list.
    {"platform": "Google Analytics", "type": "property",
     "name": "Mapped GA4", "resource_id": "G-MAPPED1", "account_name": "",
     "google_login": "ops@smart1.example", "domains": [],
     "client": "Riverstone Heating LLC", "match": "name"},
]
jsonstore.write_json(
    google_index._path(),                                       # noqa: SLF001
    {"built_at": "2026-08-25T09:00:00+00:00", "last_attempt": "",
     "last_error": "", "items": ITEMS,
     "accounts": ["ops@smart1.example", "seo@smart1.example"], "errors": []})

WEB_ROWS = [
    {"id": "rec1", "client_name": "", "organization": "Buckeye Lake Marina",
     "client": "Buckeye Lake Marina", "has_client": True,
     "production_url": "https://buckeyelakemarina.example",
     "domain": "buckeyelakemarina.example", "ga_account": "G-BUCKEYE1",
     "gtm_account": "", "platform": "", "go_live": "", "hm_fee": 0,
     "media_partner": "", "registrar": "", "live_date": "",
     "client_status": "", "domain_bought": "", "domain_bought_raw": None,
     "domain_bought_on": "", "domain_renews": "", "domain_fee": 0,
     "renewal_billing_date": ""},
    {"id": "rec3", "client_name": "Riverstone Heating LLC", "organization": "",
     "client": "Riverstone Heating LLC", "has_client": True,
     "production_url": "https://riverstoneheating.example",
     "domain": "riverstoneheating.example", "ga_account": "",
     "gtm_account": "GTM-EXISTING", "platform": "", "go_live": "", "hm_fee": 0,
     "media_partner": "", "registrar": "", "live_date": "",
     "client_status": "", "domain_bought": "", "domain_bought_raw": None,
     "domain_bought_on": "", "domain_renews": "", "domain_fee": 0,
     "renewal_billing_date": ""},
]
knack_websites.rows = lambda limit=2000, refresh=False: list(WEB_ROWS)
knack_websites.last_error = lambda: ""

CLIENTS = [
    {"name": "Buckeye Lake Marina", "slug": "blm",
     "url": "https://buckeyelakemarina.example",
     "domain": "buckeyelakemarina.example", "live": True, "running_count": 2,
     "product_count": 3, "source": "knack", "is_house": False,
     "products": [], "running_products": [], "key": "d:buckeyelakemarina.example"},
    {"name": "Riverstone Heating LLC", "slug": "rh",
     "url": "https://riverstoneheating.example",
     "domain": "riverstoneheating.example", "live": True, "running_count": 1,
     "product_count": 1, "source": "knack", "is_house": False,
     "products": [], "running_products": [], "key": "d:riverstoneheating.example"},
    {"name": "Shared One", "slug": "s1", "url": "https://shared.example",
     "domain": "shared.example", "live": True, "running_count": 1,
     "product_count": 1, "source": "knack", "is_house": False,
     "products": [], "running_products": [], "key": "d:shared.example"},
    {"name": "Shared Two", "slug": "s2", "url": "https://shared.example",
     "domain": "shared.example", "live": False, "running_count": 0,
     "product_count": 1, "source": "knack", "is_house": False,
     "products": [], "running_products": [], "key": "d:shared.example"},
]
import hub.clients_registry as registry                             # noqa: E402
registry.all_clients = lambda refresh=False: [dict(c) for c in CLIENTS]
import hub.client_key as client_key                                 # noqa: E402
client_key.alias_index(refresh=True)


# ---------------------------------------------------------------------------
section("Finding whose account it is")
# ---------------------------------------------------------------------------
rep = google_links.orphans()
by_id = {r["resource_id"]: r for r in rep["rows"]}

check("a resource already attached to a client is not an orphan",
      "G-MAPPED1" not in by_id, list(by_id))
check("the three platforms asked for are listed",
      {"G-BUCKEYE1", "GTM-SHARED1", "sc-domain:riverstoneheating.net"}
      <= set(by_id), list(by_id))
check("Business Profile is not, by default",
      "locations/9911" not in by_id, list(by_id))
check("...and what was left out is counted and named, not dropped in silence",
      "other Google platforms are not shown" in rep["note"], rep["note"])
check("asking for everything includes it",
      "locations/9911" in {r["resource_id"] for r in
                           google_links.orphans(include_other=True)["rows"]})

ga = by_id["G-BUCKEYE1"]
top = ga["suggestions"][0]
check("a GA4 id already recorded in Knack names its client",
      top["client"] == "Buckeye Lake Marina", ga["suggestions"])
check("...at the strongest confidence, because it is evidence not resemblance",
      top["confidence"] == "recorded", top)
check("...and says where that came from",
      "Knack website record" in top["why"], top["why"])

gtm = by_id["GTM-SHARED1"]
names = {s["client"] for s in gtm["suggestions"]}
check("a domain two clients share offers both rather than picking one",
      names == {"Shared One", "Shared Two"}, gtm["suggestions"])
check("...as possible, not as a domain match",
      all(s["confidence"] == "possible" for s in gtm["suggestions"]),
      gtm["suggestions"])

gsc = by_id["sc-domain:riverstoneheating.net"]
check("the same name on another TLD is offered, with the doubt attached",
      gsc["suggestions"] and gsc["suggestions"][0]["client"] == "Riverstone Heating LLC"
      and gsc["suggestions"][0]["confidence"] == "possible", gsc["suggestions"])

check("rows we can propose an owner for sort first",
      rep["rows"][0]["suggestions"][0]["confidence"] == "recorded",
      [r["resource_id"] for r in rep["rows"]])
check("the count of those is reported",
      rep["with_suggestion"] == 3, rep["with_suggestion"])
check("searching narrows the list",
      len(google_links.orphans(q="buckeye")["rows"]) == 1)
check("...and searching by the suggested client works too",
      len(google_links.orphans(q="riverstone")["rows"]) == 1)
check("a search matching nothing returns nothing, not everything",
      google_links.orphans(q="zzzznothing")["count"] == 0)
check("filtering to one platform works",
      {r["key"] for r in google_links.orphans(platform="gsc")["rows"]} == {"gsc"})


# ---------------------------------------------------------------------------
section("Attaching writes three systems and names what it could not")
# ---------------------------------------------------------------------------
written = []
knack_websites.configured = lambda: True
knack_websites.set_analytics_ids = lambda rid, ga="", gtm="", actor="": (
    written.append((rid, ga, gtm)) or {"ok": True, "rejected": []})

rep = google_links.attach("G-BUCKEYE1", "Buckeye Lake Marina", actor="Todd")
systems = {w["system"] for w in rep["written"]}
check("the attach reports as done", rep["ok"], rep)
check("the client record is written", "client" in systems, rep)
check("the stored index is written", "index" in systems, rep)
check("the Knack website record is left alone when it already agrees",
      any("Already recorded" in w["detail"] for w in rep["written"]), rep)
check("...so nothing was sent to Knack for it", not written, written)

from hub import seo                                                 # noqa: E402
check("the property really is on the client record afterwards",
      any(x.get("resource_id") == "G-BUCKEYE1"
          for x in seo.get_links("Buckeye Lake Marina").get("analytics", [])),
      seo.get_links("Buckeye Lake Marina"))
check("and it leaves the orphan list at once, not at the next sweep",
      "G-BUCKEYE1" not in {r["resource_id"] for r in google_links.orphans()["rows"]},
      google_links.orphans()["rows"])
check("...because the index row now names the client",
      google_index.for_client("Buckeye Lake Marina",
                              "buckeyelakemarina.example")["total"] >= 1)

rep = google_links.attach("sc-domain:riverstoneheating.net",
                          "Riverstone Heating LLC", actor="Todd")
whys = " ".join(s["why"] for s in rep["skipped"])
check("Search Console still attaches", rep["ok"], rep)
check("...and says plainly that object_153 has nowhere to record it",
      "no field on the website record" in whys, whys)

rep = google_links.attach("GTM-SHARED1", "Riverstone Heating LLC", actor="Todd")
whys = " ".join(s["why"] for s in rep["skipped"])
check("a recorded id that disagrees is not overwritten",
      "GTM-EXISTING" in whys, whys)
check("...and the disagreement is described as worth resolving",
      "worth resolving" in whys, whys)
check("...while the client record and the index were still written",
      {w["system"] for w in rep["written"]} == {"client", "index"}, rep)

forced = google_links.attach("GTM-SHARED1", "Riverstone Heating LLC",
                             actor="Todd", force=True)
check("forcing it does write Knack", written and written[-1][2] == "GTM-SHARED1",
      written)

check("a resource that is not in the index is refused, never invented",
      not google_links.attach("G-MADEUP", "Buckeye Lake Marina")["ok"])
check("...with a reason worth reading",
      "not in the Google account index"
      in google_links.attach("G-MADEUP", "Buckeye Lake Marina")["error"])
check("a resource with no client is refused",
      not google_links.attach("GTM-SHARED1", "")["ok"])

many = google_links.attach_many(
    [{"resource_id": "GTM-SHARED1", "client": "Shared One"},
     {"resource_id": "G-MADEUP", "client": "Shared One"}])
check("a bulk attach reports each one separately",
      many["attached"] == 1 and len(many["failed"]) == 1, many)


# ---------------------------------------------------------------------------
section("A source that could not be read is named")
# ---------------------------------------------------------------------------
def _boom(limit=2000, refresh=False):
    raise RuntimeError("Knack timed out")


knack_websites.rows = _boom
rep = google_links.orphans()
src = {s["source"]: s for s in rep["sources"]}
check("the Knack registry is reported as not read",
      src["knack"]["ok"] is False, src)
check("...by name, with the reason", "Knack timed out" in src["knack"]["error"])
check("and the note says the suggestions are a floor",
      "floor, not a total" in rep["note"], rep["note"])
knack_websites.rows = lambda limit=2000, refresh=False: list(WEB_ROWS)


# ---------------------------------------------------------------------------
section("A domain match is applied now, not at the next sweep")
# ---------------------------------------------------------------------------
# The join is the index's own rule 2. What is being asserted is *when* it
# runs: the stored index only ever saw the client list as it stood at sweep
# time, so a client that gained a URL an hour ago left their container sitting
# on the report as belonging to nobody, next to the client whose domain it
# plainly carries.
LATE_ITEMS = [
    # Carries a client's domain and was swept before that client had a URL.
    {"platform": "Google Tag Manager", "type": "container",
     "name": "Marina container", "resource_id": "GTM-LATE1",
     "google_login": "ops@smart1.example", "domains": ["buckeyelakemarina.example"],
     "client": "", "match": "", "match_detail": ""},
    # Two client records share this domain, so it cannot say which.
    {"platform": "Search Console", "type": "site", "name": "shared.example",
     "resource_id": "sc-domain:shared.example", "google_login": "ops@smart1.example",
     "domains": ["shared.example"], "client": "", "match": ""},
    # No URL at all — a GA4 property summary never carries one.
    {"platform": "Google Analytics", "type": "property", "name": "Nameless GA4",
     "resource_id": "G-NOURL1", "google_login": "ops@smart1.example",
     "domains": [], "client": "", "match": ""},
    # Already attached to somebody, and its domain says otherwise. That
    # disagreement is a finding; re-deciding it on a page load is the worst
    # possible place to resolve one.
    {"platform": "Google Analytics", "type": "property", "name": "Disputed",
     "resource_id": "G-DISPUTED", "google_login": "ops@smart1.example",
     "domains": ["buckeyelakemarina.example"], "client": "Riverstone Heating LLC",
     "match": "attached"},
]


def _write_index(items):
    jsonstore.write_json(
        google_index._path(),                                   # noqa: SLF001
        {"built_at": "2026-08-25T09:00:00+00:00", "last_attempt": "",
         "last_error": "", "items": [dict(i) for i in items],
         "accounts": ["ops@smart1.example"], "errors": []})


_write_index(LATE_ITEMS)
out = google_index.apply_domain_matches()
check("the resource carrying a client's domain is joined",
      out["mapped"] == 1 and out["items"][0]["client"] == "Buckeye Lake Marina",
      out)
check("...and what it joined is named, not just counted",
      out["items"][0]["domain"] == "buckeyelakemarina.example", out)

after = {r["resource_id"]: r for r in google_index.rows()}
check("the stored row now names the client",
      after["GTM-LATE1"]["client"] == "Buckeye Lake Marina", after["GTM-LATE1"])
check("...on the domain rule, and the row says so",
      after["GTM-LATE1"]["match"] == "domain", after["GTM-LATE1"])
check("...and says it happened after the sweep rather than in it",
      "after the sweep" in after["GTM-LATE1"]["match_detail"],
      after["GTM-LATE1"]["match_detail"])
check("a domain two clients share is awarded to neither",
      after["sc-domain:shared.example"]["client"] == "",
      after["sc-domain:shared.example"])
check("a resource with no URL is left for a human",
      after["G-NOURL1"]["client"] == "", after["G-NOURL1"])
check("a resource that already has a client is never re-decided",
      after["G-DISPUTED"]["client"] == "Riverstone Heating LLC",
      after["G-DISPUTED"])

again = google_index.apply_domain_matches()
check("running it again joins nothing and writes nothing",
      again["mapped"] == 0 and not again["items"], again)

# The whole point is that these stop being orphans without anybody clicking.
orph = google_links.orphans()
check("the joined resource has left the orphan list",
      "GTM-LATE1" not in {r["resource_id"] for r in orph["rows"]},
      [r["resource_id"] for r in orph["rows"]])
check("...and the ones that genuinely cannot be joined have not",
      {"sc-domain:shared.example", "G-NOURL1"}
      <= {r["resource_id"] for r in orph["rows"]},
      [r["resource_id"] for r in orph["rows"]])


# ---------------------------------------------------------------------------
section("The QA report can map a resource to a customer from the row")
# ---------------------------------------------------------------------------
# The report is where somebody notices that a property maps to nobody, so it
# has to be where they can say whose it is. Sending them to another screen to
# find the same row again is how a list stays unactioned.
_write_index(LATE_ITEMS)                     # unmapped again, as swept
from hub import qa                                                  # noqa: E402

rep = qa.google_accounts()
check("the report carries a customer picker column",
      "Map to client" in rep["columns"], rep["columns"])
# Not at the end: on the end it was the seventh column of a table wider than
# its own scroll box, so the control sat past the right edge with the header
# reading "MAP TO CLIE". It belongs beside the cell it changes.
check("...immediately after the cell it changes, so it is on screen",
      rep["columns"].index("Map to client")
      == rep["columns"].index("Mapped to") + 1, rep["columns"])
check("every row has one cell per column",
      all(len(r) == len(rep["columns"]) for r in rep["rows"]),
      [len(r) for r in rep["rows"]])

mi = rep["columns"].index("Map to client")
cells = {r[mi]["map_client"]: r[mi] for r in rep["rows"]}
check("the picker addresses the resource by its own id",
      set(cells) == {i["resource_id"] for i in LATE_ITEMS}, list(cells))
check("the domain match was applied on the load, not left for the sweep",
      cells["GTM-LATE1"]["current"] == "Buckeye Lake Marina",
      cells["GTM-LATE1"])
check("...and the note says how many changed under the reader",
      "joined to a client by domain on this load" in rep["note"], rep["note"])

check("an unmapped resource's picker opens on the suggestions",
      {s["client"] for s in cells["sc-domain:shared.example"]["suggestions"]}
      >= {"Shared One", "Shared Two"},
      cells["sc-domain:shared.example"]["suggestions"])
check("...with the evidence for each, so nothing is a bare name",
      all(s["why"] and s["confidence"]
          for s in cells["sc-domain:shared.example"]["suggestions"]),
      cells["sc-domain:shared.example"]["suggestions"])
check("a resource that already has a client is proposed nobody",
      cells["G-DISPUTED"]["suggestions"] == [], cells["G-DISPUTED"])
check("every picker cell exports as text rather than a blank CSV column",
      all(c["text"] for c in cells.values()),
      {k: v["text"] for k, v in cells.items()})
check("a resource nothing can be proposed for says so rather than showing one",
      "no suggestion" in cells["G-NOURL1"]["text"], cells["G-NOURL1"])


# ---------------------------------------------------------------------------
section("A shared word is a proposal, and it says which word")
# ---------------------------------------------------------------------------
# The four rules above join on a recorded id, a domain or a whole name. None
# of them fires for a GTM container called "Buckeye Marina - new" against a
# client filed as "Buckeye Lake Marina", which a person reading the row can
# see in a second. So a shared word is offered too — last, weakest, and with
# the word itself named so the guess can be judged rather than trusted.
_write_index(LATE_ITEMS + [
    {"platform": "Google Tag Manager", "type": "container",
     "name": "Buckeye Marina - new", "resource_id": "GTM-WORDS",
     "google_login": "ops@smart1.example", "domains": [], "client": "",
     "match": ""},
    # Nothing in this name identifies anybody: every word is either a company
    # suffix or a platform word.
    {"platform": "Google Analytics", "type": "property",
     "name": "GA4 Property - Main Account", "resource_id": "G-NOWORDS",
     "google_login": "ops@smart1.example", "domains": [], "client": "",
     "match": ""},
])
ctx = google_links._context()                                   # noqa: SLF001
words = google_links.suggest_for(
    {"platform": "Google Tag Manager", "name": "Buckeye Marina - new",
     "resource_id": "GTM-WORDS", "domains": []}, ctx)
by_client = {w["client"]: w for w in words}
check("a resource sharing a word with a client name proposes that client",
      "Buckeye Lake Marina" in by_client, [w["client"] for w in words])
check("...as possible, never as anything stronger",
      by_client.get("Buckeye Lake Marina", {}).get("confidence") == "possible",
      by_client.get("Buckeye Lake Marina"))
check("...and names the words it matched on",
      "buckeye" in by_client.get("Buckeye Lake Marina", {}).get("why", ""),
      by_client.get("Buckeye Lake Marina", {}).get("why"))
check("a client sharing no word with it is not proposed",
      "Shared One" not in by_client, [w["client"] for w in words])

# LLC is not a word that identifies a business, and matching on it would offer
# every incorporated client as a possible owner of everything.
plain = google_links.suggest_for(
    {"platform": "Google Analytics", "name": "GA4 Property - Main Account",
     "resource_id": "G-NOWORDS", "domains": []}, ctx)
check("a name made only of company and platform words proposes nobody",
      plain == [], plain)
check("...because those words are not counted at all",
      not google_links._words("LLC Inc the Google Analytics property"),   # noqa: SLF001
      google_links._words("LLC Inc the Google Analytics property"))       # noqa: SLF001
check("a real word survives tokenising",
      google_links._words("Buckeye Lake Marina LLC")                      # noqa: SLF001
      == {"buckeye", "lake", "marina"},
      google_links._words("Buckeye Lake Marina LLC"))                     # noqa: SLF001

# A word half the book shares identifies none of it. The ceiling is computed
# against the client list rather than guessed at.
many = [{"name": f"Heating Co {i}", "slug": f"h{i}",
         "url": f"https://heating{i}.example", "domain": f"heating{i}.example",
         "live": True, "running_count": 1, "product_count": 1, "source": "knack",
         "is_house": False, "products": [], "running_products": [],
         "key": f"d:heating{i}.example"} for i in range(30)]
registry.all_clients = lambda refresh=False: [dict(c) for c in CLIENTS + many]
client_key.alias_index(refresh=True)
wide = google_links._context()                                  # noqa: SLF001
check("a word shared by more clients than the ceiling is dropped",
      "heating" in (wide.get("common_words") or set()),
      sorted(wide.get("common_words") or []))
flood = google_links.suggest_for(
    {"platform": "Google Analytics", "name": "Heating GA4",
     "resource_id": "G-FLOOD", "domains": []}, wide)
check("...so it does not propose thirty clients for one resource",
      len(flood) <= 3, [f["client"] for f in flood])
registry.all_clients = lambda refresh=False: [dict(c) for c in CLIENTS]
client_key.alias_index(refresh=True)


# ---------------------------------------------------------------------------
section("The orphan list is paged, and every count is of the whole book")
# ---------------------------------------------------------------------------
MANY = [{"platform": "Google Analytics", "type": "property",
         "name": f"Orphan property {i}", "resource_id": f"G-PAGE{i}",
         "google_login": "ops@smart1.example", "domains": [], "client": "",
         "match": ""} for i in range(60)]
_write_index(MANY)

first = google_links.orphans(limit=25, offset=0)
check("a page is the size that was asked for", len(first["rows"]) == 25,
      len(first["rows"]))
check("...and the count is of the whole list, not the page",
      first["count"] == 60, first["count"])
check("...which is the number a reader would otherwise get wrong",
      first["orphans_total"] == 60, first["orphans_total"])
check("it says there is more and where to continue from",
      first["has_more"] is True and first["next_offset"] == 25, first)

second = google_links.orphans(limit=25, offset=25)
check("the next page carries different rows",
      not ({r["resource_id"] for r in first["rows"]}
           & {r["resource_id"] for r in second["rows"]}),
      "pages overlap")
last = google_links.orphans(limit=25, offset=50)
check("the final page reports no more", last["has_more"] is False, last)
check("...and holds the remainder", len(last["rows"]) == 10, len(last["rows"]))
check("an offset past the end is empty rather than an error",
      google_links.orphans(limit=25, offset=999)["rows"] == [])

# Searching has to search the book, not the page. That is the whole reason it
# moved off the browser: a filter over whichever 25 rows had been sent is a
# filter that quietly answers about part of the list.
_write_index(MANY + [
    {"platform": "Google Analytics", "type": "property", "name": "Findable one",
     "resource_id": "G-FINDME", "google_login": "ops@smart1.example",
     "domains": [], "client": "", "match": ""}])
hit = google_links.orphans(q="findable", limit=25, offset=0)
check("a row on the last page is still found by a search",
      [r["resource_id"] for r in hit["rows"]] == ["G-FINDME"], hit["rows"])
check("...and the count reflects the search, not the book",
      hit["count"] == 1, hit["count"])

# The three tickboxes, not one platform at a time.
_write_index(LATE_ITEMS)
picked = google_links.orphans(platforms=["gsc"])
check("the platform tickboxes filter the list",
      {r["key"] for r in picked["rows"]} == {"gsc"},
      [r["key"] for r in picked["rows"]])
check("the older single-platform parameter still works",
      {r["key"] for r in google_links.orphans(platform="ga4")["rows"]} == {"ga4"})


# ---------------------------------------------------------------------------
section("A platform that refused is not a platform with nothing in it")
# ---------------------------------------------------------------------------
# The reason Tag Manager and Search Console can vanish from this page with
# nothing anywhere saying why: every fetcher swallows its own exception and
# returns an empty list, so a 403 from a scope the token never got looks
# exactly like a login that owns no containers.
data = jsonstore.read_json(google_index._path(), default={})    # noqa: SLF001
data["sweep"] = [
    {"email": "a@x.example", "platform": "Google Analytics", "kind": "ok",
     "ok": True, "count": 12, "error": ""},
    {"email": "a@x.example", "platform": "Google Tag Manager", "kind": "refused",
     "ok": False, "count": 0, "error": "403 insufficient scope"},
    {"email": "a@x.example", "platform": "Google Business Profile",
     "kind": "disabled", "ok": False, "count": 0,
     "error": "GOOGLE_GMB_ENABLED is not set on this deployment."},
]
jsonstore.write_json(google_index._path(), data)                # noqa: SLF001

st = google_index.status()
check("the sweep is carried on the index", len(st["sweep"]) == 3, st["sweep"])
check("...and what failed is separated from what answered",
      {n["platform"] for n in st["sweep_problems"]}
      == {"Google Tag Manager", "Google Business Profile"}, st["sweep_problems"])

rep = google_links.orphans()
check("the orphan page is handed the sweep", len(rep["sweep"]) == 3, rep["sweep"])
check("...and the note says rows are missing rather than absent",
      "missing rather than absent" in rep["note"], rep["note"])
check("...naming the platforms that could not be swept",
      "Tag Manager" in rep["note"], rep["note"])

data["sweep"] = []
jsonstore.write_json(google_index._path(), data)                # noqa: SLF001
check("an index built before this existed says not measured, not all-clear",
      "not measured" in google_links.orphans()["note"],
      google_links.orphans()["note"])


# ---------------------------------------------------------------------------
section("The sweep itself tells refused apart from empty")
# ---------------------------------------------------------------------------
# Asserted against the real fetchers with only their HTTP call stubbed, because
# the classification is the whole point: "reconnect this login" and "try again
# later" need opposite things from a person, and an empty book needs neither.
try:
    from modules.google_finder import app as gf

    def _403(*a, **k):
        raise RuntimeError("HTTP 403: insufficient authentication scopes")

    def _net(*a, **k):
        raise RuntimeError("Connection reset by peer")

    real_get, real_gtm = gf.google_get, gf.gtm_get
    gf.google_get = gf.gtm_get = _403
    notes = []
    gf.fetch_ga_items("t", "a@x", notes)
    gf.fetch_gtm_items("t", "a@x", notes)
    gf.fetch_gsc_items("t", "a@x", notes)
    check("a 403 on any platform is recorded as refused",
          [n["kind"] for n in notes] == ["refused"] * 3, notes)
    check("...against the login it happened to",
          {n["email"] for n in notes} == {"a@x"}, notes)

    gf.google_get = gf.gtm_get = _net
    notes = []
    gf.fetch_ga_items("t", "b@x", notes)
    check("a network error is failed, not refused — they need opposite fixes",
          notes and notes[0]["kind"] == "failed", notes)

    gf.google_get = lambda *a, **k: {"accountSummaries": []}
    notes = []
    gf.fetch_ga_items("t", "c@x", notes)
    check("a login that genuinely owns nothing is ok with a count of zero",
          notes and notes[0]["kind"] == "ok" and notes[0]["count"] == 0, notes)
    check("...and ok is the only kind that reads as an answer",
          notes[0]["ok"] is True, notes)

    notes = []
    gf.fetch_gmb_items("t", "gmb@x", notes)
    check("a platform switched off says so rather than returning an empty book",
          notes and notes[0]["kind"] == "disabled", notes)
    check("...and names the variable that would switch it on",
          "GOOGLE_GMB_ENABLED" in notes[0]["error"], notes)

    # The old signature has to keep working: nine other callers pass no notes.
    check("a caller that wants no notes is unaffected",
          gf.fetch_ga_items("t", "d@x") == [])
    gf.google_get, gf.gtm_get = real_get, real_gtm
except Exception as exc:                                        # noqa: BLE001
    check("the Google Finder sweep can be inspected", False, exc)


# ---------------------------------------------------------------------------
section("The routes exist under the hub app, not a mount")
# ---------------------------------------------------------------------------
# /google belongs to Google Finder, so these have to be hub routes elsewhere —
# a route under a mounted prefix is never reached, and nothing looks wrong.
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})
    for path in ("/tools/google-match", "/api/google/orphans",
                 "/api/google/orphans?q=buckeye&platform=ga4"):
        check(f"{path} answers", composed.get(path).status_code == 200)
    check("attach refuses an unknown resource through the route too",
          composed.post("/api/google/attach",
                        json={"resource_id": "G-MADEUP", "client": "X"}
                        ).get_json()["ok"] is False)
except Exception as exc:                                        # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
