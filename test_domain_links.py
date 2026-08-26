"""Attaching a domain to a client, and the domain records that follow.

    python3 test_domain_links.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Every external reader is stubbed, so it needs no
Knack, Simvoly or Insites credentials and reaches no third party.

## What is worth asserting

**A match that lands in one system is invisible.** Matching a site used to
write `internal_client_name` on the Simvoly project and nothing else, so a rep
who matched a site here opened Client 360 and found the client still had no
website. `domain_links.attach()` writes four systems and reports each one
separately — "attached" and "attached in two of four places" are different
outcomes, and one tick for both is how a rep learns not to trust the tick.

**A client has more than one website.** The shop, the campaign landing pages,
the microsite for one location. An overlay that keeps one URL per client makes
the rest invisible to everything keyed on domain, which is the problem the
overlay exists to fix.

**Accepting a URL has to stick.** Accepting one and being offered it again on
the next scan reads as a button that does nothing. The client registry caches
per process and there are two gunicorn workers, so the scan after an accept
often runs in the worker that never saw it; the overlay is the durable record
and it decides.

**A project already linked to somebody else is not relinked.** A wrong
`internal_client_name` attributes revenue to the wrong client, and quietly
overwriting one is worse than refusing to.

**A tick that outlives what it was ticked for is a wrong answer.** A domain
renews every year; a "billed" tick kept against the record rather than against
the renewal billing date would stay green for a charge nobody has raised.

**A registrar we recorded and a registrar WHOIS observed are different
claims.** Both are useful; presenting the second as the first is not.
"""
import os
import shutil
import sys
import tempfile
import types
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-domainlinks-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "domain-links-test")
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


from hub import (client_urls, domain_links, domain_purchase,        # noqa: E402
                 knack_websites)


# ---------------------------------------------------------------------------
section("object_153 is pinned by field id, and the labels are ours")
# ---------------------------------------------------------------------------
# Pinned rather than matched on label: a renamed label breaks a label match
# silently, which is how the Issue column on the Accounting report came to be
# empty. These are the ids this deployment uses.
for key, want in (("domain", "field_3111"), ("organization", "field_2924"),
                  ("client_name", "field_3112"), ("registrar", "field_2926"),
                  ("live_date", "field_3048"), ("client_status", "field_3193"),
                  ("domain_bought", "field_2964"),
                  ("domain_bought_on", "field_3063"),
                  ("domain_renews", "field_3101"),
                  ("domain_fee", "field_3064"),
                  ("renewal_billing_date", "field_3298")):
    check(f"{key} is {want}", knack_websites.FIELDS.get(key) == want,
          knack_websites.FIELDS.get(key))

bought = next(f for f in knack_websites.DOMAIN_RECORD if f["key"] == "domain_bought")
check("the purchase question is asked as a person would ask it",
      bought["label"] == "Did we buy the domain?", bought["label"])
check("...and still carries Knack's own label, so the two can be reconciled",
      bought["knack_label"] == "S1M Purchase Domain for Client?", bought)
check("every domain-record field is writable",
      set(knack_websites.EDITABLE) ==
      {f["key"] for f in knack_websites.DOMAIN_RECORD})
check("and nothing else is — a mistyped key cannot land on the record",
      "hm_fee" not in knack_websites.EDITABLE)


# ---------------------------------------------------------------------------
section("A domain matches a client through the registry, or not at all")
# ---------------------------------------------------------------------------
WEB_ROWS = [
    {"id": "rec1", "client_name": "", "organization": "Buckeye Lake Marina",
     "client": "Buckeye Lake Marina", "has_client": True,
     "production_url": "https://buckeyelakemarina.example",
     "domain": "buckeyelakemarina.example", "ga_account": "", "gtm_account": "",
     "platform": "WordPress", "go_live": "", "hm_fee": 0, "media_partner": "",
     "registrar": "GoDaddy", "live_date": "01/04/2024",
     "client_status": "Active", "domain_bought": "Yes",
     "domain_bought_raw": True, "domain_bought_on": "01/02/2024",
     "domain_renews": "01/02/2026", "domain_fee": 22.5,
     "renewal_billing_date": "08/14/2026"},
    {"id": "rec2", "client_name": "", "organization": "", "client": "",
     "has_client": False, "production_url": "https://orphaned.example",
     "domain": "orphaned.example", "ga_account": "", "gtm_account": "",
     "platform": "", "go_live": "", "hm_fee": 0, "media_partner": "",
     "registrar": "", "live_date": "", "client_status": "",
     "domain_bought": "No", "domain_bought_raw": False,
     "domain_bought_on": "", "domain_renews": "", "domain_fee": 0,
     "renewal_billing_date": ""},
    {"id": "rec3", "client_name": "Riverstone Heating LLC",
     "organization": "", "client": "Riverstone Heating LLC",
     "has_client": True, "production_url": "https://riverstoneheating.example",
     "domain": "riverstoneheating.example", "ga_account": "",
     "gtm_account": "", "platform": "", "go_live": "", "hm_fee": 0,
     "media_partner": "", "registrar": "", "live_date": "",
     "client_status": "Active", "domain_bought": True,
     "domain_bought_raw": True, "domain_bought_on": "03/03/2025",
     "domain_renews": "03/03/2026", "domain_fee": 18.0,
     "renewal_billing_date": ""},
    {"id": "rec5", "client_name": "", "organization": "", "client": "",
     "has_client": False, "production_url": "https://coastalroofing.example",
     "domain": "coastalroofing.example", "ga_account": "", "gtm_account": "",
     "platform": "", "go_live": "", "hm_fee": 0, "media_partner": "",
     "registrar": "", "live_date": "", "client_status": "",
     "domain_bought": "", "domain_bought_raw": None, "domain_bought_on": "",
     "domain_renews": "", "domain_fee": 0, "renewal_billing_date": ""},
    {"id": "rec4", "client_name": "", "organization": "", "client": "",
     "has_client": False, "production_url": "", "domain": "res.cloudinary.com",
     "ga_account": "", "gtm_account": "", "platform": "", "go_live": "",
     "hm_fee": 0, "media_partner": "", "registrar": "", "live_date": "",
     "client_status": "", "domain_bought": "", "domain_bought_raw": None,
     "domain_bought_on": "", "domain_renews": "", "domain_fee": 0,
     "renewal_billing_date": ""},
]
knack_websites.rows = lambda limit=2000, refresh=False: list(WEB_ROWS)

