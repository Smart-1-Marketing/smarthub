"""One login gate for a module registered as a blueprint on the hub app.

`wsgi.py` wraps every *dispatcher-mounted* module in `AuthGuard`. A module
registered as a blueprint on the hub app never passes through it, and the hub
app has no blanket gate of its own — its pages are guarded view by view. So a
blueprint that does not guard itself answers **200 to anyone with the URL**,
and nothing anywhere says so: the tile beside it redirects to `/login` while
it serves.

This repository has now paid for that three times. Commercial Builder shipped
with every page and API route open, client names and briefs included.
`modules/calculators` shipped with `/tools/calculators/leads` — a table of
real people's names, emails and phone numbers — open. And a sweep of the
composed app found three more still open: Web Tickets and its Knack field map,
the Page Image Optimizer and its saved-job archive, and Video Search with its
Cloudinary library, search and status routes.

Each of the first two was fixed by writing the same `before_request` into that
module. This is that gate, once, so the fourth blueprint does not need a
fourth copy of it — and so the module that adds a route next month is covered
without anybody remembering.

## What it does not do

**It never widens access.** A path a module declares public stays public;
everything else needs the Hub cookie that `hub/auth.py` issues.

**It never breaks the module standing alone.** Outside the Hub there is no
`hub.auth` to import and no cookie to check, so the guard steps aside — which
is safe, because standalone the module is only reachable on the machine
running it.

**Exempting a path from the login is only half.** The hub app's own
`after_request` injects the sidebar, help layer and feedback tab into any HTML
it returns, so a client-facing path needs an entry in `CHROMELESS` in
`hub/__init__.py` as well. Without it the client gets the staff nav; with only
the chrome exemption and no login exemption they get a sign-in form for an
account they will never have. `test_blueprint_guards.py` holds both halves.
"""
from __future__ import annotations

__all__ = ["install"]


def install(bp, *, mount: str = "", public: tuple[str, ...] = ()) -> None:
    """Put a staff-only gate in front of every route on this blueprint.

    `public` is relative to `mount` — the same shape a dispatcher-mounted
    module's `PUBLIC_PREFIXES` has, so a module that later becomes mounted
    needs no second spelling of what is public.

    Never raises: a module that cannot import the Hub's auth is a module
    running standalone, and refusing to start it would be worse than the
    gate not applying where there is nothing to protect.
    """
    prefixes = tuple(p for p in public if p)

    @bp.before_request
    def _staff_only():                                   # noqa: ANN202
        from flask import request

        path = request.path or "/"
        if prefixes:
            rel = path[len(mount):] if mount and path.startswith(mount) else path
            if rel.startswith(prefixes):
                return None
        try:
            from hub.auth import user_from_environ
        except Exception:                                # noqa: BLE001
            return None                                  # standalone, no Hub
        if user_from_environ(request.environ):
            return None
        # A fetch() that follows a redirect to the login page parses the HTML
        # as JSON and reports "Bad response from server", which says nothing
        # about the real problem. JSON callers get a 401 they can read; this
        # came from modules/commercial_builder, which had worked it out, and
        # is the reason that guard could not simply be deleted in favour of
        # a plainer one.
        from flask import jsonify, redirect
        if "/api/" in path or request.method not in ("GET", "HEAD"):
            return jsonify({"ok": False, "error": "Not authenticated. "
                                                  "Please log in to the Hub."}), 401
        # Otherwise a redirect rather than a 403: the reader is a member of
        # staff who followed a link or a bookmark, and `next` puts them back
        # where they were once they have signed in.
        return redirect("/login?next=" + path)
