"""The department sidebar (2026-09-14) and its index pages.

    python3 test_sidebar_departments.py

Same shape as the other test files: no pytest, a temporary data directory
and a throwaway SQLite database.

What it guards:

**Every tool named in a department exists in LEAVES, and every leaf is in
some department.** A key typo in SECTIONS is dropped silently by
`department_tiles()` (the nav must never break a page), so this is the one
place it is caught. A leaf nobody lists is a tool the menu cannot reach —
the "tool with no tile" failure this codebase has paid for before.

**The three pages found with no link at all in the 2026-09-14 audit are
now reachable**: Check Reconciliation, SEO Intelligence, Commercial
Library.

**Every /views/<slug> renders for a signed-in person and carries the
department's tools**, Utilities included for an admin and refused for a
General account the same way its sidebar row is hidden.

**The flat `_ITEMS` the rest of the Hub reads is still whole**: pinned
rows first, the section headers, no duplicate keys.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1sidebar_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "sidebar-test-secret")

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


print("\nThe department sidebar\n" + "=" * 60)
from hub import sidebar                                          # noqa: E402

print("\n-- the tree is whole --")
named = {k for _t, depts in sidebar.SECTIONS for d in depts for _g, keys in d["groups"] for k in keys}
check("every key a department names is a leaf", sorted(named - set(sidebar.LEAVES)), [])
check("every leaf is named by at least one department", sorted(set(sidebar.LEAVES) - named), [])
check("twelve departments", len(sidebar.departments(True)), 12)
check("all twelve in one section now",
      [len(d) for _t, d in sidebar.SECTIONS], [12])
check("General loses exactly Utilities",
      [d["slug"] for d in sidebar.departments(True) if d not in sidebar.departments(False)],
      ["utilities"])
slugs = [d["slug"] for d in sidebar.departments(True)]
check("in the agreed order", slugs,
      ["sales", "client-success", "product-success", "seo", "web-dev", "accounting",
       "creative", "studio", "ad-tools", "leads", "qa", "utilities"])

print("\n-- every tool says what it does, in fifty words or fewer --")
check("every leaf has a blurb", sorted(set(sidebar.LEAVES) - set(sidebar.BLURBS)), [])
check("no blurb names a tool that does not exist", sorted(set(sidebar.BLURBS) - set(sidebar.LEAVES)), [])
check("none is over fifty words",
      sorted(k for k, b in sidebar.BLURBS.items() if len(b.split()) > 50), [])
check("none says GoHighLevel", sorted(k for k, b in sidebar.BLURBS.items() if "GoHighLevel" in b or "GHL" in b), [])
check("every department has a color of its own",
      len({d["color"] for d in sidebar.departments(True)}), 12)

print("\n-- the pinned five --")
check("Dashboard, Ask SmartHub, Client 360, My Clients, My View",
      [r[1] for r in sidebar.PINNED], ["/", "/ask-smarthub", "/client360", "/my-clients", "/views"])

print("\n-- the orphans found on 2026-09-14 are reachable --")
hrefs = {h for _k, (h, _i, _l) in sidebar.LEAVES.items()}
for href in ("/tools/check-reconciliation/", "/seo/intelligence/", "/tools/commercial-builder/library"):
    check(f"  {href} is in the nav", href in hrefs)
for slug, href in (("accounting", "/tools/check-reconciliation/"), ("seo", "/seo/intelligence/"),
                   ("creative", "/tools/commercial-builder/library"),
                   ("product-success", "/tools/commercial-builder/library"),
                   ("client-success", "/tools/commercial-builder/library")):
    tiles = {h for _g, leaves in sidebar.department_tiles(sidebar.department(slug)) for _k, h, *_ in leaves}
    check(f"  {href} is under {slug}", href in tiles)

print("\n-- the flat list the rest of the Hub reads --")
keys = [r[0] for r in sidebar._ITEMS]
check("no duplicate keys", len(keys), len(set(keys)))
check("pinned rows come first", keys[:5], [r[0] for r in sidebar.PINNED])
check("one section header now", [k for k in keys if k.startswith("_sec")], ["_sec0"])
check("every row is a 5-tuple", all(len(r) == 5 for r in sidebar._ITEMS))
check("/tools/ads/ is labeled PPC Builder",
      next(r[3] for r in sidebar._ITEMS if r[1] == "/tools/ads/"), "PPC Builder")
check("Utilities rows are admin-only",
      {r[4] for r in sidebar._ITEMS if r[1] in ("/diagnostics", "/status", "/views/manage", "/views/utilities")},
      {sidebar.ADMIN_ONLY})
check("visible_items(General) drops them",
      [r[1] for r in sidebar.visible_items(False) if r[1] in ("/diagnostics", "/views/utilities")], [])

print("\n-- rendering --")
html = sidebar.render_sidebar("reports", is_admin=True).decode()
# Counted by the data attribute, which is present on every dept and not by
# the class -- the active department carries an extra `s1hub-pinned` class
# now (auto-expand), so a literal `class="s1hub-dept" ` count would miss it.
check("twelve department rows", html.count(' data-s1hub-dept="'), 12)
check("each with a flyout", html.count('class="s1hub-fly"'), 12)
check("each with a chevron", html.count('class="s1hub-chev"'), 12)
check("the active leaf is lit", 'class="s1hub-leaf s1hub-on" href="/reports/"' in html)
check("...and so is the first department that holds it (Client Success)",
      html.index('class="s1hub-dept-row s1hub-on"') < html.index('data-s1hub-dept="product-success"'))
check("a department row lights itself", 'class="s1hub-dept-row s1hub-on"' in
      sidebar.render_sidebar("dept_seo", is_admin=True).decode())
general = sidebar.render_sidebar("", is_admin=False).decode()
check("General does not see Utilities", 'data-s1hub-dept="utilities"' in general, False)
check("General still sees the other eleven", general.count(' data-s1hub-dept="'), 11)
check("no GoHighLevel wording in the nav", "GoHighLevel" in html, False)
check("the flyout is placed by script, not clipped by the scroll", "getBoundingClientRect" in html)

# Auto-expand the active department: the tree of the dept the person came in
# on is what they want to see the moment the page renders. Rendered directly
# on the outer div so the existing `.s1hub-pinned` CSS rule covers it, and
# the chevron's aria-expanded matches. A stored preference still wins.
check("the active department is auto-pinned",
      'class="s1hub-dept s1hub-pinned"' in html)
check("...with an aria-expanded chevron to match",
      'aria-expanded="true"' in html)
check("...but only for the active one, not all twelve",
      html.count('aria-expanded="true"'), 1)
check("a stored preference still wins in both directions",
      "slug in open" in html and "pin(open[slug])" in html)

# Department monograms: colored 2-letter squares in place of emoji. Same 18px
# slot as the emoji, tinted with the department's own color from
# DEPT_COLORS, so the rail's layout is unchanged and the eye can learn
# "the green square is Sales." A monogram declared here covers every one.
check("every department has a monogram", len(sidebar.DEPT_MONOS), 12)
check("...one for each slug",
      sorted(sidebar.DEPT_MONOS.keys()),
      sorted(d["slug"] for d in sidebar.departments(True)))
check("...rendered as a colored square in the sidebar",
      'class="s1hub-ico s1hub-mono"' in html)
check("...tinted from DEPT_COLORS",
      f'style="background:{sidebar.DEPT_COLORS["sales"]}"' in html)
check("...with the two-letter code inline",
      ">SL<" in html and ">CS<" in html and ">QA<" in html)

# The search input at the top of the nav filters every row in place -- pinned,
# departments, and every leaf inside them -- and Cmd/Ctrl-K from anywhere
# focuses it. Without the box, finding a tool by name means guessing which of
# 12 folded departments it lives in.
check("the search input is at the top of the nav", 'class="s1hub-search-input"' in html)
check("it has a keyboard shortcut hint", "s1hub-search-kbd" in html)
check("Cmd/Ctrl-K focuses it", "metaKey" in html and "ctrlKey" in html)
check("Escape and the clear button restore the nav",
      "si.value=''" in html and "s1hub-search-clear" in html)
check("a no-match state is announced", 's1hub-nomatch' in html and "No tool matches." in html)

# The Recent section is drawn client-side from localStorage: every real click
# on a sidebar link is pushed to the front of `s1hub:recent`, deduped by href
# and capped at 5. The section header and container are rendered [hidden] and
# unhide the moment the first entry lands. Search hides the whole block, since
# search results are the answer once the person typed.
check("the Recent placeholder is rendered hidden",
      's1hub-recent-head" hidden' in html and 'class="s1hub-recent"' in html)
check("...and its active leaf is threaded in for the light-up",
      'data-s1hub-active="reports"' in html)
check("clicks are recorded on every nav link kind",
      'a.s1hub-item,a.s1hub-dept-link,a.s1hub-leaf,a.s1hub-g' in html)
check("localStorage holds five entries at most", 'RECENT_MAX=5' in html)
check("a recent-item click does not re-record itself",
      "getAttribute('data-s1hub-recent')==='1'" in html)
check("during a search the Recent block hides",
      '.s1hub-searching .s1hub-recent { display: none' in html or
      '.s1hub-searching .s1hub-recent' in html and 'display: none' in html)
check("recent rows are excluded from the search filter's pinned iterator",
      ":not(.s1hub-recent-item)" in html)

print("\n-- the index pages --")
from hub import create_hub_app                                   # noqa: E402
from hub.extensions import create_all                            # noqa: E402
from hub import auth, access                                     # noqa: E402

app = create_hub_app()
create_all(app)
signed = app.test_client()
signed.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Tester"))
for d in sidebar.departments(True):
    r = signed.get(d["href"])
    body = r.get_data(as_text=True)
    first = next(h for _g, leaves in sidebar.department_tiles(d) for _k, h, *_ in leaves)
    check(f"  {d['href']} renders with its tools", r.status_code == 200 and f'href="{first}"' in body)
    check(f"  ...with the menu's icon, its color and a description",
          d["ico"] in body and f'--dept:{d["color"]}' in body and 'class="s1d-sec"' in body
          and "<p>" in body.split("s1d-tiles")[1].split("</section>")[0])
check("/views/utilities is a Utilities path for the gate", access.is_utility("/views/utilities"))
check("/views/sales is not", access.is_utility("/views/sales"), False)
r = signed.get("/views")
check("My View with no assignment offers the departments",
      r.status_code == 200 and 'href="/views/sales"' in r.get_data(as_text=True))
qa = signed.get("/qa").get_data(as_text=True)
check("the QA page group says Smart 1 Suite, not GoHighLevel",
      "Smart 1 Suite" in qa and "GoHighLevel" not in qa)

print(f"\n{'=' * 60}\n{_passed} passed, {_failed} failed\n")
sys.exit(1 if _failed else 0)
