"""The IO Builder's PDF downloads, and the per-product logic that feeds them.

    python3 test_io_downloads.py

Same shape as the other test files: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite mirror, so it never touches
/var/data or the real one.

## Why this file exists

The tool has three ways of producing a PDF and a wizard that decides, per
product, what goes on it -- and none of it had a persisted test. The bug that
started this pass (`customerRequirementItems()` calling a function that was
never defined, and a generic "confirm the creative files" line printed for
products with no design spec) was fixed and verified once, by hand, against a
throwaway Node harness -- and then nothing was left behind to catch it coming
back, which is exactly how the original bug went unnoticed for as long as it
did.

**Extracted rather than restated.** The rate card is ~90 hand-typed rows this
file does not own, so the harness below lifts the real `rateCard` /
`productConfig` / `productRequirements` / `creativeChecklistItems` /
`customerFriendlyNeeds` / `customerRequirementItems` / `buildPdfPayload` block
straight out of the template and runs it in node -- the same shape
`test_target_areas.py` already uses for the area helpers. A second, hand-typed
copy of the rate card here would drift from the real one the day either is
edited, and would have missed the actual bug: the failure was in how a real
product on the real card gets read, not in an invented fixture.

**Every product on the card, not a sample.** A handful of hand-picked products
proves nothing about the ninety-first. The regression this file guards against
-- a product whose creative spec is empty getting a fallback checklist line
anyway, or a product whose regex should match not matching it -- is exactly
the kind of thing that only shows up on the row nobody thought to try.

**The three PDF-producing endpoints are three different shapes of the same
risk.** `/api/download-requirements-pdf` builds and returns the bytes
directly and needs no Cloudinary; `/api/generate-client-pdf` and
`/api/generate-internal-pdf` build the same PDF, store it in Cloudinary and
file it into the client's gallery -- the write `_generate_named_pdf`'s own
comment says an IO PDF is finished client work; and `/api/generate-requirements-pdf`
does the storage half without the filing half and, per a repo-wide search, is
called by nothing at all -- an orphan route, not a route to remove: it costs
nothing sitting there, and deleting a route on the strength of "nothing calls
it today" is how the direct-download path would have been deleted the day it
was added, before anything called that either. Noted here rather than acted
on twice, in the same spirit `hub/config.py`'s ALIASES table keeps a spelling
nobody has typed yet: it is cheaper to know about than to have to rediscover.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1iodl_test_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "io-downloads-test-secret"
# Cloudinary is genuinely unset in this environment (and in a fresh dev
# checkout) -- Section 4 below asserts that real state rather than a
# simulated one, so the three provider env spellings are cleared explicitly
# rather than merely relying on them being absent already.
for _k in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
          "CLOUDINARY_API_SECRET"):
    os.environ.pop(_k, None)

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


import cloudinary                                                  # noqa: E402
import cloudinary.uploader                                         # noqa: E402
cloudinary.config(cloud_name=None, api_key=None, api_secret=None)

import modules.io_builder.app as io                                 # noqa: E402
client = io.app.test_client()
TEMPLATE = (ROOT / "modules" / "io_builder" / "templates" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
section("Every product on the real rate card, not a sample")
# ---------------------------------------------------------------------------
_start = TEMPLATE.index("const rateCard=[")
_end = TEMPLATE.index("async function generateStoredPDF")
JS_SLICE = TEMPLATE[_start:_end]

HARNESS = (
    # document has to exist BEFORE the slice runs: one real top-level line in
    # the wizard reads document.getElementById at module scope
    # (`const messages=document.getElementById("messages"),...`), and a const
    # declared after that point is in the temporal dead zone for the whole
    # script, not just for code that follows it textually.
    'const document = { addEventListener: function(){}, '
    'getElementById: function(){ return {}; } };\n'
    + JS_SLICE +
    r"""
const results = {};
const allProducts = Object.keys(productConfig);
results.productCount = allProducts.length;

