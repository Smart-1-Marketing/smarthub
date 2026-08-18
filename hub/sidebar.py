"""Shared Hub sidebar, injected into every module page.

Modules keep their own layouts; this adds the same fixed navy sidebar the Hub
shell uses (scoped s1hub- class names to avoid collisions) and shifts the
page right to make room. Below 950px the sidebar collapses to the floating
chip so phone layouts are untouched.
"""

_ITEMS = [
    ("dashboard", "/", "&#8962;", "Dashboard"),
    ("c360", "/client360", "&#9678;", "Client 360"),
    ("_sec", "", "", "Modules"),
    ("clients", "/clients", "&#9636;", "Clients"),
    ("google", "/google/", "G", "Google"),
    ("sites", "/sites/", "&#11041;", "Sites"),
    ("sitesmatch", "/tools/sites-match", "&#128279;", "Match Sites"),
    ("suite", "/suite/", "&#9635;", "Suite"),
    ("_secseo", "", "", "SEO"),
    ("seo", "/seo", "&#128269;", "SEO Clients"),
    ("scans", "/scans/", "&#128200;", "Site Scans"),
    ("bulkscan", "/scans/bulk", "&#9776;", "Scan All Clients"),
    ("_sec2", "", "", "Sales"),
    ("salesb", "/sales/builder/", "&#9998;", "Sales Builder"),
    ("props", "/sales/proposals/", "&#9733;", "Proposal Builder"),
    ("_seccre", "", "", "Creative"),
    ("imgpick", "/tools/image-picker", "&#128444;", "Image Picker"),
    ("pageimg", "/tools/page-images", "&#128200;", "Page Images"),
    ("commb", "/tools/commercial-builder", "&#127916;", "Commercial Builder"),
    ("fanrad", "/tools/fan-radio", "&#127911;", "Fan Radio"),
    ("radiop", "/tools/radio-promo", "&#128266;", "Radio Promo"),
    ("landads", "/tools/landing-ads", "&#128240;", "Landing Page Ads"),
    ("_sec3", "", "", "Utilities"),
    ("_sec3", "", "", "Utilities"),
    ("tools", "/tools", "&#10022;", "Tools"),
    ("calc", "/tools/calculators", "&#128425;", "Calculators"),
    ("gaccess", "/tools/google-access", "G", "Google Access"),
    ("tickets", "/tools/tickets/", "&#127915;", "Web Tickets"),
    ("qa", "/qa", "&#10003;", "QA Reports"),
    ("stalecre", "/qa/stale-creative", "&#9203;", "Stale Creative"),
    ("activity", "/activity", "&#8801;", "Activity Log"),
    ("diagnostics", "/diagnostics", "&#9678;", "Diagnostics"),
    ("users", "/diagnostics/users", "&#128100;", "Users"),
    ("status", "/status", "&#9825;", "System Status"),
]

_CSS = """
<style>
@media (min-width: 950px) {
  /* Offset the page for the fixed sidebar — but only when the host page
     isn't already doing it. hub.css lays the Hub's own pages out with
     .main{margin-left:224px}, so applying it to <body> as well pushed the
     content 448px right. :not(:has(.main)) leaves those pages alone and
     still offsets every module page, which has no such rule. */
  body:not(:has(.main)) { margin-left: 224px; }
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
.s1hub-feed{position:fixed;bottom:10px;right:14px;z-index:99991;
  font:600 12px 'Segoe UI',system-ui,sans-serif;color:#64748b;background:rgba(255,255,255,.92);
  border:1px solid #e2e8f0;border-radius:20px;padding:6px 14px;cursor:pointer;
  box-shadow:0 4px 14px rgba(15,23,42,.10);text-decoration:none}
.s1hub-feed:hover{color:#1a2e58;border-color:#cbd5e1}
</style>
<a class="s1hub-feed" onclick="s1hubFeedback();return false" href="#">Issues, Suggestions, Problems?</a>
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


def render_sidebar(active: str = "") -> bytes:
    rows = []
    rows.append('<div class="s1hub-logo"><div class="s1hub-mark">S1</div><span class="s1hub-name">Smart 1 Hub</span></div>')
    rows.append('<div class="s1hub-sec">Overview</div>')
    for key, href, ico, label in _ITEMS:
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
        "var t=document.querySelector('.s1hub-toggle');"
        "function coll(on){document.body.classList.toggle('s1hub-collapsed',on);"
        "if(t){t.innerHTML=on?'\\u276F':'\\u276E';"
        "t.title=on?'Show menu':'Hide menu';"
        "t.setAttribute('aria-label',t.title);}"
        "try{localStorage.setItem('s1hub:collapsed',on?'1':'0');}catch(e){}}"
        "try{if(localStorage.getItem('s1hub:collapsed')==='1')coll(true);}catch(e){}"
        "if(t)t.addEventListener('click',function(){"
        "coll(!document.body.classList.contains('s1hub-collapsed'));});"
        "})();</script>"
    )
    html = (
        _CSS
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
