"""Every write into a client's own account has a name against it.

    python3 test_write_attribution.py

No pytest, no new dependencies, and nothing here reaches Simvoly or Google:
the checks are over the modules' own source and their declared exemptions.

## Why this file exists

`/api/integrity`'s silent-module check reads a **call** now rather than an
import, and the seven modules that had bound `hub.audit` and never used it
were fixed. CLAUDE.md names what that was worth: *deleting a client's live
website, connecting a domain, deploying a tag into somebody else's Tag Manager
container and compressing a client's documents were the least attributable
actions in this Hub*.

**That sweep wired a handful of call sites per module and stopped**, and
nothing could see the remainder, because the check one level up is satisfied
by **one** call site — the same shape as the check that read the string
`for_module(` and counted the binding. A module can be loudly attributable
about a quarter of its work and pass.

Two modules were found doing exactly that, and in both the *creating* half of
a create/destroy pair was the half left out:

  * **Sites Admin** recorded `delete_website` and not `add_site`, which makes
    a client's website and can activate a paid plan — so the record showed
    sites being deleted by somebody and appearing from nowhere. Nor
    `personalization`, which writes brand colors onto their live pages, nor
    `pricing`, which sets what they are billed *and* `internal_client_name`,
    the join `hub/domain_links.py` writes and every domain-keyed report reads.
  * **Google Finder** recorded `disconnect` and not `oauth_callback`, which is
    the moment the Hub *gains* a refresh token for somebody else's Google
    account. And it recorded `gtm_deploy_event` while `gtm_deploy_pixel` —
    arbitrary code, in a container we do not own — went unrecorded, which is
    the very action CLAUDE.md names. `api_gsc_bulk_add` writes properties into
    their Search Console and was silent too.

`hub.audit.write_route_attribution()` is the one walk both are held to, rather
than a copy per module: two readings of one question drift the day either is
edited, which is the failure `_client_log_modules()` already had to undo.

**What is not here is a repo-wide gate, deliberately.** The same walk over
every module that logs finds ~229 silent write routes across 34 files, and the
great majority are genuinely housekeeping — autosaves, drafts, previews, and
GA4's `runReport`, which is a POST that reads. A check landing with 229
findings nobody can act on is the one people learn to skip, which is the note
`help_audit.demo_targets()` already makes about the walkthrough backlog. The
modules that have been triaged declare their remainder and are held to it;
the rest is a backlog somebody works down.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import ast                                                         # noqa: E402
from hub import audit                                              # noqa: E402

# Each module, the writes that must be attributable, and the guards it puts
# in front of them. A module is here once it has been triaged; adding one is
# reading its write routes and declaring the remainder.
TRIAGED = {
    "sites_admin": {
        "path": "modules/sites_admin/app.py",
        "must_log": ["add_site", "personalization", "pricing", "project_sso",
                     "delete_website", "connect_domain", "disconnect_domain",
                     "project_action"],
        # Ordered: each logs only once the provider answered, so a change
        # Simvoly refused is not written down as one that was made.
        "after_provider": {"add_site": "client.create_project_website(",
                           "personalization": "client.set_personalization_tags(",
                           "project_sso": "client.start_building_session(",
                           "delete_website": "client.set_website_status("},
        "guards": ("login_required", "require_csrf()"),
        "guard_exempt": {"login"},
    },
    "google_finder": {
        "path": "modules/google_finder/app.py",
        "must_log": ["disconnect", "gtm_deploy_event",
                     "gtm_deploy_pixel", "api_gsc_bulk_add"],
        # Google redirects the browser here, so it is a GET by protocol and
        # the method-based walk above cannot see it -- while what it does is
        # store a refresh token for somebody else's Google account, which is
        # the grant every write in this module is made under. Asserted by
        # name, because the one thing worse than a walk that misses a route
        # is a walk that misses it silently.
        "writes_but_not_a_write_method": ["oauth_callback"],
        "after_provider": {"gtm_deploy_pixel": "google_post(",
                           "api_gsc_bulk_add": "google_put("},
        "guards": (),
        "guard_exempt": set(),
    },
}

SOURCES = {name: (ROOT / cfg["path"]).read_text() for name, cfg in TRIAGED.items()}
WALKS = {name: audit.write_route_attribution(src) for name, src in SOURCES.items()}


# ---------------------------------------------------------------------------
section("One walk, read by both, and it read something")
# ---------------------------------------------------------------------------
check("the walk is shared rather than copied per module",
      callable(audit.write_route_attribution))
for name, walk in WALKS.items():
    # Finding no routes is a failure, not a clean sweep: a walk that quietly
    # stops walking reports a clean bill of health about nothing.
    check(f"{name}: the write routes were found",
          len(walk["logs"]) + len(walk["silent"]) > 8, True)
    check(f"{name}: and its declaration was read", len(walk["declared"]) > 0, True)


# ---------------------------------------------------------------------------
section("Every write logs, or is declared with its reason")
# ---------------------------------------------------------------------------
for name, walk in WALKS.items():
    undeclared = sorted(set(walk["silent"]) - set(walk["declared"]))
    check(f"{name}: no write is silent without a reason written down",
          undeclared, [])
    blank = sorted(k for k, v in walk["declared"].items() if not str(v).strip())
    check(f"{name}: every declared entry carries its reason", blank, [])

    # An exemption that outlives what it exempted goes on covering whatever is
    # written at that name next -- check_stale_json_exemptions()'s rule.
    known = set(walk["logs"]) | set(walk["silent"])
    gone = sorted(n for n in walk["declared"] if n not in known)
    check(f"{name}: no entry names a route that no longer exists", gone, [])
    moved = sorted(n for n in walk["declared"] if n in walk["logs"])
    check(f"{name}: nor one that has since started logging", moved, [])

    for fn in TRIAGED[name]["must_log"]:
        check(f"  {name}.{fn} records who did it", fn in walk["logs"])

    # Routes that write without a write method: a GET the walk cannot classify.
    for fn in TRIAGED[name].get("writes_but_not_a_write_method", []):
        node = next(n for n in ast.walk(ast.parse(SOURCES[name]))
                    if isinstance(n, ast.FunctionDef) and n.name == fn)
        body = ast.get_source_segment(SOURCES[name], node) or ""
        check(f"  {name}.{fn} records who did it, though it is a GET",
              "_audit(" in body)


# ---------------------------------------------------------------------------
section("A refused change is not recorded as a made one")
# ---------------------------------------------------------------------------
for name, cfg in TRIAGED.items():
    tree = ast.parse(SOURCES[name])
    for fn, provider_call in cfg["after_provider"].items():
        node = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == fn)
        body = ast.get_source_segment(SOURCES[name], node) or ""
        check(f"{name}.{fn} logs only after the provider answered",
              body.index(provider_call) < body.index("_audit("))


# ---------------------------------------------------------------------------
section("The guards in front of those writes are still on all of them")
# ---------------------------------------------------------------------------
for name, cfg in TRIAGED.items():
    if not cfg["guards"]:
        continue
    tree = ast.parse(SOURCES[name])
    walk = WALKS[name]
    writes = set(walk["logs"]) | set(walk["silent"])
    dec_guard, body_guard = cfg["guards"]
    missing_dec, missing_body = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name not in writes:
            continue
        if node.name in cfg["guard_exempt"]:
            continue
        decs = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
        body = ast.get_source_segment(SOURCES[name], node) or ""
        if dec_guard not in decs:
            missing_dec.append(node.name)
        if body_guard not in body:
            missing_body.append(node.name)
    check(f"{name}: every write is behind {dec_guard}", sorted(missing_dec), [])
    check(f"{name}: and every one checks its CSRF token", sorted(missing_body), [])


# ---------------------------------------------------------------------------
section("And the walk bites when a write route goes silent")
# ---------------------------------------------------------------------------
# A check that has only ever been green is one nobody can trust. Handed a
# module with a write route that records nothing and is in no declaration, it
# must name it -- and must not name the same route once the call is there.
_SILENT = '''
HOUSEKEEPING_ROUTES = {"sync": "reads only"}

@app.post("/projects/<pid>/rename")
def rename_project(pid):
    client.rename(pid, request.form.get("name"))
    return "ok"

@app.post("/sync")
def sync():
    return "ok"

@app.get("/projects")
def list_projects():
    return "ok"
'''
probe = audit.write_route_attribution(_SILENT)
check("a new write route that logs nothing is named",
      sorted(set(probe["silent"]) - set(probe["declared"])), ["rename_project"])
check("a GET is not asked the question at all", "list_projects" in probe["silent"], False)

_LOUD = _SILENT.replace('    client.rename(pid, request.form.get("name"))',
                        '    client.rename(pid, request.form.get("name"))\n'
                        '    _audit("project_renamed", project=pid)')
check("and the same route with the call is not",
      "rename_project" in audit.write_route_attribution(_LOUD)["logs"])

# Prose is not a call site: both modules explain at length why _audit went
# uncalled, and a check matching text reports the explanation as the defect.
_PROSE = _SILENT.replace("    client.rename",
                         '    """_audit was bound here and called nowhere."""\n    client.rename')
check("a docstring naming _audit does not count as calling it",
      "rename_project" in audit.write_route_attribution(_PROSE)["silent"])

# A module's own log() wrapper is a call -- the shape radio_promo and
# landing_ads use, which check_work_kinds() had to learn to resolve.
_WRAPPED = _SILENT.replace('    client.rename(pid, request.form.get("name"))',
                           '    client.rename(pid, request.form.get("name"))\n'
                           '    log("project_renamed", project=pid)')
check("a module's own log() wrapper counts",
      "rename_project" in audit.write_route_attribution(_WRAPPED)["logs"])


# ---------------------------------------------------------------------------
section("Both modules still log under a name the client record can place")
# ---------------------------------------------------------------------------
from hub import client_brand                                        # noqa: E402

for name in TRIAGED:
    check(f"{name} binds the shared logger",
          f'for_module("{name}")' in SOURCES[name])
    # Only a module that files a row *against a client* has to be a name the
    # record can place -- that is what check_work_kinds() asks. Google Finder
    # records a google_login and never a client, so it is outside that
    # question rather than missing from it, and asserting otherwise would be
    # inventing a rule this Hub does not have.
    files_client_work = "client=" in SOURCES[name]
    if files_client_work:
        check(f"  and {name} is a name the client record can place",
              name in client_brand.WORK_KINDS
              or name in getattr(client_brand, "NOT_WORK", {}))
    else:
        check(f"  {name} files no client work, so it needs no entry",
              name not in client_brand.WORK_KINDS)

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
