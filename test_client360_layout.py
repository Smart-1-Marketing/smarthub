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
KNACK = (ROOT / "hub" / "knack_data.py").read_text(encoding="utf-8")

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
    "Approvals & proof links": "social",
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
check("Proposals spans the complete Overview row",
      'class="card c360-proposals-card"' in REC)
check("the proposal table wraps inside its card rather than scrolling",
      'class="c360-proposals-table"' in REC
      and '.c360-proposals-card .card-b{overflow-x:visible}' in REC)
check("Suite accounts use a wrapping account layout rather than a wide table",
      'class="c360-suite-list"' in REC
      and 'class="c360-suite-account"' in REC)
check("proposal upload makes a missing client website optional",
      'id="up-url"' in REC and 'Website optional' in REC
      and "if(scanWebsite){" in REC)
check("proposal upload defines the date helper it calls",
      'function todayISO(){' in REC
      and 'value="${todayISO()}"' in REC)
check("a newly attached website offers the site scan next step",
      'Run a site scan now?' in REC and '/tools/website-audit?client=' in REC)
check("Client 360 seeds searches from the complete shared client registry",
      'from hub import clients_registry as _registry' in KNACK
      and '_registry.search_clients(ql, limit=500)' in KNACK)
check("a later record selection makes earlier fetch responses inert",
      'let c360Generation=0;' in REC
      and 'if(generation!==c360Generation) return stale();' in REC
      and 'c360Generation++;' in REC)
check("a later search cannot render an earlier search result",
      'let c360SearchGeneration=0;' in REC
      and 'if(searchGeneration!==c360SearchGeneration) return;' in REC)
check("the social card validates an outbound URL before making a link",
      'function safeExternalUrl(value)' in REC
      # The social card was rebuilt as concatenation when it grew an add
      # menu and per-row controls; the guard on the href is the assertion,
      # not the string form it is written in.
      and 'safeExternalUrl(v)?' in REC
      and '\'<a href="\'+esc(safeExternalUrl(v))+\'"' in REC)
check("a failed card request is surfaced to the record rather than hidden",
      'function showC360RequestFailure(status)' in REC
      and "if(!response.ok) showC360RequestFailure(response.status);" in REC
      and 'id="c360-request-status"' in REC)

# Drive the real freshness guard. This is the failure a static spelling check
# cannot catch: A starts, B replaces it, then A finishes after B. Only B may
# settle a handler that can paint into the current record.
g0 = REC.find("let c360Generation=0;")
g1 = REC.find("function fetchJson", g0)
GENERATION_SRC = REC[g0:g1] if 0 <= g0 < g1 else ""
guard_driver = """
const pending=[];
const window={fetch:url=>new Promise(resolve=>pending.push({url,resolve}))};
""" + GENERATION_SRC + """
const applied=[];
window.fetch('https://client-a.test/').then(()=>applied.push('A'));
c360Generation++;
window.fetch('https://client-b.test/').then(()=>applied.push('B'));
pending[0].resolve({ok:true});
pending[1].resolve({ok:true});
setTimeout(()=>console.log(JSON.stringify(applied)), 0);
"""
guard_run = subprocess.run(["node", "-"], input=guard_driver,
                           capture_output=True, text=True)
check("a delayed first client's response cannot settle after a switch",
      json.loads(guard_run.stdout or "[]") if guard_run.returncode == 0 else [],
      ["B"])

# ------------------------------------------------------------------------
section("3. Assignment and outstanding-work behavior")

# Drive the real rendering functions. Static checks for the select's spelling
# would pass even if both assigned and unassigned clients still received it.
oa = REC.find("function renderOwner(d){")
ob = REC.find("function loadOwner(name){")
OWNER_SRC = REC[oa:ob] if 0 < oa < ob else ""
check("the owner rendering block can be lifted", bool(OWNER_SRC))

owner_driver = """
const nodes={
  'c-owner':{innerHTML:''},
  'c-owner-issues':{innerHTML:''}
};
const document={getElementById:id=>nodes[id]||null};
const window={CURRENT_CLIENT:'Acme'};
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
""" + OWNER_SRC + """
renderOwner({email:'aimee@smart1marketing.com',owner:'Aimee',known:true,
  users:[{email:'aimee@smart1marketing.com',name:'Aimee'}]});
const assigned=nodes['c-owner'].innerHTML;
renderOwner({email:'',owner:'',known:false,
  users:[{email:'aimee@smart1marketing.com',name:'Aimee'}]});
const unassigned=nodes['c-owner'].innerHTML;
renderClientIssues({ok:true,complete:true,issues:[{
  label:'Assets needed',title:'Spring campaign',detail:'Waiting on banners.',
  where:'Campaign Assets Needed'
}]});
console.log(JSON.stringify({assigned,unassigned,issues:nodes['c-owner-issues'].innerHTML}));
"""
owner_run = subprocess.run(["node", "-"], input=owner_driver,
                           capture_output=True, text=True)
check("the owner rendering block runs on its own", owner_run.returncode, 0)
owner_out = json.loads(owner_run.stdout or "{}") if owner_run.returncode == 0 else {}
assigned = owner_out.get("assigned", "")
unassigned = owner_out.get("unassigned", "")
issues_html = owner_out.get("issues", "")
check("an assigned client has no reassignment picker",
      'id="c-owner-pick"' in assigned, False)
check("an assigned client has no reassignment save control",
      'id="c-owner-save"' in assigned, False)
check("an assigned client cannot be reassigned through a partner-rule control",
      'id="c-owner-follow"' in assigned, False)
check("an unassigned client can still receive its first assignment",
      all(token in unassigned for token in ('id="c-owner-pick"',
                                             'id="c-owner-save"')), True)
check("outstanding work renders in a drop-down container",
      "<details" in issues_html and "Spring campaign" in issues_html, True)
check("the drop-down describes the handling screen without navigating away",
      "Handled in: Campaign Assets Needed" in issues_html
      and "href=" not in issues_html, True)
check("Client 360 no longer links its outstanding control to another screen",
      'href="/my-clients"' in REC, False)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
