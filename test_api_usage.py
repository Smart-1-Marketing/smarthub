"""hub/quotas.py — usage estimates for Google, ElevenLabs and Cloudinary.

    python3 test_api_usage.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary activity log so it never reads or writes the real
one.

## Why this file exists

A usage page is only worth having if its numbers are right, and every way one
of these can be wrong is quiet. Nothing 500s. The page renders, the bars fill,
and the figure is off by a factor of two or reads zero for a provider we are
demonstrably paying — which is worse than no page at all, because somebody
will make a plan decision on it.

So each check below is a way the estimate could lie:

  1.  characters, not renders     — ElevenLabs bills the character; a count of
                                    renders makes a tag and a 60-second read
                                    cost the same
  2.  the model changes the rate  — Flash and Turbo bill half a credit per
                                    character, so a single rate is wrong by 2x
                                    for whichever module is on the other model
  3.  failures spent nothing      — a 4xx never reached the voice engine
  4.  cached is not billable      — the existing rule, still true
  5.  nothing recorded            — reads "not measured", never a confident 0
  6.  Cloudinary attribution      — bytes land against the module that sent them
  7.  no invented Cloudinary price— credits are reported; money only when the
                                    per-credit rate is configured
  8.  Google is filed by URL      — one helper calls four APIs, so the caller
                                    cannot be what identifies the API
  9.  Google quota is per DAY     — a monthly total would never show the 4pm
                                    cliff that actually stops work
  10. Google costs nothing        — and says so, rather than showing a blank
  11. blind spots are named       — a call site that spends without recording
                                    is listed, not silently missing
  12. one ledger pass             — every view reads the log once, together
  13. the API contract holds      — /api/quotas still carries what the page
                                    reads off it
"""
import json
import os
import shutil
import pathlib
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1apiusage_test_")
LOG = os.path.join(TMP, "audit.jsonl")
os.environ["AUDIT_LOG_PATH"] = LOG
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
# The estimates read these; pin them so the assertions below are about the
# arithmetic and not about whatever the environment happens to carry.
os.environ["ELEVENLABS_USD_PER_1K_CREDITS"] = "0.22"
os.environ["ELEVENLABS_MONTHLY_LIMIT"] = "100000"
os.environ["ELEVENLABS_WARN_AT"] = "90000"
os.environ["GOOGLE_ADS_DAILY_QUOTA"] = "15000"
os.environ.pop("CLOUDINARY_USD_PER_CREDIT", None)

from hub import quotas                                   # noqa: E402

MONTH = quotas.month_key()
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def close(label, got, want, tol=0.01):
    check(label, abs(float(got) - float(want)) <= tol, True)


def section(title):
    print(f"\n{title}")


def clear_log():
    open(LOG, "w").close()


def rows_of(provider):
    out = []
    for line in open(LOG, encoding="utf-8"):
        row = json.loads(line)
        if row.get("provider") == provider:
            out.append(row)
    return out


# --------------------------------------------------- 1-3. ElevenLabs characters
section("ElevenLabs is billed per character, and not every character costs the same")
clear_log()
# A 30-second read and a 5-second tag, on the standard model.
quotas.record_tts("x" * 450, module="radio_promo", model="eleven_multilingual_v2")
quotas.record_tts("x" * 75, module="fan_radio", model="eleven_multilingual_v2")
# The same 450-character read on Flash, which bills half a credit per character.
quotas.record_tts("x" * 450, module="commercial_builder", model="eleven_flash_v2_5")

est = quotas.elevenlabs_estimate(MONTH)
check("three renders recorded", est["measured"]["renders"], 3)
check("characters are summed, not renders", est["measured"]["characters"], 975)
# 450 + 75 at 1.0, 450 at 0.5 = 750 credits. Counting renders would have said
# "3", and counting every character at one rate would have said 975.
close("Flash bills at half a credit", est["measured"]["credits"], 750)
close("cost is credits at the configured rate", est["estimated_cost"],
      750 / 1000.0 * 0.22)
check("attributed to the module that spent it",
      est["by_module"]["radio_promo"]["characters"], 450)
