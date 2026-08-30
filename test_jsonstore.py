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
    if _w.get("HUB_DATA_DIR") != "assign":
        continue
    _checked += 1
    if _w.get("DATABASE_URL") != "assign":
        _offenders.append(f"{_f.name} (DATABASE_URL is "
                          f"{_w.get('DATABASE_URL') or 'never set'})")

# What is enforced here is the pair that demonstrably breaks, not every file
# matching the shape. Thirteen other files also own their directory and
# inherit the database, and every one of them re-runs clean: they either
# write nothing durable or overwrite what they read before reading it. They
# are left alone deliberately -- several boot the composed app, and CI runs
# Postgres precisely because Sites Admin refuses to start on SQLite and drops
# out of every check that boots it, so "fix" them and the gate quietly covers
# less. A check landing with thirteen findings it cannot safely act on is the
# one people learn to skip past, which is the note `provider_key_drift`
# already carries about going in red.
_REGRESSION = ("test_dashboard_trends.py", "test_google_index.py")
for _name in _REGRESSION:
    _w = _env_writes(_ast.parse((_ROOT / _name).read_text()))
    check(f"{_name} owns its database, not just its directory",
          (_w.get("HUB_DATA_DIR"), _w.get("DATABASE_URL")), ("assign", "assign"))

check("the sweep looked at something rather than passing vacuously",
      _checked >= len(_REGRESSION), True)

# And the pattern is read from the repo rather than asserted from memory:
# the file CI's own comment calls safe to re-run is the one assigning both.
_blog = _env_writes(_ast.parse((_ROOT / "test_blog_publish.py").read_text()))
check("test_blog_publish.py assigns both, as CI says it does",
      (_blog.get("HUB_DATA_DIR"), _blog.get("DATABASE_URL")), ("assign", "assign"))


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
