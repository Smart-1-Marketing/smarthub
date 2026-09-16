"""Follow every link the Hub's menu actually emits, and every anchor it aims at.

``tools/linkcheck.py`` reads URL literals out of the repo and resolves them
against the route table. ``tools/pagecheck.py`` requests a hand-maintained list
of pages and checks the bytes. Neither asks the navigation what it is putting
in front of a person, so two kinds of break live in the gap between them:

  1. **A menu row nobody can open.** The sidebar builds its hrefs from
     ``LEAVES`` in ``hub/sidebar.py``, and a department page builds its tiles
     from the same table. Both are data, not URL literals in a template, so a
     row whose route moved or was never registered reads as a normal link, and
     ``department_tiles()`` deliberately drops a key missing from ``LEAVES``
     rather than raising -- the nav must never break a page. The failure is a
     row that is simply gone, or one that 404s on click.

  2. **A group heading that scrolls nowhere.** Every flyout group links to
     ``/views/<slug>#<anchor>``, and the anchor is computed by
     ``_group_anchor()`` at both ends. That is one function today, which is the
     point of it -- but "the two ends agree on the slug" and "the page actually
     carries an element with that id" are different claims, and a fragment that
     matches nothing is invisible to every other check here: the page returns
     200, the link resolves, and the browser silently stays where it is.

So this boots the real composed application, signs in, and asks the nav itself
for its links rather than being told what they are. Every department index,
every leaf in the flat nav, every tile on every department page, and every
group anchor against the ids on the page it lands on.

A non-200 is reported with what came back rather than assumed to be a defect:
three of them are gates working, and they are named in ``EXPECTED`` below with
the reason, so a fourth is a finding rather than noise somebody learns to
scroll past.

    python tools/menucheck.py            # report, exit 1 on any failure
    python tools/menucheck.py --quiet    # only the summary lines

Exits 1 when a menu link leads nowhere, so it can gate a release.
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Booting for real is the point, but it must not touch the live database or
# inherit a half-configured environment. Same approach as linkcheck.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "menucheck.db"))
os.environ.setdefault("SECRET_KEY", "menucheck")
os.environ.setdefault("PANEL_PASSWORD", "menucheck")

from werkzeug.test import Client                                 # noqa: E402

# Status codes that are the answer working rather than a broken link, by path
# prefix, each with what makes it correct. A path not on this list answering
# anything but 2xx is a finding.
#
# This list is the reason the tool is worth gating: without it every run ends
# with three failures somebody has to remember are fine, which is how a real
# fourth one gets scrolled past.
EXPECTED = {
    # Refuses every session except an allowlisted named Admin account
    # (CHECK_RECONCILIATION_ALLOWED_EMAILS), so there is no session this
    # checker could hold that the gate should accept. tools/pagecheck.py skips
    # the same mount for the same reason.
    "/tools/check-reconciliation": (403,
        "gate: allowlisted owner only, no session this checker can hold"),
    # Needs a real account with is_admin, and the shared password this signs in
    # with has no account behind it -- hub/access.py's own reading, and the one
    # hub/department_views_routes.py names. Answers users_denied.html, titled
    # "Account needed", which is the gate working rather than a missing page.
    "/diagnostics/users": (403,
        "gate: needs a named Admin account, not the shared password"),
}

# Mounts served by a separate process in the same container, which is not
# running when this executes outside it. A 502/503 here means the proxy said so
# properly; it is not a menu defect. hub/marketing_audit_proxy.py and
# hub/ad_builder_proxy.py are the two.
PROXIED = ("/tools/marketing-audit", "/tools/display-ads")
PROXY_CODES = (502, 503, 504)


def sign_in(client):
    r = client.post("/login", data={
        "email": "menucheck@smart1marketing.com",
        "password": os.environ["PANEL_PASSWORD"], "next": "/"})
    if r.status_code not in (302, 303):
        raise SystemExit(
            f"menucheck could not sign in (/login returned {r.status_code}). "
            "PANEL_PASSWORD is the shared password the login page accepts.")


def menu_links() -> dict[str, str]:
    """{href: what it is called in the menu}, asked of the nav rather than
    grepped. Admin's view, because it is the superset: a link a General
    account never sees is still a link somebody clicks."""
    from hub import sidebar
    targets: dict[str, str] = {}

    # Every department's own index page. Each exists whether or not anything
    # is under it, so all of them are the menu's responsibility.
    for d in sidebar.departments(True):
        targets[d["href"]] = "department: " + d["label"]

    # Every row in the flat nav, section headers dropped.
    for key, href, _ico, label, _level in sidebar.visible_items(True):
        if not key.startswith("_sec") and href.startswith("/"):
            targets.setdefault(href, label)

    # Every tile a department page and its flyout draw. Mostly the same set as
    # above, and not entirely: a leaf listed under three departments appears
    # once in the flat list, at its first mention.
    for d in sidebar.departments(True):
        for _group, rows in sidebar.department_tiles(d):
            for _k, href, _ico, label in rows:
                if href.startswith("/"):
                    targets.setdefault(href, label)

    return targets


def _expected(href: str):
    for prefix, (code, why) in EXPECTED.items():
        if href == prefix or href.startswith(prefix.rstrip("/") + "/"):
            return code, why
    return None, ""


def check_links(client, quiet=False):
    """Request every menu link. Returns (checked, problems, notes)."""
    targets = menu_links()
    problems, notes = [], []

    for href in sorted(targets):
        label = targets[href]
        try:
            resp = client.get(href, follow_redirects=False)
        except Exception as exc:                                 # noqa: BLE001
            problems.append(f"{href}  ({label}) raised "
                            f"{type(exc).__name__}: {exc}")
            continue

        code = resp.status_code
        if code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location", "")
            # A menu link that bounces to the login form is broken for the
            # person clicking it: they are already signed in. Every other
            # redirect is followed, because a mount root redirecting to its
            # own canonical form is how several of them are written.
            if "/login" in location:
                problems.append(f"{href}  ({label}) {code} -> {location} "
                                f"-- bounced to login while signed in")
                continue
            try:
                code = client.get(href, follow_redirects=True).status_code
            except Exception as exc:                             # noqa: BLE001
                problems.append(f"{href}  ({label}) redirect raised "
                                f"{type(exc).__name__}: {exc}")
                continue

        want, why = _expected(href)
        if want is not None:
            if code == want:
                notes.append(f"{href}  {code} by design -- {why}")
            else:
                problems.append(f"{href}  ({label}) HTTP {code}, expected "
                                f"{want} -- {why}")
        elif any(href.startswith(p) for p in PROXIED) and code in PROXY_CODES:
            notes.append(f"{href}  {code} -- upstream process not running here")
        elif code >= 400:
            problems.append(f"{href}  ({label}) HTTP {code}")
        elif not quiet:
            print(f"  ok    {href}  ({label})")

    return len(targets), problems, notes


def check_anchors(client, quiet=False):
    """Every flyout group heading links to /views/<slug>#<anchor>. Returns
    (checked, problems)."""
    from hub import sidebar
    problems = []
    checked = 0
    ids_cache: dict[str, set] = {}

    for d in sidebar.departments(True):
        page = d["href"]
        if page not in ids_cache:
            body = client.get(page, follow_redirects=True).get_data(as_text=True)
            ids_cache[page] = set(re.findall(r'id="([^"]+)"', body))
        have = ids_cache[page]

        for group, _rows in sidebar.department_tiles(d):
            anchor = sidebar._group_anchor(group)
            if not anchor:
                # An unnamed group is a department's leading rows, which the
                # page draws without a heading of its own to link to.
                continue
            checked += 1
            if anchor in have:
                if not quiet:
                    print(f"  ok    {page}#{anchor}")
            else:
                problems.append(f"{page}#{anchor}  -- group {group!r} in "
                                f"{d['label']} has no element with that id")

    return checked, problems


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    quiet = "--quiet" in argv

    import wsgi
    client = Client(wsgi.application)
    sign_in(client)

    if not quiet:
        print("menu links")
    n_links, link_problems, notes = check_links(client, quiet)

    if not quiet:
        print("\ngroup anchors")
    n_anchors, anchor_problems = check_anchors(client, quiet)

    problems = link_problems + anchor_problems
    if notes and not quiet:
        print("\nnot a defect:")
        for n in notes:
            print("  " + n)
    if problems:
        print()
        for p in problems:
            print("  FAIL  " + p)

    print(f"\n{n_links} menu link(s) and {n_anchors} group anchor(s) checked, "
          f"{len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
