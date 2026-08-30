"""Find internal links that point at no route.

The Hub is a hub app plus ~26 standalone Flask apps behind
DispatcherMiddleware, so "does this link work?" cannot be answered by reading
one url_map, and grepping for hrefs answers a different question entirely.
This boots the real composed application, asks each mounted app what it
actually serves, and checks every internal URL literal in the repo against
whichever app owns that path.

That distinction is the whole point. A module page written as
``fetch("/api/lead")`` is correct when the module runs standalone and wrong
the moment it is mounted at ``/land/hvac`` — the request goes to the hub app,
which has no such route, and the page fails silently in production while
looking perfectly healthy in the module's own tests. Twenty-two links were
broken this way when this tool was first run, including the lead capture on
seven landing pages.

    python tools/linkcheck.py           # report, exit 1 if anything is broken
    python tools/linkcheck.py --quiet   # only the summary line

Exits 1 when a link resolves to nothing, so it can gate a release.
"""
from __future__ import annotations

import os
import re
import sys
import collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Booting the app for real is the point of this tool, but it must not touch
# the live database or inherit a half-configured environment.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "linkcheck.db"))
os.environ.setdefault("SECRET_KEY", "linkcheck")

from werkzeug.exceptions import MethodNotAllowed, NotFound       # noqa: E402
from werkzeug.middleware.dispatcher import DispatcherMiddleware  # noqa: E402
from werkzeug.routing import RequestRedirect                     # noqa: E402

SKIP_DIRS = {".git", "node_modules", "_attic", "dist", "build", ".venv",
             "venv", "__pycache__"}
EXTS = ("html", "py", "js", "ts", "tsx", "jsx")

# The Display Ad Builder is a Node service proxied at /tools/display-ads. Its
# routes are not in any Flask url_map and its root-relative URLs are rewritten
# at runtime by src/basepath.ts, so Flask cannot judge them.
#
# This file is skipped too: the URLs in its docstring and patterns are examples
# of the bug, not links, and a checker that fails on its own documentation is a
# checker people learn to ignore.
#
# test_ads_module.py for the same reason: it carries a JavaScript fixture that
# feeds fake URLs to the ad builder's base-path shim to prove the shim leaves
# the Hub's chrome alone. Those are the shim's INPUT, not links a page follows
# -- and they are the ad builder's own routes, which the line above already
# says Flask cannot judge.
# test_alt_text.py for the same reason again: its fixture is a page from a
# CLIENT's website, held as a string so the alt-text scanner can be tested
# without fetching anybody's live site. Its <img src="/img/..."> paths are that
# page's images -- they are the checker's INPUT, and they were never meant to
# resolve against this Hub's route table.
# test_ads_estimate.py, a third time: it holds a client landing page as a
# string so the conversion-point scanner can be tested without fetching
# anybody's live site. Its <form action="/lead"> is that page's own form --
# the thing being counted, not a link this Hub serves.
# test_display_ads.py, a fourth: it asserts what the Display Ad Builder's own
# screens contain, so an assertion reads `href="/presets"` as a literal. That
# is the string being looked FOR inside a Node page, not a link this Hub
# serves -- and /presets is one of the ad builder's own routes, which the note
# at the top already says Flask cannot judge. Nothing is lost by skipping it:
# the Hub template that links to that page writes the href as
# "{{ url_prefix }}/presets", which this file does not extract either, because
# the pattern wants an href starting with a slash. The same is already true of
# the "All builds" link beside it.
SKIP_PREFIXES = ("modules/ad_builder/", "tools/linkcheck.py",
                 "test_ads_module.py", "test_alt_text.py", "test_ads_estimate.py",
                 "test_display_ads.py")

# Known-good references that are not links in the running app. Empty today:
# /tools/ads/ lived here while Smart 1 Ads shipped in the repo unmounted, and
# came out when wsgi.py started serving it. An entry here is a promise that a
# path is unroutable on purpose, so it has to come out the moment that stops
# being true -- otherwise this file excuses a real break.
ALLOW: dict[str, tuple[str, ...]] = {}

