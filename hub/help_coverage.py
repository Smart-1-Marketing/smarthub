"""Which tools have an explanation on them, asked of what the Hub serves.

`hub/help.py`'s own docstring says the registry "can be audited for coverage —
`missing_for()` will tell you which screens have no help at all", and that is
true of the function. What decided the answer was a **hand-typed list** in
`hub/help_routes.py`, one per surface, and both had stopped keeping up with
the Hub:

* `/api/help/coverage` named 23 screens and answered **`missing: []`** — a
  clean bill of health — while the Proposal Builder carried bubbles on one
  panel of its fourteen steps and the IO Builder, the Social Content Planner,
  Web Tickets, Stock Photo Search, Scan Widgets, Website Blocks, GPT Ads and
  Google Access carried none at all. None of them was on the list, so none of
  them could be reported.
* `/api/demos/coverage` answered `missing: ['proposal_builder']` — a
  walkthrough for `modules/proposal_builder`, whose own docstring opens "The
  retired Proposal Builder — a redirect and an archive". Its one finding was
  about a module that no longer does anything, and its silence was about two
  dozen live ones.

That is the shape this repository keeps paying for: a check measured against a
restated copy, reading as clean because the copy went stale. The env drift
check regexed call sites that had become a table and reported no groups as a
clean bill; the anonymous route sweep read `mounts` off the wrong middleware
layer and swept half the app. Same failure, wearing help copy.

## What decides the list now

The **tiles on the staff index pages** — `hub/templates/creative.html` and
`hub/templates/tools.html`. That is this codebase's own definition of a tool
somebody opens: CLAUDE.md's conventions say a new tool gets a tile answering
the question it answers, and counts six tools that were invisible for weeks
because they had none. A tool with a tile is a tool a member of staff is sent
to; a tool with no tile has a bigger problem than missing help.

Four rules, each a way this goes quietly wrong.

**Finding no tiles is a failure, not a clean sweep.** The template is parsed,
and a parse that comes back empty means the markup changed — not that the Hub
has no tools. `measured` is False and the report says so, rather than
answering "nothing is missing" because it looked at nothing.

**An unmapped tile is named, never counted as covered.** Help keys are
`module.screen` and the module part is a label chosen for the help registry
(`utm` for `modules/utm_builder`, `display_ads` for the TypeScript renderer),
so the tile's URL cannot be resolved to one by rule. It is declared in
`PREFIXES`, and a tile in neither table comes back under `unmapped` — adding a
tool therefore forces a decision rather than inheriting silence.

**A page a client reads takes no staff help, and says why.** The nine industry
landing pages and the MSA signing page are tiled for staff and *served to a
prospect*; `hub/help.py`'s layer is our own explanation of our own screens, so
a bubble on one of those is an internal note in front of somebody we are
selling to. They are in `CLIENT_FACING` with that reason rather than left to
read as unexplained work.

**This reports; it does not gate.** Most of what it names is real and none of
it breaks a page, so failing a build on it would be a check switched off
within a week. It is `env_report()`'s shape — the thing that stands beside a
check and says what the check cannot see.
"""
from __future__ import annotations

import html
import pathlib
import re

__all__ = ["tiles", "report", "PREFIXES", "CLIENT_FACING"]

ROOT = pathlib.Path(__file__).resolve().parent
INDEX_PAGES = ("creative.html", "tools.html")

