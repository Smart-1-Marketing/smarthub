"""Live competitor ad intelligence — the scaffolding, with no vendor behind it.

No provider has been chosen or paid for, so the state this ships in is the one
that matters most and is asserted hardest: **unconfigured, the feature is
invisible.** `research_competitors()` returns exactly what it returned before,
with no third key on it, and nothing on any screen promises a client a
competitive picture the Hub cannot produce.

The rest is the shape a vendor slots into: absent is not empty, a verified name
never merges into the model's guesses, every row carries its source, and
nothing in the module raises whatever the feed does.

    python3 test_ad_intel.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

for var in ("AD_INTEL_PROVIDER", "AD_INTEL_API_KEY"):
    os.environ.pop(var, None)

from modules.ads_builder import ad_intel, campaign_ai   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


MODEL_ANSWER = {
    "named": [{"name": "Riverside Roofing", "note": "the client named them"}],
    "researched": [{"name": "Apex Exteriors", "why": "bids on the same terms",
                    "confidence": "Medium"}],
    "implications": ["Brand terms are cheap here."],
    "brandTermAdvice": "Bid on their brand.",
}

CAMPAIGN = {"businessName": "Northside Roofing",
            "websiteUrl": "https://www.northsideroofing.com/repair",
            "sector": "Home Services / Trades", "intake": {}}


def with_stubbed_model(fn):
    real = campaign_ai._chat
    campaign_ai._chat = lambda *a, **k: dict(MODEL_ANSWER)
    try:
        return fn()
    finally:
        campaign_ai._chat = real


def run():
    print("\nCompetitor ad intelligence scaffolding\n" + "=" * 60)

    # ------------------------------------------------------ the shipped state
    print("\nUnconfigured, it is invisible")
    check("no provider set means we did not look",
          ad_intel.verified_competitor_data("northsideroofing.com") is None)
    check("and the status says which kind of unconfigured",
          ad_intel.status()["state"] == "not_configured", ad_intel.status())
    check("naming both variables that would turn it on",
          "AD_INTEL_PROVIDER" in ad_intel.status()["note"]
          and "AD_INTEL_API_KEY" in ad_intel.status()["note"])

    baseline = with_stubbed_model(lambda: campaign_ai.research_competitors(CAMPAIGN))
    check("research_competitors grows no third bucket",
          "verified" not in baseline, sorted(baseline))
    check("the client's own names are untouched",
          [n["name"] for n in baseline["named"]] == ["Riverside Roofing"], baseline["named"])
    check("so are the model's guesses",
          [n["name"] for n in baseline["researched"]] == ["Apex Exteriors"],
          baseline["researched"])
    check("and the note still says they are unverified",
          "not been" in baseline["note"] and "verified" in baseline["note"], baseline["note"])

    # ------------------------------------------------- the other three states
    print("\nWhy it is off is four answers, not a boolean")
    os.environ["AD_INTEL_PROVIDER"] = "nonesuch"
    check("a provider this Hub cannot talk to is named as that",
          ad_intel.status()["state"] == "unknown_provider", ad_intel.status())
    check("and it lists the ones it knows",
          "spyfu" in ad_intel.status()["note"], ad_intel.status()["note"])
    os.environ["AD_INTEL_PROVIDER"] = "spyfu"
    check("a known provider with no implementation says so, not 'no key'",
          ad_intel.status()["state"] == "not_built", ad_intel.status())
    check("nothing is fetched for it either",
          ad_intel.verified_competitor_data("northsideroofing.com") is None)

    # A provider that IS built, but has no key.
    ad_intel.PROVIDERS["spyfu"]["built"] = True
    try:
        check("a built provider with no key is 'no key'",
              ad_intel.status()["state"] == "no_key", ad_intel.status())
        os.environ["AD_INTEL_API_KEY"] = "k"
        check("and with one it is ready", ad_intel.status()["state"] == "ready",
              ad_intel.status())

        # --------------------------------------------- a feed that answers
        print("\nWhen a provider answers")
        real_fetch = ad_intel._fetch
        ad_intel._fetch = lambda provider, domain: {
            "observed": "2026-09-01",
            "competitors": [
                {"name": "Apex Exteriors", "domain": "apexexteriors.com",
                 "paid_keywords": 412, "estimated_monthly_spend": 8200},
                {"domain": "roofsrus.com"},          # named only by its domain
                {"nothing": "usable"},               # dropped, not crashed on
            ]}
        try:
            data = ad_intel.verified_competitor_data(
                "https://www.northsideroofing.com/repair")
            check("the URL resolves to a canonical domain",
                  data["domain"] == "northsideroofing.com", data["domain"])
            check("rows a vendor could not name are dropped rather than kept blank",
                  len(data["competitors"]) == 2, data["competitors"])
            check("every row says which provider claimed it",
                  all(r["source"] == "SpyFu" for r in data["competitors"]),
                  data["competitors"])
            check("and when it was observed",
                  all(r["observed"] == "2026-09-01" for r in data["competitors"]))
            check("a feed that answered with rows is measured",
                  data["state"] == "measured", data["state"])

            result = with_stubbed_model(
                lambda: campaign_ai.research_competitors(CAMPAIGN))
            check("now the third bucket appears", "verified" in result, sorted(result))
            check("and the model's two buckets are byte-for-byte what they were",
                  result["named"] == baseline["named"]
                  and result["researched"] == baseline["researched"],
                  (result["named"], result["researched"]))
            check("a verified name is never folded into the model's guesses",
                  all(r["name"] != "Apex Exteriors" or r.get("why")
                      for r in result["researched"])
                  and [n["name"] for n in result["researched"]] == ["Apex Exteriors"],
                  result["researched"])

            # ------------------------- an index that simply has nothing on them
            print("\nAn empty index is a finding; a failure is not")
            ad_intel._fetch = lambda provider, domain: {"competitors": []}
            empty = ad_intel.verified_competitor_data("northsideroofing.com")
            check("a provider that answered with nothing says so",
                  empty is not None and empty["state"] == "none_in_index", empty)
            check("which is not the same value as never having looked",
                  empty != ad_intel.verified_competitor_data(""), empty)

            def boom(provider, domain):
                raise RuntimeError("the feed is down")
            ad_intel._fetch = boom
            check("a feed that raises costs the bucket, not the proposal",
                  ad_intel.verified_competitor_data("northsideroofing.com") is None)
            result = with_stubbed_model(
                lambda: campaign_ai.research_competitors(CAMPAIGN))
            check("and research_competitors still answers, without the bucket",
                  "verified" not in result and result["named"] == baseline["named"],
                  sorted(result))
        finally:
            ad_intel._fetch = real_fetch
    finally:
        ad_intel.PROVIDERS["spyfu"]["built"] = False
        os.environ.pop("AD_INTEL_PROVIDER", None)
        os.environ.pop("AD_INTEL_API_KEY", None)

    # ------------------------------------------------------ no live API here
    print("\nThe test itself reaches nothing")
    source = (ROOT / "modules/ads_builder/ad_intel.py").read_text()
    check("the module makes no HTTP call of its own yet",
          "requests." not in source and "urlopen" not in source)
    check("_fetch is the one place a vendor's HTTP would live",
          "NotImplementedError" in source)
    check("and every provider ships not built",
          all(not p["built"] for p in ad_intel.PROVIDERS.values()),
          {k: p["built"] for k, p in ad_intel.PROVIDERS.items()})

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
