"""The recipe library: the questions Ask SmartHub is good at, written down.

A recipe is a **question template, a tool plan and rendering guidance** kept
together. The chips on `/ask-smarthub` and the dashboard were four hard-coded
strings, and every other page that could have asked a good question of this
data -- the reporting hub, Client 360, the optimization page -- offered
nothing at all. This module is the one list, and each placement is a front
door to `/ask-smarthub` rather than a second copy of the chat.

Why a recipe rather than a longer prompt: the planner has a four-call budget
(`ask_smarthub.MAX_CALLS`) and a fixed catalog, and a chip that hands it a
pre-shaped question plus the tools that question is about spends that budget
on the read rather than on working out what was meant. The planner still
runs; a recipe is a hint and a rendering instruction, never a bypass. Python
re-validates every call it plans exactly as before.

**A recipe cannot widen anybody's access.** `tools` has to be a subset of
what `allowed_tools(role)` grants -- asserted at import, so a recipe naming a
tool that does not exist or that its own roles cannot reach fails the boot
rather than silently offering a chip that always errors. `render` is appended
to the ANSWER prompt, where it shapes a table; it is never appended to the
planner's tool allowlist.

The house rule in `hub/audit_summary.py` applies to every word a recipe
produces: never print a figure this Hub did not measure. The two recipes that
ask for a tone -- the monthly executive summary especially -- say so in their
own text, because positive framing that changes a number is the one way this
becomes a liability rather than a time saver.
"""
from __future__ import annotations

from dataclasses import dataclass

from hub.ask_smarthub import STAFF, TOOLS, allowed_tools
from hub.periods import DEFAULT_PERIOD, PERIODS

# Every page a chip can appear on. A placement the templates do not know is a
# recipe nobody can reach, so the list is closed and asserted at import --
# "declared and never wired" is a whole file in docs/claude/.
PLACEMENTS = ("ask", "dashboard", "reports_trends", "reports_pacing",
              "client360", "client_dashboard", "ads_optimization_google",
              "ads_optimization_bing")

# The groups chips are shown under, in order. A group with no recipe the
# viewer can reach renders no heading at all.
GROUPS = ("Reporting", "Optimization", "Audit")

# What {platform_label} and the platform argument become, per placement.
PLATFORM_FOR_PLACEMENT = {
    "ads_optimization_google": ("google_ads", "Google Ads"),
    "ads_optimization_bing": ("bing", "Microsoft Ads"),
}