check("and to the model that priced it",
      est["by_model"]["eleven_flash_v2_5"]["characters"], 450)
close("the half-rate model's own cost", est["by_model"]["eleven_flash_v2_5"]["credits"], 225)

section("A render that failed spent nothing")
quotas.record_tts("x" * 1000, module="radio_promo",
                  model="eleven_multilingual_v2", ok=False)
est = quotas.elevenlabs_estimate(MONTH)
check("failed renders are not billed", est["measured"]["characters"], 975)
check("but they are counted, so a wall of them is visible",
      est["measured"]["failed_renders"], 1)
check("the failure is not in the allowance either",
      [r for r in quotas.status(MONTH) if r["key"] == "elevenlabs"][0]["used"], 975)

section("The monthly allowance still works the way the older providers do")
check("under the warning mark",
      [r for r in quotas.status(MONTH) if r["key"] == "elevenlabs"][0]["state"], "ok")
quotas.record("elevenlabs", module="radio_promo", units=95_000)
row = [r for r in quotas.status(MONTH) if r["key"] == "elevenlabs"][0]
check("past the warning mark", row["state"], "warn")
check("the plan allowance is the one from the environment", row["limit"], 100_000)

section("Cached work is free, and is reported so the saving is visible")
clear_log()
quotas.record("elevenlabs", module="radio_promo", units=500)
quotas.record("elevenlabs", module="radio_promo", units=500, cached=True)
row = [r for r in quotas.status(MONTH) if r["key"] == "elevenlabs"][0]
check("cached characters are not billed", row["used"], 500)
check("cached characters are still shown", row["cached"], 500)


# ----------------------------------------------------- 5. absent is not zero
section("A provider nothing recorded reads 'not measured', never zero")
clear_log()
for est in quotas.estimates(MONTH):
    check(f"{est['key']} with no rows", est["state"], "not_measured")
check("and the money is not asserted either",
      quotas.elevenlabs_estimate(MONTH)["estimated_cost"], 0.0)


# ------------------------------------------------------------- 6-7. Cloudinary
section("Cloudinary attributes bytes to the module that sent them")
clear_log()
quotas.record_asset(module="blog_images", nbytes=2_000_000, detail="a")
quotas.record_asset(module="blog_images", nbytes=3_000_000, detail="b")
# A rendered commercial, which is where the gigabytes actually come from.
quotas.record_asset(module="commercial_builder", nbytes=1_200_000_000, detail="c")
quotas.record_asset(module="image_creator", kind="delete", nbytes=0, detail="d")

est = quotas.cloudinary_estimate(MONTH)
check("every operation counted", est["measured"]["operations"], 4)
check("uploads and deletes told apart", est["measured"]["deletes"], 1)
check("bytes summed", est["measured"]["bytes_added"], 1_205_000_000)
# By bytes, not by call count: three blog images and one video is four
# operations either way, and only one of them is the bill.
check("the module holding the bytes is named first",
      list(est["by_module"])[0], "commercial_builder")
check("its share is its own bytes",
      est["by_module"]["commercial_builder"]["bytes"], 1_200_000_000)
close("storage credits added", est["measured"]["storage_credits_added"], 1.205, 0.0005)

section("A few megabytes is thousandths of a credit, and must not round to zero")
close("small uploads keep their precision",
      est["by_module"]["blog_images"]["mb"], 5.0, 0.05)
clear_log()
quotas.record_asset(module="blog_images", nbytes=5_500_000)
close("5.5 MB is 0.0055 credits, not 0.0",
      quotas.cloudinary_estimate(MONTH)["measured"]["storage_credits_added"],
      0.0055, 0.00005)

section("Cloudinary is not priced in money unless a rate is configured")
clear_log()
quotas.record_asset(module="commercial_builder", nbytes=1_200_000_000)
est = quotas.cloudinary_estimate(MONTH)
check("no invented dollar figure", est["estimated_cost"], None)
check("but credits are reported regardless",
      est["measured"]["storage_credits_added"], 1.2)
