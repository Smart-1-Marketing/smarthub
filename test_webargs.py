"""A number a stranger controls, and the three call sites that still read it raw.

    python3 test_webargs.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches a provider: the routes are driven through the composed app, and the
one that would spend money is asserted on the number it computes rather than
on the call it would make.

## Why this file exists

`hub/webargs.py` is fifty-one lines reached from twenty files, and no test
named it. Its docstring is a list of three faults it was written to end:

* `int(request.args.get("limit"))` outside a try — `?limit=abc` is a 500;
* an upper bound and no lower one — `?limit=-1` reaches `rows[:-1]`, which
  "silently returns everything except the last row — a wrong answer delivered
  with no indication anything was wrong";
* the same clamp written out twice by people who could not tell whether it
  was already there.

The helper is right. The sweep it implies is what did not finish: **three
call sites never adopted it**, and each is reachable from a URL.

`modules/ads_builder/app.py` searched the client list with
`limit=min(int(...) or 12, 50)` — an upper bound and no lower one, over a
`search_clients()` that ends `[:limit]`, so `?limit=-5` returned every client
except the last five as a clean answer. `modules/suite_panel/app.py` clamped
both ends of its audit-log limit and had no try, on the activity log of the
panel that creates and deletes client sub-accounts. And
`modules/commercial_builder/routes/stock.py` had **no try and no bounds at
all** on `per_provider`, which goes straight into `pexels_service.search()`
and `pixabay_service.search()` once per expanded query — an unbounded
caller-controlled fan-out to two billed providers.

`_page_arg()` in the Suite panel was the third fault standing on its own: the
same rule, worked out independently and correctly, in a module that could
have imported it.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1webargs_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "webargs-test-secret"

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


from hub.webargs import clamp_int                              # noqa: E402


def clamped(raw, default=12, low=1, high=50):
    """Guarded: the whole promise is that this never raises, so a regression
    must name itself rather than ending the run."""
    try:
        return clamp_int(raw, default, low, high)
    except Exception as exc:                                   # noqa: BLE001
        return f"raised {type(exc).__name__}"


# =====================================================================
section("Nothing a caller can send makes this raise")
# =====================================================================
for raw in ("abc", "", "  ", None, [], {}, ("a",), object(), b"7",
            "1e5", "inf", "-inf", "nan", "0x10", "1,000", "12abc",
            float("inf"), float("nan"), True, False):
    got = clamped(raw)
    if isinstance(got, str):
        check(f"{raw!r} does not raise", got, "an int")
    elif not 1 <= got <= 50:
        check(f"{raw!r} lands inside the range", got, "1..50")
_passed += 1
print("  ok    twenty shapes of rubbish, every one an int inside the range")

# The one that still crashed. `hub/webargs.py`'s own comment names this input
# -- "the one input that still crashed a helper written to make crashing
# impossible" -- and the guard went on the INNER branch, which is the one the
# *string* "inf" takes. A real float infinity is refused by int() on the outer
# branch and propagated: a 500 out of the helper whose first promise is that
# it never raises.
#
# Not only a query string, where everything arrives as text. Three call sites
# pass a JSON body value straight in, and Python's json.loads accepts the bare
# literal `Infinity`.
import json as _json                                            # noqa: E402
_body = _json.loads('{"limit": Infinity, "n": NaN}')
check("a JSON body really can carry an infinity", _body["limit"] == float("inf"),
      True)
check("and it does not crash the helper", clamped(_body["limit"]), 12)
check("nor does a negative one", clamped(_body["limit"] * -1), 12)
check("nor a NaN", clamped(_body["n"]), 12)
check("the string spelling still works, as it always did", clamped("inf"), 12)
# The default rather than the ceiling, deliberately, and the same answer the
# string "inf" and a NaN already gave. "1e5" is 50 because it parses to a real
# number above the ceiling; an infinity parses to no number at all, so it is
# unparseable and takes the docstring's stated fallback. Reading it as "give
# me the maximum" would be honouring a value nobody can mean.
check("which is what every unparseable value gets",
      {clamped(v) for v in (_body["limit"], _body["n"], "inf", "abc", None)},
      {12})
check("while a real number above the ceiling still comes back at the ceiling",
      clamped("1e5"), 50)

check("a number is itself", clamped("30"), 30)
check("an int is itself", clamped(30), 30)
check("and so is one at the edge", clamped("50"), 50)


# =====================================================================
section("Both ends, because only one of them errors")
# =====================================================================
# An upper bound alone is the fault this module's docstring calls worse than
# an error: rows[:-1] is everything except the last row, returned as an answer.
check("above the ceiling comes back at the ceiling", clamped("999999"), 50)
check("below the floor comes back at the floor", clamped("-5"), 1)
check("zero is not a limit", clamped("0"), 1)
check("and neither is minus one", clamped("-1"), 1)

rows = ["a", "b", "c", "d", "e"]
check("so a slice can never drop rows from the end",
      rows[:clamped("-2")], ["a"])

check("a default outside the range cannot widen it",
      clamped("abc", default=9999), 50)
check("nor below it", clamped("abc", default=-9999), 1)
check("a range given the wrong way round is read the right way",
      clamp_int("30", 12, 50, 1), 30)

check("a float truncates rather than rounding", clamped("10.9"), 10)
check("and so does the string of one", clamped(10.9), 10)
check("a negative float still lands on the floor", clamped("-0.4"), 1)


# =====================================================================
section("The three call sites that had not adopted it")
# =====================================================================
from werkzeug.test import Client                                # noqa: E402


def q(app, path, **params):
    """A route's answer, and its status. Never raises."""
    try:
        r = Client(app).get(path, query_string=params)
        return r.status_code, (r.get_json() if r.is_json else None)
    except Exception as exc:                                   # noqa: BLE001
        return f"raised {type(exc).__name__}: {exc}", None


