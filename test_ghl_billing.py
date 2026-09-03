"""The two Suite billing reports, driven against a stubbed SaaS API.

    python3 test_ghl_billing.py

`ghl-billing-no-products` and `ghl-billing-this-month` had been in
`hub/qa.py` since the SaaS Configurator endpoints were transcribed from
GoHighLevel's OpenAPI spec, had never been run against live data, and had no
test of their own: `test_qa_reports.py` sweeps them for the shape of what
they return and nothing anywhere stubbed a response. Two things in them are
assumptions -- the shape of the v3 `/saas/saas-locations/{companyId}` page,
and the three status strings that count as billing -- and this file pins
what the code does with each, the way `test_qa_reports.py` pins the rest of
the page. `tools/probe_ghl_saas.py` is the other half: it prints what GHL
actually returns, and nothing here changes until it has been run.

Four things it holds, worst first:

  * An unrecognised subscription status is NAMED, never dropped. Before this
    a string in neither list was excluded exactly as "canceled" was, and the
    difference between those is a paying client vanishing from a billing
    report with every screen green.
  * A source that could not be read is never an all-clear: GHL refusing,
    the company id unset, and the Knack export unreadable each render as
    "could not check", and none is stored as the day's answer.
  * The plan catalogue refusing is said on the report, not passed off as
    every client costing $0.
  * Pagination walks every page and stops on the last, and the fixture is
    the shape the code assumes so the day the probe disagrees the fixture
    is the thing to correct.
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-ghlbilling-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["CLIENTS_DATA_DIR"] = os.path.join(ROOT, "tests", "fixtures", "clients")
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "activity.jsonl")
os.environ["REPORT_CACHE"] = "off"
os.environ.setdefault("SECRET_KEY", "ghl-billing-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
# `settings` is a frozen dataclass built once at import, so the credentials
# the reports check have to be in the environment before `hub` is imported.
os.environ["GHL_PRIVATE_TOKEN"] = "pit-test-token"
os.environ["GHL_COMPANY_ID"] = "comp_test"

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


from hub import qa, report_cache, knack_data                    # noqa: E402
from hub import client_key as ck                                # noqa: E402

# --------------------------------------------------------------------- fixtures
#
# The v3 shape as hub/qa.py assumes it: a page carries `locations`, each with
# `subscriptionInfo` holding the status and the plan id, and a `pagination`
# block whose `hasNext` says whether to ask again. The agency plans answer
# carries `plans`, each with a `prices` list. Both are transcribed from the
# spec and neither has been confirmed live -- see the module docstring.


def _loc(lid, name, status, plan="plan_pro", website=""):
    row = {"locationId": lid, "name": name,
           "subscriptionInfo": {"subscriptionStatus": status, "saasPlanId": plan}}
    if website:
        row["website"] = website
    return row


PLANS = {"plans": [
    {"planId": "plan_pro", "title": "Suite Pro",
     "prices": [{"billingInterval": "month", "amount": 297, "active": True},
                {"billingInterval": "year", "amount": 2970, "active": True}]},
    {"planId": "plan_lite", "title": "Suite Lite",
     "prices": [{"billingInterval": "month", "amount": 97, "active": True}]},
]}

PAGE_ONE = {
    "locations": [
        _loc("loc_acme", "Acme Roofing", "active", website="https://acmeroofing.com"),
        _loc("loc_blue", "Blue Harbor Dental", "trialing", plan="plan_lite"),
        _loc("loc_cane", "Cane Creek Marina", "past_due"),
        _loc("loc_done", "Done Deal Realty", "canceled"),
        _loc("loc_paus", "Pause Pottery", "paused"),
    ],
    "pagination": {"hasNext": False},
}


class FakeSaas:
    """Stands in for `qa._ghl_saas`: answers by path, remembers every call."""

    def __init__(self, pages, plans=PLANS, plans_error=None):
        self.pages = pages
        self.plans = plans
        self.plans_error = plans_error
        self.calls = []

    def __call__(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if path.startswith("/saas/agency-plans/"):
            if self.plans_error:
                raise RuntimeError(self.plans_error)
            return self.plans
        if path.startswith("/saas/saas-locations/"):
            page = int((params or {}).get("page") or 1)
            return self.pages[page - 1]
        raise AssertionError(f"unexpected path {path}")


def _group(live=True):
    return {"rows": [], "partner": "", "sales": "", "live": ["x"] if live else [],
            "thisM": live, "lastM": False, "this_total": 0, "last_total": 0,
            "live_total": 0, "has_dash": False, "last_end": None}


# Acme has a live Smart 1 product; Blue Harbor is on the book with nothing
# running; the other three are not Knack clients at all.
GROUPS = {"Acme Roofing": _group(live=True), "Blue Harbor Dental": _group(live=False)}


def _index(names):
    """A client_key index the two Knack names resolve through -- built by hand
    so the test does not depend on the fixture registry knowing them."""
    by_name, entries = {}, {}
    for n in names:
        key = ck.name_key(n)
        e = {"key": key, "name": n, "domain": "", "names": [n], "products": 1}
        by_name[ck.normalise_name(n)] = e
        entries[key] = e
    return {"by_domain": {}, "by_name": by_name, "entries": entries,
            "domain_conflicts": {}, "error": ""}


def run_with(saas, groups=GROUPS, products_error="", company=True):
    """Run both reports with every outside source stubbed."""
    saved = (qa._ghl_saas, qa._client_groups, knack_data.products_error,
             ck.alias_index)
    qa._ghl_saas = saas
    qa._client_groups = lambda: groups
    knack_data.products_error = lambda: products_error
    ck.alias_index = lambda refresh=False: _index(list(groups))
    if not company:
        from hub import config as _config
        object.__setattr__(_config.settings, "ghl_company_id", "")
    try:
        return qa.run("ghl-billing-no-products"), qa.run("ghl-billing-this-month")
    finally:
        (qa._ghl_saas, qa._client_groups, knack_data.products_error,
         ck.alias_index) = saved
        if not company:
            object.__setattr__(_config.settings, "ghl_company_id", "comp_test")


def _names(report):
    return [r[1] for r in report["rows"]]


# ------------------------------------------------------------ 1. the v3 shape
section("The v3 response shape the code assumes")

saas = FakeSaas([PAGE_ONE])
no_products, this_month = run_with(saas)

check("both reports run without an error", not no_products.get("error")
      and not this_month.get("error"),
      f"{no_products.get('error')!r} / {this_month.get('error')!r}")
check("the locations page was asked for under the company id",
      any(p == "/saas/saas-locations/comp_test" for p, _ in saas.calls), saas.calls)
check("the plan catalogue was read once per report",
      sum(1 for p, _ in saas.calls if p.startswith("/saas/agency-plans/")) == 2)

check("this-month lists exactly the active, trialing and past-due accounts",
      set(_names(this_month)) == {"Acme Roofing", "Blue Harbor Dental", "Cane Creek Marina"},
      _names(this_month))
check("and a canceled or paused one is left out silently -- those are known",
      "Done Deal Realty" not in _names(this_month)
      and "Pause Pottery" not in _names(this_month))
check("biggest bill first", _names(this_month)[0] == "Acme Roofing", _names(this_month))
check("the plan title and monthly price come off the catalogue",
      this_month["rows"][0][2] == "Suite Pro" and "297" in this_month["rows"][0][3],
      this_month["rows"][0])
check("the total is the sum of what was counted",
      "$691" in this_month["note"], this_month["note"])
cane = next(r for r in this_month["rows"] if r[1] == "Cane Creek Marina")
check("the status prints in words", cane[4] == "Past Due", cane)

check("no-products leaves out the client with a live Smart 1 product",
      "Acme Roofing" not in _names(no_products), _names(no_products))
check("and keeps the client on the book with nothing running",
      "Blue Harbor Dental" in _names(no_products), _names(no_products))
check("and keeps the sub-account Knack has never heard of",
      "Cane Creek Marina" in _names(no_products), _names(no_products))
check("a Knack client links to their record",
      isinstance(no_products["rows"][0][0], dict)
      or isinstance(no_products["rows"][1][0], dict), no_products["rows"])
check("no unknown-status line when every status is known",
      "does not recognise" not in no_products["note"]
      and "does not recognise" not in this_month["note"])
check("both are stored as the day's answer",
      report_cache.is_answer(no_products) and report_cache.is_answer(this_month))
check("both are JSON-encodable",
      json.dumps(no_products) and json.dumps(this_month))

# ------------------------------------------------------------- 2. pagination
section("More locations than one page")

page_a = {"locations": [_loc(f"loc_{i}", f"Client {i}", "active") for i in range(3)],
          "pagination": {"hasNext": True}}
page_b = {"locations": [_loc("loc_last", "Last Page Co", "active")],
          "pagination": {"hasNext": False}}
saas = FakeSaas([page_a, page_b])
_, this_month = run_with(saas, groups={})
# Two reports ran, so the walk happened twice; each must ask for both pages.
pages_asked = [p.get("page") for path, p in saas.calls if "saas-locations" in path]
check("every page is asked for, in order", pages_asked == [1, 2, 1, 2], pages_asked)
check("and the rows from both pages are on the report",
      set(_names(this_month)) == {"Client 0", "Client 1", "Client 2", "Last Page Co"},
      _names(this_month))

empty_end = {"locations": [], "pagination": {"hasNext": True}}
saas = FakeSaas([page_a, empty_end, page_b])
_, this_month = run_with(saas, groups={})
pages_asked = [p.get("page") for path, p in saas.calls if "saas-locations" in path]
check("an empty page ends the walk even when hasNext says otherwise",
      pages_asked == [1, 2, 1, 2], pages_asked)

# ---------------------------------------------- 3. a status nobody recognises
section("An unrecognised subscription status is named, never dropped")

odd = {"locations": [
    _loc("loc_acme", "Acme Roofing", "active"),
    _loc("loc_new", "Newfangled Co", "frozen"),
    _loc("loc_blank", "Blank Status Co", ""),
    _loc("loc_can", "Gone Co", "canceled"),
], "pagination": {"hasNext": False}}
no_products, this_month = run_with(FakeSaas([odd]))

for label, rep in (("no-products", no_products), ("this-month", this_month)):
    check(f"{label}: the unknown statuses are not counted as billing",
          "Newfangled Co" not in _names(rep) and "Blank Status Co" not in _names(rep),
          _names(rep))
    check(f"{label}: and the report says so, with the count",
          "2 sub-accounts had a billing status this report does not recognise"
          in rep["note"], rep["note"])
    check(f"{label}: naming the statuses", "frozen" in rep["note"]
          and "(blank)" in rep["note"], rep["note"])
    check(f"{label}: and the sub-accounts", "Newfangled Co" in rep["note"]
          and "Blank Status Co" in rep["note"], rep["note"])
    check(f"{label}: and where to go next", "probe_ghl_saas" in rep["note"])
    check(f"{label}: a canceled account is still left out without comment",
          "Gone Co" not in rep["note"] and "Gone Co" not in _names(rep))
    check(f"{label}: is still the day's answer -- the rows it could count are real",
          report_cache.is_answer(rep))

check("the vocabulary in the code has the three billing statuses it started with",
      qa._ACTIVE_SUB_STATUSES == {"active", "trialing", "past_due"},
      qa._ACTIVE_SUB_STATUSES)
check("and the two lists never overlap",
      not (qa._ACTIVE_SUB_STATUSES & qa._INACTIVE_SUB_STATUSES))

# ---------------------------------------------- 4. the plan catalogue refusing
section("The plan catalogue refusing is said, not passed off as $0")

no_products, this_month = run_with(FakeSaas([PAGE_ONE], plans_error="HTTP 403"))
check("the rows are still listed", len(this_month["rows"]) == 3, _names(this_month))
check("every Monthly reads $0", all("$0" in r[3] for r in this_month["rows"]),
      [r[3] for r in this_month["rows"]])
for label, rep in (("no-products", no_products), ("this-month", this_month)):
    check(f"{label}: says the plan catalogue could not be read",
          "plan catalogue could not be read" in rep["note"], rep["note"])
    check(f"{label}: and that the totals are therefore not measured",
          "not measured" in rep["note"])
    check(f"{label}: carrying the reason", "HTTP 403" in rep["note"])

# --------------------------------------------------- 5. could not look at all
section("Could not check is never all clear")


def _refuse(path, params=None):
    raise RuntimeError("GHL SaaS /saas/saas-locations/comp_test failed (HTTP 401)")


for label, rep in zip(("no-products", "this-month"), run_with(_refuse)):
    check(f"{label}: GHL refusing is an error on the report",
          "HTTP 401" in (rep.get("error") or ""), rep)
    check(f"{label}: with no rows and no all-clear", rep["rows"] == []
          and not rep.get("note"), rep)
    check(f"{label}: and is not stored as the day's answer",
          report_cache.is_answer(rep) is False)

for label, rep in zip(("no-products", "this-month"), run_with(FakeSaas([PAGE_ONE]), company=False)):
    check(f"{label}: an unset company id names the variable",
          "GHL_COMPANY_ID" in (rep.get("error") or ""), rep)
    check(f"{label}: and is not stored", report_cache.is_answer(rep) is False)

for label, rep in zip(("no-products", "this-month"),
                      run_with(FakeSaas([PAGE_ONE]),
                               products_error="the products export could not be read")):
    check(f"{label}: an unreadable Knack export is not measured",
          rep.get("measured") is False, rep)
    check(f"{label}: and says why", "could not be read" in rep.get("note", ""), rep)
    check(f"{label}: and is not stored", report_cache.is_answer(rep) is False)

saas = FakeSaas([PAGE_ONE])
run_with(saas, products_error="the products export could not be read")
check("and GHL is never asked when the Knack half cannot answer", saas.calls == [],
      saas.calls)

# ----------------------------------------------------------- 6. the probe
section("The probe is read-only")

import ast                                                       # noqa: E402
probe = os.path.join(ROOT, "tools", "probe_ghl_saas.py")
check("tools/probe_ghl_saas.py exists", os.path.exists(probe))
if os.path.exists(probe):
    src = open(probe, encoding="utf-8").read()
    tree = ast.parse(src)
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    check("it never posts, puts or deletes", not ({"post", "put", "delete", "patch"} & calls),
          calls & {"post", "put", "delete", "patch"})
    check("it reads the same SaaS helper the reports use", "_ghl_saas" in src)
    check("it asserts nothing", not any(isinstance(n, ast.Assert) for n in ast.walk(tree)))
    check("and never prints the token",
          not any("print" in ln and "ghl_token" in ln for ln in src.splitlines()))

shutil.rmtree(_TMP, ignore_errors=True)
print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