os.environ["CLOUDINARY_USD_PER_CREDIT"] = "0.40"
priced = quotas.cloudinary_estimate(MONTH)
close("priced once told the rate", priced["estimated_cost"], 0.48)
os.environ.pop("CLOUDINARY_USD_PER_CREDIT")
check("and the credits did not change with the price",
      priced["measured"]["storage_credits_added"],
      est["measured"]["storage_credits_added"])


# ----------------------------------------------------------------- 8-10. Google
section("A Google call is filed by URL, because one helper calls four APIs")
cases = [
    ("https://googleads.googleapis.com/v25/customers/1/googleAds:search", "ads"),
    ("https://tagmanager.googleapis.com/tagmanager/v2/accounts", "gtm"),
    ("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", "ga4"),
    ("https://www.googleapis.com/webmasters/v3/sites", "gsc"),
    ("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", "gbp"),
    ("https://www.googleapis.com/webfonts/v1/webfonts", "fonts"),
    ("https://oauth2.googleapis.com/token", "oauth"),
    ("https://openidconnect.googleapis.com/v1/userinfo", "oauth"),
]
for url, want in cases:
    check(f"{url.split('//')[1][:38]:<38} → {want}", quotas.google_api_of(url), want)

section("Google quota is a per-day number, so it is compared per day")
clear_log()
for _ in range(12_100):
    quotas.record_google(cases[0][0], module="ads_builder")
g = quotas.google_estimate(MONTH)
ads = [a for a in g["apis"] if a["key"] == "ads"][0]
check("today's calls", ads["today"], 12_100)
check("against the daily ceiling", ads["daily_quota"], 15_000)
# 12,100 of 15,000 is 80.7% — past the warning mark and not yet over.
check("past 80% warns", ads["state"], "warn")
check("busiest day is named", ads["busiest_day"], TODAY)
quotas.record_google(cases[1][0], module="google_finder")
g = quotas.google_estimate(MONTH)
gtm = [a for a in g["apis"] if a["key"] == "gtm"][0]
check("a quiet API stays ok", gtm["state"], "ok")
ga4 = [a for a in g["apis"] if a["key"] == "ga4"]
check("an API with no published ceiling is listed", bool(ga4), True)
check("...and not measured against an invented one", ga4[0]["daily_quota"], None)
check("...and says so rather than showing ok", ga4[0]["state"], "not_measured")

section("A refusal spends the quota, so it is counted per API and not only once")
# One aggregate "failed" for every Google call this month cannot say that Tag
# Manager is refusing a third of its requests while Analytics is fine — and
# that rate is the whole early warning, because a 429 spends the daily quota
# exactly as a useful call does and returns nothing for it. On the live
# service a fixed pace had Tag Manager 429ing on very nearly every first
# attempt, and the only place it showed was the raw activity log.
clear_log()
for _ in range(6):
    quotas.record_google(cases[1][0], module="google_finder")
for _ in range(2):
    quotas.record_google(cases[1][0], module="google_finder", ok=False)
quotas.record_google(cases[2][0], module="google_finder")
g = quotas.google_estimate(MONTH)
gtm = [a for a in g["apis"] if a["key"] == "gtm"][0]
ga4 = [a for a in g["apis"] if a["key"] == "ga4"][0]
check("refusals are counted against the API that made them", gtm["failed"], 2)
check("...as a share of what that API sent, not of what worked",
      gtm["failed_percent"], 25)
check("...and a quiet API is not tarred with them", ga4["failed"], 0)
# Zero refusals is a real answer and must not read as a suppressed number.
check("...which is a measured nought, not a blank", ga4["failed_percent"], 0)
check("the total still adds up", g["measured"]["failed"], 2)


section("Google costs nothing, and the page says so instead of leaving a blank")
clear_log()
for _ in range(12_100):
    quotas.record_google(cases[0][0], module="ads_builder")
quotas.record_google(cases[1][0], module="google_finder")
g = quotas.google_estimate(MONTH)
check("zero, asserted", g["estimated_cost"], 0.0)
check("no runaway projection either", g["projected_month_end"], 0.0)
check("a refused call still spent its quota", g["measured"]["calls"], 12_101)


