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
    "seo_images": {
        "path": "modules/seo_images/app.py",
        # Its wrapper is called `_log`, which is why this module is here: the
        # walk hard-coded `_audit` and `log`, so a module recording five of
        # its seven writes read as recording none. A check that invents
        # findings is switched off faster than one that misses them.
        "must_log": ["api_finalize", "api_save_one", "api_gallery_update",
                     "api_add_house_client", "api_delete_house_client"],
        "after_provider": {},
        "guards": (),
        "guard_exempt": set(),
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
    # Nine files rather than one, which is why `path` takes a list: this
    # module is a blueprint package and its writes are spread over the wizard,
    # the client record, the review link and the render queue. Merging them is
    # right — `HOUSEKEEPING_ROUTES` is per file because that is where the
    # reason belongs, and the question "is every write here attributable" is
    # about the module.
    "commercial_builder": {
        # A **glob**, not a list of the nine files that had writes the day
        # this was written. `pages.py` and `stock.py` have none today, so a
        # hand-list would have left them off and a write route added to
        # either next month would have been invisible to the sweep -- which
        # is the "a sweep that quietly stops sweeping" failure this whole
        # change is about, one level up from the walk it fixed. Finding no
        # files is asserted as a failure below, so the glob cannot silently
        # stop matching either.
        "path_glob": "modules/commercial_builder/routes/*.py",
        # The two deletes and the two creates, because in both earlier
        # triages the *creating* half of a create/destroy pair was the half
        # left out — and here neither half of either pair was recorded. The
        # rest are the moments something reaches the client: a cut submitted
        # and approved, a link sent and taken back, an answer arriving, a
        # voiceover kept, footage uploaded, a sign-off given.
        "must_log": ["create_client", "update_client", "adopt_hub_client",
                     "delete_client", "start_commercial", "delete_project",
                     "submit_render", "approve_render", "acknowledge_compliance",
                     "send_for_review", "revoke_review", "client_decide",
                     "client_comment", "save_pronunciation",
                     # The finished spot reaching the client's own CRM
                     # account. Same class as approving a cut: it is a write
                     # into somebody else's Suite sub-account, and an
                     # unattributable one is the thing this sweep exists for.
                     "push_to_suite",
                     "generate_full_voiceover", "upload_scene_asset",
                     "generate_ai_video"],
        "after_provider": {},
        "guards": (),
        "guard_exempt": set(),
    },
}


def _paths(cfg):
    """Every file a module is spread across: named, or matched from disk.

    A single-file module names its one path; a blueprint package globs its
    routes directory, so the sweep covers a file added to it without anybody
    remembering to widen a list.
    """
    pattern = cfg.get("path_glob")
    if pattern:
        found = sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)
                       if p.name != "__init__.py")
        return found
    raw = cfg["path"]
    return [raw] if isinstance(raw, str) else list(raw)


# Per module, every file it is spread across. A single-file module is a
# one-entry list, so nothing below has to branch on which shape it got.
FILES = {name: [(p, (ROOT / p).read_text()) for p in _paths(cfg)]
         for name, cfg in TRIAGED.items()}


def _merge(walks):
    out = {"logs": [], "silent": [], "declared": {}}
    for w in walks:
        out["logs"] += w["logs"]
        out["silent"] += w["silent"]
        out["declared"].update(w["declared"])
    out["logs"] = sorted(set(out["logs"]))
    out["silent"] = sorted(set(out["silent"]))
    return out


WALKS = {name: _merge([audit.write_route_attribution(src) for _, src in files])
         for name, files in FILES.items()}


def _find(name, fn):
    """The function and the source it came from, in whichever file holds it.

    `ast.get_source_segment` needs the exact text the node was parsed from, so
    the pair travels together rather than the caller guessing which file to
    read it back out of.
    """
    for _, src in FILES[name]:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == fn:
                return node, src
    return None, ""


# ---------------------------------------------------------------------------
section("One walk, read by both, and it read something")
# ---------------------------------------------------------------------------
check("the walk is shared rather than copied per module",
      callable(audit.write_route_attribution))
