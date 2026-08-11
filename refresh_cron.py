#!/usr/bin/env python3
"""
Scheduled refresher for Smart 1 Sites Admin.

Runs from a Render Cron Job. For every project ID already in the local
registry, it re-pulls that project's live details (status, plan, billing
dates, sites, domains) from Simvoly's official Platform API and upserts
them into the database.

It is READ-ONLY against Simvoly (GET calls only) and never creates,
changes, or deletes anything on the platform. Write actions are not needed
and stay off.

It does NOT discover new project IDs — Simvoly's documented API has no
reseller-wide "list all projects" endpoint. New projects enter the registry
when they are created through this app (Add Site) or via Inventory Import /
Discover Customer. This job keeps whatever is already known up to date.

Usage:
    python refresh_cron.py

Environment (already set on the web service; the cron job needs the same):
    DATABASE_URL          Render Postgres connection string (required)
    SIMVOLY_API_BASE_URL  e.g. https://api.smart1sites.com
    SIMVOLY_API_KEY       Platform API key (X-CLIENT-KEY)
    MOCK_MODE             false
    REFRESH_LIMIT         Optional int; refresh only the first N IDs (batching)
"""
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from sync_service import refresh_known_projects


def main() -> int:
    raw_limit = os.getenv("REFRESH_LIMIT", "").strip()
    limit = int(raw_limit) if raw_limit.isdigit() and int(raw_limit) > 0 else None

    started = time.time()
    print(f"[refresh_cron] starting refresh (limit={limit or 'all'})", flush=True)

    try:
        out = refresh_known_projects(limit=limit)
    except Exception as exc:  # total failure (e.g. DB or API unreachable)
        print(f"[refresh_cron] FATAL: {exc}", file=sys.stderr, flush=True)
        return 1

    elapsed = time.time() - started
    print(
        f"[refresh_cron] done in {elapsed:.0f}s — "
        f"refreshed {out['ok']}, failed {out['failed']}",
        flush=True,
    )
    for err in out.get("errors", []):
        print(f"[refresh_cron]   error: {err}", file=sys.stderr, flush=True)

    # Mark the run red only if nothing succeeded but something was attempted,
    # so a few transient per-project errors don't fail the whole job while a
    # real outage (0 ok, N failed) does.
    if out["ok"] == 0 and out["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
