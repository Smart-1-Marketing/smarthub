"""One client reader: what a form may offer, and what a model is told.

    python3 test_client_prefill.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database.

## Why this file exists

Three failures, each of which looks like a working page.

**A prefill that reads a key the payload does not have.** `context()` asked
the site scan for `detected_phone`, `detected_address` and five more like
them, at the top level of the report. Insites publishes none of them there --
they are `meta.detected_phone`, `local_presence.business_phone` and so on,
which is why `hub/scan_facts.py` exists and carries the paths as data. So a
client with a complete site audit prefilled a form with a name and a URL and
left the phone, the address and the industry blank, on a record that had held
all three since the scan ran. Nothing errored. A blank field reads exactly
like a client we know nothing about.

**An offer that overwrites.** A value somebody typed is the better source and
must never be offered over -- the overlay rule `hub/client_urls.py` works to.
And a connection is never offered at all: it is written by record id, and
putting the display text into one creates nothing and clears the link.

**A model told nothing writes something plausible.** The generic copy that
comes back is the hardest kind of wrong to catch, because every sentence in it
is a real sentence. `for_prompt()` is the one block every AI feature appends,
and it has to carry what is *not* known as well as what is -- a gap a model
cannot see is a gap it fills in.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1prefill_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "client-prefill-test-secret")

from hub import client_context as cc  # noqa: E402

_passed = _failed = 0


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


# A client whose every source answers. Patched in rather than mocked at the
# HTTP layer: what is being asserted is the merge and the offer, not Knack.
FIELDS = {
    "client": "Riverside HVAC",
    "business_name": "Riverside HVAC & Plumbing",
    "website": "https://riversidehvac.com",
    "domain": "riversidehvac.com",
    "phone": "(317) 555-0142",
    "address": "1200 Main St, Carmel, IN",
    "city": "Carmel",
    "state": "IN",
    "industry": "Home Services",
    "media_partner": "TMRG",
    "brand_primary_color": "#1a2e58",
    "products": ["Connected TV", "Programmatic Display"],
}
SOURCES = {
    "client": "knack", "website": "knack", "city": "knack", "state": "knack",
    "industry": "knack", "media_partner": "registry",
    "business_name": "scan", "phone": "scan", "address": "scan",
    "brand_primary_color": "brand",
}


def fake_context(client, domain="", providers=None):
    def _ctx(_client, _domain=""):
        return {"client": client, "fields": dict(FIELDS),
                "sources": dict(SOURCES),
                "providers": providers or {"knack": "ok", "scan_facts": "ok"},
                "missing": [], "complete": True, "note": ""}
    return _ctx


_real_context = cc.context


# ------------------------------------------------- 1. the scan is read properly
section("The site scan is read by the module that knows where things are")

src = (ROOT / "hub" / "client_context.py").read_text(encoding="utf-8")
check("the scan is read through scan_facts", "from hub import scan_facts" in src)
check("...by the helper that carries the Insites paths",
      "scan_facts.contact_observed" in src)
check("a scan that could not be read is named, not absent",
      'tried["scan_facts"] = f"error:' in src)
check("and 'nothing on this website' is its own answer",
      'tried["scan_facts"] = "nothing read from this website"' in src)


# ------------------------------------------------------- 2. the offer
section("What a form is offered, and what it is never offered")

cc.context = fake_context("Riverside HVAC")
try:
    got = cc.prefill("Riverside HVAC")
    check("the website comes across", got["values"].get("website"),
          "https://riversidehvac.com")
    check("so does the phone read off their own site",
          got["values"].get("phone"), "(317) 555-0142")
    check("each offer says where it came from in words a rep can act on",
          got["offers"]["phone"]["from"], "their website")
    check("...and never names the provider that answered",
          any("insites" in o["from"].lower() or "brandfetch" in o["from"].lower()
              for o in got["offers"].values()), False)
    check("a list is not offered into a text box", "products" in got["values"],
          False)
    check("...but it still travels for a panel to draw",
          got["products"], ["Connected TV", "Programmatic Display"])

    # The overlay rule. What somebody typed is the better source.
    held = cc.prefill("Riverside HVAC", have={"phone": "(317) 555-9999"})
    check("a value already typed is never offered over",
          "phone" in held["values"], False)
    check("...and the rest is still offered",
          held["values"].get("city"), "Carmel")

    # A source that could not be read is a finding, not a silence.
    cc.context = fake_context("Riverside HVAC",
                              providers={"knack": "ok",
                                         "scan_facts": "error: Timeout"})
    unread = cc.prefill("Riverside HVAC")
    check("a source that failed is named", unread["unreadable"], ["scan_facts: Timeout"])

    # ------------------------------------------------- 3. into a drawn form
    section("Offered into a drawn form")

    cc.context = fake_context("Riverside HVAC")
    fields = [
        {"key": "website", "label": "Client Website URL", "control": "text",
         "writable": True},
        {"key": "phone", "label": "Phone", "control": "text", "writable": True},
        {"key": "client", "label": "Client Organization",
         "control": "connection", "writable": True},
        {"key": "new_website_url", "label": "New Website URL",
         "control": "text", "writable": True},
        {"key": "description", "label": "Describe the changes",
         "control": "paragraph", "writable": True},
    ]
    values, notes = cc.offer_into(fields, {}, "Riverside HVAC")
    check("the website is filled in", values.get("website"),
          "https://riversidehvac.com")
    # A connection is written by record id. The display text creates nothing
    # and clears the link -- the rule hub/knack_api.py gives at length.
    check("a connection is never filled in", "client" in values, False)
    # The site being built is not the site they have.
    check("a field that means something else is not swept in",
          "new_website_url" in values, False)
    check("what was filled in is said out loud",
          any("Client Website URL from the client record" in n for n in notes), True)

    held2, _ = cc.offer_into(fields, {"website": "https://typed.example"},
                             "Riverside HVAC")
    check("and a form that already has a value keeps it",
          held2["website"], "https://typed.example")

    # ------------------------------------------------------ 4. and for a model
    section("The block a model is handed")

    block = cc.for_prompt("Riverside HVAC")
    check("it carries the facts", "Riverside HVAC" in block
          and "(317) 555-0142" in block and "Carmel" in block)
    check("each fact says where it came from", "(from their website)" in block)
    check("what is running with us is in it",
          "Currently running with us: Connected TV, Programmatic Display" in block)
    check("what is NOT on file is named rather than left to be invented",
          "Not on file, and not to be invented" in block)
    check("...and the model is told so in as many words",
          "Do not state any fact about this business that is not listed above"
          in block)
    check("no credential ever travels",
          any(w in block.lower() for w in ("api_key", "secret", "token")), False)

    # A client nothing could be found for gets no block at all. `context()`
    # falls back to the name the caller handed in, so a "what we know" section
    # whose only content is the caller's own input tells a model this is a
    # business with no facts -- a different claim from not having looked.
    cc.context = _real_context
    check("a client nothing was found for produces no block",
          cc.for_prompt("A Business Nobody Has Heard Of"), "")
finally:
    cc.context = _real_context


# ------------------------------------------------- 5. one reader, not three
section("One reader, not a copy per tool")

for mod in ("modules/social_planner/app.py", "modules/gpt_ads/app.py"):
    src = (ROOT / mod).read_text(encoding="utf-8")
    check(f"{mod.split('/')[1]} delegates rather than copying",
          "from hub.client_context import tool_context" in src)
    check(f"  ...and carries no second gallery reader",
          "def _gallery_images" in src, False)

for mod, marker in (
        ("modules/ads_builder/campaign_ai.py", "from hub.client_context import for_prompt"),
        ("hub/seo.py", "from .client_context import for_prompt")):
    src = (ROOT / mod).read_text(encoding="utf-8")
    check(f"{mod} hands the client's facts to the model", marker in src)

hub = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
check("the web ticket form prefills from it",
      hub.count("from .client_context import offer_into"), 2)
adc = (ROOT / "hub" / "ad_copy.py").read_text(encoding="utf-8")
check("and so does the ad copy request",
      "from hub.client_context import offer_into" in adc)

# The scan is what tool_context added that neither copy had.
ctxsrc = (ROOT / "hub" / "client_context.py").read_text(encoding="utf-8")
check("a creative tool now sees the client's own site scan",
      "scan_facts.brand_observed" in ctxsrc)
check("...and a logo lifted off a page says so rather than standing in",
      '"seen on their website"' in ctxsrc)


shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
