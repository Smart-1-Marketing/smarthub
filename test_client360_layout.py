"""Client 360's workspace layout: every card lands in the section it belongs
to, and the controls the accordion used to supply still exist.

    python3 test_client360_layout.py

Same shape as the other test files here — no pytest, no new dependencies. The
section mapping is lifted out of the template and driven in node, the
arrangement test_menu_layout.py uses on hub-crumbs.js: a copy restated here
would be a third thing to keep in step.

## Why this file exists

The record's ~22 cards are built by render() in one flat grid and moved into
per-section views by sectionize(), matching on each card's own title. Two ways
that goes quietly wrong, and each is asserted from the direction it fails:

  1. **A set of the right size and the wrong contents.** If the title matching
     stops matching — a renamed card, an edited match list — every card falls
     through to Overview and the page still renders, complete-looking, with
     six empty sections. So the REAL titles are read out of the template's own
     card markup and each is required to land where the grouping intends,
     by name.

  2. **The controls the accordion used to carry.** hub-accordion.js is opted
     out by the data-s1-workspace marker (its reorder() would pile every card
     into the first section), and its toolbar was what carried New IO /
     Renew IO / IO from proposal / Group. Those must now come from the
     record's own actions row, or the restructure quietly retires four
     working buttons.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

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


REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")

# ------------------------------------------------------------------------
section("1. The lifted section mapping, driven in node")

a = REC.find("/* ---- c360 sections (lifted")
b = REC.find("/* ---- end c360 sections ----")
SRC = REC[a:b] if 0 < a < b else ""
check("the section block is still marked for lifting", bool(SRC))

# The titles as render() actually writes them, read from the template's own
# card markup — not a hand-typed list, which would be exactly the set that
# was already right.
titles = re.findall(r'<h3>([^<{]+)', REC)
titles = [t.replace("&amp;", "&").strip() for t in titles
          if t.strip() and "{{" not in t]
check("the template still yields a readable set of card titles",
      len(titles) >= 18)

WANT = {
    "Products & IOs": "overview",
    "Orders we have sent": "overview",
    "Smart 1 Suite Account": "overview",
    "Proposals": "overview",
    "Client Notes": "overview",
    "Invoices": "billing",
    "Website record": "website",
    "Site Health & Audits": "website",
    "What they are already spending": "website",
    "What we know about this business": "website",
    "GA4, GTM & more": "google",
    "GTM Containers": "google",
    "Traffic Summary": "google",
    "Creative Information": "creative",
    "Client Images": "creative",
    "Brand & logos": "creative",
    "Social Media": "social",
    "Social content requests": "social",
    "Tracked links": "social",
    "Form submissions": "social",
    "Work for this client": "work",
    "Web Tickets": "work",
}

driver = SRC + "\nconst out={};\n" \
    + "for(const t of " + json.dumps(list(WANT.keys())) + ")" \
    + "out[t]=c360SectionFor(t);\n" \
    + "console.log(JSON.stringify(out));\n"
r = subprocess.run(["node", "-"], input=driver, capture_output=True, text=True)
check("the lifted block runs on its own", r.returncode, 0)
got = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
for title, want_key in WANT.items():
    check(f"'{title}' lands on {want_key}", got.get(title), want_key)
check("an unknown title falls through to Overview rather than vanishing",
      json.loads(subprocess.run(
          ["node", "-"],
          input=SRC + "\nconsole.log(JSON.stringify("
                      "c360SectionFor('A Card Added Next Month')));\n",
          capture_output=True, text=True).stdout), "overview")

# Every emitted card title must be one the grouping has an opinion about —
# a card added later that nobody grouped lands on Overview by rule, and this
# is the reminder to decide rather than the silent default deciding.
check("every card the template emits is in the grouping table",
      len([t for t in titles if not any(t.startswith(k) for k in WANT)]), 0)

# ------------------------------------------------------------------------
section("2. The wiring the layout depends on")

check("the workspace marker opts hub-accordion out",
      'data-s1-workspace="1"' in REC)
ACC = (ROOT / "hub" / "static" / "hub-accordion.js").read_text(encoding="utf-8")
check("and the accordion honors it", "[data-s1-workspace]" in ACC)
check("sectionize runs right after the cards are injected",
      "$('results').innerHTML=html;\n  sectionize();" in REC)
check("the staging grid the cards render into is hidden",
      '<div id="c360Stage" hidden>' in REC)

# The accordion's toolbar carried these; suppressed, they must come from the
# record's own actions row or four working buttons quietly retire.
for act in ("ioStart('new')", "ioStart('renewal')", "ioStart('proposal')",
            "openGroupModal()"):
    check(f"the actions row still offers {act}",
          act.replace("'", "\\'") in REC or act in REC)
check("the group button keeps the class loadGroup() addresses it by",
      'class="s1-acc-group"' in REC)
check("the record asks for the icon rail like the other workbenches",
      'data-s1hub-collapse="1"' in REC)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
