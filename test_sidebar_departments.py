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
check("six of them are Departments and six are Tools",
      [len(d) for _t, d in sidebar.SECTIONS], [6, 6])
check("General loses exactly Utilities",
      [d["slug"] for d in sidebar.departments(True) if d not in sidebar.departments(False)],
      ["utilities"])
slugs = [d["slug"] for d in sidebar.departments(True)]
check("in the agreed order", slugs,
      ["sales", "client-success", "product-success", "seo", "web-dev", "accounting",
       "creative", "studio", "ad-tools", "leads", "qa", "utilities"])

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
check("both section headers are present", [k for k in keys if k.startswith("_sec")], ["_sec0", "_sec1"])
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
check("twelve department rows", html.count('class="s1hub-dept" '), 12)
check("each with a flyout", html.count('class="s1hub-fly"'), 12)
check("each with a chevron", html.count('class="s1hub-chev"'), 12)
check("the active leaf is lit", 'class="s1hub-leaf s1hub-on" href="/reports/"' in html)
check("...and so is the first department that holds it (Client Success)",
      html.index('class="s1hub-dept-row s1hub-on"') < html.index('data-s1hub-dept="product-success"'))
check("a department row lights itself", 'class="s1hub-dept-row s1hub-on"' in
      sidebar.render_sidebar("dept_seo", is_admin=True).decode())
general = sidebar.render_sidebar("", is_admin=False).decode()
check("General does not see Utilities", 'data-s1hub-dept="utilities"' in general, False)
check("General still sees the other eleven", general.count('class="s1hub-dept" '), 11)
check("no GoHighLevel wording in the nav", "GoHighLevel" in html, False)
check("the flyout is placed by script, not clipped by the scroll", "getBoundingClientRect" in html)

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
