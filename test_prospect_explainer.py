"""The two screens of the prospect pipeline explain themselves.

    python3 test_prospect_explainer.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory, so it never touches /var/data or the real one.

## Why this file exists

The Website Audit tool and the prospect record are the two screens somebody
lands on cold: the first is where an audit is read and a lead is filed, the
second is where that lead is then worked. Both shipped the way Smart 1 Ads
shipped -- `hub/help.py`, `hub/help_routes.py` and `hub/static/hub-help.js`
all working, and neither screen opting into any of it. Every failure in
between is silent by design, which is the whole reason this file reads the
templates rather than trusting that somebody looked:

  1. **A bubble whose key is not in the registry is removed client-side.** So
     a typo'd key reads as helped from the template and shows nothing at all
     on the page, and nothing anywhere reports it.

  2. **A tour step whose selector matches no element keeps its narration and
     hides the ring.** A renamed card therefore costs the step its anchor and
     says so nowhere -- confident narration, ringing nothing. The prospect
     record is drawn entirely from a fetch, so its anchors cannot be found in
     the rendered HTML at all: they are produced by `card()`, and this file
     reads that function's call sites instead.

  3. **`data-screen` is a claim, and `has_tour()` is the question.**
     `hub/help.tour()` falls back to the *module* prefix when a screen has
     none, which is right for serving a tour somebody asked for and wrong for
     deciding whether to offer one. Both bodies name the registry's own screen
     name, and both are gated.

  4. **None of it reaches the page a prospect reads.** The customer-facing
     audit report is served to a stranger on somebody else's website; a staff
     note in it is an internal note in front of a client.

  5. **A tool that files a lead has to say where it went.** The record is
     where a prospect is actually worked. Filing used to end at the word
     "Filed." and the rep went to the panel to find the row again, which is
     the signpost failure `hub/stale_creative.py` names.
"""
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1explain_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import help as help_registry                          # noqa: E402

AUDIT_T = (ROOT / "hub" / "templates" / "website_audit.html").read_text()
PROSPECT_T = (ROOT / "hub" / "templates" / "prospect.html").read_text()
ROUTES = (ROOT / "hub" / "website_audit_routes.py").read_text()

AUDIT_SCREEN = "hub.website_audit"
PROSPECT_SCREEN = "hub.prospect"


def keys_in(text):
    """Every help key a template places, however it places it.

    `help_dot('x')` in Jinja and a `data-help="x"` span written by the page's
    own JavaScript are the same placeholder -- help_dot() emits exactly that
    span -- so both are read. A key built by string concatenation is skipped:
    it is `card()`'s one generic call site, and its real keys are asserted
    from the call sites instead.
    """
    found = set(re.findall(r"help_dot\('([^']+)'\)", text))
    for k in re.findall(r'data-help="([^"]+)"', text):
        if "+" not in k:
            found.add(k)
    return found


# =====================================================================
section("Every key placed resolves")
# =====================================================================

for name, text, least in (("the audit tool", AUDIT_T, 5),
                          ("the prospect record", PROSPECT_T, 2)):
    placed = keys_in(text)
    # The record places only two directly -- the strip and the convert button.
    # Its nine cards are explained through card()'s one call, asserted below.
    check(f"{name} places bubbles at all", len(placed) >= least, True)
    check(f"{name}: every key placed is in the registry",
          sorted(k for k in placed if help_registry.get(k) is None), [])
    check(f"{name}: every key placed belongs to this screen",
          sorted(k for k in placed
                 if not k.startswith(("hub.website_audit.", "hub.prospect."))), [])

# The record draws its nine cards from a fetch, so `card()` carries the key and
# the anchor together -- one decision rather than nine, so a card added next
# month is explained by naming itself.
CARD_KEYS = set(re.findall(r"return card\([^;]*?,\s*'([a-z_]+)'\);", PROSPECT_T))
check("the record's cards name their own help key",
      sorted(CARD_KEYS),
      ["assets", "audit", "duplicates", "proposals", "spend", "suite", "timeline"])
check("and every one of those resolves",
      sorted(k for k in CARD_KEYS
             if help_registry.get(f"hub.prospect.{k}") is None), [])
check("card() emits the bubble and the anchor from that one argument",
      'data-card="' in PROSPECT_T and "data-help=\"hub.prospect.'+esc(key)+'\"" in PROSPECT_T,
      True)


# =====================================================================
section("Every tour step anchors on its own screen")
# =====================================================================

check("the audit tool registers steps of its own",
      help_registry.has_tour(AUDIT_SCREEN), True)
check("so does the record", help_registry.has_tour(PROSPECT_SCREEN), True)

# has_tour() is exact where tour() falls back to the module prefix. A screen
# with no steps of its own must answer False even though a sibling under the
# same prefix has plenty -- named the other way, it draws seventeen of another
# screen's steps over elements that are not on the page.
check("a sibling screen with no steps of its own answers False",
      help_registry.has_tour("hub.prospect_queue"), False)
