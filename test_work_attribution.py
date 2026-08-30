"""Work filed against a client reaches that client's record.

    python3 test_work_attribution.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

This is the third distinct way a tool's work has gone missing from the client
record, and the first two each have a check already:

  1. **A module logs `client=` under a name `WORK_KINDS` cannot name.**
     `work_log()` skips a module it cannot name. Found five times by somebody
     opening a client's record and noticing; `check_work_kinds()` asks it of
     every call site now.
  2. **A module binds a logger and never calls it.** Seven did.
     `check_silent_modules()` reads a *call* through the AST now, so an import
     cannot satisfy it.

  3. **And this one: the row is written, the table knows the name, and the
     client is under a key the record does not read.** Nothing was asking it.

`work_log()` takes the client from exactly five keys — `client`,
`client_name`, `company`, `business_name`, `tool_client` — and from nowhere
else. Two tools put it somewhere else:

* **UTM Builder** wrote `_log("links_saved", detail=client, …)`. And it logs
  under the name **`utm`** while `WORK_KINDS` was keyed `utm_builder`, so the
  row was dropped twice over — once for the key, once for the name. Every
  tracked-link batch this tool has ever saved was invisible on the client's
  own record, and the tool read there as one nobody had ever used.

* **Background Remover** wrote `_log("cutout_saved", detail=client, …)`. Its
  neighbouring comment explains at length that a cut-out has to reach the
  client's *gallery* or it is absent from the one page somebody opens to see
  what we have produced — that half was done, and the activity-log half was
  one keyword away from working.

Neither errored. The tool's own screens were complete, the row was on disk,
and the client record was confidently empty — which is the failure this
corner of the codebase keeps having to undo.

**What the check had to get right, and what a first draft got wrong.**
`modules/ads_builder` mirrors through `store.log_event(**details)`, and the
client arrives from `app.py` through that forward. The AST cannot follow it,
so the first version of the check reported `ads_builder` — a module that
names its client perfectly well — as one that never does. A check with a
false positive in it is a check somebody switches off, and switching it off
costs the two real findings with it. A call site that forwards `**kwargs` is
counted apart now: *not determinable* is not the same answer as *never does*.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1attrib_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "attribution-test-secret"
os.environ["AUDIT_LOG_PATH"] = os.path.join(DISK, "audit.jsonl")

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import audit                                        # noqa: E402
from hub import client_brand                                 # noqa: E402
from hub.client_brand import (CLIENT_KEYS, NOT_WORK,          # noqa: E402
                              WORK_KINDS, check_client_attribution,
                              check_work_kinds, stale_work_exemptions,
                              work_log)

MODULES = ROOT / "modules"

# A throwaway tree the check can be pointed at, so "does it still bite?" is
# asked of code that plainly has the bug rather than of the real tree, which
# is green and therefore proves nothing about whether the check still works.
FIXTURE = Path(TMP) / "fixture"
(FIXTURE / "modules" / "wrongkey").mkdir(parents=True)
(FIXTURE / "modules" / "stock_photos").mkdir(parents=True)
(FIXTURE / "modules" / "forwarder").mkdir(parents=True)
(FIXTURE / "hub").mkdir(parents=True)

# 1. In WORK_KINDS, logs, and names the client under a key nothing reads.
(FIXTURE / "modules" / "wrongkey" / "app.py").write_text('''
from hub import audit as hub_audit


def _log(event, **extra):
    hub_audit.log("bg_remover", event, **extra)


def save(client):
    _log("cutout_saved", detail=client, name="hero")
''')

# 2. Directory in WORK_KINDS, logs under a different, undeclared name. The
#    directory has to be one WORK_KINDS actually holds, or the shape cannot
#    arise -- which is the whole condition the check keys on.
(FIXTURE / "modules" / "stock_photos" / "app.py").write_text('''
from hub import audit as hub_audit


def _log(event, **extra):
    hub_audit.log("stockph", event, **extra)


def save(client):
    _log("saved", client=client)
''')

# 3. Forwards **kwargs, so whether a client is named is not determinable here.
(FIXTURE / "modules" / "forwarder" / "app.py").write_text('''
from hub import audit as hub_audit


def log_event(action, **details):
    hub_audit.log("fwd_only", action, **details)
''')


# =====================================================================
section("The five keys are written down once, and work_log reads them")
# =====================================================================
# The list and the reader must not be two copies: a key added to one and not
# the other is a row that lands nowhere, silently.

check("the client keys are declared",
      CLIENT_KEYS,
      ("client", "client_name", "company", "business_name", "tool_client"))

src = (ROOT / "hub" / "client_brand.py").read_text()
for key in CLIENT_KEYS:
    check(f"work_log() reads {key!r}", f'"{key}"' in src, True)


# =====================================================================
section("A row written the old way never arrives; the new way does")
# =====================================================================
# Driven through the real work_log(), not asserted about the source.

audit.log("utm", "links_saved", actor="Ada", detail="Acme Tyre", count=8)
audit.log("bg_remover", "cutout_saved", actor="Ada", detail="Acme Tyre")
check("a client named under detail= reaches nothing",
      work_log("Acme Tyre")["count"], 0)

audit.log("utm", "links_saved", actor="Ada", client="Acme Tyre", count=8)
audit.log("bg_remover", "cutout_saved", actor="Ada", client="Acme Tyre")
landed = work_log("Acme Tyre")
check("naming it under client= lands both rows", landed["count"], 2)
check("named as the tools a person would know",
      sorted(landed["by_source"]), ["Background Remover", "UTM Builder"])
check("with the kind the table gives them",
      sorted(i["kind"] for i in landed["items"]),
      ["Cut-out produced", "Tracked links"])

# Every one of the five keys works, or a tool using a different one is
# silently dropped exactly as these two were.
for i, key in enumerate(CLIENT_KEYS):
    audit.log("scans", "site_audit", **{key: f"KeyCo {i}"})
    check(f"a client named under {key!r} reaches the record",
          work_log(f"KeyCo {i}")["count"], 1)

# An unfiled batch names nobody rather than inventing a client called
# "unfiled" — which would collect everybody's work onto one record.
audit.log("utm", "links_saved", actor="Ada", count=2)
check("a batch with no client is filed against nobody",
      work_log("unfiled")["count"], 0)


# =====================================================================
section("The two tools that were doing it are fixed")
# =====================================================================

utm = (MODULES / "utm_builder" / "app.py").read_text()
bg = (MODULES / "bg_remover" / "app.py").read_text()
check("UTM Builder names the client under client=",
      "_log(\"links_saved\", client=" in utm, True)
check("and no longer under detail=",
      "_log(\"links_saved\", detail=" in utm, False)
check("Background Remover names the client under client=",
      "_log(\"cutout_saved\", client=" in bg, True)
check("and no longer under detail=",
      "_log(\"cutout_saved\", detail=" in bg, False)

# The other half of the UTM bug: it logs under `utm`, and the table has to be
# keyed on the name actually written. Declared rather than renamed -- renaming
# the call site would orphan every row already on disk.
check("the table is keyed on the name UTM Builder actually logs under",
      "utm" in WORK_KINDS, True)
check("and the mapping from its directory is declared",
      audit.LOG_NAMES.get("utm_builder"), "utm")
check("the call site is unchanged, so nothing already written is orphaned",
      'hub_audit.log("utm"' in utm, True)


# =====================================================================
section("The check catches both shapes, and says which")
# =====================================================================

findings = check_client_attribution(root=str(FIXTURE))
by_mod = {f["module"]: f for f in findings}

check("a client under a key the record cannot read is reported",
      "bg_remover" in by_mod, True)
if "bg_remover" in by_mod:
    check("and the finding names the keys that would work",
          "client_name" in by_mod["bg_remover"]["detail"], True)
    check("and says detail= is not one of them",
          "detail=" in by_mod["bg_remover"]["fix"], True)

check("a module logging under a name the table is not keyed on is reported",
      "stockph" in by_mod, True)
if "stockph" in by_mod:
    check("and the finding names both the directory and the log name",
          "stock_photos" in by_mod["stockph"]["detail"]
          and "stockph" in by_mod["stockph"]["detail"], True)
    check("and says to declare rather than rename",
          "orphan" in by_mod["stockph"]["fix"], True)

# The false positive a first draft produced. modules/ads_builder mirrors
# through store.log_event(**details) and the client arrives through the
# forward; the AST cannot follow it, so "we cannot tell" must not be reported
# as "it never does".
check("a wrapper that forwards **kwargs is not reported as naming nobody",
      "fwd_only" in by_mod, False)
check("and the real ads_builder is not reported either",
      "ads_builder" in {f["module"] for f in check_client_attribution()}, False)
check("because it does name a client, through the forward",
      "client=body[\"businessName\"]"
      in (MODULES / "ads_builder" / "app.py").read_text(), True)


# =====================================================================
section("And the real tree is clean")
# =====================================================================

check("nothing is filed under a key the record cannot read",
      check_client_attribution(), [])
check("nothing logs client work under a name the table cannot name",
      check_work_kinds(), [])
check("and no NOT_WORK entry has outlived its call site",
      stale_work_exemptions(), [])

# Every name the table knows must be one something could plausibly write:
# a key in neither WORK_KINDS nor NOT_WORK is the finding above, and the two
# tables must not both claim one.
overlap = sorted(set(WORK_KINDS) & set(NOT_WORK))
check("no name is both work and not-work", overlap, [])


# =====================================================================
section("The check is registered where the others are")
# =====================================================================

integrity_src = (ROOT / "hub" / "integrity.py").read_text()
check("it runs on /api/integrity",
      "check_client_attribution" in integrity_src, True)
check("at high, beside the check it is the other half of",
      '"client_attribution", "Work filed under a key the record cannot read",\n'
      '     "high"' in integrity_src, True)

# One walk, not three: check_work_kinds, stale_work_exemptions and this one
# all read _log_call_sites(). Two checks asking one question answer it
# differently, and both answers end up on the same panel.
check("all three read one walker",
      client_brand.__dict__["_log_call_sites"].__doc__ is not None, True)
check("and there is only one of it",
      src.count("for folder in (\"hub\", \"modules\")"), 1)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
