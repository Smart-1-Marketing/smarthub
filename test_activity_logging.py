"""Every module's work is attributable, and the check that says so bites.

    python3 test_activity_logging.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

`/api/integrity`'s silent-module check asks whether a module ever writes to the
activity log, because a module that does not is unauditable. It reported zero
findings, and seven modules were writing nothing at all.

The check tested whether the **string** `"for_module("` appeared in the
module's source. Binding the logger satisfies that. So seven modules imported
`hub.audit`, bound it to `_audit`, wrapped it in a no-op fallback for running
standalone, wrote a comment above the import explaining exactly why attribution
mattered there — and called it nowhere:

    calculators  google_finder  image_optimizer  page_image_optimizer
    pdf_optimizer  sites_admin  tickets

The comments are the part worth reading. `pdf_optimizer`'s said *"work that
isn't logged is work nobody can point to later"*. `page_image_optimizer`'s and
`sites_admin`'s said *"an unattributable change to a client's account is one
nobody can explain later"*. All three were true. None of them wrote a row —
so deleting a client's live website, connecting a domain, deploying a tag into
somebody's Tag Manager container and compressing a client's documents were the
least attributable actions in this Hub, behind a check reporting them clean.

That is the declared-but-unwired integration point this codebase has already
found in `RECORD_HOOK`, `io_creative`, `manifest()`, `thumb_url()`,
`mark_pushed()` and `check_limits()` — wearing the activity log. It is also the
half *before* the one PR #202 closed: that fixed a logged row the client record
could not name, and this is a row that was never written.

**What the check does now.** It reads the **AST** and requires a *call*. Both
halves of that matter, and they fail in opposite directions:

  1. **An import is not a call.** Reading the text for `for_module(` is
     satisfied by the binding alone, which is what hid all seven.

  2. **Prose is not a call site.** Several files here — this one included —
     explain the trap by quoting `audit.log(` and `for_module(` in a comment.
     A substring match reports the explanation of the fix as the defect: the
     rule `hub/config.py`'s drift check and `hub/image_audit.py`'s producer
     check each arrived at independently.

**And the honest remainder is declared, not left dangling.** A module whose
work genuinely does not belong in the activity log is a decision, so it goes in
`hub/audit.NO_ACTIVITY` with its reason. `calculators` is the only one: what a
public estimate box produces is a *lead*, and leads go through `hub/leads.py`.
Its dangling `_audit` binding is gone rather than wired — left there it would
go on silencing a check it no longer satisfies. An entry naming a module that
no longer exists is itself a finding, the rule
`check_stale_json_exemptions()` works to.

## The same shape, one step further on

Writing the check above turned up its neighbour. `check_work_kinds()` — added
by PR #202, at **high**, green — asks which modules log work against a client
that `WORK_KINDS` cannot name, because `work_log()` skips a module it cannot
name and a skipped module reads on the record as a client nobody has done any
work for. It counted only a **direct** `audit.log("mod", …, client=…)`, on the
stated reasoning that a bare `log()` is a module's own wrapper whose first
argument is the event rather than the module. True — and the conclusion drops
those modules entirely, because the module name is not missing, it is one level
up in whatever bound the wrapper:

    log = audit.for_module("msa")                  ->  log(…)  is msa
    def log(event, **extra):                       ->  log(…)  is radio_promo
        hub_audit.log("radio_promo", event, **extra)

Four modules fell through: `radio_promo`, `gpt_ads`, `landing_ads` and `msa`.
`radio_promo` is the one that shows the cost — `fan_radio` has been in
`WORK_KINDS` since it was written and its sibling was not, so a client who had
a Fan Radio spot made appeared on their own record and a client who had a Radio
Promo spot made did not, from two tools doing the same job. Two more surfaced
and are the *other* answer, now written into `NOT_WORK`: `hub/prospect.py` logs
a lead converting, and `hub/stale_creative.py` logs a report row being marked
evergreen.

**And the two halves of that check had their own copy of the walk.**
`stale_work_exemptions()` asks what no longer logs — the same walk from the
opposite end — so the moment `check_work_kinds()` learned to resolve a wrapper
and the other did not, every `NOT_WORK` entry added for a wrapper-shaped call
site was immediately reported stale. Reproduced before it was fixed. They read
one `_client_log_modules()` now: two checks asking one question will answer it
differently, and both answers end up on the same panel.
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1activity_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "activity-test-secret"
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
from hub import integrity                                    # noqa: E402
from hub.client_brand import (WORK_KINDS, _client_log_modules,  # noqa: E402
                              _logger_bindings, check_work_kinds,
                              stale_work_exemptions)

MODULES = ROOT / "modules"

# A throwaway tree the two work-log checks can be pointed at, so "does this
# check still bite?" is asked of a module that plainly logs client work under
# a name nothing can name -- rather than of the real tree, which is green and
# therefore proves nothing about whether the check still works.
FIXTURE = Path(TMP) / "fixture"
(FIXTURE / "modules" / "invented").mkdir(parents=True)
(FIXTURE / "hub").mkdir(parents=True)
(FIXTURE / "modules" / "invented" / "app.py").write_text('''
from hub import audit as hub_audit

MODULE = "invented"


def _log(event, **extra):
    hub_audit.log(MODULE, event, **extra)


def deliver():
    _log("delivered", client="Acme Tyre")
''')

# The seven that bound a logger and never called it. Named individually rather
# than counted: a regression on any one of them is a module going silent, and
# "six of seven" is not something a count can tell you.
WAS_SILENT = ["calculators", "google_finder", "image_optimizer",
              "page_image_optimizer", "pdf_optimizer", "sites_admin",
              "tickets"]


# =====================================================================
section("The check reads a call, not an import")
# =====================================================================
# Every shape below is one a real module in this repo has had.

binds_only = '''
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("thing")
except Exception:
    def _audit(*a, **k):
        return None

def do_the_work():
    return "done"
'''
check("binding the logger and never calling it does not count",
      integrity._calls_the_logger(binds_only), False)

bare_import = '''
from hub import audit

def do_the_work():
    return "done"
'''
check("importing hub.audit does not count either",
      integrity._calls_the_logger(bare_import), False)

bound_and_called = binds_only + '''
def save():
    _audit("saved", client="Acme")
'''
check("binding it and calling it does count",
      integrity._calls_the_logger(bound_and_called), True)

direct = '''
from hub import audit

def save():
    audit.log("thing", "saved", client="Acme")
'''
check("a direct audit.log() call counts",
      integrity._calls_the_logger(direct), True)

# Prose is not a call site. This repo explains the trap in comments and
# docstrings; a text match reports the explanation as the defect.
prose = '''
"""This module deliberately writes nothing.