# ------------------------------------------------------------ 11. blind spots
section("A call site that spends without recording is named, not missed")
blind = quotas.untracked_provider_calls(force=True)
# Pinned deliberately. A provider that quietly leaves this list is a spend
# nothing counts any more, and the whole point of the scanner is that the gap
# is named rather than silent — so growing it is an edit somebody makes on
# purpose, which is what this assertion forces.
#
# The last three arrived together: HeyGen, Runway and Creatomate all bill per
# generation, were recorded nowhere, and had no marker here — so no check
# could ever have named them, which is why it stood.
check("every provider is scanned", sorted(blind),
      ["brandfetch", "cloudinary", "creatomate", "elevenlabs", "google",
       "heygen", "runway"])
# This is the point of the check: the repository is expected to be clean, and
# the moment a new module calls one of these without recording it, this fails
# here and on /api/integrity rather than quietly understating the bill.
for provider, found in blind.items():
    check(f"no unrecorded {provider} call sites",
          [f["file"] for f in found], [])


# ---------------------------------------------- 11b. and OpenAI, per call site
section("The OpenAI check asks per call site, not per file")
# It exempted the whole file the moment `from hub import ai` appeared anywhere
# in it. Image Creator's two text routes go through a helper that records, and
# its image route posted straight to /v1/images/generations and recorded
# nothing -- so every image it generated was billed at the per-image rate and
# invisible on the usage page, behind a check reporting the module clean. That
# is the `for_module(` failure one provider over: the string satisfying the
# check.
check("the repository has no unrecorded OpenAI call site",
      quotas.untracked_openai_modules(), [])

# Handed the shape that was live, it must say so -- a check that has only ever
# been green is one nobody can trust.
_SILENT_IMAGE = """
import requests

def generate(prompt):
    r = requests.post("https://api.openai.com/v1/images/generations", json={})
    return r.json()

def rewrite(text):
    from hub import ai
    r = requests.post("https://api.openai.com/v1/chat/completions", json={})
    ai.note_usage("m", r.json())
    return r.json()
"""
check("a silent image call in a file that records elsewhere is named",
      quotas.openai_spend_unrecorded(_SILENT_IMAGE), True)
check("and the same call once it records is not",
      quotas.openai_spend_unrecorded(
          _SILENT_IMAGE.replace("    return r.json()\n\ndef rewrite",
                                "    note_usage('m', r.json())\n    return r.json()\n\ndef rewrite")),
      False)
check("a file that never reaches OpenAI is not asked",
      quotas.openai_spend_unrecorded("x = 1\n"), False)
# An images response carries no usage block, so the model name is what makes
# the spend priceable at all -- openai_cost() prices anything named gpt-image*
# per image, and without the name there is nothing to price.
_img = pathlib.Path(ROOT, "modules", "image_creator", "app.py").read_text()
check("Image Creator passes the model when it records an image",
      "model=model" in _img and 'purpose="image"' in _img, True)
check("and records a refused call too, so a spent allowance is visible",
      "_note(False)" in _img, True)

# Brandfetch joined the table late, and the two things that make it worth
# having are the two it could most easily get wrong.
section("The Brandfetch scan bites, and does not cry wolf on a key check")
_bf = quotas._PROVIDER_MARKERS["brandfetch"]

# A module that looks a client up and records nothing is the whole point.
check("a direct lookup with no recording is caught",
      _bf["calls"]("r = requests.get('https://api.brandfetch.io/v2/brands/' + d)")
      and not any(m in "r = requests.get('https://api.brandfetch.io/v2/brands/' + d)"
                  for m in _bf["recorded"]),
      True)
# Both of Brandfetch's published type routes are real. Matching one path would
# give a module a clean bill for using the other spelling.
check("and so is the explicit domain route",
      _bf["calls"]("requests.get(f'https://api.brandfetch.io/v2/brands/domain/{d}')"), True)
# Going through the shared lookup is what clears it.
check("a module on hub/brand_lookup.py is clear",
      any(m in "from hub import brand_lookup\nbrand_lookup.lookup(d)"
          for m in _bf["recorded"]), True)
