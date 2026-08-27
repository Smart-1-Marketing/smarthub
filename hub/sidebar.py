"""Shared Hub sidebar, injected into every module page.

Modules keep their own layouts; this adds the same fixed navy sidebar the Hub
shell uses (scoped s1hub- class names to avoid collisions) and shifts the
page right to make room. Below 950px the sidebar collapses to the floating
chip so phone layouts are untouched.
"""

# Ordered the way the work runs, not the way the code grew: what you sell,
# what you make, then the systems underneath. Utilities went last because
# Diagnostics, System Status and Users are things you go looking for when
# something is wrong -- putting them third pushed the actual tools below the
# fold on a laptop.
#
# The keys are what render_sidebar() matches `active` against, so they are
# fixed points: reordering or relabelling is free, renaming a key silently
# stops that page highlighting itself in the nav.
EVERYONE = "everyone"
ADMIN_ONLY = "admin"

_ITEMS = [
    ("dashboard", "/", "&#127968;", "Dashboard"),
    ("c360", "/client360", "&#127919;", "Client 360"),
    ("_sec2", "", "", "Sales"),
    # One entry, because there is one proposal builder. /sales/proposals is the
    # retired standalone tool: it redirects here and serves its archive only.
    # Before the Proposal Builder because it is what happens before one: the
    # audit is the evidence a proposal is written from, and the builder offers
    # to run it anyway if nobody did.
    ("website_audit", "/tools/website-audit", "&#128269;", "Website Audit"),
    ("salesb", "/sales/builder/", "&#128196;", "Proposal Builder"),
    # Sales, not Tools. It is the last step of the sales flow -- a proposal
    # becomes an insertion order -- and the Tools page is where staff looked
    # for it only because that is where its URL happens to live. The mount
    # stays at /tools/io so every existing link keeps working.
    ("io_builder", "/tools/io/", "&#128221;", "IO Builder"),
    ("leads", "/sales/leads", "&#128229;", "Leads"),
    ("landing", "/sales/landing", "&#128187;", "Landing Pages"),
    ("_sec4", "", "", "Tools"),
    ("creative", "/creative", "&#127912;", "Creative"),
    # "Client Tools", not "Tools" -- a "Tools" item inside a "Tools" section
    # reads as though it might be the section header repeated, and gives no
    # hint of what is behind it. The URL is unchanged.
    ("tools", "/tools", "&#128295;", "Client Tools"),
    # Media buying sits in the nav rather than only on the Tools page:
    # it is the one tool here that can start spend in a client's own
    # account, and it is opened directly rather than looked up.
    ("ads", "/tools/ads/", "&#128227;", "Smart 1 Ads"),
    ("qa", "/qa", "&#9989;", "QA Reports"),
    ("_secseo", "", "", "SEO"),
    ("seo", "/seo", "&#128269;", "SEO Clients"),
    ("scans", "/scans/", "&#128200;", "Site Scans"),
    ("_sec", "", "", "Modules"),
    ("clients", "/clients", "&#128101;", "Clients"),
    ("google", "/google/", "&#128202;", "Google"),
    ("sites", "/sites/", "&#127760;", "Sites"),
    ("suite", "/suite/", "&#129520;", "Suite"),
    # Everything from here down is Utilities, and Utilities is the Admin-only
    # section: hub/access.py gates the same paths server-side. This flag is
    # what hides them, and it is *only* the hiding — a General user who types
    # /diagnostics still meets the gate. Nav that lies about what you can
    # reach is a worse experience than a refusal, but nav is not the guard.
    ("_sec3", "", "", "Utilities", ADMIN_ONLY),
    ("diagnostics", "/diagnostics", "&#128300;", "Diagnostics", ADMIN_ONLY),
    ("status", "/status", "&#128678;", "System Status", ADMIN_ONLY),
    ("users", "/diagnostics/users", "&#128100;", "Users", ADMIN_ONLY),
    # Not named in the reshuffle, and it had to land somewhere rather than be
    # dropped: it is a system-wide record read for the same reason as the three
    # above, so it sits with them rather than among the tools that write to it.
    ("activity", "/activity", "&#128220;", "Activity Log", ADMIN_ONLY),
]