hit = knack_websites.client_for_domain("https://WWW.BuckeyeLakeMarina.example/about")
check("a domain resolves to the client the registry files it under",
      hit.get("client") == "Buckeye Lake Marina", hit)
check("...and says which field said so", hit.get("field") in
      ("Client organization", "Client"), hit)
check("a record with a domain and nobody's name is not a match",
      knack_websites.client_for_domain("orphaned.example") == {},
      knack_websites.client_for_domain("orphaned.example"))
check("...it is an orphan",
      [r["id"] for r in knack_websites.orphan_rows()] == ["rec2", "rec5"],
      [r["id"] for r in knack_websites.orphan_rows()])
check("a file host is never offered as an orphan website either",
      not any(r["domain"] == "res.cloudinary.com"
              for r in knack_websites.orphan_rows()))


# ---------------------------------------------------------------------------
section("The registrar: recorded, observed, or not known — never blurred")
# ---------------------------------------------------------------------------
reg = knack_websites.registrar_for("buckeyelakemarina.example")
check("a registrar on the Knack record is used as recorded",
      reg["value"] == "GoDaddy" and reg["source"] == "knack", reg)

_real_scans = sys.modules.get("modules.scans.app")
stub = types.ModuleType("modules.scans.app")
stub.latest_payload_for_domain = lambda d: (
    {"domain_age": {"registrar": "Tucows Domains Inc.",
                    "registered_date": "2011-05-02",
                    "expiry_date": "2027-05-02"}}
    if d == "riverstoneheating.example" else {})
sys.modules["modules.scans.app"] = stub
try:
    reg = knack_websites.registrar_for("riverstoneheating.example")
    check("with none on the record, WHOIS from the site scan answers instead",
          reg["value"] == "Tucows Domains Inc.", reg)
    check("...labelled as observed rather than as something we recorded",
          reg["source"] == "scan" and "observed" in reg["label"], reg)
    check("...and it brings the expiry date with it",
          reg.get("expires") == "2027-05-02", reg)
    reg = knack_websites.registrar_for("orphaned.example")
    check("a domain with neither says so, rather than reading as none",
          reg["value"] == "" and "no completed site scan" in reg["label"], reg)
finally:
    if _real_scans is not None:
        sys.modules["modules.scans.app"] = _real_scans
    else:
        sys.modules.pop("modules.scans.app", None)


# ---------------------------------------------------------------------------
section("A client can have more than one website")
# ---------------------------------------------------------------------------
first = client_urls.accept("Buckeye Lake Marina", "buckeyelakemarina.example",
                           source="knack_website", actor="Todd")
second = client_urls.accept("Buckeye Lake Marina",
                            "https://buckeyelakemarina.example/summer-sale",
                            source="attached", actor="Todd")
check("the first URL is recorded", first["ok"])
check("a second does not replace it", len(second["sites"]) == 1
      or {s["domain"] for s in second["sites"]} ==
      {"buckeyelakemarina.example"}, second["sites"])

third = client_urls.accept("Buckeye Lake Marina", "marina-summer.example",
                           source="attached", actor="Todd")
doms = {s["domain"] for s in third["sites"]}
check("a genuinely different domain is added beside the first",
      doms == {"buckeyelakemarina.example", "marina-summer.example"}, doms)
check("the first stays the primary, so one-URL readers are unchanged",
      third["row"]["domain"] == "buckeyelakemarina.example", third["row"])
check("sites_for lists every one",
      len(client_urls.sites_for("Buckeye Lake Marina")) == 2)

old_shape = {"client": "Legacy Co", "url": "https://legacy.example",
             "domain": "legacy.example", "source": "scan"}
check("a row written before the list existed still reads as one site",
      [s["domain"] for s in client_urls.sites_of(old_shape)] == ["legacy.example"],
      client_urls.sites_of(old_shape))

