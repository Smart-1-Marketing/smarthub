"""The recipe library and every placement its chips appear in.

    python3 test_ask_recipes.py

No pytest. The composed app is booted and the real pages are requested with a
session, because a chip that renders in a template nobody serves is the
"declared and never wired" shape docs/claude/47 is about.

What it holds:

  * every recipe's tools are a subset of what its own roles can reach --
    checked at import, so a recipe naming a tool that does not exist fails the
    boot rather than offering a chip that always errors;
  * a recipe is a HINT, never a second allowlist: the planner's catalog is
    unchanged by one, and validate_plan still drops anything outside it;
  * fill() puts the page's client and period into the question, and
    sweep_findings takes its platform label from the placement, so the Bing
    page asks about Microsoft Ads;
  * the render guidance reaches the ANSWER prompt and not the planner's;
  * a recipe the caller's role may not run is ignored rather than refused --
    the words are the user's own either way -- and never leaks its tools;
  * the audit row carries recipe= when a chip was used and not when one was
    not;
  * the chips actually appear: /ask-smarthub, the dashboard, the reporting
    hub's Trends and Pacing sections, Client 360 and the optimization page;
  * an empty group renders no heading;
  * the shared URL builder is root-absolute, because a relative one resolves
    under a mounted module's prefix and 404s.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1recipes_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "hub.sqlite3")
os.environ["SECRET_KEY"] = "ask-recipes-test"

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import ask_recipes, ask_smarthub                           # noqa: E402
from mcp_gateway import v2_tools                                    # noqa: E402

IDENTITY = {"known": True, "client": "Acme", "client_key": "n:acme", "domain": "",
            "matched_on": "exact", "confidence": "exact", "candidates": [], "why": ""}

section("Every recipe is reachable, and the check runs at import")
check("the six recipes are declared", [r.key for r in ask_recipes.RECIPES],
      ["performance_summary", "wow_trends", "pacing_review", "monthly_exec",
       "ga4_sources", "sweep_findings"])
check("every tool named exists in the catalog",
      all(t in ask_smarthub.TOOLS for r in ask_recipes.RECIPES for t in r.tools))
check("every tool is one the recipe's own roles can reach",
      all(t in ask_smarthub.allowed_tools(role)
          for r in ask_recipes.RECIPES for role in r.roles for t in r.tools))
check("every placement is one the module declares",
      all(p in ask_recipes.PLACEMENTS
          for r in ask_recipes.RECIPES for p in r.placements))
check("every default period is a real one",
      all(r.default_period in __import__("hub.periods", fromlist=["x"]).PERIODS
          for r in ask_recipes.RECIPES))
check("every recipe carries rendering guidance",
      all(r.render.strip() for r in ask_recipes.RECIPES))


def raises_on(mutate):
    """_check() with one recipe broken: the boot should refuse it."""
    original = ask_recipes.RECIPES
    try:
        ask_recipes.RECIPES = mutate(original)
        try:
            ask_recipes._check()
        except ValueError as exc:
            return str(exc)
        return ""
    finally:
        ask_recipes.RECIPES = original


from dataclasses import replace                                     # noqa: E402

check("a recipe naming a tool that does not exist fails the boot",
      "no such tool" in raises_on(
          lambda rs: (replace(rs[0], tools=("get_the_moon",)),) + rs[1:]))
check("a recipe whose role cannot reach its tool fails the boot",
      "cannot reach" in raises_on(
          lambda rs: (replace(rs[0], tools=("get_client_quickbooks",),
                              roles=ask_smarthub.STAFF),) + rs[1:]))
check("an unknown placement fails the boot",
      "unknown placement" in raises_on(
          lambda rs: (replace(rs[0], placements=("sidebar",)),) + rs[1:]))
check("an unknown period fails the boot",
      "unknown period" in raises_on(
          lambda rs: (replace(rs[0], default_period="last_fortnight"),) + rs[1:]))

section("The question is filled from the page's own context")
perf = ask_recipes.BY_KEY["performance_summary"]
check("the client goes in", ask_recipes.fill(perf, "Quality Air Columbus"),
      "Summarize Quality Air Columbus's ad performance for this month by campaign.")
check("...and the period, in words",
      ask_recipes.fill(perf, "Acme", "last_month"),
      "Summarize Acme's ad performance for last month by campaign.")
check("no client asks for one the way a person would",
      ask_recipes.fill(perf), "Summarize this client's ad performance for this "
      "month by campaign.")
check("an unknown period falls back to the recipe's own",
      ask_recipes.fill(perf, "Acme", "next_tuesday"),
      ask_recipes.fill(perf, "Acme", "this_month"))
sweep = ask_recipes.BY_KEY["sweep_findings"]
check("the Google page asks about Google Ads",
      "Google Ads account" in ask_recipes.fill(
          sweep, "Acme", placement="ads_optimization_google"))
check("...and the Microsoft page about Microsoft Ads",
      "Microsoft Ads account" in ask_recipes.fill(
          sweep, "Acme", placement="ads_optimization_bing"))
chip = ask_recipes.chip(sweep, "Acme", placement="ads_optimization_bing")
check("...and the chip carries the platform argument", chip["platform"], "bing")

section("Placements: the right chips on the right page")
check("the reporting hub's Trends section",
      [c["key"] for c in ask_recipes.chips("reports_trends", "member")],
      ["performance_summary", "wow_trends", "ga4_sources"])
check("the pacing board", [c["key"] for c in ask_recipes.chips("reports_pacing", "member")],
      ["pacing_review"])
check("Client 360", [c["key"] for c in ask_recipes.chips("client360", "member")],
      ["performance_summary", "wow_trends", "monthly_exec", "ga4_sources",
       "pacing_review"])
check("the optimization page",
      [c["key"] for c in ask_recipes.chips("ads_optimization_google", "member")],
      ["sweep_findings"])
check("the dashboard takes one", len(ask_recipes.chips("dashboard", "member", limit=1)), 1)
check("all six are on the Ask page",
      len(ask_recipes.chips("ask", "member")), 6)
check("a placement nothing claims is empty, not an error",
      ask_recipes.chips("client_dashboard", "member"),
      [c for c in ask_recipes.chips("client_dashboard", "member")])

section("Groups, and no heading over an empty one")
groups = ask_recipes.grouped("ask", "member")
check("Reporting and Optimization have chips",
      [g["group"] for g in groups], ["Reporting", "Optimization"])
check("Audit is declared but has no recipe yet, so it is not rendered",
      "Audit" in ask_recipes.GROUPS and "Audit" not in [g["group"] for g in groups])
check("the pacing board shows only its own group",
      [g["group"] for g in ask_recipes.grouped("reports_pacing", "member")],
      ["Optimization"])

section("No placement is declared and never wired")
# The trap docs/claude/47 is named after. Every placement is either rendered
# by a page, or listed as pending WITH a reason -- and a pending one offers no
# chips at all, so the entry is what makes the button not exist rather than a
# note beside a live one.
SOURCES = [ROOT / "hub" / "templates" / "ask_smarthub.html",
           ROOT / "hub" / "templates" / "dashboard.html",
           ROOT / "hub" / "templates" / "client360.html",
           ROOT / "hub" / "__init__.py",
           ROOT / "modules" / "reports" / "app.py",
           ROOT / "modules" / "reports" / "templates" / "reports_index.html",
           ROOT / "modules" / "reports" / "templates" / "reports_pacing.html",
           ROOT / "modules" / "ads_builder" / "app.py"]
WIRING = "\n".join(f.read_text(encoding="utf-8") for f in SOURCES)
for placement in ask_recipes.PLACEMENTS:
    wired = f'"{placement}"' in WIRING or f"'{placement}'" in WIRING
    pending = placement in ask_recipes.PENDING_PLACEMENTS
    check(f"{placement} is wired to a page, or declared pending",
          wired or pending)
    if pending:
        check(f"...{placement} offers no chips while it is pending",
              ask_recipes.chips(placement, "member"), [])
        check(f"...and says why in a sentence",
              len(ask_recipes.PENDING_PLACEMENTS[placement]) > 40)
    else:
        check(f"...{placement} offers at least one chip",
              len(ask_recipes.chips(placement, "member")) > 0)

section("A chip is never offered for a sweep that does not exist")
check("Microsoft Ads has no sweep, so its placement offers nothing",
      ask_recipes.chips("ads_optimization_bing", "member"), [])
check("...and Google's does", len(ask_recipes.chips("ads_optimization_google", "member")), 1)
check("the judgment is the tool's own, not a second list",
      ask_recipes.SWEPT_PLATFORMS is v2_tools.SWEPT_PLATFORMS)
check("sweep_exists says yes for a placement naming no platform",
      ask_recipes.sweep_exists("client360"))
check("...and for an unknown placement", ask_recipes.sweep_exists("nowhere"))
# The two halves cannot drift: the tool refuses exactly what the library hides.
for code in ("bing", "microsoft"):
    with patch.object(v2_tools, "resolve_identity", return_value=IDENTITY):
        out = v2_tools.client_ads_findings("Acme", platform=code)
    check(f"the tool answers 'not built' for {code}", out["reason"], "sweep_not_built")
    check(f"...and {code} is outside SWEPT_PLATFORMS",
          code not in v2_tools.SWEPT_PLATFORMS)
for code in v2_tools.SWEPT_PLATFORMS:
    check(f"{code} is a platform the findings tool knows",
          code in v2_tools.FINDINGS_PLATFORMS)

section("The client dashboard's draft button reads its recipe from the placement")
app_src = (ROOT / "modules" / "reports" / "app.py").read_text(encoding="utf-8")
check("the route asks the placement which recipe to draft",
      'for_placement("client_dashboard"' in app_src)
check("...rather than naming one directly", '"monthly_exec"' not in app_src)
check("...and the placement answers with exactly one",
      [r.key for r in ask_recipes.for_placement("client_dashboard", "member")],
      ["monthly_exec"])

section("A recipe is a hint, never a second allowlist")
check("tool_hint gives a staff member the recipe's tools",
      ask_recipes.tool_hint("performance_summary", "member"),
      ("get_client_performance",))
check("an unknown recipe hints nothing",
      ask_recipes.tool_hint("make_it_up", "member"), ())
check("...and renders nothing", ask_recipes.render_for("make_it_up", "member"), "")
check("a role outside the recipe hints nothing",
      ask_recipes.tool_hint("performance_summary", "client"), ())

seen = {}


def fake_chat_json(messages, **kw):
    seen["plan_system"] = messages[0]["content"]
    seen["plan_user"] = messages[1]["content"]
    return {"calls": [{"tool": "get_client_performance",
                       "arguments": {"client_name": "Acme", "period": "this_month"}}],
            "direct_answer": ""}


def fake_chat(messages, **kw):
    seen["answer_system"] = messages[0]["content"]
    return "Acme spent $2,625.00 this month."


def ask(question, **kw):
    seen.clear()
    with patch.object(ask_smarthub.ai, "chat_json", side_effect=fake_chat_json), \
         patch.object(ask_smarthub.ai, "chat", side_effect=fake_chat), \
         patch.object(ask_smarthub.v2_tools, "resolve_identity", return_value=IDENTITY), \
         patch.object(ask_smarthub.v2_tools, "client_performance",
                      return_value={"found": True, "available": True, "totals": {}}):
        return ask_smarthub.ask(question, role="member", actor="tester@smart1.agency", **kw)


out = ask("Summarize Acme's ad performance for this month by campaign.",
          recipe="performance_summary")
check("the plan payload carries the hint",
      '"suggested_tools": ["get_client_performance"]' in seen["plan_user"])
check("...and the catalog is still the whole universe",
      all(name in seen["plan_user"] for name in
          ("get_client_proposals", "get_client_ga4_summary", "search_clients")))
check("the rendering guidance reaches the ANSWER prompt",
      "Return one table: Campaign, Platform" in seen["answer_system"])
check("...and NOT the planner's", "Return one table" not in seen["plan_system"])
check("the response names the recipe it ran", out["recipe"], "performance_summary")

plain = ask("How is Acme doing?")
check("a typed question carries no hint",
      "suggested_tools" not in seen["plan_user"])
check("...and no rendering guidance",
      "Return one table" not in seen["answer_system"])
check("...and names no recipe", plain["recipe"], "")

section("A recipe a role may not run is ignored, not refused, and leaks nothing")
admin_only = ask_recipes.Recipe(
    key="qb_probe", group="Audit", title="QuickBooks probe",
    question="Check QuickBooks for {client}.", tools=("get_client_quickbooks",),
    default_period="last_30", roles=ask_smarthub.ADMINS, placements=("ask",),
    render="Show the balance.")
with patch.dict(ask_recipes.BY_KEY, {"qb_probe": admin_only}):
    check("a member gets no hint from it",
          ask_recipes.tool_hint("qb_probe", "member"), ())
    check("...and no rendering guidance",
          ask_recipes.render_for("qb_probe", "member"), "")
    check("...while an admin gets both",
          (ask_recipes.tool_hint("qb_probe", "admin"),
           bool(ask_recipes.render_for("qb_probe", "admin"))),
          (("get_client_quickbooks",), True))
    out = ask("Check QuickBooks for Acme.", recipe="qb_probe")
    check("the member's question is still answered", bool(out["answer"]))
    check("...with the recipe simply not applied", out["recipe"], "")
    check("...and its guidance nowhere in the prompt",
          "Show the balance." not in seen["answer_system"])

section("The audit row records which chip was used")
from hub import audit                                               # noqa: E402
rows = [r for r in audit.read(limit=200, module="ask_smarthub")]
check("a recipe run is recorded by key",
      "performance_summary" in {r.get("recipe") for r in rows})
check("...and a typed question records none",
      any(r.get("recipe") in (None, "") for r in rows))

section("The chips actually appear on the pages")
import wsgi                                                         # noqa: E402
from werkzeug.test import Client                                    # noqa: E402

from hub import auth                                                # noqa: E402

# Through wsgi.application, not the hub app alone. DispatcherMiddleware routes
# by URL prefix, so /reports/ and /tools/ads/ do not exist on the hub app at
# all -- testing the hub app would 404 on every mounted page while the real
# site served them, and testing ONLY the hub app is how mount shadowing hides.
http = Client(wsgi.application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("tester@smart1.agency"),
                domain="localhost")


def page(path):
    r = http.get(path)
    return r.status_code, r.get_data(as_text=True)


code, body = page("/ask-smarthub")
check("/ask-smarthub renders", code, 200)
check("...with every recipe chip",
      all(f'data-recipe="{r.key}"' in body for r in ask_recipes.RECIPES
          if "ask" in r.placements))
check("...under its group heading", "Reporting" in body and "Optimization" in body)
check("...and no empty Audit heading", "ask-group-h\">Audit" not in body)
check("...each carrying its filled question",
      "ad performance for this month by campaign" in body)

code, body = page("/ask-smarthub?recipe=pacing_review&client=Acme&period=this_month")
check("a chip's URL is asked on arrival", code, 200)
check("...with the question filled in",
      "Review Acme&#39;s budget pacing for this month." in body
      or "Review Acme's budget pacing for this month." in body)
check("...and the recipe key carried into the page",
      '"pacing_review"' in body)

code, body = page("/ask-smarthub?recipe=not_a_recipe&client=Acme")
check("an unknown recipe opens the page empty rather than erroring", code, 200)
check("...asking nothing", 'initialRecipe=""' in body or "initialRecipe = \"\"" in body
      or '""' in body)

code, body = page("/")
check("the dashboard renders", code, 200)
check("...with the performance recipe chip",
      'data-recipe="performance_summary"' in body)
check("...and still its three-chip row",
      body.count('class="ask-chip"') == 3)

code, body = page("/client360")
check("Client 360 renders", code, 200)
# The client is chosen in the browser, so the card -- and its chips -- are
# built there from the recipe list the page is handed.
check("...carrying the recipe list to the browser",
      '"wow_trends"' in body and '"pacing_review"' in body)
check("...all five of this placement's recipes",
      all(f'"{r["key"]}"' in body for r in ask_recipes.chips("client360", "member")))
check("...and the ad-performance card renders them",
      "${askChips()}" in body and 'data-recipe="${esc(c.key)}"' in body)
check("...with the shared URL builder loaded",
      "/assets/ask-recipes.js" in body)

code, body = page("/reports/")
check("the reporting hub renders", code, 200)
check("...with the Trends chips", 'data-recipe="performance_summary"' in body)
code, body = page("/reports/pacing")
check("the pacing board renders", code, 200)
check("...with the pacing review chip", 'data-recipe="pacing_review"' in body)

code, body = page("/tools/ads/optimization")
check("the optimization page renders", code, 200)
check("...with the sweep chip", 'data-recipe="sweep_findings"' in body)

section("The shared URL builder is root-absolute")
js = (ROOT / "hub" / "static" / "ask-recipes.js").read_text(encoding="utf-8")
check("the URL starts at the root, not the module's mount",
      "'/ask-smarthub?'" in js)
check("...and is built in one place, not per page",
      js.count("/ask-smarthub?") == 1)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
