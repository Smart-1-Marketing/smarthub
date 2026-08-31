"""hub/jsonstore.py — test harness.

    python3 test_jsonstore.py

Same shape as test_ads_module.py: no pytest, no new dependencies, runs against
a temporary data directory and a throwaway SQLite mirror so it never touches
/var/data or the real database.

## Why this file exists

The point of hub/jsonstore.py is that a file survives losing the disk. That is
not something you can confirm by looking at a page: the tools render exactly
the same whether the mirror is working or silently off, which is the whole
reason the original problem went unnoticed. Clicking through after a deploy
proves the pages still load — it cannot prove the backup is real.

So each check below destroys something and asserts the data came back:

  1.  round trip                     — write, read, same value
  2.  a lost file                    — delete it, read it, it is restored
  3.  a recreated disk               — wipe everything, boot, all files return
  4.  a second boot                  — restore does not re-run or duplicate
  5.  delete_json                    — file AND mirror go, no resurrection
  6.  os.remove is the trap          — proves why 5 has to exist
  7.  durable=False                  — a cache is not mirrored
  8.  restore=False                  — a stale cache is never handed back
  9.  database down                  — saves still work, and stay fast
  10. database wakes                 — mirroring resumes on its own
  11. same-disk detection            — the mirror-on-/var/data trap is caught
  12. oversized payload              — reported, never silently truncated
  13. path parity                    — the tools whose storage moved
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1jsonstore_test_")
DISK = os.path.join(TMP, "disk")          # stands in for /var/data
MIRROR = os.path.join(TMP, "mirror.sqlite3")   # stands in for Render Postgres
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + MIRROR

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


def reset_module():
    """Reload jsonstore with its module-level connection state cleared.

    The engine, the circuit breaker and the "already initialised" flag are all
    module globals, so a test that takes the database away has to start from a
    clean import or it inherits the previous test's open connection.
    """
    for name in [m for m in sys.modules if m.startswith("hub")]:
        del sys.modules[name]
    from hub import jsonstore
    return jsonstore


# ---------------------------------------------------------------- 1. round trip
section("Round trip")
js = reset_module()
p = os.path.join(js.data_dir("fan_radio", "projects"), "p1.json")
js.write_json(p, {"title": "Spot A", "versions": [1, 2]}, indent=1)
check("value reads back", js.read_json(p), {"title": "Spot A", "versions": [1, 2]})
check("file is on disk", os.path.isfile(p), True)
check("mirror holds it", js.status()["blobs"] >= 1, True)
check("no .tmp left behind", os.path.exists(p + ".tmp"), False)


# ------------------------------------------------------------- 2. a lost file
section("A single file is lost")
os.remove(p)
check("gone from disk", os.path.exists(p), False)
check("read still returns it", js.read_json(p), {"title": "Spot A", "versions": [1, 2]})
check("and put it back on disk", os.path.isfile(p), True)


# ------------------------------------------------------- 3. a recreated disk
section("The whole disk is recreated")
js.stamp_generation()
paths = {}
for rel, payload in [
    ("seo/acme.json", {"pages": {"/": {"@type": "LocalBusiness"}}}),
    ("house_clients.json", [{"slug": "house-one"}]),
    ("quickbooks_tokens.json", {"refresh_token": "rt-123"}),
    ("utm/links.json", [{"url": "https://x.test"}]),
]:
    full = os.path.join(DISK, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    js.write_json(full, payload)
    paths[rel] = (full, payload)

shutil.rmtree(DISK)                       # Render hands back an empty volume
os.makedirs(DISK, exist_ok=True)
js = reset_module()                       # ...and the workers boot fresh

check("disk is detected as new", js.disk_is_fresh(), True)
out = js.maybe_restore()
check("restore ran", out.get("ran"), True)
check("nothing failed", out.get("failed", 0), 0)
for rel, (full, payload) in paths.items():
    check(f"recovered {rel}", js.read_json(full), payload)


# --------------------------------------------------------- 4. an ordinary boot
section("The next ordinary boot")
js = reset_module()
check("disk no longer looks new", js.disk_is_fresh(), False)
check("restore does not re-run", js.maybe_restore().get("ran"), False)


# ------------------------------------------------------------- 5. delete_json
section("delete_json removes both copies")
target = paths["quickbooks_tokens.json"][0]
js.delete_json(target)
check("file removed", os.path.exists(target), False)
check("does not come back", js.read_json(target, default="GONE"), "GONE")


# ------------------------------------------------------- 6. why 5 has to exist
section("os.remove alone is the trap delete_json exists for")
victim = os.path.join(DISK, "trap.json")
js.write_json(victim, {"deleted": "by hand"})
os.remove(victim)                         # the mistake
check("the mirror resurrects it", js.read_json(victim, default="GONE"),
      {"deleted": "by hand"})
js.delete_json(victim)
check("delete_json is the fix", js.read_json(victim, default="GONE"), "GONE")


# ------------------------------------------------------------ 7. durable=False
section("A declared cache is not mirrored")
cache = os.path.join(js.data_dir("knack-cache"), "products.json")
js.write_json(cache, {"rows": [1, 2, 3]}, durable=False)
check("still written to disk", js.read_json(cache), {"rows": [1, 2, 3]})
check("listed as a known gap", js.key_for(cache) in js.status()["declared_caches"], True)
os.remove(cache)
check("not restorable, by design", js.read_json(cache, default="GONE"), "GONE")


# ----------------------------------------------------------- 8. restore=False
section("restore=False never hands back a stale cache")
stale = os.path.join(DISK, "stale.json")
js.write_json(stale, {"fetched": 111})    # mirrored
os.remove(stale)
check("a plain read would restore it", js.read_json(stale) is not None, True)
os.remove(stale)
check("restore=False refuses to", js.read_json(stale, default="GONE", restore=False),
      "GONE")


# --------------------------------------------------------- 9. database is down
section("The database is unreachable")
os.environ["DATABASE_URL"] = "postgresql://nobody:nope@127.0.0.1:59999/missing"
js = reset_module()
# Set before the first call: _init() stamps its retry deadline using whatever
# INIT_RETRY_SECONDS was at the moment it failed, so lowering it afterwards
# leaves the original two-minute wait in place.
js.INIT_RETRY_SECONDS = 1
check("mirror reports unavailable", js.available(), False)

down = os.path.join(DISK, "during_outage.json")
started = time.time()
for i in range(5):
    js.write_json(down, {"i": i})
elapsed = time.time() - started
check("saves still succeed", js.read_json(down), {"i": 4})
check("and stay fast (no per-save timeout)", elapsed < 2.0, True)
st = js.status()
check("blobs reported unknown, not 0", st["blobs"], None)


# -------------------------------------------------------- 10. database wakes
section("The database comes back")
import hub.extensions as _ext
_ext.database_url = lambda: "sqlite:///" + MIRROR
check("still cached as down", js.available(), False)
time.sleep(1.1)
js.write_json(down, {"i": "after-wake"})
check("mirroring resumed", js.available(), True)
os.remove(down)
check("and the file is recoverable again", js.read_json(down), {"i": "after-wake"})


# ---------------------------------------------- 11. mirror on the same disk
section("The mirror must not live on the disk it protects")
js = reset_module()
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(DISK, "hub.sqlite3")
js = reset_module()
js.write_json(os.path.join(DISK, "sd.json"), {"a": 1})
check("detected", js.mirror_is_on_the_same_disk(), True)
check("surfaced in status()", js.status()["same_disk"], True)
from hub import diagnostics
check("diagnostics calls it an error", diagnostics.check_json_backup().state, "error")

os.environ["DATABASE_URL"] = "sqlite:///" + MIRROR
js = reset_module()
check("a separate database is fine", js.mirror_is_on_the_same_disk(), False)


# ------------------------------------------------------------- 12. oversized
section("An oversized file is reported, never truncated")
js.MAX_MIRROR_BYTES = 2048
big = os.path.join(DISK, "big.json")
js.write_json(big, {"rows": ["x" * 100 for _ in range(100)]})
check("still written to disk in full", len(js.read_json(big)["rows"]), 100)
report = js.status()["error"]
check("says which file", js.key_for(big) in report, True)
check("says it was NOT backed up", "NOT backed up" in report, True)
# Parsed, not substring-matched: "0 KB, over" is a substring of "10 KB, over".
_m = re.search(r"is (\d+) KB, over the (\d+) KB", report)
check("states a real size", bool(_m) and int(_m.group(1)) > 0, True)
check("and the real cap", bool(_m) and int(_m.group(2)) == 2, True)
os.remove(big)
check("and honestly unrecoverable", js.read_json(big, default="GONE"), "GONE")
js.MAX_MIRROR_BYTES = 8 * 1024 * 1024


# ----------------------------------------------------------- 13. path parity
section("Storage locations of the tools whose paths changed")
# Under production conditions the data root is the mounted disk. A tool whose
# resolved directory is NOT under it is still writing somewhere ephemeral —
# which is the bug this change fixed in three places, and the one most likely
# to come back the next time somebody adds a module.
root = os.path.abspath(js.data_root())


def under_root(path):
    return os.path.abspath(str(path)).startswith(root)


from modules.page_image_optimizer import store as pio_store
from modules.page_image_optimizer import archive as pio_archive
from modules.tickets import config as tickets_config

check("page image optimizer jobs", under_root(pio_store.DATA_DIR), True)
check("page image optimizer archive", under_root(pio_archive.FALLBACK_ARCHIVE), True)
check("tickets field map", under_root(tickets_config.fieldmap_path()), True)
check("proposal builder", under_root(js.data_dir("proposal-builder")), True)
# boat and restaurant resolve their directories at import through jsonstore;
# assert the helper they now use rather than importing two large Flask apps.
check("boat reports", under_root(js.data_dir("boat-reports")), True)
check("restaurant reports", under_root(js.data_dir("restaurant-reports")), True)


# --------------------------------------- 14. one answer to the unbacked-JSON question
section("Who still writes JSON without the mirror — asked once")
# /api/db/structure and /api/integrity both report this, on the same
# Diagnostics page, and they disagreed: structure counted build scripts and
# integrity did not, so the panel read "1 file writes JSON outside
# hub/jsonstore.py — ad_builder" directly above an audit of the same question
# that had found nothing. The file was a one-off script rewriting layout JSON
# committed to the repo; ad_builder is the Node renderer and keeps no Python
# state on the data disk at all. Both read hub/jsonstore.py now, so the way
# this comes back is somebody re-growing a second copy of the rule.
import importlib

REPO = Path(__file__).resolve().parent
js_scan = importlib.import_module("hub.jsonstore")
integrity = importlib.import_module("hub.integrity")
client_context = importlib.import_module("hub.client_context")

shared = {h["file"] for h in js_scan.unmirrored_json_writers(REPO)}
audit = {f["file"] for f in integrity.check_unbacked_json()}
check("the audit reports what the shared rule found", audit <= shared, True)
check("and reports all of it bar its own reporters",
      shared - audit <= set(integrity.SELF), True)

# The structure report is the half that was wrong. Assert the count it puts on
# screen is the shared rule's, not a second reading of the source.
report = client_context.structure_report()
check("the structure panel counts the same files",
      report["json_stores"], len(shared))

# The specific false positive, named: a build script is not a data store.
script = "modules/ad_builder/scripts/fix_safezones.py"
check("the build script exists to be excluded", (REPO / script).is_file(), True)
check("and is excluded, with a reason",
      bool(js_scan._unmirrored_exempt_reason(script)), True)
check("so ad_builder is not named as a JSON store",
      any(h["module"] == "ad_builder" for h in js_scan.unmirrored_json_writers(REPO)),
      False)

# A scanner used to exempt itself only because its own explanatory text
# happened to contain the word "jsonstore". Reword the string and it starts
# reporting itself. Both are named outright now.
for reporter in ("hub/integrity.py", "hub/client_context.py"):
    check(f"{reporter} is exempt by name, not by wording",
          reporter in js_scan.UNMIRRORED_EXEMPT, True)

# An exemption that outlives its file goes on covering whatever is written at
# that path next, while the audit stays green doing it. The list this replaced
# had exactly that shape.
check("no exemption names a file that is gone",
      js_scan.stale_exemptions(REPO), [])
check("and the audit says so too", integrity.check_stale_json_exemptions(), [])


# ------------------------------- 15. a resolved risk is not an amber finding
section("The structure panel's own colors")
# The client-key row is the *resolved* case: the columns still differ and
# hub/client_key.py joins them on read. renderStructure() painted every
# non-high level amber, so "this is handled, and here is what handles it" sat
# in the same colour as a real finding — which is how a panel teaches people to
# skim past the rows that matter.
page = (REPO / "hub" / "templates" / "diagnostics.html").read_text()
check("low renders neutral, not amber",
      'level==="low" ? "off"' in page, True)
check("and the header pill ignores resolved rows",
      'var open=d.risks.filter(function(r){ return r.level!=="low"; });' in page, True)
levels = {r["level"] for r in report["risks"]}
check("nothing above low is outstanding", levels - {"low"}, set())


# =====================================================================
section("A fresh data directory is not isolation on its own")
# =====================================================================
# The harness-level version of the trap above. A test file that assigns its
# own HUB_DATA_DIR looks isolated and is not, if it merely *setdefault*s
# DATABASE_URL: `key_for()` keys the mirror relative to the data root by
# design -- that is what lets a production blob restore into a dev checkout --
# so an inherited database (CI's Postgres, or a developer's own) refills the
# new empty directory with the last run's rows.
#
# This is not hypothetical and it is not new. checks.yml carries a paragraph
# headed "RUN THIS FILE EXACTLY ONCE" recording that two lineages each added
# a target-areas step, git merged both cleanly, and the duplicate run failed
# on the first run's rows. That mitigation is a comment asking people to
# remember; this is the same rule as a property of the files.
#
# The rule is narrow on purpose. Setting neither is fine (the file inherits
# both and is consistent). Setting both is the pattern test_blog_publish.py
# uses. Only "own directory, inherited database" is wrong, because only that
# pair gives you an empty disk in front of a full mirror.
#
# And the first version of this sweep asked for that pair by the SPELLING
# rather than by what the file ends up with: `HUB_DATA_DIR` assigned and
# `DATABASE_URL` not. A file that setdefaults *both* reaches the identical
# state whenever only the database is set in the environment -- fresh
# directory, inherited mirror -- and was invisible to the check written for
# it. Two were: test_io_records.py and test_sales_status.py, each passing on
# the first run against a database and failing on every run after, which is
# why CI never saw either (a CI run gets a new Postgres) and why nobody
# else did until a session-start hook began exporting DATABASE_URL for a
# whole session. Both are read now.

import ast as _ast                                                # noqa: E402

def _env_writes(tree):
    """{name: "assign" | "setdefault"} for os.environ at module level."""
    out = {}
    for node in _ast.walk(tree):
        if (isinstance(node, _ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], _ast.Subscript)
                and _ast.unparse(node.targets[0].value) == "os.environ"
                and isinstance(node.targets[0].slice, _ast.Constant)):
            out.setdefault(node.targets[0].slice.value, "assign")
        if (isinstance(node, _ast.Call)
                and isinstance(node.func, _ast.Attribute)
                and node.func.attr == "setdefault"
                and _ast.unparse(node.func.value) == "os.environ"
                and node.args and isinstance(node.args[0], _ast.Constant)):
            out.setdefault(node.args[0].value, "setdefault")
    return out

_ROOT = Path(__file__).parent
_offenders, _checked = [], 0
for _f in sorted(_ROOT.glob("test_*.py")):
    try:
        _w = _env_writes(_ast.parse(_f.read_text(errors="ignore")))
    except SyntaxError:
        continue
    # Either spelling gives this run its own directory when HUB_DATA_DIR is
    # unset, which is the ordinary case; only assigning DATABASE_URL gives it
    # its own mirror.
    if _w.get("HUB_DATA_DIR") not in ("assign", "setdefault"):
        continue
    _checked += 1
    if _w.get("DATABASE_URL") != "assign":
        _offenders.append(_f.name)

# Being in the shape is not the same as breaking, and a check that failed on
# every file in it would land with two dozen findings nobody can safely act
# on -- the one people learn to skip past, which is the note
# `provider_key_drift` already carries about going in red. Several of these
# boot the composed app, and CI runs Postgres precisely because Sites Admin
# refuses to start on SQLite and drops out of every check that boots it, so
# "fixing" them by pinning SQLite would make the gate quietly cover less.
#
# So the shape is held against a list of files that were *run twice against
# one database* and came back identical -- they write nothing durable, or
# overwrite what they read before reading it. That is the whole verification,
# and it is the reason the list can be trusted rather than assumed: the note
# this replaces asserted the same thing about thirteen files and was wrong
# about two of them, because nobody had run them twice.
#
# A file in the shape and not on this list fails. Own DATABASE_URL as well
# (the test_blog_publish.py pattern), or run the suite twice against one
# database, confirm it is identical, and add it here.
#
# The list was twenty-three files and is now one. That is not the check
# finding less: this PR gave the other twenty-two their own database, which
# takes them out of the shape entirely and so retires their entries -- the
# staleness check below is what requires that pruning rather than leaving an
# exemption standing over a file it no longer describes.
_RERUNS_CLEAN = (
    # Left in the shape deliberately, and measured rather than assumed:
    # 215 assertions with DATABASE_URL inherited, 211 with SQLite
    # pinned. Sites Admin refuses to start on SQLite and takes its four
    # assertions out of this file with it -- which is the loss the note
    # above predicts, so this one keeps setdefault and is listed here.
    "test_detail_ui.py",
    # Left in the shape deliberately too, and the file says why in its own
    # words: it starts its rows from a known state and never asserts on the
    # totals, because jsonstore keys its mirror *relative to the data root* --
    # so hub/knack_map_confirmed.json is one key however many temporary
    # directories there are, and a confirmation an earlier run made is
    # restored into this one. Re-run four times against one shared Postgres
    # here, identical every time.
    "test_knack_map.py",
    "test_prospect_explainer.py",
)

check("no test file is in the shape without having been re-run to prove it is safe",
      sorted(set(_offenders) - set(_RERUNS_CLEAN)), [])

# The other side, the rule check_stale_json_exemptions() works to: an entry
# that outlives what it exempted goes on covering whatever is written at that
# path next. A file that has since started owning its database, or that is
# gone, is named rather than left standing.
_stale = [n for n in _RERUNS_CLEAN
          if not (_ROOT / n).exists() or n not in _offenders]
check("and no exemption outlives the file or the shape it was written for",
      sorted(_stale), [])

# The four that were fixed rather than exempted, named so the failure is a
# record rather than a number: each wrote durable rows, so each passed on the
# first run against a database and failed on every run after.
_REGRESSION = ("test_dashboard_trends.py", "test_google_index.py",
               "test_io_records.py", "test_sales_status.py")
for _name in _REGRESSION:
    _w = _env_writes(_ast.parse((_ROOT / _name).read_text()))
    check(f"{_name} owns its database, not just its directory",
          (_w.get("HUB_DATA_DIR"), _w.get("DATABASE_URL")), ("assign", "assign"))

check("the sweep looked at something rather than passing vacuously",
      _checked >= len(_RERUNS_CLEAN), True)

# And the pattern is read from the repo rather than asserted from memory:
# the file CI's own comment calls safe to re-run is the one assigning both.
_blog = _env_writes(_ast.parse((_ROOT / "test_blog_publish.py").read_text()))
check("test_blog_publish.py assigns both, as CI says it does",
      (_blog.get("HUB_DATA_DIR"), _blog.get("DATABASE_URL")), ("assign", "assign"))


section("Every log follows the one root, not its own copy of the rule")
# =====================================================================
# data_root() says why it exists: "every module had its own copy of this
# expression. They all agreed, which is luck rather than design: the moment
# one of them disagreed, its files would land somewhere the backup sweep never
# looks." hub/audit.py and hub/errors.py were two such copies, and they did
# disagree -- on HUB_DATA_DIR, which data_root() reads first and neither read
# at all. Both preferred /var/data unconditionally.
#
# Nothing moved on Render, where HUB_DATA_DIR is unset and /var/data is
# mounted. What it cost was every test that sets HUB_DATA_DIR and then reads
# one of those logs: it was handed the real shared one. test_msa_embed.py
# asserts that signing writes an entry carrying the client -- and on this
# machine fourteen `msa` rows were already there, entries[0] already carried
# "Acme Marine, LLC", and both assertions passed before the test ran a line.
# Dropping client= from the route left it green.
#
# So: a log answers to the root, and the explicit per-file override still
# wins, because naming one file is the more specific answer.

import subprocess as _sp                                          # noqa: E402

_probe = """
import os, sys, json, tempfile
root = tempfile.mkdtemp(prefix="rootcheck_")
os.environ["HUB_DATA_DIR"] = root
os.environ.pop("AUDIT_LOG_PATH", None)
os.environ.pop("ERROR_LOG_PATH", None)
sys.path.insert(0, %r)
from hub import audit, errors, jsonstore
out = {"root": jsonstore.data_root(), "audit": audit._path(),
       "errors": errors._path()}
