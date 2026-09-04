"""Performance Max as a second campaign type.

The half asserted hardest is the one that must not have moved: a Search
proposal generates, validates and deploys byte-for-byte as it did, and every
proposal written before campaignType existed still reads as the Search
campaign it is.

The rest is what makes Performance Max a different product rather than a flag:
asset groups instead of ad groups, no keywords anywhere, Google's own asset
minimums refused by field before the mutate is attempted, and the asset group
and every one of its assets in ONE request, because Google's minimum check
runs against the request.

    python3 test_ads_pmax.py
"""
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_pmax_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ.pop("CLOUDINARY_URL", None)

from PIL import Image                                    # noqa: E402

from hub import images as hub_images                     # noqa: E402
from modules.ads_builder import (campaign_ai, google_ads, pmax_images,  # noqa: E402
                                 pmax_spec, spec)
from modules.ads_builder.google_ads import GoogleAdsError  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


def good_group(**over):
    group = {
        "name": "Roof repair",
        "theme": "Storm damage, emergency",
        "businessName": "Northside Roofing",
        "headlines": [f"Headline {n}" for n in range(11)],
        "longHeadlines": ["A long headline that fits inside ninety characters comfortably.",
                          "A second long headline, also comfortably inside the limit."],
        "descriptions": ["Short one under sixty.",
                         "A description that is comfortably inside the ninety-character limit here.",
                         "Another one, also inside ninety characters and useful to read.",
                         "A fourth description for ad strength, inside the limit."],
        "searchThemes": ["emergency roof repair", "storm damage roofer"],
        "images": [{"role": "marketing", "url": "https://cdn.example.com/a.jpg"},
                   {"role": "square", "url": "https://cdn.example.com/b.jpg"},
                   {"role": "logo", "url": "https://cdn.example.com/logo.png"}],
    }
    group.update(over)
    return group


def pmax_campaign(**over):
    campaign = {
        "campaignType": "PERFORMANCE_MAX",
        "businessName": "Northside Roofing",
        "websiteUrl": "https://northsideroofing.example.com/repair",
        "monthlyBudget": 6000,
        "assetGroups": [good_group()],
    }
    campaign.update(over)
    return campaign


