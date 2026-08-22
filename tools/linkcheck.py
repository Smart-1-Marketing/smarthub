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
SKIP_PREFIXES = ("modules/ad_builder/", "tools/linkcheck.py")

# Known-good references that are not links in the running app:
#   install_into_hub.py  writes the mount that would then serve /tools/ads/
#   ui_check.py          is a standalone harness that serves it on its own port
ALLOW = {
    "/tools/ads/": ("install_into_hub.py", "ui_check.py"),
}

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
]


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


def literals():
    """Every internal URL literal in the repo, with where it came from."""
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
            for n, line in enumerate(text.splitlines(), 1):
                for kind, rx in PATTERNS:
                    for m in rx.finditer(line):
                        found[m.group(1)].append((rel, n, kind))
    return found


def scan():
    routes = Routes()
    found = literals()
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
    return routes, checked, prefixes, broken, shadowed


def self_rules(routes):
    return sorted({str(r.rule) for r in routes.hub.url_map.iter_rules()})


def main(argv):
    quiet = "--quiet" in argv
    routes, checked, prefixes, broken, shadowed = scan()

    if not quiet:
        print("routes: %d across hub + %d mounts" % (len(routes.all),
                                                     len(routes.apps)))
        print("internal URLs checked: %d (%d prefix-only)" % (checked,
                                                              prefixes))
        print()
        for url, mount, where in broken:
            print("BROKEN  %s   [handled by %s]" % (url, mount or "hub app"))
            for f, n, kind in where:
                print("          %s:%d (%s)" % (f, n, kind))
            print()
        for rule, mount in shadowed:
            print("SHADOWED  %s is on the hub app but %s owns that prefix"
                  % (rule, mount))
        if shadowed:
            print()

    bad = len(broken) + len(shadowed)
    print("%d broken link(s), %d shadowed route(s)." % (len(broken),
                                                        len(shadowed)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