os.environ["AUDIT_LOG_PATH"] = "/tmp/named-audit.jsonl"
os.environ["ERROR_LOG_PATH"] = "/tmp/named-errors.jsonl"
import importlib
importlib.reload(audit); importlib.reload(errors)
out["audit_named"] = audit._path()
out["errors_named"] = errors._path()
print(json.dumps(out))
""" % str(ROOT)

# A subprocess, because both modules read the environment at call time and
# this file has already set HUB_DATA_DIR for its own run.
_r = _sp.run([sys.executable, "-c", _probe], capture_output=True, text=True)
check("the probe runs", _r.returncode, 0)
if _r.returncode:
    print("   " + (_r.stderr or "").strip()[-300:])
else:
    _p = json.loads(_r.stdout.strip().splitlines()[-1])
    check("the activity log follows HUB_DATA_DIR",
          _p["audit"].startswith(_p["root"]), True)
    check("the error log follows HUB_DATA_DIR",
          _p["errors"].startswith(_p["root"]), True)
    # Neither may fall back to the shared disk while a root is named.
    check("...and neither falls back to /var/data",
          [x for x in (_p["audit"], _p["errors"]) if x.startswith("/var/data")], [])
    # Naming one file is more specific than naming a root.
    check("AUDIT_LOG_PATH still wins", _p["audit_named"], "/tmp/named-audit.jsonl")
    check("ERROR_LOG_PATH still wins", _p["errors_named"], "/tmp/named-errors.jsonl")

# And none of them may go back to deciding for itself.
for _rel in ("hub/audit.py", "hub/errors.py", "hub/leads.py", "hub/extensions.py",
             "hub/scheduler.py", "modules/landing_ads/store.py",
             "modules/google_finder/app.py"):
    _src = (ROOT / _rel).read_text(encoding="utf-8")
    check(f"{_rel} defers to the one root",
          "jsonstore.data_root()" in _src or "jsonstore.data_dir(" in _src, True)


section("...and every store follows it, not just the two logs")
# =====================================================================
# Seven files carried the same expression. They agreed, which is the luck
# data_root() names -- and five of them also agreed on skipping HUB_DATA_DIR,
# so a named root moved the jsonstore files and left the lead book, the
# SQLite fallback, the scheduler's leader lock, the saved landing ads and the
# Google refresh tokens on the shared disk. hub/scheduler.py had a fifth
# spelling of its own: it fell back to "." , the current working directory,
# which is the one answer that depends on where somebody started the process.

_stores = """
import os, sys, json, tempfile
root = tempfile.mkdtemp(prefix="storecheck_")
os.environ["HUB_DATA_DIR"] = root
for k in ("HUB_LEADS_FILE", "DATABASE_URL", "TOKEN_DB_PATH", "AUDIT_LOG_PATH",
          "ERROR_LOG_PATH"):
    os.environ.pop(k, None)
