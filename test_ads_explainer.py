"""Smart 1 Ads — the explainer: bubbles, the per-screen tour, the walkthroughs.

    python3 test_ads_explainer.py

House style: no pytest, no new dependencies, and nothing here reaches
/var/data, the real database or a provider.

## Why this file exists

Smart 1 Ads shipped with no explanation on any screen. Not a missing feature —
an invisible one: the help layer, the tour and the guided walkthrough all
already existed and worked, and this module was wired into none of them, so
the tool that decides what a client is quoted was the one nobody could be
walked through.

Four ways that goes wrong quietly, each of which this file asserts against:

  1. **A bubble whose key is not in the registry disappears.** hub-help.js
     removes a placeholder it cannot fill rather than leaving a dead "?" on the
     page, which is right — and it means a typo'd key is invisible from both
     ends: the template looks helped, the page shows nothing.

  2. **A tour step pointing at nothing narrates into thin air.** The ring is
     hidden when the selector matches no element, by design, so a renamed card
     costs the step its anchor and says so nowhere.

  3. **A walkthrough that drives a screen it is not on does nothing at all.**
     That is what was live: the scenario named ``#geography``, ``#budget`` as a
     text field and four ``data-demo`` hooks that existed in no template, and
     hub-demo.js floated its "Walk me through this" button onto every screen in
     the module — Settings included, where none of the fields are. "Do it for
     me" returns silently when the node is missing, so the button lied.

  4. **Staff help must not reach the client's estimate.** /tools/ads/estimate/
     is public and chrome-free; a bubble or a tour anchor in that document is
     an internal note in front of a prospect.

And one that is not about content at all: a module's Jinja environment is its
own, so ``{{ help_dot(...) }}`` raises UndefinedError and 500s the page unless
``install_template_helpers()`` ran for that app. Every call is written with the
``if help_dot is defined`` guard for that reason, and the last section renders
the real templates both ways round to prove both halves.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_explainer_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "ads-explainer-test-secret"
os.environ["PANEL_PASSWORD"] = "ads-explainer-test-password"
os.environ.pop("CLOUDINARY_URL", None)
os.environ.pop("OPENAI_API_KEY", None)          # nothing here may call a model

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def truthy(label, got):
    check(label, bool(got), True)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import demos, help as help_registry                        # noqa: E402

TPL = ROOT / "modules" / "ads_builder" / "templates"
TEMPLATES = {p.name: p.read_text(encoding="utf-8") for p in sorted(TPL.glob("*.html"))}
ALL_MARKUP = "\n".join(TEMPLATES.values())

# The three documents a client can end up looking at. ads_estimate.html is the
# public page itself; _estimate_doc.html and _estimate_style.html are included
# by it, so anything staff-only in them reaches the client too.
CLIENT_FACING = ("ads_estimate.html", "_estimate_doc.html", "_estimate_style.html",
                 "ads_estimate_gone.html")

# Which template owns which screen, and which walkthrough it offers. This is
# the map the assertions below hold the templates to.
SCREENS = {
    "ads_builder.generator": "ads_generator.html",
    "ads_builder.proposal": "ads_proposal.html",
    "ads_builder.approvals": "ads_approvals.html",
    "ads_builder.campaigns": "ads_campaigns.html",
    "ads_builder.settings": "ads_settings.html",
    "ads_builder.activity": "ads_activity.html",
}

HELP_CALL = re.compile(r"help_dot\(\s*'([^']+)'\s*\)")
SET_SCREEN = re.compile(r"{%\s*set\s+screen\s*=\s*'([^']+)'\s*%}")
SET_WALKTHROUGH = re.compile(r"{%\s*set\s+walkthrough\s*=\s*'([^']+)'\s*%}")
ATTR_SELECTOR = re.compile(r"\[data-(tour|demo)='([^']+)'\]")


def anchored(selector: str, src: str) -> bool:
    """Does this CSS selector match anything written in that template?

    Only the two shapes the demos use: an id, and an attribute hook. Written as
    a selector with single quotes and in the markup with double ones, so the
    strings are never equal -- comparing them literally is how a check like this
    passes on nothing at all.
    """
    if selector.startswith("#"):
        return f'id="{selector[1:]}"' in src
    m = ATTR_SELECTOR.fullmatch(selector.strip())
    if m:
        return f'data-{m.group(1)}="{m.group(2)}"' in src
    return selector in src


def ads_help():
    return [h for h in help_registry.REGISTRY if h.key.startswith("ads_builder.")]


# ---------------------------------------------------------------- the content
section("There is an explainer, and it is written")

check("every Smart 1 Ads screen has help written for it",
      sorted({h.screen for h in ads_help()}), sorted(SCREENS))

for screen in sorted(SCREENS):
    steps = help_registry.tour(screen)
    truthy(f"{screen} has a tour", len(steps) >= 2)
    check(f"{screen}'s tour steps are numbered without a gap or a repeat",
          [s["step"] for s in steps], list(range(1, len(steps) + 1)))

truthy("every entry says something",
       all(len(h.body) > 80 and h.title for h in ads_help()))

# A body that names a CPC number would be a claim about this account that
# nobody measured, on the one screen money is chosen from. The registry talks
# about the benchmark; it never quotes one.
check("no bubble quotes a dollar figure of its own",
      [h.key for h in ads_help() if re.search(r"\$\s?\d", h.body)], [])


# ------------------------------------------------------------- bubbles ↔ keys
section("Every bubble on a page has content behind it")

used = {}
for name, src in TEMPLATES.items():
    for key in HELP_CALL.findall(src):
        used.setdefault(key, []).append(name)

truthy("the module places bubbles at all", len(used) >= 15)
check("every key a template asks for exists in the registry",
      sorted(k for k in used if not help_registry.get(k)), [])
check("...and every key is one of this module's",
      sorted(k for k in used if not k.startswith("ads_builder.")), [])

# The guarded form, everywhere. Unguarded, a module whose Jinja environment
# never got the helper raises UndefinedError and the page 500s.
unguarded = []
for name, src in TEMPLATES.items():
    for line in src.splitlines():
        if "help_dot(" in line and "help_dot is defined" not in line:
            unguarded.append(f"{name}: {line.strip()[:60]}")
check("every help_dot call is guarded with 'if help_dot is defined'", unguarded, [])

# Written but never placed is the same as not written.
placed = set(used)
check("no entry with a tour step is missing its anchor on a page",
      sorted(h.key for h in ads_help()
             if h.step and h.key not in placed
             and h.selector and not anchored(h.selector, ALL_MARKUP)), [])


# ----------------------------------------------------------- tour selectors
section("Every tour step points at something that exists")

missing = []
for screen, template in SCREENS.items():
    for step in help_registry.tour(screen):
        sel = step["selector"]
        if not sel:
            missing.append(f"{step['key']}: no selector")
        elif not anchored(sel, ALL_MARKUP):
            missing.append(f"{step['key']}: {sel}")
check("every ads tour step has an anchor in the module's templates", missing, [])

# The anchor has to be on the screen the step belongs to, not merely somewhere
# in the module: the tour runs on one page.
off_screen = []
for screen, template in SCREENS.items():
    for step in help_registry.tour(screen):
        if step["selector"] and not anchored(step["selector"], TEMPLATES[template]):
            off_screen.append(f"{step['key']} -> {template}")
check("...and it is on that screen's own template", off_screen, [])


# ------------------------------------------------------------ screen wiring
section("Each screen declares itself, and offers only a walkthrough it can run")

declared = {}
for name, src in TEMPLATES.items():
    for screen in SET_SCREEN.findall(src):
        declared[screen] = name
check("every screen in the registry is declared by its template", declared, SCREENS)

check("the base template turns the declaration into a tour",
      'data-screen="{{ screen }}"' in TEMPLATES["ads_base.html"], True)
check("...and into a button that replays it",
      'data-tour-start="{{ screen }}"' in TEMPLATES["ads_base.html"], True)

walkthroughs = {}
for name, src in TEMPLATES.items():
    for key in SET_WALKTHROUGH.findall(src):
        walkthroughs[name] = key

check("only the two screens with a walkthrough offer one",
      sorted(walkthroughs), ["ads_generator.html", "ads_proposal.html"])
check("and each names a scenario that exists",
      sorted(k for k in walkthroughs.values() if not demos.get(k)), [])
check("...belonging to this module",
      sorted(k for k in walkthroughs.values() if demos.get(k).module != "ads_builder"), [])

# hub-demo.js floats its own launcher onto any page carrying data-module. On a
# screen with no walkthrough of its own that offered the generator's, which
# cannot drive Settings or Live campaigns -- it highlighted nothing and "Do it
# for me" did nothing. Both halves of the opt-out are asserted, because either
# one alone silently restores the lying button.
demo_js = (ROOT / "hub" / "static" / "hub-demo.js").read_text(encoding="utf-8")
check("the launcher honours an opt-out",
      'data-demo") !== "off"' in demo_js, True)
check("...and the module opts its walkthrough-less screens out",
      '{%- if walkthrough is not defined %} data-demo="off"{% endif %}'
      in TEMPLATES["ads_base.html"], True)


# ------------------------------------------------------------- walkthroughs
section("Every walkthrough step drives a control that is on its own screen")

SCENARIO_TEMPLATE = {
    "ads_builder.first_campaign": "ads_generator.html",
    "ads_builder.review_and_launch": "ads_proposal.html",
}

check("the module's walkthroughs are the two the screens name",
      sorted(s["key"] for s in demos.for_module("ads_builder")),
      sorted(SCENARIO_TEMPLATE))

dead = []
for key, template in SCENARIO_TEMPLATE.items():
    scenario = demos.get(key)
    src = TEMPLATES[template]
    for i, step in enumerate(scenario.steps, 1):
        if step.selector and not anchored(step.selector, src):
            dead.append(f"{key} step {i}: {step.selector}")
check("no walkthrough step points at a control that does not exist", dead, [])

# A step that fills a <select> has to offer a value that select actually has,
# or "Choose it" sets a value the page ignores and the demo moves on.
bad_choice = []
for key, template in SCENARIO_TEMPLATE.items():
    for step in demos.get(key).steps:
        if step.action == "choose" and step.selector == "#sector":
            from modules.ads_builder.campaign_ai import SECTOR_CPC
            if step.value not in SECTOR_CPC:
                bad_choice.append(f"{key}: {step.value}")
check("a 'choose' step names a real option", bad_choice, [])

# Every step carries the sentence worth remembering: a walkthrough that only
# says "now click Save" teaches clicking.
check("every step says why the step matters",
      [f"{k} step {i}" for k, _ in SCENARIO_TEMPLATE.items()
       for i, s in enumerate(demos.get(k).steps, 1) if not s.notice], [])

# Nothing in a walkthrough may write to a client's account or spend on its own.
# Generating costs tokens, so it is simulated; approving and sending are real
# writes, so the learner does those themselves rather than "Do it for me".
gen = demos.get("ads_builder.first_campaign")
check("the generate step is simulated",
      [s.simulated for s in gen.steps if s.selector == "[data-demo='ads-generate']"], [True])
rev = demos.get("ads_builder.review_and_launch")
check("no step clicks approve or send for you",
      [s.title for s in rev.steps if s.action == "click" and s.autofill], [])


# ------------------------------------------------ nothing staff-only leaks out
section("The client's estimate carries none of it")

for name in CLIENT_FACING:
    src = TEMPLATES[name]
    check(f"{name} has no help bubble", "help_dot(" in src or "data-help" in src, False)
    check(f"{name} has no tour anchor", "data-tour" in src, False)
    check(f"{name} declares no screen", "data-screen" in src or SET_SCREEN.search(src) is not None,
          False)
    check(f"{name} offers no walkthrough", "data-demo" in src, False)

# The public page must not inherit the chrome by extending the module's shell,
# either -- that is where the tour button and data-screen live.
check("the public estimate does not extend the module's base template",
      re.search(r"{%\s*extends", TEMPLATES["ads_estimate.html"]) is not None, False)


# --------------------------------------------------------- it actually renders
section("The bubbles survive a real render, both ways round")

from flask import Flask                                            # noqa: E402
from hub.help_routes import install_template_helpers               # noqa: E402
from modules.ads_builder.app import app as ads_app                 # noqa: E402

# wsgi.py calls this for every mounted module. With it, a placeholder the JS can
# fill; without it, the guard has to degrade to nothing rather than a 500.
install_template_helpers(ads_app)
ads_app.config["TESTING"] = True
page = ads_app.test_client().get("/")
check("the generator renders", page.status_code, 200)
html = page.get_data(as_text=True)

truthy("the page declares its screen", 'data-screen="ads_builder.generator"' in html)
truthy("the tour can be replayed from the header",
       'data-tour-start="ads_builder.generator"' in html)
truthy("the walkthrough is offered by key",
       'data-demo-start="ads_builder.first_campaign"' in html)
check("a screen with a walkthrough is not opted out of the launcher",
      'data-demo="off"' in html, False)
truthy("bubbles are placed as fillable placeholders",
       'data-help="ads_builder.generator.client"' in html)
check("every placeholder on the rendered page has content behind it",
      sorted(k for k in re.findall(r'data-help="([^"]+)"', html)
             if not help_registry.get(k)), [])

settings_page = ads_app.test_client().get("/settings")
check("a screen with no walkthrough renders", settings_page.status_code, 200)
settings_html = settings_page.get_data(as_text=True)
truthy("...declares its own screen", 'data-screen="ads_builder.settings"' in settings_html)
truthy("...and opts out of the floating walkthrough button",
       'data-demo="off"' in settings_html)

# The other half of the trap: a module whose environment never received the
# helpers must lose the icon, not the page.
bare = Flask(__name__, template_folder=str(TPL))
with bare.app_context():
    rendered = bare.jinja_env.get_template("ads_settings.html").render(
        screen="ads_builder.settings", mount="/tools/ads", version="0", version_date="",
        status={"deploy_ready": False, "blocks": []}, openai_configured=False,
        connected=False, account=None, accounts=[], env_rows=[], bing=None)
check("with no helper registered the page still renders", "What works right now" in rendered, True)
check("...and simply carries no bubble", "data-help" in rendered, False)


print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed\n")
sys.exit(1 if _failed else 0)
