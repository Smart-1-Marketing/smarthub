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
    "Coming up": "overview",

    "Ad performance": "overview",
    "CamHub live cam": "overview",
    # Overview is the one-screen summary now; the presence cards, the landing
    # pages and the audience live with their kin.
    "Landing pages": "website",
    "Google listing": "google",
    "YouTube channel": "social",
    "Email campaigns": "social",
    "Smart 1 Suite Account": "overview",
    "Pipeline & leads": "overview",
    "Proposals": "overview",
    "Client Notes": "overview",
    "Target audience": "creative",
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
    "YouTube accounts": "social",
    "Social content requests": "social",
    "Social suggestions": "social",
    "Tracked links": "social",
    "Form submissions": "social",
    "Approvals & proof links": "social",
    "Work for this client": "work",
    "Web Tickets": "work",
    "Execution plan": "work",
    # Skill-gated (modules/skills360): drawn only when the skill is on.
    "Ecommerce": "skills",
    "Email Creator": "skills",
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
#
# The owner-only renderOwner() was replaced by renderRoles(), which draws the
# whole Partner / Assigned / Client Success / Followers strip from the
# combined /api/client/roles payload -- one function rather than one per role,
# the reason `hub-crumbs.js`'s own map is lifted whole rather than restated.
oa = REC.find("function renderRoles(d){")
ob = REC.find("function loadOwner(name){")
OWNER_SRC = REC[oa:ob] if 0 < oa < ob else ""
check("the roles rendering block can be lifted", bool(OWNER_SRC))

owner_driver = """
const nodes={
  'c-owner':{innerHTML:'', querySelectorAll:()=>[]}
};
const document={getElementById:id=>nodes[id]||null};
const window={CURRENT_CLIENT:'Acme'};
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
""" + OWNER_SRC + """
renderRoles({partner:'',
  assigned:{email:'aimee@smart1marketing.com',name:'Aimee',known:true},
  client_success:{raw:'',known:false}, followers:[],
  users:[{email:'aimee@smart1marketing.com',name:'Aimee'}]});
const assigned=nodes['c-owner'].innerHTML;
renderRoles({partner:'',
  assigned:{email:'',name:'',known:false},
  client_success:{raw:'',known:false}, followers:[],
  users:[{email:'aimee@smart1marketing.com',name:'Aimee'}]});
const unassigned=nodes['c-owner'].innerHTML;
console.log(JSON.stringify({assigned,unassigned}));
"""
owner_run = subprocess.run(["node", "-"], input=owner_driver,
                           capture_output=True, text=True)
check("the roles rendering block runs on its own", owner_run.returncode, 0)
owner_out = json.loads(owner_run.stdout or "{}") if owner_run.returncode == 0 else {}
assigned = owner_out.get("assigned", "")
unassigned = owner_out.get("unassigned", "")
check("an assigned client has no reassignment picker",
      'id="c-owner-pick"' in assigned, False)
check("an assigned client has no reassignment save control",
      'id="c-owner-save"' in assigned, False)
check("an assigned client cannot be reassigned through a partner-rule control",
      'id="c-owner-follow"' in assigned, False)
check("an unassigned client can still receive its first assignment",
      all(token in unassigned for token in ('id="c-owner-pick"',
                                             'id="c-owner-save"')), True)
# George's call: the "N outstanding issues -- show details" disclosure comes
# off Client 360 entirely. Its underlying report (hub/client_health.py) still
# feeds the "Outstanding" pill in the health strip -- this only asserts the
# owner card no longer draws or fetches it.
check("renderRoles no longer draws an outstanding-issues container",
      'c-owner-issues' in assigned, False)
# The header used to say Salesperson and Partner under the name AND in the
# roles row. One place: the roles row, now titled Smart 1 Internal, with the
# assignment's "since <date> by <user>" and "managed from" text gone and a
# Client Warnings column on the right.
check("the header no longer repeats Salesperson / Partner under the name",
      "'Salesperson: '" in REC, False)
