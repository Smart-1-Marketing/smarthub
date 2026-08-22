"""Check the pages the browser actually receives, not the templates.

``tools/checktemplates.py`` reads the JavaScript in a template file. That is a
different question from "does the page work", because a Hub page is not what
its template says: ``HubBar`` in wsgi.py and the hub's ``after_request`` both
rewrite the response on the way out, injecting the sidebar, a stylesheet and
five script tags into HTML they did not write.

That rewrite is where a whole class of failure lives, and none of it is
visible in a template, a diff, or a url_map:

  The IO Builder builds two printable documents as JavaScript template
  literals, and each carries its own ``<html>...</body></html>``. HubBar
  injected at the FIRST ``</body>`` in the response, so the sidebar markup
  and five ``<script>`` tags landed in the middle of a string literal. The
  injected ``<script>`` closed the page's own script block early, everything
  after it parsed as HTML, and the entire tool died with "Unexpected
  identifier" before drawing a question. Every template was valid. Every link
  resolved. The page was blank.

So this boots the real composed application, signs in, requests each page and
asks two things of the bytes that come back:

  1. The Hub chrome is a real ELEMENT, not text. This is the whole test.
     html.parser switches to raw-text mode inside <script> exactly like a
     browser does, so if the sidebar was injected into a script literal the
     parser never sees a <nav> start tag -- it sees script data. A page that
     "contains" the sidebar as a string fails here, which is correct: so did
     the browser.

  2. Every script block the BROWSER would delimit still parses. Blocks are cut
     at the first ``</script>`` the same way a browser cuts them, then handed
     to tools/jscheck.py.

    python tools/pagecheck.py            # report, exit 1 on any failure
    python tools/pagecheck.py --quiet    # only the summary line
    python tools/pagecheck.py --strict   # a module that failed to start fails
                                         # the run too (how CI runs it)

Exits 1 on failure, so it can gate a release.
"""
from __future__ import annotations

import os
import sys
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

# Booting for real is the point, but it must not touch the live database or
# inherit a half-configured environment. Same approach as linkcheck.py.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "pagecheck.db"))
os.environ.setdefault("SECRET_KEY", "pagecheck")
os.environ.setdefault("PANEL_PASSWORD", "pagecheck")

from jscheck import check as js_check                            # noqa: E402

# Staff pages served by the hub app itself.
HUB_PAGES = [
    "/", "/client360", "/tools", "/creative", "/qa", "/seo", "/activity",
    "/diagnostics", "/clients", "/status", "/sales/leads",
]

# Mounted modules whose root is not a staff page, so no sidebar is expected:
# a landing page is looked at by a prospect on a client's website, and the MSA
# signing page by a client. Injecting staff navigation there is a bug of its
# own, which is why they are asserted to have NONE.
NO_CHROME_PREFIXES = ("/land/", "/msa")

# Mount roots that redirect or need an argument rather than serving a page.
SKIP_MOUNTS = ()

CHROME_MARKERS = ("s1hub-sb", "sidebar")


class PageParser(HTMLParser):
    """Browser-faithful enough for the two questions above.

    HTMLParser puts script and style into raw-text mode and ends them at the
    first closing tag, which is exactly the rule that made the injected
    sidebar disappear into a template literal.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts = []          # inline script bodies, browser-delimited
        self.chrome_elements = 0   # sidebar seen as an ELEMENT, not as text
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and not a.get("src"):
            self._in_script = True
            self.scripts.append("")
        cls = a.get("class") or ""
        if tag in ("nav", "div") and any(m in cls for m in CHROME_MARKERS):
            self.chrome_elements += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and self.scripts:
            self.scripts[-1] += data


def sign_in(client):
    r = client.post("/login", data={
        "email": "pagecheck@smart1marketing.com",
        "password": os.environ["PANEL_PASSWORD"], "next": "/"})
    if r.status_code not in (302, 303):
        raise SystemExit(
            f"pagecheck could not sign in (/login returned {r.status_code}). "
            "PANEL_PASSWORD is the shared password the login page accepts.")


def mount_pages():
    """Every mounted module's root, so a new module is covered by existing."""
    import wsgi
    app = wsgi.application
    while not hasattr(app, "mounts"):
        app = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
        if app is None:
            return []
    out = []
    for prefix in sorted(app.mounts):
        if prefix in SKIP_MOUNTS:
            continue
        out.append(prefix + "/")
    return out


