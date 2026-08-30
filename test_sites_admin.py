"""Sites Admin: every write on a client's website has a name against it.

    python3 test_sites_admin.py

No pytest, no new dependencies, and nothing here reaches Simvoly: the checks
are over the module's own source and its declared exemptions.

## Why this file exists

CLAUDE.md records that `_audit` was bound at the top of this module and called
nowhere, so **deleting a client's live website and connecting a domain were
among the least attributable actions in the Hub** — behind an
`/api/integrity` check reporting them clean.

The sweep that fixed that wired four call sites and stopped. Four more write
routes were still silent, and every one of them changes something a client
would notice:

  * **`add_site`** creates a client's website and can activate a paid plan.
    Its opposite, `delete_website`, has been logged since that fix, so the
    pair was asymmetric: destroying a site was attributable and making one
    was not.
  * **`personalization`** writes brand colors and tags onto their live pages.
  * **`pricing`** sets `client_price` — what they are billed — and
    `internal_client_name`, which is the join `hub/domain_links.py` writes and
    every domain-keyed report reads. Changing it moves a website onto a
    different client's record.
  * **`project_sso`** mints a builder session into the site. Not a change, but
    it is the door the changes are made through, and an unexplained edit to a
    client's pages is answerable only if somebody can say who was let in.

Nothing could see any of it. `test_activity_logging.py` asks whether a module
logs **at all**, and one call site satisfies that — which is the same shape as
the check that read the *string* `for_module(` and counted the binding.

So this asks the finer question of every write route in the module, and the
remainder is declared in `HOUSEKEEPING_ROUTES` with its reason rather than
left as an absence.
"""
import ast
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


SRC = (ROOT / "modules" / "sites_admin" / "app.py").read_text()
TREE = ast.parse(SRC)


def _routes(tree=None, src=None):
    """Every route function, with its methods and whether it logs.

    Read through the AST rather than by matching text: this module's own
    comments name `_audit` while explaining why it went uncalled, and a check
    that matches the explanation reports the fix as the defect — the rule
    `hub/config.py`'s drift check gives at length.
    """
    out = {}
    for node in ast.walk(tree if tree is not None else TREE):
        if not isinstance(node, ast.FunctionDef):
            continue
        methods, is_route = [], False
        for d in node.decorator_list:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)):
                continue
            if d.func.attr in ("get", "post", "delete", "put"):
                is_route = True
                methods.append(d.func.attr.upper())
            elif d.func.attr == "route":
                is_route = True
                for kw in d.keywords:
                    if kw.arg == "methods":
                        methods += [e.value for e in kw.value.elts]
                if not any(kw.arg == "methods" for kw in d.keywords):
                    methods.append("GET")
        if not is_route:
            continue
        logs = any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                   and c.func.id == "_audit" for c in ast.walk(node))
        out[node.name] = {"methods": {m.upper() for m in methods}, "logs": logs}
    return out


ROUTES = _routes()
WRITES = {n: r for n, r in ROUTES.items()
          if r["methods"] & {"POST", "PUT", "DELETE"}}


# ---------------------------------------------------------------------------
section("The module was read at all")
# ---------------------------------------------------------------------------
# Finding no routes is a failure, not a clean sweep: a walk that quietly stops
# walking reports a clean bill of health about nothing.
check("the route table was read", len(ROUTES) > 15, True)
check("and the write routes with it", len(WRITES) > 10, True)
check("including the four that were silent",
      sorted(n for n in ("add_site", "personalization", "pricing", "project_sso")
             if n in WRITES),
      ["add_site", "personalization", "pricing", "project_sso"])


# ---------------------------------------------------------------------------
section("Every write is attributable, or declared as housekeeping")
# ---------------------------------------------------------------------------
# Read from the source rather than by importing the module: Sites Admin needs
# a real Postgres and its own directory on sys.path before it will import at
# all, and a check that cannot run without those is one that quietly stops
# running. The declaration is a literal dict, so the AST has it exactly.
EXEMPT = next(
    ({k.value: v.value for k, v in zip(node.value.keys, node.value.values)}
     for node in ast.walk(TREE)
     if isinstance(node, ast.Assign)
     and any(getattr(t, "id", "") == "HOUSEKEEPING_ROUTES" for t in node.targets)),
    None)
check("the module declares which writes deliberately record nothing",
      isinstance(EXEMPT, dict) and len(EXEMPT) > 0, True)
