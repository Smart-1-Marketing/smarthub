"""Client 360: deleting an image, counting them honestly, the brand card, and
the way back to the client record.

    python3 test_client_images.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

Five failures, and every one of them left a screen looking healthy.

  1. **"Showing 1 of 7 saved images."** `/api/gallery` filtered the rows and
     then reported `len(load_archive())` as the total, so a client with one
     image was described as having seven and the gallery the sentence linked
     to then showed the one. Both screens were right; the sentence joining
     them was the wrong answer, which is the worst of the three.

  2. **A card that shows a wrong image and offers nothing to do about it.**
     The tile linked out to the file and to the gallery, and the gallery — the
     one screen somebody opens from a client record — had no delete either. It
     was a trip through two screens to remove one image, so it did not happen.

  3. **"Back" meant the tool.** Every link out of Client 360 landed somewhere
     whose idea of back was its own parent, so a rep who opened the gallery
     for Icon Solar got "← SEO Image Pipeline" and had to search for the
     client again. The client is carried on the link now and offered back on
     every page downstream.

  4. **The brand card read stored data and nothing ever stored any.** Three
     modules ran live brand lookups and only one of them ever saved the
     answer — and Client 360 asked by client *name*, so the domain-keyed half
     of the store, which is where every saved lookup actually landed, was
     never consulted. Empty card, working lookups, nothing errored anywhere.

  5. **An empty answer that cannot say why.** "Nobody has looked yet", "there
     is no website to look up by", "the key is not set" and "we looked and
     they publish nothing" are four situations and one of them is a button
     press. They read identically before this.

And the second source: a site audit already carries the logo, the brand
colours, the Google Business Profile and thirty other things this record had
no answer for. It is read here — labelled **observed**, never merged into the
brand kit, because a logo lifted off a home page is a candidate and a wrong
logo on a client-facing document is worse than none.

Three more since:

  6. **Two brand cards.** The kit and the sighting drew as two blocks, so the
     same colours appeared twice under two headings — and since the lookup
     publishes nothing for a local business, the *upper* one was the empty
     one: "No brand data on file yet" printed directly above the logo the
     card plainly had. One card now, one set of tiles, each saying where it
     came from and none of them saying which of our services answered.

  7. **A contact strip reading "none on file" about a record holding the
     address.** The name, address and phone were read off the client's own
     site and sat three cards down. They are offered into the empty fields,
     drawn as an offer, and kept by one press — never over anything typed.

  8. **Display ads that reached the log and not the record.**
     `hub/client_brand.WORK_KINDS` was keyed on neither `ad_builder` nor
     `display_ads`, and `work_log()` skips a module it cannot name, so a
     client who had just had a set of ads built read as one nobody had done
     any work for.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cimg_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "client-images-test-secret"
os.environ["PANEL_PASSWORD"] = "client-images-test-password"

_passed, _failed = 0, 0


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


from hub import brand_lookup as brand_lookup_mod              # noqa: E402

C360 = (ROOT / "hub" / "templates" / "client360.html").read_text()
GALLERY = (ROOT / "modules" / "seo_images" / "templates" / "gallery.html").read_text()
CRUMBS = (ROOT / "hub" / "static" / "hub-crumbs.js").read_text()


# =====================================================================
section("The count is this client's, not the whole archive's")
# =====================================================================

from modules.seo_images import app as seo_images             # noqa: E402

ARCHIVE = [
    {"id": "a1", "company": "Icon Solar", "filename": "icon-solar-panel.webp",
     "url": "https://res.cloudinary.com/x/a1.webp", "alt_text": "Panels",
     "project": "Website refresh"},
    {"id": "b1", "company": "Riverside HVAC", "filename": "riverside-1.webp",
     "url": "https://res.cloudinary.com/x/b1.webp", "alt_text": "Unit",
     "project": "Spring"},
    {"id": "b2", "company": "Riverside HVAC", "filename": "riverside-2.webp",
     "url": "https://res.cloudinary.com/x/b2.webp", "alt_text": "Van",
     "project": "Spring"},
]
seo_images.save_archive(list(ARCHIVE))
gclient = seo_images.app.test_client()

one = gclient.get("/api/gallery?company=Icon%20Solar").get_json()
check("one client, one image", len(one["gallery"]), 1)
# The whole point: the sentence Client 360 prints is "Showing N of total".
check("and the total is that client's", one["total"], 1)
check("the archive-wide figure is still there, named", one["archive_total"], 3)
check("and the answer says it was filtered", one["filtered"], True)

allrows = gclient.get("/api/gallery").get_json()
check("unfiltered, the two agree", (allrows["total"], allrows["archive_total"]), (3, 3))
check("and nothing claims a filter", allrows["filtered"], False)

# A search is a filter too — it used to report the archive as its total.
found = gclient.get("/api/gallery?q=riverside").get_json()
check("a search totals its own results", found["total"], 2)

# The Client 360 card must read the filtered figure, never the archive one.
check("Client 360 reads d.total for the count",
      "const total=(d.total||0)+" in C360, True)


# =====================================================================
section("Every image can be deleted from the record it belongs to")
# =====================================================================

check("the tile carries a delete", 'class="open-link img-del"' in C360, True)
check("and a download beside it", "api/gallery/download?id=" in C360, True)
check("the tile is addressable by id", 'data-img="${esc(r.id)}"' in C360, True)
# One description of what deleting an image means: the same endpoint the
# pipeline's own archive table posts to, so the Cloudinary copy is dealt with
# in one place rather than two that can disagree.
check("through the pipeline's own endpoint",
      "imgBase+'api/gallery/update'" in C360, True)
check("it says out loud that it cannot be undone",
      "cannot be undone" in C360, True)
# A card that deletes and does not redraw looks like a button that did
# nothing, which is how somebody deletes an image twice.
check("and the card redraws afterwards",
      "window.loadClientImages" in C360, True)

deleted = gclient.post("/api/gallery/update", json={"id": "b1", "delete": True})
check("the delete answers ok", deleted.get_json().get("ok"), True)
left = gclient.get("/api/gallery?company=Riverside%20HVAC").get_json()
check("the row is gone", [r["id"] for r in left["gallery"]], ["b2"])
check("and the count went with it", left["total"], 1)

gone = gclient.post("/api/gallery/update", json={"id": "b1", "delete": True})
check("deleting it again is refused, not silently ok", gone.status_code, 404)

# The gallery is the screen a client record links to, and it had no delete at
# all — so the one place you went to look at a wrong image could not remove it.
check("the gallery has a delete per tile", 'data-del="${esc(r.id)}"' in GALLERY, True)
check("and an alt-text edit", 'data-edit="${esc(r.id)}"' in GALLERY, True)
check("the gallery names the file in the confirmation",
      "r.filename" in GALLERY and "cannot be undone" in GALLERY, True)
check("and posts to the same one endpoint",
      GALLERY.count("api/gallery/update") >= 2, True)


# =====================================================================
section("Back goes to the client, not to the tool")
# =====================================================================

seo_images.save_archive(list(ARCHIVE))
page = gclient.get("/gallery?company=Icon+Solar").get_data(as_text=True)
check("the gallery still offers the tool's own parent",
      "SEO Image Pipeline" in page, True)
check("and a slot for the client it was opened from",
      'id="backClient"' in page, True)
# The client name is not COMPANY: opening the gallery and searching for a
# company is not the same as arriving from that company's record, and only
# the second has somewhere to go back to.
check("read from the link, never guessed", "get('c360')" in GALLERY, True)
check("pointing at the record", "'/client360?q='" in GALLERY, True)
check("and carried onto anything that leaves the page",
      "function keepC360" in GALLERY, True)

# The generic half: one script, loaded on hub pages by base.html and injected
# into every mounted module by HubBar, so a tool linked from Client 360 next
# month gets this without being edited.
check("the shared script stamps outgoing links", "function stamp(" in CRUMBS, True)
check("and draws the way back", "function backBar(" in CRUMBS, True)
check("chrome is not stamped — following the sidebar is not still working "
      "on this client", ".s1hub-sb" in CRUMBS, True)
check("nor is anything cross-origin", "location.origin" in CRUMBS, True)
check("nor an API path", "api\\/" in CRUMBS, True)
check("nor a download", 'hasAttribute("download")' in CRUMBS, True)
# Arriving back at a record clears the trail: a bar offering the way back to
# the page you are standing on is noise, and one pointing at yesterday's
# client is worse than noise.
check("landing on the record clears it", "function onClient360(" in CRUMBS, True)
check("and it is dismissible", "s1-c360-x" in CRUMBS, True)
# Client 360 and the tools it opens draw themselves from fetches, so a single
# pass at load would stamp the shell and miss every link not yet drawn.
check("late-rendered links are stamped too", "MutationObserver" in CRUMBS, True)

check("the script is loaded on hub pages",
      "hub-crumbs.js" in (ROOT / "hub" / "templates" / "base.html").read_text(), True)
check("and injected into every mounted module",
      "hub-crumbs.js" in (ROOT / "wsgi.py").read_text(), True)


# =====================================================================
section("The brand card asks by domain as well as by name")
# =====================================================================

from hub import client_brand, seo                             # noqa: E402

PAYLOAD = {
    "name": "Icon Solar", "domain": "iconsolar.com",
    "logos": [{"type": "logo", "theme": "light",
               "formats": [{"src": "https://cdn/icon.svg", "format": "svg",
                            "width": 512, "height": 128}]}],
    "colors": [{"hex": "#ff8800", "type": "accent"}],
    "fonts": [{"name": "Inter", "type": "title"}],
}

# Saved the way every real lookup saves it: keyed by domain, with no client
# name attached, because the tool that ran it had only a domain.
seo.save_brandfetch("iconsolar.com", PAYLOAD, client="")

by_name = client_brand.brand_kit("Icon Solar", "")
check("asking by name alone finds nothing", by_name["found"], False)
by_domain = client_brand.brand_kit("Icon Solar", "iconsolar.com")
check("asking with the domain finds it", by_domain["found"], True)
check("and the logo is there", by_domain["logos"][0]["url"], "https://cdn/icon.svg")

# Which is why the caller has to pass one.
check("Client 360 sends the domain", "'&domain='+encodeURIComponent(dom)" in C360, True)
check("from the record's own website", "window.__c360domain" in C360, True)


# =====================================================================
section("An empty brand card says which kind of empty it is")
# =====================================================================

nothing = client_brand.brand_kit("Nobody Ltd", "")
check("no website: nothing to look up by",
      "nothing to look a brand up by" in nothing["note"], True)
check("and no button is offered", nothing.get("can_lookup"), False)

# hub.config.settings is frozen by design, so the switch is flipped where the
# lookup reads it rather than by rewriting the setting under it.
_real_configured = brand_lookup_mod.configured
brand_lookup_mod.configured = lambda: False
unset = client_brand.brand_kit("Nobody Ltd", "nobody-ltd.com")
check("a website but no key: named as not switched on",
      "not switched on" in unset["note"], True)
check("and still no button — it would fail for a reason nobody can act on",
      unset.get("can_lookup"), False)

brand_lookup_mod.configured = lambda: True
askable = client_brand.brand_kit("Nobody Ltd", "nobody-ltd.com")
check("a website and a key: the button is offered", askable["can_lookup"], True)
check("and the domain it would ask about is named",
      askable["lookup_domain"], "nobody-ltd.com")
check("the card says so in words", "Look it up from nobody-ltd.com" in askable["note"], True)

# A card that already has data still offers a refresh: brand details go stale
# and the alternative is somebody with a new logo having nowhere to put it.
check("a full card offers a refresh too",
      client_brand.brand_kit("Icon Solar", "iconsolar.com")["can_lookup"], True)


# =====================================================================
section("A lookup keeps what it paid for")
# =====================================================================

from hub import brand_lookup                                  # noqa: E402


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload


calls = []


def _fake_get(url, **kw):
    calls.append(url)
    return _Resp({"name": "Fresh Co", "domain": "freshco.com",
                  "logos": [{"type": "logo",
                             "formats": [{"src": "https://cdn/fresh.png",
                                          "format": "png", "width": 300}]}],
                  "colors": [{"hex": "#123456"}]})


_real_get = brand_lookup.requests.get
_real_key = brand_lookup._key
brand_lookup._key = lambda: "test-key"          # frozen settings, patched here
brand_lookup.requests.get = _fake_get
try:
    res = brand_lookup.lookup("https://www.freshco.com/contact",
                              client="Fresh Co", module="test")
finally:
    brand_lookup.requests.get = _real_get

check("the lookup found it", res["found"], True)
check("and says it was a live call", res["source"], "lookup")
check("the URL was the bare domain", calls[0].endswith("/brands/freshco.com"), True)
# Both halves of the store, because the two readers key on different things
# and a payload filed under one is invisible to the other.
check("saved against the domain",
      (seo.brand_for("", "freshco.com") or {}).get("name"), "Fresh Co")
check("and against the client",
      (seo.brand_for("Fresh Co", "") or {}).get("name"), "Fresh Co")
check("so the card can now draw it",
      client_brand.brand_kit("Fresh Co", "")["found"], True)

# A refusal, an unreachable service and a domain nobody publishes are three
# different answers. Reporting all of them as "no logo" sends somebody to
# rotate a key that was fine.
brand_lookup.requests.get = lambda url, **kw: _Resp({}, 401)
try:
    refused = brand_lookup.lookup("freshco.com", module="test", use_cache=False)
finally:
    brand_lookup.requests.get = _real_get
check("a refusal is named as one", refused.get("refused"), True)
check("and is not reported as 'no logo'", "refused our key" in refused["note"], True)

brand_lookup.requests.get = lambda url, **kw: _Resp({}, 404)
try:
    unknown = brand_lookup.lookup("freshco.com", module="test", use_cache=False)
finally:
    brand_lookup.requests.get = _real_get
check("a domain nobody publishes is its own answer",
      unknown.get("refused") is None and "Nothing is published" in unknown["note"], True)

check("no website is refused before any call is made",
      brand_lookup.lookup("", module="test")["note"],
      "No website to look a brand up by.")

brand_lookup._key = _real_key

# It is billed, so it is a button. A GET that spends money is one a reload or
# a prefetch fires without anybody asking.
HUB = (ROOT / "hub" / "__init__.py").read_text()
check("the route is a POST",
      '@app.route("/api/client/brand/lookup", methods=["POST"])' in HUB, True)
check("and the refresh does not answer out of the cache",
      "use_cache=False" in HUB, True)

# The modules that were throwing their lookups away.
for mod in ("modules/image_creator/assets.py", "modules/ads_builder/logo.py"):
    src = (ROOT / mod).read_text()
    check(f"{mod} saves what it fetched", "save_brandfetch" in src, True)


# =====================================================================
section("What the last scan saw is offered as observed, never as the brand")
# =====================================================================

from hub import scan_facts                                    # noqa: E402

REPORT = {
    "logo": {"logo_url": "https://site/logo.png", "has_detected_logo": True},
    "colour_scheme": {"primary_accent_colour": "ff8800",
                      "primary_text_colour": "#111111"},
    "google_business_profile": {"is_listing_found": True,
                                "is_listing_claimed": False,
                                "review_rating": 4.7, "review_count": 132},
    "paid_search": {"has_adwords_spend": True, "average_adspend": 2400},
    "retargeting": {"has_facebook_pixel": False},
    "domain_age": {"registrar": "GoDaddy.com, LLC"},
    "alternative_text": {"images_no_alt_count": 41},
    "meta": {"detected_name": "Icon Solar"},
}
ROW = {"public_id": "sc123", "domain_key": "iconsolar.com", "overall_score": 63,
       "tier": "C", "completed_at": "2026-08-01T10:00:00", "created_at": None}

_real_latest = scan_facts._latest
scan_facts._latest = lambda d: (REPORT, ROW, "")
obs = scan_facts.brand_observed("iconsolar.com")
f = scan_facts.facts("iconsolar.com")

check("the scan's logo is found", obs["logo_url"], "https://site/logo.png")
check("the colors come with their role",
      [c["type"] for c in obs["colors"]], ["Primary accent", "Text"])
check("and it says where it came from",
      "candidate, not an approved brand asset" in obs["note"], True)
check("with the date it was seen", obs["scanned_at"], "2026-08-01 10:00:00")
check("and a way to the scan", obs["scan_url"], "/scans/scan/sc123")

# Never merged into the kit. A logo scraped off a home page and a logo the
# client gave us are different claims, and only the second belongs on a
# document a client reads.
kit = client_brand.brand_kit("Icon Solar", "iconsolar.com")
check("the stored kit keeps its own logos",
      [l["url"] for l in kit["logos"]], ["https://cdn/icon.svg"])
check("and the sighting rides beside it, not in it",
      kit["observed"]["logo_url"], "https://site/logo.png")
check("the card draws it under its own heading",
      "Seen on their website" in C360, True)

titles = [g["title"] for g in f["groups"]]
check("the Google Business Profile is read", "Google Business Profile" in titles, True)
check("so is what they already spend", "What they are already spending" in titles, True)
check("and whether a campaign could be measured at all",
      "Can we run a campaign to this site" in titles, True)
check("and the registrar", "Domain and security" in titles, True)


def row_of(title, label):
    for g in f["groups"]:
        if g["title"] == title:
            for r in g["rows"]:
                if r["label"] == label:
                    return r["value"]
    return None


check("an unclaimed listing reads as No, not as missing",
      row_of("Google Business Profile", "Claimed"), "No")
check("a False pixel is an answer and is kept",
      row_of("Can we run a campaign to this site", "Meta pixel"), "No")
check("spend is money-formatted",
      row_of("What they are already spending",
             "Estimated monthly Google Ads spend"), "$2,400")
check("and is labeled an estimate rather than a bill",
      any("not a billed figure" in (r.get("hint") or "")
          for g in f["groups"] for r in g["rows"]), True)
# A field the account's plan does not include is left out, not printed as a
# confident zero — forty rows of "not measured" is a wall nobody reads, and a
# zero here would be a lie.
check("a section the scan never returned is absent, not nought",
      row_of("Organic search", "Keywords ranked for"), None)

# "Nobody has scanned them" and "we could not look" are different answers and
# only the first means there is nothing to do.
scan_facts._latest = lambda d: ({}, {}, "")
none_yet = scan_facts.facts("iconsolar.com")
check("never scanned: found is False", none_yet["found"], False)
check("and it says so plainly",
      "Nothing has been read from this website yet" in none_yet["note"], True)
check("without naming which of our tools would have done the reading",
      "scan" in none_yet["note"].lower(), False)
check("but it is not an error", none_yet.get("error"), None)

scan_facts._latest = lambda d: ({}, {}, "OperationalError: no such table")
broken = scan_facts.facts("iconsolar.com")
check("unreadable: not measured, and the reason is carried",
      (broken["measured"], "OperationalError" in broken["error"]), (False, True))
check("the brand half answers the same way",
      scan_facts.brand_observed("iconsolar.com").get("error") is not None, True)
check("and the card refuses to draw a clean nothing",
      "not measured" in C360, True)
scan_facts._latest = _real_latest

# It reads; it does not scan. No Insites credit is spent drawing this card.
check("nothing here reaches a provider",
      "requests" not in (ROOT / "hub" / "scan_facts.py").read_text(), True)



# =====================================================================
section("One brand card, from both sources, naming neither of them")
# =====================================================================
#
# It was two blocks: a brand kit fed by the lookup, and a second block under
# it fed by what had been read off the client's own website. For a local
# business the lookup publishes nothing at all, so the *upper* block was the
# empty one — the card led with "No brand data on file yet" directly above the
# logo it plainly had, and the same colours drew twice under two headings.

scan_facts._latest = lambda d: (REPORT, ROW, "")
merged = client_brand.brand_kit("Icon Solar", "iconsolar.com")

check("one set of tiles, both sources in it",
      [t["origin"] for t in merged["logo_tiles"]], ["file", "site"])
check("and each says where it came from, not which service answered",
      [t["label"] for t in merged["logo_tiles"]],
      ["On file", "Seen on their website"])
check("one palette, and a color both sources agree on draws once",
      len({c["hex"] for c in merged["palette"]}), len(merged["palette"]))
check("the stored role label survives the merge",
      merged["palette"][0]["origin"], "file")

# The merge is a thing the card does for a reader. It is not done to the data:
# `logos` is what brand_guide_payload() pushes to Suite and what io_prefill,
# landing_maker and client_context read, and a logo lifted off a home page has
# no business in any of them.
check("nothing was merged into the kit itself",
      [l["url"] for l in merged["logos"]], ["https://cdn/icon.svg"])
guide = client_brand.brand_guide_payload("Icon Solar", "iconsolar.com")
check("so the Suite push still carries only the approved logo",
      guide["brand_logo_url"], "https://cdn/icon.svg")

# The ordinary local business: no lookup has ever answered, and their own site
# carries the only logo the Hub will ever have for them. `found` stays False —
# it is the answer to "is there brand data on file", which the lookup button
# reads — while the card asks `has_brand`, and drawing an empty state over a
# logo we hold is the failure this closes.
_real_brand_for = seo.brand_for
seo.brand_for = lambda c, d="": None
nolookup = client_brand.brand_kit("Icon Solar", "iconsolar.com")
check("no lookup on file: found is still False", nolookup["found"], False)
check("but there is something to draw", nolookup["has_brand"], True)
check("and it is their website's logo",
      [t["url"] for t in nolookup["logo_tiles"]], ["https://site/logo.png"])
seo.brand_for = _real_brand_for

check("the card draws one merged set", "d.logo_tiles" in C360, True)
check("and asks whether there is anything at all to draw",
      "d.has_brand" in C360, True)
check("the card names no provider to the rep reading it",
      "Brandfetch</span>" in C360, False)


# =====================================================================
section("Contact details are offered into the strip that had none")
# =====================================================================
#
# The strip said "No contact info on file yet" about businesses whose address
# and phone number were already on this record, three cards further down,
# under a heading about our own tooling. Nobody types a client's address in
# twice, so it stayed unfilled.

CONTACT_REPORT = {
    "meta": {"detected_name": "Icon Solar", "detected_phone": "(317) 555-0142",
             "primary_industry": "Solar installer"},
    "local_presence": {"business_address": "12 Mill Rd, Carmel IN"},
    "google_business_profile": {"google_address": "1 Old Road, Carmel IN"},
}
scan_facts._latest = lambda d: (CONTACT_REPORT, ROW, "")
seen = scan_facts.contact_observed("iconsolar.com")

check("the details their own site publishes are read",
      (seen["fields"]["name"], seen["fields"]["phone"]),
      ("Icon Solar", "(317) 555-0142"))
# First non-empty wins, and the order is the point: the business describing
# itself beats the listing address a customer is driven to, which is the one
# most often out of date.
check("their own address beats the listing address",
      seen["fields"]["address"], "12 Mill Rd, Carmel IN")
check("with the date it was read", seen["observed_at"], "2026-08-01 10:00:00")
check("and no plumbing in the wording", "scan" in seen["note"].lower(), False)

# "Nothing has been read" and "we could not look" are different answers, and
# only the first means there is nothing to offer.
scan_facts._latest = lambda d: ({}, {}, "")
check("nothing read yet is not an error",
      (scan_facts.contact_observed("x.com")["found"],
       scan_facts.contact_observed("x.com").get("error")), (False, None))
scan_facts._latest = lambda d: ({}, {}, "OperationalError: no such table")
check("and a table that will not answer says so",
      "OperationalError" in scan_facts.contact_observed("x.com")["error"], True)

# Suggested is never saved, and only ever fills a blank: a value a person
# typed is the better source than anything read off a home page.
scan_facts._latest = lambda d: (CONTACT_REPORT, ROW, "")
empty = scan_facts.contact_suggestions({"contacts": [], "address": "", "category": ""},
                                       "iconsolar.com")
check("an untouched record is offered everything read",
      sorted(empty["values"]), ["address", "category", "name", "phone"])
typed = scan_facts.contact_suggestions(
    {"contacts": [{"name": "Dana", "phone": "(317) 555-9000"}],
     "address": "9 Elm St", "category": ""}, "iconsolar.com")
check("a field somebody filled in is never offered over",
      sorted(typed["values"]), ["category"])
check("and a record with no domain is offered nothing at all",
      scan_facts.contact_suggestions({}, "")["values"], {})

check("the strip asks with the record's own domain",
      "'&domain='+encodeURIComponent(window.__c360domain" in C360, True)
check("an offer is drawn as an offer, not as a record",
      "Read from their website" in C360, True)
check("and one press keeps it", "Save these details" in C360, True)
check("the edit modal opens on what the strip is showing",
      "profileWith(((clientSuggested||{}).values)||{})" in C360, True)
scan_facts._latest = _real_latest


# =====================================================================
section("Work in the Display Ad Builder reaches the client record")
# =====================================================================
#
# `modules/ad_builder` is the TypeScript renderer, so its Hub-side half logs
# under `display_ads` — declared in `audit.LOG_NAMES` precisely because the
# directory name and the log name differ. `WORK_KINDS` was keyed on neither,
# and `work_log()` skips a module it cannot name: every build started and
# every pack filed against a client was written to the activity log, kept, and
# then dropped on the way to the record it was written for. A client who had
# just had a set of display ads built read as a client nobody had done any
# work for, which is the confidently wrong answer this codebase treats as
# worse than an error.

from hub import audit                                          # noqa: E402

check("the Display Ad Builder's log name is a kind of work",
      "display_ads" in client_brand.WORK_KINDS, True)
check("and it is named for the tool a rep opened, not the directory",
      client_brand.WORK_KINDS["display_ads"], ("Display ads", "Display Ad Builder"))
# Whatever audit declares a renamed log for is a name work_log has to know:
# the declaration exists because the two differ, so it is exactly the case
# where the table gets keyed on the wrong one.
check("every declared log name is one the work log can name",
      sorted(set(audit.LOG_NAMES.values()) - set(client_brand.WORK_KINDS)), [])

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