// 1. Nothing throws, for any product actually on the card -- the shape of
//    the original bug: customerRequirementItems() called a function that did
//    not exist, and it threw the moment any product was selected.
const errors = [];
allProducts.forEach(function(p){
  try {
    productRequirements(p);
    creativeChecklistItems(p);
    customerFriendlyNeeds(p);
  } catch(e) { errors.push(p + ": " + e.message); }
});
results.errors = errors;

// 2. A product with no design spec gets no checklist item at all -- not the
//    old generic "confirm the creative files" fallback, which asked every
//    product to supply creative whether or not one was ever going to exist.
const noSpec = allProducts.filter(function(p){
  return productRequirements(p).creative.length === 0;
});
results.noSpecCount = noSpec.length;
results.noSpecAllEmpty = noSpec.every(function(p){
  return creativeChecklistItems(p).length === 0;
});
// Phone numbers and toll-free numbers are the named example of a product
// with nothing to check -- confirm at least one of them is actually in the
// no-spec set, so this assertion is not vacuously true against an empty list.
results.phoneNumberHasNoSpec = allProducts.some(function(p){
  return /phone number|toll free/i.test(p) && productRequirements(p).creative.length === 0;
});

// 3. A product that DOES carry a spec still gets its checklist.
const withSpec = allProducts.filter(function(p){
  return productRequirements(p).creative.length > 0;
});
results.withSpecCount = withSpec.length;
results.withSpecAllNonEmpty = withSpec.every(function(p){
  return creativeChecklistItems(p).length > 0;
});

// 4. "Tik Tok" (a space, the rate card's own spelling) is recognized by the
//    same regex "tiktok" (no space) is -- the fix for the exact gap that
//    used to leave this product with no creative, access or setup items at
//    all, silently, because /tiktok/ alone never matched a space.
const tiktokProducts = allProducts.filter(function(p){ return /tik ?tok/i.test(p); });
results.tiktokProductCount = tiktokProducts.length;
results.tiktokHasCreative = tiktokProducts.every(function(p){
  return productRequirements(p).creative.length > 0;
});
results.tiktokCustomerNeedsMatch = tiktokProducts.every(function(p){
  return customerFriendlyNeeds(p).some(function(c){ return /platform/i.test(c); });
});

// 5. Selecting a representative mix -- two products with a design spec, one
//    without -- and building both PDF payloads the way the wizard does.
state.client = "Harness Test Co";
state.industry = "Home Services";
state.creativeSource = "Smart 1 is building the creative";
state.selected = withSpec.slice(0, 2).concat(noSpec.slice(0, 1));
state.items = state.selected.map(function(p){ return {product:p, budget:500, campaignBudget:3000}; });
state.creativeAssets = [{product: state.selected[0], fileName:"banner.jpg",
                        resourceType:"image", status:"approved", evergreen:false}];

results.customerRequirements = customerRequirementItems();
results.internalSections = internalRequirementSections()
  .map(function(s){ return {title:s.title, itemCount:s.items.length}; });

const clientPayload = buildPdfPayload("client");
const internalPayload = buildPdfPayload("internal");
results.clientDocType = clientPayload.documentType;
results.internalDocType = internalPayload.documentType;
results.clientHasRequirements = clientPayload.customerRequirements.length > 0;
results.internalHasSections = internalPayload.internalRequirements.length === state.selected.length;
results.monthlySpendFormatted = clientPayload.monthlySpendFormatted;
results.totalCampaignBudgetFormatted = clientPayload.totalCampaignBudgetFormatted;
// A creative asset is labeled with the product's real name, not its raw
// rate-card key -- buildPdfPayload() maps productLabel from productConfig.
results.creativeAssetProductLabel = clientPayload.creativeAssets[0].productLabel;
results.creativeAssetProductKey = clientPayload.creativeAssets[0].product;