sys.path.insert(0, %r)
from hub import jsonstore, leads, extensions, scheduler
from modules.landing_ads import store as landing_ads
import modules.google_finder.app as gfinder
out = {"root": jsonstore.data_root(),
       "leads": leads._path(),
       "sqlite": extensions.database_url().replace("sqlite:///", ""),
       "lock": scheduler._lock_path(),
       "landing_ads": landing_ads.data_dir(),
       "tokens": gfinder.TOKEN_DB_PATH}
os.environ["HUB_LEADS_FILE"] = "/tmp/named-leads.jsonl"
import importlib; importlib.reload(leads)
out["leads_named"] = leads._path()
print(json.dumps(out))
""" % str(ROOT)

_r2 = _sp.run([sys.executable, "-c", _stores], capture_output=True, text=True)
check("the store probe runs", _r2.returncode, 0)
if _r2.returncode:
    print("   " + (_r2.stderr or "").strip()[-400:])
else:
    _q = json.loads(_r2.stdout.strip().splitlines()[-1])
    for _name in ("leads", "sqlite", "lock", "landing_ads", "tokens"):
        check(f"{_name} follows HUB_DATA_DIR",
              _q[_name].startswith(_q["root"]), True)
        if not _q[_name].startswith(_q["root"]):
            print(f"          {_q[_name]}")
    # Naming one file is still more specific than naming a root.
    check("HUB_LEADS_FILE still wins", _q["leads_named"], "/tmp/named-leads.jsonl")



# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
