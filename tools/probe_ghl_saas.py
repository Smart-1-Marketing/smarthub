"""What GoHighLevel's SaaS Configurator actually returns.

    python3 tools/probe_ghl_saas.py            # first page, raw, plus the statuses
    python3 tools/probe_ghl_saas.py --all      # walk every page for the statuses

Read-only. It makes the same two GET calls `hub/qa.py`'s Suite billing
reports make -- `/saas/saas-locations/{company}` and `/saas/agency-plans/
{company}` -- prints the raw JSON of the first locations page, and then the
DISTINCT set of subscription status strings actually present, with a count
each. It asserts nothing and writes nothing.

Two assumptions in those reports have never been checked against a live
answer: the shape of the v3 page (`locations` / `pagination.hasNext` /
`subscriptionInfo.subscriptionStatus`) and whether `active`, `trialing` and
`past_due` are the strings GHL sends. One run of this answers both. Anything
the reports would not recognize is flagged here in the same words the report
uses, so the fix -- adding the string to `_ACTIVE_SUB_STATUSES` or
`_INACTIVE_SUB_STATUSES` in hub/qa.py -- is one line.

Needs GHL_PRIVATE_TOKEN (an Agency-level Private Integration Token with the
SaaS Configurator scope) and GHL_COMPANY_ID in the environment, exactly as
the Hub reads them. The token is never printed.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "probe_ghl_saas.db"))
os.environ.setdefault("SECRET_KEY", "probe")
os.environ.setdefault("PANEL_PASSWORD", "probe")


def _status_of(loc: dict) -> str:
    info = loc.get("subscriptionInfo") or {}
    return str(info.get("subscriptionStatus")
               or loc.get("subscriptionStatus") or "").strip().lower()


def main(argv: list[str]) -> int:
    from hub import qa
    from hub.config import settings

    company = settings.ghl_company_id
    if not settings.ghl_token or not company:
        names = settings.spellings("ghl_token"), settings.spellings("ghl_company_id")
        print(f"Not configured: set {names[0]} and {names[1]} before running this.")
        return 2

    walk_all = "--all" in argv
    print(f"GET /saas/saas-locations/{company}  (Version: {qa.GHL_SAAS_VERSION})")
    try:
        first = qa._ghl_saas(f"/saas/saas-locations/{company}", {"page": 1})
    except RuntimeError as exc:
        print(f"  refused: {exc}")
        return 1

    print("\n--- first page, raw ---")
    print(json.dumps(first, indent=2, sort_keys=True)[:20000])

    locs = first.get("locations") if isinstance(first, dict) else first
    locs = locs or []
    pagination = (first.get("pagination") or {}) if isinstance(first, dict) else {}
    print("\n--- shape ---")
    print(f"  top-level keys: {sorted(first) if isinstance(first, dict) else type(first).__name__}")
    print(f"  locations on page 1: {len(locs)}")
    print(f"  pagination block: {pagination!r}")
    if locs:
        print(f"  keys on a location: {sorted(locs[0])}")
        print(f"  keys on subscriptionInfo: {sorted((locs[0].get('subscriptionInfo') or {}))}")

    if walk_all:
        try:
            locs = qa._ghl_saas_locations()
            print(f"\n  walked every page: {len(locs)} locations")
        except RuntimeError as exc:
            print(f"\n  could not walk every page: {exc}")

    statuses = Counter(_status_of(l) or "(blank)" for l in locs)
    print("\n--- distinct subscription statuses ---")
    for status, n in statuses.most_common():
        if status in qa._ACTIVE_SUB_STATUSES:
            verdict = "counted as billing"
        elif status in qa._INACTIVE_SUB_STATUSES:
            verdict = "left out as not billing"
        else:
            verdict = "NOT RECOGNIZED -- the reports would name and exclude this"
        print(f"  {status:<22} {n:>5}   {verdict}")

    print(f"\nGET /saas/agency-plans/{company}")
    try:
        plans = qa._ghl_agency_plans()
        print(f"  {len(plans)} plans: " + ", ".join(
            f"{p.get('title') or pid} (${qa._plan_monthly_price(p):,.0f}/mo)"
            for pid, p in plans.items()))
    except RuntimeError as exc:
        print(f"  refused: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
