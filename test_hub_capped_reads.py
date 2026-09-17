"""Readings in hub/ that have to be complete, and are no longer a window.

    python3 test_hub_capped_reads.py

No pytest, no new dependencies, a throwaway data directory.

The third file in the capped-read family, after ``test_reports_map_reads.py``
and ``test_ads_account_reads.py``. Same shape every time: a global read that
truncates, filtered or counted in Python, so the answer is correct until the
store passes the cap and then wrong about the OLDEST records only.

  * ``hub/image_audit._page_images`` called ``archive.recent(limit=2000)``.
    ``recent`` CLAMPS its limit to 1000, so the call asked for 2000, silently
    got 1000, and swept at most a fifth of the 5000 rows the archive keeps.
    The whole job of that sweep is to find images nothing else knows about,
    and a sweep that stops looking reports an orphan as filed -- while
    ``_attach_page_image`` reads the same file uncapped, so an image the audit
    said was not there attached perfectly well.
  * ``hub/audit.silent_modules`` decided "this module has never logged" from
    ``read(limit=5000)``. A module that logged steadily a year ago and has
    been quiet since -- a fair description of a seasonal tool -- was reported
    as never having logged at all. It is now a ``SELECT DISTINCT module``.
  * ``hub/help_center``'s personal inbox filtered the newest 2000 rows
    HUB-WIDE by actor. On a busy day that is a few hours, so somebody's own
    renders scrolled out of their own inbox. ``audit.read`` now narrows by
    actor in the query.

What it holds: each truncation reproduced rather than asserted from the
source, the complete reading asserted against it, and the bounded reads left
in place for the screens that page.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1hub_capped_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "hub-capped-reads-test"
os.environ.pop("CLOUDINARY_URL", None)

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import audit, jsonstore                                     # noqa: E402
from modules.page_image_optimizer import archive                     # noqa: E402

# ------------------------------------------------ the page-image archive
section("archive: recent() pages, all_rows() sweeps")

# More rows than recent() will ever hand back, whatever limit it is asked
# for. Newest first, which is the order save() writes in (rows.insert(0, ...)).
ROWS = 1200
jsonstore.write_json(archive.FALLBACK_ARCHIVE,
                     [{"public_id": f"img/{i:05d}", "company": "Acme Co",
                       "filename": f"shot-{i}.png", "url": f"https://x/{i}.png",
                       "saved_at": "2026-01-01T00:00:00Z", "page_name": "Home"}
                      for i in range(ROWS)])

check("recent() clamps to 1000 however large a limit is asked for",
      len(archive.recent(limit=2000)), 1000)
check("...so the old call asked for 2000 and got a thousand",
      len(archive.recent(limit=2000)) < ROWS)
check("...and the oldest row is not in it",
      "img/01199" in {r["public_id"] for r in archive.recent(limit=2000)}, False)
check("all_rows() is the whole archive", len(archive.all_rows()), ROWS)
check("...including the oldest row",
      "img/01199" in {r["public_id"] for r in archive.all_rows()})
check("all_rows() narrows by company like recent() does",
      len(archive.all_rows(company="Acme Co")), ROWS)
check("...and a company with nothing filed is empty, not everything",
      archive.all_rows(company="Nobody Inc"), [])
check("recent() still pages for the screen that pages",
      len(archive.recent(limit=25)), 25)

from hub import image_audit                                          # noqa: E402

swept = list(image_audit._page_images())
check("the audit sweeps every archived image, not a window of them",
      len(swept), ROWS)
check("...so the oldest image is reachable by the sweep that exists to find it",
      "img/01199" in {r["public_id"] for r in swept})


# ------------------------------------------------------- silent_modules
section("audit.silent_modules: a DISTINCT, not the newest N rows")

audit.log("seasonal_tool", "spot_recorded", detail="the one row this tool ever wrote")
NOISE = 60
for i in range(NOISE):
    audit.log("busy_tool", "render_submitted", detail=f"row {i}")

check("the quiet module's one row is older than the newest N",
      "seasonal_tool" in {e.get("module") for e in audit.read(limit=NOISE)}, False)
check("...so the old way would call it silent",
      "seasonal_tool" not in {e.get("module") for e in audit.read(limit=NOISE)})
check("modules_seen() knows it logged", "seasonal_tool" in audit.modules_seen())
check("silent_modules does not accuse it",
      audit.silent_modules(["seasonal_tool", "busy_tool"]), [])
check("a module that really never logged is still named",
      audit.silent_modules(["never_wired"]), ["never_wired"])
check("...alongside one that did", audit.silent_modules(["never_wired", "busy_tool"]),
      ["never_wired"])
check("an empty expectation is an empty answer", audit.silent_modules([]), [])


# ---------------------------------------------------- the actor narrowing
section("audit.read(actor=): one person's rows, narrowed in the query")

audit.log("fan_radio", "spot_recorded", actor="Quiet Person", detail="their only spot")
for i in range(NOISE):
    audit.log("display_ads", "ads_job_tracked", actor="Busy Person", detail=f"job {i}")

window = audit.read(limit=NOISE)
check("a hub-wide window of the newest rows is all the busy person's",
      {e.get("actor") for e in window} - {None, ""}, {"Busy Person"})
check("...so filtering it by the quiet person finds nothing",
      [e for e in window if e.get("actor") == "Quiet Person"], [])
mine = audit.read(limit=NOISE, actor="Quiet Person")
check("narrowing by actor finds their row", len(mine), 1)
check("...and it is theirs", mine[0]["actor"], "Quiet Person")
check("actor combines with module", len(audit.read(limit=50, actor="Quiet Person",
                                                   module="fan_radio")), 1)
check("...and with a module they never touched",
      audit.read(limit=50, actor="Quiet Person", module="display_ads"), [])
check("an actor nobody logged under is empty, not everybody",
      audit.read(limit=50, actor="Nobody At All"), [])
check("no actor is still everybody", len(audit.read(limit=5)), 5)
check("tail() carries the same narrowing",
      len(audit.tail(limit=NOISE, actor="Quiet Person")), 1)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