for name, walk in WALKS.items():
    # Finding no routes is a failure, not a clean sweep: a walk that quietly
    # stops walking reports a clean bill of health about nothing.
    # Finding no routes is a failure, not a clean sweep -- but the floor is a
    # handful rather than a big number: seo_images has seven writes and a
    # threshold set from the largest module would have excused an empty walk
    # over a small one.
    check(f"{name}: the write routes were found",
          len(walk["logs"]) + len(walk["silent"]) >= 5, True)
    check(f"{name}: and its declaration was read", len(walk["declared"]) > 0, True)
    # A glob that stops matching reports a clean bill of health about
    # nothing, which is the same failure the walk itself had.
    check(f"{name}: the files it is spread across were found",
          len(FILES[name]) > 0, True)


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
        node, fsrc = _find(name, fn)
        body = ast.get_source_segment(fsrc, node) if node else ""
        check(f"  {name}.{fn} records who did it, though it is a GET",
              "_audit(" in (body or ""))


# ---------------------------------------------------------------------------
section("A refused change is not recorded as a made one")
# ---------------------------------------------------------------------------
for name, cfg in TRIAGED.items():
    for fn, provider_call in cfg["after_provider"].items():
        node, fsrc = _find(name, fn)
        body = ast.get_source_segment(fsrc, node) if node else ""
        check(f"{name}.{fn} logs only after the provider answered",
              body.index(provider_call) < body.index("_audit("))


# ---------------------------------------------------------------------------
section("The guards in front of those writes are still on all of them")
# ---------------------------------------------------------------------------
for name, cfg in TRIAGED.items():
    if not cfg["guards"]:
        continue
    walk = WALKS[name]
    writes = set(walk["logs"]) | set(walk["silent"])
    dec_guard, body_guard = cfg["guards"]
    missing_dec, missing_body = [], []
    for _path, fsrc in FILES[name]:
      for node in ast.walk(ast.parse(fsrc)):
        if not isinstance(node, ast.FunctionDef) or node.name not in writes:
            continue
        if node.name in cfg["guard_exempt"]:
            continue
        decs = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
        body = ast.get_source_segment(fsrc, node) or ""
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

# A wrapper is resolved from its DEFINITION, not guessed from its name.
# `_audit` and `log` were hard-coded, and modules/seo_images calls its wrapper
# `_log` -- so a module recording five of its seven writes read as recording
# none. A check that invents findings is switched off faster than one that
# misses them, which is the note QR_CODE_RULES already makes.
_ODDLY_NAMED = """
from hub import audit as hub_audit

def _log(event, **extra):
    hub_audit.log("mod", event, **extra)

@app.post("/rename")
def rename_project(pid):
    client.rename(pid)
    _log("renamed", project=pid)
    return "ok"
"""
check("a wrapper called something else is still a call",
      "rename_project" in audit.write_route_attribution(_ODDLY_NAMED)["logs"])
_UNWRAPPED = _ODDLY_NAMED.replace('    _log("renamed", project=pid)\n', "")
check("and without it the same route is named",
      "rename_project" in audit.write_route_attribution(_UNWRAPPED)["silent"])

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
    # Two spellings, both correct: a bound logger, or the shared one called
    # with the module name. Asserting only the first is the kind of narrowness
    # that made the walk miss `_log` in the first place.
    # Every file of the module, because one that binds the logger and one
    # that does not are both ordinary — what must be true is that the module
    # reaches the shared log under its own name from somewhere.
    joined = "\n".join(fsrc for _p, fsrc in FILES[name])
    check(f"{name} reaches the shared log under its own name",
          f'for_module("{name}")' in joined or f'log("{name}"' in joined)
    # A module that is already a name the client record can place must stay
    # one. Whether a module *should* file client work is not this file's
    # question -- check_work_kinds() asks that, from the call sites.
    if name in client_brand.WORK_KINDS or name in getattr(client_brand, "NOT_WORK", {}):
        check(f"  and {name} is still a name the client record can place", True)
    else:
        check(f"  {name} files no client work, so it needs no entry",
              name not in client_brand.WORK_KINDS)

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
