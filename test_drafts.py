"""Unfinished work that survives the interruption.

    python3 test_drafts.py

Same shape as the others: no pytest, no new dependencies, a throwaway SQLite
database and a temporary data directory, nothing reaching a third party.

## Why this file exists

Building an insertion order or a proposal is fifteen minutes of concentration
and a rep almost never gets fifteen uninterrupted minutes. The two builders
lost that interruption in opposite directions, and each half fails silently:

  * **The IO Builder kept the place and could lose the work.** Its draft went
    to `localStorage`, which survives exactly one browser -- and nothing on
    any screen said an unfinished IO existed, so on a different machine it was
    simply started again from the top. What is asserted here is the server
    copy: that it is written, listed, resumed, capped without dropping
    anything in silence, and that it cannot be addressed with a path fragment
    from a request.
  * **The Proposal Builder saved the work and lost the place.** Reopening a
    quote put the rep on step 1 of 14. The position now rides inside the
    quote's own data blob -- never a new column, because `create_all()` adds
    none to an existing table and one here would be silently absent on the
    live Postgres with every local test green.

And the two ways a draft store quietly becomes a liability: a failed read that
reads as "nobody has one" (only the first means there is nothing to pick up),
and a listing that carries the whole state blob into a page.
"""
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-drafts-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "drafts-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ.setdefault("HUB_DATA_DIR", _TMP)

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 62)


# ---------------------------------------------------------------------------
section("the store itself")
# ---------------------------------------------------------------------------
from hub import drafts                                             # noqa: E402

saved = drafts.save("io", owner="Rep One", title="Riverstone Dental",
                    step=4, state={"client": "Riverstone Dental", "items": [1]})
check("a draft is written and hands back its id", saved["ok"] and saved["id"])
one = saved["id"]

back = drafts.get(one)
check("and reads back with its state intact",
      back and back["state"]["client"] == "Riverstone Dental", back)
check("with where the rep had got to", back["step"] == 4)

again = drafts.save("io", one, owner="Rep One", title="Riverstone Dental",
                    step=6, state={"client": "Riverstone Dental", "items": [1, 2]})
check("saving the same draft updates it rather than minting a second",
      again["id"] == one and len(drafts.listing("io")) == 1)
check("and the newer state is what comes back",
      drafts.get(one)["step"] == 6)

check("an unknown kind is refused by name, never filed under a guess",
      drafts.save("invoice", state={})["error"], drafts.save("invoice", state={}))

check("a draft that cannot be serialized says so and writes nothing",
      drafts.save("io", owner="Rep One", state={"x": {1, 2}})["error"])

big = drafts.save("io", owner="Rep One", state={"pad": "x" * (drafts.MAX_BYTES + 10)})
check("and one too large to keep is refused in words the rep can act on",
      not big["ok"] and "browser" in big["error"], big)

# --- the listing -----------------------------------------------------------
drafts.save("io", owner="Rep Two", title="Icon Solar", step=1, state={"client": "Icon Solar"})
rows = drafts.listing("io", owner="Rep One")
check("the listing carries no state blob — it is read into a page",
      all("state" not in r for r in rows), rows)
check("a colleague's unfinished IO is listed too, because hiding it is how "
      "the same IO gets built twice",
      len(rows) == 2 and {r["owner"] for r in rows} == {"Rep One", "Rep Two"})
check("mine sorts first, and says which is mine",
      rows[0]["mine"] is True and rows[1]["mine"] is False, rows)

drafts.save("proposal", owner="Rep One", title="Not an IO", state={})
check("a kind filter answers about that kind alone",
      len(drafts.listing("io")) == 2 and len(drafts.listing("proposal")) == 1)

# --- what a request may not address ---------------------------------------
check("a path fragment from a request reaches nothing",
      drafts.get("../../etc/passwd") is None
      and drafts.get("../secrets") is None
      and drafts.delete("../../etc/passwd") is False)
check("and an id nobody minted is simply not there", drafts.get("nosuchdraft") is None)

# --- the cap ---------------------------------------------------------------
ids = [drafts.save("io", owner="Capped", title=f"IO {i}", step=i,
                   state={"n": i})["id"] for i in range(drafts.MAX_PER_OWNER + 3)]
mine = drafts.listing("io", owner="Capped")
mine = [r for r in mine if r["owner"] == "Capped"]
check("one owner is held to the cap", len(mine) == drafts.MAX_PER_OWNER, len(mine))
check("the newest draft is never the one dropped to make room for itself",
      ids[-1] in {r["id"] for r in mine})
last = drafts.save("io", owner="Capped", title="One more", state={"n": "last"})
check("and a dropped draft is named rather than vanishing",
      last["ok"] and last["dropped"], last)

check("deleting removes it", drafts.delete(one) and drafts.get(one) is None)
check("and deleting it twice is not an error", drafts.delete(one) is False)