check("the roles row is titled Smart 1 Internal", "Smart 1 Internal" in assigned)
check("and carries the salesperson", "Salesperson" in assigned)
check("an assigned client no longer reads 'managed from Client Assignments'",
      "managed from Client Assignments" in assigned, False)
check("nor 'since <date>'", "since " in assigned, False)
check("the row has a Client Warnings column",
      "Client Warnings" in assigned and 'id="c360Warnings"' in assigned)

# ------------------------------------------------------------------------
section("4. The Client Warnings column, driven in node")
wa = REC.find("/* ---- c360 warnings (lifted")
wb = REC.find("/* ---- end c360 warnings ----")
WARN_SRC = REC[wa:wb] if 0 < wa < wb else ""
check("the warnings block is marked for lifting", bool(WARN_SRC))
warn_driver = """
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const c360RefreshButton=()=>'<button class="c360-refresh">Refresh</button>';
""" + WARN_SRC + """
const out={
  pending: renderC360WarningsHtml(null, []),
  clear: renderC360WarningsHtml([], []),
  queue: renderC360WarningsHtml([
    {level:'warn', section:'overview', title:'6 products ending within 21 days', detail:'Display in 9 days'},
    {level:'bad', section:'billing', title:'Live products with no monthly amount on file'}], []),
  local: renderC360WarningsHtml([], [{level:'warn', section:'overview', title:'No contact on file'}]),
  unread: renderC360WarningsHtml({error:'HTTP 502'}, []),
};
console.log(JSON.stringify(out));
"""
warn_run = subprocess.run(["node", "-"], input=warn_driver, capture_output=True, text=True)
check("the warnings block runs on its own", warn_run.returncode, 0)
w = json.loads(warn_run.stdout or "{}") if warn_run.returncode == 0 else {}
check("before the health strip answers it says so", "Checking" in w.get("pending", ""))
check("a clean record says nothing is flagged", "Nothing flagged" in w.get("clear", ""))
check("the health queue's ending-soon warning is drawn",
      "6 products ending within 21 days" in w.get("queue", ""))
check("worst first: the bad row precedes the warn row",
      w.get("queue", "").find("no monthly amount") < w.get("queue", "").find("ending within"))
check("each row jumps to its section", 'data-go="billing"' in w.get("queue", ""))
check("the record's own contact warning is drawn", "No contact on file" in w.get("local", ""))
check("a health strip that could not be read says so, with a Refresh",
      "could not be read" in w.get("unread", "") and "c360-refresh" in w.get("unread", ""))

# ------------------------------------------------------------------------
section("5. The rest of the header work")
sec_run = subprocess.run(["node", "-"], input=SRC + "\nconsole.log(C360_SECTIONS[1].key);\n",
                         capture_output=True, text=True)
check("Work & requests sits directly under Overview in the rail",
      (sec_run.stdout or "").strip(), "work")
check("a failed request offers a Refresh button, not a hint",
      "function c360Refresh()" in REC and "c360RefreshButton()" in REC
      and "showC360RequestFailure" in REC)
check("Create display ads and Email client & history are styled as buttons",
      ".c360-actions button,.c360-actions a{" in REC)
check("the scan's screenshots load beside the name and hide when the image fails",
      'id="c360Shots"' in REC and "function loadScreenshots(name)" in REC
      and "img.onerror=()=>{ b.remove();" in REC and "/api/client/screenshots?domain=" in REC)
check("clicking a screenshot opens the lightbox",
      'id="shotLightbox"' in REC and "function openShot(url, caption, scanUrl)" in REC)
check("the category pill is editable, with a dropdown and your-own wording",
      "__custom" in REC and "body.custom=text" in REC and "function c360EditIndustry()" in REC)
check("saving the category refreshes the Client Info strip too",
      "window.__c360reloadProfile" in REC)