check("removing one leaves the others alone",
      client_urls.clear("Buckeye Lake Marina", "marina-summer.example")["ok"]
      and [s["domain"] for s in client_urls.sites_for("Buckeye Lake Marina")]
      == ["buckeyelakemarina.example"],
      client_urls.sites_for("Buckeye Lake Marina"))
check("removing one that was never there says so rather than pretending",
      not client_urls.clear("Buckeye Lake Marina", "nothing.example")["ok"])


# ---------------------------------------------------------------------------
section("An accepted URL is not proposed again on the next scan")
# ---------------------------------------------------------------------------
# The bug this asserts against: accepting a domain and immediately being
# offered the same one again, as if the click had done nothing. The registry
# caches for two minutes per process and there are two gunicorn workers, so the
# scan after an accept often runs in the worker that never saw it.
CLIENTS = [
    {"name": "Buckeye Lake Marina", "slug": "blm", "url": "", "domain": "",
     "live": True, "running_count": 2, "product_count": 3, "source": "knack"},
    {"name": "Nowhere Services", "slug": "nw", "url": "", "domain": "",
     "live": False, "running_count": 0, "product_count": 1, "source": "knack"},
]
import hub.clients_registry as registry                             # noqa: E402
# Deliberately stale, exactly as the other worker's cache would be: it still
# reports the client as having no URL after the accept.
registry.all_clients = lambda refresh=False: [dict(c) for c in CLIENTS]


def _fake_products(found):
    client_urls._add(found, "Buckeye Lake Marina",                  # noqa: SLF001
                     "https://buckeyelakemarina.example", "product_clickthru")
    return {"rows": 1}


client_urls._READERS = (("product_clickthru", _fake_products),)     # noqa: SLF001

report = client_urls.missing()
names = [c["client"] for c in report["clients"]]
check("the client we already accepted a URL for drops off the list",
      "Buckeye Lake Marina" not in names, names)
check("...and the one with nothing on file is still on it",
      "Nowhere Services" in names, names)
check("the count of who is missing a website reflects that",
      report["without_url"] == 1, report["without_url"])

with_found = client_urls.missing(include_found=True)
row = next(c for c in with_found["clients"] if c["client"] == "Buckeye Lake Marina")
check("asking for everything shows what was accepted for them",
      [s["domain"] for s in row["accepted_sites"]] == ["buckeyelakemarina.example"],
      row["accepted_sites"])


# ---------------------------------------------------------------------------
section("Attaching writes every system, and names the ones it could not")
# ---------------------------------------------------------------------------
PROJECTS = [
    {"project_id": "p1", "name": "Coastal", "status": "ACTIVE",
     "lifecycle_state": "", "domain": "coastalroofing.example",
     "internal_client_name": ""},
    {"project_id": "p2", "name": "Someone else", "status": "ACTIVE",
     "lifecycle_state": "", "domain": "otherclient.example",
     "internal_client_name": "Another Client"},
]
import hub.sites_match as sites_match                               # noqa: E402
sites_match._site_rows = lambda: [dict(r) for r in PROJECTS]        # noqa: SLF001

saved_meta = []
fake_db = types.SimpleNamespace(
    save_meta=lambda pid, **kw: saved_meta.append((pid, kw)))
import modules.sites_admin as sites_admin_pkg                        # noqa: E402
sites_admin_pkg.db = fake_db
sys.modules["modules.sites_admin.db"] = fake_db

written_to_knack = []
knack_websites.configured = lambda: True
knack_websites.attach_client = lambda rid, client, actor="": (
    written_to_knack.append((rid, client)) or {"ok": True, "rejected": []})

rep = domain_links.attach("coastalroofing.example", "Coastal Roofing",
                          actor="Todd")
systems = {w["system"] for w in rep["written"]}
check("the attach reports as done", rep["ok"], rep)
check("the Hub's own client registry is written", "hub" in systems, rep)
check("the client's 360 record is written", "c360" in systems, rep)
check("the Simvoly project is written", "sites" in systems, rep)
check("...with the client name on it", saved_meta and
      saved_meta[-1][1].get("internal_client_name") == "Coastal Roofing",
      saved_meta)
check("and the Knack website record is written",
      written_to_knack and written_to_knack[-1] == ("rec5", "Coastal Roofing"),
      written_to_knack)
check("the note counts systems rather than saying a bare 'done'",
      "4 of 4 systems" in rep["note"] or "of 4 systems" in rep["note"],
      rep["note"])

from hub import seo                                                 # noqa: E402
check("the website really is on the client's 360 record afterwards",
      any(w.get("domain") == "coastalroofing.example"
          for w in seo.get_links("Coastal Roofing").get("website", [])),
      seo.get_links("Coastal Roofing"))

saved_meta.clear()
rep = domain_links.attach("otherclient.example", "Buckeye Lake Marina",
                          actor="Todd")
whys = " ".join(s["why"] for s in rep["skipped"])
check("a project already linked to somebody else is not relinked",
      not saved_meta, saved_meta)
check("...and the refusal names who it belongs to",
      "Another Client" in whys, whys)
check("the row still counts as attached where it could be", rep["ok"], rep)
check("the note says it did not land everywhere",
      "could not be written to must not read as one that was" in rep["note"]
      or "The rest are listed" in rep["note"], rep["note"])

