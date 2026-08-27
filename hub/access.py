"""Two levels of access: General sees the work, Admin sees the machinery.

General Access reaches everything in the Hub **except Utilities** — the
Diagnostics page, System Status, the Users panel and the Activity Log, and the
APIs each of those reads. Admin reaches all of it.

## Why this is one list and not a decorator on forty views

The Hub gained a route the day it needed one, and the next Utilities route
added would be behind whichever guard its author happened to copy. The failure
that produces is the one `hub/auth.py` names in its own docstring: Commercial
Builder answered 200 to anyone with the URL for months because a blueprint
never passed the guard the tiles beside it did. So the rule here is a
**prefix list checked in one `before_request`**, and a Utilities route added
later is covered by having been named in it rather than by somebody
remembering a decorator.

Prefix matching is on path segments, not on `startswith` alone: `/statuses`
must not be gated because `/status` is. `_matches()` is what makes that true,
and it is the whole reason this is a function rather than a `startswith`
inline at the call site.

## What a General user gets instead of a 403 they cannot act on

A page says, in words, that the section is for admins and names what to do
about it. An API answers 403 JSON. Both are the same decision, taken in the
same place — a page that renders and then fails on its first fetch is how a
gate reads as a broken tool.

## The shared password counts as Admin, and that is a decision

`PANEL_PASSWORD` grants a session with no account behind it, so there is no
role to read. It is treated as Admin because it is the emergency door: it is
how somebody gets to Diagnostics when the thing that is broken is sign-in
itself, and a shared password that cannot reach the diagnostics page is an
emergency door into a corridor. Every such request is logged as
`shared_password_utility` so the use of it is visible, and the way to close
the door for good is to clear `PANEL_PASSWORD` on Render once every account
exists — which the Users panel says on the page.
"""
from __future__ import annotations

# Every Utilities surface, page and API alike. The sidebar's Utilities group
# is the list of pages here; the APIs are what those pages fetch, and leaving
# them out would gate the page while its data stayed readable to anyone.
UTILITY_PREFIXES = (
    "/diagnostics",         # includes /diagnostics/users
    "/status",
    "/activity",
    "/api/diagnostics",
    "/api/integrity",
    "/api/oauth-redirects",
    "/api/backup",
    "/api/quotas",
    "/api/housekeeping",    # what needs filling in, and on which page
    "/api/activity",
    "/api/users",
    "/api/status",
)

# Utilities paths that stay open to everyone, because being locked out of them
# is how a General user reports the problem in the first place.
#
#   /login/health  diagnoses sign-in for somebody who cannot sign in, so it
#                  cannot itself require a signed-in admin.
#   /api/version   is on the sign-in page and in the footer of every page.
UTILITY_EXEMPT = (
    "/login/health",
    "/api/version",
)

# What the Utilities group is called on screen, in one place so the sidebar,
# the refusal page and the API message cannot describe it differently.
SECTION_LABEL = "Utilities"


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    """True when ``path`` is one of ``prefixes`` or sits underneath one.

    Segment-aware on purpose: `/statuses` is not `/status`, and a plain
    `startswith` would gate a route nobody meant to gate — silently, and only
    for the half of the company that cannot then say what broke.
    """
    path = "/" + (path or "").strip("/")
    for prefix in prefixes:
        if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
            return True
    return False


def is_utility(path: str) -> bool:
    if _matches(path, UTILITY_EXEMPT):
        return False
    return _matches(path, UTILITY_PREFIXES)


def may_view(path: str, is_admin: bool) -> bool:
    """The whole access rule, in one expression that both call sites read."""
    return bool(is_admin) or not is_utility(path)


def wants_json(path: str, accept: str = "") -> bool:
    """Does this request want a JSON refusal rather than a page?

    Asked from the path and the Accept header together, because a 403 HTML
    page delivered into a `fetch()` renders as a panel that silently never
    fills in.
    """
    path = path or ""
    return path.startswith("/api/") or "/api/" in path or \
        "application/json" in (accept or "")
