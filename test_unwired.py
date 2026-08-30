"""Functions this repo defines and never calls.

    python3 test_unwired.py

Same shape as the other test files: no pytest, no new dependencies, and it
reads the sources rather than booting anything.

## Why this file exists

Declared and never wired is the single failure this codebase has paid for most
often, and there was no check for it. Every one of these is written down in
CLAUDE.md, and each cost a feature that looked complete from every screen:

  * **Page Image Optimizer** shipped an ">>> INTEGRATION POINT <<<" naming
    three candidate writers, not one of which has ever existed -- so every
    image it saved went to a private JSON file nothing reads, while
    `archive_backend()` reported *local* to a screen nobody read it on.
  * **`hub/storage.manifest()`** had no caller at all. Its docstring says it
    "feeds the orphaned-asset audit", and that audit had never been built.
  * **`io_creative`** sat in `filing.KIND_LABELS` with no writer.
  * **`simvoly_client.check_limits()`** had been written and had no caller,
    while the page that would have used it 500'd on every visit.
  * **`TICKET_CREATE_FIELDS`, `TICKET_MANAGE_FIELDS` and `update_ticket()`**
    existed with no caller, so the Hub wrote four of a ticket's eight fields.
  * **`openai_service.write_runway_prompt()`** sat written and uncalled until
    the button that needed it was built.

None of them errored. That is the whole difficulty: an uncalled function is
indistinguishable from a working one until somebody goes looking for the
feature it was supposed to be half of.

## What it does not claim

Not every unreferenced function is a defect. A thin client over somebody
else's API is reasonably kept whole -- `check_limits()` above is the proof, in
both directions: it was unwired *and* it was needed. So this is an
**allowlist** rather than a rule, and the allowlist is the point: every
survivor carries the reason it is allowed to survive, which is what makes the
next one somebody adds a decision rather than an accident.

Held to the discipline `check_stale_json_exemptions()` works to: an entry
naming a function that is gone, or one that something now calls, **fails**.
An exemption that outlives what it exempted goes on covering whatever is
written at that path next.
"""
import ast
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

SKIP_DIRS = {"__pycache__", "_attic", ".git", "node_modules", "dist", "build",
             "venv", ".venv"}

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


