"""Every route in the composed app, asked anonymously: who can reach it?

    python3 test_blueprint_guards.py

## Why this exists

`wsgi.py` wraps every *dispatcher-mounted* module in `AuthGuard`. A module
registered as a **blueprint on the hub app** never passes through it, and the
hub app has no blanket gate of its own — its own pages are guarded view by
view. So a blueprint that does not guard itself serves everything it has to
anyone with the URL, and nothing says so: the tile beside it redirects to
`/login` while the tool itself answers 200.

This repository has paid for that three times over. Commercial Builder shipped
with every page and API route open, client names and briefs included.
`modules/calculators` shipped with `/tools/calculators/leads` open — a table of
real people's names, emails and phone numbers. Both were fixed by writing the
same `before_request` into that module, and neither fix could see the others.

A sweep of the composed app then found **three more still open**: Web Tickets
and its Knack field map, the Page Image Optimizer and its saved-job archive,
and Video Search with its Cloudinary library, search and status routes. Ten
routes in total, pages and APIs alike.

So the gate is shared now (`hub/blueprint_guard.py`) and this is the check
that stops a fourth. It is deliberately **empirical**: it boots the composed
app and asks every route, rather than reading source and inferring. A guard
that is present but does not apply looks identical to one that is absent in
any static reading, and it is the applying that matters.

## The two ways this check could go wrong

**It could start red**, and a check that starts red is one somebody switches
off — CLAUDE.md says so twice. So the baseline below is what the app actually
served when this was written, each entry with the reason it is public.

**Its allowlist could outlive what it exempts.** An entry naming a route that
no longer exists goes on quietly covering whatever is added at that path next.
`stale` catches that, the same way `jsonstore.stale_exemptions()` does for the
unbacked-JSON check.
"""
import ast
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-guards-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["SECRET_KEY"] = "guards-test"
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["AUDIT_LOG_PATH"] = os.path.join(_TMP, "audit.jsonl")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


# Every path that may answer 200 with no Hub session, and why. A prefix
# entry ends in "*". Adding to this list is a deliberate act: it is the list
# of things a stranger with the URL can read.
PUBLIC: dict[str, str] = {
    # --- signing in, and getting back in ---
    "/login": "the sign-in page itself",
    "/login/health": "diagnoses sign-in without a session — being locked out "
                     "is exactly when it is needed",
    "/signup": "account creation",
    "/forgot": "names who to ask; there is no mailer in this Hub",

    # --- liveness, for the platform rather than for a person ---
    "/health": "Render's health probe",
    "/healthz": "Render's health probe",
    "/api/version": "the build tag, exempt so a locked-out person can report it",

    # --- crawlers ---
    "/robots.txt": "has to be readable to be obeyed",
    "/llms.txt": "the AI-crawler refusal, which has to be readable to be "
                 "obeyed as well",

    # --- shared front-end assets, served at the root by hub/help_routes.py ---
    "/hub-*": "the chrome's own scripts and stylesheet, injected into every "
              "page including the twenty mounted modules",
    "/ad-copy.js": "the Ad Copy request drawer, loaded by three templates",
    "/knack-form.js": "the shared Knack form renderer",
    "/web-ticket.js": "the web ticket drawer",
    "/campaign-request.js": "the campaign change drawer",
    "/ask-analytics.js": "the analytics question box",

    # --- the help and demo registries ---
    "/api/help*": "the help bubble text, which is our own explanation of our "
                  "own screens and carries no client data",
    "/api/demos*": "the walkthrough scenarios, likewise",

    # --- client-facing by design ---
    "/suite-app": "the Smart 1 Suite SSO frame. A client has no Hub account; "
                  "it renders nothing of theirs until the handshake proves "
                  "which sub-account they are in (hub/suite_sso.py)",
    "/tools/calculators/embed.js": "the resizer a host page loads beside an "
                                   "embedded calculator",

    # --- module health, deliberately outside the login ---
    "/tools/calculators/api/health": "reports slugs and whether lead delivery "
                                     "is configured; no client data",
    "/tools/google-access/api/health": "reports whether the OAuth client "
                                       "and scopes are set; no client data",
    "/tools/image-picker/api/health": "reports whether Cloudinary is "
                                      "configured; no client data",
    "/tools/image-picker/api/taxonomy": "the industry chip vocabulary, which "
                                        "is ours rather than any client's",
}


def _allowed(path: str) -> bool:
    if path in PUBLIC:
        return True
    return any(path.startswith(k[:-1]) for k in PUBLIC if k.endswith("*"))


import wsgi                                                       # noqa: E402
from hub import create_hub_app                                    # noqa: E402
from werkzeug.test import Client                                  # noqa: E402


def static_get_routes() -> set[str]:
    """Every GET route with no variable in it, hub app and mounts alike."""
    out: set[str] = set()
    hub = create_hub_app()
    for rule in hub.url_map.iter_rules():
        path = str(rule)
        if "<" not in path and "GET" in (rule.methods or set()):
            out.add(path)
    mounts = getattr(wsgi.application, "mounts", {}) or {}
    for prefix, sub in mounts.items():
        inner = sub
        for _ in range(6):
            if hasattr(inner, "url_map"):
                break
            inner = getattr(inner, "app", None) or getattr(inner, "wsgi_app", None) or inner
        if not hasattr(inner, "url_map"):
            continue                                    # a 503 fallback app
        for rule in inner.url_map.iter_rules():
            path = str(rule)
            if "<" not in path and "GET" in (rule.methods or set()):
                out.add((prefix + path).replace("//", "/"))
    return out