# The sign-in health panel and diagnostics fetch Brandfetch's OWN domain to
# prove the key still works. Flagging that would put a finding nobody can act
# on in front of somebody from the day this landed, and a check that starts
# life red is one people switch off.
check("a key-validity probe is not a client lookup",
      _bf["calls"]("requests.get('https://api.brandfetch.io/v2/brands/brandfetch.com')"),
      False)

section("Image Creator asks what is stored before it spends a call")
# It used to fetch FIRST and consult the Hub's stored brand data only if the
# fetch came back empty -- so the store was a fallback for a failed call, and
# every search for a client already on file spent one of the plan's hundred
# monthly lookups on an answer the Hub was already holding.
from unittest import mock as _mock                            # noqa: E402
from hub import brand_lookup as _bl                           # noqa: E402
from modules.image_creator import assets as _ic               # noqa: E402

_PAYLOAD = {"name": "Icon Solar", "domain": "iconsolar.com",
            "logos": [{"type": "logo",
                       "formats": [{"src": "http://x/l.svg", "format": "svg"}]}],
            "colors": [{"hex": "#123456"}]}

with _mock.patch.object(_bl, "requests") as _rq, \
     _mock.patch("hub.seo.brand_for", return_value=_PAYLOAD):
    _out = _ic.brand_lookup("iconsolar.com", client="Icon Solar")
    check("a stored answer costs no call", _rq.get.call_count, 0)
    check("and is still the answer", _out.get("name"), "Icon Solar")
    check("with its logos", len(_out.get("logos") or []), 1)

_resp = _mock.Mock(status_code=200, ok=True)
_resp.json.return_value = dict(_PAYLOAD)
with _mock.patch.object(_bl, "requests") as _rq, \
     _mock.patch("hub.seo.brand_for", return_value=None), \
     _mock.patch("hub.seo.save_brandfetch") as _save, \
     _mock.patch.object(_bl, "_key", return_value="k"):
    _rq.get.return_value = _resp
    _rq.RequestException = Exception
    _ic.brand_lookup("iconsolar.com", client="Icon Solar")
    check("a miss goes to Brandfetch, once", _rq.get.call_count, 1)
    check("and what it paid for is kept against the client",
          _save.called and _save.call_args.kwargs.get("client"), "Icon Solar")

# A cache hit answers with no key at all, which this function has always
# promised, so "not configured" is only right when nothing was stored either.
with _mock.patch.object(_bl, "configured", return_value=False), \
     _mock.patch("hub.seo.brand_for", return_value=None):
    check("the unconfigured wording survives the move",
          "nothing is cached" in (_ic.brand_lookup("iconsolar.com").get("error") or ""), True)
check("and so does the empty-query one",
      _ic.brand_lookup("").get("error"), "Enter a company name or domain.")


section("The Suite Panel stores what Brandfetch returned, not its own reshape")
# This is the one that was losing data rather than money. Every other caller
# stores the RAW Brandfetch payload, which is the shape hub/client_brand.py
# walks -- `logos` as a list of objects each carrying `formats`. The Suite
# Panel stored a bare `logo` URL string and no `logos` key at all, keyed on the
# domain, so a lookup here overwrote a good payload with one the Client 360
# brand card cannot read: colours, and no logo, silently.
from hub import client_brand as _cb                            # noqa: E402
import importlib as _il                                        # noqa: E402
_sp = _il.import_module("modules.suite_panel.app")

_RAW = {"name": "Icon Solar", "domain": "iconsolar.com",
        "logos": [{"type": "logo", "theme": "light",
                   "formats": [{"src": "http://x/l.png", "format": "png"}]}],
        "colors": [{"hex": "#123456", "type": "primary"}]}
_RESHAPED = {"name": "Icon Solar", "domain": "iconsolar.com",
             "logo": "http://x/l.png", "icon": None,
             "colors": [{"hex": "#123456", "type": "primary"}]}

with _mock.patch("hub.seo.brand_for", return_value=_RAW):
    check("the raw shape puts a logo on the client's card",
          len(_cb.brand_kit("Icon Solar", "iconsolar.com").get("logos") or []), 1)