@dataclass(frozen=True)
class Recipe:
    key: str                     # stable id, used in URLs and in the audit row
    group: str                   # one of GROUPS
    title: str                   # the chip's label
    question: str                # sent to /api/ask-smarthub
    tools: tuple[str, ...]       # the tools this question is about
    default_period: str          # a name from hub.periods.PERIODS
    roles: tuple[str, ...]       # STAFF or ADMINS, the ask_smarthub tuples
    placements: tuple[str, ...]  # where its chip appears
    render: str                  # appended to the answer prompt for this recipe
    needs_client: bool = True


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        key="performance_summary", group="Reporting",
        title="Campaign performance summary",
        question="Summarize {client}'s ad performance for {period} by campaign.",
        tools=("get_client_performance",), default_period="this_month", roles=STAFF,
        placements=("ask", "dashboard", "reports_trends", "client360"),
        render=(
            "Return one table: Campaign, Platform, Product, Partner, Impressions, "
            "Clicks, CTR, Avg CPC, Conversions, Cost, Pacing band, Prorated margin. "
            "Sort by Cost descending. Add a Totals row. Below the table, list every "
            "campaign carrying the ctr_drop_gt_20 flag with its delta. State the "
            "window label and the data_through date. If state is not 'ok', say so "
            "in one sentence and stop."),
    ),
    Recipe(
        key="wow_trends", group="Reporting",
        title="Week-over-week trend check",
        question=("Compare {client}'s ad performance this week against last week "
                  "and flag what moved."),
        tools=("get_client_performance",), default_period="last_7", roles=STAFF,
        placements=("ask", "reports_trends", "client360"),
        render=(
            "compare must be previous_period. Show Impressions, Clicks, CTR, CPC, "
            "Conversions, CPA and Cost with the percent change. For each "
            "metric_move_gt_15 flag, give one plausible cause drawn ONLY from other "
            "fields in the payload -- a pacing band, quarantined days, a campaign "
            "flag, a platform delta -- and one suggested next action. Never invent a "
            "cause the data cannot support; say 'no explanation in the data' instead."),
    ),
    Recipe(
        key="pacing_review", group="Optimization",
        title="Budget pacing review",
        question="Review {client}'s budget pacing for {period}.",
        tools=("get_client_performance",), default_period="this_month", roles=STAFF,
        placements=("ask", "reports_pacing", "client360"),
        render=(
            "One row per pacing line: Product, Platform, Monthly budget, Spent, "
            "Expected by now, Pace, Utilization percent, Band, Days remaining, and "
            "the line's CPA against the account CPA. Then two short lists: lines "
            "flagged pacing_under_strong, where the recommendation is to release "
            "budget, and pacing_over_weak, where it is to review. Recommendations "
            "are suggestions for a person to act on; never phrase one as though a "
            "change was made."),
    ),
    Recipe(
        key="monthly_exec", group="Reporting",
        title="Monthly executive summary",
        question="Write {client}'s monthly executive summary for {period}.",
        tools=("get_client_performance", "get_client_ga4_summary"),
        default_period="last_month", roles=STAFF,
        placements=("ask", "client360", "client_dashboard"),
        render=(
            "The audience is the client. Structure: 1) headline totals with the "
            "month-over-month change; 2) the top three campaigns by conversions and "
            "the top three by CPA; 3) campaigns needing attention, framed as the "
            "plan for next month rather than as failures; 4) website traffic from "
            "GA4 in two sentences if it is available; 5) three concrete "
            "recommendations. Lead with wins and momentum, frame every issue as an "
            "action already in hand, and keep it to about 250 words. Positive "
            "framing never changes a number, hides a flag, or states a result that "
            "was not measured: where a figure is null or the window is incomplete, "
            "say so plainly. Do not name a platform Smart 1 buys from -- use the "
            "product names the client was sold."),
    ),
    Recipe(
        key="ga4_sources", group="Reporting",
        title="Website traffic sources and UTM review",
        question=("Break down {client}'s website traffic for {period} by "
                  "source/medium and by campaign, and tell me what to scale and "
                  "what to fix."),
        tools=("get_client_ga4_summary",), default_period="last_30", roles=STAFF,
        placements=("ask", "reports_trends", "client360"),
        render=(
            "Call get_client_ga4_summary at most twice: once with "
            "breakdown=source_medium, then once with breakdown=campaign. Table 1: "
            "Source/Medium, Sessions, Users, Engagement rate, Conversions, "
            "Conversion rate, and the change. Table 2: Campaign, Source, Medium, "
            "the same metrics. Then three short lists: the top three by conversion "
            "rate, to scale; rows flagged high_volume_low_conv, to optimize; and "
            "any utm_case_variants or direct_none_gt_40 flags, which are tagging to "
            "fix. If no property is mapped, say that and stop."),
    ),
    Recipe(
        key="sweep_findings", group="Optimization",
        title="What did the optimization sweep flag?",
        question=("What did the latest optimization sweep flag for {client}'s "
                  "{platform_label} account?"),
        tools=("get_client_ads_findings",), default_period="last_30", roles=STAFF,
        placements=("ask", "ads_optimization_google", "ads_optimization_bing"),
        render=(
            "List the high-severity findings grouped by kind -- wasted spend, no "
            "conversions, quality score, structure, tracking. Each one: campaign, "
            "detail, the estimated monthly figure if the sweep recorded one, and "
            "the link. State scanned_at and the account state (never, failed, "
            "stale, clean). If the platform is not swept yet, say exactly that. Do "
            "not add findings the sweep did not record."),
    ),
)

BY_KEY: dict[str, Recipe] = {r.key: r for r in RECIPES}


def _check() -> None:
    """Every recipe is reachable, at boot, or the Hub does not start.

    A chip whose tool its own roles cannot reach is a chip that always errors,
    and a placement no template knows is a chip nobody sees. Both are the
    "declared and never wired" shape; neither survives an import here.
    """
    for recipe in RECIPES:
        if recipe.group not in GROUPS:
            raise ValueError(f"{recipe.key}: unknown group {recipe.group!r}")
        if recipe.default_period not in PERIODS:
            raise ValueError(f"{recipe.key}: unknown period {recipe.default_period!r}")
        for placement in recipe.placements:
            if placement not in PLACEMENTS:
                raise ValueError(f"{recipe.key}: unknown placement {placement!r}")
        for role in recipe.roles:
            reachable = allowed_tools(role)
            for tool in recipe.tools:
                if tool not in TOOLS:
                    raise ValueError(f"{recipe.key}: no such tool {tool!r}")
                if tool not in reachable:
                    raise ValueError(
                        f"{recipe.key}: {role} cannot reach {tool!r}")
    if len({r.key for r in RECIPES}) != len(RECIPES):
        raise ValueError("two recipes share a key")


_check()


def get(key: str) -> Recipe | None:
    return BY_KEY.get(" ".join(str(key or "").split())[:60])


