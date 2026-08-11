"""
Auto-seed the portfolio on boot.

If the projects table is empty AND a committed seed file exists, import it once.
This means a brand-new or freshly-recreated database (e.g. after a free-tier
Postgres is deleted and remade) repopulates itself on the next startup with no
manual paste/upload.

Safe to call on every boot:
  - does nothing once any projects exist (idempotent),
  - imports are upserts keyed on project id (no duplicates),
  - never raises; a failure just logs and lets the app start normally.

Seed file location (first match wins):
  1. $SEED_FILE if set
  2. <folder containing app.py>/seed/portfolio.json
Drop your management-panel list-projects JSON export at seed/portfolio.json.
"""
import json
import os

from db import known_project_ids
from sync_service import import_inventory

_DEFAULT_SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "portfolio.json")
SEED_PATH = os.getenv("SEED_FILE", _DEFAULT_SEED)


def seed_if_empty() -> None:
    try:
        if known_project_ids():
            return  # already populated — nothing to do
        if not os.path.exists(SEED_PATH):
            print(f"[seed_boot] projects empty and no seed file at {SEED_PATH}; skipping", flush=True)
            return
        with open(SEED_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        count = import_inventory(payload)
        print(f"[seed_boot] projects table was empty — seeded {count} projects from {SEED_PATH}", flush=True)
    except Exception as exc:  # never block startup on a seed problem
        print(f"[seed_boot] skipped seeding ({exc})", flush=True)