with _mock.patch("hub.seo.brand_for", return_value=_RESHAPED):
    _k = _cb.brand_kit("Icon Solar", "iconsolar.com")
    check("the reshaped one puts none there — colors, and no logo",
          (len(_k.get("logos") or []), len(_k.get("colors") or [])), (0, 1))

_c = _sp.app.test_client()
with _mock.patch.object(_bl, "lookup", return_value={"found": True, "payload": _RAW}), \
     _mock.patch("hub.seo.save_brandfetch") as _save:
    _r = _c.get("/api/brand?domain=iconsolar.com&client=Icon+Solar")
    check("so the route saves nothing of its own", _save.called, False)
    check("and still answers in its own format", _r.get_json().get("logo"), "http://x/l.png")

# The status codes this panel's script branches on, each from the answer
# lookup() actually gives. 429 keeps its own wording because "try again later"
# is different advice from "the key was refused".
for _name, _ret, _want in (
    ({"found": False, "unconfigured": True}, None, 400),
    ({"found": False, "note": "Nothing is published for iconsolar.com."}, None, 404),
    ({"found": False, "note": "The brand lookup answered HTTP 429."}, None, 429),
    ({"found": False, "refused": True, "note": "refused our key"}, None, 502),
):
    with _mock.patch.object(_bl, "lookup", return_value=_name):
        check(f"status {_want} survives the move",
              _c.get("/api/brand?domain=iconsolar.com").status_code, _want)


section("The integrity page reports the same scan, from the same code")
from hub import integrity                                # noqa: E402
keys = [c[0] for c in integrity.CHECKS]
check("the check is registered", "untracked_provider_usage" in keys, True)
group = [g for g in integrity.run()["groups"]
         if g["key"] == "untracked_provider_usage"][0]
check("and agrees with the scan above", group["count"],
      sum(len(v) for v in blind.values()))


# --------------------------------------------------------- 12-13. the contract
section("Every view reads the activity log once, together")
clear_log()
quotas.record_tts("x" * 100, module="radio_promo", model="eleven_multilingual_v2")
quotas.record_asset(module="blog_images", nbytes=1000)
quotas.record_google(cases[0][0], module="ads_builder")
reads = []
_real_read = quotas.audit.read


def counting_read(*a, **kw):
    reads.append(kw.get("module"))
    return _real_read(*a, **kw)


quotas.audit.read = counting_read
try:
    summary = quotas.summary(MONTH)
finally:
    quotas.audit.read = _real_read
# One pass for the quota ledger and one for the OpenAI rows, which are a
# different module in the log. Four separate scans of a file that is allowed
# to reach 64 MB is what this replaced.
check("one pass over the quota ledger", reads.count("quota"), 1)
check("one pass over the AI rows", reads.count("ai"), 1)

section("The API still carries what the page reads off it")
check("estimates are present", sorted(e["key"] for e in summary["estimates"]),
      ["cloudinary", "elevenlabs", "google"])
check("the OpenAI card's data is untouched", "openai" in summary, True)
check("the allowance rows still include the older providers",
      {"brandfetch", "insites", "removebg"} <= {q["key"] for q in summary["quotas"]},
      True)
check("and now the new ones too",
      {"elevenlabs", "cloudinary", "google"} <= {q["key"] for q in summary["quotas"]},
      True)
for est in summary["estimates"]:
    for field in ("key", "label", "month", "state", "measured", "account",
                  "estimated_cost", "by_module", "caveat", "untracked"):
        check(f"{est['key']} carries {field}", field in est, True)
check("no provider counter fetched unless asked",
      [e["account"] for e in summary["estimates"]], [None, None, None])
check("live is reported honestly", summary["live"], False)

section("The provider's own counter degrades to a reason, never to a number")
os.environ.pop("ELEVENLABS_API_KEY", None)
quotas._account_cache.clear()
acct = quotas.elevenlabs_account()
check("unavailable, and says why", acct["available"], False)
check("no zero standing in for it", "used" in acct, False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
