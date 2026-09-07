#!/usr/bin/env python3
"""The Google Ads sweep, on the dashboard.

`modules/ads_builder/monitoring.py` has swept every deployed account twice a
day since it was built, and nothing told anybody a reading had arrived. There
is no mailer in this Hub, so the number goes where people already look --
`hub/social_status.py`'s own note, one tool over.

What is asserted here is the half that goes wrong quietly:

* every kind of nothing says which kind it is, because "no account flagged
  anything", "no proposal has been deployed with a customer id" and "we could
  not read the scans" are three different answers and only the first means the
  book is clean;
* the five signals stay apart, because a finding in a client's account, a scan
  Google refused, an account nobody has swept and a reading that has gone stale
  send somebody to four different places;
* every count opens the rows it counted, and the page it opens knows the filter
  and the account parameter -- a link that resolves, renders 200 and shows
  something other than what was counted is the failure this rule exists for;
* the card reads `measured` before it draws a number, and so does every other
  card on that dashboard.

Run: python3 test_ads_dashboard_card.py
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Own directory AND own database. Setting only the first is the trap
# test_jsonstore.py names: the mirror is keyed relative to the data root, so a
# fresh directory in front of an inherited DATABASE_URL is refilled with the
# last run's rows.
_TMP = tempfile.mkdtemp(prefix="ads-card-")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"

FAILED = []
_SECTION = ""


def section(name: str) -> None:
    global _SECTION
    _SECTION = name
    print(f"\n== {name} ==")


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        if detail:
            print(f"        {detail}")
        FAILED.append(f"{_SECTION}: {label}")


from hub import ads_status                                # noqa: E402
from modules.ads_builder import monitoring, store         # noqa: E402


# ---------------------------------------------------------------------------
# A fake store, so the shape is asserted rather than whatever this checkout
# happens to have deployed. The real one is driven further down.
# ---------------------------------------------------------------------------
class FakeStore:
    def __init__(self, accounts, runs, applied=0, raises=""):
        self._accounts = accounts
        self._runs = runs
        self._applied = applied
        self._raises = raises

    def deployed_accounts(self, limit=500):
        if self._raises == "accounts":
            raise RuntimeError("Postgres said no")
        return list(self._accounts)

    def latest_optimization_runs(self, limit=100):
        if self._raises == "runs":
            raise RuntimeError("Postgres said no")
        return list(self._runs)

    def count_events(self, action, *, since=None):
        if self._raises == "events":
            raise RuntimeError("Postgres said no")
        return self._applied


def _at(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _board(fake, **kw):
    real = ads_status._module
    ads_status._module = lambda: fake
    try:
        return ads_status.scoreboard(**kw)
    finally:
        ads_status._module = real


ACCOUNTS = [
    {"customer_id": "1111111111", "client_name": "Acme Plumbing", "proposal_id": "p1"},
    {"customer_id": "2222222222", "client_name": "Riverside HVAC", "proposal_id": "p2"},
    {"customer_id": "3333333333", "client_name": "Icon Solar", "proposal_id": "p3"},
    {"customer_id": "4444444444", "client_name": "Buckeye Marina", "proposal_id": "p4"},
    {"customer_id": "5555555555", "client_name": "Cirilla's", "proposal_id": "p5"},
]


def _runs_now():
    return [
        # flagged, and swept an hour ago
        {"customer_id": "1111111111", "client_name": "Acme Plumbing",
         "scanned_at": _at(1), "item_count": 9, "high_severity_count": 3,
         "error": "", "measured": True},
        # Google refused
        {"customer_id": "2222222222", "client_name": "Riverside HVAC",
         "scanned_at": _at(2), "item_count": 0, "high_severity_count": 0,
         "error": "PERMISSION_DENIED", "measured": False},
        # clean and current
        {"customer_id": "3333333333", "client_name": "Icon Solar",
         "scanned_at": _at(3), "item_count": 4, "high_severity_count": 0,
         "error": "", "measured": True},
        # clean, but the sweep has not reached it in five days
        {"customer_id": "4444444444", "client_name": "Buckeye Marina",
         "scanned_at": _at(24 * 5), "item_count": 2, "high_severity_count": 0,
         "error": "", "measured": True},
        # 5555555555 has never been swept: no row at all
    ]


# ---------------------------------------------------------------------------
section("Five signals, kept apart")
# One "needs attention" number covering all of these is a number nobody can
# act on -- the note hub/sales_status.py makes about its own five.

board = _board(FakeStore(ACCOUNTS, _runs_now(), applied=2))
counts = board.get("counts") or {}

check("it measured", board.get("measured") is True, repr(board.get("error")))
check("an account with a high-severity finding is 'needs attention'",
      counts.get("attention") == 1, repr(counts))
check("an account Google refused is counted apart, not as a finding",
      counts.get("failed") == 1, repr(counts))
check("an account nobody has swept is counted apart from a clean one",
      counts.get("never") == 1, repr(counts))
check("a reading older than the sweep's own cadence is 'out of date'",
      counts.get("stale") == 1, repr(counts))
check("and a swept, current, unflagged account is clean",
      counts.get("clean") == 1, repr(counts))
check("the findings are totalled, not just the accounts",
      board.get("findings") == 3, repr(board.get("findings")))

# The one thing on the card that is not a to-do: work done with nobody having
# pressed anything. It is the only record of it.
check("unattended changes are counted over a stated window",
      (board.get("applied") or {}).get("count") == 2
      and (board.get("applied") or {}).get("days") == ads_status.APPLIED_WINDOW_DAYS,
      repr(board.get("applied")))
check("and the sentence says so out loud",
      "applied automatically" in (board.get("line") or ""),
      repr(board.get("line")))


# ---------------------------------------------------------------------------
section("Every kind of nothing says which kind it is")

clean = _board(FakeStore(
    ACCOUNTS[:1],
    [{"customer_id": "1111111111", "client_name": "Acme Plumbing",
      "scanned_at": _at(1), "item_count": 2, "high_severity_count": 0,
      "error": "", "measured": True}]))
check("a swept, unflagged book says every account was swept",
      clean["measured"] and clean["counts"]["clean"] == 1
      and "none of them flagged anything" in clean["line"],
      repr(clean.get("line")))

none_deployed = _board(FakeStore([], []))
check("no deployed account is a named state, never four noughts",
      none_deployed["measured"] and none_deployed.get("empty") == "no_accounts"
      and "customer id" in none_deployed["line"],
      repr(none_deployed.get("line")))
check("and it draws no rows to imply a book",
      none_deployed["rows"] == [] and none_deployed["accounts"] == 0)

never_swept = _board(FakeStore(ACCOUNTS[:2], []))
check("deployed but never swept does not read as clean",
      never_swept["counts"]["never"] == 2
      and never_swept["counts"]["clean"] == 0
      and "never swept" in never_swept["line"],
      repr(never_swept.get("line")))

for which in ("accounts", "runs"):
    refused = _board(FakeStore(ACCOUNTS, _runs_now(), raises=which))
    check(f"a store that will not answer ({which}) is not measured, never zero",
          refused.get("measured") is False and bool(refused.get("error")),
          repr(refused))

# The one figure that must never read as "none happened" when what happened is
# that we could not ask: unattended writes into a client's live account.
no_events = _board(FakeStore(ACCOUNTS, _runs_now(), raises="events"))
check("unattended changes that could not be counted are None, not 0",
      no_events.get("applied") is None, repr(no_events.get("applied")))
check("and the line says so rather than staying silent",
      "could not be counted" in (no_events.get("line") or ""),
      repr(no_events.get("line")))


# ---------------------------------------------------------------------------
section("Nothing here may raise, and nothing here writes")

class Exploding:
    def __getattr__(self, _name):
        raise RuntimeError("boom")


exploded = _board(Exploding())
check("a store that explodes on every attribute still answers",
      exploded.get("measured") is False, repr(exploded))

# A naive datetime is what SQLite hands back and an aware one is what Postgres
# hands back, so a bare comparison raises on exactly one of the two backends --
# inside a dashboard render.
naive = _board(FakeStore(
    ACCOUNTS[:1],
    [{"customer_id": "1111111111", "client_name": "Acme Plumbing",
      "scanned_at": datetime.utcnow().isoformat(), "item_count": 1,
      "high_severity_count": 0, "error": "", "measured": True}]))
check("a naive stored timestamp does not raise on the comparison",
      naive.get("measured") is True, repr(naive.get("error")))
check("and an unparseable one is neither stale nor a crash",
      _board(FakeStore(
          ACCOUNTS[:1],
          [{"customer_id": "1111111111", "client_name": "A", "scanned_at": "soon",
            "item_count": 0, "high_severity_count": 0, "error": "",
            "measured": True}]))["counts"]["stale"] == 0)

_SRC = pathlib.Path(ROOT, "hub", "ads_status.py").read_text(encoding="utf-8")
for _writer in ("record_optimization_run", "set_auto_apply", "log_event",
                "apply_action", "scan_account", "set_report_schedule"):
    check(f"it never calls {_writer}()", f"{_writer}(" not in _SRC)


# ---------------------------------------------------------------------------
section("A count is never a link to a page that cannot show it")

urls = board.get("urls") or {}
check("every signal carries a URL", all(urls.get(k) for k in
                                        ("all",) + ads_status.FILTERS),
      repr(urls))

_PAGE = pathlib.Path(ROOT, "modules", "ads_builder", "templates",
                     "ads_optimization.html").read_text(encoding="utf-8")

# The parameter the page's own boot() reads. `?customer=` looks right, resolves,
# renders 200 and selects whichever account came first -- a link landing on the
# wrong client's scan with nothing reporting it.
check("a row opens its own account by the name the page actually reads",
      "customer_id=" in board["rows"][0]["url"]
      and "params.get('customer_id')" in _PAGE,
      board["rows"][0]["url"])

check("the page reads the filter the counts build",
      "params.get('filter')" in _PAGE or "get('filter')" in _PAGE)
for _f in ads_status.FILTERS:
    check(f"and knows the '{_f}' state by name", f"'{_f}'" in _PAGE)

# A filter that narrows without saying so is a short list read as the whole
# book -- the "N of M shown" rule, and one press back to all of it.
check("a narrowed panel says how many of how many",
      "of ' + rows.length" in _PAGE or "of ' + rows.length" in _PAGE)
check("and offers one press back to every account",
      "clearMonitorFilter" in _PAGE)

# The page and the card must not decide 'out of date' separately.
_MON = pathlib.Path(ROOT, "modules", "ads_builder",
                    "monitoring.py").read_text(encoding="utf-8")
check("the panel reads the tile's own overdue threshold rather than its own",
      "ads_status.overdue_after_minutes()" in _MON
      and "overdue_after_hours" in _PAGE)
check("and the threshold itself comes from the scheduler's registered cadence",
      "from hub.scheduler import JOBS" in _SRC and "JOBS[JOB][0]" in _SRC)

minutes, measured = ads_status.overdue_after_minutes()
check("which resolves for the job that actually runs the sweep",
      measured is True and minutes > 0, f"{minutes} {measured}")
check("and a cadence that could not be read is reported, not assumed",
      board.get("cadence_measured") is True
      and "cadence_measured" in _SRC)


# ---------------------------------------------------------------------------
section("The card draws it, and reads `measured` before it draws a number")

_DASH = pathlib.Path(ROOT, "hub", "templates",
                     "dashboard.html").read_text(encoding="utf-8")

check("the dashboard has the card", 'id="adsboard"' in _DASH)
check("and fetches the Hub route that serves it",
      "/api/ads/scoreboard" in _DASH)
check("it branches on measured before drawing a figure",
      "d.measured===false" in _DASH.replace(" ", "")
      .split("adsboard")[2].split("catch")[0],
      "the ads card draws counts straight from the payload")
check("it draws the named empty rather than four noughts",
      "no_accounts" in _DASH)
check("a fetch that fails says not measured rather than clean",
      "not measured rather than every account clean" in _DASH)

# The sweep, so the next card on this dashboard cannot drop the flag either.
# `test_qa_reports.py` sweeps the `_scorecard_*.html` partials; the inline
# cards were covered by a comment and by nothing else.
_INLINE = re.findall(r"fetch\('(/api/[^']+)'\)\.then\(r=>r\.json\(\)\)\.then\(d=>\{"
                     r"(.*?)\n\}\)\.catch", _DASH, re.S)
_blind = [path for path, blockp in _INLINE if "measured" not in blockp]
check("every inline dashboard card that fetches JSON reads `measured`",
      not _blind, ", ".join(_blind))
check("and the sweep actually found the cards", len(_INLINE) >= 3,
      f"only matched {len(_INLINE)}")

# A bubble whose key is not in the registry is removed client-side: the
# template reads as helped and the screen shows nothing.
from hub import help as hub_help                          # noqa: E402
_keys = {h.key for h in hub_help.REGISTRY}
for _placed in re.findall(r"help_dot\('([^']+)'\)", _DASH):
    check(f"the bubble {_placed} has an entry behind it", _placed in _keys)
check("including the one this card places", "hub.dashboard.ads" in _keys)


# ---------------------------------------------------------------------------
section("The real store answers the shape the card reads")
# Driven rather than asserted about source: a fake agreeing with a fake proves
# nothing about the table.

check("count_events counts rather than fetching and measuring the list",
      "func.count" in pathlib.Path(ROOT, "modules", "ads_builder",
                                   "store.py").read_text(encoding="utf-8"))

store.log_event(ads_status.AUTO_APPLIED_EVENT, "scheduler",
                client="Acme Plumbing", customer_id="1111111111")
store.log_event("SOMETHING_ELSE", "scheduler", client="Acme Plumbing")
check("it counts only the action it was asked for",
      store.count_events(ads_status.AUTO_APPLIED_EVENT) == 1,
      str(store.count_events(ads_status.AUTO_APPLIED_EVENT)))
check("and respects the window",
      store.count_events(ads_status.AUTO_APPLIED_EVENT,
                         since=datetime.now(timezone.utc) + timedelta(days=1)) == 0)

# The panel the counts link into has to keep answering when the Hub half does
# not import: losing the filter is better than losing the one panel that still
# has something to say when Google is down.
_real = monitoring._panel_filters
check("the panel's filter block never raises",
      isinstance(monitoring._panel_filters(), dict))
panel = monitoring.account_panel(limit=5)
check("and account_panel carries the threshold and the filter names",
      "overdue_after_hours" in panel and panel.get("filters") == list(ads_status.FILTERS),
      repr({k: panel.get(k) for k in ("overdue_after_hours", "filters")}))

# The live route, through the composed app, refusing a stranger: this names
# every client we advertise for and what is wrong with each.
os.environ.setdefault("SECRET_KEY", "test-only")
try:
    from hub import create_hub_app
    app = create_hub_app()
    with app.test_client() as c:
        r = c.get("/api/ads/scoreboard")
        check("the route refuses a request with no session",
              r.status_code in (301, 302, 401, 403),
              f"answered {r.status_code}")
except Exception as exc:                                  # noqa: BLE001
    check("the composed app boots for the route check", False, repr(exc))


print("\n" + ("ALL PASS" if not FAILED else f"{len(FAILED)} FAILED"))
for f in FAILED:
    print("  - " + f)
sys.exit(1 if FAILED else 0)
