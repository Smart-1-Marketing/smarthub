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
import shutil
import sys
import tempfile

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
    # --- a named reading of a table this module owns ------------------------
    # Each is one expression over a constant in the same file. Kept because
    # the alternative is the next screen reading the table with a literal, and
    # a literal is what drifts; deleting them buys nothing and loses the name.
    "hub/audit.py:registered_modules": "names _REGISTERED rather than exposing it",
    "hub/social_content.py:post_status_label": "names POST_STATUSES' label",
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

}


def public_functions(root=None):
    """Every undecorated public function, by "path:name".

    Undecorated on purpose: a route, a property or a CLI command is called by
    its framework and naming it nowhere is normal. What is left is the code
    this repo calls itself, or does not.
    """
    root = root or ROOT
    out = collections.defaultdict(list)
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
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
            out[node.name].append((rel.as_posix(), node.lineno))
    return out


def _token_counts(root=None):
    """Every identifier-shaped word in the repo, counted once.

    One pass over the bytes rather than one substring scan per name per file:
    the naive shape is 1,700 names against 1,500 files and takes the best part
    of a minute, which is a check somebody drops from CI. Counting whole words
    is also the more honest question -- `foo` appearing inside `foobar` was
    never a reference to `foo`.
    """
    root = root or ROOT
    counts = collections.Counter()
    here = pathlib.Path(__file__).resolve()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
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


def unwired(root=None):
    """Public functions named nowhere but their own definition.

    Textual rather than a call graph, and deliberately so: this repo reaches
    functions from Jinja, from JavaScript, from an entry in a table and
    through getattr, and a call-graph walk would report every one of those as
    unwired. A name that appears nowhere else in any file is the only claim
    that survives all four.
    """
    defs = public_functions(root)
    seen = _token_counts(root)
    out = {}
    for name, sites in defs.items():
        if seen[name] <= len(sites):
            for path, line in sites:
                out[f"{path}:{name}"] = line
    return out



# =====================================================================
# The sibling question: called ONLY by its own tests
# =====================================================================
# `ALLOW` above is for a function named nowhere else in the repo at all. This
# is the other shape, the one `integrity.check_tested_but_unwired()` asks
# about: a function whose only callers are its own tests, which `unwired()`
# cannot see because a test file is part of the repo and five calls from
# `test_x.py` read as thoroughly wired.
#
# It is a SECOND dict rather than more entries in the first, and that is the
# whole reason this backlog sat at twenty-two: the integrity check's own fix
# text said to add these to `ALLOW`, and doing so turns the stale half of THIS
# file red -- every one of them is referenced, by its test, so `ALLOW` counts
# it as an entry that has outlived what it exempted. One list answering two
# questions cannot be stale-checked on either, which is the trap this repo
# names about two checks asking one question and answering it differently.
#
# Held to the same rule in both directions: an entry naming a function that is
# gone, that nothing calls at all (that is `ALLOW`'s question), or that a real
# caller has since reached, fails below.
TEST_ONLY_ALLOW = {
    # --- a named reading of a table this module owns ------------------------
    # One expression over a constant in the same file. Kept because the
    # alternative is the next screen reading the table with a literal, and a
    # literal is what drifts; deleting them buys nothing and loses the name.
    "hub/industry.py:alias_label":
        "names ALIAS_LABELS for the alias that decided the key",
    "modules/commercial_builder/config.py:publisher_labels":
        "names CTV_PUBLISHERS' labels",
    "modules/scans/leads.py:defaults_for":
        "names KINDS' defaults for one widget kind",
    "hub/seo.py:social_label":
        "names SOCIAL_LABELS first and the client's own saved label second -- "
        "the ORDER is the rule, and a screen reading the two dicts itself is "
        "how they come to be read in the other one",
    "hub/seo.py:custom_social_key":
        "the one rule for minting a custom social key: the slug, the first "
        "character, and refusing one the catalog already owns. A second "
        "screen writing that regex is a key the catalog silently shadows",
    "hub/knack_websites.py:client_for_domain":
        "names record_for_domain()'s client half, including that a row "
        "carrying the domain and nobody's name is an ORPHAN rather than a "
        "match -- the distinction a caller reading the record itself drops",
    "modules/image_creator/animation.py:frame_times":
        "names frame_count() as the timestamps to capture at; the browser "
        "decides its own today and reads FRAME_INTERVAL_MS for it",

    # --- a store's own vocabulary, complete on purpose ----------------------
    # A store that can write but not delete is a store whose next caller
    # writes the delete itself, somewhere else, differently.
    "hub/master_identity.py:add_relationship":
        "master identity store: attaching a durable relationship. The store "
        "can mint and merge an entity; one that cannot say how two of them "
        "are related is one whose next caller invents its own join",
    "hub/master_identity.py:add_role": "master identity store: the role add",
    "hub/master_links.py:for_master":
        "master link store: every link for one id",
    "modules/page_image_optimizer/store.py:drop_job":
        "job store: the delete. sweep() drops what is older than the TTL, "
        "which is a different question from dropping one job by id",

    # --- a harness, and it says so ------------------------------------------
    # A test file gets a clean start by pinning its own database, which on
    # SQLite is a fresh file per run and on a shared Postgres is not. Something
    # has to empty the table between files, or every check after the first
    # reads the rows the run before it invented. Production must never call
    # these, so "only its tests call it" is the correct state, not a finding.
    "hub/lead_store.py:drop_for_tests": "test harness: empties the lead table",
    "modules/page_image_optimizer/bytes_store.py:drop_table_for_tests":
        "test harness: empties the bytes table",

    # --- a hook the framework calls, not our code ---------------------------
    # An override of a stdlib base-class method is reached by the framework
    # through the instance, so no call site here names it and none should.
    # Named rather than taught to the check: a rule that skipped every method
    # would blind it to a real finding on any class we own.
    "ui_check.py:handle_error":
        "overrides socketserver's own hook so a dropped preview connection is "
        "not a traceback on the console",

    # --- the one place a browser mirror can be checked ----------------------
    # Its own docstring makes this argument: `campaign_cost()` only ever reads
    # item["dollars"] as saved by the browser, so nothing server-side prices a
    # fresh consulting line -- and without this there is nothing to hold the
    # wizard's `consultingPrice()` against. The test IS the caller, on purpose.
    "modules/sales_builder/app.py:consulting_price":
        "the formula the plan editor's mirror is checked against",

    # --- computed here, for a screen nobody has built -----------------------
    # None is a second reading of something live, which is the distinction
    # that matters: `clients_missing` asks the INDEX who has no GA4 where
    # `qa._google_coverage()` asks what we have RECORDED, and recorded and
    # observed are different claims -- so pointing the no-analytics report at
    # it would change what that report measures rather than make it cheaper.
    "hub/google_index.py:clients_missing":
        "who has nothing on one platform, from the stored index",
    "hub/proposal_spec.py:audience_segments_for":
        "the per-industry segment shortlist; no section renders one today",
    "hub/schema_prefill.py:build_localbusiness_jsonld":
        "a minimal LocalBusiness node from saved fields only -- its docstring "
        "named two callers that do not exist and now says so",
}


