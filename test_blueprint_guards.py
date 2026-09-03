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

## The three ways this check could go wrong

**It could start red**, and a check that starts red is one somebody switches
off — CLAUDE.md says so twice. So the baseline below is what the app actually
served when this was written, each entry with the reason it is public.

**Its allowlist could outlive what it exempts.** An entry naming a route that
no longer exists goes on quietly covering whatever is added at that path next.
`stale` catches that, the same way `jsonstore.stale_exemptions()` does for the
unbacked-JSON check.

**And it could quietly stop asking.** The first version of this file reached
the mount table with `getattr(wsgi.application, "mounts", {})` — and
`wsgi.application` is a `ProxyFix` wrapping `NoIndex` wrapping `ErrorMirror`
wrapping the `DispatcherMiddleware` that actually holds it. So the default
answered, the walk found **no mounts at all**, and the sweep covered the hub
app and its blueprints while reporting that it had asked the composed app:
199 routes checked where there are 415, with twenty mounted modules — every
landing page, Smart 1 Ads, the Proposal Builder, Site Scans — never asked.
It passed, which is the whole failure. `_dispatcher()` unwraps to whichever
layer holds `mounts`, and finding none is a **failure** rather than an empty
sweep: a check that answers "nothing is open" because it looked at nothing is
worse than no check.

**A write route is the one worth having.** The same version asked GET and
nothing else, so a POST that creates, deletes or sends would not have been
seen. Every static write route is asked too, with its own baseline — the
landing pages' lead capture, the MSA signing, sign-up and password reset are
public by design and say so per entry.
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
    "/suite-app/start": "the app's own getting-started tab, framed by Suite "
                        "for whoever installed it. Our copy about our app -- "
                        "it reads nothing and renders nothing belonging to a "
                        "client. Public because that reader may have no Hub "
                        "account, and a sign-in form there teaches them the "
                        "app needs one",
    "/tools/calculators/embed.js": "the resizer a host page loads beside an "
                                   "embedded calculator",

    # --- module health, deliberately outside the login ---
    # /tools/calculators/api/health is NOT here any more: it reports which
    # CALC_WEBHOOK_ overrides are set and the delivery route, and it is the
    # one staff route under that module's public /api/ prefix. The guard
    # excludes it by name (modules/calculators/app.py, PUBLIC_EXCLUDED) and
    # test_calculator_embeds.py asserts a stranger gets a 401.
    "/tools/google-access/api/health": "reports whether the OAuth client "
                                       "and scopes are set; no client data",
    "/tools/image-picker/api/health": "reports whether Cloudinary is "
                                      "configured; no client data",
    "/tools/image-picker/api/taxonomy": "the industry chip vocabulary, which "
                                        "is ours rather than any client's",

    # --- dispatcher-mounted, public by declaration -------------------------
    # These reach a prospect on somebody else's website. Each module declares
    # PUBLIC_PREFIXES and wsgi.py's _mount() hands it to *both* AuthGuard and
    # HubBar, so the page is outside the login and carries none of our chrome.
    # They are prefixes here because the set is per landing page rather than
    # per route, and a tenth industry page must not need an edit to nine.
    "/land/*": "the nine industry landing pages, their iframe copies for "
               "smart1marketing.com, the resizer a host page loads beside "
               "one, and each page's own health probe. Every one is served "
               "to a prospect who has no Hub account",
    "/msa/*": "the client's MSA signing page, its embed and its health "
              "probe -- a document sent to somebody for signature, so a "
              "login in front of it is a form they cannot fill in",
    "/scans/embed.js": "the resizer a client's website loads beside an "
                       "embedded scan widget",
}


def _allowed(path: str) -> bool:
    if path in PUBLIC:
        return True
    return any(path.startswith(k[:-1]) for k in PUBLIC if k.endswith("*"))


# Writes a stranger may make. Read and write are different permissions, so
# this is its own list rather than the one above: a page somebody may look at
# is not a form somebody may submit, and folding them together would let an
# entry written for a readable page quietly cover a route that creates
# something.
PUBLIC_WRITES: dict[str, str] = {
    "/land/*": "every landing page's lead capture and its abandoned-form "
               "partial, plus the estimate and analysis a prospect runs on "
               "the page. All of it goes through hub/leads.py, which is the "
               "one write path for a lead; a login here is a form the "
               "prospect cannot fill in, so there would be no lead at all",
    "/msa/*": "the client signing their MSA. The whole document exists to "
              "be signed by somebody with no Hub account",
    "/api/leads/capture": "the same capture, reached by the hub app's own "
                          "landing routes",
    "/signup": "a person asking for an account necessarily has none yet",
    "/reset": "completing an admin-issued reset token -- somebody locked "
              "out has no session to prove anything with. hub/auth.py's "
              "throttle is what stops this being guessed at",
    "/api/demos/event": "the walkthrough's own telemetry, which records "
                        "that a tour step was shown and carries no client "
                        "data",
    "/api/help/tour-event": "the same, for the help layer",
}


def _allowed_write(path: str) -> bool:
    if path in PUBLIC_WRITES:
        return True
    return any(path.startswith(k[:-1]) for k in PUBLIC_WRITES if k.endswith("*"))


import wsgi                                                       # noqa: E402
from hub import create_hub_app                                    # noqa: E402
from werkzeug.test import Client                                  # noqa: E402