Do not add audit.log("thing", "saved") here, and do not bind
_audit = audit.for_module("thing") -- see hub/audit.NO_ACTIVITY.
"""

def do_the_work():
    # audit.log("thing", "saved") would be wrong here
    return "done"
'''
check("a docstring quoting audit.log() is not a call site",
      integrity._calls_the_logger(prose), False)
check("nor is a comment quoting for_module()",
      integrity._calls_the_logger(
          "# _audit = audit.for_module('x')\ndef f():\n    return 1"), False)

# A file that cannot be parsed is not evidence of logging.
check("an unparseable file does not count as logging",
      integrity._calls_the_logger("def ("), False)


# =====================================================================
section("Every module either logs or says why it does not")
# =====================================================================


def module_dirs():
    return sorted(p.name for p in MODULES.iterdir()
                  if p.is_dir() and p.name != "__pycache__")


def module_logs(mod):
    """The same question the check asks, across all of a module's files."""
    for path in (MODULES / mod).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if integrity._calls_the_logger(path.read_text(errors="ignore")):
            return True
    return False


silent = [m for m in module_dirs()
          if not module_logs(m) and m not in audit.NO_ACTIVITY
          and m not in audit.LOG_NAMES]
check("no module writes to the activity log by accident of an import",
      silent, [])