section("Nothing is defined and left uncalled without a reason on it")

FOUND = unwired()
print(f"  ({len(FOUND)} unreferenced, {len(ALLOW)} allowed)")

_new = sorted(k for k in FOUND if k not in ALLOW)
check("no unreferenced function without an entry saying why", _new, [])

# An exemption that outlives what it exempted goes on covering whatever is
# written at that path next -- check_stale_json_exemptions()'s rule.
_gone = sorted(k for k in ALLOW if k not in FOUND)
check("no entry naming a function that is called, or gone", _gone, [])


section("...and the sibling list, held to the same rule on its own question")

# `check_tested_but_unwired()` reads TEST_ONLY_ALLOW, so asking it with the
# list in place only ever answers zero. It is asked with the list EMPTIED
# instead -- the raw set of functions only their tests call -- because the
# question worth asking of an exemption is whether the thing it exempts is
# still there, and an entry checked against a list that contains it is the
# assertion that cannot fail this repo names a dozen of.
import hub.integrity as _integ                                  # noqa: E402

_real_allow = _integ._test_only_allow
_integ._test_only_allow = lambda: set()                         # noqa: E731
try:
    _RAW = {f"{r['file']}:{(r['detail'] or '').split('(')[0]}"
            for r in _integ.check_tested_but_unwired()}
finally:
    _integ._test_only_allow = _real_allow

print(f"  ({len(_RAW)} called only by their tests, "
      f"{len(TEST_ONLY_ALLOW)} allowed)")

_stale = sorted(k for k in TEST_ONLY_ALLOW if k not in _RAW)
check("no entry naming a function that is wired, unreferenced, or gone",
      _stale, [])
_unlisted = sorted(k for k in _RAW if k not in TEST_ONLY_ALLOW)
check("and nothing called only by its tests without an entry saying why",
      _unlisted, [])

# The two lists answer two questions and must not both claim one function:
# an entry in each would leave whichever check ran second reporting nothing
# while the other reported it, which is one function described two ways.
check("no function is in both lists",
      sorted(set(ALLOW) & set(TEST_ONLY_ALLOW)), [])