# Rows are 4-tuples or 5-tuples; the fifth is the access level. Left off, an
# entry is visible to everyone, so an entry added later is public by default
# and a *new Utilities* entry has to say so. That is the safe direction to be
# wrong in for a nav, and the wrong one for the gate — which is why the gate
# in hub/access.py names its paths explicitly instead of inferring them here.
_ITEMS = [row if len(row) == 5 else row + (EVERYONE,) for row in _ITEMS]

_CSS = """
<style>
@media (min-width: 950px) {
  /* Offset the page for the fixed sidebar — but only when the host page
     isn't already doing it. hub.css lays the Hub's own pages out with
     .main{margin-left:224px}, so applying it to <body> as well pushed the
     content 448px right. :not(:has(.main)) leaves those pages alone and
     still offsets every module page, which has no such rule. */
  body:not(:has(.main)) { margin-left: 224px; --s1hub-offset: 224px; }
  /* Published so full-height tools (Image Creator) can size themselves
     against the space the sidebar actually took, rather than guessing. */
  body.s1hub-collapsed:not(:has(.main)) { --s1hub-offset: 56px; }
  .s1hub-chip { display: none !important; }
}
/* Below 950px the sidebar becomes a slide-out drawer rather than vanishing.
   It used to be display:none with a chip that only linked to the dashboard,
   which meant a phone had no navigation at all -- every tool was unreachable
   unless you typed its URL. */
@media (max-width: 949.98px) {
  .s1hub-sb { transform: translateX(-100%); transition: transform .22s ease;
              width: min(280px, 84vw) !important; box-shadow: 0 0 40px rgba(6,18,32,.45); }
  .s1hub-sb.s1hub-open { transform: translateX(0); }
  .s1hub-burger { display: flex !important; }
  .s1hub-scrim { position: fixed; inset: 0; z-index: 99988; background: rgba(9,22,38,.5);
                 opacity: 0; pointer-events: none; transition: opacity .2s; }
  .s1hub-scrim.s1hub-open { opacity: 1; pointer-events: auto; }
  .s1hub-sb a.s1hub-item { padding: 12px 18px; }   /* bigger tap targets */
}
@media (prefers-reduced-motion: reduce) {
  .s1hub-sb { transition: none } .s1hub-scrim { transition: none }
}
/* Collapsed state: the nav folds to a 56px icon rail rather than vanishing.
   Hiding it entirely is what the old mobile behaviour did, and it left people
   with no way back — a hide control has to be reversible from the hidden
   state, so the toggle stays visible either way. */
body.s1hub-collapsed .s1hub-sb { width: 56px !important; }
body.s1hub-collapsed .s1hub-sb .s1hub-label,
body.s1hub-collapsed .s1hub-sb .s1hub-sec,
body.s1hub-collapsed .s1hub-sb .s1hub-foot,
body.s1hub-collapsed .s1hub-sb .s1hub-logo span { display: none !important; }
body.s1hub-collapsed .s1hub-sb a.s1hub-item { justify-content: center; padding: 11px 0; }
body.s1hub-collapsed .s1hub-sb .s1hub-ico { margin: 0 }
body.s1hub-collapsed:not(:has(.main)) { margin-left: 56px; }
body.s1hub-collapsed .main { margin-left: 56px !important; }
.s1hub-toggle { position: absolute; top: 10px; right: 8px; z-index: 2;
  width: 24px; height: 24px; border: 0; border-radius: 6px; cursor: pointer;
  background: rgba(255,255,255,.08); color: #c9d4ea; font-size: 13px;
  line-height: 1; padding: 0; }
.s1hub-toggle:hover { background: rgba(255,255,255,.18); }
body.s1hub-collapsed .s1hub-toggle { right: 4px; }
@media (max-width: 949.98px) { .s1hub-toggle { display: none } }

.s1hub-burger { display: none; position: fixed; top: 12px; left: 12px; z-index: 99991;
  width: 42px; height: 42px; align-items: center; justify-content: center;
  border: 0; border-radius: 10px; background: #1a2e58; color: #fff;
  font-size: 19px; line-height: 1; cursor: pointer;
  box-shadow: 0 3px 12px rgba(6,18,32,.3); }
/* This markup is injected into 13 modules whose CSS we do not control, so it
   has to assert its own layout rather than inherit whatever the host page
   happens to set. sites_admin ships `header>div,nav{display:flex;align-items:
   center}` -- a bare element selector -- which turned this sidebar into a
   horizontal, vertically-centred row with every item overflowing off-screen.
   Hence the explicit display/flex resets and the !important on the few
   properties a host stylesheet can plausibly clobber. */
.s1hub-sb { position: fixed !important; top: 0 !important; bottom: 0 !important;
  left: 0 !important; width: 224px !important; height: auto !important;
  z-index: 99990;
  display: block !important; flex-direction: initial !important;
  align-items: stretch !important; justify-content: flex-start !important;
  gap: 0 !important; margin: 0 !important; padding: 0 !important;
  background: #1a2e58 !important; color: #c9d4ea; overflow-y: auto;
  font: 13.5px 'Segoe UI', system-ui, sans-serif; text-align: left; }
.s1hub-sb * { box-sizing: border-box; }
.s1hub-sb .s1hub-logo { display: flex !important; align-items: center; gap: 10px;
  padding: 18px 18px 14px; width: auto !important;
  border-bottom: 1px solid rgba(255,255,255,.08); }
.s1hub-sb .s1hub-mark { width: 34px; height: 34px; border-radius: 10px; background: rgba(255,255,255,.12);
  color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; }
.s1hub-sb .s1hub-name { font-weight: 700; font-size: 16px; color: #fff; }
.s1hub-sb .s1hub-sec { display: block !important; padding: 14px 18px 4px;
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1px; color: #7d8db2; }
.s1hub-sb a.s1hub-item { display: flex !important; align-items: center; gap: 10px;
  padding: 9px 18px; width: auto !important; float: none !important;
  color: #c9d4ea; text-decoration: none; border-left: 3px solid transparent;
  font: inherit; text-transform: none; letter-spacing: normal; }
.s1hub-sb a.s1hub-item:hover { background: rgba(255,255,255,.06); color: #fff; }
.s1hub-sb a.s1hub-item.s1hub-on { background: rgba(255,255,255,.1); color: #fff;
  border-left-color: #5b8bff; font-weight: 600; }
.s1hub-sb .s1hub-ico { width: 18px; text-align: center; font-size: 15px; }
.s1hub-chip { position: fixed; bottom: 14px; left: 14px; z-index: 99999; background: #1a2e58;
  color: #fff; padding: 8px 14px; border-radius: 20px; font: 600 12.5px 'Segoe UI', system-ui, sans-serif;
  text-decoration: none; box-shadow: 0 6px 18px rgba(0,0,0,.3); }
</style>
"""