_TILE = re.compile(r'<a class="tool-tile" href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_NAME = re.compile(r"<h3>(.*?)</h3>", re.S)


def tiles() -> list[dict]:
    """Every tool tiled on a staff index page, as (href, name, page).

    Read from the templates rather than from a list beside them, for the
    reason `wsgi.py` gives about `modules/ads_builder`: the page and the
    inventory must not be able to disagree about what exists.
    """
    out: list[dict] = []
    for page in INDEX_PAGES:
        path = ROOT / "templates" / page
        try:
            src = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for href, body in _TILE.findall(src):
            name = _NAME.search(body)
            out.append({
                "href": href.split("?")[0],
                # The tile's own markup, so "Image Optimizer &amp; Resizer"
                # arrives as the name a person reads rather than as its
                # source.
                "name": html.unescape(
                    re.sub(r"\s+", " ", (name.group(1) if name else ""))).strip(),
                "page": page,
            })
    return out


# A tile's href to the prefix its help keys are registered under. Declared
# rather than derived: the prefix is a label chosen for the registry and is
# not the module directory's name (`utm` is modules/utm_builder, `seo` is the
# hub's own SEO pages, `display_ads` is the TypeScript ad renderer whose
# Hub-side half lives in hub/). A tile absent from here is reported as
# unmapped, so a tool added next month cannot inherit a silent pass.
PREFIXES: dict[str, str] = {
    # Creative
    "/tools/display-ads/_hub/start": "display_ads",
    "/tools/image-creator/": "image_creator",
    "/tools/image/": "image_optimizer",
    "/tools/bg-remover/": "bg_remover",
    "/tools/page-images/": "page_images",
    "/tools/image-picker/": "image_picker",
    "/tools/seo-images/": "seo_images",
    "/tools/landing-ads/": "landing_ads",
    "/tools/stock-photos/": "stock_photos",
    "/tools/commercial-builder/": "commercial_builder",
    "/tools/video-backgrounds/": "video_backgrounds",
    "/tools/dead-air/": "video_tools",
    "/tools/vertical-reframe/": "video_tools",
    "/tools/radio-promo/": "radio_promo",
    "/tools/fan-radio/": "fan_radio",
    # Client tools
    # Two segments, because that is the screen the registry publishes. The
    # Website Audit tool's keys are filed under `hub.website_audit.*` -- it is
    # a hub route, not a mounted module -- and declared as bare
    # `website_audit` this matched nothing and reported a tool carrying six
    # bubbles and a six-step tour as having no help at all.
    "/tools/website-audit": "hub.website_audit",
    "/sales/builder/": "sales_builder",
    "/tools/io/": "io_builder",
    "/sales/landing": "landing_maker",
    "/tools/pdf/": "pdf_optimizer",
    "/tools/ads/": "ads_builder",
    "/tools/gpt-ads/": "gpt_ads",
    "/tools/social/": "social",
    "/tools/site-blocks/": "site_blocks",
    "/scans/widgets": "scans",
    "/tools/calculators/": "calculators",
    "/tools/calculators/internal/digital-audio": "calculators",
    "/tools/calculators/internal/ctv": "calculators",
    "/tools/calculators/internal/dooh": "calculators",
    "/tools/calculators/internal/trade": "calculators",
    "/google/": "google_finder",
    "/google/ga-tools": "google_finder",
    "/google/gtm-tools": "google_finder",
    "/google/webmaster-tools": "google_finder",
    "/google/gmb-tools": "google_finder",
    "/google/history": "google_finder",
    "/tools/google-access/": "google_access",
    "/tools/seo-images/house": "seo_images",
    "/tools/utm/": "utm",
    "/tools/smartforecast/": "smartforecast",
}

# Tiled for staff and served to somebody who is not staff. The help layer is
# our own explanation of our own screens, so a bubble here is an internal note
# in front of a prospect -- the rule test_ads_explainer.py holds the client
# estimate to. Named with the reason rather than left to read as a gap.
CLIENT_FACING: dict[str, str] = {
    "/land/boat/": "a landing page a prospect fills in",
    "/land/hvac/": "a landing page a prospect fills in",
    "/land/legal/": "a landing page a prospect fills in",
    "/land/recruit/": "a landing page a prospect fills in",
    "/land/restaurant/": "a landing page a prospect fills in",
    "/land/rv/": "a landing page a prospect fills in",
    "/land/ski/": "a landing page a prospect fills in",
    "/land/stadium/": "a landing page a prospect fills in",
    "/land/tourism/": "a landing page a prospect fills in",
    "/msa/": "the agreement a client reads and signs",
}


# Help written for something that is not a tiled tool. Each of these is a
# screen rather than a tool -- the dashboard and Client 360 are the Hub
# itself, the QA reports are an index, the SEO pages hang off a client
# record, and `demo` is the walkthrough layer's own copy. Named here so
# `stray_prefixes()` can tell them from a prefix that has drifted.
NOT_A_TOOL: dict[str, str] = {
    "hub": "the dashboard and Client 360 -- the Hub itself, not a tool tile",
    "qa": "the QA report index, reached from its own page",
    "client_health": "My Clients, reached from the nav and the QA index "
                     "rather than tiled on Creative or Client Tools",
    "client_owner": "Assign Clients, reached from the QA index and from the "
                    "owner strip on a client record",
    "seo": "the schema and FAQ builders, reached from a client record",
    "demo": "the walkthrough layer explaining itself",
}


def stray_prefixes() -> list[str]:
    """Help registered under a prefix no tile maps to.

    The other direction, and the one that fails silently: renaming a prefix
    in `hub/help.py` -- or writing a tool's help under a different label from
    the one declared here -- leaves that tool reading as **missing** while
    its help sits there written. A tool that has had help written for it and
    is reported as having none is worse than one nobody has got to, because
    somebody writes it twice.
    """
    from . import help as help_registry

    known = set(PREFIXES.values()) | set(NOT_A_TOOL)
    return sorted({s.split(".")[0] for s in help_registry.screens()} - known)


def mislabeled_prefixes() -> list[dict]:
    """A tile's declared prefix that backs nothing, where help exists anyway.

    `stray_prefixes()` above asks the reverse question and cannot see this
    one: it reduces every screen to its **first segment**, so help written as
    `hub.website_audit.*` reduces to `hub`, which `NOT_A_TOOL` exempts as the
    dashboard and Client 360. That exemption has to be broad -- the Hub's own
    pages genuinely are not tiled tools -- so nothing on that side can tell a
    real hub page from a tool whose keys happen to start `hub.`.

    Which leaves the forward direction, and it fails in the **safe-looking**
    way: the tool is reported as having no help written, which reads as a
    backlog entry rather than as a defect, so nobody investigates and the
    copy gets written a second time. That is exactly what happened to the
    Website Audit tool the release after it was given six bubbles and a tour.

    A prefix matching nothing is *ordinarily correct* -- a tool is tiled
    before its help is written, and at one point fourteen tiled tools had
    none -- so the finding is narrower than that: a prefix that resolves to
    no screen **while the registry holds one whose name contains it**. That is a label that names the wrong thing, and
    it is the only case where "no help written" is a wrong answer rather than
    a true one.
    """
    from . import help as help_registry

    screens = list(help_registry.screens())
    have = set(screens) | {s.split(".")[0] for s in screens}

    by_prefix: dict[str, list] = {}
    for href, prefix in PREFIXES.items():
        by_prefix.setdefault(prefix, []).append(href)

    out = []
    for prefix, hrefs in sorted(by_prefix.items()):
        if prefix in have:
            continue
        found = sorted(s for s in screens
                       if prefix in s.split("."))
        if found:
            out.append({"prefix": prefix, "tiles": sorted(hrefs),
                        "registered": found})
    return out


def report() -> dict:
    """Coverage of the tools staff are actually sent to.

    Never raises and never answers a confident nothing: a template that could
    not be parsed comes back `measured: False` with the reason, because "no
    tool is missing help" and "we could not read the index pages" are
    different answers and only the first means there is nothing to write.
    """
    from . import help as help_registry

    found = tiles()
    if not found:
        return {"measured": False,
                "reason": "no tiles parsed from " + ", ".join(INDEX_PAGES) +
                          " — the markup changed, so this says nothing about "
                          "coverage rather than reporting none missing",
                "tools": [], "covered": [], "missing": [],
                "unmapped": [], "client_facing": []}

    # Both shapes, because a prefix is a label chosen for the registry and
    # some of them need two segments to be unambiguous: `hub.website_audit`
    # is a tool, `hub.prospect` is a record page, and the bare `hub` they
    # share names neither. Matching the first segment alone made a tile
    # mapped to a two-segment screen resolve to nothing, and the tool then
    # read as unexplained -- the failure this whole module exists to find,
    # inside the module.
    have = set()
    for screen in help_registry.screens():
        have.add(screen)
        have.add(screen.split(".")[0])

    covered, missing, unmapped, client = [], [], [], []
    for t in found:
        href = t["href"]
        if href in CLIENT_FACING:
            client.append({**t, "reason": CLIENT_FACING[href]})
            continue
        prefix = PREFIXES.get(href)
        if prefix is None:
            unmapped.append(t)
            continue
        row = {**t, "prefix": prefix}
        (covered if prefix in have else missing).append(row)

    return {
        "measured": True,
        "tools": len(found),
        "covered": covered,
        "missing": missing,
        "unmapped": unmapped,
        "client_facing": client,
        "stray": stray_prefixes(),
        # Kept apart from `missing`, because they are two different jobs:
        # a missing tool needs copy written, a mislabeled one needs one line
        # corrected -- and folded together the second is invisible inside the
        # first, which is how it went unnoticed.
        "mislabeled": mislabeled_prefixes(),
        "note": "Measured against the tiles on " + " and ".join(INDEX_PAGES) +
                ", which is what decides whether a member of staff is sent to "
                "a tool at all. A tool with no tile has a bigger problem than "
                "missing help.",
    }
