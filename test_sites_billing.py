"""hub/sites_billing.py and the QA Sites Billing Report — test harness.

    python3 test_sites_billing.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. QuickBooks, Sites Admin and the client registry are
all injected as fixtures, so this needs no connected company, no Simvoly
credentials and reaches no third party.

## What is worth asserting

This report joins a **typed invoice description** to a **website**, which is
the loosest join in the Hub and the one with the most expensive wrong answers.
Every assertion below is a way it could be confidently wrong:

  * **A product name that matches no QuickBooks item empties the report.**
    Rename "Website Maintenance" in QuickBooks and every site on the book reads
    as unbilled — a clean, complete, entirely wrong table. The catalogue is
    read first and the report says *not measured* rather than reporting that
    nothing is billed. This is the single most important check in the file,
    because the failure has no other symptom.

  * **A product that merely resembles one of the three is named, not counted.**
    "Monthly Web Hosting - Annual" is probably hosting and is not one of the
    three. Matching it is the substring rule `hub/client_key.py` refuses;
    dropping it silently loses a tier of revenue from a report that looks
    complete.

  * **A name matches exactly or not at all.** "Riverside HVAC" must not collect
    "Riverside HVAC Supply", and a name answering to two clients answers for
    neither.

  * **A resemblance is printed and still counted as unmatched.** A fuzzy hit
    folded into the totals is a fact nobody re-examines.

  * **An email address is not a website.** "billing@acme.com" contains the
    string "acme.com", and joining on it attaches a hosting charge to a site on
    the strength of somebody's mailbox. Cloudinary, Google Drive and the
    Simvoly platform domains are rejected for the same reason.

  * **Lapsed is not unbilled, and stopped is not overbilled.** A live site
    billed eight months ago and one never billed are different findings; an
    inactive site whose billing also stopped is the system working, and
    flagging it buries the ones still being charged.

  * **"We could not look" is not "all clear."** Sites Admin unreadable,
    QuickBooks unreadable and the products missing all render as *Not measured*
    rather than as a green tick.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-sitesbilling-")
# Set, not setdefault: this file always gets its own throwaway mirror, so it is
# safe to re-run in a job whose DATABASE_URL is already a real Postgres.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "activity.jsonl")
os.environ.setdefault("SECRET_KEY", "sites-billing-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  — {detail}" if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * len(title))


import datetime as _dt                                          # noqa: E402

from hub import sites_billing as sb                             # noqa: E402

TODAY = _dt.date(2026, 8, 26)


def _ym(months_back):
    d = TODAY.replace(day=1)
    for _ in range(months_back):
        d = (d - _dt.timedelta(days=1)).replace(day=1)
    return d.replace(day=15).isoformat()


def project(pid, name, domain="", client="", status="ACTIVE", lifecycle=""):
    return {"project_id": pid, "name": name, "domain": domain,
            "internal_client_name": client, "status": status,
            "lifecycle_state": lifecycle}


def line(item, customer, description="", date=None, amount=75.0,
         invoice_text=""):
    return {"invoice_id": "i" + str(abs(hash((item, customer, description))) % 9999),
            "doc_number": "1001", "date": date or _ym(0),
            "customer_id": "c1", "customer": customer, "item_id": "1",
            "item": item, "description": description,
            "invoice_text": invoice_text, "amount": amount, "qty": 1,
            "link": "https://qbo.intuit.com/app/invoice?txnId=1"}


ITEMS = [{"name": p} for p in sb.HOSTING_PRODUCTS] + [{"name": "SEO Retainer"}]


# ---------------------------------------------------------------------------
section("The three products, and only the three")
# ---------------------------------------------------------------------------
check("the three products are the ones asked for",
      sb.HOSTING_PRODUCTS == ("Monthly Web Hosting",
                              "Monthly Website Hosting & Maintenance",
                              "Website Maintenance"),
      sb.HOSTING_PRODUCTS)
check("an exact name matches",
      sb.is_hosting_item("Monthly Web Hosting") == "Monthly Web Hosting")
check("& and 'and' are the same word",
      sb.is_hosting_item("Monthly Website Hosting and Maintenance")
      == "Monthly Website Hosting & Maintenance")
check("case and punctuation are not distinctions",
      sb.is_hosting_item("  website   maintenance ") == "Website Maintenance")
check("a QuickBooks category prefix is stripped",
      sb.is_hosting_item("Services:Website Maintenance") == "Website Maintenance")
check("a product that merely CONTAINS one of the three does not match",
      sb.is_hosting_item("Monthly Web Hosting - Annual") == "")
check("an unrelated product does not match",
      sb.is_hosting_item("SEO Retainer") == "")

cat = sb.catalogue_check(ITEMS)
check("all three found in a healthy catalog", cat["missing"] == [], cat)
cat2 = sb.catalogue_check([{"name": "Monthly Web Hosting"},
                           {"name": "Monthly Web Hosting - Annual"},
                           {"name": "Website Care Plan"}])
check("a product QuickBooks no longer carries is named",
      cat2["missing"] == ["Monthly Website Hosting & Maintenance",
                          "Website Maintenance"], cat2)
check("a resembling product is named rather than counted",
      cat2["similar"] == ["Monthly Web Hosting - Annual"], cat2)
check("a product resembling nothing is not named as similar",
      "Website Care Plan" not in cat2["similar"], cat2)


# ---------------------------------------------------------------------------
section("Reading a domain out of a typed description")
# ---------------------------------------------------------------------------
check("a bare domain is read",
      sb.domains_in("Monthly hosting acmeplumbing.com") == ["acmeplumbing.com"])
check("protocol, www, path and case are normalized away — and a file in the "
      "path is not a second domain",
      sb.domains_in("HTTPS://WWW.AcmePlumbing.com/index.html")
      == ["acmeplumbing.com"],
      sb.domains_in("HTTPS://WWW.AcmePlumbing.com/index.html"))
check("a bare file name is not a domain",
      sb.domains_in("see invoice.pdf and logo.png") == [],
      sb.domains_in("see invoice.pdf and logo.png"))
check("an email address is not a website",
      sb.domains_in("invoice to billing@acmeplumbing.com") == [],
      sb.domains_in("invoice to billing@acmeplumbing.com"))
check("a file host is not a website",
      sb.domains_in("assets at res.cloudinary.com") == [])
check("a social profile is not a website",
      sb.domains_in("see facebook.com/acme") == [])
check("a Simvoly platform domain is not a real domain",
      sb.domains_in("hosted at acme.simvoly.com") == [])
check("two domains in one description both come back",
      sb.domains_in("acme.com and acme-parts.net")
      == ["acme.com", "acme-parts.net"])
check("prose with a full stop in it is not a domain",
      sb.domains_in("Hosting for the year. Renewed.") == [])


# ---------------------------------------------------------------------------
section("Matching one charge to one site")
# ---------------------------------------------------------------------------
PROJECTS = [
    project("1", "TMRG - Acme Plumbing", "acmeplumbing.com", "Acme Plumbing"),
    project("2", "Riverside HVAC Supply", "riversidesupply.com",
            "Riverside HVAC Supply"),
    project("3", "Buckeye Lake Marina", "", "Buckeye Lake Marina"),
    project("4", "FabLocal - Dockside Diner", "docksidediner.com"),
]
IDX = sb.site_index(PROJECTS)

m = sb.match_line(line("Monthly Web Hosting", "Whoever",
                       "Hosting for acmeplumbing.com"), IDX)
check("a domain in the description is the join",
      m["kind"] == "domain" and [p["project_id"] for p in m["projects"]] == ["1"], m)

m = sb.match_line(line("Monthly Web Hosting", "Whoever", "Monthly hosting",
                       invoice_text="Site: acmeplumbing.com"), IDX)
check("a domain elsewhere on the invoice matches, labeled differently",
      m["kind"] == "invoice_domain" and "not on this line" in m["why"], m)

m = sb.match_line(line("Website Maintenance", "Acme Plumbing", "Monthly"), IDX)
check("an exact customer name matches the client on the project",
      m["kind"] == "client" and [p["project_id"] for p in m["projects"]] == ["1"], m)

m = sb.match_line(line("Website Maintenance", "Dockside Diner", "Monthly"), IDX)
check("an exact name matches the business name derived from the project title",
      m["kind"] == "name" and [p["project_id"] for p in m["projects"]] == ["4"], m)

m = sb.match_line(line("Website Maintenance", "Riverside HVAC", "Monthly"), IDX)
check("a name that is a SUBSTRING of a client's does not match them",
      m["kind"] != "client" and not m["projects"], m)

m = sb.match_line(line("Monthly Web Hosting", "Whoever",
                       "hosting for someoneelse.com"), IDX)
check("a domain we hold no site for is its own answer, with the domain named",
      m["kind"] == "domain_not_ours" and m["domain"] == "someoneelse.com", m)

m = sb.match_line(line("Website Maintenance", "Buckeye Marina", "Monthly"), IDX)
check("a resemblance comes back as possible, matching nothing",
      m["kind"] == "possible" and not m["projects"]
      and "Buckeye Lake Marina" in m["why"], m)

m = sb.match_line(line("Website Maintenance", "Nobody At All", "Monthly"), IDX)
check("a charge naming nothing says so", m["kind"] == "none" and not m["projects"], m)

# Two projects titled the same thing, filed under two different companies.
# "Summit Roofing" is the business name derived from both titles, so a charge
# naming it names both — and picking one files a hosting bill against the wrong
# company. (Note that "Summit Roofing" and "Summit Roofing LLC" are NOT this
# case: client_key.normalise_name drops LLC, so those are one company.)
TWINS = sb.site_index([
    project("10", "Summit Roofing", "summit-a.com", "Summit Roofing North"),
    project("11", "Summit Roofing", "summit-b.com", "Summit Roofing South"),
])
m = sb.match_line(line("Website Maintenance", "Summit Roofing", "Monthly"), TWINS)
check("a name answering to two different clients answers for neither",
      m["kind"] == "ambiguous" and not m["projects"], m)
check("and the refusal names the companies it would have had to choose between",
      "Summit Roofing North" in m["why"] and "Summit Roofing South" in m["why"], m)
# The title-derived name is indexed even when a client IS recorded — the
# reason the two are separate fields. Folding them into one made a project
# filed under a longer trading name unreachable by the name on its own title.
TRADING = sb.site_index([
    project("30", "FabLocal - Harbour Bakery", "harbourbakery.com",
            "Harbour Bakery Holdings"),
])
check("a project answers to the business name on its title as well as to the "
      "client it is filed under",
      sb.match_line(line("Website Maintenance", "Harbour Bakery", "Monthly"),
                    TRADING)["kind"] == "name",
      sb.match_line(line("Website Maintenance", "Harbour Bakery", "Monthly"), TRADING))
check("and to the client it is filed under",
      sb.match_line(line("Website Maintenance", "Harbour Bakery Holdings",
                         "Monthly"), TRADING)["kind"] == "client")
check("but never to the media partner in the title",
      not sb.match_line(line("Website Maintenance", "FabLocal", "Monthly"),
                        TRADING)["projects"])

# The client registry is the fifth rule: a QuickBooks customer name that names
# no project can still resolve to a Knack client whose website is on one.
REG_INDEX = sb.site_index([
    project("40", "Legacy Build 2019", "northsidedental.com"),
])
REG_INDEX["alias"] = {
    "by_domain": {},
    "by_name": {"northside dental group": {
        "key": "d:northsidedental.com", "name": "Northside Dental",
        "domain": "northsidedental.com", "names": ["Northside Dental"]}},
}
m = sb.match_line(line("Monthly Web Hosting", "Northside Dental Group",
                       "monthly hosting"), REG_INDEX)
check("a customer resolving through the client registry reaches their site",
      m["kind"] == "registry" and [p["project_id"] for p in m["projects"]] == ["40"], m)
check("and the row says it went through the registry, and to which domain",
      "client registry" in m["why"] and "northsidedental.com" in m["why"], m)
REG_INDEX["alias"] = None
check("a registry we could not read costs that rule and nothing else",
      sb.match_line(line("Monthly Web Hosting", "Northside Dental Group",
                         "monthly hosting"), REG_INDEX)["kind"] in ("possible", "none"))

check("an LLC suffix is not a second company",
      sb.match_line(line("Website Maintenance", "Acme Plumbing LLC", "Monthly"),
                    IDX)["kind"] == "client")


# ---------------------------------------------------------------------------
section("The report: what is billed, what is not, and what is not measured")
# ---------------------------------------------------------------------------
REPORT_PROJECTS = [
    project("1", "TMRG - Acme Plumbing", "acmeplumbing.com", "Acme Plumbing"),
    project("2", "Dead Diner", "deaddiner.com", "Dead Diner",
            status="EXPIRED"),
    project("3", "Cancelled Cafe", "cancelledcafe.com", "Cancelled Cafe",
            status="EXPIRED", lifecycle="CANCELLED"),
    project("4", "Never Billed Bakery", "neverbilled.com", "Never Billed Bakery"),
    project("5", "Lapsed Legal", "lapsedlegal.com", "Lapsed Legal"),
]
REPORT_LINES = [
    line("Monthly Web Hosting", "Acme Plumbing", "Hosting acmeplumbing.com"),
    # billed now, site expired — the finding
    line("Website Maintenance", "Dead Diner", "deaddiner.com upkeep"),
    # billed a year ago, site cancelled — cancelled AND stopped billing
    line("Website Maintenance", "Cancelled Cafe", "cancelledcafe.com",
         date=_ym(11)),
    # billed once, six months ago, site still live — lapsed
    line("Monthly Web Hosting", "Lapsed Legal", "lapsedlegal.com", date=_ym(6)),
    # a charge naming nothing we host
    line("Website Maintenance", "Someone Else", "hosting for elsewhere.org"),
]

rep = sb.report(lines=REPORT_LINES, projects=REPORT_PROJECTS, items=ITEMS,
                today=TODAY)
check("nothing blocks a healthy run", sb.unavailable(rep) == "", sb.unavailable(rep))
ids = lambda bucket: [r["site"]["project_id"] for r in rep[bucket]]   # noqa: E731

check("the billed, active site is fine", ids("ok") == ["1"], ids("ok"))
check("a site billed now but not active is the finding",
      ids("billed_inactive") == ["2"], ids("billed_inactive"))
check("a cancelled site whose billing also stopped is NOT a finding",
      "3" not in ids("billed_inactive"), rep["billed_inactive"])
check("a live site with no charge at all is listed as never billed",
      ids("unbilled") == ["4"], ids("unbilled"))
check("a live site billed once, long ago, is lapsed rather than unbilled",
      ids("lapsed") == ["5"], ids("lapsed"))
check("lapsed rows carry the date they were last billed",
      rep["lapsed"][0]["last_date"] == _ym(6), rep["lapsed"])
check("a charge matching no site is listed on its own",
      [l["customer"] for l in rep["unmatched"]] == ["Someone Else"],
      rep["unmatched"])
check("the unmatched row keeps the description it failed to match",
      "elsewhere.org" in rep["unmatched"][0]["description"])
check("counts add up to what was rendered",
      rep["counts"]["lines"] == 5 and rep["counts"]["sites"] == 5
      and rep["counts"]["active"] == 3, rep["counts"])
check("a non-hosting product is not read at all",
      sb.report(lines=[line("SEO Retainer", "Acme Plumbing", "acmeplumbing.com")],
                projects=REPORT_PROJECTS, items=ITEMS,
                today=TODAY)["counts"]["lines"] == 0)

# A resemblance must not silence the "nobody is billing for this" finding.
fuzzy = sb.report(
    lines=[line("Monthly Web Hosting", "Never Billed Bakry", "monthly")],
    projects=REPORT_PROJECTS, items=ITEMS, today=TODAY)
check("a possible match still counts the site as unbilled",
      "4" in [r["site"]["project_id"] for r in fuzzy["unbilled"]],
      fuzzy["unbilled"])
check("and the suggestion is printed on the unmatched charge",
      "Never Billed Bakery" in (fuzzy["unmatched"][0]["match"]["why"]),
      fuzzy["unmatched"])

# One charge, three live sites, matched on the customer name.
SHORT_PROJECTS = [
    project("20", "Multi Co Main", "multico.com", "Multi Co"),
    project("21", "Multi Co Shop", "multicoshop.com", "Multi Co"),
    project("22", "Multi Co Events", "multicoevents.com", "Multi Co"),
]
short = sb.report(lines=[line("Monthly Web Hosting", "Multi Co", "monthly hosting")],
                  projects=SHORT_PROJECTS, items=ITEMS, today=TODAY)
check("one name-matched charge against three live sites is its own finding",
      len(short["short"]) == 1 and short["short"][0]["lines"] == 1
      and len(short["short"][0]["sites"]) == 3, short["short"])
check("and none of those three is reported as unbilled",
      short["unbilled"] == [], short["unbilled"])
# A charge that names ONE domain says nothing about the client's other sites.
per_domain = sb.report(
    lines=[line("Monthly Web Hosting", "Multi Co", "hosting multico.com")],
    projects=SHORT_PROJECTS, items=ITEMS, today=TODAY)
check("a domain-matched charge covers only the site it names",
      [r["site"]["project_id"] for r in per_domain["ok"]] == ["20"]
      and sorted(r["site"]["project_id"] for r in per_domain["unbilled"])
      == ["21", "22"], per_domain)
check("and it is not reported as a customer short of charges",
      per_domain["short"] == [], per_domain["short"])


# ---------------------------------------------------------------------------
section("Not measured is not all clear")
# ---------------------------------------------------------------------------
gone = sb.report(lines=REPORT_LINES, projects=REPORT_PROJECTS,
                 items=[{"name": "Website Care Plan"}], today=TODAY)
check("with none of the three products in the catalog, nothing is measured",
      "None of the three hosting products" in sb.unavailable(gone),
      sb.unavailable(gone))
check("and every one of the three is named in the refusal",
      all(p in sb.unavailable(gone) for p in sb.HOSTING_PRODUCTS))

partial = sb.report(lines=REPORT_LINES, projects=REPORT_PROJECTS,
                    items=[{"name": "Monthly Web Hosting"}], today=TODAY)
check("one product missing does not block the report",
      sb.unavailable(partial) == "", sb.unavailable(partial))
check("but the missing one is named",
      "Website Maintenance" in partial["catalogue"]["missing"])

nosites = sb.report(lines=REPORT_LINES, projects=[], items=ITEMS, today=TODAY)
check("an empty site list is not measured, not all-clear",
      "no projects at all" in sb.unavailable(nosites), sb.unavailable(nosites))

broken = sb.report(lines=REPORT_LINES, projects=REPORT_PROJECTS, items=ITEMS,
                   today=TODAY)
broken["sites_error"] = "connection refused"
check("a site list that could not be read is not measured",
      "could not be read" in sb.unavailable(broken), sb.unavailable(broken))
broken2 = dict(broken, sites_error="", error="token refresh failed")
check("QuickBooks refusing is not measured",
      "QuickBooks could not be read" in sb.unavailable(broken2))
broken3 = dict(broken, sites_error="", error="",
               catalogue_error="HTTP 401", catalogue={"found": {}, "missing":
                                                      list(sb.HOSTING_PRODUCTS),
                                                      "similar": []})
check("a catalog we could not READ is a different answer from products that "
      "are not there",
      "could not be read" in sb.unavailable(broken3)
      and "None of the three" not in sb.unavailable(broken3), sb.unavailable(broken3))


# ---------------------------------------------------------------------------
section("The QA report renders it")
# ---------------------------------------------------------------------------
from hub import qa                                              # noqa: E402

check("it is registered under Billing & Accounting",
      qa.REPORTS["sites-billing"]["group"] == "Billing & Accounting")
check("its title says what it is",
      qa.REPORTS["sites-billing"]["title"] == "Sites Billing Report")

_real_report, _real_state = sb.report, qa._qb_state
try:
    sb.report = lambda *a, **k: rep
    qa._qb_state = lambda: (None, None)
    out = qa.sites_billing()
finally:
    sb.report, qa._qb_state = _real_report, _real_state

check("every row has a cell per column",
      all(len(r) == len(out["columns"]) for r in out["rows"]),
      [len(r) for r in out["rows"]])
check("row styles line up with rows one for one",
      len(out["row_styles"]) == len(out["rows"]))
text = str(out["rows"])
check("the dead site being billed is on the page", "Dead Diner" in text)
check("the never-billed live site is on the page", "Never Billed Bakery" in text)
check("the unmatched charge is on the page", "elsewhere.org" in text)
check("findings are tinted and the all-clear rows are not",
      "red" in out["row_styles"] and "yellow" in out["row_styles"])
check("the note says what was NOT read",
      "sales receipts" in out["note"] and "recurring" in out["note"], out["note"])
check("the note names the window it read",
      rep["since"] in out["note"], out["note"])

# Not measured has to reach the page as `unavailable`, which the template
# renders instead of the green tick — the whole difference between "we looked
# and it is fine" and "we could not look".
try:
    sb.report = lambda *a, **k: gone
    qa._qb_state = lambda: (None, None)
    blocked = qa.sites_billing()
finally:
    sb.report, qa._qb_state = _real_report, _real_state
check("a report that cannot answer sets unavailable, not an empty table",
      blocked.get("unavailable") and blocked["rows"] == [], blocked)


# ---------------------------------------------------------------------------
section("The routes exist under the hub app, not a mount")
# ---------------------------------------------------------------------------
try:
    from werkzeug.test import Client as WClient

    import wsgi
    composed = WClient(wsgi.application)
    composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"],
                                  "name": "T"})
    check("/qa/sites-billing answers",
          composed.get("/qa/sites-billing").status_code == 200)
    api = composed.get("/api/qa/sites-billing")
    check("/api/qa/sites-billing answers", api.status_code == 200)
    body = api.get_json()
    # With no QuickBooks company connected this must be the "connect it" state
    # and never an empty all-clear table.
    check("with QuickBooks unconnected it says so rather than reporting clear",
          bool(body.get("needs_qb") or body.get("unavailable") or body.get("error")),
          body)
    check("the tile appears on the QA index",
          b"Sites Billing Report" in composed.get("/qa").data)
except Exception as exc:                                        # noqa: BLE001
    check("the composed app boots with these routes", False, exc)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