FEEDBACK_FORM_URL = "https://api.leadconnectorhq.com/widget/form/XOszuVj3bHvyOasIeGhw"

FOOTER_HTML = """
<style>
/* Collapsed to a "?" so it stops competing with the page, and widens to
   its full label on hover or keyboard focus. The label stays in the markup
   rather than appearing on hover, so screen readers and search-in-page still
   find it and the width animates from real text. */
.s1hub-feed{position:fixed;bottom:10px;right:14px;z-index:99991;
  display:inline-flex;align-items:center;
  font:600 12px 'Segoe UI',system-ui,sans-serif;color:#64748b;background:rgba(255,255,255,.92);
  border:1px solid #e2e8f0;border-radius:20px;padding:5px;cursor:pointer;
  box-shadow:0 4px 14px rgba(15,23,42,.10);text-decoration:none;
  transition:padding .18s ease,color .18s ease,border-color .18s ease}
.s1hub-feed-q{flex:none;width:22px;height:22px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:14px;font-weight:700;line-height:1}
.s1hub-feed-t{max-width:0;overflow:hidden;white-space:nowrap;
  transition:max-width .22s ease,padding .22s ease}
.s1hub-feed:hover,.s1hub-feed:focus-visible{color:#1a2e58;border-color:#cbd5e1;
  padding:5px 13px 5px 5px}
.s1hub-feed:hover .s1hub-feed-t,.s1hub-feed:focus-visible .s1hub-feed-t{
  max-width:16rem;padding-left:3px}
/* Touch has no hover: a tap opens the form, which is the point of the button,
   so the collapsed "?" is the whole control there. */
@media (prefers-reduced-motion:reduce){
  .s1hub-feed,.s1hub-feed-t{transition:none}
}
</style>
<a class="s1hub-feed" onclick="s1hubFeedback();return false" href="#"
   aria-label="Issues, suggestions, problems?" title="Issues, suggestions, problems?"
   ><span class="s1hub-feed-q" aria-hidden="true">?</span><span class="s1hub-feed-t">Issues, Suggestions, Problems?</span></a>
<script>
function s1hubFeedback(){
  var m=document.getElementById('s1hubFeedModal');
  if(m){m.remove();return;}
  m=document.createElement('div');
  m.id='s1hubFeedModal';
  m.style.cssText='position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px';
  m.innerHTML='<div style="background:#fff;border-radius:14px;width:640px;max-width:100%;height:86vh;display:flex;flex-direction:column;overflow:hidden">'
    +'<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #e2e8f0">'
    +'<b style="color:#1a2e58;font:600 14px \\'Segoe UI\\',system-ui,sans-serif">Issues, Suggestions, Problems?</b>'
    +'<button onclick="document.getElementById(\\'s1hubFeedModal\\').remove()" style="border:0;background:none;font-size:22px;cursor:pointer;color:#64748b">&times;</button></div>'
    +'<iframe src="__FORM_URL__" style="flex:1;border:0;width:100%"></iframe></div>';
  m.onclick=function(e){if(e.target===m)m.remove();};
  document.body.appendChild(m);
}
</script>
""".replace("__FORM_URL__", FEEDBACK_FORM_URL)