anon = Client(wsgi.application)
routes = static_get_routes()

# ---------------------------------------------------------------------------
print("\nNothing answers a stranger that is not on the list")
# ---------------------------------------------------------------------------
check("the composed app booted with its routes", len(routes) > 100, len(routes))

reachable = set()
for path in sorted(routes):
    try:
        if anon.get(path).status_code == 200:
            reachable.add(path)
    except Exception:                                             # noqa: BLE001
        pass

undeclared = sorted(p for p in reachable if not _allowed(p))
check("every anonymously reachable route is one this file declares public",
      not undeclared,
      "NOT DECLARED: " + ", ".join(undeclared) if undeclared else "")
check("and the app does have routes a stranger cannot reach",
      len(routes) - len(reachable) > 100, (len(routes), len(reachable)))


# ---------------------------------------------------------------------------
print("\nThe three blueprints that were open are shut")
# ---------------------------------------------------------------------------
# Each of these served pages *and* API routes to anyone with the URL until the
# shared guard went on. Named individually rather than trusted to the sweep
# above, so that if somebody removes a guard the failure says which module.
WAS_OPEN = {
    "Web Tickets": ["/tools/tickets/", "/tools/tickets/setup",
                    "/tools/tickets/api/fieldmap"],
    "Page Image Optimizer": ["/tools/page-images/",
                             "/tools/page-images/api/archive",
                             "/tools/page-images/api/health"],
    "Video Search": ["/tools/video-backgrounds/",
                     "/tools/video-backgrounds/api/search",
                     "/tools/video-backgrounds/api/status",
                     "/tools/video-backgrounds/api/pending"],
}
for name, paths in WAS_OPEN.items():
    still = [p for p in paths if anon.get(p).status_code == 200]
    check(f"{name} refuses a stranger", not still, still)


# ---------------------------------------------------------------------------
print("\nAnd a signed-in member of staff still gets in")
# ---------------------------------------------------------------------------
# The other half. A guard that refuses everybody is not a fix, and this is the
# cheapest way for that to go unnoticed.
staff = Client(wsgi.application)
staff.post("/login", data={"password": "test"}, follow_redirects=True)
for name, paths in WAS_OPEN.items():
    page = paths[0]
    code = staff.get(page).status_code
    check(f"{name} opens for staff", code == 200, f"{page} -> {code}")


# ---------------------------------------------------------------------------
print("\nThe allowlist has not outlived what it exempts")
# ---------------------------------------------------------------------------
# An entry naming a route that is gone goes on covering whatever is added at
# that path next — the failure `jsonstore.stale_exemptions()` names.
stale = []
for entry in PUBLIC:
    if entry.endswith("*"):
        if not any(p.startswith(entry[:-1]) for p in routes):
            stale.append(entry)
    elif entry not in routes:
        stale.append(entry)
check("no entry names a route that no longer exists", not stale, stale)

unreached = [e for e in PUBLIC
             if not e.endswith("*") and e in routes and e not in reachable]
check("and every entry is genuinely reachable, so none is covering nothing",
      not unreached, unreached)

check("every entry says why it is public",
      all(len(v) > 15 for v in PUBLIC.values()),
      [k for k, v in PUBLIC.items() if len(v) <= 15])


# ---------------------------------------------------------------------------
print("\nThe shared gate is shared, not copied a fourth time")
# ---------------------------------------------------------------------------
from hub import blueprint_guard                                   # noqa: E402
check("hub/blueprint_guard.py is the one implementation",
      callable(blueprint_guard.install))
# Every blueprint-registered module that needs a login gate, including the
# two that had written their own before this was shared. A module here that
# stops reading the shared gate has either lost its guard or grown a sixth
# copy of it, and both are the failure this file exists for.
BLUEPRINTS = ("modules/tickets/app.py",
              "modules/page_image_optimizer/app.py",
              "modules/video_backgrounds/app.py",
              "modules/calculators/app.py",
              "modules/commercial_builder/__init__.py")
missing = [m for m in BLUEPRINTS
           if "blueprint_guard" not in
           open(os.path.join(ROOT, m), encoding="utf-8").read()]
check("and every guarded blueprint reads it rather than restating it",
      not missing, missing)

# The gate decides who gets in; a module keeping its own `before_request`
# beside it is a second answer to that, and the two drift the day either is
# edited. `hub/auth.py` and `wsgi.py` are the mounted half and are not
# blueprints, so they are not in the list above.
#
# Read as a decorator through the AST rather than matched as text: three of
# these files *explain* the before_request they no longer have, and a
# full-text pass reports the explanation of the fix as the defect. That is
# the rule `tools/spellcheck.py` and the env-drift check already work to.
def _has_own_hook(path: str) -> bool:
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and \
                    target.attr in ("before_request", "before_app_request"):
                return True
    return False


own = [m for m in BLUEPRINTS if _has_own_hook(os.path.join(ROOT, m))]
check("and none of them still keeps a login check of its own", not own, own)


print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
