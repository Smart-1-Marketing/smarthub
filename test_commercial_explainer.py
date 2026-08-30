"""Commercial Builder — the layer that explains the tool.

    python3 test_commercial_explainer.py

Same shape as test_ads_explainer.py and test_commercial_wizard.py: no pytest,
no new dependencies, a temporary data directory and a throwaway SQLite
database, so it never touches /var/data or the real one.

## Why this file exists

Every failure in this feature is silent by design, which is the whole reason
it needs a test rather than a look.

A bubble whose key is not in the registry is **removed** client-side, so the
template reads as helped and the page shows nothing. A tour step whose
selector matches no element keeps its narration and **hides the ring**, so a
renamed card costs the step its anchor and says so nowhere. And a walkthrough
step whose element is not on the page draws no ring either, while
`hub-demo.js`'s `perform()` opens with `if (!node) return` — so "Do it for me"
returns in silence, once per step, with the panel counting cheerfully up.

That last one had happened here in full. One nine-step
`commercial_builder.first_spot` walked the whole wizard, and hub-demo.js does
not navigate: it drives one page. Not one of its nine `data-demo` hooks
existed in any template, and every screen in the module offered it, so the
button was nine silent steps from wherever it was pressed. It was also
describing a tool that no longer exists — a Storyboard step the wizard
replaced, lengths with no :06, "eleven checks" against the 24 `run_qc`
returns, and a QR code it called required with QC hard-failing without it,
which is the exact rule `QR_CODE_RULES` reversed.

Each section below guards one of those.
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbexp_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbexp-test-secret"
os.environ["PANEL_PASSWORD"] = "cbexp-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY", "HEYGEN_API",
           "RUNWAY_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


MOUNT = "/tools/commercial-builder"
TEMPLATES = ROOT / "modules" / "commercial_builder" / "templates"

import werkzeug.test                                                    # noqa: E402
from wsgi import application                                            # noqa: E402
from hub import demos, help as hub_help                                 # noqa: E402

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)
anon = werkzeug.test.Client(application)

client_row = staff.post(MOUNT + "/api/clients",
                        json={"name": "Acme Heating", "website": "acme.example"}
                        ).get_json()["client"]
pid = staff.post(MOUNT + "/api/projects",
                 json={"client_id": client_row["id"], "lengths": [30], "formats": ["16:9"],
                       "commercial_type": "stock_vo", "platform": "ctv"}
                 ).get_json()["projects"][0]["id"]

PAGES = {
    "dashboard": "/",
    "library": "/library",
    "start": "/new",
    "brief": f"/project/{pid}/brief",
    "blueprint": f"/project/{pid}/blueprint",
    "voice": f"/project/{pid}/voice",
    "cta": f"/project/{pid}/cta",
    "preview": f"/project/{pid}/preview",
}
BODY = {name: staff.get(MOUNT + path).get_data(as_text=True)
        for name, path in PAGES.items()}


# ---------------------------------------------------------------------------
# 1. Bubbles
# ---------------------------------------------------------------------------
section("Every bubble a template places resolves in the registry")
# A key that does not resolve is removed client-side, so the template reads as
# helped and the page shows nothing — and nothing anywhere reports it.
placed = set()
for tpl in sorted(TEMPLATES.glob("*.html")):
    placed |= set(re.findall(r"help_dot\('([\w.]+)'\)", tpl.read_text()))
check("the module places bubbles at all", len(placed) > 0, True)
unresolved = sorted(k for k in placed if not hub_help.get(k))
check("and every one of them resolves", unresolved, [])


# ---------------------------------------------------------------------------
# 2. Tours
# ---------------------------------------------------------------------------
section("Every tour step is anchored on its own screen's template")
SCREEN_TEMPLATE = {
    "commercial_builder.start": "commercial_new.html",
    "commercial_builder.blueprint": "commercial_blueprint.html",
    "commercial_builder.voice": "commercial_voice.html",
    "commercial_builder.cta": "commercial_cta.html",
}
steps = [h for h in hub_help.REGISTRY
         if h.step and h.key.startswith("commercial_builder.")]
check("there are tour steps to check", len(steps) > 0, True)
check("and every screen carrying one has a template named here",
      sorted({h.screen for h in steps} - set(SCREEN_TEMPLATE)), [])

missing = []
for h in steps:
    src = (TEMPLATES / SCREEN_TEMPLATE[h.screen]).read_text()
    sel = h.selector
    if not sel:
        missing.append(f"{h.key} (no selector at all)")
        continue
    m = re.match(r"^#([\w-]+)$", sel)
    if m:
        found = f'id="{m.group(1)}"' in src
    elif sel.startswith("."):
        found = sel[1:] in src
    elif sel.startswith("["):
        found = sel.strip("[]").replace("'", '"') in src.replace("'", '"')
    else:
        found = sel in src
    if not found:
        missing.append(f"{h.key} -> {sel}")
# A step whose selector matches nothing keeps its narration and hides the
# ring, so the tour reads as working and points at nothing.
check("no step points at an element that is not there", missing, [])


section("A screen offers its own tour, and never another screen's")
for name in ("start", "blueprint", "voice", "cta"):
    check(f"{name} offers one", 'data-screen="commercial_builder.' in BODY[name], True)
    check(f"and {name} names itself",
          f'data-screen="commercial_builder.{name}"' in BODY[name], True)
for name in ("dashboard", "library", "brief", "preview"):
    # Naming a screen with no steps of its own is worse than naming none:
    # hub_help.tour() falls back to the MODULE prefix, so it would serve all
    # seventeen steps of four other screens over elements that are not here.
    check(f"{name} has no tour, so it offers none",
          "data-screen=" in BODY[name], False)
check("the fallback that makes that dangerous is real",
      len(hub_help.tour("commercial_builder.dashboard")) > 0, True)
check("and has_tour answers exactly, which is why the layout can rely on it",
      hub_help.has_tour("commercial_builder.dashboard"), False)
check("while a screen with steps answers yes",
      hub_help.has_tour("commercial_builder.blueprint"), True)


section("A dismissed tour can be reached again")
# Without this the offer is a one-shot and there is no second chance at it.
check("the header carries the way back on a screen with a tour",
      'data-tour-start="commercial_builder.blueprint"' in BODY["blueprint"], True)
check("and not on a screen without one",
      "data-tour-start=" in BODY["dashboard"], False)


# ---------------------------------------------------------------------------
# 3. The walkthrough
# ---------------------------------------------------------------------------
section("A walkthrough drives one page, so each one names the page it drives")
scenarios = {s.key: s for s in demos.SCENARIOS if s.module == "commercial_builder"}
check("the nine-step journey that drove nothing is gone",
      "commercial_builder.first_spot" in scenarios, False)
check("replaced by per-screen ones", sorted(scenarios), [
    "commercial_builder.blueprint", "commercial_builder.start_a_spot"])

DRIVES = {"commercial_builder.start_a_spot": "start",
          "commercial_builder.blueprint": "blueprint"}
silent = []
for key, sc in scenarios.items():
    body = BODY[DRIVES[key]]
    for st in sc.steps:
        hook = re.match(r"^\[data-demo='([\w-]+)'\]$", st.selector)
        if not hook:
            silent.append(f"{key}: {st.selector!r} is not a data-demo hook")
            continue
        if f'data-demo="{hook.group(1)}"' not in body:
            silent.append(f"{key}: {st.selector} is on no element of that page")
# perform() opens with `if (!node) return`, so a hook that is not there means
# "Do it for me" returns in silence — the failure this whole file exists for.
check("every step's element is on the page its scenario drives", silent, [])


section("A billed step is never offered as a button")
# hub-demo.js hides the action button when simulated is true. A walkthrough
# that spends money on a press somebody made to learn the tool is the one
# thing it must not do.
billed = {"cb-narration", "cb-checks"}
for st in scenarios["commercial_builder.blueprint"].steps:
    hook = re.match(r"^\[data-demo='([\w-]+)'\]$", st.selector).group(1)
    if hook in billed:
        check(f"{hook} is simulated", st.simulated, True)
check("and the screen that spends nothing says so",
      scenarios["commercial_builder.start_a_spot"].spends, [])


section("The floating button is offered only where a walkthrough can drive")
# A module is one data-module across every one of its screens, so without
# data-demo="off" the launcher offers the module's FIRST scenario on pages
# that scenario cannot drive.
check("Start gets it, and it is the module's first scenario",
      demos.SCENARIOS[[s.key for s in demos.SCENARIOS].index(
          "commercial_builder.start_a_spot")].key,
      "commercial_builder.start_a_spot")
check("so Start needs no button of its own",
      "data-demo-start=" in BODY["start"], False)
check("Start is not opted out", 'data-demo="off"' in BODY["start"], False)
# Blueprint's scenario is not the first, so it names its own — and a page
# carrying [data-demo-start] is skipped by the launcher, which is what stops
# it offering the Start page's walkthrough over its own elements.
check("Blueprint names its own scenario",
      'data-demo-start="commercial_builder.blueprint"' in BODY["blueprint"], True)
for name in ("dashboard", "library", "brief", "voice", "cta", "preview"):
    check(f"{name} is opted out", 'data-demo="off"' in BODY[name], True)


section("The scenarios describe the tool as it is now")
text = " ".join(st.title + " " + st.body + " " + st.notice
                for sc in scenarios.values() for st in sc.steps).lower()
# Each of these was in the old scenario and each is now false. A rep believes
# a walkthrough, which makes a stale one worse than none.
check("no Storyboard step, which the wizard replaced", "storyboard step" in text, False)
check("QR is not called required", "requires a qr" in text, False)
check("nor QC said to hard-fail without it", "hard-fail" in text, False)
# A number here is a number that drifts: run_qc returned eleven checks when
# the old scenario was written and returns twenty-four now. The count has to
# be adjacent to the word, or "a client of eleven years' standing" reads as
# one — a check that fires on correct copy is a check somebody switches off.
check("no hard-coded check count that drifts",
      bool(re.search(r"\b(?:eleven|twelve|twenty[- ]\w+|\d+)\s+checks\b", text)), False)
check("and the :06 is named", ":06" in text, True)


# ---------------------------------------------------------------------------
# 4. None of it reaches the client
# ---------------------------------------------------------------------------
section("A client sees none of the layer that explains our tool to staff")
with_job = staff.post(MOUNT + f"/api/projects/{pid}/render",
                      json={"format": "16:9", "force_despite_qc_failures": True})
job = with_job.get_json()["render_jobs"][0]
from modules.commercial_builder.db import db                            # noqa: E402
from modules.commercial_builder.models import RenderJob                 # noqa: E402
from wsgi import hub_app                                                # noqa: E402
with hub_app.app_context():
    row = RenderJob.query.get(job["id"])
    row.output_url = "https://example.test/spot.mp4"
    db.session.commit()
token = staff.post(MOUNT + f"/api/projects/{pid}/reviews",
                   json={}).get_json()["review"]["token"]
page = anon.get(f"{MOUNT}/review/{token}").get_data(as_text=True)
check("the client's page opens", bool(page), True)
# A staff tour or walkthrough on a page a client opens is an internal note in
# front of a customer — the rule test_ads_explainer.py holds for the estimate.
check("no tour is offered on it", "data-screen=" in page, False)
check("no walkthrough either", "data-module=" in page, False)
check("and no floating button can find a module to offer",
      "data-demo" in page, False)
check("nor any help bubble", "hub-help.js" in page, False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
