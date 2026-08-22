"""Run the Hub's own /api/integrity self-check from the command line.

The Hub already knows how to find its known defect patterns — a PDF uploaded
as an image type, a convert with no resize, a route shadowed by a mount, JSON
written to the disk with no database mirror. Every one of those was a real
shipped bug at least once. The check lived behind a login on a page somebody
had to remember to open, which is the same as not having it.

Severity decides what fails the run:

  high    -> fails. These are the patterns that broke production outright.
             `shadowed_routes` is the mount trap CLAUDE.md opens with.
  medium  -> reported, does not fail. Two are outstanding today (ad_builder
  low        and msa never write to the activity log), and a check that is
             red the day it is switched on is a check people learn to ignore.
             --strict fails on these too, for when they are cleared.

    python tools/integritycheck.py            # report, exit 1 on any high
    python tools/integritycheck.py --strict   # exit 1 on anything at all
    python tools/integritycheck.py --quiet    # only the summary line
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Same isolation as linkcheck.py and pagecheck.py: booting for real is the
# point, touching the live database is not.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "integritycheck.db"))
os.environ.setdefault("SECRET_KEY", "integritycheck")
os.environ.setdefault("PANEL_PASSWORD", "integritycheck")


def main():
    quiet = "--quiet" in sys.argv
    strict = "--strict" in sys.argv

    import wsgi
    from werkzeug.test import Client

    client = Client(wsgi.application)
    r = client.post("/login", data={
        "email": "integrity@smart1marketing.com",
        "password": os.environ["PANEL_PASSWORD"], "next": "/"})
    if r.status_code not in (302, 303):
        raise SystemExit(f"could not sign in (/login returned {r.status_code})")

    r = client.get("/api/integrity")
    if r.status_code != 200:
        raise SystemExit(f"/api/integrity returned {r.status_code}")
    data = r.get_json() or {}

    blocking, reported = 0, 0
    for group in data.get("groups", []):
        count = int(group.get("count") or 0)
        severity = str(group.get("severity") or "low")
        if not count:
            if not quiet:
                print(f"  ok    [{severity:6s}] {group.get('label')}")
            continue
        fails = severity == "high" or strict
        blocking += count if fails else 0
        reported += count if not fails else 0
        head = "FAIL " if fails else "note "
        print(f"  {head} [{severity:6s}] {group.get('label')} — {count} finding(s)")
        for f in group.get("findings", [])[:20]:
            where = f.get("file") or f.get("module") or ""
            print(f"          {where}: {f.get('detail','')}")
            if f.get("fix"):
                print(f"          fix: {f['fix']}")

    print()
    if blocking:
        print(f"{blocking} finding(s) at a severity that fails this run.")
        return 1
    if reported:
        print(f"0 blocking findings; {reported} reported (medium/low) — see above.")
    else:
        print("0 findings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