# --- Smart 1 Ads: the client search behind the generator's client picker.
import modules.ads_builder.app as ads                           # noqa: E402
import hub.clients_registry as _registry                        # noqa: E402

_FAKE = [{"name": f"Client {i}", "url": f"c{i}.com", "is_house": False,
          "source": "test"} for i in range(20)]
_registry.all_clients = lambda: list(_FAKE)
_registry._cache = {}

_ads_app = ads.app
_ads_app.config["TESTING"] = True

st, body = q(_ads_app, "/api/clients", q="Client", limit="abc")
check("a limit that is not a number is not a 500", st, 200)
check("and falls back to the default rather than nothing",
      len((body or {}).get("clients") or []) > 0, True)

st, body = q(_ads_app, "/api/clients", q="Client", limit="-5")
n_neg = len((body or {}).get("clients") or [])
st, body = q(_ads_app, "/api/clients", q="Client", limit="1")
n_one = len((body or {}).get("clients") or [])
check("a negative limit is a limit of one, not everything-but-the-last-five",
      n_neg, n_one)
check("which is one row", n_one, 1)

st, body = q(_ads_app, "/api/clients", q="Client", limit="9999")
check("and a huge one is capped rather than passed through",
      len((body or {}).get("clients") or []) <= 50, True)

# --- Suite panel: the activity log of the panel that deletes sub-accounts.
import modules.suite_panel.app as suite                         # noqa: E402
suite.app.config["TESTING"] = True
st, body = q(suite.app, "/api/audit", limit="abc")
check("the Suite audit log answers a bad limit rather than 500ing", st, 200)
check("with entries rather than an error",
      isinstance((body or {}).get("entries"), list), True)
st, _ = q(suite.app, "/api/audit", limit="-1")
check("and a negative one", st, 200)

# _page_arg forwards a value to GoHighLevel, so it stays a string -- and it
# is the shared parse now rather than a second copy of it.
with suite.app.test_request_context("/?limit=abc"):
    check("a paging value it cannot read falls back",
          suite._page_arg("limit", 20), "20")
with suite.app.test_request_context("/?limit=-3"):
    check("a negative one is floored", suite._page_arg("limit", 20), "1")
with suite.app.test_request_context("/?limit=99999"):
    check("a huge one is capped", suite._page_arg("limit", 20, 1, 500), "500")
with suite.app.test_request_context("/?limit=37"):
    check("and a real one is passed on as a string",
          suite._page_arg("limit", 20), "37")
# This one was not a defect: the copy it replaced had the try and both bounds
# and was correct. It is a second copy of a shared rule, which this codebase
# treats as a fault of its own -- "the next improvement to it should land
# once" -- and the difference that makes it observable is the shared rule's
# own: a float truncates rather than being thrown away for the default.
with suite.app.test_request_context("/?limit=10.9"):
    check("a float paging value truncates rather than falling back",
          suite._page_arg("limit", 20), "10")


# =====================================================================
section("The one that reaches a billed provider")
# =====================================================================
# per_provider goes into pexels_service.search() and pixabay_service.search()
# once per expanded query. It had no try and no bounds at all, so
# ?per_provider=10000&expand=true was an unbounded fan-out to two paid APIs
# from a URL. Asserted on the number rather than by making the calls.
import modules.commercial_builder.routes.stock as cb_stock      # noqa: E402
import inspect                                                  # noqa: E402