def allowed(recipe: Recipe, role: str) -> bool:
    """Whether this role may be offered, and run, this recipe.

    Both halves: the recipe's own roles, and -- because a role tuple can drift
    from the catalog -- that every tool it names is one this role can reach.
    """
    if role not in recipe.roles:
        return False
    reachable = allowed_tools(role)
    return all(tool in reachable for tool in recipe.tools)


def for_placement(placement: str, role: str) -> list[Recipe]:
    """The recipes whose chips belong on one page, in group order."""
    place = " ".join(str(placement or "").split())[:60]
    rows = [r for r in RECIPES if place in r.placements and allowed(r, role)]
    rows.sort(key=lambda r: (GROUPS.index(r.group), RECIPES.index(r)))
    return rows


def grouped(placement: str, role: str) -> list[dict]:
    """The same list as headings and chips, with empty groups left out."""
    rows = for_placement(placement, role)
    out = []
    for group in GROUPS:
        chips = [r for r in rows if r.group == group]
        if chips:
            out.append({"group": group, "recipes": chips})
    return out


def period_label(period: str) -> str:
    """A period name as the words a question says it in."""
    return {
        "last_7": "the last 7 days", "last_14": "the last 14 days",
        "last_30": "the last 30 days", "last_90": "the last 90 days",
        "this_month": "this month", "last_month": "last month",
        "this_quarter": "this quarter", "last_quarter": "last quarter",
        "this_year": "this year", "last_year": "last year",
        "custom": "the period given",
    }.get(period, period)


def fill(recipe: Recipe, client: str = "", period: str = "",
         placement: str = "", period_text: str = "") -> str:
    """One recipe's question, with the page's own context filled in.

    A recipe that needs a client and has not been given one asks for one in
    the words a person would: the planner resolves the name, and Python
    refuses an uncertain match before any read runs, exactly as it does for a
    typed question.
    """
    name = " ".join(str(client or "").split())[:180]
    chosen = " ".join(str(period or "").split())[:40] or recipe.default_period
    if chosen not in PERIODS:
        chosen = recipe.default_period or DEFAULT_PERIOD
    _code, label = PLATFORM_FOR_PLACEMENT.get(
        " ".join(str(placement or "").split())[:60], ("google_ads", "Google Ads"))
    # ``period_text`` is a phrase the caller has already worked out -- a
    # named month with its own ISO dates, say -- and it is used verbatim.
    # The caller computing the dates is the point: the planner is told never
    # to compute one, so a question about an arbitrary month has to carry
    # the days it means rather than leave them to be guessed at.
    words = " ".join(str(period_text or "").split())[:120] or period_label(chosen)
    return recipe.question.format(
        client=name or "this client", period=words, platform_label=label)


def chip(recipe: Recipe, client: str = "", period: str = "",
         placement: str = "") -> dict:
    """One chip, as a template renders it and static/ask-recipes.js reads it."""
    chosen = period if period in PERIODS else recipe.default_period
    code, _label = PLATFORM_FOR_PLACEMENT.get(placement, ("", ""))
    return {
        "key": recipe.key, "group": recipe.group, "title": recipe.title,
        "question": fill(recipe, client, chosen, placement),
        "period": chosen, "client": client or "",
        "platform": code if placement in PLATFORM_FOR_PLACEMENT else "",
        "needs_client": recipe.needs_client,
    }


def chips(placement: str, role: str, client: str = "",
          period: str = "", limit: int = 0) -> list[dict]:
    """Every chip for one placement, ready to render."""
    rows = for_placement(placement, role)
    if limit:
        rows = rows[:max(1, int(limit))]
    return [chip(r, client, period, placement) for r in rows]


# The least-privileged staff role. A page behind AuthGuard that has no role
# of its own -- a mounted module's page -- offers a chip only when THIS role
# could run it, which is the safe direction: a chip nobody can run is a chip
# that always errors, and an admin-only recipe on a page every member can
# open is a promise the API then refuses.
BASELINE_STAFF = STAFF[0]


def staff_chips(placement: str, client: str = "", period: str = "",
                limit: int = 0) -> list[dict]:
    """Chips for a page that knows it is staff but not which staff.

    Mounted modules get their own Jinja environment and cannot see the hub's
    globals (the trap CLAUDE.md names second), so a module route calls this
    and passes the plain list into its template.
    """
    return chips(placement, BASELINE_STAFF, client, period, limit)


def render_for(key: str, role: str) -> str:
    """The rendering guidance for one recipe, or "" when it is not offered."""
    recipe = get(key)
    if recipe is None or not allowed(recipe, role):
        return ""
    return recipe.render


def tool_hint(key: str, role: str) -> tuple[str, ...]:
    """The tools a recipe expects, for the planner's hint. Empty when the
    recipe is unknown or this role may not run it -- never a wider set."""
    recipe = get(key)
    if recipe is None or not allowed(recipe, role):
        return ()
    return recipe.tools
