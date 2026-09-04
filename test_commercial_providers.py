"""Commercial Builder — does a key that was added actually reach the tool.

    python3 test_commercial_providers.py

Same shape as test_commercial_heygen.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. It runs with NO provider keys and sets them one
spelling at a time, because the defect this file exists for is a key that IS
set and IS NOT read.

## Why this file exists

Every provider in this module degrades to mock data instead of erroring. That
is the right behaviour and it is also what makes a misnamed key invisible:
concepts arrive from a template, stock search returns placehold.co images
labelled like footage, the voice track is silent and the render is a job id
with no file behind it. Nothing logs, nothing 500s, and the dashboard chip
says "mock mode" beside a key the person is looking at in the Render
dashboard.

Two providers were in exactly that state. ElevenLabs and Creatomate read
os.environ at IMPORT time, under one spelling each — while this deployment
sets every other provider as PEXELS_API, PIXABAY_API, HEYGEN_API. Adding
ELEVENLABS_API and CREATOMATE_API would have changed nothing at all: no voice
on any commercial, and no commercial rendered, with every screen healthy.

A third was quieter still. Runway had a working service and a real key check,
and the dashboard drew it from a separate "V1.5" list as a permanently grey
chip — so connecting Runway could not change what the page said about Runway.

And the whole panel only ever answered the weak question. A non-empty string
is not an accepted credential: a truncated paste, a revoked key, an account
out of credit and a key from the wrong workspace all look identical to
`bool(key)` and all fail at the moment somebody is waiting on a render. So
`provider_check` asks each provider, and the checks below are mostly about
the ways that answer can be confidently wrong.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1providers_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "providers-test-secret"
os.environ["PANEL_PASSWORD"] = "providers-test-password"

_SPELLINGS = {
    "heygen": ("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY"),
    "runway": ("RUNWAY_API", "RUNWAY_API_KEY", "RUNWAY_KEY"),
    "elevenlabs": ("ELEVENLABS_API", "ELEVENLABS_API_KEY", "ELEVENLABS_KEY"),
    "creatomate": ("CREATOMATE_API", "CREATOMATE_API_KEY", "CREATOMATE_KEY"),
    "pexels": ("PEXELS_API", "PEXELS_API_KEY", "PEXELS_KEY"),
    "pixabay": ("PIXABAY_API", "PIXABAY_API_KEY", "PIXABAY_KEY"),
    "coverr": ("COVERR_API", "COVERR_API_KEY", "COVERR_KEY"),
    "openai": ("OPENAI_API_KEY",),
}
for _names in _SPELLINGS.values():
    for _n in _names:
        os.environ.pop(_n, None)
for _n in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
           "CLOUDINARY_API_SECRET"):
    os.environ.pop(_n, None)

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


from hub.config import Settings                                          # noqa: E402
from modules.commercial_builder import config as cb_config                # noqa: E402
from modules.commercial_builder.services import cloudinary_service       # noqa: E402
from modules.commercial_builder.services import coverr_service           # noqa: E402
from modules.commercial_builder.services import creatomate_service       # noqa: E402
from modules.commercial_builder.services import elevenlabs_service       # noqa: E402
from modules.commercial_builder.services import heygen_service           # noqa: E402
from modules.commercial_builder.services import openai_service           # noqa: E402
from modules.commercial_builder.services import pexels_service           # noqa: E402
from modules.commercial_builder.services import pixabay_service          # noqa: E402
from modules.commercial_builder.services import provider_check           # noqa: E402
from modules.commercial_builder.services import runway_service           # noqa: E402
from modules.commercial_builder.routes import stock as stock_route       # noqa: E402


# ------------------------------------------- 1. every spelling, at call time
section("A key set under any accepted name reaches the service")

_SERVICES = {
    "heygen": heygen_service, "runway": runway_service,
    "elevenlabs": elevenlabs_service, "creatomate": creatomate_service,
}

for name, service in _SERVICES.items():
    check(f"{name} is not live with no key set", service.is_live(), False)
    for spelling in _SPELLINGS[name]:
        os.environ[spelling] = "k-" + spelling
        # Read at CALL time is the point: these modules are already imported,
        # so a value read at import would still be empty here. That is exactly
        # what happened live — the key arrived after the process started
        # reading it, which for a module-level constant is never.
        check(f"{name} reads {spelling} after import", service.is_live(), True)
        os.environ.pop(spelling)
    check(f"{name} is not live again once every spelling is unset",
          service.is_live(), False)


# -------------------------------------------------- 2. and reaches settings
section("hub/config.py knows both of the providers it did not")

for name, env, attr in (("ElevenLabs", "ELEVENLABS_API", "elevenlabs_key"),
                        ("Creatomate", "CREATOMATE_API", "creatomate_key")):
    os.environ[env] = "k-1"
    check(f"{name} is read from {env}", getattr(Settings(), attr), "k-1")
    check(f"and {name} appears on /diagnostics",
          any(row["name"] == name for row in Settings().status()), True)
    os.environ.pop(env)
    check(f"and reports absent when {env} is unset",
          getattr(Settings(), attr), "")


# ------------------------------------------------ 3. a placeholder is not a key
section("A placeholder Cloudinary URL is not a configured Cloudinary")

# The variable IS set, so every bool() check said yes and the failure surfaced
# later as an auth error from the provider.
os.environ["CLOUDINARY_URL"] = "cloudinary://API_KEY:API_SECRET@CLOUD_NAME"
check("the documented placeholder does not read as live",
      cloudinary_service.is_live(), False)
os.environ["CLOUDINARY_URL"] = "cloudinary://12345:abcdef@smart1"
check("a real-shaped URL does read as live",
      cloudinary_service.is_live(), bool(cloudinary_service._SDK_AVAILABLE))
os.environ.pop("CLOUDINARY_URL")


# --------------------------------------------- 4. the panel covers everything
section("The dashboard's status covers every provider with a service")

status = provider_check.status()
check("Runway is in the status the dashboard renders",
      "runway" in status, True)
check("and so is every other provider that has a service behind it",
      sorted(status), sorted(["cloudinary", "coverr", "creatomate", "elevenlabs",
                              "heygen", "openai", "pexels", "pixabay", "runway"]))

# Two lists that must agree: the spec's roster by release, and the display
# order the panel is built from. A provider added to one and forgotten in the
# other would simply never be checked, and nothing would say so.
check("the checked roster is exactly the spec's V1 + V1.5 stack",
      sorted(provider_check.PROVIDERS),
      sorted(cb_config.V1_PROVIDERS + cb_config.V1_5_PROVIDERS))
check("and V2 is not in it, because nothing is implemented behind it",
      set(cb_config.V2_PROVIDERS) & set(provider_check.PROVIDERS), set())

# The regression that hid Runway was in the template, not the data: it drew a
# hard-coded grey chip from a second list, so connecting Runway could not
# change what the page said.
_dash = (ROOT / "modules/commercial_builder/templates/commercial_dashboard.html").read_text()
check("the template no longer draws a hard-coded V1.5 chip",
      "V1.5" in _dash, False)
check("and renders one chip per provider from the status list",
      "{% for p in providers %}" in _dash, True)

_blueprint = (ROOT / "modules/commercial_builder/templates/commercial_blueprint.html").read_text()
_blueprint_js = (ROOT / "modules/commercial_builder/static/js/blueprint.js").read_text()
check("the footage button names our video library",
      "Search video library" in _blueprint, True)
check("the picker says owned footage is searched first",
      "We search our owned footage first" in _blueprint_js, True)
check("Video Search suggestions have their own first shelf",
      "Suggested from Video Search" in _blueprint_js, True)
check("outside providers are clearly secondary",
      "More stock options" in _blueprint_js, True)
check("the API keeps Video Search results separate from stock",
      '"video_search_results": owned_results' in
      (ROOT / "modules/commercial_builder/routes/stock.py").read_text(), True)

for name in provider_check.PROVIDERS:
    check(f"{name} has a check defined", name in provider_check._CHECKS, True)
    check(f"{name} says what breaks without it", bool(provider_check.COSTS.get(name)), True)
    check(f"{name} names the env var it wants", bool(provider_check.ENV_NAMES.get(name)), True)


# -------------------------------------- 4b. our footage gets the good queries
section("The owned video library gets the same short searches as stock")


class _OwnedLibrary:
    def __init__(self):
        self.calls = []

    @staticmethod
    def ready():
        return True

    @staticmethod
    def cutoff():
        return "2026-09-01T00:00:00Z"

    def search(self, query, *, orientation, limit):
        self.calls.append((query, orientation, limit))
        rows = {
            "roofing crew": [
                {"id": "ours-1", "tier": "OWNED", "preview_url": "one.mp4",
                 "description": "general contractor walking outside"},
            ],
            "storm roof": [
                {"id": "ours-1", "tier": "OWNED", "preview_url": "one.mp4",
                 "description": "general contractor walking outside"},
                {"id": "ours-2", "tier": "OWNED", "preview_url": "two.mp4",
                 "description": "roofing crew repairing storm damage",
                 "tags": ["roofing", "storm", "repair"], "bg_ready": True},
            ],
        }
        return {"results": rows.get(query, [])[:limit]}


_real_video_library = stock_route.video_library
_owned_library = _OwnedLibrary()
stock_route.video_library = _owned_library
try:
    _owned = stock_route._owned_queries(
        ["roofing crew", "storm roof", "golden roof"], "landscape", 2,
        "roofing crew repairs storm damage")
finally:
    stock_route.video_library = _real_video_library

check("every expanded query is tried before Video Search suggestions are ranked",
      [c[0] for c in _owned_library.calls],
      ["roofing crew", "storm roof", "golden roof"])
check("the relevant owned clip outranks a newer generic match",
      [r["id"] for r in _owned], ["ours-2", "ours-1"])
check("the Video Search shelf keeps one overall result cap",
      len(_owned), 2)
check("each query can look past duplicates before the merged shelf is capped",
      [c[2] for c in _owned_library.calls], [2, 2, 2])


# Exercise the response contract as well as the ranking helper. Commercial
# Builder renders these as separate shelves; `results` stays for older callers.
_real_expand = openai_service.expand_stock_queries
_real_pexels_search = pexels_service.search
_real_pixabay_search = pixabay_service.search
stock_route.video_library = _owned_library
openai_service.expand_stock_queries = lambda _q: ["roofing crew", "storm roof"]
pexels_service.search = lambda q, *_a: [{
    "id": f"pexels-{q}", "tier": "FREE", "provider": "pexels"}]
pixabay_service.search = lambda q, *_a: [{
    "id": f"pixabay-{q}", "tier": "FREE", "provider": "pixabay"}]
try:
    _stock_app = Flask("commercial-stock-contract")
    _stock_app.register_blueprint(stock_route.bp)
    _payload = _stock_app.test_client().get(
        "/api/stock/search?q=roofing+crew+repairs+storm+damage&expand=true"
    ).get_json()
finally:
    stock_route.video_library = _real_video_library
    openai_service.expand_stock_queries = _real_expand
    pexels_service.search = _real_pexels_search
    pixabay_service.search = _real_pixabay_search

check("the API identifies the Video Search shelf as first",
      _payload["source_order"], ["video_search", "stock"])
check("Video Search suggestions lead the backwards-compatible result list",
      [r["id"] for r in _payload["results"][:2]], ["ours-2", "ours-1"])
check("outside stock is not mixed into the Video Search shelf",
      {r["tier"] for r in _payload["video_search_results"]}, {"OWNED"})
check("Video Search clips are not repeated in the outside stock shelf",
      {r["tier"] for r in _payload["stock_results"]}, {"FREE"})


# ------------------------------------------- 5. no key is "not measured"
section("A provider nobody configured has not failed — it was not asked")

_calls = []


def _explode(*a, **k):
    _calls.append(a)
    raise AssertionError("a verify with no key must not touch the network")


_real_request = provider_check.requests.request
provider_check.requests.request = _explode

rows = provider_check.check_all()
check("every provider reports absent with no keys set",
      sorted({r["state"] for r in rows}), ["absent"])
check("and none of them made a request", _calls, [])
check("and the wording is 'not measured', never a failure",
      all("not measured" in r["detail"] for r in rows), True)
check("and each one names the variable to set",
      all(r["env"] and r["env"] in r["detail"] for r in rows), True)
check("and every provider is reported, not just the failing ones",
      len(rows), len(provider_check.PROVIDERS))

provider_check.requests.request = _real_request


# --------------------------------------------- 6. what each answer means
section("Refused, unreachable and out-of-date are three different answers")


class _Resp:
    def __init__(self, code):
        self.status_code = code


def _fake(code):
    def go(*a, **k):
        return _Resp(code)
    return go


def _raises(exc):
    def go(*a, **k):
        raise exc
    return go


_cases = [
    (200, "ok"), (201, "ok"),
    (401, "rejected"), (403, "rejected"), (400, "rejected"),
    # A key good enough to be rate-limited against is a key that was accepted.
    (429, "ok"),
    # These two say this file is out of date, not that the key is bad.
    (404, "inconclusive"), (405, "inconclusive"), (503, "inconclusive"),
]
for code, want in _cases:
    provider_check.requests.request = _fake(code)
    check(f"HTTP {code} reads as {want}",
          provider_check._probe("X", "GET", "https://example.invalid")["state"], want)

provider_check.requests.request = _raises(provider_check.requests.Timeout())
out = provider_check._probe("X", "GET", "https://example.invalid")
check("a timeout is unreachable, not a bad key", out["state"], "unreachable")
check("and says the key was not checked", "not checked" in out["detail"], True)

provider_check.requests.request = _raises(RuntimeError("boom"))
check("any other transport failure is also unreachable",
      provider_check._probe("X", "GET", "https://example.invalid")["state"], "unreachable")

provider_check.requests.request = _real_request


# ------------------------------------------- 7. a broken check is not a verdict
section("A check that falls over does not condemn the key")

os.environ["HEYGEN_API"] = "k-heygen"
_real_heygen = provider_check._CHECKS["heygen"]
provider_check._CHECKS["heygen"] = _raises(RuntimeError("the check itself is broken"))
row = provider_check.check_one("heygen")
check("a raising check reports inconclusive, not rejected", row["state"], "inconclusive")
check("and says it was the check that failed",
      "the key was not judged" in row["detail"], True)
provider_check._CHECKS["heygen"] = _real_heygen


# ----------------------------------------------------- 8. keys stay in here
section("A result is rendered into a page — it never carries the key")

os.environ["CREATOMATE_API"] = "k-creatomate-secret"
os.environ["ELEVENLABS_API"] = "k-elevenlabs-secret"
os.environ["RUNWAY_API"] = "k-runway-secret"
provider_check.requests.request = _fake(401)
rows = provider_check.check_all(["heygen", "runway", "elevenlabs", "creatomate"])
blob = repr(rows)
for secret in ("k-heygen", "k-creatomate-secret", "k-elevenlabs-secret", "k-runway-secret"):
    check(f"{secret} does not appear in the result", secret in blob, False)
check("a refused key reads as refused", sorted({r["state"] for r in rows}), ["rejected"])
check("and says what stops working",
      all(r["cost"] for r in rows), True)
provider_check.requests.request = _real_request
for _n in ("HEYGEN_API", "CREATOMATE_API", "ELEVENLABS_API", "RUNWAY_API"):
    os.environ.pop(_n, None)


# ------------------------------------------------ 9. mock mode still says so
section("Nothing above turned a missing key into a plausible answer")

check("no OpenAI key means the script writer is not live", openai_service.is_live(), False)
check("no Pexels key means stock search is not live", pexels_service.is_live(), False)
check("no Pixabay key means stock search is not live", pixabay_service.is_live(), False)
check("no Coverr key means stock search is not live", coverr_service.is_live(), False)

mock = creatomate_service.submit_render({"elements": []})
check("a mocked render is flagged as mock", mock.get("_mock"), True)
check("and reports no URL rather than a plausible one", mock.get("url"), None)

voices = elevenlabs_service.list_voices()
check("mock voices are flagged as mock", all(v.get("_mock") for v in voices), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