# The one way the backup bites you: os.remove leaves the mirror to restore it.
src = open(os.path.join(ROOT, "hub", "drafts.py"), encoding="utf-8").read()
check("nothing here removes a mirrored file behind jsonstore's back",
      not re.search(r"os\.remove\(", src) and "jsonstore.delete_json" in src)
check("and every draft is written through jsonstore, so it outlives the disk",
      "jsonstore.write_json" in src and "open(" not in src.split('"""', 2)[-1])

# ---------------------------------------------------------------------------
section("the IO builder, through the running app")
# ---------------------------------------------------------------------------
from werkzeug.test import Client                                   # noqa: E402
import wsgi                                                        # noqa: E402
from hub import auth                                               # noqa: E402

staff = Client(wsgi.application)
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Harness"),
                 domain="localhost")
stranger = Client(wsgi.application)

check("an unfinished IO is not readable without a Hub login",
      stranger.get("/tools/io/api/drafts").status_code in (302, 401, 403),
      stranger.get("/tools/io/api/drafts").status_code)

made = staff.post("/tools/io/api/draft", json={
    "step": 7,
    "state": {"state": {"client": "Riverstone Dental", "orderNumber": "10412"},
              "index": 7, "currentProduct": 0}}).get_json()
check("the builder can write one", made["ok"] and made["id"], made)
did = made["id"]

listed = staff.get("/tools/io/api/drafts").get_json()
row = [r for r in listed["drafts"] if r["id"] == did]
check("it appears on the start screen's list", len(row) == 1, listed)
check("named by the client and the order number, which is what a rep "
      "recognizes it by",
      row[0]["title"] == "Riverstone Dental - IO 10412", row[0])
check("filed against whoever is signed in", row[0]["owner"] == "Harness")
check("and the list says it could look, which is not the same answer as "
      "nobody having one",
      listed["measured"] is True)

got = staff.get(f"/tools/io/api/draft/{did}").get_json()
check("resuming hands back the whole state",
      got["ok"] and got["draft"]["state"]["state"]["client"] == "Riverstone Dental")
check("a proposal draft is not an insertion order",
      staff.get("/tools/io/api/draft/"
                + drafts.listing("proposal")[0]["id"]).status_code == 404)

second = staff.post("/tools/io/api/draft", json={
    "id": did, "step": 9,
    "state": {"state": {"client": "Riverstone Dental", "orderNumber": "10412"},
              "index": 9}}).get_json()
check("autosaving again updates the same row rather than filling the list",
      second["id"] == did
      and len([r for r in staff.get("/tools/io/api/drafts").get_json()["drafts"]
               if r["id"] == did]) == 1)

check("discarding it works", staff.delete(f"/tools/io/api/draft/{did}").get_json()["deleted"])
check("and discarding it again is not an error, because deleted, never "
      "existed and somebody else's all mean the same thing here",
      staff.delete(f"/tools/io/api/draft/{did}").get_json()["ok"])

# ---------------------------------------------------------------------------
section("the IO builder's own page")
# ---------------------------------------------------------------------------
page = staff.get("/tools/io/").get_data(as_text=True)
check("the page still renders", "startScreen" in page)
check("the start screen offers the unfinished ones", 'id="startDraftsPanel"' in page)
calls = re.findall(r"(?:fetch|sendBeacon)\(\s*[\"']([^\"']*/api/drafts?[^\"']*)",
                   page)
check("every draft call carries the mount — a root-absolute /api/draft leaves "
      "this module and reaches a different app on the hub",
      calls and all(c.startswith("/tools/io/api/draft") for c in calls), calls)
check("a tab closing still saves, which is exactly the interruption this is for",
      "sendBeacon" in page and "pagehide" in page)
check("and the beacon carries it too — sendBeacon returns a boolean nobody "
      "reads and fires on pagehide, so a wrong path fails in total silence",
      'sendBeacon("/tools/io/api/draft"' in page)

tpl = open(os.path.join(ROOT, "modules", "io_builder", "templates",
                        "index.html"), encoding="utf-8").read()
check("starting a new IO drops this browser's copy and leaves the server "
      "draft on the list, where discarding it is a deliberate press",
      "forgetLocalDraft" in tpl and "else{forgetLocalDraft();}" in tpl)
check("submitting clears both copies", "clearDraft()" in tpl
      and 'method:"DELETE"' in tpl.split("function clearDraft")[1][:800])
check("and so does Reset, whose confirmation says it clears the saved draft — "
      "left to localStorage alone the reset IO comes straight back on the list",
      "clearDraft()" in tpl.split("function restart()")[1][:600])
check("both deletes are keepalive, because either is followed by a reload "
      "that would cancel a plain fetch",
      "keepalive:true" in tpl.split("function clearDraft")[1][:800])

# ---------------------------------------------------------------------------
section("the proposal builder resumes where it was left")
# ---------------------------------------------------------------------------
sales = open(os.path.join(ROOT, "modules", "sales_builder", "templates",
                          "index.html"), encoding="utf-8").read()