check("the Client Info category opens the same editor",
      'onclick="c360EditIndustry();return false"' in REC)
check("a record with no contact shows a warning with an Add contact button",
      "c360-contact-warn" in REC and 'id="profAddFirst"' in REC)
check("contacts carry a level dropdown with Make primary",
      'class="pc-role"' in REC and "Make primary" in REC)
check("the QuickBooks contact can be pulled from the strip",
      "/api/client/profile/qb-sync" in REC)
check("the creative table's third column has room between its controls",
      'class="c360-cre-acts"' in REC)

# ------------------------------------------------------------------------
section("6. The quick wins: no alert boxes, no page reloads, real buttons")
check("no browser alert() box is left on the record", "alert(" in REC, False)
check("the one page reload left is the Refresh button's fallback with no client",
      REC.count("location.reload()"), 1)
na = REC.find("/* ---- c360 notices (lifted")
nb = REC.find("/* ---- end c360 notices ----")
NOTICE_SRC = REC[na:nb] if 0 < na < nb else ""
check("the notice block is marked for lifting", bool(NOTICE_SRC))
notice_driver = """
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
""" + NOTICE_SRC + """
console.log(JSON.stringify({
  err: c360NoticeHtml('That did not save.'),
  ok: c360NoticeHtml('Brand guide sent.', 'ok'),
  escaped: c360NoticeHtml('<b>x</b>'),
}));
"""
notice_run = subprocess.run(["node", "-"], input=notice_driver, capture_output=True, text=True)
check("the notice block runs on its own", notice_run.returncode, 0)
nt = json.loads(notice_run.stdout or "{}") if notice_run.returncode == 0 else {}
check("a failure is an alert-role error card", 'c360-toast err' in nt.get("err", "") and 'role="alert"' in nt.get("err", ""))
check("a success is a status card", 'c360-toast ok' in nt.get("ok", "") and 'role="status"' in nt.get("ok", ""))
check("the text is escaped", "&lt;b&gt;x&lt;/b&gt;" in nt.get("escaped", ""))
check("every notice can be dismissed", 'class="tclose"' in nt.get("err", ""))
check("QuickBooks attach and detach re-run the Invoices card, not the page",
      "function loadQbCard()" in REC and REC.count("loadQbCard();") >= 3)
# An anchor with a pointer cursor and no href cannot be reached from the
# keyboard. Every control that was one is a button now.
stray = [m for m in re.findall(r'<a class="(?:gbtn|open-link|btn-primary)[^"]*"([^>]*)>', REC)
         if "href=" not in m and "c-client-links-open" not in m]
check("no link-styled control is left without an href", len(stray), 0)
check("the button forms keep the shared styling",
      "button.gbtn,button.open-link,button.btn-primary{font-family:inherit;cursor:pointer}" in REC)
check("the primary contact's phone is a tel: link", 'href="tel:' in REC)
check("the email contact line lives in the Client Info strip, not above the health strip",
      'id="c360EmailInline"' in REC and 'id="c360EmailSummary"' not in REC
      and "function paintEmailLine()" in REC)

# ------------------------------------------------------------------------
section("7. The bigger improvements: a slim Overview, rail badges, Ends, score, latest note")
ov = [t for t, k in WANT.items() if k == "overview"]
check("Overview holds nine cards, not thirteen", len(ov), 9)
counts_driver = """
const esc=s=>String(s??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
const c360RefreshButton=()=>'';
""" + WARN_SRC + """
console.log(JSON.stringify(c360WarningCounts([
  {level:'warn', section:'overview', title:'a'},
  {level:'bad', section:'billing', title:'b'},
  {level:'warn', section:'billing', title:'c'},
  {level:'warn', href:'/seo/x#blogs', title:'goes elsewhere'},
  {level:'warn', go:'work', title:'d'}],
  [{level:'warn', section:'overview', title:'No contact on file'}])));
"""
cr = subprocess.run(["node", "-"], input=counts_driver, capture_output=True, text=True)
check("the badge counts run on their own", cr.returncode, 0)
counts = json.loads(cr.stdout or "{}") if cr.returncode == 0 else {}
check("findings are counted per rail section, local ones included",
      counts.get("overview", {}).get("n"), 2)
