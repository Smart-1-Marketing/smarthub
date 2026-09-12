"""The Marketing Efficiency Audit -- test harness for the half that is not Python.

    python3 test_marketing_audit.py

Like the Display Ad Builder, `modules/marketing_audit` is a Node service and
this repo does not re-derive it in Flask -- the questionnaire, the scoring
model and the PDF are a working tool, ported wholesale, and this file asserts
facts stored in files rather than behaviour a unit test of either half would
already cover.

**Every asset and API call in this tool is written as a relative path, and
that is the one thing that had to be true for the whole port to work at
all.** The tool used to be served from a bare origin; it is proxied under
`/tools/marketing-audit/` now (`hub/marketing_audit_proxy.py`), and any
fetch call or asset tag whose path begins with a leading slash resolves
against the *Hub's* root instead, in exactly the shape this codebase's own
CLAUDE.md names as its most-repeated trap: a page that renders, links that
resolve, and every button silently reaching the wrong app. It bit twice
while this port was being built -- once in the static HTML and once inside
the embed loader's own iframe-source builder, which threw away the path
prefix entirely and pointed the frame at the Hub's own root -- and both are
asserted here so neither comes back.

**The proxy blueprint has to actually be registered.** The first draft of
`hub/marketing_audit_proxy.py` built the blueprint, added every route to it,
and never called `app.register_blueprint(bp)` -- every route 404'd, silently,
with no exception anywhere (`register()` returned `None` either way, and the
registration loop in hub/__init__.py wraps every tool in a try/except that
swallows exactly this). A source check for the call site is what catches this
class of bug, because a runtime test would need the Node process running to
notice the routes were never there at all.

**Lead delivery goes through hub/leads.py's one route, and nowhere else.**
The standalone build had its own GoHighLevel client and a generic webhook;
both are gone, and this asserts they stay gone -- a second lead-delivery path
reappearing here is exactly the duplicate-contact failure `hub/leads.py`'s
own docstring spends a section refusing.

**`docker-start.sh` must not leak this tool's own settings onto the Hub's.**
The child process needs its own `PUBLIC_BASE_URL` (with `/tools/marketing-audit`
appended, for its local-disk PDF fallback) and must not export that back into
the script's shared environment -- the Hub's own `hub/config.py` reads that
exact variable name for every OAuth redirect it builds, and a first draft of
this script did exactly that.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
MODULE = ROOT / "modules" / "marketing_audit"
PUBLIC = MODULE / "public"

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


def strip_js_comments(src: str) -> str:
    """Enough to keep a comment describing the trap from matching the trap."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", " ", src)
    return src


# Root-absolute references this port's own fix removed. Each is matched as an
# actual attribute/call value, not as a substring, so a comment that mentions
# "/api/analyze" while explaining the fix does not itself fail the check --
# the same "prose is not a call site" rule this repo's drift checks all share.
ROOT_ABSOLUTE_PATTERNS = (
    # An src or href attribute whose value opens with a single leading
    # slash -- not a protocol-relative "//host" one.
    re.compile(r'''(?:src|href)\s*=\s*["']/(?!/)'''),
    # A fetch() call whose first argument opens the same way.
    re.compile(r'''fetch\(\s*["']/(?!/)'''),
)


def test_no_root_absolute_paths():
    """Every asset and fetch call is relative, so the tool works at whatever prefix mounts it"""
    for name in ("public/index.html", "public/embed-demo.html", "public/app.js"):
        path = MODULE / name
        src = strip_js_comments(path.read_text())
        hits = [m.group(0) for pat in ROOT_ABSOLUTE_PATTERNS for m in pat.finditer(src)]
        check(f"{name} carries no root-absolute asset or fetch path",
              not hits, f"found: {hits}")


def test_embed_loader_keeps_the_path_prefix():
    """embed.js builds the iframe src from its own directory, not just its origin"""
    src = (PUBLIC / "embed.js").read_text()
    check("embed.js does not throw the path away with `origin + '/'`",
          "origin + '/?embed=1'" not in src)
    check("embed.js derives the iframe base from the script's own URL",
          "scriptUrl.href.replace" in src and "iframe.src = base +" in src)


def test_no_second_lead_delivery_path():
    """The tool has exactly one place a lead can go: the Hub"""
    server = (MODULE / "server.js").read_text()
    check("ghl.js is gone", not (MODULE / "ghl.js").exists())
    check("hubLeads.js exists", (MODULE / "hubLeads.js").exists())
    check("server.js requires hubLeads, not a GoHighLevel client",
          "require('./hubLeads')" in server and "require('./ghl')" not in server)
    for dead_env in ("GHL_WEBHOOK_URL", "GHL_API_KEY", "GHL_LOCATION_ID", "LEAD_WEBHOOK_URL"):
        check(f"server.js no longer reads {dead_env}",
              dead_env not in server)
    check("no /api/partial-lead route -- the Hub's capture route refuses a "
          "contact-less lead by design, so there is nothing this endpoint "
          "could ever successfully deliver",
          "/api/partial-lead" not in server)
    app_js = strip_js_comments((PUBLIC / "app.js").read_text())
    check("the browser no longer posts a partial lead either",
          "/api/partial-lead" not in app_js and "sendPartial" not in app_js)