def _dispatcher(app):
    """The layer of the middleware stack that actually holds the mounts.

    `wsgi.application` is a ProxyFix wrapping NoIndex wrapping ErrorMirror
    wrapping the DispatcherMiddleware, and the first version of this file
    read `getattr(wsgi.application, "mounts", {})` — which answered with the
    default, so the whole mounted half of the app went unasked while this
    file reported having swept it. Unwrap rather than assume a depth: the
    next middleware added to `wsgi.py` must not be able to switch this off.
    """
    seen = set()
    while app is not None and id(app) not in seen:
        seen.add(id(app))
        if hasattr(app, "mounts"):
            return app
        app = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
    return None


def _flask_of(sub):
    """The Flask app inside a mount, or None for a 503 fallback."""
    inner = sub
    for _ in range(6):
        if hasattr(inner, "url_map"):
            return inner
        inner = getattr(inner, "app", None) or getattr(inner, "wsgi_app", None) or inner
    return inner if hasattr(inner, "url_map") else None


WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")


def static_routes() -> tuple[set[str], set[tuple[str, str]]]:
    """Every route with no variable in it, hub app and mounts alike.

    Returns the GET paths and the (path, method) writes separately: they are
    asked differently and have their own baselines, because a page a stranger
    may read and a write a stranger may make are different permissions.
    """
    gets: set[str] = set()
    writes: set[tuple[str, str]] = set()

    def take(prefix, url_map):
        for rule in url_map.iter_rules():
            path = str(rule)
            if "<" in path:
                continue
            full = (prefix + path).replace("//", "/") if prefix else path
            methods = rule.methods or set()
            if "GET" in methods:
                gets.add(full)
            for m in WRITE_METHODS:
                if m in methods:
                    writes.add((full, m))
                    break

    take("", create_hub_app().url_map)
    for prefix, sub in (MOUNTS or {}).items():
        app = _flask_of(sub)
        if app is not None:                             # else a 503 fallback
            take(prefix, app.url_map)
    return gets, writes


_disp = _dispatcher(wsgi.application)
MOUNTS = getattr(_disp, "mounts", None) if _disp is not None else None

anon = Client(wsgi.application)
routes, writes = static_routes()

# ---------------------------------------------------------------------------
print("\nThe sweep reaches the whole composed app")
# ---------------------------------------------------------------------------
# Asked before anything else, because every assertion below is worthless if
# the walk quietly covered a fraction of the app -- which is exactly how the
# first version of this file passed while never asking a mounted module.
check("the mount table was found in the middleware stack",
      MOUNTS is not None and len(MOUNTS) > 0,
      f"{len(MOUNTS)} mounts" if MOUNTS else "NO MOUNTS FOUND — the walk "
      "covered the hub app only")
# wsgi.py mounts twenty-odd modules. A number far below that means a mount
# stopped resolving to a Flask app, which shrinks the sweep in silence.
check("and it holds the modules wsgi.py mounts",
      len(MOUNTS or {}) >= 25, len(MOUNTS or {}))
# The named ones are the load-bearing check: a set that is the right *size*
# and the wrong contents is the same failure one step further on.
for prefix in ("/scans", "/sales/builder", "/tools/ads", "/land/stadium"):
    check(f"  {prefix} is in the sweep",
          any(p.startswith(prefix + "/") or p == prefix for p in routes), True)


# ---------------------------------------------------------------------------
print("\nNothing answers a stranger that is not on the list")
# ---------------------------------------------------------------------------
check("the composed app booted with its routes", len(routes) > 300, len(routes))

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
# ---------------------------------------------------------------------------
print("\nAnd no stranger can write")
# ---------------------------------------------------------------------------
# The GET sweep above is about what can be read. This is the half that
# matters more and that the first version of this file never asked at all: a
# POST that creates, sends or deletes, reachable with no session.
#
# A 401, a 403 or a redirect to the login is a refusal. So is a 400 -- the
# route was reached, and the guard is not what answered -- so those are read
# as *reached* and held to the same allowlist, because a validation error is
# what an open write looks like when the empty body happens not to satisfy
# it. Nothing is sent but an empty JSON body, so nothing here creates a row.
check("the sweep found the write routes too", len(writes) > 200, len(writes))

answered = set()
for path, method in sorted(writes):
    try:
        r = anon.open(path, method=method, json={})
    except Exception:                                             # noqa: BLE001
        continue
    if r.status_code in (401, 403) or 300 <= r.status_code < 400:
        continue
    answered.add(path)

undeclared_writes = sorted(p for p in answered if not _allowed_write(p))
check("every write a stranger can reach is one this file declares public",
      not undeclared_writes,
      "NOT DECLARED: " + ", ".join(undeclared_writes)
      if undeclared_writes else "")
check("and the app does have writes a stranger cannot reach",
      len(writes) > len(answered), f"{len(answered)} of {len(writes)} reachable")

# Same rule the read allowlist is held to: an exemption that outlives what it
# exempted goes on covering whatever is written at that path next.
stale_writes = sorted(k for k in PUBLIC_WRITES if not k.endswith("*")
                      and k not in {p for p, _ in writes})
check("no write exemption names a route that no longer exists",
      not stale_writes, stale_writes)
covering_nothing = sorted(k for k in PUBLIC_WRITES
                          if not any(_allowed_write(p) and
                                     (p == k or p.startswith(k[:-1]))
                                     for p in answered))
check("and every write exemption is genuinely reachable",
      not covering_nothing, covering_nothing)


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
