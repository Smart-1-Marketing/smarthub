"""The integrity check that finds a capped read counted, or searched by key.

    python3 test_capped_reads.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database.

## Why this file exists

`docs/claude/03` calls it "a capped global read filtered in Python", and it
was found BY HAND four times -- across `modules/reports`, the Ads Builder and
`hub/`. Every instance had the same shape and a different disguise: a cap
spent on the wrong noun, a cap behind a `* 10` multiplier, a cap the callee
silently clamped tighter than the caller asked for, a cap under a check whose
whole job was catching silence. Each round cost a sweep by eye, and the AST
scanner written for the third round had two blind spots that were only found
by accident.

So the repo looks for it now. `hub/integrity.check_capped_read_misuse()` asks
two questions, both narrow enough to have no false positives:

  1. a **count** over a capped read -- `len()`/`sum()` over a function that
     stops at `limit`. The count stops there and goes on being printed as the
     total;
  2. a **by-key search or index** over one -- `next(... if ...)` or a dict
     comprehension keyed off it. Past the cap it answers "no such row" about
     a row that exists.

What this holds: the check is empty on the repo; it finds each of the four
historical shapes when they are put back; and the three things that look like
the defect and are not stay quiet -- because a check that cries wolf gets
scrolled past, which costs exactly what a silent one costs.
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1capped_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "capped-reads-test"
os.environ["PANEL_PASSWORD"] = "capped-reads-pass"

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


from hub import integrity                                          # noqa: E402


def findings(**files):
    """Drive the sweep over a handful of synthetic modules."""
    trees = {rel.replace("__", "/"): ast.parse(src) for rel, src in files.items()}
    return integrity.capped_read_findings(trees)


STORE = '''
def rows(limit=200):
    return _all()[:limit]

def rows_all(limit=None):
    out = _all()
    return out if limit is None else out[:limit]

def count():
    return len(_all())
'''


# ------------------------------------------------------------- the repo
section("The check is empty on this repo")

live = integrity.check_capped_read_misuse()
check("no capped read is counted or searched by key today", live, [])
check("...and it is registered, so a pull request runs it",
      "capped_read_misuse" in {name for name, *_ in integrity.CHECKS})
check("...at a severity that reports rather than blocks",
      next(sev for name, _l, sev, _f in integrity.CHECKS
           if name == "capped_read_misuse"), "medium")


# ------------------------------------------- it finds each historical shape
section("It names each shape that has actually shipped")

got = findings(**{"store.py": STORE, "app.py": """
import store
def badge():
    return len([p for p in store.rows() if p["open"]])
"""})
check("a COUNT over a capped read -- the Approval hub badge",
      [(f["file"], f["line"]) for f in got], [("app.py", 4)])
check("...and the fix names a worked example", "open_proposal_count" in got[0]["fix"])

got = findings(**{"store.py": STORE, "app.py": """
import store
def one(cid):
    return next((a for a in store.rows(limit=500) if a["id"] == cid), None)
"""})
check("a BY-KEY SEARCH over a capped read -- the campaign-map sweep",
      [(f["file"], f["line"]) for f in got], [("app.py", 4)])
check("...and that fix names its own worked example", "campaign_map" in got[0]["fix"])

got = findings(**{"store.py": STORE, "app.py": """
import store
def index():
    return {a["id"]: a for a in store.rows(limit=200)}
"""})
check("an INDEX built from a capped read -- the optimization-run lookup",
      [(f["file"], f["line"]) for f in got], [("app.py", 4)])

got = findings(**{"store.py": STORE, "app.py": """
import store
def held_in():
    return sum(1 for h in store.rows(limit=5000) if h["platform"] == "ttd")
"""})
check("a sum() over a capped read -- the reconcile held count",
      [(f["file"], f["line"]) for f in got], [("app.py", 4)])


# ------------------------------------------------- and stays quiet otherwise
section("The three things that look like it and are not")

got = findings(**{"store.py": STORE, "app.py": """
import store
def total():
    return len(store.rows_all())
"""})
check("a limit that DEFAULTS to None is the uncapped path", got, [])

got = findings(**{"store.py": STORE, "app.py": """
import store
def total():
    return len(store.rows_all(limit=None))
"""})
check("...and so is limit=None written at the call site", got, [])

got = findings(**{"store.py": STORE, "app.py": """
import store
FLOOR = 5
def enough():
    return len(store.rows(limit=FLOOR + 1)) >= FLOOR
"""})
check("the floor+1 threshold idiom stops at one more row than it needs",
      got, [])

got = findings(**{"store.py": STORE, "other.py": """
def rows(client):
    return _for(client)
""", "app.py": """
from other import rows
def one(c):
    return next((r for r in rows(c) if r["x"]), None)
"""})
check("a DIFFERENT function sharing the name is not the capped one -- the "
      "collision that made a first pass report five phantom findings",
      got, [])

got = findings(**{"store.py": STORE, "test_thing.py": """
import store
def test_the_cap():
    assert len(store.rows(limit=5)) == 5
"""})
check("a test asserting the cap is the opposite of the defect", got, [])

got = findings(**{"store.py": STORE, "app.py": """
import store
def page():
    return store.rows(limit=25)
"""})
check("reading a page and returning it is what the bound is FOR", got, [])

got = findings(**{"store.py": STORE, "app.py": '''
def note():
    """Never write len(store.rows()) -- it stops at the cap."""
    return 1
'''})
check("and prose quoting the fault is not the fault", got, [])


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
