"""hub/knack_data.websites() — live where Knack answers, the export where it will not.

    python3 test_knack_websites_source.py

## What is worth asserting here

`clients_registry.all_clients()` — which feeds client search, every client
picker, Client 360's lookup and the social content link — built its domains
from a static website export refreshed by hand. Meanwhile
`hub/knack_websites.py` had been reading the same
object live for the domain record, the renewals calendar and the orphan list.
So the Hub held a live answer and a stale one to "what websites does this
client have", and the load-bearing readers took the stale one — silently,
because a short list looks exactly like a complete one.

Four things about the fix fail quietly rather than loudly, and all four are
here:

  * **A failed pull must never empty a good export.** If a Knack outage turned
    610 sites into zero, every client would read as having no website and
    every domain-keyed join in the Hub would come apart, with nothing on any
    screen saying why. Stale beats empty.

  * **The live rows must arrive in the export's own field names.** Eight call
    sites read `name` / `domain` / `liveUrl` / `platform`. One shape, so no
    reader can tell which source answered — and so none of them can come to
    depend on one.

  * **`summary()` must keep reading the export.** It measures the dashboard's
    scorecard against the export's own period and its `active` field, which
    object_153 does not publish. A live list folded in there reports 0 active
    websites and $0 of H&M billing on the CEO's dashboard.

  * **Nothing is invented in the mapping.** `active`, `hmFreq`, `notes` and
    `created` are absent from a live row rather than defaulted, because a
    False `active` reads as a dead site on every row.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-web-source-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["SECRET_KEY"] = "web-source-test"
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["CLIENTS_DATA_DIR"] = os.path.join(ROOT, "tests", "fixtures", "clients")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


from hub import knack_data as kd                                  # noqa: E402
from hub import knack_websites as kw                              # noqa: E402

LIVE = [
    {"id": "r1", "client": "Icon Solar", "organization": "",
     "domain": "iconsolar.com", "production_url": "https://iconsolar.com",
     "platform": "Simvoly", "client_status": "Active", "hm_fee": 250,
     "media_partner": "TMRG", "ga_account": "GA-1", "gtm_account": "GTM-2",
     "registrar": "GoDaddy", "domain_bought": "Yes"},
    # A site nobody has ever exported: the whole point of reading live.
    {"id": "r2", "client": "Northgate Dental", "organization": "",
     "domain": "northgatedental.com",
     "production_url": "https://northgatedental.com",
     "platform": "WordPress", "client_status": "Active", "hm_fee": 0},
]


def _reset():
    kd._WEB_CACHE.update({"rows": None, "source": "", "at": 0.0})    # noqa: SLF001


# ---------------------------------------------------------------------------
print("\nThe mapping invents nothing and keeps the export's field names")
# ---------------------------------------------------------------------------
row = kd.website_row_from_live(LIVE[0])
check("the client's name lands on `name`", row["name"] == "Icon Solar", row)
check("the production URL lands on `liveUrl`",
      row["liveUrl"] == "https://iconsolar.com")
check("the domain is lower-cased", row["domain"] == "iconsolar.com")
check("the platform carries", row["platform"] == "Simvoly")
check("client_status becomes `status`", row["status"] == "Active")
check("the H&M fee carries under both names it is read by",
      row["hm"] == 250 and row["hmMonthly"] == 250)
check("the media partner carries", row["partner"] == "TMRG")
check("GA and GTM carry", row["ga"] == "GA-1" and row["gtm"] == "GTM-2")
absent = [k for k in ("active", "hmFreq", "notes", "created", "domainCost")
          if k in row]
check("what object_153 does not publish is absent, not defaulted", not absent,
      absent)
check("a record with no production URL still gets a usable one",
      kd.website_row_from_live({"client": "X", "domain": "x.com"})["liveUrl"]
      == "https://x.com")

export_keys = set(kd.export_websites()[0])
live_keys = set(row)
check("every field the live row carries is one the export also has",
      live_keys <= export_keys, sorted(live_keys - export_keys))


# ---------------------------------------------------------------------------
print("\nLive when Knack answers")
# ---------------------------------------------------------------------------
_real_rows, _real_err = kw.rows, kw.last_error
kw.rows = lambda *a, **k: list(LIVE)
kw.last_error = lambda: ""
_reset()

rows, source, err = kd._website_source()                          # noqa: SLF001
check("the live registry answers", source == "knack", (source, err))
check("and every record comes through", len(rows) == 2, len(rows))
check("in the export's shape",
      all({"name", "domain", "liveUrl", "platform"} <= set(r) for r in rows))
check("websites() reports which source answered",
      kd.websites_source() == "knack")

names = {r["name"] for r in kd.websites()}
check("a site that is in Knack and in no export is now visible",
      "Northgate Dental" in names)


# ---------------------------------------------------------------------------
print("\nA failed pull never empties a good export")
# ---------------------------------------------------------------------------
kw.rows = lambda *a, **k: []
kw.last_error = lambda: "Knack was unreachable (Timeout)."
_reset()

rows, source, err = kd._website_source()                          # noqa: SLF001
check("an outage falls back to the export rather than to nothing",
      source == "export" and len(rows) > 0, (source, len(rows)))
check("and the reason travels with it, rather than reading as an empty registry",
      "unreachable" in err, err)


def _boom(*a, **k):
    raise RuntimeError("Knack exploded")


kw.rows = _boom
_reset()
rows, source, err = kd._website_source()                          # noqa: SLF001
check("a raising pull is caught, not propagated to the page",
      source == "export" and len(rows) > 0)
check("and it is named by its exception", "RuntimeError" in err, err)

kw.rows, kw.last_error = _real_rows, _real_err
_reset()


# ---------------------------------------------------------------------------
print("\nThe dashboard scorecard keeps reading the export")
# ---------------------------------------------------------------------------
import inspect                                                    # noqa: E402
src = inspect.getsource(kd.summary)
check("summary() reads export_websites(), not websites()",
      "export_websites()" in src and "webs = websites()" not in src)
check("and says why in the code rather than leaving it to be rediscovered",
      "measured differently" in src or "active" in src)

summary_live_calls = []


def _summary_live_rows(*a, **k):
    summary_live_calls.append(True)
    return list(LIVE)


kw.rows = _summary_live_rows
kw.last_error = lambda: ""
_reset()
totals = kd.summary()
# The real assertion: the dashboard's website figures must describe the
# fallback export without consulting the live list. If it followed the live
# list, its active count and H&M billing would collapse to fields object_153
# does not publish — a confident wrong answer on the CEO's dashboard.
check("websites_total still counts the export, not the live pull",
      totals["websites_total"] == len(kd.export_websites()),
      (totals["websites_total"], len(kd.export_websites())))
check("and calculating it never pulls the live rows",
      not summary_live_calls, summary_live_calls)
check("websites_active is measured off the export's own `active` field",
      totals["websites_active"] > 0, totals["websites_active"])
check("so H&M billing is not collapsed to the live rows' fees",
      totals["hm_monthly"] > 0, totals["hm_monthly"])
kw.rows, kw.last_error = _real_rows, _real_err
_reset()


# ---------------------------------------------------------------------------
print("\nThe payoff: the client registry sees a site only Knack knows about")
# ---------------------------------------------------------------------------
from hub import clients_registry                                  # noqa: E402

kw.rows = lambda *a, **k: list(LIVE)
kw.last_error = lambda: ""
_reset()
rows = clients_registry.all_clients(refresh=True)
by_name = {str(r.get("name", "")).strip().lower(): r for r in rows}
found = by_name.get("northgate dental")
check("a client whose only website is live in Knack reaches the registry",
      found is not None, sorted(by_name)[:4])
if found:
    check("carrying the domain, so every domain-keyed join can find them",
          "northgatedental.com" in
          f"{found.get('url', '')} {found.get('domain', '')}", found)

kw.rows, kw.last_error = _real_rows, _real_err
_reset()
clients_registry.all_clients(refresh=True)


# ---------------------------------------------------------------------------
print("\nThe status row is about the data, not about the last deploy")
# ---------------------------------------------------------------------------
# /status used to read products.json's mtime and print it as "Refreshed Xh
# ago", warning past 48 hours. In a Docker deploy every file is written at
# image build time, so that measures time since the last *deploy* — wrong in
# both directions: a months-old export reads as two hours old for two days
# after any deploy, and a container left up for a week warns about data
# nothing has touched. export_state() is the honest signal and is already
# shared with the dashboard and hub/housekeeping.py.
state = kd.export_state()
check("the export knows which month it was generated for",
      bool(state["period"]), state)
check("and it is compared against the calendar, not a file timestamp",
      state["current"] and "stale" in state, state)

import hub as _hub                                                # noqa: E402
import inspect as _inspect                                        # noqa: E402
hub_src = _inspect.getsource(_hub.create_hub_app)
row = hub_src[hub_src.index("--- Knack data ---"):][:2000]
check("the status row reads export_state()", "export_state()" in row)
check("and no longer prints the file age as a refresh time",
      "Refreshed {age" not in row and "Last refreshed" not in row, row[:200])
check("a stale export is a warning naming both months",
      "state['label']" in row and "state['current_label']" in row)
check("an export carrying no month at all is named rather than passed as fresh",
      'not state["period"]' in row)

signed = None
try:
    app = _hub.create_hub_app()
    cli = app.test_client()
    cli.post("/login", data={"password": os.environ["PANEL_PASSWORD"]},
             follow_redirects=True)
    rows = (cli.get("/api/status").get_json() or {}).get("checks") or []
    signed = next((r for r in rows if "Smart 1 Team data" in r.get("name", "")), None)
except Exception as exc:                                          # noqa: BLE001
    check("the status page renders the row", False, exc)
if signed:
    check("the row renders", bool(signed.get("message")), signed)
    check("and says which source the site count came from",
          "private fallback" in signed["message"] or "live from Knack" in signed["message"],
          signed["message"])
    check("without claiming a refresh time it cannot know",
          "Refreshed" not in signed["message"], signed["message"])


# ---------------------------------------------------------------------------
print("\nThe dead exports are gone, and the live ones still load")
# ---------------------------------------------------------------------------
check("campaigns.json is gone", kd._load("campaigns.json") is None)     # noqa: SLF001
check("live_products.json is gone", kd._load("live_products.json") is None)  # noqa: SLF001
check("products.json still loads", len(kd.products()) > 0)
check("websites.json still loads", len(kd.export_websites()) > 0)
# The claim, not the string. The docstring still *quotes* the old wording in
# order to correct it, and matching the text reports the explanation of the
# fix as the defect — the reason tools/spellcheck.py reads the AST and
# CLAUDE.md says "prose is not a call site". This asserts what it now says.
doc = kd.__doc__ or ""
check("knack_data says plainly that fallback data is private",
      "private mounted volume" in doc, doc[:200])
check("and names them as a fallback rather than a source",
      "fall" in doc.lower() and "cannot be reached" in doc)
check("the two dead files are accounted for rather than silently dropped",
      "campaigns.json" in doc and "live_products.json" in doc)
import pathlib as _pl                                             # noqa: E402
check("and there is genuinely no refresh workflow to point at",
      [f.name for f in _pl.Path(ROOT, ".github", "workflows").glob("*.yml")]
      == ["checks.yml"],
      [f.name for f in _pl.Path(ROOT, ".github", "workflows").glob("*.yml")])


# ---------------------------------------------------------------------------
print("\nOne mapping, not two")
# ---------------------------------------------------------------------------
attach_src = inspect.getsource(kd._attachment_only_websites)      # noqa: SLF001
check("the attachment path reads the shared mapping",
      "website_row_from_live" in attach_src)
check("rather than restating the field names",
      attach_src.count('"platform"') == 0, attach_src)


# ---------------------------------------------------------------------------
print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