check("and the integrity check agrees, with nothing outstanding",
      integrity.check_silent_modules(), [])


# =====================================================================
section("The seven that were silent now write a row")
# =====================================================================
# calculators is the one that is declared instead, because what it produces
# is a lead rather than client work.

for mod in WAS_SILENT:
    if mod in audit.NO_ACTIVITY:
        check(f"{mod} is declared as writing nothing, with a reason",
              len(audit.NO_ACTIVITY[mod]) > 40, True)
    else:
        check(f"{mod} calls its logger", module_logs(mod), True)

check("calculators is the only one declared rather than wired",
      sorted(audit.NO_ACTIVITY), ["calculators"])

# Its dangling binding is gone rather than wired. Asserted through the AST,
# not the text -- the replacement comment explains the trap by quoting
# `for_module("calculators")`, and a substring test reports that explanation
# as the defect. Which is the same rule this whole file is about, biting the
# test that checks it.
_calc = ast.parse((MODULES / "calculators" / "app.py").read_text())
_binds = [n for n in ast.walk(_calc)
          if isinstance(n, ast.Call)
          and getattr(n.func, "attr", "") == "for_module"]
check("and its dangling binding is gone rather than left to silence a check",
      _binds, [])


# =====================================================================
section("An exemption cannot outlive what it exempted")
# =====================================================================
# The rule check_stale_json_exemptions() works to: an entry naming something
# that no longer exists goes on covering whatever is written at that path next.

check("every declared module is a module that exists",
      [m for m in audit.NO_ACTIVITY if not (MODULES / m).is_dir()], [])

original = dict(audit.NO_ACTIVITY)
try:
    audit.NO_ACTIVITY["a_module_that_was_deleted"] = "reason no longer relevant"
    findings = integrity.check_silent_modules()
    named = [f for f in findings
             if f.get("module") == "a_module_that_was_deleted"]
    check("a stale exemption is reported by name", len(named), 1)
    check("and it says where to delete it",
          "NO_ACTIVITY" in named[0]["fix"] if named else False, True)
finally:
    audit.NO_ACTIVITY.clear()
    audit.NO_ACTIVITY.update(original)

check("and the check is clean again once it is removed",
      integrity.check_silent_modules(), [])


# =====================================================================
section("The check bites when a module goes silent")
# =====================================================================
# A check that cannot fail is worse than no check. Feed it a module whose
# every file merely binds the logger, and require it to say so.

real_sources = integrity._sources


def _sources_with_a_silent_module():
    for rel, src in real_sources():
        yield rel, src
    yield "modules/gone_quiet/app.py", binds_only


try:
    integrity._sources = _sources_with_a_silent_module
    findings = integrity.check_silent_modules()
    named = [f for f in findings if f.get("module") == "gone_quiet"]
    check("a module that only binds the logger is reported", len(named), 1)
    check("and the finding says binding is not enough",
          "reads a call" in named[0]["fix"] if named else False, True)
    check("and says how to declare a module with nothing to log",
          "NO_ACTIVITY" in named[0]["fix"] if named else False, True)
finally:
    integrity._sources = real_sources

check("and the real tree is clean once the fixture is removed",
      integrity.check_silent_modules(), [])


# =====================================================================
section("A row filed against a client is one the record can name")
# =====================================================================
# work_log() skips a module WORK_KINDS cannot name, so a client who has just
# had work done reads as a client nobody has done any work for -- the failure
# PR #202 closed, from the other end. Any call site passing client= has to be
# nameable, or the row is written, kept, and dropped on the way to the record.


check("every module logging against a client is named or declared",
      check_work_kinds(), [])
check("and no NOT_WORK entry has outlived its call site",
      stale_work_exemptions(), [])

# The one this change newly files against a client.
check("page_image_optimizer is nameable", "page_image_optimizer" in WORK_KINDS,
      True)
check("sites_admin is nameable", "sites_admin" in WORK_KINDS, True)