PATTERNS = [
    ("attr", re.compile(
        r"""\b(?:href|action|src)\s*=\s*["'](/[^"'#\s>]*)["']""")),
    ("fetch", re.compile(r"""\bfetch\s*\(\s*["'`](/[^"'`?#\s]*)""")),
    ("location", re.compile(
        r"""location(?:\.href)?\s*=\s*["'`](/[^"'`?#\s]*)""")),
    ("open", re.compile(r"""window\.open\s*\(\s*["'`](/[^"'`?#\s]*)""")),
    ("xhr", re.compile(
        r"""\.open\s*\(\s*["'][A-Z]+["']\s*,\s*["'`](/[^"'`?#\s]*)""")),
    ("redirect", re.compile(r"""\bredirect\s*\(\s*["'](/[^"'?#\s]*)["']""")),
    # A request this tool never saw. CLAUDE.md spends a paragraph on how
    # invisible it is -- it returns a boolean nobody reads and fires on
    # pagehide, so a wrong path there fails in total silence, which is how six
    # landing modules lost their abandoned-form leads.
    ("beacon", re.compile(r"""sendBeacon\s*\(\s*["'`](/[^"'`?#\s]*)""")),
]

# URLs assembled at runtime -- fetch(BASE + "/api/thing"). The path fragment is
# real, but the base is a variable this tool cannot resolve, so neither
# "resolves" nor "broken" is an honest verdict and reporting either would make
# the checker cry wolf. They are *counted* instead, so the summary says how
# much of the surface went unverified.
#
# This is not hypothetical. Three of the Proposal Builder's AI buttons and all
# four of its IO-conversion calls pointed at paths no app served -- they were
# built as IO_API_BASE + "/sales/builder/api/...", which exists on neither app
# -- and every one of them sat here unnoticed because the literal was never in
# the fetch() call by itself.
UNCHECKED = [
    ("concat-fetch", re.compile(
        r"""\bfetch\s*\(\s*[A-Za-z_$][\w.$]*\s*\+\s*["'`](/[^"'`?#\s${]*)""")),
    # The same shape with no leading slash: fetch(base + "api/gallery?…").
    # It resolves against the *document's directory*, so it is not even a path
    # this tool could match against a rule -- and without this it matched
    # neither PATTERNS nor the rule above, so it was invisible rather than
    # unverified. hub/templates/seo_client.html builds its SEO-image gallery
    # links that way.
    ("concat-relative", re.compile(
        r"""\bfetch\s*\(\s*[A-Za-z_$][\w.$]*\s*\+\s*["'`]([A-Za-z][^"'`?#\s${]*)""")),
]

# A module's own request helper is invisible to the patterns above: the URL is
# a literal, but it sits in `post(...)` rather than in `fetch(...)`. On the SEO
# client record alone that hid twenty-seven paths, which is most of what the
# page does.
#
# The helpers are NOT alike, and that is why this is a table rather than a list
# of names. Some hand the URL straight to fetch(); others do fetch(BASE + path),
# where the literal is a fragment and resolving it as a root-absolute path
# would report a break that is not there -- the crying wolf UNCHECKED exists to
# avoid. `post` is pass-through in seo_client.html and prefixed in
# ads_estimate.html; `api` is pass-through in the Suite panel and prefixed in
# the Commercial Builder. A name alone cannot tell them apart.
#
# Keyed on the file, because a bare `post(` matched `app.post(` and
# `client.post(` in Python and reported 292 breaks that were route decorators
# and test clients. Only spellings actually in use go in, the rule
# hub/config.py's ALIASES table gives: a speculative name costs nothing to
# resolve and a great deal to police.
HELPERS_PASS_THROUGH = {
    "hub/templates/seo_client.html": ("post",),
    "hub/templates/client_owners.html": ("send",),
    "modules/suite_panel/public/index.html": ("api",),
    "modules/gpt_ads/templates/index.html": ("get",),
}

# Declared so their literals are *counted as unverified* rather than silently
# invisible -- naming them is what stops somebody adding the name to the table
# above and getting a page of false breaks.
HELPERS_PREFIXED = {
    "modules/ads_builder/templates/ads_estimate.html": ("post",),
    "modules/commercial_builder/static/js/common.js": ("api",),
    "modules/stadium/public/index.html": ("apiUrl",),
}