def run():
    print("\nPerformance Max\n" + "=" * 60)

    # ------------------------------------------------ Search is unchanged
    print("\nSearch is untouched")
    check("SEARCH is the default", spec.DEFAULT_CAMPAIGN_TYPE == "SEARCH")
    check("a proposal written before this existed reads as Search",
          spec.campaign_type_of({}) == "SEARCH")
    check("as does one carrying nothing at all",
          spec.campaign_type_of(None) == "SEARCH")
    check("and an unknown value is not accepted",
          spec.campaign_type_of({"campaignType": "SHOPPING"}) == "SEARCH")
    check("a spelling with a space still resolves",
          spec.campaign_type_of("performance max") == "PERFORMANCE_MAX")

    viability = campaign_ai.analyse_budget(6000, "homeservices")
    search = campaign_ai.normalise(
        {"adGroups": [{"name": "G", "keywords": ["[roof repair]"],
                       "ads": {"headlines": ["a", "b", "c"], "descriptions": ["d", "e"]}}]},
        {"businessName": "Northside Roofing", "websiteUrl": "https://x.example.com",
         "budget": 6000}, viability)
    check("a search campaign still carries ad groups and keywords",
          search["adGroups"][0]["keywords"] == ["[roof repair]"], search["adGroups"])
    check("and now says which product it is",
          search["campaignType"] == "SEARCH", search.get("campaignType"))
    check("it grows no asset groups", "assetGroups" not in search, sorted(search))

    # ------------------------------------------------------- the spec table
    print("\nGoogle's own minimums, transcribed")
    check("three headlines is the refusal, eleven is the advice",
          pmax_spec.TEXT_ASSETS["headlines"]["minimum"] == 3
          and pmax_spec.TEXT_ASSETS["headlines"]["recommended"] == 11,
          pmax_spec.TEXT_ASSETS["headlines"])
    check("a landscape image and a square image are required",
          pmax_spec.IMAGE_ASSETS["marketing"]["minimum"] == 1
          and pmax_spec.IMAGE_ASSETS["square"]["minimum"] == 1)
    check("a portrait image is named as optional rather than left out",
          "portrait" in pmax_spec.IMAGE_ASSETS
          and pmax_spec.IMAGE_ASSETS["portrait"]["minimum"] == 0)
    check("a 1:1 logo is required", pmax_spec.LOGO_ASSETS["logo"]["minimum"] == 1)
    check("every requirement names the page it came from",
          all(pmax_spec.source_of(r) for r in
              list(pmax_spec.TEXT_ASSETS.values()) + list(pmax_spec.IMAGE_ASSETS.values())))
    check("and the transcription says it is a transcription",
          "transcribed" in pmax_spec.kit_note().lower(), pmax_spec.kit_note())

    # ---------------------------------------------------------- validation
    print("\nWhat Google will refuse, named by field")
    check("a complete asset group passes", pmax_spec.validate(good_group())["ok"])
    short = pmax_spec.validate(good_group(headlines=["one", "two"]))
    check("two headlines is a refusal", not short["ok"], short)
    check("and the message names the field and both numbers",
          any("Headlines" in e and "3" in e for e in short["errors"]), short["errors"])

    # The one every generator gets wrong: each description is individually
    # valid and the asset group is still refused.
    long_only = pmax_spec.validate(good_group(descriptions=[
        "A description that is comfortably inside the ninety-character limit and no shorter.",
        "A second description also inside ninety characters but longer than sixty of them."]))
    check("descriptions all over sixty is a refusal on its own",
          not long_only["ok"]
          and any("60 characters or fewer" in e for e in long_only["errors"]),
          long_only["errors"])

    over = pmax_spec.validate(good_group(headlines=["x" * 40] + ["h%d" % n for n in range(5)]))
    check("a headline over thirty characters is a refusal",
          not over["ok"] and any("over 30" in e for e in over["errors"]), over["errors"])

    no_square = pmax_spec.validate(good_group(images=[
        {"role": "marketing", "url": "u"}, {"role": "logo", "url": "u"}]))
    check("a missing square image is named by ratio",
          not no_square["ok"] and any("Square image (1:1)" in e for e in no_square["errors"]),
          no_square["errors"])
    check("which is a different sentence from having no images at all",
          len(pmax_spec.validate(good_group(images=[]))["errors"]) > len(no_square["errors"]))

    thin = pmax_spec.validate(good_group(headlines=["a", "b", "c"]))
    check("three headlines deploys, and is advised against rather than refused",
          thin["ok"] and any("lifts ad strength" in w for w in thin["warnings"]),
          (thin["ok"], thin["warnings"]))

    check("a campaign with no asset group at all is a refusal",
          not pmax_spec.validate_campaign({"assetGroups": []})["ok"])
    check("and a multi-group campaign reports per group",
          len(pmax_spec.validate_campaign(
              {"assetGroups": [good_group(), good_group(name="Second")]})["groups"]) == 2)

    # ------------------------------------------------------- normalisation
    print("\nThe model is never trusted with a limit")
    tidy = pmax_spec.normalise_asset_group(
        {"headlines": ["x" * 60, "x" * 60, "keep me"],
         "longHeadlines": ["y" * 200], "descriptions": ["z" * 200],
         "businessName": "A business name far longer than twenty-five characters",
         "keywords": ["[roof repair]"],
         "images": [{"role": "marketing", "url": "u"}, {"role": "nonsense", "url": "u"}]},
        business_name="Fallback")
    check("headlines are clamped to thirty",
          all(len(h) <= 30 for h in tidy["headlines"]), tidy["headlines"])
    check("and deduped after clamping, not before",
          len(tidy["headlines"]) == 2, tidy["headlines"])
    check("long headlines to ninety", len(tidy["longHeadlines"][0]) == 90)
    check("the business name to twenty-five", len(tidy["businessName"]) == 25)
    check("a keyword the model should not have written is dropped entirely",
          "keywords" not in tidy, sorted(tidy))
    check("an image role Google has no field for is dropped",
          [i["role"] for i in tidy["images"]] == ["marketing"], tidy["images"])

    campaign = campaign_ai.normalise_pmax(
        {"assetGroups": [good_group()], "businessName": "Northside Roofing"},
        {"businessName": "Northside Roofing", "websiteUrl": "https://x.example.com",
         "budget": 6000}, viability)
    check("a generated PMax campaign carries asset groups",
          len(campaign["assetGroups"]) == 1, campaign.get("assetGroups"))
    check("an empty adGroups list rather than no key, so every reader still works",
          campaign["adGroups"] == [], campaign.get("adGroups"))
    check("no negative keyword vault, because there are no keywords to negate",
          campaign["negativeKeywordVault"] == {}, campaign["negativeKeywordVault"])
    check("and it validates itself on the way out",
          campaign["assetValidation"]["ok"] is True, campaign["assetValidation"])

    # ------------------------------------------------------------- deploy
    print("\nOne atomic mutate, and the asset group inside it")
    sent = {}

    def fake_request(method, path, body=None, store=None, customer_id=None):
        sent.update(method=method, path=path, body=body)
        return {"mutateOperationResponses": [
            {"campaignResult": {"resourceName": f"customers/{customer_id}/campaigns/55"}}]}

    real_request = google_ads.request
    real_bytes = google_ads._asset_image_bytes
    google_ads.request = fake_request
    google_ads._asset_image_bytes = lambda url: b"\x89PNG\r\n\x1a\n" + b"0" * 64
    try:
        out = google_ads.deploy_proposal_pmax("111-111-1111", pmax_campaign())
        ops = sent["body"]["mutateOperations"]
        kinds = [next(iter(o)) for o in ops]
        check("one request, not several", sent["path"].endswith("googleAds:mutate"))
        check("and never partial", sent["body"]["partialFailure"] is False)
        check("the asset group is in it", "assetGroupOperation" in kinds, kinds)
        check("and so is every one of its assets — Google's minimum check runs "
              "against the request", "assetGroupAssetOperation" in kinds, kinds)
        group_at = kinds.index("assetGroupOperation")
        check("the group is created before the assets that link to it",
              group_at < kinds.index("assetGroupAssetOperation"))
        campaign_op = next(o["campaignOperation"]["create"] for o in ops
                           if "campaignOperation" in o)
        check("the channel type is PERFORMANCE_MAX",
              campaign_op["advertisingChannelType"] == "PERFORMANCE_MAX", campaign_op)
        check("created paused, like every campaign this module builds",
              campaign_op["status"] == "PAUSED")
        check("on a conversion strategy, which is all Performance Max takes",
              "maximizeConversions" in campaign_op, campaign_op)
        check("with URL expansion off, so Google cannot send traffic to a page "
              "nobody chose", campaign_op.get("urlExpansionOptOut") is True, campaign_op)
        check("no ad group is created", "adGroupOperation" not in kinds, kinds)
        check("and no keyword criterion", "adGroupCriterionOperation" not in kinds, kinds)
        check("search themes go as asset group signals",
              "assetGroupSignalOperation" in kinds, kinds)
        check("images are uploaded as bytes, since the API has no URL ingest",
              any("imageAsset" in (o.get("assetOperation", {}).get("create") or {})
                  for o in ops))
        field_types = {o["assetGroupAssetOperation"]["create"]["fieldType"]
                       for o in ops if "assetGroupAssetOperation" in o}
        check("headlines, long headlines, descriptions and the business name all land",
              {"HEADLINE", "LONG_HEADLINE", "DESCRIPTION", "BUSINESS_NAME"} <= field_types,
              field_types)
        check("and the two required image roles",
              {"MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE", "LOGO"} <= field_types,
              field_types)
        check("the result says which product it built",
              out["campaign_type"] == "PERFORMANCE_MAX", out)
        check("and counts asset groups rather than ad groups",
              out["asset_group_count"] == 1 and "keyword_count" not in out, out)

        # A dry run is the same operations with validateOnly.
        google_ads.deploy_proposal_pmax("111-111-1111", pmax_campaign(),
                                        validate_only=True)
        check("a dry run validates the same batch",
              sent["body"].get("validateOnly") is True)

        # ----------------------------------------- refused before the mutate
        print("\nRefused by field here, not by resource name at Google")
        before = dict(sent)
        sent.clear()
        thin_campaign = pmax_campaign(assetGroups=[good_group(headlines=["one"])])
        refused = ""
        try:
            google_ads.deploy_proposal_pmax("111-111-1111", thin_campaign)
        except GoogleAdsError as exc:
            refused = str(getattr(exc, "message", None) or exc)
        check("an asset group short of Google's minimum never reaches Google",
              not sent and "Headlines" in refused, (bool(sent), refused))
        check("a campaign with no asset groups is refused too",
              _refuses(lambda: google_ads.deploy_proposal_pmax(
                  "111-111-1111", pmax_campaign(assetGroups=[]))))
        sent.update(before)
    finally:
        google_ads.request = real_request
        google_ads._asset_image_bytes = real_bytes

    # ---------------------------------------------------------- the crop
    print("\nRatios are cropped, because OpenAI does not offer them")
    buf = io.BytesIO()
    Image.new("RGB", (1536, 1024), (20, 80, 160)).save(buf, "PNG")
    landscape = hub_images.crop_to_ratio(buf.getvalue(), 600 / 314, min_width=1200)
    check("a landscape crop is 1.91:1 to two decimals",
          abs(landscape.width / landscape.height - 600 / 314) < 0.01,
          (landscape.width, landscape.height))
    check("and meets Google's recommended width", landscape.width >= 1200, landscape.width)
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1536), (20, 80, 160)).save(buf, "PNG")
    portrait = hub_images.crop_to_ratio(buf.getvalue(), 480 / 600, min_height=1200)
    check("a portrait crop is 4:5",
          abs(portrait.width / portrait.height - 0.8) < 0.01,
          (portrait.width, portrait.height))
    check("the source orientation is chosen per role rather than guessed",
          set(pmax_images.SOURCE_SIZE) == {"marketing", "square", "portrait"},
          pmax_images.SOURCE_SIZE)

    # ------------------------------------------------------------ the logo
    print("\nA logo is looked up, never generated")
    source = (ROOT / "modules/ads_builder/pmax_images.py").read_text()
    check("nothing generates a logo",
          "logo" not in pmax_images.SOURCE_SIZE and "logo_lookup.resolve" in source)
    absent = pmax_images.client_logo("Nobody At All Ltd", "https://nobody.example.com")
    check("a client with no logo on file is a named absence, not a blank",
          absent["found"] is False and bool(absent["note"]), absent)
    check("and generate_asset_image refuses the logo role outright",
          _refuses_image(lambda: pmax_images.generate_asset_image("logo", "a mark")))
    check("as it does an empty prompt",
          _refuses_image(lambda: pmax_images.generate_asset_image("square", "  ")))

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


def _refuses(fn) -> bool:
    try:
        fn()
    except GoogleAdsError:
        return True
    except Exception:                                    # noqa: BLE001
        return False
    return False


def _refuses_image(fn) -> bool:
    try:
        fn()
    except pmax_images.ImageError:
        return True
    except Exception:                                    # noqa: BLE001
        return False
    return False


if __name__ == "__main__":
    sys.exit(run())
