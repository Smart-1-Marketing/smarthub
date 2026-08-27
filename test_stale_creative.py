"""Stale Creative's row actions: Evergreen, New, Create — and who may read it.

    python3 test_stale_creative.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

The report said how long it had been and offered nothing to do about it. Three
actions now sit at the end of each row, the Source column has gone, and every
failure below is one where a screen would go on looking healthy:

  1. **A mark taken in one gunicorn worker must be honoured by the other.**
     The audit is cached for five minutes. Bake the evergreen overlay into that
     cache and a mark made in worker A is ignored by worker B until its own
     cache expires — a button that appears to do nothing, which is the failure
     `hub/client_urls.missing()` had to undo. So the overlay is applied on
     every *read* of the cache, and this file proves it by marking a client
     while the cache is warm and asking again.

  2. **Nothing disappears in silence.** A marked client leaves the stale list
     and appears under Evergreen, carrying the group it came from, who marked
     it and when, with one press to put it back. A list that quietly gets
     shorter cannot be told from a list that failed to load.

  3. **Every count on the page moves with it**, the dashboard scorecard
     included. A row pulled from the list and still counted in the total is a
     wrong number that looks exactly like a right one.

  4. **The mark is stored against the client's name, never the derived match
     key** — `hub/client_key.py` gives the reason at length — so it is
     re-matched on read and survives the report's matcher being tightened.

  5. **The write route and the report are behind a login.** The blueprint had
     no guard at all: `wsgi.py` wraps only dispatcher-mounted modules in
     AuthGuard, and the hub app guards its own views one at a time.

  6. **The buttons open the tools that already exist** — the Campaign Change
     Request form from /campaign-request.js, and the Display Ad Builder with
     the client filled in — rather than a second copy of either.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1stale_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "stale-test-secret"

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


from hub import auth, create_hub_app                          # noqa: E402
from hub import creative_evergreen as evergreen               # noqa: E402
from hub import stale_creative                                # noqa: E402

app = create_hub_app()

signed_in = app.test_client()
signed_in.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test Rep"))
anon = app.test_client()


def audit():
    return signed_in.get("/api/qa/stale-creative").get_json()


def group(data, key):
    return [g for g in data["groups"] if g["key"] == key][0]


def listed(data):
    """Every client still on the stale list, evergreen excluded."""
    return [c["client"] for g in data["groups"] if g["key"] != "evergreen"
            for c in g["clients"]]


# =====================================================================
section("Nobody reads this report without signing in")
# =====================================================================

r = anon.get("/qa/stale-creative")
check("the page redirects to the sign-in", r.status_code, 302)
check("naming where it was going", "/login?next=/qa/stale-creative" in
      (r.headers.get("Location") or ""), True)

r = anon.get("/api/qa/stale-creative")
# A fetch gets a JSON refusal, not a login page it would report as bad data.
check("the API refuses in JSON", r.status_code, 401)
check("and says why", bool((r.get_json() or {}).get("error")), True)

r = anon.post("/api/qa/stale-creative/evergreen",
              json={"client": "Anybody", "evergreen": True})
check("and so does the write route", r.status_code, 401)
check("nothing was written", evergreen.marks(), [])


# =====================================================================
section("The list carries actions, and no longer carries Source")
# =====================================================================

html = signed_in.get("/qa/stale-creative").get_data(as_text=True)
check("the page renders", "<h1>Stale Creative</h1>" in html, True)
check("the Source column is gone", "<th>Source</th>" in html, False)
check("an Actions column is there", '<th class="acts">Actions</th>' in html, True)
check("Evergreen is offered on a row", 'data-evergreen="1"' in html, True)
check("so is New", 'data-campaign-change="1"' in html, True)
check("New opens the one campaign form, not a copy",
      '<script src="/campaign-request.js"></script>' in html, True)
check("Create opens the ad builder with the client filled in",
      "/tools/display-ads/_hub/start?client=" in html, True)

# Which of our tools filed it is not a decision a rep makes — but the panel
# they open is where it belongs, so it is dropped from the row and kept there.
check("each creative still names its own source", 'class="m">' in html, True)

# The route the Create button points at has to exist on the composed app.
rules = {str(r.rule) for r in app.url_map.iter_rules()}
check("the evergreen route is registered",
      "/api/qa/stale-creative/evergreen" in rules, True)


# =====================================================================
section("Marking one evergreen takes it off the list, not out of sight")
# =====================================================================

before = audit()
target = None
for g in before["groups"]:
    if g["key"] != "evergreen" and g["clients"]:
        target = g["clients"][0]
        from_group = g["label"]
        break

if target is None:
    # No client book in this checkout: the overlay itself is still testable,
    # and saying so beats a section that silently asserts nothing.
    print("  ..    no clients in this checkout — testing the store directly")
    check("a mark is written", evergreen.set_mark(
        "Acme Plumbing", True, actor="Test Rep").get("ok"), True)
    check("and read back under its own name",
          [m["client"] for m in evergreen.marks()], ["Acme Plumbing"])
    check("who took it is kept", evergreen.marks()[0]["by"], "Test Rep")
    check("clearing it is case-insensitive on the name",
          evergreen.set_mark("acme plumbing", False).get("ok"), True)
    check("and it is gone", evergreen.marks(), [])
else:
    name = target["client"]

    # The audit above warmed the cache. Marking now and asking again is the
    # two-worker case: if the overlay were inside the cache this would still
    # show the client on the stale list.
    check("the cache is warm", stale_creative._CACHE["data"] is not None, True)
    r = signed_in.post("/api/qa/stale-creative/evergreen",
                       json={"client": name, "evergreen": True})
    check("the mark is accepted", r.status_code, 200)

    after = audit()
    ever = group(after, "evergreen")
    check("the client is off the stale list", name in listed(after), False)
    check("and on the evergreen one",
          [c["client"] for c in ever["clients"]], [name])
    check("carrying the group it came from",
          ever["clients"][0]["evergreen"]["from_group"], from_group)
    check("and who marked it", ever["clients"][0]["evergreen"]["by"], "Test Rep")
    check("with the date", len(ever["clients"][0]["evergreen"]["at"]) >= 10, True)
    check("the elapsed time travels with it",
          ever["clients"][0]["days_since"], target["days_since"])

    check("the listed total drops by one",
          after["totals"]["clients"], before["totals"]["clients"] - 1)
    check("the evergreen total rises by one", after["totals"]["evergreen"], 1)
    check("and evergreen is not counted as needing attention",
          after["totals"]["needs_attention"] <= before["totals"]["needs_attention"],
          True)

    # The dashboard tile reads the same cache through the same overlay, or the
    # two screens would disagree about how many clients are behind.
    sc = signed_in.get("/api/qa/stale-creative/scorecard").get_json()
    check("the dashboard scorecard agrees", sc["clients"], after["totals"]["clients"])
    check("and does not list an evergreen client among the worst",
          name in [w["client"] for w in sc["worst"]], False)

    page = signed_in.get("/qa/stale-creative").get_data(as_text=True)
    check("the page offers the way back", "Not evergreen" in page, True)
    check("and says who marked it", "marked by Test Rep" in page, True)

    # Stored against the name, never the derived key: the file holds what a
    # person marked, and the match is re-made on read.
    check("the store holds the name",
          [m["client"] for m in evergreen.marks()], [name])
    check("and no derived key", "key" in evergreen.marks()[0], False)

    r = signed_in.post("/api/qa/stale-creative/evergreen",
                       json={"client": name, "evergreen": False})
    check("the mark comes off", r.status_code, 200)
    back = audit()
    check("the client is back on the stale list", name in listed(back), True)
    check("evergreen is empty again", group(back, "evergreen")["count"], 0)
    check("and the total is what it was",
          back["totals"]["clients"], before["totals"]["clients"])


# =====================================================================
section("A mark that cannot be attributed is refused, not guessed at")
# =====================================================================

r = signed_in.post("/api/qa/stale-creative/evergreen", json={"client": "   "})
check("a blank client is refused", r.status_code, 400)
check("by name", (r.get_json() or {}).get("error"), "No client named.")

check("re-pressing keeps the first mark's author", (
    evergreen.set_mark("Repeat Co", True, actor="First"),
    evergreen.set_mark("Repeat Co", True, actor="Second", note="still on"),
    evergreen.marks()[0]["by"])[2], "First")
check("and takes the newer note", evergreen.marks()[0]["note"], "still on")
check("one row per client", len(evergreen.marks()), 1)
evergreen.set_mark("Repeat Co", False)

# The overlay must never cost the report. A store that will not read reports
# nothing rather than raising into a page nobody can then open at all.
_real = evergreen.marks
evergreen.marks = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))
try:
    data = audit()
    check("an unreadable overlay still renders the report",
          isinstance(data.get("groups"), list), True)
    check("with nothing marked", group(data, "evergreen")["count"], 0)
finally:
    evergreen.marks = _real


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