def render_footer() -> bytes:
    return FOOTER_HTML.encode()


def visible_items(is_admin: bool = True) -> list[tuple]:
    """The nav rows this person should see, section headers included.

    A section whose every entry was filtered out has its header dropped too —
    a bare "Utilities" heading with nothing under it reads as a nav that
    failed to load rather than as a section that isn't yours.
    """
    rows = [r for r in _ITEMS if is_admin or r[4] != ADMIN_ONLY]
    out = []
    for i, row in enumerate(rows):
        if row[0].startswith("_sec"):
            follows = rows[i + 1:]
            nxt = next((r for r in follows if not r[0].startswith("_sec")), None)
            has_entries = nxt is not None and not any(
                r[0].startswith("_sec") for r in follows[:follows.index(nxt)])
            if not has_entries:
                continue
        out.append(row)
    return out


def render_sidebar(active: str = "", is_admin: bool = True,
                   collapsed_default: bool = False) -> bytes:
    """The nav. ``is_admin=False`` drops the Utilities section.

    Defaults to True because every existing caller renders for a signed-in
    session and passing the flag is the new part; a caller that cannot work
    out the role gets the full nav and the server-side gate still refuses the
    click. The reverse default would hide Diagnostics from an admin whenever
    the role lookup hiccuped, which is a bug nobody would report as one.

    ``collapsed_default`` starts the nav as an icon rail on tools that are
    themselves a full-width workbench -- the Display Ad Builder is a
    three-column bench and the nav takes a fifth of it. It is a *default*,
    not a lock: a stored preference always wins, so somebody who opens the
    menu there keeps it open, and the toggle still works either way. Without
    that distinction it would be a page fighting the person using it.
    """
    rows = []
    rows.append('<div class="s1hub-logo"><div class="s1hub-mark">S1</div><span class="s1hub-name">Smart 1 Hub</span></div>')
    rows.append('<div class="s1hub-sec">Overview</div>')
    for key, href, ico, label, _level in visible_items(is_admin):
        if key.startswith("_sec"):
            rows.append(f'<div class="s1hub-sec">{label}</div>')
            continue
        on = " s1hub-on" if key == active else ""
        rows.append(f'<a class="s1hub-item{on}" href="{href}" title="{label}">'
                    f'<span class="s1hub-ico">{ico}</span>'
                    f'<span class="s1hub-label"> {label}</span></a>')
    # The burger replaces the old chip, which only linked to the dashboard.
    # Inline vanilla JS with no dependencies, because this markup is injected
    # into 20 modules whose own scripts we do not control.
    _JS = (
        "<script>(function(){"
        "var b=document.querySelector('.s1hub-burger'),"
        "n=document.querySelector('.s1hub-sb'),"
        "s=document.querySelector('.s1hub-scrim');"
        "if(!b||!n||!s)return;"
        "function set(o){n.classList.toggle('s1hub-open',o);"
        "s.classList.toggle('s1hub-open',o);"
        "b.setAttribute('aria-expanded',o?'true':'false');}"
        "b.addEventListener('click',function(){"
        "set(!n.classList.contains('s1hub-open'));});"
        "s.addEventListener('click',function(){set(false);});"
        "document.addEventListener('keydown',function(e){"
        "if(e.key==='Escape')set(false);});"
        # Follow a link and the drawer closes itself, otherwise it covers
        # the page you just navigated to.
        "n.addEventListener('click',function(e){"
        "if(e.target.closest('a'))set(false);});"
        # Collapse to an icon rail, remembered across pages. Applied before
        # paint where possible so the layout doesn't jump on every navigation.
        #
        # A page asks for the rail two ways, and both are honoured because
        # both are in use: `collapsed_default` above, decided server-side from
        # the path (the Display Ad Builder), and `data-s1hub-collapse="1"` on
        # the body, declared by the page's own template (the Proposal
        # Builder's wizard). The wide tools lose 224px of a laptop's width to
        # a nav nobody is reading while they work, which is what turns a step
        # into a horizontal scroll. The body attribute simply feeds the same
        # flag, so there is one decision rather than two racing each other.
        "var t=document.querySelector('.s1hub-toggle');"
        "if(document.body&&document.body.getAttribute("
        "'data-s1hub-collapse')==='1')window.__s1hubCollapseDefault=true;"
        # `persist` is what keeps a page default from becoming everybody's
        # preference. `coll()` writes localStorage, and the automatic call
        # below used to write it too -- so one visit to a collapsed-by-default
        # tool stored 's1hub:collapsed=1' globally and every other screen in
        # the Hub came up collapsed, without anybody having pressed anything.
        # It also meant the page default was only ever consulted once, since
        # after that first visit there was no longer such a thing as "no
        # stored preference". Only a real press of the toggle records a
        # preference now; asking for the rail is per page and per visit.
        "function coll(on,persist){"
        "document.body.classList.toggle('s1hub-collapsed',on);"
        "if(t){t.innerHTML=on?'\\u276F':'\\u276E';"
        "t.title=on?'Show menu':'Hide menu';"
        "t.setAttribute('aria-label',t.title);}"
        "if(persist!==false){"
        "try{localStorage.setItem('s1hub:collapsed',on?'1':'0');}catch(e){}}}"
        # A stored preference wins over the page default in both directions,
        # so a tool that starts collapsed can still be opened for good.
        "try{var sv=localStorage.getItem('s1hub:collapsed');"
        "if(sv==='1'||(sv===null&&window.__s1hubCollapseDefault))coll(true,false);}"
        "catch(e){if(window.__s1hubCollapseDefault)coll(true,false);}"
        "if(t)t.addEventListener('click',function(){"
        "coll(!document.body.classList.contains('s1hub-collapsed'));});"
        "})();</script>"
    )
    html = (
        (f"<script>window.__s1hubCollapseDefault="
         f"{'true' if collapsed_default else 'false'};</script>")
        + _CSS
        + '<button class="s1hub-burger" aria-label="Open menu" '
          'aria-expanded="false" aria-controls="s1hub-nav">&#9776;</button>'
        + '<div class="s1hub-scrim"></div>'
        + '<nav class="s1hub-sb" id="s1hub-nav">'
        + '<button class="s1hub-toggle" type="button" aria-label="Hide menu" '
          'title="Hide menu">&#10094;</button>'
        + "".join(rows) + "</nav>"
        + FOOTER_HTML
        + _JS
    )
    return html.encode()