bad = domain_links.attach("res.cloudinary.com", "Buckeye Lake Marina")
check("a file host is refused before anything is written",
      not bad["ok"] and "file host" in bad.get("error", ""), bad)
check("...and nothing was written for it", not bad["written"], bad)
check("a domain with no client is refused",
      not domain_links.attach("something.example", "")["ok"])

# The point of writing the 360 record at all: the client's own page shows the
# website afterwards. The websites export is stale by definition and does not
# have a newly discovered domain in it, so an attachment that only resolved
# against the export was made and then not shown — "No website record matched"
# on a client somebody had just matched.
import hub.knack_data as knack_data                                 # noqa: E402
knack_data._product_source = lambda: (                              # noqa: SLF001
    [{"client": "Coastal Roofing", "product": "SEO", "status": "Live"}],
    "stub", None)
knack_data.websites = lambda: []
groups = knack_data.search_client("Coastal Roofing")
sites = [w for g in groups for w in g["websites"]]
check("the attached domain shows on the client's 360 record",
      any(w["domain"] == "coastalroofing.example" for w in sites), sites)
check("...marked as attached rather than presented as filed data",
      all(w.get("attached") for w in sites
          if w["domain"] == "coastalroofing.example"), sites)

many = domain_links.attach_many(
    [{"domain": "coastalroofing.example", "client": "Coastal Roofing"},
     {"domain": "not a url", "client": "Buckeye Lake Marina"}])
check("a bulk attach reports each one separately",
      many["attached"] == 1 and len(many["failed"]) == 1, many)


# ---------------------------------------------------------------------------
section("Orphans: a URL with no client, from every system that holds one")
# ---------------------------------------------------------------------------
def _broken(add):
    raise RuntimeError("Knack timed out")


orph = domain_links.orphans()
doms = {r["domain"] for r in orph["domains"]}
check("a Knack record with a domain and no client is an orphan",
      "orphaned.example" in doms, doms)
check("a domain that already belongs to a client is not",
      "buckeyelakemarina.example" not in doms
      and "coastalroofing.example" not in doms, doms)
check("a file host is not offered as an orphan",
      "res.cloudinary.com" not in doms, doms)
check("...and is counted rather than silently dropped",
      any(r["domain"] == "res.cloudinary.com"
          for r in orph["rejected_domains"]), orph["rejected_domains"])
row = next(r for r in orph["domains"] if r["domain"] == "orphaned.example")
check("each orphan says where it was seen", bool(row["sightings"]), row)
check("...naming the system in words",
      any("Knack" in s["label"] for s in row["sightings"]), row["sightings"])
check("searching narrows it",
      domain_links.orphans(q="orphan")["count"] >= 1)
check("...and a search that matches nothing returns nothing, not everything",
      domain_links.orphans(q="zzzznothing")["count"] == 0)

_readers = domain_links._ORPHAN_READERS                             # noqa: SLF001
domain_links._ORPHAN_READERS = (                                    # noqa: SLF001
    ("knack", "Knack website registry (object_153)", _broken),)
broken = domain_links.orphans()
src = {s["source"]: s for s in broken["sources"]}
check("a source that could not be read is reported as not read",
      src["knack"]["ok"] is False, src)
check("...by name, with the reason", "Knack timed out" in src["knack"]["error"])
check("and the note says the total is a floor",
      "floor, not a total" in broken["note"], broken["note"])
domain_links._ORPHAN_READERS = _readers                             # noqa: SLF001


# ---------------------------------------------------------------------------
section("Domain purchases: only ours, by renewal billing date")
# ---------------------------------------------------------------------------
check("a Knack boolean reads as ours",
      domain_purchase.is_ours({"domain_bought_raw": True}))
check("a yes/no dropdown reads the same way",
      domain_purchase.is_ours({"domain_bought_raw": "Yes"}))
check("a no does not", not domain_purchase.is_ours({"domain_bought_raw": "No"}))
check("and neither does a blank — absent is not yes",
      not domain_purchase.is_ours({"domain_bought_raw": ""}))

check("a US date parses", domain_purchase.parse_date("08/14/2026")
      == date(2026, 8, 14))
check("an ISO one does too", domain_purchase.parse_date("2026-08-14")
      == date(2026, 8, 14))
check("something unreadable is None rather than today",
      domain_purchase.parse_date("sometime next year") is None)

window = domain_purchase.month_window(date(2026, 11, 20))
check("the window is this month and the next three",
      [w["label"] for w in window] ==
      ["November 2026", "December 2026", "January 2027", "February 2027"],
      [w["label"] for w in window])
check("...and it rolls over the year end rather than stopping at December",
      window[-1]["key"] == "2027-02", window[-1])
check("this month is marked as this month", window[0]["current"] is True)

rep = domain_purchase.report(today=date(2026, 8, 1))
listed = {r["domain"] for g in rep["groups"] for r in g["rows"]}
check("a domain we bought, renewing this month, is in the current month",
      "buckeyelakemarina.example" in listed, listed)
check("a domain nobody bought for a client is not listed at all",
      "orphaned.example" not in listed
      and not any(r["domain"] == "orphaned.example" for r in rep["undated"]),
      rep)
