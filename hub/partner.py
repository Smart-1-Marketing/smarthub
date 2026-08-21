"""Partner resource pages — rate card, specs, sales kit, learning library.

These five pages arrived as finished, fully self-contained HTML documents:
their own <head>, their own CSS, their own scripts, no external assets. They
are the pages a partner is actually sent to, and they are already signed off,
so this module serves them as they were written rather than re-authoring them
as Jinja templates. Rewriting 1.1 MB of styled HTML into the Hub's layout would
be a week of work whose only outcome is the same page looking slightly worse.

Two consequences follow, and both are handled here:

* They were built to sit at a site root and link to each other as `/sales-kit`.
  The copies under `hub/partner_pages/` were rewritten once, on the way into
  the repo, to `/partner/sales-kit`. Nothing rewrites at request time.
* Being whole documents, they get no HubBar sidebar — that middleware wraps
  dispatcher-mounted modules, not hub blueprints. Without something they would
  be a one-way trip, so a thin bar is injected at the top with a way back and
  the other pages one click away.

Every route carries `@login_required` explicitly. AuthGuard in `wsgi.py` only
wraps dispatcher-mounted modules, so a blueprint registered on the hub app is
open unless it says otherwise -- and these pages carry our rate card. If they
ever need to be partner-facing without an account, that is a deliberate change
here, one page at a time, not a blanket removal.
"""
from __future__ import annotations

import pathlib

from flask import Blueprint, abort, redirect
from markupsafe import escape

from .auth import login_required

_DIR = pathlib.Path(__file__).with_name("partner_pages")

# Order matters: it is the order of the nav bar and of the index list.
PAGES: tuple[tuple[str, str, str], ...] = (
    ("rate-card-universal", "Rate Card", "Every product and its current rate"),
    ("creative-specs", "Design Specs", "Sizes, weights and file rules"),
    ("sales-kit", "Product Sales Kit", "How to position and sell each product"),
    ("learning-library", "Learning Library", "Lessons and walkthroughs"),
    ("digital-dictionary", "Digital Dictionary", "Plain-English term lookup"),
)
_TITLES = {slug: title for slug, title, _ in PAGES}

# Read once per process. These are static files of up to 768 KB; re-reading
# them from disk on every request would be pure waste on a container that
# never changes them between deploys.
_CACHE: dict[str, str] = {}


def available() -> list[str]:
    """Slugs whose file is actually present.

    A missing file is reported as missing rather than silently dropped —
    the dashboard uses this to grey out a button instead of offering a link
    that 404s.
    """
    return [slug for slug, _, _ in PAGES if (_DIR / f"{slug}.html").is_file()]


def _bar(current: str) -> str:
    links = []
    for slug, title, _ in PAGES:
        if not (_DIR / f"{slug}.html").is_file():
            continue
        cls = "pbar-on" if slug == current else ""
        links.append(f'<a class="{cls}" href="/partner/{slug}">{escape(title)}</a>')
    return (
        "<style>"
        ".hub-pbar{position:sticky;top:0;z-index:99999;display:flex;flex-wrap:wrap;"
        "align-items:center;gap:4px;padding:8px 14px;background:#0A2240;"
        "font:600 13px/1.2 system-ui,-apple-system,Segoe UI,sans-serif;"
        "box-shadow:0 1px 6px rgba(0,0,0,.25)}"
        ".hub-pbar a{color:#cfe0f5;text-decoration:none;padding:6px 10px;border-radius:7px}"
        ".hub-pbar a:hover{background:rgba(255,255,255,.12);color:#fff}"
        ".hub-pbar a.pbar-on{background:#fff;color:#0A2240}"
        ".hub-pbar .pbar-home{margin-right:8px;border-right:1px solid rgba(255,255,255,.22);"
        "border-radius:0;padding-right:14px;color:#fff}"
        "@media print{.hub-pbar{display:none}}"
        "</style>"
        '<div class="hub-pbar"><a class="pbar-home" href="/">&#8592; Hub</a>'
        + "".join(links)
        + "</div>"
    )


def _page(slug: str) -> str:
    if slug not in _CACHE:
        path = _DIR / f"{slug}.html"
        if not path.is_file():
            abort(404)
        html = path.read_text(encoding="utf-8", errors="replace")
        bar = _bar(slug)
        # Straight after <body> so the bar is the first thing in the flow. If
        # the tag is ever missing or written oddly, prepending still produces a
        # usable page rather than a 500.
        low = html.lower()
        i = low.find("<body")
        if i != -1:
            j = html.find(">", i)
            html = html[: j + 1] + bar + html[j + 1:] if j != -1 else bar + html
        else:
            html = bar + html
        _CACHE[slug] = html
    return _CACHE[slug]


def register(app) -> None:
    bp = Blueprint("partner", __name__, url_prefix="/partner")

    @bp.get("/")
    @login_required
    def index():
        # No separate landing page to maintain: the dashboard already has the
        # buttons, so a bare /partner goes to the first page that exists.
        have = available()
        return redirect(f"/partner/{have[0]}" if have else "/")

    @bp.get("/<slug>")
    @login_required
    def page(slug: str):
        if slug not in _TITLES:
            abort(404)
        return _page(slug), 200, {"Content-Type": "text/html; charset=utf-8"}

    app.register_blueprint(bp)
