"""One search box, over the clients and over the Hub itself.

    python3 test_search.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database.

## Why this file exists

The box at the top of every page was a GET form pointed at `/client360`, so
whatever you typed became a client lookup and nothing else. Twenty-two
modules, three index pages and a help registry, and the only way to reach any
of it was to know where it lived.

Three things have to hold, and each is a way a search box goes quietly wrong.

**A client the query names comes first.** That is the ask, and it is right:
this Hub's subject is a book of clients and the box is on every screen.

**...but "first" cannot mean "any client row the matcher returned".**
`search_clients()` matches a substring of the name *and of the domain*, which
is right for a type-ahead — and is why a search for "image" returned a bridal
shop whose domain contains the word. Promoted unconditionally, that row sits
above Image Creator for ever. So a *named* client is promoted and a looser hit
goes below the pages, where it costs nobody anything.

**A book that could not be read is named.** "No client called that" and "we
could not read the client book" are different answers and only the first means
check the spelling — the rule `connected_accounts_result()` gives in Google
Finder. A search that quietly returns the pages when the client book is down
is the worse of the two.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1search_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))
os.environ.setdefault("SECRET_KEY", "search-test-secret")

from hub import search_index as si  # noqa: E402

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


BOOK = [
    {"name": "Riverside HVAC", "url": "https://riversidehvac.com",
     "domain": "riversidehvac.com", "city": "Carmel", "live": True},
    {"name": "Linda's Bridal", "url": "https://lindasbridalimages.com",
     "domain": "lindasbridalimages.com", "city": "Fishers", "live": True},
]


def with_book(rows, error=None):
    import types
    fake = types.SimpleNamespace(
        search_clients=lambda q, limit=12: (
            (_ for _ in ()).throw(RuntimeError("Knack timed out")) if error
            else [r for r in rows
                  if q.lower() in r["name"].lower()
                  or q.lower() in (r["domain"] or "").lower()][:limit]))
    import hub as hub_pkg
    hub_pkg.clients_registry = fake
    sys.modules["hub.clients_registry"] = fake


# ------------------------------------------------------- 1. what is indexed
section("The Hub's own screens are in the index")

docs = si.pages()
kinds = {d["kind"] for d in docs}
check("tools, reports and help are all indexed", sorted(kinds),
      ["help", "report", "tool"])
titles = {d["title"] for d in docs}
check("a tool tiled on Creative is findable", "Image Creator" in titles)
check("a tool tiled on Client Tools is findable", "Proposal Builder" in titles)
# These seven were inline in the route that drew them, so the only list of
# them was one nothing else could read.
check("...and so is a tool filed under QA Reports",
      "Domain Renewals" in titles and "Match Sites to Clients" in titles)
check("a QA report is findable", "Sites Billing Report" in titles)
# The template says "Image Optimizer &amp; Resizer".
check("HTML entities are unescaped rather than shown",
      any("&amp;" in t for t in titles), False)
check("nothing indexed carries a client's data",
      any("client" in d and d.get("kind") != "client" for d in docs), False)


# ------------------------------------------------------- 2. the ordering
section("A client the query names is the first answer")

with_book(BOOK)
r = si.search("riverside")
check("the client leads", r["results"][0]["kind"], "client")
check("...and it is the one named", r["results"][0]["title"], "Riverside HVAC")

# The whole point of splitting named from loose. "image" matches Linda's
# Bridal only through a substring of their domain.
r = si.search("image")
check("a domain substring does not outrank the tool somebody meant",
      r["results"][0]["title"], "Image Creator")
check("...but the looser client is still an answer",
      any(x["kind"] == "client" and x["title"] == "Linda's Bridal"
          for x in r["results"]), True)
check("...and it sits below the pages",
      [x["kind"] for x in r["results"]].index("client") > 0, True)

# A whole word in the name counts as being named, which is how somebody finds
# a client whose name they only half remember.
r = si.search("bridal")
check("a whole word in the name is a naming match",
      r["results"][0]["title"], "Linda's Bridal")

check("client_first is one rule, not a tie-break",
      [x["title"] for x in si.client_first(
          [{"title": "named"}], [{"title": "page"}], [{"title": "loose"}])],
      ["named", "page", "loose"])


# -------------------------------------------------- 3. the empty answers
section("Which kind of empty it is")

r = si.search("")
check("an empty query asks rather than searching",
      r["results"], [])
check("...and says what the box is for", "Type a client" in r["note"])

r = si.search("zzzqqq nothing here")
check("nothing matching says so", r["results"], [])
check("...naming the query", "zzzqqq" in r["note"])

r = si.search("proposal")
check("pages with no client match say which they are",
      r["note"], "No client of that name — these are pages and reports.")

with_book(BOOK, error=True)
r = si.search("riverside")
check("a client book that could not be read is a finding, not a silence",
      r["errors"], ["The client book could not be read (RuntimeError)."])
check("...and the pages still answer",
      any(x["kind"] != "client" for x in r["results"]), True)


# ------------------------------------------------------- 4. wiring
section("Wired into the box on every page")

hub = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
check("there is a route for it", '@app.route("/api/search")' in hub)
check("...behind the API gate",
      "_require_api()" in hub.split("def api_search():")[1].split("@app.route")[0])
check("the QA extras are read from one list rather than two",
      "extras = qa.EXTRAS" in hub)

base = (ROOT / "hub" / "templates" / "base.html").read_text(encoding="utf-8")
# Enter has meant "look this client up" since the box existed, and that URL is
# in browser history and in links across this repo.
check("submitting still goes to Client 360",
      'class="global-search" action="/client360" method="get"' in base)
check("the dropdown is fetched from the shared route",
      "'/api/search?q='" in base)
check("a slow reply for an older query cannot land on a newer one",
      "latest === q" in base)
check("and a help topic with no page is drawn as text, not a dead link",
      "class=\"gs-row\" role=\"option\"><b>" in base)

qa = (ROOT / "hub" / "qa.py").read_text(encoding="utf-8")
check("EXTRAS is module-level so both readers see it", "\nEXTRAS = [" in qa)


shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
