"""Commercial Builder dashboard: the Client 360-style workbench stays wired.

    python test_commercial_dashboard_layout.py

Static by design: layout regressions are in the template and stylesheet, so
this check needs no provider keys, database, or Flask installation.
"""
import re
import sys
from pathlib import Path


ROOT = Path(__file__).parent
TPL = (ROOT / "modules" / "commercial_builder" / "templates" /
       "commercial_dashboard.html").read_text(encoding="utf-8")
LAYOUT = (ROOT / "modules" / "commercial_builder" / "templates" /
          "_layout.html").read_text(encoding="utf-8")
CSS = (ROOT / "modules" / "commercial_builder" / "static" / "css" /
       "commercial-builder.css").read_text(encoding="utf-8")

passed = failed = 0


def check(label, got, want=True):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


print("\nThe dashboard uses the Client 360 workbench shape\n"
      "------------------------------------------------")
check("the shared layout exposes page-specific body attributes",
      "{% block body_attrs %}" in LAYOUT)
check("only this dashboard asks for the Hub icon rail",
      'data-s1hub-collapse="1"' in TPL)
check("the dashboard opts the generic accordion out",
      'data-s1-workspace="1"' in TPL)
check("the section rail is labeled for assistive technology",
      'aria-label="Dashboard sections"' in TPL)
check("the rail offers the three groups and the old all-at-once view",
      re.findall(r'class="cb-dashboard-rail-link(?: on)?"[^>]*>([^<]+)', TPL),
      ["Overview", "Clients", "Production", "Everything"])
check("Overview is the only server-visible initial view",
      len(re.findall(r'class="cb-dashboard-view on"', TPL)), 1)

print("\nNo dashboard job was lost in the regrouping\n"
      "-------------------------------------------")
markers = {
    "overview": ('id="cb-review-inbox"', "Recent commercials"),
    "clients": ("Clients ({{ clients|length }})", "?client_id={{ c.id }}"),
    "production": ('id="cb-verify-btn"', 'id="cb-verify-out"'),
}
for key, tokens in markers.items():
    start = TPL.index(f'id="cb-dashboard-{key}"')
    next_starts = [TPL.find('id="cb-dashboard-', start + 1)]
    next_starts = [p for p in next_starts if p > start]
    end = next_starts[0] if next_starts else TPL.index("{% endblock %}", start)
    section = TPL[start:end]
    for token in tokens:
        check(f"{key} keeps {token}", token in section)
check("the review loader still calls the real endpoint",
      'fetch("/tools/commercial-builder/api/reviews/waiting")' in TPL)
check("the provider verifier still calls the real endpoint",
      'fetch("/tools/commercial-builder/api/providers/verify"' in TPL)

print("\nThe rail works at desktop and phone widths\n"
      "------------------------------------------")
check("the desktop rail uses Client 360's 190px workbench column",
      ".cb-dashboard-work{display:grid;grid-template-columns:190px minmax(0,1fr)" in CSS)
check("one view at a time is the default",
      ".cb-dashboard-view{display:none;}" in CSS and
      ".cb-dashboard-view.on{display:block" in CSS)
check("Everything deliberately reveals every section",
      'section === "all" || view.dataset.section === section' in TPL)
check("the active section is bookmarkable",
      'history.replaceState(null, "", "#" + section)' in TPL)
check("the phone rail becomes a normal horizontal row",
      ".cb-dashboard-rail{position:static;flex-direction:row;flex-wrap:wrap;}" in CSS)
check("phone tables scroll inside their card instead of crushing columns",
      ".cb-dashboard-view .cb-table{min-width:500px;}" in CSS and
      ".cb-dashboard-view .cb-card{min-width:0;overflow-x:auto;}" in CSS)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