# Why each survivor survives. Keyed on "path:function", because two modules
# may reasonably define a function of one name and only one of them be
# unwired -- the per-file-and-per-word shape tools/spellcheck.py's ALLOW uses.
ALLOW = {
    # --- a thin client over somebody else's API -----------------------------
    # The surface is kept whole so the next call site reads a named method
    # rather than re-deriving a signature against live Simvoly. This is the
    # bucket check_limits() came out of, which is the argument in both
    # directions: it was unwired, and then it was exactly what was needed.
    "modules/sites_admin/simvoly_client.py:create_user": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:search_user": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:get_user": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:delete_user": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:add_website": "Simvoly API surface",
    # Found only once the scan counted whole words: `assign_customer` was
    # "referenced" by `unassign_customer` containing it, which is the
    # substring trap this repo names about `.btn` matching `subtle`.
    "modules/sites_admin/simvoly_client.py:assign_customer": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:unassign_customer": "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:activate_project_for_period":
        "Simvoly API surface",
    "modules/sites_admin/simvoly_client.py:set_addon": "Simvoly API surface",
    "modules/scans/insites_client.py:fetch_llm_report":
        "Insites API surface -- the narrative payload, for a caller that does "
        "not exist yet",

    # --- a named reading of a table this module owns ------------------------
    # Each is one expression over a constant in the same file. Kept because
    # the alternative is the next screen reading the table with a literal, and
    # a literal is what drifts; deleting them buys nothing and loses the name.
    "hub/audit.py:registered_modules": "names _REGISTERED rather than exposing it",
    "hub/social_content.py:post_status_label": "names POST_STATUSES' label",
    "hub/voice_casting.py:style_for": "names STYLE_BY_ENERGY's default",
    "hub/product_intake.py:as_consulting": "names the rate card's catch-all line",
    "hub/target_areas.py:density_table":
        "the density assumption itself, for the wizard's mirror and the help text",
    "hub/stock_search.py:any_source_configured": "names configured_sources()'s any()",
    "modules/image_creator/photo_search.py:any_configured": "the same, per module",
    "modules/scans/audit_fields.py:field_label_map": "names the field dictionary",
    "modules/sites_admin/pricing.py:wholesale_cost": "names the plan cost table",
    "modules/sites_admin/pricing.py:default_retail_price": "names the plan price table",
    "hub/sidebar.py:render_footer": "names FOOTER_HTML",
    "hub/clients_registry.py:is_seo_client": "names find_client()'s seo flag",
    "modules/commercial_builder/services/qrcode_service.py:is_available":
        "names whether the qrcode package imported. generate_qr() returns the "
        "reason with the result, which is the reading the CTA panel uses -- a "
        "separate pre-check would be a second answer to one question",

    # --- needs a credential this deployment does not hold -------------------
    "modules/google_access/google_client.py:verify_ga4":
        "confirming a GA4 binding still stands needs an agency GA4 token, and "
        "the screen that would check one is not built. Named rather than "
        "deleted for the reason Google Ads is PARKED rather than removed",

    # --- a store's own vocabulary, complete on purpose ----------------------
    # A store that can write but not delete is a store whose next caller
    # writes the delete itself, somewhere else, differently.
    "modules/ads_builder/store.py:latest_share": "share store: the newest live row",
    "modules/page_image_optimizer/store.py:drop_job": "job store: the delete",
    "modules/proposal_builder/store.py:save_proposal":
        "the retired builder's archive is read-only today; its writer is kept "
        "beside the reader rather than half a store being left behind",
    "hub/clients_registry.py:update_house_client": "house client store: the update",

    # --- computed and reported as a count rather than a list ----------------
    "hub/target_areas.py:dropped_zips":
        "what a ZIP exception removed. zip_exceptions() reports the count, "
        "which is what the screens and the client document show; the list "
        "itself is here for a screen that wants to name them",
    "hub/current_marketing.py:gaps_named":
        "the discovery gaps with their labels; suggestions() is what the "
        "builder renders",
    "modules/tickets/reports.py:summary_counts":
        "one line per ticket report; the /qa index draws its own counts",
    "hub/quickbooks.py:link_status":
        "how much of the invoice cache has a public link -- written for a "
        "Diagnostics row that has not been added",

    # --- a half that is deliberately switched off ---------------------------
    # Google sign-in stays off until the OAuth consent screen clears review
    # (CLAUDE.md says so at the end of the accounts section). Both routes
    # resolve to the same account row, so nothing here has to change when it
    # lands -- which is only true if this half is still here.
    "hub/identity.py:demo_enabled": "Google sign-in, pending consent review",
    "hub/identity.py:password_login": "Google sign-in, pending consent review",
    "hub/identity.py:state_ok": "Google sign-in, pending consent review",
    "hub/demo.py:sample_scan": "demo data, for a walkthrough on an empty deploy",
    "hub/demo.py:sample_photos": "demo data, for a walkthrough on an empty deploy",
    "hub/demo.py:sample_ai_names": "demo data, for a walkthrough on an empty deploy",
    "hub/demo.py:sample_billing": "demo data, for a walkthrough on an empty deploy",

    # --- named, because it promises something it cannot currently do --------
    "modules/sites_admin/seed_boot.py:seed_if_empty":
        "REPAIR THIS OR REMOVE IT. Its docstring says a freshly-recreated "
        "database 'repopulates itself on the next startup', and it is imported "
        "by nothing and there is no seed/portfolio.json for it to read, so it "
        "has never run and could not. Wiring it means a database write at boot "
        "in two gunicorn workers and a seed file exported from the live "
        "portfolio; neither is a decision this file should make quietly",
}