def test_hub_leads_delivery_shape():
    """hubLeads.js posts to the one route hub/leads.py exists to be, with the shared secret"""
    src = (MODULE / "hubLeads.js").read_text()
    check("posts to /api/leads/capture", "/api/leads/capture" in src)
    check("sends the shared header hub/leads.py's trusted_source() reads",
          "X-S1-Lead-Token" in src)
    leads_py = (ROOT / "hub" / "leads.py").read_text()
    check("that header name matches hub/leads.py's own SOURCE_TOKEN_HEADER",
          'SOURCE_TOKEN_HEADER = "X-S1-Lead-Token"' in leads_py)


def test_proxy_blueprint_is_actually_registered():
    """hub/marketing_audit_proxy.py's register() calls app.register_blueprint -- the exact bug this file exists to catch"""
    src = (ROOT / "hub" / "marketing_audit_proxy.py").read_text()
    m = re.search(r"def register\(.*?\n(.*)", src, re.S)
    check("register() is defined", bool(m))
    body = m.group(1) if m else ""
    # Stop at the next top-level def/EOF so a later helper's own text cannot
    # satisfy this by accident.
    body = re.split(r"\n(?=\S)", body, maxsplit=1)[0]
    check("and it registers the blueprint it built",
          "app.register_blueprint(bp)" in body)
    check("the bare prefix redirects to the trailing-slash form the tool's "
          "own relative paths need",
          '@bp.route("")' in src and "redirect(f\"{url_prefix}/\")" in src)


def test_registered_on_the_hub_app():
    """The tool is wired into hub/__init__.py's registration table, or it is invisible"""
    init = (ROOT / "hub" / "__init__.py").read_text()
    check("hub.marketing_audit_proxy is in the blueprint-tools table",
          '"hub.marketing_audit_proxy"' in init and '"register"' in init)
    check("its whole prefix is chrome-free -- a partner has no Hub account "
          "and must never meet the staff sidebar",
          '"/tools/marketing-audit/"' in init)


def test_tiled():
    """A tool with no tile is invisible -- this repo's own rule, paid for six times already"""
    tools_html = (ROOT / "hub" / "templates" / "tools.html").read_text()
    check("Client Tools links to it",
          'href="/tools/marketing-audit/"' in tools_html)


def test_docker_start_does_not_leak_public_base_url():
    """The child process gets its own PUBLIC_BASE_URL; the Hub's is never overwritten"""
    src = (ROOT / "docker-start.sh").read_text()
    check("docker-start.sh starts the marketing-audit process",
          "start_marketing_audit" in src and "MARKETING_AUDIT_DIR" in src)
    check("HUB_BASE_URL is derived from the port captured before it is "
          "reassigned for any child process",
          "HUB_PORT=" in src and 'HUB_BASE_URL="http://127.0.0.1:${HUB_PORT}"' in src)
    # The dangerous shape: `export PUBLIC_BASE_URL=...` anywhere in the file
    # would rewrite the Hub's own copy of the variable for every process
    # started after it, including gunicorn itself.
    check("PUBLIC_BASE_URL is never exported into the script's own environment",
          not re.search(r"^\s*export\s+PUBLIC_BASE_URL=", src, re.M))
    check("it is set only inside the child process's own env prefix",
          re.search(r"PUBLIC_BASE_URL=.*node server\.js", src, re.S) is not None)


def test_dockerfile_installs_it():
    """The image actually contains this module's node_modules, or the proxy has nothing to talk to"""
    dockerfile = (ROOT / "Dockerfile").read_text()
    check("Dockerfile installs modules/marketing_audit's dependencies",
          "modules/marketing_audit" in dockerfile and "npm ci" in dockerfile)


def test_package_json_has_no_build_step_to_forget():
    """Plain Express, no TypeScript -- confirms the Dockerfile is right to skip a build/prune step"""
    import json
    pkg = json.loads((MODULE / "package.json").read_text())
    check("no devDependencies to accidentally ship or accidentally omit",
          not pkg.get("devDependencies"))
    check("no build script the Dockerfile would need to run",
          "build" not in (pkg.get("scripts") or {}))


def main():
    print(__doc__.strip().splitlines()[0])
    print()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(fn.__doc__.strip().splitlines()[0] if fn.__doc__ else name)
            fn()
            print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        for f in FAIL:
            print(f"  FAILED: {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
