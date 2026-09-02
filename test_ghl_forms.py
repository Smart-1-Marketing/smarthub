"""The Client 360 forms card: whose forms, and what an empty one means.

This module had no test at all, which is how three failures survived in it --
each of which rendered as a card with nothing in it, so the whole feature read
as "not working yet" rather than as a bug anybody would go and look for.

  * The card asked for forms with no sub-account and fell back to
    GHL_LEAD_LOCATION_ID -- which config.py describes as the sub-account
    "leads are written into", meaning Smart 1's own. Client 360 passes no
    location, so every client's card was answered with the AGENCY's form
    submissions under that client's name. Not an empty card: a wrong one,
    wrong identically for every client.

  * A form whose submission count raised was dropped with `continue` placed
    BEFORE the skipped tally, so it left no trace. When every form failed the
    card said "No form submissions in <month>" and reported nothing at all.

  * A previous period that could not be counted was recorded as 0, which
    prints "14 vs 0" and an up-arrow over a comparison that never happened.

Nothing here reaches Smart 1 Suite: _get is stubbed, so what is asserted is
what this module does with each answer.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import date

TMP = tempfile.mkdtemp(prefix="ghlforms-")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "forms-test-secret")

# The agency's own lead sub-account -- the value summary() used to fall back
# to. It MUST be set before hub is imported: hub.config.settings is a frozen
# dataclass built once at import, so setting it later leaves the field "" and
# every assertion about the fallback passes because there was no fallback
# value to reach. That is how the first draft of this file reported the
# agency-location bug as fixed while it was still live.
AGENCY_LOC = "agency-lead-location"
os.environ["GHL_LEAD_LOCATION_ID"] = AGENCY_LOC
os.environ["GHL_PRIVATE_TOKEN"] = "pit-test-token"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hub import ghl_forms                                    # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# --------------------------------------------------------------- whose forms
section("A form belongs to one sub-account, and never to a default")

# Proof the fallback value is really in play, so the assertions below are
# about the code and not about an empty setting.
from hub.config import settings as _settings                 # noqa: E402
check("the agency fallback value is actually configured",
      _settings.ghl_lead_location_id, AGENCY_LOC)

_asked_for: list[str] = []


def _fake_get(path, params):
    _asked_for.append(str(params.get("locationId") or ""))
    if path == "/forms/":
        return {"forms": [{"id": "f1", "name": "Contact us"}]}
    return {"meta": {"total": 3}}


ghl_forms._get = _fake_get                                   # type: ignore[assignment]

# No sub-account recorded for this client -- which is every client today.
# summary() imports location_for INSIDE the function, so patching the module
# attribute here is what the call actually resolves to.
import hub.suite_accounts as _sa                             # noqa: E402


def _no_mapping(name, url=""):
    return {"state": "not_connected", "location_id": "",
            "detail": "No Smart 1 Suite sub-account is recorded for this "
                      "client. It is set on their row in Client Image Uploads."}


_sa.location_for = _no_mapping                               # type: ignore[assignment]

_asked_for.clear()
out = ghl_forms.summary("Icon Solar", period="this_month")
check("an unmapped client gets an error, not a card", bool(out.get("error")), True)
check("and it is not measured", out.get("measured"), False)
check("it says the sub-account is not recorded",
      "sub-account" in (out.get("error") or ""), True)
# The load-bearing one: no request may be made against the agency's location.
check("nothing was asked of the agency's own location",
      AGENCY_LOC in _asked_for, False)
check("in fact nothing was asked at all", _asked_for, [])

# With the sub-account recorded, that is the location asked about.
def _mapped(name, url=""):
    return {"state": "connected", "location_id": "loc-icon-solar", "detail": ""}


_sa.location_for = _mapped                                   # type: ignore[assignment]
_asked_for.clear()
out = ghl_forms.summary("Icon Solar", period="this_month")
check("a mapped client resolves to their own sub-account",
      set(_asked_for), {"loc-icon-solar"})
check("and the card has rows", len(out.get("forms") or []), 1)

# A caller naming a location explicitly still wins.
_asked_for.clear()
ghl_forms.summary("Icon Solar", "loc-explicit", "this_month")
check("an explicit location overrides the lookup",
      set(_asked_for), {"loc-explicit"})

# "We could not look" is its own answer.
def _unreadable(name, url=""):
    return {"state": "not_measured", "location_id": "",
            "detail": "The client-to-sub-account mapping could not be read."}


_sa.location_for = _unreadable                               # type: ignore[assignment]
out = ghl_forms.summary("Icon Solar", period="this_month")
check("a mapping we could not read is not 'not recorded'",
      out.get("state"), "not_measured")


# ------------------------------------------------- a failure is not a zero
section("A count that failed is never a period with nothing in it")

_sa.location_for = _mapped                                   # type: ignore[assignment]


def _all_counts_fail(path, params):
    if path == "/forms/":
        return {"forms": [{"id": "f1", "name": "Contact us"},
                          {"id": "f2", "name": "Quote request"}]}
    raise RuntimeError("Smart 1 Suite returned HTTP 500.")


ghl_forms._get = _all_counts_fail                            # type: ignore[assignment]
out = ghl_forms.summary("Icon Solar", period="this_month")
check("every form unreadable is an error, not an empty period",
      bool(out.get("error")), True)
check("and it is not measured", out.get("measured"), False)
check("it says how many could not be read", out.get("unreadable"), 2)


def _one_fails(path, params):
    if path == "/forms/":
        return {"forms": [{"id": "ok", "name": "Contact us"},
                          {"id": "bad", "name": "Quote request"}]}
    if params.get("formId") == "bad":
        raise RuntimeError("Smart 1 Suite returned HTTP 500.")
    return {"meta": {"total": 5}}


ghl_forms._get = _one_fails                                  # type: ignore[assignment]
out = ghl_forms.summary("Icon Solar", period="this_month")
check("one unreadable form does not cost the readable ones",
      len(out.get("forms") or []), 1)
check("the unreadable one is counted rather than dropped",
      out.get("unreadable"), 1)
check("and the note says the totals are incomplete",
      "could not be read" in (out.get("note") or ""), True)
check("a partial read is not reported as measured", out.get("measured"), False)


# ------------------------------------------------ an unmeasured baseline
section("A baseline nobody could read is not a baseline of zero")

# Guarded: the version this replaces does `now - before` on None and raises,
# and an assertion that raises takes every check after it out of the run --
# so a regression here would hide the rest of the file rather than name
# itself.
def _delta_or_raised(now, before):
    try:
        return ghl_forms._delta(now, before)
    except Exception as exc:                                 # noqa: BLE001
        return {"direction": f"raised {type(exc).__name__}",
                "percent": f"raised {type(exc).__name__}",
                "text": f"raised {type(exc).__name__}"}


d = _delta_or_raised(14, None)
check("an unknown previous period draws no direction", d["direction"], "flat")
check("and no percentage", d["percent"], None)
check("and does not print 'vs 0'", "vs 0" in str(d["text"]), False)
check("a genuine zero baseline still says so",
      "vs 0" in str(_delta_or_raised(14, 0)["text"]), True)

# The aggregate follows the rows: one unmeasured baseline makes the total's
# baseline unmeasured too, rather than comparing against a smaller sum and
# reporting a rise that is an artifact of the failure.
def _baseline_fails(path, params):
    if path == "/forms/":
        return {"forms": [{"id": "f1", "name": "Contact us"}]}
    start = str(params.get("startAt") or "")
    if start < date.today().replace(day=1).isoformat():
        raise RuntimeError("Smart 1 Suite returned HTTP 500.")
    return {"meta": {"total": 9}}


ghl_forms._get = _baseline_fails                             # type: ignore[assignment]
try:
    out = ghl_forms.summary("Icon Solar", period="this_month")
except Exception as exc:                                     # noqa: BLE001
    out = {"raised": f"{type(exc).__name__}"}
check("a row whose baseline failed carries None, not 0",
      (out.get("forms") or [{}])[0].get("previous"), None)
check("and the total's baseline is unmeasured too",
      out.get("total_previous"), None)
check("so the headline claims no comparison",
      "vs 0" in (out.get("total_text") or ""), False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