def public_functions():
    """Every undecorated public function, by "path:name".

    Undecorated on purpose: a route, a property or a CLI command is called by
    its framework and naming it nowhere is normal. What is left is the code
    this repo calls itself, or does not.
    """
    out = collections.defaultdict(list)
    for p in sorted(ROOT.rglob("*.py")):
        rel = p.relative_to(ROOT)
        if any(d in rel.parts for d in SKIP_DIRS):
            continue
        # The checks and the tools are entry points; nothing calls them either.
        if rel.name.startswith("test_") or rel.parts[0] == "tools":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or node.decorator_list:
                continue
            out[node.name].append((str(rel), node.lineno))
    return out


def _token_counts():
    """Every identifier-shaped word in the repo, counted once.

    One pass over the bytes rather than one substring scan per name per file:
    the naive shape is 1,700 names against 1,500 files and takes the best part
    of a minute, which is a check somebody drops from CI. Counting whole words
    is also the more honest question -- `foo` appearing inside `foobar` was
    never a reference to `foo`.
    """
    counts = collections.Counter()
    here = pathlib.Path(__file__).resolve()
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or any(d in rel.parts for d in SKIP_DIRS):
            continue
        # Code and templates only. **Prose is not a call site** -- the rule
        # hub/config.py's drift check works to, inverted: there, matching text
        # reported a docstring explaining the fix as the defect; here, a
        # paragraph in CLAUDE.md naming `assign_customer` to explain why it is
        # allowed counted as somebody calling it, and silenced the finding it
        # was written about. A function is not reached from a .md file.
        if p.suffix not in (".py", ".html", ".js", ".ts", ".json",
                            ".yml", ".yaml"):
            continue
        # Not this file. ALLOW names every survivor as a string, so counting
        # it as a reference makes the check exempt its own allowlist and
        # report a clean nothing -- which is exactly how
        # unmirrored_json_writers() came to exempt each scanner by accident,
        # its test being `"jsonstore" not in src` while every one of them
        # explained jsonstore in its own prose.
        if p.resolve() == here:
            continue
        try:
            counts.update(_WORD.findall(p.read_text(encoding="utf-8",
                                                    errors="ignore")))
        except OSError:
            continue
    return counts


def unwired():
    """Public functions named nowhere but their own definition.

    Textual rather than a call graph, and deliberately so: this repo reaches
    functions from Jinja, from JavaScript, from an entry in a table and
    through getattr, and a call-graph walk would report every one of those as
    unwired. A name that appears nowhere else in any file is the only claim
    that survives all four.
    """
    defs = public_functions()
    seen = _token_counts()
    out = {}
    for name, sites in defs.items():
        if seen[name] <= len(sites):
            for path, line in sites:
                out[f"{path}:{name}"] = line
    return out


section("Nothing is defined and left uncalled without a reason on it")

FOUND = unwired()
print(f"  ({len(FOUND)} unreferenced, {len(ALLOW)} allowed)")

_new = sorted(k for k in FOUND if k not in ALLOW)
check("no unreferenced function without an entry saying why", _new, [])

# An exemption that outlives what it exempted goes on covering whatever is
# written at that path next -- check_stale_json_exemptions()'s rule.
_gone = sorted(k for k in ALLOW if k not in FOUND)
check("no entry naming a function that is called, or gone", _gone, [])

# Every reason is a reason, not a shrug.
_thin = sorted(k for k, v in ALLOW.items() if len(v.strip()) < 12)
check("every entry says something", _thin, [])


section("...and the check bites")

# A check that can be silenced by an edit somewhere else is worse than no
# check, so it is handed a function that is plainly unreferenced and required
# to say so. It started green, which is the only way it was worth adding.
_probe = ROOT / "hub" / "_unwired_probe.py"
try:
    _probe.write_text(
        "def a_function_nothing_anywhere_calls():\n    return 1\n", encoding="utf-8")
    _again = unwired()
    check("it names a function nothing calls",
          any(k.endswith(":a_function_nothing_anywhere_calls") for k in _again), True)
finally:
    _probe.unlink(missing_ok=True)

# And does not name one that is called, however indirectly.
check("it does not name a function something calls",
      "hub/access.py:is_utility" in FOUND, False)
check("...nor one reached only from a template",
      "hub/sidebar.py:render_sidebar" in FOUND, False)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