console.log(JSON.stringify(results));
"""
)

_js_path = os.path.join(TMP, "harness.js")
Path(_js_path).write_text(HARNESS, encoding="utf-8")
try:
    _out = subprocess.run(["node", _js_path], capture_output=True, text=True,
                          timeout=30, check=True).stdout
    js = json.loads(_out)
    check("the real rate card has several dozen products", js["productCount"] > 50)
    check("no product throws building its checklist or customer needs",
          js["errors"], [])
    check("some products carry no design spec", js["noSpecCount"] > 0)
    check("  a phone number or toll-free line is one of them",
          js["phoneNumberHasNoSpec"])
    check("  and every one of them gets an empty checklist, not a fallback line",
          js["noSpecAllEmpty"])
    check("most products do carry a design spec", js["withSpecCount"] > 0)
    check("  and every one of them gets a non-empty checklist",
          js["withSpecAllNonEmpty"])
    check("TikTok is on the card", js["tiktokProductCount"] > 0,
          js["tiktokProductCount"])
    check("  and it now gets creative requirements rather than none",
          js["tiktokHasCreative"])
    check("  the customer-facing needs recognize it as a social platform",
          js["tiktokCustomerNeedsMatch"])
    check("selecting products builds a real customer requirements list",
          len(js["customerRequirements"]) > 6)
    check("and exactly one internal section per selected product",
          js["internalHasSections"])
    check("buildPdfPayload('client') is stamped as such",
          js["clientDocType"], "client")
    check("buildPdfPayload('internal') is stamped as such",
          js["internalDocType"], "internal")
    check("the client payload carries the requirements list",
          js["clientHasRequirements"])
    check("spend is formatted for the document", js["monthlySpendFormatted"],
          "$1,500.00")
    check("and so is the campaign total", js["totalCampaignBudgetFormatted"],
          "$9,000.00")
    check("a creative asset is labeled with the product's name",
          js["creativeAssetProductLabel"] != js["creativeAssetProductKey"])
except FileNotFoundError:
    print("  skip node is not installed -- the per-product checklist harness "
         "could not run")
except subprocess.CalledProcessError as exc:
    check("the checklist harness runs", False, exc.stderr[:600])


# ---------------------------------------------------------------------------
section("The direct download needs no Cloudinary")
# ---------------------------------------------------------------------------
FULL_STATE = {
    "client": "Riverside HVAC", "orderNumber": "20777", "salesContact": "Jamie Rep",
    "salesEmail": "jamie@smart1marketing.com", "url": "https://riversidehvac.example.com",
    "start": "2026-10-01", "end": "2027-03-31", "sameDates": True,
    "creativeSource": "Smart 1 is building the creative",
    "monthlySpendFormatted": "$5,750.00", "totalCampaignBudgetFormatted": "$34,500.00",
    "targetAreas": [
        {"name": "Carmel showroom", "type": "City/ZIP + Radius",
         "origin": "Carmel, IN", "radius": 10, "zips": "46032, 46033"},
        {"name": "", "type": "DMA", "dma": "Indianapolis"},
    ],
    "brandfetch": {"status": "found", "name": "Riverside HVAC",
                  "domain": "riversidehvac.example.com",
                  "description": "Residential HVAC services",
                  "logo": "", "colors": ["#14284b", "#f2f2f2"], "fonts": ["Inter"],
                  "links": [{"name": "Facebook", "url": "https://facebook.com/riversidehvac"}]},
    "creativeAssets": [{"product": "OUTREACH — RON (Run of Network)",
                        "productLabel": "RON (Run of Network)",
                        "fileName": "banner.jpg", "resourceType": "image",
                        "url": "", "status": "approved", "evergreen": False}],
    "customerRequirements": ["Approved logo, brand colors or brand guidelines...",
                             "Main offer or message, call to action..."],
    "internalRequirements": [{"title": "RON (Run of Network)",
                              "items": ["item a", "item b"]}],
    "trackingPlan": {"primaryConversion": "Phone call",
                     "secondaryConversions": ["Form fill"], "ga4": "Yes",
                     "gtm": "Yes", "callTracking": "Yes", "thankYouPage": "Yes",
                     "offlineImport": "No", "verifier": "Jamie"},
    "guardrailWarnings": [{"message": "Budget below the listed minimum for this product."}],
    "internalWarnings": ["Landing page missing a phone number."],
    "landingPageReviews": [{"product": "RON",
                            "url": "https://riversidehvac.example.com/promo",
                            "review": "Has a phone number and a form."}],
    "mediaMixRecommendation": {"summary": "Lead with programmatic display.",
                               "primary_product": "RON",
                               "supporting_products": ["Category"],
                               "suggested_test_budget": "$2,000/mo",
                               "minimum_run_length": "3 months"},
    "clientUploadUrl": "https://smart1.agency/tools/image-picker/pick/abc123",
}

_pdf_bytes = {}
for _doc_type in ("client", "internal"):
    r = client.post("/api/download-requirements-pdf",
                    json=dict(FULL_STATE, documentType=_doc_type))
    check(f"the {_doc_type} PDF downloads directly", r.status_code, 200)
    check("  as a real PDF", r.data[:5], b"%PDF-")
    check("  with the right content type", r.mimetype, "application/pdf")
    check("  offered as an attachment, not inline",
          "attachment" in (r.headers.get("Content-Disposition") or ""))
    check("  named for the order and the client",
          "20777" in (r.headers.get("Content-Disposition") or "") and
          "Riverside HVAC" in (r.headers.get("Content-Disposition") or ""))
    _pdf_bytes[_doc_type] = r.data

check("the internal document carries more than the customer sees "
     "(tracking, warnings, the media mix, the landing-page review)",
     len(_pdf_bytes["internal"]) > len(_pdf_bytes["client"]))

r = client.post("/api/download-requirements-pdf", json={"documentType": "nope"})
check("an unrecognized document type is refused rather than guessed at",
     r.status_code, 400)

# A payload with none of the computed sections still has to build -- a rep
# hitting this route directly (or a caller upstream of buildPdfPayload
# failing to compute them) must not get a 500 for a missing optional key.
r = client.post("/api/download-requirements-pdf",
                json={"client": "Bare Co", "orderNumber": "1", "documentType": "client"})
check("a minimal payload with none of the optional sections still builds",
     r.status_code, 200)
check("  and is still a real PDF", r.data[:5], b"%PDF-")


# ---------------------------------------------------------------------------
section("The stored PDF: Cloudinary upload, and filing into the client's gallery")
# ---------------------------------------------------------------------------
cloudinary.config(cloud_name="testcloud", api_key="testkey", api_secret="testsecret")

_upload_calls = []
_real_upload = cloudinary.uploader.upload


def _fake_upload(*args, **kwargs):
    _upload_calls.append(kwargs)
    pid = kwargs.get("public_id", "x")
    return {"secure_url": f"https://res.cloudinary.com/testcloud/{kwargs.get('resource_type','image')}/"
                          f"{kwargs.get('type','upload')}/v1/{pid}.pdf",
           "public_id": pid}


import modules.image_picker.filing as filing                        # noqa: E402
_real_file_asset = filing.file_asset
_filing_calls = []


def _fake_file_asset(**kwargs):
    _filing_calls.append(kwargs)
    return {"ok": True, "image": {}, "gallery_url": "/tools/image-picker/gallery/1"}


from hub import quotas                                              # noqa: E402
_real_record_asset = quotas.record_asset
_quota_calls = []


def _fake_record_asset(**kwargs):
    _quota_calls.append(kwargs)


cloudinary.uploader.upload = _fake_upload
filing.file_asset = _fake_file_asset
quotas.record_asset = _fake_record_asset
try:
    ORDER = {"client": "Riverside HVAC", "orderNumber": "20500",
            "start": "2026-10-01", "salesContact": "Jamie"}

    result, title = io._store_requirements_pdf(ORDER, "client")
    check("the PDF is uploaded as a raw asset, not an image",
          _upload_calls[-1].get("resource_type"), "raw")
    check("  with the ordinary (non-authenticated) delivery type by default",
          _upload_calls[-1].get("type"), "upload")
    check("  filed under the client/tool/date/IO/project convention",
          _upload_calls[-1].get("folder"),
          "client-assets/riverside-hvac/io-builder/"
          + str(__import__("datetime").date.today()) + "/io-20500/project-io-documents")
    check("the upload returns a usable URL", result.get("secure_url", "").startswith("https://"))

    r = client.post("/api/generate-client-pdf", json=ORDER)
    check("the client PDF route succeeds once Cloudinary is configured",
          r.status_code, 200)
    body = r.get_json()
    check("  and reports ok", body.get("ok"), True)
    check("  with a URL", body.get("url", "").startswith("https://"))
    check("  it is filed into the client's gallery",
          bool(_filing_calls), True)
    filed = _filing_calls[-1]
    check("  under the io_creative kind, so it is not confused with an upload",
          filed.get("kind"), "io_creative")
    check("  attributed to the tool that made it",
          filed.get("tool"), "io-builder")
    check("  and to the order it belongs to",
          filed.get("io_number"), "20500")
    check("  attributed to the rep who submitted it, not a bare 'system'",
          filed.get("saved_by"), "Jamie")
    check("  never pushed to Suite from here -- the IO submit route is the "
         "one write path for that",
          filed.get("push_to_suite"), False)
    check("the Cloudinary credit is recorded for the usage page",
          any(c.get("module") == "io_builder" for c in _quota_calls))

    _filing_calls.clear()
    r = client.post("/api/generate-internal-pdf", json=ORDER)
    check("the internal PDF route succeeds too", r.status_code, 200)
    check("  and is filed separately from the client copy",
          bool(_filing_calls), True)

    # SECURE_INTERNAL_PDF: the internal PDF (fees, margins, strategy) is
    # served through a signed URL when this is on; the client PDF never is.
    os.environ["SECURE_INTERNAL_PDF"] = "true"
    try:
        _result_internal, _ = io._store_requirements_pdf(ORDER, "internal")
        check("with SECURE_INTERNAL_PDF on, the internal PDF is delivered "
             "as an authenticated, signed asset",
             _upload_calls[-1].get("type"), "authenticated")
        check("  and the URL it hands back is actually signed",
             "authenticated" in _result_internal.get("secure_url", ""))
        _result_client, _ = io._store_requirements_pdf(ORDER, "client")
        check("the client PDF is unaffected by that flag",
             _upload_calls[-1].get("type"), "upload")
    finally:
        os.environ.pop("SECURE_INTERNAL_PDF", None)

    # The orphan route: reachable, storage-only, no gallery filing. Noted in
    # this file's own docstring rather than fixed to match its siblings --
    # nothing calls it, so there is no submitted order at risk either way.
    _filing_calls.clear()
    r = client.post("/api/generate-requirements-pdf", json=dict(ORDER, documentType="client"))
    check("the un-called generate-requirements-pdf route still answers",
          r.status_code, 200)
    body = r.get_json()
    check("  with a url, a public_id and a filename",
          all(k in body for k in ("url", "public_id", "filename")))
    check("  and, unlike the named routes, files nothing into the gallery",
          _filing_calls, [])

    r = client.post("/api/generate-requirements-pdf", json={"documentType": "sideways"})
    check("an unrecognized document type is refused here too", r.status_code, 400)
finally:
    cloudinary.uploader.upload = _real_upload
    filing.file_asset = _real_file_asset
    quotas.record_asset = _real_record_asset


# ---------------------------------------------------------------------------
section("Without Cloudinary, the stored-PDF routes say so rather than 500ing")
# ---------------------------------------------------------------------------
cloudinary.config(cloud_name=None, api_key=None, api_secret=None)
for path in ("/api/generate-client-pdf", "/api/generate-internal-pdf",
            "/api/generate-requirements-pdf"):
    r = client.post(path, json={"client": "X"})
    check(f"{path} answers 503 rather than raising",
          r.status_code, 503)
    check("  naming Cloudinary rather than a stack trace",
          "Cloudinary" in json.dumps(r.get_json()))
check("but the direct-download route needs none of that",
     client.post("/api/download-requirements-pdf",
                 json={"client": "X", "documentType": "client"}).status_code,
     200)


print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