check("one we bought with no renewal billing date is not placed in a month",
      [r["domain"] for r in rep["undated"]] == ["riverstoneheating.example"],
      rep["undated"])
check("...and it is in no month at all rather than sorting to the top",
      rep["undated"][0]["month"] == ""
      and not any(r["domain"] == "riverstoneheating.example"
                  for g in rep["groups"] for r in g["rows"]),
      rep["undated"][0])
check("the table carries the columns the sheet asks for",
      all(k in rep["groups"][0]["rows"][0]
          for k in ("domain", "registrar", "client_status", "fee",
                    "renewal_billing_date")), rep["groups"][0]["rows"][0])
check("the fee comes across", rep["groups"][0]["rows"][0]["fee"] == 22.5)


# ---------------------------------------------------------------------------
section("Billed is a tick against a date, not against a record")
# ---------------------------------------------------------------------------
check("nothing is billed to begin with",
      rep["groups"][0]["rows"][0]["billed"] is False)
out = domain_purchase.set_billed("rec1", True, for_date="08/14/2026",
                                 actor="Todd")
check("ticking one is stored", out["ok"])
rep = domain_purchase.report(today=date(2026, 8, 1))
row = rep["groups"][0]["rows"][0]
check("and it reads back as billed", row["billed"] is True, row)
check("...saying who ticked it", "Todd" in row.get("note", ""), row)