# `sendBeacon` and a relative concat are browser calls. A match in a .py file
# is therefore not a call site -- it is prose, and reporting the explanation of
# a fix as the defect is the mistake hub/config.py's drift check and
# tools/spellcheck.py both had to be taught to stop making. It is not a
# hypothetical here: the first run of the beacon pattern reported
# test_landing_embeds.py:263, a comment that reads "The bug this section exists
# for: sendBeacon('/api/partial-lead')" -- the note describing the very trap,
# flagged as the trap.
FRONT_EXTS = ("html", "js", "ts", "tsx", "jsx")
BROWSER_ONLY = ("beacon", "concat-relative")


def _helper_rx(name):
    """A bare call to `name`, never `.name(` -- see the note above."""
    return re.compile(r"""(?<![.\w$])""" + re.escape(name)
                      + r"""\s*\(\s*["'`](/[^"'`?#\s${]*)""")


def _unwrap(app):
    """Peel AuthGuard/HubBar and friends until a Flask app appears."""
    for _ in range(20):
        if app is None or hasattr(app, "url_map"):
            return app
        nxt = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
        if nxt is app:
            return None
        app = nxt
    return None


class Routes:
    """The composed application's real route table."""

    def __init__(self):
        import wsgi
        d = wsgi.application
        while not isinstance(d, DispatcherMiddleware):
            d = getattr(d, "app", None) or getattr(d, "wsgi_app", None)
        self._mounts = d.mounts
        self.hub = _unwrap(d.app)
        self.apps = {p: _unwrap(a) for p, a in d.mounts.items()}
        self.all = [str(r.rule) for r in self.hub.url_map.iter_rules()]
        for prefix, app in self.apps.items():
            if app is not None:
                self.all += [prefix + str(r.rule)
                             for r in app.url_map.iter_rules()]

    def owner(self, path):
        """Which app DispatcherMiddleware hands this path to."""
        script, info = path, ""
        while "/" in script:
            if script in self._mounts:
                return script, (info or "/"), self.apps.get(script)
            script, last = script.rsplit("/", 1)
            info = "/%s%s" % (last, info)
        return "", path, self.hub

    def resolves(self, path):
        mount, sub, app = self.owner(path)
        if app is None:
            return True, mount          # module failed to import; not our call
        adapter = app.url_map.bind("localhost",
                                   script_name=(mount or "") + "/",
                                   path_info=sub)
        try:
            adapter.match(sub, method="GET")
        except (RequestRedirect, MethodNotAllowed):
            return True, mount          # exists; redirect or other verb
        except NotFound:
            return False, mount
        return True, mount

    def under(self, prefix):
        """True if any route lives below this prefix.

        A URL cut short at a JS interpolation — ``/seo/faq/`` from
        ``/seo/faq/${slug}`` — is a prefix, not a link, so it is judged on
        whether anything is served underneath it.
        """
        stem = prefix.rstrip("/")
        return any(f == stem or f.startswith(stem + "/") for f in self.all)


def literals(patterns=None):
    """Every internal URL literal in the repo, with where it came from."""
    patterns = patterns or PATTERNS
    found = collections.defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.rsplit(".", 1)[-1].lower() not in EXTS:
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            if rel.startswith(SKIP_PREFIXES):
                continue
            try:
                text = open(os.path.join(dirpath, name), encoding="utf-8",
                            errors="ignore").read()
            except OSError:
                continue
            # A file's own request helper, if it has one declared. Which
            # table it is in decides whether the URL is resolved or merely
            # counted: pass-through hands it to fetch() unchanged, prefixed
            # makes it a fragment of a URL this tool cannot assemble.
            rel_key = rel.replace(os.sep, "/")
            extra = []
            if patterns is PATTERNS:
                extra = [("helper", _helper_rx(h))
                         for h in HELPERS_PASS_THROUGH.get(rel_key, ())]
            elif patterns is UNCHECKED:
                extra = [("helper-prefixed", _helper_rx(h))
                         for h in HELPERS_PREFIXED.get(rel_key, ())]
            is_front = name.rsplit(".", 1)[-1].lower() in FRONT_EXTS
            for n, line in enumerate(text.splitlines(), 1):
                for kind, rx in list(patterns) + extra:
                    if kind in BROWSER_ONLY and not is_front:
                        continue
                    for m in rx.finditer(line):
                        found[m.group(1)].append((rel, n, kind))
    return found