check("every sibling entry says something",
      sorted(k for k, v in TEST_ONLY_ALLOW.items() if not str(v or "").strip()),
      [])

# Every reason is a reason, not a shrug.
_thin = sorted(k for k, v in ALLOW.items() if len(v.strip()) < 12)
check("every entry says something", _thin, [])


section("...and the check bites")

# A check that can be silenced by an edit somewhere else is worse than no
# check, so it is handed a function that is plainly unreferenced and required
# to say so. It started green, which is the only way it was worth adding.
#
# The tree is a throwaway one rather than a file planted in hub/ and deleted
# again. A probe in the working tree races every concurrent sweep -- a
# `tools/preflight.py` run listed one of these and read it after the delete,
# failing `compile every module` on a file that had never been committed -- and
# a run killed between the write and the `finally` leaves it behind.
_tmp = pathlib.Path(tempfile.mkdtemp(prefix="unwired-probe-"))
try:
    (_tmp / "hub").mkdir()
    (_tmp / "hub" / "probe.py").write_text(
        "def a_function_nothing_anywhere_calls():\n    return 1\n"
        "def one_something_calls():\n    return 2\n", encoding="utf-8")
    (_tmp / "hub" / "caller.py").write_text(
        "from .probe import one_something_calls\n"
        "def go():\n    return one_something_calls()\n", encoding="utf-8")
    _again = unwired(_tmp)
    check("it names a function nothing calls",
          any(k.endswith(":a_function_nothing_anywhere_calls") for k in _again), True)
    check("...and not the one beside it that something does call",
          any(k.endswith(":one_something_calls") for k in _again), False)
finally:
    shutil.rmtree(_tmp, ignore_errors=True)

# Handing the walk a root means the green result above no longer proves the
# default walk reaches this repo at all -- an empty FOUND and a repo with
# nothing unwired in it read identically. So that is asserted.
_defs = public_functions()
check("the default walk reaches hub/ and modules/",
      any(pth.startswith("hub/") for s in _defs.values() for pth, _ in s)
      and any(pth.startswith("modules/") for s in _defs.values() for pth, _ in s),
      True)

# And does not name one that is called, however indirectly.
check("it does not name a function something calls",
      "hub/access.py:is_utility" in FOUND, False)
check("...nor one reached only from a template",
      "hub/sidebar.py:render_sidebar" in FOUND, False)


# ---------------------------------------------------------------------------
section("Called by its own tests, and by nothing else")
# ---------------------------------------------------------------------------
# The shape THIS file cannot see. It counts every identifier-shaped word in the
# repo and a test file is part of the repo, so a function called five times
# from test_x.py and nowhere else reads as thoroughly wired.
#
# hub/keyring.needs_reseal() is what found it: it shipped with a test proving
# it worked and no caller at all, and what it does is FINISH a key rotation --
# without it the old key can never be dropped. This file was green throughout.
# The check lives in hub/integrity.py at low severity, because it went in with
# 21 findings behind it and a check red on the day it is switched on is a check
# people learn to ignore.
from hub import integrity as _integrity                            # noqa: E402

_tbu = _integrity.check_tested_but_unwired()
_tbu_keys = {f"{f['file']}:{f['detail'].split('(')[0]}" for f in _tbu}

check("the allowlist is read from this file, not copied into the checker",
      len(_integrity._unwired_allow()) == len(ALLOW), True)
check("...and it is not silently empty, which would make the check report "
      "everything or nothing",
      bool(_integrity._unwired_allow()), True)

_allowed_reported = [k for k in _tbu_keys if k in ALLOW]
check("nothing ALLOW already accounts for is reported twice",
      _allowed_reported, [])

# needs_reseal is the one this was written for, and it is wired now: get() and
# site_login_state() re-seal through it. If it comes back, the rotation has
# quietly stopped being completable again.
check("hub/keyring.py:needs_reseal is no longer among them — it has a caller",
      "hub/keyring.py:needs_reseal" in _tbu_keys, False)

# An empty answer and a scan that stopped running read the same, so the check
# is shown finding something rather than trusted to be looking. Asked of the
# RAW set -- the check with its allowlist emptied -- because the live answer
# is zero now and this assertion would otherwise be encoding a backlog: true
# only while one existed, and failing on the day somebody finished it, which
# is the assertion that punishes the fix.
check("the check still finds the shape it is for", bool(_RAW), True)
check("...and finds nothing once the reasons are read",
      sorted(_tbu_keys), [])
check("and every finding says what a green test over it does not prove",
      all("proves the function works" in f["detail"] for f in _tbu), True)
check("and offers the allowlist as one of the three ways out",
      all("test_unwired.ALLOW" in f["fix"] for f in _tbu), True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
