"""A scan of a business we don't bill becomes a lead.

    python3 test_scan_lead.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. Nothing in it reaches the network: Smart 1 Suite
delivery is unconfigured, which `hub/leads.py` answers with "none" and no
call.

## What this holds

`modules/scans` filed a lead for exactly one of the three ways a scan starts:
the widget, where a stranger types their own name and email on a client's
website. A rep scanning a business from /scans — which is most of them —
wrote a row in the scans table and told nothing else in the Hub. The leads
panel did not have it, `hub/prospect_queue.py` could not rank it, and
`hub/prospect.py`, which exists to be the record a scanned business gets
before it is a client and hangs off a **lead id**, had nothing to open.

`modules/scans/prospect_leads.py` closes that, and every assertion below is
one of the ways closing it could go wrong instead:

  1. **Filing the client list as leads.** The check is "is this a client",
     and an empty or unreadable client registry answers "no" to that question
     for every client in the business at once. So "we could not look" is a
     third answer, it is written on the scan, and nothing is filed.

  2. **Two rows, one business.** Band 2 of the prospect queue is somebody's
     morning spent working the row nobody else is looking at. A website that
     is already a lead is linked to, not filed again — and a lead store that
     could not be read is not evidence that there is no lead.

  3. **A lead nobody can contact.** It reads as a live prospect on every
     count that follows it and can never leave the "needs attention" pile,
     because `hub/ghl_contacts.upsert()` has nothing to match a contact on.
     Refused by name, with the one way back: a rep types a number in.

  4. **Filing it twice.** A late callback, the refresh button and the
     scheduler's sweep over stuck rows all land in `_apply_report()`.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1scanlead_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
# Both pinned. `HUB_LEADS_FILE` names the legacy file and selects no backend;
# an unpinned DATABASE_URL is how a test run reads whatever database the
# environment happens to point at, which on a shared one is the run before it.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["HUB_LEADS_FILE"] = os.path.join(DISK, "leads.jsonl")
os.environ["SECRET_KEY"] = "scan-lead-test-secret"
os.environ["PANEL_PASSWORD"] = "scan-lead-test-password"
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
os.environ["SCANS_CALLBACK_TOKEN"] = "scan-lead-test-token"
# Nothing is sent anywhere: with no Suite credentials `delivery_mode()` is
# "none", which stores the lead and queues delivery without a request.
for _k in ("GHL_PRIVATE_TOKEN", "GHL_LEAD_LOCATION_ID", "HUB_LEAD_WEBHOOK_URL"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import client_key, lead_tags, leads as hub_leads      # noqa: E402
from hub import prospect as hub_prospect                       # noqa: E402
from modules.scans import app as scans_app                     # noqa: E402
from modules.scans import prospect_leads                       # noqa: E402

client = scans_app.app.test_client()
Session = scans_app.SessionLocal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def index_of(*clients) -> dict:
    """A client registry index in the shape `hub/client_key.alias_index()`
    really returns, so `resolve()` is exercised rather than stubbed."""
    by_domain, by_name, entries = {}, {}, {}
    for name, domain in clients:
        key = f"d:{domain}" if domain else client_key.name_key(name)
        entry = {"key": key, "name": name, "domain": domain, "source": "test",
                 "is_house": False, "products": 1, "names": [name]}
        entries[key] = entry
        if domain:
            by_domain[domain] = entry
        by_name[client_key.normalise_name(name)] = entry
    return {"by_domain": by_domain, "by_name": by_name, "entries": entries,
            "domain_conflicts": {}, "clients": len(entries), "error": "",
            "built_at": "2026-09-16T00:00:00+00:00"}


REGISTRY = index_of(("Riverside HVAC", "riverside-hvac.com"))
client_key.alias_index = lambda refresh=False: REGISTRY


def report(*, domain="prospect-co.test", phone="(555) 555-0100", score=38):
    """The shape `audit_fields.summarise()` and `site_health.fixes()` read."""
    return {
        "domain": domain,
        "overall_score": score,
        "report_id": "insites-report-1",
        "meta": {"detected_name": "Prospect Co", "detected_phone": phone,
                 "detected_address": "1 Main Street, Springfield",
                 "primary_industry": "HVAC", "analysis_country": "US"},
        "analysed_page_count": 12,
        "broken_links": {"links_broken_count": 7},
        "alternative_text": {"images_no_alt_count": 12},
        "image_optimisation": {"images_to_optimise_count": 9},
    }


def new_scan(domain, *, name="", source="hub", public_id=None) -> str:
    db = Session()
    try:
        pid = public_id or scans_app._new_public_id()
        db.add(scans_app.Scan(public_id=pid, domain_key=domain,
                              input_url=f"https://{domain}", business_name=name,
                              source=source, requested_by="A Rep",
                              status="running", created_at=scans_app._now()))
        db.commit()
        return pid
    finally:
        db.close()


def complete(public_id, payload=None):
    """Land the audit the way a callback does, and hand back the row."""
    db = Session()
    try:
        s = db.query(scans_app.Scan).filter(
            scans_app.Scan.public_id == public_id).first()
        scans_app._apply_report(s, payload or report())
        db.commit()
        db.refresh(s)
        return scans_app.scan_to_row(s)
    finally:
        db.close()


def lead_rows(source=prospect_leads.SOURCE):
    return [r for r in hub_leads._read_all() if r.get("source") == source]


# =====================================================================
section("A scanned business that is not a client becomes a lead")
# =====================================================================

pid = new_scan("prospect-co.test", name="Prospect Co")
row = complete(pid)

check("the scan is filed as a lead", row["lead_state"], "filed")
check("...and carries the lead id, so the record can be opened",
      bool(row["lead_id"]), True)
check("...at the prospect record, not the panel",
      row["lead_url"], f"/prospect/{row['lead_id']}")

filed = [r for r in lead_rows() if r["id"] == row["lead_id"]]
check("exactly one lead was written", len(filed), 1)
lead = filed[0]
check("the contact is the phone number the audit read off the site",
      lead["phone"], "(555) 555-0100")
check("the business name rides with it", lead["company"], "Prospect Co")
check("the website is on the lead", (lead["fields"] or {}).get("website"),
      "prospect-co.test")
check("so is the score a rep opens it for",
      (lead["fields"] or {}).get("audit_score"), "38")
check("and the tier", (lead["fields"] or {}).get("audit_tier"), "Competitive")
check("the worst findings are named, in the detail page's own words",
      (lead["fields"] or {}).get("top_issues"),
      "Images with no alt text (12); Oversized images (9); Broken links (7)")
check("the scan it came from is on the row",
      (lead["meta"] or {}).get("scan_public_id"), pid)

# The join the prospect queue and the prospect record both make. A lead whose
# domain cannot be read off it is a prospect with no audit attached, which is
# the band this whole path exists to fill.
check("the lead's domain resolves, so the audit joins to it",
      hub_prospect._lead_domain(lead), "prospect-co.test")
check("the leads panel has it",
      any(r["id"] == lead["id"] for r in hub_leads.listing(days=1)["leads"]),
      True)

# Nothing is sent anywhere without Suite credentials, and the lead is stored
# either way -- the order hub/leads.py opens by stating.
check("delivery is queued rather than claimed", lead["delivered"], False)

section("The source tag is one the Suite vocabulary knows")
check("site_scan is declared in hub/lead_tags.py",
      prospect_leads.SOURCE in lead_tags.SOURCES, True)
check("...and says what it is",
      len(lead_tags.SOURCES[prospect_leads.SOURCE]["what"]) > 10, True)
check("...with no workflow claimed behind it",
      lead_tags.backed(prospect_leads.SOURCE), False)


# =====================================================================
section("A client is not a lead")
# =====================================================================

pid = new_scan("riverside-hvac.com", name="Riverside HVAC")
row = complete(pid, report(domain="riverside-hvac.com"))

check("a client's own scan files no lead", row["lead_state"], "client")
check("...and no id is invented for it", row["lead_id"], "")
check("...the note names which client it matched",
      "Riverside HVAC" in row["lead_note"], True)
check("nothing reached the lead store for it",
      [r for r in lead_rows() if (r.get("meta") or {}).get("scan_public_id") == pid],
      [])

# The match is on the domain, so a client scanned under a name nobody typed
# the same way is still a client.
pid = new_scan("riverside-hvac.com", name="", public_id=None)
row = complete(pid, report(domain="riverside-hvac.com"))
check("matched on the domain, with no business name typed at all",
      row["lead_state"], "client")


# =====================================================================
section("'We could not look' is not 'they are not a client'")
# =====================================================================

for label, broken in (("a registry that reported an error",
                       {**REGISTRY, "error": "Knack: 502"}),
                      ("a registry that came back with nobody in it",
                       index_of())):
    client_key.alias_index = lambda refresh=False, _b=broken: _b
    pid = new_scan("unknown-co.test", name="Unknown Co")
    row = complete(pid, report(domain="unknown-co.test"))
    check(f"{label} files nothing", row["lead_state"], "undecided")
    check("...and says so on the scan rather than filing a lead",
          bool(row["lead_note"]), True)
    check("...with nothing written to the lead store",
          [r for r in lead_rows()
           if (r.get("meta") or {}).get("scan_public_id") == pid], [])

client_key.alias_index = lambda refresh=False: REGISTRY

# Undecided is revisited: the same scan asked again, with the registry
# answering, files the lead. This is what the refresh button and the
# scheduler's sweep are for.
db = Session()
try:
    s = db.query(scans_app.Scan).filter(scans_app.Scan.public_id == pid).first()
    again = prospect_leads.ensure(s, report=report(domain="unknown-co.test"))
    db.commit()
finally:
    db.close()
check("asking again once the registry answers files it", again["state"], "filed")


# =====================================================================
section("One business, one row")
# =====================================================================

first = new_scan("prospect-co.test", name="Prospect Co")
row = complete(first)
check("a second scan of a website that is already a lead links to it",
      row["lead_state"], "linked")
check("...pointing at the row that exists", row["lead_id"], lead["id"])
check("...and files no second one",
      len([r for r in lead_rows()
           if (r.get("fields") or {}).get("website") == "prospect-co.test"]), 1)

# A store that will not answer is not evidence that there is no lead for this
# website, so nothing is filed rather than a possible duplicate.
_real_find = hub_leads.find_by_domain


def _broken_find(domain):
    raise OSError("lead store unreadable")


hub_leads.find_by_domain = _broken_find
pid = new_scan("another-co.test", name="Another Co")
row = complete(pid, report(domain="another-co.test"))
hub_leads.find_by_domain = _real_find
check("a lead store that cannot be read files nothing", row["lead_state"],
      "undecided")
check("...rather than risking a second row for the website",
      [r for r in lead_rows()
       if (r.get("fields") or {}).get("website") == "another-co.test"], [])

section("find_by_domain answers the question it is asked")
check("a website with a lead comes back",
      (hub_leads.find_by_domain("https://prospect-co.test/contact") or {}).get("id"),
      lead["id"])
check("a website with none comes back None",
      hub_leads.find_by_domain("nobody-here.test"), None)
try:
    _raised = False
    _real_read = hub_leads._read_all

    def _boom(*a, **kw):
        raise OSError("no store")

    hub_leads._read_all = _boom
    try:
        hub_leads.find_by_domain("prospect-co.test")
    except OSError:
        _raised = True
    finally:
        hub_leads._read_all = _real_read
finally:
    pass
check("a store that cannot be read raises rather than answering 'none'",
      _raised, True)


# =====================================================================
section("A lead nobody can contact is refused by name")
# =====================================================================

pid = new_scan("silent-co.test", name="Silent Co")
row = complete(pid, report(domain="silent-co.test", phone=""))
check("no phone and no email means no lead", row["lead_state"], "no_contact")
check("...and the note says what to do about it",
      "email" in row["lead_note"].lower(), True)
check("...with nothing in the store",
      [r for r in lead_rows()
       if (r.get("fields") or {}).get("website") == "silent-co.test"], [])

r = client.post(f"/api/scans/{pid}/lead", json={"email": "owner@silent-co.test",
                                                "name": "Sam Owner"})
body = r.get_json()
check("a rep typing a contact in files it", r.status_code, 200)
check("...and is sent to the record, not the panel",
      body["record_url"], f"/prospect/{body['lead_id']}")
typed = [x for x in lead_rows() if x["id"] == body["lead_id"]][0]
check("the typed email is what it was filed on", typed["email"],
      "owner@silent-co.test")
check("...and the typed name with it", typed["name"], "Sam Owner")

r = client.post(f"/api/scans/{pid}/lead", json={"email": "someone@else.test"})
check("filing the same scan again returns the row it already has",
      r.get_json()["lead_id"], body["lead_id"])
check("...and does not write a second lead",
      len([x for x in lead_rows()
           if (x.get("fields") or {}).get("website") == "silent-co.test"]), 1)

# Refusing by name means refusing: an empty post on a scan with nothing
# detected must not create the contactless row the rule exists to stop.
pid = new_scan("quiet-co.test", name="Quiet Co")
complete(pid, report(domain="quiet-co.test", phone=""))
r = client.post(f"/api/scans/{pid}/lead", json={})
check("an empty submission is refused", r.status_code, 422)
check("...and files nothing",
      [x for x in lead_rows()
       if (x.get("fields") or {}).get("website") == "quiet-co.test"], [])


# =====================================================================
section("What the by-hand route will not do")
# =====================================================================

pid = new_scan("riverside-hvac.com", name="Riverside HVAC")
complete(pid, report(domain="riverside-hvac.com"))
r = client.post(f"/api/scans/{pid}/lead", json={"email": "a@b.test"})
check("a client is not filed as a lead from this button", r.status_code, 409)
check("...and the refusal says which client it is",
      "Riverside HVAC" in (r.get_json().get("error") or ""), True)

r = client.post("/api/scans/does-not-exist/lead", json={})
check("an unknown scan is a 404, not a lead", r.status_code, 404)


# =====================================================================
section("The widget's own lead is not filed twice")
# =====================================================================

pid = new_scan("widget-co.test", name="Widget Co", source="widget")
row = complete(pid, report(domain="widget-co.test"))
check("a widget scan leaves the lead to the widget", row["lead_state"],
      "widget")
check("...and files nothing of its own",
      [r for r in lead_rows()
       if (r.get("fields") or {}).get("website") == "widget-co.test"], [])


# =====================================================================
section("Filing is idempotent, because three paths land in _apply_report")
# =====================================================================

pid = new_scan("second-look.test", name="Second Look")
row = complete(pid, report(domain="second-look.test"))
check("the first look files it", row["lead_state"], "filed")
before = row["lead_id"]
# A late callback, then the refresh button, then the scheduler's sweep.
for _ in range(3):
    row = complete(pid, report(domain="second-look.test"))
check("every later look keeps the id it already had", row["lead_id"], before)
check("...and never a second row",
      len([r for r in lead_rows()
           if (r.get("fields") or {}).get("website") == "second-look.test"]), 1)


# =====================================================================
section("A scan is complete whatever the lead does")
# =====================================================================

_real_ensure = prospect_leads.ensure


def _explode(*a, **kw):
    raise RuntimeError("prospect_leads is broken")


prospect_leads.ensure = _explode
pid = new_scan("resilient-co.test", name="Resilient Co")
row = complete(pid, report(domain="resilient-co.test"))
prospect_leads.ensure = _real_ensure
check("a lead that cannot be filed does not cost the audit", row["status"],
      "complete")
check("...and the score is stored", row["score"], 38)


# =====================================================================
section("The temperature tag has something to tag at last")
# =====================================================================

# `_tag_lead_temperature()` has scored every completed audit since WO-3d and
# written the result onto `Scan.lead_id` -- a column no staff scan ever
# filled in, so every staff scan scored itself and wrote it nowhere.
tagged = {}
_real_tag = scans_app._tag_lead_temperature


def _spy(s, rep):
    tagged["lead_id"] = getattr(s, "lead_id", "")
    return _real_tag(s, rep)


scans_app._tag_lead_temperature = _spy
pid = new_scan("temperature-co.test", name="Temperature Co")
row = complete(pid, report(domain="temperature-co.test"))
scans_app._tag_lead_temperature = _real_tag
check("the lead is filed before the audit is scored",
      tagged.get("lead_id"), row["lead_id"])
check("...which is a real id", bool(row["lead_id"]), True)
stored = hub_leads.get(row["lead_id"]) or {}
check("...so the temperature lands on the lead",
      (stored.get("meta") or {}).get("scan_temperature") in ("hot", "warm", "cold"),
      True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