check("a section's badge takes the worst level there",
      counts.get("billing"), {"n": 2, "level": "bad"})
check("a finding that links to another record counts against no section",
      "undefined" not in counts and len(counts) == 3)
check("a `go` key counts like a section", counts.get("work", {}).get("n"), 1)
check("the rail draws the badges and redraws them when the strip answers",
      "function paintRailBadges()" in REC and REC.count("paintRailBadges();") >= 2
      and ".c360-rail .rl .rlb" in REC)
check("Products & IOs has an Ends column",
      "<th>Ends</th>" in REC and "const endCell=p=>{" in REC and 'colspan="5"' in REC)
import hub.record_health as _rh
m = re.search(r"const C360_ENDING_SOON_DAYS=(\d+);", REC)
check("...and its renewal window is the health strip's own",
      int(m.group(1)) if m else None, _rh.ENDING_SOON_DAYS)
check("both of Knack's date spellings are read",
      "^(\\d{4})-(\\d{2})-(\\d{2})" in REC and "^(\\d{1,2})\\/(\\d{1,2})\\/(\\d{4})" in REC)
check("the site score sits beside the screenshots and opens the audit",
      "c360-score" in REC and "Site score" in REC and "a.href=d.scan_url" in REC)
SF = (ROOT / "hub" / "scan_facts.py").read_text(encoding="utf-8")
check("...and the screenshots route carries the score whenever there is a scan",
      '"score": score,' in SF and '"tier": str(row.get("tier") or ""),' in SF)
# ------------------------------------------------------------------------
section("8. The record's JavaScript lives in files (hub/client360_assets.py)")
from hub import client360_assets as _assets                             # noqa: E402
TPL = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
on_disk = sorted(p.name for p in (ROOT / "hub" / "static").glob("client360-*.js"))
check("every module on disk is in the load-order list", on_disk, sorted(_assets.MODULES))
for _m in _assets.MODULES:
    check(f"the template loads {_m} with the content-hash cache-buster",
          f'<script src="/assets/{_m}?v={{{{ c360_v }}}}"></script>' in TPL)
    _src = (ROOT / "hub" / "static" / _m).read_text(encoding="utf-8")
    check(f"...and {_m} carries no Jinja", "{{" not in _src and "{%" not in _src)
    check(f"...and {_m} has no alert() box", "alert(" not in _src)
_tag_order = [m for m in re.findall(r'<script src="/assets/(client360-[a-z]+\.js)', TPL)]
check("the tags stand in the list's order", _tag_order, list(_assets.MODULES))
check("the modules load before the record's own inline script",
      TPL.find('/assets/client360-core.js') < TPL.find('<script>\nconst user_name='))
check("only what needs Jinja stays inline: the constants, run, pick, render",
      "function render(g,q){" in TPL and "async function run(){" in TPL
      and "function renderRoles(d){" not in TPL and "function loadHealth(name){" not in TPL)
check("the cache-buster is a content hash, ten characters",
      re.fullmatch(r"[0-9a-f]{10}", _assets.version()) is not None)
check("the route stamps it", "c360_v=client360_assets.version()" in
      (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8"))

check("the latest note is pinned under the header and opens the notes card",
      'id="c360LatestNote"' in REC and "c360-latest-note" in REC
      and "showC360Section('overview'); const c=document.getElementById('c-notes')" in REC)
check("and the page no longer defines the disclosure renderer",
      "function renderClientIssues" in REC, False)
check("nor fetches the route that fed it",
      "/api/client/issues" in REC, False)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