def check_page(client, path, quiet, unavailable):
    """Returns a list of problem strings — empty means the page is fine.

    A module that failed to load serves wsgi.py's _fallback_app with a 503.
    That is a configuration answer, not a page defect: run without a Postgres
    DATABASE_URL and Sites Admin reports 503 on any machine. It goes in the
    `unavailable` list and is named in the summary rather than counted as a
    pass, because a page silently treated as fine is exactly the confident
    wrong answer this codebase treats as worse than an error. --strict turns
    it into a failure, which is how CI runs it — there the database IS
    configured, so a 503 there means a module really is broken.
    """
    problems = []
    r = client.get(path, follow_redirects=True)
    if r.status_code == 503:
        unavailable.append(path)
        if not quiet:
            print(f"  ----  {path}  NOT CHECKED — the module reports 503 "
                  f"(it failed to start; usually DATABASE_URL)")
        return []
    if r.status_code != 200:
        return [f"{path}: HTTP {r.status_code}"]
    ctype = r.headers.get("Content-Type", "")
    if "text/html" not in ctype:
        return []                                  # not a page; nothing to say

    body = r.get_data(as_text=True)
    parser = PageParser()
    try:
        parser.feed(body)
        parser.close()
    except Exception as exc:                        # noqa: BLE001
        return [f"{path}: could not be parsed as HTML ({exc})"]

    wants_chrome = not path.startswith(NO_CHROME_PREFIXES)
    if wants_chrome and parser.chrome_elements == 0:
        # The distinction that matters: present in the bytes, absent as an
        # element, means it was injected inside a script or a string.
        if any(m in body for m in CHROME_MARKERS):
            problems.append(
                f"{path}: the Hub sidebar is in the response but NOT as an "
                f"element — it was injected inside a <script> or a string "
                f"literal, which ends that script early and breaks the page. "
                f"See the note in wsgi.py's HubBar.")
        else:
            problems.append(f"{path}: no Hub sidebar was injected")
    if not wants_chrome and parser.chrome_elements:
        problems.append(f"{path}: staff sidebar injected into a public page")

    for n, src in enumerate(parser.scripts):
        if not src.strip():
            continue
        try:
            err = js_check(src, f"{path} script #{n + 1}")
        except Exception as exc:                    # noqa: BLE001
            err = f"{path} script #{n + 1}: checker error {exc}"
        if err:
            problems.append(err)

    if not problems and not quiet:
        print(f"  ok    {path}  ({len(parser.scripts)} script block(s), "
              f"{parser.chrome_elements} chrome element(s))")
    return problems


def main():
    quiet = "--quiet" in sys.argv
    strict = "--strict" in sys.argv
    import wsgi
    from werkzeug.test import Client

    client = Client(wsgi.application)
    sign_in(client)

    pages = HUB_PAGES + mount_pages()
    problems, unavailable = [], []
    if not quiet:
        print(f"checking {len(pages)} rendered page(s)\n")
    for path in pages:
        problems.extend(check_page(client, path, quiet, unavailable))

    if strict:
        problems.extend(
            f"{p}: module unavailable (503) — in strict mode that is a failure"
            for p in unavailable)
        unavailable = []

    print()
    for p in problems:
        print("  FAIL  " + str(p))
    checked = len(pages) - len(unavailable)
    summary = f"{checked} of {len(pages)} pages checked, {len(problems)} problem(s)"
    if unavailable:
        summary += (f"; {len(unavailable)} NOT CHECKED (module unavailable): "
                    + ", ".join(unavailable))
    print(("\n" if problems else "") + summary + ".")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