def scan():
    routes = Routes()
    found = literals()
    unchecked = literals(UNCHECKED)
    broken, prefixes, checked = [], 0, 0
    for url in sorted(found):
        # Protocol-relative, and anything still holding a Jinja or JS
        # placeholder, cannot be matched against a rule.
        if url.startswith("//") or "{" in url or "$" in url:
            continue
        checked += 1
        path = url.split("?")[0].split("#")[0] or "/"
        ok, mount = routes.resolves(path)
        if ok:
            continue
        if path.endswith("/") and routes.under(path):
            prefixes += 1
            continue
        where = found[url]
        if url in ALLOW and all(f in ALLOW[url] for f, _, _ in where):
            continue
        broken.append((url, mount, where))

    shadowed = []
    for rule in self_rules(routes):
        mount, _, _ = routes.owner(rule.split("<")[0])
        if mount:
            shadowed.append((rule, mount))
    missing_eps, dead_eps = endpoint_check(routes)
    return (routes, checked, prefixes, broken, shadowed, unchecked,
            missing_eps, dead_eps)


# ---------------------------------------------------------------------------
# url_for endpoints
# ---------------------------------------------------------------------------
# The other half of "does this URL exist", and the half nothing looked at.
# `url_for('website_check_limits', …)` sat in a Sites Admin template with no
# route of that name behind it, and Flask raises BuildError at *render* time —
# so it was not a broken button, it was a 500 on the whole page, on every
# visit to any project. Every link on the page was fine; the page never got
# drawn. linkcheck reads URL literals and cannot see an endpoint name, which
# is exactly why this shipped.
#
# Only quoted literals are checked. `url_for(name)` cannot be resolved here,
# and a blueprint-relative `url_for('.index')` depends on the request's own
# blueprint, so both are left alone rather than guessed at and reported wrong.
URL_FOR = re.compile(r"""\burl_for\s*\(\s*["']([A-Za-z_][\w.]*)["']""")


def _template_roots(app):
    """Every directory this app resolves template names in, absolute."""
    roots = []
    loader = getattr(app, "jinja_loader", None)
    roots += list(getattr(loader, "searchpath", None) or [])
    for bp in (getattr(app, "blueprints", None) or {}).values():
        bl = getattr(bp, "jinja_loader", None)
        roots += list(getattr(bl, "searchpath", None) or [])
    out = []
    for r in roots:
        try:
            out.append(os.path.realpath(r))
        except OSError:
            continue
    # The app's own package directory too, so a url_for in its .py is checked
    # against its own route table rather than against the hub's.
    root_path = getattr(app, "root_path", "")
    if root_path:
        out.append(os.path.realpath(root_path))
    return out


def _rendered_templates():
    """Template names some view actually renders.

    A url_for in a template nothing renders cannot 500 anybody today, and
    failing the build on one would have started this check red — which is how
    a check gets switched off. Those are reported as a note instead.

    What this note *cannot* say is that the template is unreachable at all:
    it sees one only when it also finds a broken url_for inside, so an orphan
    whose links happen to resolve is invisible here. That question belongs to
    `integrity.check_orphan_templates()`, which asks it directly — and its
    first three findings were deleted rather than reported, so this note now
    has nothing standing behind it. Left as a live check because the next
    template written against routes nobody added will land here first, the
    way sites_admin's project_detail.html did.
    """
    # Whole-file, not line by line. sites_admin writes
    #     return render_template(
    #         "project_detail.html",
    # and a per-line scan sees neither half — which had this check filing the
    # one template that 500s a live page under "nothing renders it".
    rx = re.compile(r"""render_template\w*\(\s*["']([\w./-]+)["']""", re.S)
    names = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), ROOT)
            if rel.startswith(SKIP_PREFIXES):
                continue
            try:
                text = open(os.path.join(dirpath, name), encoding="utf-8",
                            errors="ignore").read()
            except OSError:
                continue
            for hit in rx.findall(text):
                names.add(os.path.basename(hit))
    return names


