"""Client 360's Social suggestions card: the planner's idea board on the
record, read from the planner's own store.

    python3 test_client360_social_ideas.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database. The renderer is
lifted out of the template and driven in node, test_client360_layout.py's
arrangement, so the three empties it keeps apart are asserted rather than
eyeballed.

What it holds:

  1. **/api/client/social-ideas reads the planner's store, not a copy.** An
     idea added through modules/social_planner/ideas.py is on the record at
     once, with the client's answer and the tag weights beside it.
  2. **The three empties stay apart.** Planner unreadable, no ideas yet, and
     ideas nobody has swiped on are three answers with three different next
     steps; a card that draws them alike sends a rep to the wrong screen.
  3. **The card lands under Social** (the section map) and its writes go to
     the planner's own routes rather than a second copy in the hub.
  4. **No token in the link** reaches the page as anything but the URL the
     client is meant to receive; the clipboard goes through copyToClipboard.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="c360ideas_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "c360-ideas-test"
os.environ["PANEL_PASSWORD"] = "c360-ideas-pass"
os.environ.pop("OPENAI_API_KEY", None)

_passed, _failed = 0, 0


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


CLIENT, URL = "Buckeye Lake Winery", "https://buckeyelakewinery.com"

# ------------------------------------------------------------------------
section("1. The route reads the planner's store")

from hub import auth, create_hub_app                       # noqa: E402
from hub.extensions import create_all                      # noqa: E402
from modules.social_planner import ideas                   # noqa: E402

app = create_hub_app()
create_all(app)
anon = app.test_client()
check("a stranger is refused", anon.get("/api/client/social-ideas?name=x").status_code, 401)
staff = app.test_client()
staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"))

d = staff.get("/api/client/social-ideas?name=" + CLIENT).get_json()
check("no ideas yet is measured, not an error", (d.get("measured"), d.get("ideas")), (True, []))
check("the counts are zero rather than absent", d.get("counts"), {"pending": 0, "liked": 0, "passed": 0, "promoted": 0})
check("every tag is offered for the add form", len(d.get("tags") or []) >= 5, True)
check("the client's ideas link is built", "/tools/social/c/" in (d.get("link") or {}).get("url", "") and d["link"]["url"].endswith("/ideas"), True)
check("no client name is required to be in Knack", d.get("client"), CLIENT)
check("an unnamed client is not measured", staff.get("/api/client/social-ideas").get_json().get("measured"), False)

a = ideas.add(CLIENT, URL, title="Meet the winemaker", idea_tag="team_spotlight", origin="staff")
b = ideas.add(CLIENT, URL, title="Fall release weekend", idea_tag="seasonal", origin="agent", source="model")
ideas.respond(a["id"], "liked")
d = staff.get("/api/client/social-ideas?name=" + CLIENT + "&url=" + URL).get_json()
check("ideas added through the planner are on the record", sorted(r["title"] for r in d["ideas"]),
      ["Fall release weekend", "Meet the winemaker"])
check("the client's answer rides along", {r["title"]: r["response"] for r in d["ideas"]},
      {"Fall release weekend": "pending", "Meet the winemaker": "liked"})
check("counts follow", (d["counts"]["pending"], d["counts"]["liked"]), (1, 1))
check("the tag label is the planner's, not restated", next(r["tag_label"] for r in d["ideas"] if r["tag"] == "team_spotlight"),
      "Somebody who works here")
w = next(x for x in d["weights"] if x["tag"] == "team_spotlight")
check("the swipe moved the tag weight the card shows", (w["liked"], w["answered"]), (1, 1))
check("no idea id leaks a batch or slot it was never promoted to", all(r["promoted"] is False for r in d["ideas"]), True)

# ------------------------------------------------------------------------
section("2. The card and its wiring in the template")

def _c360_source():
    """The Client 360 record as one text: the template plus its script modules
    (hub/client360_assets.MODULES), because the record's JavaScript lives in
    files now and a check that asks what the record does reads all of it."""
    _os, _sys = __import__("os"), __import__("sys")
    _root = _os.path.dirname(_os.path.abspath(__file__))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    return __import__("hub.client360_assets", fromlist=["source_text"]).source_text()

REC = _c360_source()
check("the card is emitted", 'id="c-social-ideas"' in REC and "<h3>Social suggestions</h3>" in REC, True)
check("it sits right after Social content requests",
      REC.find('id="c-social-content"') < REC.find('id="c-social-ideas"'), True)
check("the section map claims it for Social", "'social suggestions'" in REC, True)
check("the loader honors the generation guard",
      "function loadSocialIdeas(name,note){" in REC and REC[REC.find("function loadSocialIdeas"):].find("if(gen!==c360Generation) return;") > 0, True)
check("it is loaded with the rest of the record", "loadSocialIdeas(name);" in REC[REC.find("function loadBrandAndWork"):], True)
check("writes go to the planner's own routes, not a hub copy",
      "'/tools/social'+path" in REC and "/api/ideas/generate" in REC, True)
check("the AI button is labeled as AI", "Suggest more (AI)" in REC, True)
check("the clipboard goes through copyToClipboard",
      len(re.findall(r"navigator\.clipboard\.writeText\(", REC)), 1)
HUB = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
check("the hub route imports the planner's store rather than copying it",
      "from modules.social_planner import ideas as _ideas" in HUB, True)

# ------------------------------------------------------------------------
section("3. The lifted renderer keeps its empties apart (node)")

a_ = REC.find("/* ---- social suggestions (lifted")
b_ = REC.find("/* ---- end social suggestions ----")
SRC = REC[a_:b_] if 0 < a_ < b_ else ""
check("the renderer is marked for lifting", bool(SRC))
if shutil.which("node") and SRC:
    driver = ("const esc=s=>String(s??'');\n" + SRC + "\nconst out={"
              "bad:renderSocialIdeas({measured:false,error:'Planner down'}),"
              "none:renderSocialIdeas({measured:true,ideas:[],counts:{},weights:[],tags:[{key:'promo',label:'An offer'}],link:{url:'https://x/tools/social/c/T/ideas'}}),"
              "unswiped:renderSocialIdeas({measured:true,ideas:[{title:'Idea one',response:'pending',tag_label:'An offer',origin:'agent',source:'model'}],counts:{pending:1},weights:[{answered:0}],tags:[],link:{url:'https://x/c'}}),"
              "swiped:renderSocialIdeas({measured:true,ideas:[{title:'Idea one',response:'liked',tag_label:'An offer',origin:'staff'}],counts:{liked:1},weights:[{label:'An offer',answered:1,liked:1,passed:0,weight:2}],tags:[],link:{revoked:true}})"
              "};console.log(JSON.stringify(out));\n")
    r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
    check("the lifted block runs on its own", r.returncode, 0)
    out = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    check("unreadable says so and offers nothing", "Planner down" in out.get("bad", "") and "si-more" not in out.get("bad", ""), True)
    check("no ideas yet offers the two ways to make some", "No ideas yet" in out.get("none", "") and 'id="si-more"' in out.get("none", ""), True)
    check("ideas nobody swiped on points at the link", "has swiped yet" in out.get("unswiped", ""), True)
    check("a swiped idea shows what they respond to", "What this client responds to" in out.get("swiped", "") and "1/1" in out.get("swiped", ""), True)
    check("a revoked link is said, not printed", "turned off" in out.get("swiped", "") and "https://x/c" not in out.get("swiped", ""), True)
    check("the copy button carries the link", 'data-si-copy="https://x/tools/social/c/T/ideas"' in out.get("none", ""), True)
else:
    print("  skip  node not available")

CI = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
check("this file runs in CI", "python3 test_client360_social_ideas.py" in CI, True)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
