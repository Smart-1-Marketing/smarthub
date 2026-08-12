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
    ("suite", "/suite/", "&#9635;", "Suite"),
    ("_secseo", "", "", "SEO"),
    ("seo", "/seo", "&#128269;", "SEO Clients"),
    ("_sec2", "", "", "Sales"),
    ("salesb", "/sales/builder/", "&#9998;", "Sales Builder"),
    ("props", "/sales/proposals/", "&#9733;", "Proposal Builder"),
    ("_sec3", "", "", "Utilities"),
    ("tools", "/tools", "&#10022;", "Tools"),
    ("qa", "/qa", "&#10003;", "QA Reports"),
    ("activity", "/activity", "&#8801;", "Activity Log"),
    ("status", "/status", "&#9825;", "System Status"),
]

_CSS = """
<style>
@media (min-width: 950px) {
  body { margin-left: 224px !important; }
  .s1hub-chip { display: none !important; }
}
@media (max-width: 949.98px) { .s1hub-sb { display: none !important; } }
.s1hub-sb { position: fixed; top: 0; bottom: 0; left: 0; width: 224px; z-index: 99990;
  background: #1a2e58; color: #c9d4ea; overflow-y: auto;
  font: 13.5px 'Segoe UI', system-ui, sans-serif; }
.s1hub-sb .s1hub-logo { display: flex; align-items: center; gap: 10px; padding: 18px 18px 14px;
  border-bottom: 1px solid rgba(255,255,255,.08); }
.s1hub-sb .s1hub-mark { width: 34px; height: 34px; border-radius: 10px; background: rgba(255,255,255,.12);
  color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; }
.s1hub-sb .s1hub-name { font-weight: 700; font-size: 16px; color: #fff; }
.s1hub-sb .s1hub-sec { padding: 14px 18px 4px; font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1px; color: #7d8db2; }
.s1hub-sb a.s1hub-item { display: flex; align-items: center; gap: 10px; padding: 9px 18px;
  color: #c9d4ea; text-decoration: none; border-left: 3px solid transparent; }
.s1hub-sb a.s1hub-item:hover { background: rgba(255,255,255,.06); color: #fff; }
.s1hub-sb a.s1hub-item.s1hub-on { background: rgba(255,255,255,.1); color: #fff;
  border-left-color: #5b8bff; font-weight: 600; }
.s1hub-sb .s1hub-ico { width: 18px; text-align: center; font-size: 15px; }
.s1hub-chip { position: fixed; bottom: 14px; left: 14px; z-index: 99999; background: #1a2e58;
  color: #fff; padding: 8px 14px; border-radius: 20px; font: 600 12.5px 'Segoe UI', system-ui, sans-serif;
  text-decoration: none; box-shadow: 0 6px 18px rgba(0,0,0,.3); }
</style>
"""


def render_sidebar(active: str = "") -> bytes:
    rows = []
    rows.append('<div class="s1hub-logo"><div class="s1hub-mark">S1</div><span class="s1hub-name">Smart 1 Hub</span></div>')
    rows.append('<div class="s1hub-sec">Overview</div>')
    for key, href, ico, label in _ITEMS:
        if key.startswith("_sec"):
            rows.append(f'<div class="s1hub-sec">{label}</div>')
            continue
        on = " s1hub-on" if key == active else ""
        rows.append(f'<a class="s1hub-item{on}" href="{href}"><span class="s1hub-ico">{ico}</span> {label}</a>')
    html = (
        _CSS
        + '<nav class="s1hub-sb">' + "".join(rows) + "</nav>"
        + '<a class="s1hub-chip" href="/">&#8962; Smart 1 Hub</a>'
    )
    return html.encode()
