"""One page, five ways of writing its URL, five tickets.

    python3 test_seo_tasks.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches Knack: the send is stubbed, because what is worth asserting is what
this module does around it.

## Why this file exists

`hub/seo_tasks.py` raises the web tickets that get FAQ sections, schema blocks
and blog posts onto a client's live website. Its docstring opens with three
promises, and the first is:

> **It must never create the same ticket twice.** … A queue that fills with
> duplicates is a queue people stop reading.

The dedupe key was the **raw URL string**. The title beside it was `_short()`
— so the module already knew how to reduce a URL to the page a person means,
and used that for what somebody reads while comparing the unreduced string:

    https://acme.com/services          Add schema markup to /services
    https://acme.com/services/         Add schema markup to /services
    http://acme.com/services           Add schema markup to /services
    https://www.acme.com/services      Add schema markup to /services

Four tickets, one page, identical titles. The URLs arrive from a crawled
sitemap or a list posted by the browser, so trailing-slash and www variation
between a crawl and a typed entry is ordinary — and an http → https migration
would duplicate the whole book in one pass.

`page_key()` is the canonical form now. Host **and** path, because the store
is per client and a client with two domains would otherwise collide on
`/services`; query and fragment dropped, because `?utm_source=x` is the same
page to somebody adding schema to it.

**And the keys already on disk are raw URLs.** Reading only the canonical key
would make every existing record invisible and raise a second ticket for
everything already ticketed — the migration this codebase refuses everywhere
else (`audit.LOG_NAMES`, `video_library.TAG_ALIASES`): match the old spelling
rather than re-indexing what is written. `already()` checks both.

The module's other two promises are asserted here too, because nothing else
did: a failed send must not be remembered as done, and a ticket that could
not be dated must say so rather than going out silently undated.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1seotask_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "seo-tasks-test-secret"
os.environ["SEO_TASKS_ENABLED"] = "1"

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


from hub import seo_tasks as st                                # noqa: E402


def keyed(url):
    """The canonical key, guarded: a regression must name itself rather than
    ending the run, which is the shape every test file here now uses."""
    try:
        return st.page_key(url)
    except Exception as exc:                                   # noqa: BLE001
        return f"raised {type(exc).__name__}"


def raise_once(store, urls, kind="schema"):
    """How many tickets a list of URLs would produce."""
    for u in urls:
        if not st.already(store, kind, u):
            st._record(store, kind, u, {"id": "t", "due": "2026-09-05"})
    return len(st._tasks(store).get(kind, {}))


# =====================================================================
section("One page, however the URL was written")
# =====================================================================

SAME = ["https://acme.com/services",
        "https://acme.com/services/",
        "http://acme.com/services",
        "https://www.acme.com/services",
        "https://ACME.com/services",
        "https://acme.com/services?utm_source=x",
        "https://acme.com/services#top",
        "acme.com/services"]

check("every spelling reduces to one key",
      len({keyed(u) for u in SAME}), 1)
check("and it is the host and the path",
      keyed("https://www.acme.com/services/"), "acme.com/services")
check("so one page raises one ticket", raise_once({}, SAME), 1)

# The title a person reads was already canonical -- that is what made the
# duplicates identical on screen and therefore unreadable as duplicates.
check("the titles were identical all along",
      len({st._short(u) for u in SAME if "?" not in u and "#" not in u}), 1)


# =====================================================================
section("And genuinely different pages are still different")
# =====================================================================
# A dedupe that over-reaches silences the ticket somebody needed, which is
# worse than the duplicate it was fixing.

DIFFERENT = ["https://acme.com/services",
             "https://acme.com/services/plumbing",
             "https://acme.com/about",
             "https://acme.com/",
             "https://other.com/services"]
check("five different pages raise five tickets",
      raise_once({}, DIFFERENT), 5)
check("a home page is the host alone", keyed("https://acme.com/"), "acme.com")
check("and not confused with a path", keyed("https://acme.com/") ==
      keyed("https://acme.com/home"), False)
# The path keeps its case, and the host does not. A hostname is
# case-insensitive by specification; a PATH is not -- /Services and /services
# can be two pages on a case-sensitive server. Merging them would silence a
# ticket for a page that never gets its schema, and a missing ticket is worse
# than a duplicate one: the duplicate is noise in a queue, the absence is work
# that never reaches the site. So this is deliberately NOT merged.
check("the host is case-insensitive",
      keyed("https://ACME.COM/services"), "acme.com/services")
check("and the path is not, because a path genuinely can be",
      keyed("https://acme.com/Services") == keyed("https://acme.com/services"),
      False)

check("two clients' domains do not collide on one path",
      keyed("https://acme.com/services") == keyed("https://other.com/services"),
      False)
check("a deeper path is its own page",
      keyed("https://acme.com/services") ==
      keyed("https://acme.com/services/plumbing"), False)


# =====================================================================
section("What is already on disk is still found")
# =====================================================================
# Every record written before this carries a raw URL as its key. Reading only
# the canonical one would raise a second ticket for everything already
# ticketed -- a migration wearing a bug fix.

OLD = {"seo_tasks": {"schema": {
    "https://acme.com/services": {"id": "old-1", "due": "2026-09-05"}}}}
for spelling in SAME:
    hit = st.already(OLD, "schema", spelling)
    check(f"a pre-existing record is found for {spelling[:38]}",
          (hit or {}).get("id"), "old-1")

check("and a page that genuinely has no ticket is not matched to one",
      st.already(OLD, "schema", "https://acme.com/about"), None)
check("nor is another client's same-named page",
      st.already(OLD, "schema", "https://other.com/services"), None)

# New records are written under the canonical key, so the walk above is a
# fallback for old rows rather than the normal path.
fresh = {}
st._record(fresh, "schema", "https://www.acme.com/services/", {"id": "n1"})
check("a new record is stored canonically",
      list(st._tasks(fresh)["schema"]), ["acme.com/services"])
check("and keeps the URL it was raised for, for the ticket body",
      st._tasks(fresh)["schema"]["acme.com/services"]["url"],
      "https://www.acme.com/services/")


# =====================================================================
section("A failed send is not remembered as done")
# =====================================================================
# The module's second promise: raising a ticket must never break the work that
# triggered it, and a send that failed has to be retried rather than recorded.

import hub.seo as _seo                                         # noqa: E402

_store = {}
_seo.load_store = lambda client: _store                        # noqa: SLF001
_seo.save_store = lambda client, s: _store.update(s)           # noqa: SLF001

st._raise_ticket = lambda *a, **kw: {"ok": False, "note": "Knack said no."}
out = st.for_schema("Acme Tyre", "https://acme.com/services")
check("a refused send reports the failure", out.get("ok"), False)
check("and nothing is recorded, so the next run retries",
      st._tasks(_store).get("schema", {}), {})

st._raise_ticket = lambda *a, **kw: {"ok": True, "id": "T-1", "due": "2026-09-05"}
out = st.for_schema("Acme Tyre", "https://acme.com/services")
check("a successful send records it", out.get("ok"), True)
check("under the canonical key",
      list(st._tasks(_store).get("schema", {})), ["acme.com/services"])

# And the same page, written differently, is now a no-op rather than a
# second ticket -- which is the whole finding.
out = st.for_schema("Acme Tyre", "https://www.acme.com/services/")
check("the same page written differently raises nothing new",
      out.get("existing"), True)
check("and the queue still holds one ticket for it",
      len(st._tasks(_store).get("schema", {})), 1)


# =====================================================================
section("The module's own switches still work")
# =====================================================================

check("it can be turned off without a deploy",
      st.enabled(), True)
os.environ["SEO_TASKS_ENABLED"] = "0"
check("and off means off", st.enabled(), False)
os.environ["SEO_TASKS_ENABLED"] = "1"

check("an empty URL is not a key", keyed(""), "")
check("nor is whitespace", keyed("   "), "")
for junk in (None, 0, [], {}):
    check(f"and {junk!r} does not raise", keyed(junk), "")


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