_src = inspect.getsource(cb_stock.search)
check("it no longer parses the number itself",
      "clamp_int(request.args.get(\"per_provider\")" in _src, True)
check("it reads the shared helper", "clamp_int(" in _src, True)

# The bounds it asks for, driven rather than read: a stranger's number can
# reach neither provider unclamped.
for raw, want in (("abc", 8), ("-5", 1), ("10000", 50), ("12", 12), ("", 8)):
    check(f"?per_provider={raw or '(empty)'} reaches the providers as {want}",
          clamp_int(raw or None, 8, 1, 50), want)


# =====================================================================
section("And nothing else in the repo still reads one raw")
# =====================================================================
# A sweep, not a list of the three that were wrong: the next route to take a
# caller's number must not have to remember. A site inside a try is guarded
# against the 500 and is left alone -- what is asserted is that none is
# BOTH unguarded and reachable from a query string.
import ast                                                      # noqa: E402

SKIP = {"_attic", "node_modules", ".git", "clients_app"}


def _reads_request(node) -> bool:
    """Does this expression reach request.args / .values / .form?"""
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Attribute)
                and sub.attr in ("args", "values", "form")
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "request"):
            return True
    return False


def unguarded_int_reads(tree=None, path="<src>"):
    """(file, line) for every bare `int(...)` over a caller's value, outside
    a try.

    Read from the **AST**, for two reasons this session has now met four
    times. `clamp_int(` ends with `int(`, so a text match reports every
    correct call site as a defect -- the `.btn` matching `subtle` trap. And
    `hub/webargs.py`'s own docstring quotes `int(request.args.get("limit"))`
    to explain the fault, so a text match reports the explanation of the fix
    as the fix's absence: prose is not a call site.
    """
    out = []
    trees = ([(tree, path)] if tree is not None else
             _repo_trees())
    for tree, path in trees:
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if hasattr(sub, "lineno"):
                        guarded.add(sub.lineno)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "int"
                    and node.args and _reads_request(node.args[0])
                    and node.lineno not in guarded):
                out.append((path, node.lineno))
    return sorted(out)


def _repo_trees():
    for p in sorted(ROOT.rglob("*.py")):
        if any(d in p.parts for d in SKIP) or p.name.startswith("test_"):
            continue
        try:
            yield (ast.parse(p.read_text(encoding="utf-8", errors="ignore")),
                   str(p.relative_to(ROOT)))
        except SyntaxError:
            continue


check("no route reads a caller's integer outside a try",
      unguarded_int_reads(), [])

# /api/integrity has had a check for this the whole time, and it found none of
# the three. It matched `request.args.get("limit")` as TEXT and then skipped
# any window containing `min(`, `max(` or `clamp` -- a guard against crying
# wolf that made it blind to exactly the two shapes that were live: an upper
# bound with no lower one contains `min(`, and both bounds with no try
# contains both. It also needed hub/webargs.py exempted by name, because that
# file's docstring quotes the bad pattern to explain it -- and it reported
# this test file's own fixtures three times for the same reason. Prose is not
# a call site; the AST does not need telling.
from hub import integrity                                       # noqa: E402


def integrity_findings():
    try:
        return integrity.check_unclamped_limits()
    except Exception as exc:                                    # noqa: BLE001
        return [{"raised": f"{type(exc).__name__}: {exc}"}]


_found = integrity_findings()
check("the integrity check is empty", _found, [])
check("including this file, whose fixtures quote the fault",
      [f for f in _found if "test_webargs" in str(f.get("file", ""))], [])
check("and the helper, whose docstring explains it",
      [f for f in _found if "webargs" in str(f.get("file", ""))], [])

# The sweep has to be able to find one, or it is asserting about nothing --
# and it must not report the shared helper's own call sites, which is what a
# text match does.
_BAD = ast.parse('def v():\n    n = int(request.args.get("limit") or 12)\n')
check("and it names one when it is there",
      [ln for _, ln in unguarded_int_reads(_BAD, "x.py")], [2])
_GOOD = ast.parse('def v():\n    n = clamp_int(request.args.get("limit"), 12)\n')
check("the helper's own call sites are not findings",
      unguarded_int_reads(_GOOD, "x.py"), [])
_TRIED = ast.parse('def v():\n    try:\n        n = int(request.args["n"])\n'
                   '    except ValueError:\n        n = 1\n')
check("nor is one inside a try", unguarded_int_reads(_TRIED, "x.py"), [])
_PROSE = ast.parse('"""Do not write int(request.args.get("limit"))."""\n')
check("and a docstring quoting the fault is not the fault",
      unguarded_int_reads(_PROSE, "x.py"), [])


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