silent = sorted(n for n, r in WRITES.items() if not r["logs"] and n not in EXEMPT)
check("no write route is silent without a reason written down", silent, [])

for name in ("add_site", "personalization", "pricing", "project_sso",
             "delete_website", "connect_domain", "disconnect_domain",
             "project_action"):
    check(f"  {name} records who did it", WRITES[name]["logs"])

check("every housekeeping entry carries its reason",
      sorted(k for k, v in EXEMPT.items() if not str(v).strip()), [])

# An exemption that outlives what it exempted goes on covering whatever is
# written at that name next -- check_stale_json_exemptions()'s rule.
gone = sorted(n for n in EXEMPT if n not in WRITES)
check("no exemption names a route that no longer exists", gone, [])
now_logs = sorted(n for n in EXEMPT if WRITES.get(n, {}).get("logs"))
check("and none names a route that has since started logging", now_logs, [])


# ---------------------------------------------------------------------------
section("A refused change is not recorded as a made one")
# ---------------------------------------------------------------------------
# Every one of these logs *after* the Simvoly call returned, inside the try,
# which is the shape the module's own comment on project_action describes and
# the shape `approve_render` uses in the Commercial Builder.
for name, provider_call in (("add_site", "client.create_project_website("),
                            ("personalization", "client.set_personalization_tags("),
                            ("project_sso", "client.start_building_session("),
                            ("delete_website", "client.set_website_status(")):
    node = next(n for n in ast.walk(TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    body = ast.get_source_segment(SRC, node) or ""
    check(f"{name} logs only after the provider answered",
          body.index(provider_call) < body.index("_audit("))


# ---------------------------------------------------------------------------
section("The guards the writes sit behind are still on all of them")
# ---------------------------------------------------------------------------
def _guarded(name):
    node = next(n for n in ast.walk(TREE)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    decs = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
    body = ast.get_source_segment(SRC, node) or ""
    return ("login_required" in decs), ("require_csrf()" in body)


unguarded = sorted(n for n in WRITES if n != "login" and not _guarded(n)[0])
check("every write route is behind the login", unguarded, [])
no_csrf = sorted(n for n in WRITES if n != "login" and not _guarded(n)[1])
check("and every one checks its CSRF token", no_csrf, [])


# ---------------------------------------------------------------------------
section("The module still logs under the name the client record can read")
# ---------------------------------------------------------------------------
from hub import client_brand                                        # noqa: E402

check("sites_admin binds the shared logger", 'for_module("sites_admin")' in SRC)
check("and that name is one the Hub can place",
      "sites_admin" in client_brand.WORK_KINDS
      or "sites_admin" in getattr(client_brand, "NOT_WORK", {}))


# ---------------------------------------------------------------------------
section("And the check bites when a write route goes silent")
# ---------------------------------------------------------------------------
# A check that has only ever been green is one nobody can trust. Handed a
# module with a write route that records nothing and is in no exemption list,
# it must name it -- and handed the same route with the call, it must not.
_SILENT = """
HOUSEKEEPING_ROUTES = {"sync": "reads only"}

@app.post("/projects/<pid>/rename")
@login_required
def rename_project(pid):
    require_csrf()
    client.rename(pid, request.form.get("name"))
    return "ok"

@app.post("/sync")
@login_required
def sync():
    require_csrf()
    return "ok"
"""
_probe = _routes(ast.parse(_SILENT))
_probe_writes = {n: r for n, r in _probe.items()
                 if r["methods"] & {"POST", "PUT", "DELETE"}}
_probe_silent = sorted(n for n, r in _probe_writes.items()
                       if not r["logs"] and n != "sync")
check("a new write route that logs nothing is named", _probe_silent,
      ["rename_project"])

_LOUD = _SILENT.replace('client.rename(pid, request.form.get("name"))',
                        'client.rename(pid, request.form.get("name"))\n'
                        '    _audit("project_renamed", project=pid)')
_probe2 = _routes(ast.parse(_LOUD))
check("and the same route with the call is not",
      _probe2["rename_project"]["logs"], True)
# Prose is not a call site: this module explains at length why _audit went
# uncalled, and a check matching text reports the explanation as the defect.
_PROSE = _SILENT.replace("    client.rename",
                         '    """_audit was bound and called nowhere."""\n    client.rename')
check("a docstring naming _audit does not count as calling it",
      _routes(ast.parse(_PROSE))["rename_project"]["logs"], False)

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