def endpoint_check(routes):
    """Every url_for('name') whose app has no endpoint of that name.

    Returns (live, dead): the ones on a page something renders, and the ones
    in a template nothing does.
    """
    apps = [("hub app", routes.hub)]
    apps += [(prefix, app) for prefix, app in sorted(routes.apps.items())]
    owners = []      # (realpath root, label, endpoint names)
    for label, app in apps:
        if app is None:
            continue          # module failed to import; not this check's call
        names = set(getattr(app, "view_functions", {}) or {})
        for root in _template_roots(app):
            owners.append((root, label, names))

    everywhere = set()
    for _root, _label, names in owners:
        everywhere |= names

    rendered = _rendered_templates()
    live, dead = [], []
    for url_for_file, lineno, name in _url_for_uses():
        path = os.path.realpath(os.path.join(ROOT, url_for_file))
        is_template = os.sep + "templates" + os.sep in path
        if not is_template:
            # A shared helper registers its routes on whichever app installs
            # it — hub/embed.py adds s1_embed to nine landing modules and to
            # no hub app — so a .py is judged against the whole composed app.
            # Narrower than a template check and deliberately so: a check that
            # cries wolf is one people learn to scroll past.
            if name not in everywhere:
                live.append((name, url_for_file, lineno, "any app"))
            continue
        # Every app that could render this template. A name that exists in
        # any of them is fine: a folder two apps both search is rendered by
        # whichever one reached it.
        seen, labels = False, []
        for root, label, names in owners:
            if path.startswith(root + os.sep):
                labels.append(label)
                if name in names:
                    seen = True
                    break
        if not labels or seen:
            continue
        row = (name, url_for_file, lineno, labels[0])
        (live if os.path.basename(path) in rendered else dead).append(row)
    return live, dead


def _url_for_uses():
    for name, where in literals([("url_for", URL_FOR)]).items():
        # A trailing dot is a name being concatenated —
        # url_for('commercial_builder.cb_pages.' + step) — and the half in
        # the source is not the endpoint.
        if name.startswith(".") or name.endswith(".") or name == "static":
            continue
        for rel, lineno, _kind in where:
            yield rel, lineno, name


def self_rules(routes):
    return sorted({str(r.rule) for r in routes.hub.url_map.iter_rules()})


def main(argv):
    quiet = "--quiet" in argv
    (routes, checked, prefixes, broken, shadowed, unchecked,
     missing_eps, dead_eps) = scan()

    if not quiet:
        print("routes: %d across hub + %d mounts" % (len(routes.all),
                                                     len(routes.apps)))
        print("internal URLs checked: %d (%d prefix-only)" % (checked,
                                                              prefixes))
        if unchecked:
            # Named, not counted silently: "36 unverified" tells you nothing,
            # while the list tells you where to look when a button does
            # nothing and the page looks fine.
            print("built at runtime, NOT verified: %d" % len(unchecked))
            for url in sorted(unchecked):
                where = ", ".join("%s:%d" % (f, n) for f, n, _ in unchecked[url])
                print("          %-40s %s" % (url, where))
        print()
        for url, mount, where in broken:
            print("BROKEN  %s   [handled by %s]" % (url, mount or "hub app"))
            for f, n, kind in where:
                print("          %s:%d (%s)" % (f, n, kind))
            print()
        for name, rel, lineno, label in missing_eps:
            print("NO ENDPOINT  url_for(%r) in %s:%d — %s has no route of "
                  "that name. Flask raises BuildError while rendering, so "
                  "this is a 500 on the whole page." % (name, rel, lineno, label))
        if missing_eps:
            print()
        for name, rel, lineno, label in dead_eps:
            print("no endpoint, but nothing renders that template: "
                  "url_for(%r) in %s:%d [%s]" % (name, rel, lineno, label))
        if dead_eps:
            print()
        for rule, mount in shadowed:
            print("SHADOWED  %s is on the hub app but %s owns that prefix"
                  % (rule, mount))
        if shadowed:
            print()

    bad = len(broken) + len(shadowed) + len(missing_eps)
    print("%d broken link(s), %d shadowed route(s), %d missing endpoint(s)."
          % (len(broken), len(shadowed), len(missing_eps)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
