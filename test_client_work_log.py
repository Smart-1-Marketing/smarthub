"""The client work log's horizon: what it reached, and what it may claim.

    python3 test_client_work_log.py

No pytest, no new dependencies, a throwaway data directory and SQLite
database.

`work_log()` assembles one client's deliverables from the activity log. It
read the newest 6000 entries HUB-WIDE and kept that client's, which fails
twice over:

  * **The window was the wrong shape.** The newest 6000 rows of everything
    this Hub logs -- sign-ins, jsonstore mirrors, scheduler ticks -- is a few
    hours of a busy day. The newest 6000 rows of the 47 modules in
    `WORK_KINDS` is months. Narrowing to those in the query is not a speed
    tweak; it is the difference between a card that can see the spring and
    one that cannot see last week.
  * **It claimed more than it had looked at.** An empty answer rendered as
    "Nothing recorded yet", `last_activity()` returned `idle`, the prospect
    card said "Nothing has been produced for this prospect yet", and Ask
    SmartHub handed a model `count: 0` as a measured figure. Every one of
    those is a statement about the WHOLE history, made by a read that stopped
    at a window, about the longest-standing clients first -- and on the health
    strip it silently replaces the 90-day churn warning a client had earned
    with "Nothing logged", which nobody chases.

`complete` is what the horizon alone cannot say: fewer entries than asked for
means the read reached the end of the log, so an empty answer really is
"nothing was ever filed". A full window means there is more underneath.

What it holds: the narrowing, the horizon, the honest count, and every reader
that used to print absence as a fact.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1worklog_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "work-log-test"
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


from hub import audit, client_brand, client_upcoming, prospect      # noqa: E402

CLIENT = "Oldest Co"
WORK = sorted(client_brand.WORK_KINDS)[0]
OTHER_WORK = sorted(client_brand.WORK_KINDS)[1]

# The client's only deliverable is filed FIRST and never touched again --
# exactly the client a window drops. Then a pile of HOUSEKEEPING rows, which
# is what a real log is mostly made of.
audit.log(WORK, "made", actor="Todd", client=CLIENT, detail="the one thing")
NOISE = 40
for i in range(NOISE):
    audit.log("auth", "signin", actor=f"Person {i}", detail=f"noise {i}")


# --------------------------------------------------- the window's shape
section("The window: narrowed to the work modules, in the query")

wide = audit.tail(limit=NOISE)
check("a hub-wide window of that size is all housekeeping",
      {e.get("module") for e in wide}, {"auth"})
check("...so the old way -- read wide, keep the work -- finds nothing",
      [e for e in wide if e.get("module") in client_brand.WORK_KINDS], [])
narrow = audit.tail(limit=NOISE, modules=tuple(client_brand.WORK_KINDS))
check("narrowed to the work modules, the same size window reaches it",
      [e.get("module") for e in narrow], [WORK])

# And the narrowing is asserted at the call, not just as a capability: a
# reader that stops passing `modules=` is back to a window of everything,
# which is the bug and not a slowdown.
_seen_kwargs = {}
_real_tail = audit.tail


def _spy(*a, **kw):
    _seen_kwargs.update(kw)
    return _real_tail(*a, **kw)


audit.tail = _spy
try:
    client_brand._work_entries()
finally:
    audit.tail = _real_tail
check("_work_entries narrows to the work modules in the query",
      set(_seen_kwargs.get("modules") or ()), set(client_brand.WORK_KINDS))


# ------------------------------------------------------------ work_log
section("work_log: the client's row, and what the read reached")

log = client_brand.work_log(CLIENT)
check("the client's deliverable is on the record", log["count"], 1)
check("...with its detail", log["items"][0]["detail"], "the one thing")
check("last_activity is the newest matching row", bool(log["last_activity"]))
check("the read reached the end of the log, so it may say so", log["complete"])
check("...and names how far back it looked", bool(log["horizon"]))
check("no error when the log answered", log["error"], "")
check("a client with nothing filed is empty, not everybody's work",
      client_brand.work_log("Nobody At All Inc")["count"], 0)


# ------------------------------------------------ the count is the client's
section("The count describes the client, not the page")

for i in range(12):
    audit.log(OTHER_WORK, "made", actor="Todd", client=CLIENT, detail=f"row {i}")

full = client_brand.work_log(CLIENT)
check("every matching row is counted", full["count"], 13)
paged = client_brand.work_log(CLIENT, limit=5)
check("a limit slices what is SHOWN, not what is counted",
      (paged["count"], paged["shown"], len(paged["items"])), (13, 5, 5))
check("...and says how many it did not show", paged["more"], 8)
check("by_source describes the client, not the page -- it used to be built "
      "from whichever rows came first",
      sum(paged["by_source"].values()), 13)
check("a limit past the end shows everything and claims no more",
      (client_brand.work_log(CLIENT, limit=500)["more"],), (0,))


# ------------------------------------------- an incomplete read may not claim
section("A read that did not reach the end may not say 'nothing'")

SHALLOW = 3
for i in range(SHALLOW + 4):
    audit.log(OTHER_WORK, "made", actor="Todd", client="Someone Else Inc",
              detail=f"later work {i}")

_real_entries = client_brand._work_entries
client_brand._work_entries = lambda limit=SHALLOW: _real_entries(SHALLOW)
try:
    shallow = client_brand.work_log(CLIENT)
    check("the shallow read does not reach this client's row", shallow["count"], 0)
    check("...and it does NOT claim to be complete", shallow["complete"], False)
    check("...it names the oldest entry it reached", bool(shallow["horizon"]))

    act = client_upcoming.last_activity(CLIENT, "", None)
    check("the health pill is NOT measured, so it cannot read 'idle'",
          act["measured"], False)
    check("...and its reason says the log goes back further",
          "further back" in (act.get("error") or ""))

    lead = {"company": CLIENT}
    sec = prospect._work(lead)
    check("the prospect card does not say 'nothing has been produced'",
          sec.get("measured"), False)
    check("...it says what it could not reach",
          "further back" in (sec.get("error") or ""))
finally:
    client_brand._work_entries = _real_entries

act = client_upcoming.last_activity(CLIENT, "", None)
check("with the real window the pill is measured again", act["measured"], True)
check("...and the client is not idle", act["state"] != "idle")


# ------------------------------------------ genuinely nothing is still idle
section("Genuinely nothing is still idle -- the answer that was worth keeping")

nobody = client_upcoming.last_activity("Never Existed LLC", "", None)
check("a client with nothing filed, on a complete read, is idle",
      (nobody["measured"], nobody["state"]), (True, "idle"))
check("...with no days rather than a zero", nobody["days"], None)


# ------------------------------------------------------ work_index agrees
section("work_index reads the same window, so the two cannot disagree")

idx = client_brand.work_index()
check("the client's rows are in the index",
      len(idx["rows"].get(client_brand._norm(CLIENT), [])), 13)
check("the index reports the same shape", sorted(idx),
      ["complete", "error", "horizon", "rows", "scanned"])
check("...and the same completeness as work_log",
      idx["complete"], client_brand.work_log(CLIENT)["complete"])


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