check("and a screen is not satisfied by its sibling's steps",
      help_registry.tour("hub.prospect")[0]["selector"],
      ".strip")

for step in help_registry.tour(AUDIT_SCREEN):
    sel = step.get("selector") or ""
    check(f"audit step {step['step']} anchors on something in its own template",
          bool(sel) and sel.strip("[]").replace("'", '"') in AUDIT_T
          or sel.lstrip("#.") in AUDIT_T, True)

for step in help_registry.tour(PROSPECT_SCREEN):
    sel = step.get("selector") or ""
    m = re.match(r"\[data-card='([a-z_]+)'\]$", sel)
    if m:
        # A data-card attribute exists only if some call site passes that key.
        check(f"record step {step['step']} rings a card the page draws",
              m.group(1) in CARD_KEYS, True)
    else:
        check(f"record step {step['step']} anchors on static markup",
              sel.lstrip("#.") in PROSPECT_T, True)

check("every audit step carries a selector",
      [s["step"] for s in help_registry.tour(AUDIT_SCREEN) if not s.get("selector")], [])
check("every record step carries a selector",
      [s["step"] for s in help_registry.tour(PROSPECT_SCREEN) if not s.get("selector")], [])


# =====================================================================
section("data-screen is gated, and names the registry's own screen")
# =====================================================================

for name, text, screen in (("the audit tool", AUDIT_T, AUDIT_SCREEN),
                           ("the record", PROSPECT_T, PROSPECT_SCREEN)):
    check(f"{name} names the screen the registry publishes",
          f'data-screen="{screen}"' in text, True)
    check(f"{name} asks has_tour rather than drawing it on the truth of a name",
          f"has_tour('{screen}')" in text, True)
    check(f"{name} offers the tour again afterwards",
          f'data-tour-start="{screen}"' in text, True)
    # Guarded `is not defined`, the pattern every helper call here uses, so a
    # Jinja environment that never got the global loses the guard, not the page.
    check(f"{name} guards the helper", "has_tour is not defined" in text, True)

check("the registry publishes exactly these two screen names",
      sorted(s for s in help_registry.screens()
             if s in (AUDIT_SCREEN, PROSPECT_SCREEN)),
      [PROSPECT_SCREEN, AUDIT_SCREEN])


# =====================================================================
section("None of it reaches the page a prospect reads")
# =====================================================================

client_pages = [
    ROOT / "modules" / "scans" / "templates" / "widget_audit.html",
    ROOT / "modules" / "scans" / "templates" / "widget_audit_report.html",
]
for p in client_pages:
    text = p.read_text()
    check(f"{p.name} places no staff help",
          bool(keys_in(text)) or "data-tour-start" in text or "data-screen" in text,
          False)


# =====================================================================
section("A tool that files a lead says where it went")
# =====================================================================

check("the lead route answers with the record's address",
      'result["record_url"] = f"/prospect/{result' in ROUTES, True)
# `capture()` allocates a uuid4 hex, so an id is always there on this path and
# the empty branch is deliberately not written server-side: a state nothing can
# reach reads as one the code handles.
check("without a branch nothing on that path can reach",
      "record_note" in ROUTES, False)
check("the panel renders the link", "d.record_url" in AUDIT_T, True)
# Two gunicorn workers, so a rolling deploy can answer from a version that has
# never heard of the key. The page still says where the row is.
check("and still says where the row is if the key is absent",
      "It is in the Leads panel." in AUDIT_T, True)
# The two writes stay apart: `note` already distinguishes saved here from
# created in Suite, and the record link is a third fact rather than either.
check("without folding into the saved/delivered note",
      "d.note||'Filed.'" in AUDIT_T, True)
check("nothing the store returns is written into the page as markup",
      "createElement('a')" in AUDIT_T and "innerHTML" not in
      AUDIT_T.split("btnLead")[-1].split("});")[0], True)


# =====================================================================
section("The pages still render")
# =====================================================================

from hub import create_hub_app, auth as hub_auth                # noqa: E402

app = create_hub_app()
c = app.test_client()
c.set_cookie(hub_auth.COOKIE_NAME,
             hub_auth.issue_cookie_value("todd@smart1marketing.com"))

r = c.get("/tools/website-audit")
html = r.get_data(as_text=True)
check("the audit tool renders", r.status_code, 200)
check("with its tour named on the body", f'data-screen="{AUDIT_SCREEN}"' in html, True)
check("and every key it placed resolving",
      sorted(k for k in re.findall(r'data-help="([^"]+)"', html)
             if help_registry.get(k) is None), [])
check("its first card carries the anchor step one rings", 'id="askCard"' in html, True)

r = c.get("/prospect/nobody")
html = r.get_data(as_text=True)
check("the record renders for an id nobody has", r.status_code, 200)
check("with its tour named on the body", f'data-screen="{PROSPECT_SCREEN}"' in html, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