check("the wizard position is stamped on every save", "S._step=step" in sales)
check("and reopening a quote reads it rather than forcing step one",
      "step=resumeStep();" in sales and "function resumeStep()" in sales)
check("clamped, because a step index out of range is a blank builder over a "
      "quote whose answers are all intact",
      "Math.min(n,STEPS.length-1)" in sales)
check("arriving mid-wizard says so and offers the top — landing on step 9 "
      "with no explanation reads as the tool having skipped ahead",
      "drawResumeNote" in sales and "Start at step 1" in sales)
check("and the note is retired the moment the rep moves",
      sales.count("clearResumeNote()") >= 3)

steps = re.search(r"const STEPS=\[(.*?)\n\];", sales, re.S)
n_steps = len(re.findall(r"\n \(\)=>", steps.group(1))) if steps else 0
count = re.search(r"const STEP_COUNT=(\d+);", sales)
check("the list's step count is the wizard's own — two numbers that can "
      "disagree is a draft row claiming a step the wizard does not have",
      count and n_steps and int(count.group(1)) == n_steps,
      (count.group(1) if count else None, n_steps))

builder = sys.modules.get("salesb_app")
if builder is None:                             # pragma: no cover - mount failed
    from modules.sales_builder import app as builder

quote = staff.post("/sales/builder/api/quotes",
                   json={"data": {"client": "Riverstone Dental", "_step": 8}}
                   ).get_json()["quote"]
check("the saved position rides in the quote's own data blob, never a new "
      "column create_all() would not add to the live Postgres",
      "step" not in [c.name for c in builder.Quote.__table__.columns],
      [c.name for c in builder.Quote.__table__.columns])
check("and the list can say a draft is half-finished rather than only that "
      "it is a draft", quote["step"] == 8, quote.get("step"))
plain = staff.post("/sales/builder/api/quotes",
                   json={"data": {"client": "Icon Solar"}}).get_json()["quote"]
check("a quote nobody has left mid-wizard reports no position at all",
      plain["step"] == 0)

# ---------------------------------------------------------------------------
section("a ?quote= link that outlives its quote still lands somewhere")
# ---------------------------------------------------------------------------
# Client 360 links a proposal by id and a draft can be deleted, so the deep
# link is the one caller of editQuote() with no screen drawn behind it yet.
# It used to toast the refusal and stop: the rep landed on the empty
# dashboard shell — a blank page with a three-second message, from a link
# that looked perfectly good. The branch and editQuote are lifted out of the
# page and driven in node rather than restated, the test_proposal_targeting
# arrangement, because a copy in the test is a third thing to keep in step.
import subprocess as _sub                                          # noqa: E402

_eq = re.search(r"(async function editQuote\(.*?)\nfunction resumeStep\(",
                sales, re.S)
_branch = re.search(r"(if\(qs\.get\('quote'\)\)\{.*?return;\})", sales, re.S)
check("the deep-link branch and editQuote are still liftable",
      bool(_eq) and bool(_branch))
if _eq and _branch:
    _harness = (
        "let navved=null,toasts=[];\n"
        "function nav(v){navved=v;}\n"
        "function toast(m){toasts.push(String(m));}\n"
        "function editLoaded(q){}\n"
        + _eq.group(1) + "\n"
        + "async function arrive(qs){" + _branch.group(1) + "}\n"
        + """
(async()=>{
  const out={};
  // The stale link: the API refuses, the page must still show something.
  api=async()=>{throw new Error("Quote not found");};
  await arrive(new Map([["quote","999"]]));
  out.fallback={nav:navved,toasted:toasts.length>0};
  // The ordinary link: the quote opens and nothing yanks the rep elsewhere.
  navved=null;toasts=[];
  api=async()=>({quote:{id:7,data:{}}});
  let loaded=false;editLoaded=()=>{loaded=true;};
  await arrive(new Map([["quote","7"]]));
  out.opened={nav:navved,loaded:loaded};
  console.log(JSON.stringify(out));
})();
""")
    _js = os.path.join(_TMP, "deeplink.js")
    open(_js, "w", encoding="utf-8").write(_harness)
    try:
        _r = json.loads(_sub.run(["node", _js], capture_output=True, text=True,
                                 timeout=30, check=True).stdout)
        check("a deleted quote's link falls back to the dashboard, in words",
              _r["fallback"]["nav"] == "dashboard" and _r["fallback"]["toasted"],
              _r["fallback"])
        check("a live quote's link opens the quote and stays there",
              _r["opened"]["loaded"] and _r["opened"]["nav"] is None,
              _r["opened"])
    except FileNotFoundError:
        print("  skip node is not installed — the deep-link branch is unchecked")
    except _sub.CalledProcessError as exc:
        check("the deep-link branch runs", False, exc.stderr[:400])

# ---------------------------------------------------------------------------
print("\n" + "-" * 62)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