# =====================================================================
section("A module's own log() wrapper is resolved, not skipped")
# =====================================================================
# check_work_kinds() counted only a direct audit.log("mod", …, client=…). Four
# modules carry the module name one level up, in the wrapper that binds the
# logger, and fell through the gap entirely: radio_promo, gpt_ads,
# landing_ads and msa. fan_radio was in WORK_KINDS and radio_promo -- its
# sibling, doing the same job for the same client -- was not.

wrapper = ast.parse('''
def log(event, **extra):
    hub_audit.log("radio_promo", event, actor=actor(), **extra)
''')
check("a def log() wrapper resolves to the module it logs as",
      _logger_bindings(wrapper), {"log": "radio_promo"})

bound_form = ast.parse('log = audit.for_module("msa")')
check("and so does a for_module binding",
      _logger_bindings(bound_form), {"log": "msa"})

via_const = ast.parse('''
MODULE = "gpt_ads"

def _log(event, **extra):
    hub_audit.log(MODULE, event, **extra)
''')
check("a wrapper naming a module-level constant resolves too",
      _logger_bindings(via_const), {"_log": "gpt_ads"})

# A wrapper that names no module is not a binding: it may be a plain Python
# logger, and attributing a client row to one is worse than missing it.
plain = ast.parse('''
def log(msg):
    logging.getLogger(__name__).info(msg)
''')
check("a plain Python logger is not mistaken for the activity log",
      _logger_bindings(plain), {})

for mod in ("radio_promo", "gpt_ads", "landing_ads", "msa"):
    check(f"{mod} is nameable by the client record", mod in WORK_KINDS, True)
check("fan_radio's sibling is named the same way it is",
      WORK_KINDS["radio_promo"][0], WORK_KINDS["fan_radio"][0])

# The check bites: a module logging client work under a name nothing can
# name is reported.
check("a module the table cannot name is reported",
      [f["module"] for f in check_work_kinds(root=str(FIXTURE))], ["invented"])


# =====================================================================
section("One walk, not two")
# =====================================================================
# check_work_kinds() asks what it cannot name; stale_work_exemptions() asks
# what no longer logs. Same walk from opposite ends, and they each had their
# own copy -- so the moment one learned to resolve a wrapper the other did
# not, and every NOT_WORK entry added for a wrapper-shaped call site read as
# stale. Two checks asking one question answering it differently, on one panel.

check("both read the same reader",
      set(_client_log_modules(root=str(FIXTURE))), {"invented"})
check("so a wrapper-shaped exemption is not reported as stale",
      [m for m in ("prospect", "stale_creative")
       if m in stale_work_exemptions()], [])


# =====================================================================
section("The logger itself cannot break the action it reports on")
# =====================================================================
# audit.log is best-effort by design: a module that cannot write its row must
# still do its work. What it cannot protect against is a caller that raises
# while building its own arguments -- the f-string over a missing attribute
# that 500'd every Commercial Builder render before the guard could apply.

audit.log("test_module", "did_a_thing", actor="Ada", client="Acme")
rows = audit.read(limit=10, module="test_module")
check("a row is written and reads back", len(rows), 1)
check("with the module first and the type second",
      (rows[0]["module"], rows[0]["type"]), ("test_module", "did_a_thing"))

bound = audit.for_module("test_bound", actor_fn=lambda: "Ada")
bound("bound_thing", client="Acme")
check("a bound logger writes under its own name",
      audit.read(limit=10, module="test_bound")[0]["type"], "bound_thing")


def _raises():
    raise RuntimeError("actor lookup failed")


noisy = audit.for_module("test_actor", actor_fn=_raises)
try:
    noisy("still_written")
    survived = True
except Exception:                                            # noqa: BLE001
    survived = False
check("an actor lookup that raises does not take the action with it",
      survived, True)
check("and the row is still written",
      audit.read(limit=10, module="test_actor")[0]["type"], "still_written")

# None-valued extras are dropped rather than written as nulls, so a caller
# that has no client does not file a row claiming an empty one.
audit.log("test_module", "no_client", client=None)
check("a None extra is left off rather than written as an empty client",
      "client" in audit.read(limit=10, module="test_module")[0], False)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