# The renewal rolls to next year. The tick was for last year's charge.
WEB_ROWS[0]["renewal_billing_date"] = "08/14/2027"
rep = domain_purchase.report(today=date(2027, 8, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "buckeyelakemarina.example")
check("when the renewal date rolls the tick does not come with it",
      row["billed"] is False, row)
check("...and it says what it was billed for rather than losing the history",
      "08/14/2026" in row.get("note", ""), row)
WEB_ROWS[0]["renewal_billing_date"] = "08/14/2026"
check("unticking works too",
      domain_purchase.set_billed("rec1", False)["ok"]
      and not domain_purchase.billed_store().get("rec1"),
      domain_purchase.billed_store())


# ---------------------------------------------------------------------------
section("A QuickBooks line description is read, never guessed at")
# ---------------------------------------------------------------------------
# These are the real shapes on this company's invoices. The client is not the
# QuickBooks customer — one invoice to a media partner carries five renewals
# for five businesses — so the description is the only place the client
# appears, and it is typed by a person in whatever shape that day suggested.
from hub import domain_renewals                                     # noqa: E402

for text, want_domain, want_name in (
        (" syrons-market.com/\tSyrons", "syrons-market.com", "Syrons"),
        ("Foreman Mechanical Services, LLC - foremanmechanical.com",
         "foremanmechanical.com", "Foreman Mechanical Services, LLC"),
        ("www.topsdigitalmarketing.com\tTOPS Marketing",
         "topsdigitalmarketing.com", "TOPS Marketing"),
        ("morningskyestates.com/ Morning Sky Estates",
         "morningskyestates.com", "Morning Sky Estates"),
        ("The Exchange Club of Helena -   helenaexchangeclub.org/ ",
         "helenaexchangeclub.org", "The Exchange Club of Helena")):
    got = domain_renewals.parse_description(text)
    check(f"“{text.strip()[:34]}…” reads as {want_domain}",
          got["domain"] == want_domain and got["name"] == want_name, got)

check("a scheme and a trailing slash are noise, not part of the domain",
      domain_renewals.parse_description("http://friendsofbridges.org/ - Annual "
                                        "renewal")["domain"]
      == "friendsofbridges.org")
check("...and “Annual renewal” is a label, so it is not offered as a name",
      domain_renewals.parse_description("http://friendsofbridges.org/ - Annual "
                                        "renewal")["name"] == "")
check("a label with a year on it identifies nobody either",
      domain_renewals.parse_description("Annual renewal for 2026")["name"] == "")
check("a file host is not a website, so it is not read as one",
      domain_renewals.parse_description(
          "Renewal - see drive.google.com/file/x")["domain"] == "")
check("a description naming only a business still yields the business",
      domain_renewals.parse_description("Acme Plumbing")["name"] == "Acme Plumbing")

check("the item is matched on the leaf of its fully-qualified name",
      __import__("hub.quickbooks", fromlist=["x"]).line_item_matches(
          "Website Hosting:Website Domain Renewal", "143",
          item_name="Website Domain Renewal"))
check("...and an item id, where there is one, is exact and wins",
      __import__("hub.quickbooks", fromlist=["x"]).line_item_matches(
          "Something Else", "143", item_name="Website Domain Renewal",
          item_id="143"))
check("a different product on the same invoice is not a domain renewal",
      not __import__("hub.quickbooks", fromlist=["x"]).line_item_matches(
          "Video Advertising:Video Ads YouTube TrueView", "98",
          item_name="Website Domain Renewal"))


# ---------------------------------------------------------------------------
section("A charge matches a domain exactly, or it is a suggestion")
# ---------------------------------------------------------------------------
def _line(iid, lid, date_, desc, amount=24.99, customer="The Montana Radio Group"):
    return {"invoice_id": iid, "line_id": lid, "doc_number": "TSN-" + iid,
            "date": date_, "customer": customer, "customer_id": "7",
            "description": desc, "amount": amount, "item_id": "143",
            "item_name": "Website Hosting:Website Domain Renewal",
            "link": "https://app.qbo.intuit.com/app/invoice?txnId=" + iid}


QB_LINES = [
    _line("1001", "2", "2026-08-20",
          "Buckeye Lake Marina -  buckeyelakemarina.example"),
    _line("1002", "2", "2026-03-11", "Riverstone Heat Co - annual renewal"),
    _line("1003", "2", "2026-05-02",
          "Wholly Unknown Business LLC - nobodyhasthis.example"),
    _line("1004", "2", "2026-06-02", "Annual renewal"),
]
matched = domain_renewals.match_charges(QB_LINES, WEB_ROWS)
by_key = {c["key"]: c for c in matched}

hit = by_key["1001:2"]
check("the domain in the description is the join key",
      hit["record_id"] == "rec1" and hit["matched_on"] == "domain", hit)
check("...and that is exact, not a suggestion", hit["confidence"] == "exact")
check("the record's media partner comes back with it", "partner" in hit)

near = by_key["1002:2"]
check("a near name is offered, and only as probable",
      near["record_id"] == "rec3" and near["confidence"] == "probable", near)
check("...saying so in words a person can act on",
      "confirm" in near["why"].lower(), near["why"])

miss = by_key["1003:2"]
check("a domain nothing here carries matches nobody",
      miss["record_id"] == "" and miss["confidence"] == "unmatched", miss)
check("...and it says what it read rather than only that it failed",
      miss["parsed"]["domain"] == "nobodyhasthis.example", miss["parsed"])

blank = by_key["1004:2"]
check("a description naming neither a domain nor a business says exactly that",
      blank["record_id"] == "" and "neither" in blank["why"], blank)

# A person's confirmation is the only fact in the matcher, and it outranks
# every rule below it — including the near-name suggestion.
check("a charge can be attached to a record by hand",
      domain_renewals.link_charge("1003:2", "rec1", actor="Todd",
                                  domain="buckeyelakemarina.example")["ok"])
relinked = {c["key"]: c for c in domain_renewals.match_charges(QB_LINES, WEB_ROWS)}
check("...and the confirmation wins over the parser",
      relinked["1003:2"]["record_id"] == "rec1"
      and relinked["1003:2"]["confidence"] == "confirmed", relinked["1003:2"])
check("...naming who confirmed it", "Todd" in relinked["1003:2"]["why"])
check("clearing it stores no blank match",
      domain_renewals.link_charge("1003:2", "")["ok"]
      and "1003:2" not in domain_renewals.links_store(),
      domain_renewals.links_store())


# ---------------------------------------------------------------------------
section("Billed is read from QuickBooks, and says so")
# ---------------------------------------------------------------------------
_real_charges = domain_renewals.charges


def _stub_charges(lines, error=""):
    def _c(year=None, *, refresh=False, ttl=0):
        return {"lines": list(lines), "error": error, "fetched_at":
                "" if error else "2026-08-26T09:00:00+00:00",
                "age_hours": None, "cached": False,
                "item": "Website Domain Renewal"}
    return _c


domain_renewals.charges = _stub_charges(QB_LINES)
rep = domain_purchase.report(today=date(2026, 8, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "buckeyelakemarina.example")
check("an invoice line marks the renewal billed",
      row["billed"] is True and row["billed_source"] == "quickbooks", row)
check("...and the row carries the invoice rather than only a tick",
      row["charges"] and row["charges"][0]["doc_number"] == "TSN-1001",
      row["charges"])
check("...saying which invoice, when and for how much",
      "TSN-1001" in row["note"] and "24.99" in row["note"], row["note"])
check("the media partner is beside the domain",
      "partner" in row, row)

# A domain renews every year and is invoiced once. Without the window, last
# year's charge marks this year's renewal billed.
domain_renewals.charges = _stub_charges([
    _line("900", "2", "2026-01-06",
          "Buckeye Lake Marina -  buckeyelakemarina.example")])
rep = domain_purchase.report(today=date(2026, 8, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "buckeyelakemarina.example")
check("a charge seven months from the renewal does not bill it",
      row["billed"] is False, row)
check("...though it is still counted against the record for the year",
      row["charges_year"] == 1, row)

# A near name is a suggestion. A suggestion that ticks a box is a fact nobody
# agreed to.
WEB_ROWS[2]["renewal_billing_date"] = "03/03/2026"
domain_renewals.charges = _stub_charges([
    _line("1002", "2", "2026-03-11", "Riverstone Heat Co - annual renewal")])
rep = domain_purchase.report(today=date(2026, 3, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "riverstoneheating.example")
check("a probable match does not mark a renewal billed",
      row["billed"] is False, row)
check("...but it is shown, named as needing confirming",
      row["maybe_charges"] and "not counted as billed" in row["note"], row)
WEB_ROWS[2]["renewal_billing_date"] = ""

# A QuickBooks that could not be read must never produce a finding.
domain_renewals.charges = _stub_charges([], error="QuickBooks is not connected.")
domain_purchase.set_billed("rec1", True, for_date="08/14/2026", actor="Todd")
rep = domain_purchase.report(today=date(2026, 8, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "buckeyelakemarina.example")
check("a failed read is not reported as “no charge matches”",
      "could not be read" in row["note"] and "No Website Domain Renewal charge"
      not in row["note"], row["note"])
check("...and the page says the same thing at the top",
      "not connected" in rep["quickbooks_note"], rep["quickbooks_note"])
domain_purchase.set_billed("rec1", False)


# ---------------------------------------------------------------------------
section("This month asks whether it was billed; later months ask whether to renew")
# ---------------------------------------------------------------------------
domain_renewals.charges = _stub_charges(QB_LINES)
rep = domain_purchase.report(today=date(2026, 8, 1))
check("the current month carries the billed column",
      rep["groups"][0]["current"] and rep["groups"][0]["column"] == "billed",
      rep["groups"][0]["column"])
check("...and every later month carries do-not-renew instead",
      all(g["column"] == "do_not_renew" for g in rep["groups"][1:]),
      [g["column"] for g in rep["groups"]])

out = domain_purchase.set_do_not_renew("rec1", True, for_date="08/14/2026",
                                       reason="Client closed the shop",
                                       actor="Todd")
check("marking one is stored", out["ok"])
rep = domain_purchase.report(today=date(2026, 8, 1))
row = next(r for g in rep["groups"] for r in g["rows"]
           if r["domain"] == "buckeyelakemarina.example")
check("and it reads back on the row", row["do_not_renew"] is True, row)
check("...with the reason, so nobody has to ask again",
      "closed the shop" in row["dnr_note"], row["dnr_note"])

dnr = domain_purchase.do_not_renew_report(today=date(2026, 8, 1))
check("the do-not-renew report lists it as still to cancel",
      [r["domain"] for r in dnr["standing"]] == ["buckeyelakemarina.example"],
      dnr["standing"])
check("...and nothing has renewed against a mark yet", dnr["renewed_count"] == 0)

# Unlike the billed tick, this one is never retired when the date rolls: the
# domain renewed after somebody said it should not, and clearing the mark
# would delete the only evidence of that.
WEB_ROWS[0]["renewal_billing_date"] = "08/14/2027"
dnr = domain_purchase.do_not_renew_report(today=date(2027, 1, 1))
check("a renewal date that moved on means it renewed anyway",
      [r["domain"] for r in dnr["renewed_anyway"]]
      == ["buckeyelakemarina.example"], dnr)
check("...and that is a separate list, not a quiet unticking",
      dnr["standing_count"] == 0 and dnr["renewed_count"] == 1, dnr)
check("...saying both dates rather than only the new one",
      "08/14/2026" in dnr["renewed_anyway"][0]["dnr_note"]
      and "08/14/2027" in dnr["renewed_anyway"][0]["dnr_note"],
      dnr["renewed_anyway"][0]["dnr_note"])
WEB_ROWS[0]["renewal_billing_date"] = "08/14/2026"
domain_purchase.set_do_not_renew("rec1", False)
check("clearing the mark works too",
      not domain_purchase.dnr_store().get("rec1"),
      domain_purchase.dnr_store())


# ---------------------------------------------------------------------------
section("Year to date, in both directions")
# ---------------------------------------------------------------------------
domain_renewals.charges = _stub_charges(QB_LINES)
ytd = domain_purchase.year_to_date(year=2026, today=date(2026, 8, 26))
check("a renewal with an invoice behind it is reconciled",
      ytd["reconciled_count"] == 1, ytd["reconciled_count"])
check("...and it is not also counted as unbilled",
      not any(r["domain"] == "buckeyelakemarina.example"
              for r in ytd["not_billed"]), ytd["not_billed"])
check("a charge nothing here carries is reported as billed with no record",
      any(c["parsed_domain"] == "nobodyhasthis.example"
          for c in ytd["unrecorded"]), ytd["unrecorded"])
check("...with the line description, so a person can see what it says",
      all("description" in c for c in ytd["unrecorded"]))
check("a suggested owner is counted apart from a blank one",
      ytd["suggested_count"] >= 1, ytd)
check("every purchased domain travels with it, so a charge can be attached",
      any(r["record_id"] == "rec1" for r in ytd["records"]), ytd["records"])
check("the value of the unrecorded charges is totalled",
      ytd["unrecorded_total"] > 0, ytd["unrecorded_total"])

# A renewal that came due with no charge behind it is the money question.
WEB_ROWS[2]["renewal_billing_date"] = "03/03/2026"
domain_renewals.charges = _stub_charges([QB_LINES[0]])
ytd = domain_purchase.year_to_date(year=2026, today=date(2026, 8, 26))
check("a renewal due this year with no invoice is named",
      [r["domain"] for r in ytd["not_billed"]] == ["riverstoneheating.example"],
      ytd["not_billed"])
check("...and the fees at risk are added up",
      ytd["not_billed_fees"] == 18.0, ytd["not_billed_fees"])

# Not billing a domain nobody is renewing is correct, not a finding.
domain_purchase.set_do_not_renew("rec3", True, for_date="03/03/2026",
                                 actor="Todd")
ytd = domain_purchase.year_to_date(year=2026, today=date(2026, 8, 26))
check("a domain marked do-not-renew is not counted as an unbilled renewal",
      ytd["not_billed_count"] == 0 and ytd["not_renewing_count"] == 1, ytd)
domain_purchase.set_do_not_renew("rec3", False)
WEB_ROWS[2]["renewal_billing_date"] = ""

# Neither side is presented as a total when either failed to read.
domain_renewals.charges = _stub_charges([], error="QuickBooks is not connected.")
ytd = domain_purchase.year_to_date(year=2026, today=date(2026, 8, 26))
check("a QuickBooks that would not answer is not a year of unbilled renewals",
      ytd["measured"] is False and any("not connected" in p
                                       for p in ytd["problems"]), ytd["problems"])


# ---------------------------------------------------------------------------
section("The renewal standing rides on the Client 360 domain record")
# ---------------------------------------------------------------------------
domain_renewals.charges = _stub_charges(QB_LINES)
st = domain_purchase.status_for_record("rec1", today=date(2026, 8, 26))
check("a domain we bought carries its renewal standing",
      st["applies"] is True and st["billed"] is True, st)
check("...with the invoice that says so",
      st["charges"] and st["charges"][0]["doc_number"] == "TSN-1001", st)
check("...and the media partner beside it", "partner" in st, st)
check("a domain we did not buy is not “not billed” — it does not apply",
      domain_purchase.status_for_record("rec2")["applies"] is False,
      domain_purchase.status_for_record("rec2"))
check("...and says why rather than showing an empty panel",
      "did not buy" in domain_purchase.status_for_record("rec2")["reason"],
      domain_purchase.status_for_record("rec2"))
check("a record with no renewal billing date is not measured, never unbilled",
      domain_purchase.status_for_record("rec3")["dated"] is False
      and "not_measured" in domain_purchase.status_for_record("rec3"),
      domain_purchase.status_for_record("rec3"))

domain_renewals.charges = _real_charges


# ---------------------------------------------------------------------------
section("The routes exist under the hub app, not a mount")
# ---------------------------------------------------------------------------
# CLAUDE.md's first trap: a hub route written under a mounted prefix is never
# reached. /tools/domains sits under /tools, which is not itself a mount.
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})
    for path in ("/tools/domains", "/api/domains/purchased", "/api/orphan-urls",
                 "/api/domains/ytd", "/api/domains/do-not-renew",
                 "/api/client/website-record?domain=example.com"):
        check(f"{path} answers", composed.get(path).status_code == 200)
    check("attach refuses a bad URL through the route too",
          composed.post("/api/domain/attach",
                        json={"domain": "nope", "client": "X"}
                        ).get_json()["ok"] is False)
    check("and refuses a write to a record it was not given",
          composed.post("/api/client/website-record/save",
                        json={"record_id": "", "values": {"live_date": "x"}}
                        ).get_json()["ok"] is False)
except Exception as exc:                                        # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
section("Sites Admin can name the customer, not only find the orphan domain")
# ---------------------------------------------------------------------------
# The domain cell on the Sites Admin table is a pair of halves, and only one
# of them was built. A project WITH a client could search orphan domains; a
# project WITHOUT one — which is the far more common row, and the one somebody
# opens the page to fix — got "there is no client to attach a domain to yet …
# use Match clients in the Hub" and stopped. A row that reports a problem
# beside a control that refuses to fix it is not a control.
DASH = os.path.join(ROOT, "modules", "sites_admin", "templates",
                    "dashboard.html")
dash = open(DASH, encoding="utf-8").read()

check("a project with no client is offered a customer search, not a dead end",
      "customerBox(" in dash and "use Match clients in the Hub" not in dash,
      "the dead-end message is still there" if "use Match clients in the Hub"
      in dash else "customerBox is missing")
check("...against the real customer list, never a free-text box",
      "/api/clients/search" in dash, dash.count("/api/clients/search"))
check("the row says which half it is offering",
      "match to a customer" in dash and "search orphan domains" in dash)
check("the project's own domain travels with the row",
      'data-domain="{{r.domain' in dash)
check("a project with neither a client nor a domain says so",
      "This project has no client and no " in dash)
check("both halves attach through the one path that writes four systems",
      dash.count("fetch('/api/domain/attach'") == 1
      and "function attachDomain(" in dash,
      dash.count("fetch('/api/domain/attach'"))
check("...and both report every system rather than one tick",
      "rep.written" in dash and "rep.skipped" in dash)

# The 500 that made all of this invisible: a form in project_detail.html
# posting to an endpoint that did not exist. Flask raises BuildError while
# rendering, so it was not a broken button — it was the whole page, on every
# project, every time.
APPPY = open(os.path.join(ROOT, "modules", "sites_admin", "app.py"),
             encoding="utf-8").read()
check("the Check plan limits form has a route behind it",
      "def website_check_limits(" in APPPY)
check("...and it calls the client method that had no caller at all",
      "client.check_limits(" in APPPY)
check("an answer that is neither pass nor fail is not shown as a pass",
      "not in a shape this page can read" in APPPY)
CSS = open(os.path.join(ROOT, "modules", "sites_admin", "static", "styles.css"),
           encoding="utf-8").read()
check("...and that flash category has a colour of its own, not red by default",
      ".flash.warning{" in CSS)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
