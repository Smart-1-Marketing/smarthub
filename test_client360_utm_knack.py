"""Tracked links reads both places a tagged link lives.

    python3 test_client360_utm_knack.py

Same shape as the other test files here — no pytest, no new dependencies.

## Why this file exists

Client 360's Tracked links card read the UTM Builder's own store and nothing
else, and that is only one of the two places a tracked link lives here. Four
fields on Knack's object_135 hold a click-thru URL — Click Thru URL
(field_2413), Display Click Thru URL (field_2414), IO Click Thru URL
(field_2415, a text formula rolled up from the insertion order) and Social
Media Url (field_2380) — and on this deployment 2,222 of 10,620 product rows
carry one with UTM parameters on it. So a client whose tagged links were all
typed onto their insertion orders read as a client with no tracked links at
all, which is the confident wrong answer this codebase keeps having to undo.

Four things are asserted, each a way that goes wrong quietly:

  1. **`utm` in a path is not a tagged link.** Matching the bare string would
     read /autumn-sale and /outmaneuver as tracked links and fill the card
     with URLs nobody tagged. `is_tagged()` matches `utm_<name>=` in the
     query.

  2. **One link on eleven product lines is one link.** The same tagged URL is
     typed onto every product row of an insertion order, so the rows are
     deduped on the URL and the count says how many lines carry it. What the
     dedupe must not lose is which campaigns and products it was found on.

  3. **A source that could not be read is not an empty client.** The route
     keeps the Knack half in its own key with its own error, so a Knack
     outage costs those rows and never the links built in the builder — and
     the card says so rather than drawing a clean "no tracked links yet".

  4. **The cache is keyed on FIELDS_VERSION.** `_row()` gained two keys, and
     the cache holds flattened rows: served at the old version every row
     would be missing them and every client would read as having no tagged
     links, however recently the cache was written.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed, _failed = 0, 0


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


from hub import knack_products as KP  # noqa: E402

REC = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")
INIT = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")

# ------------------------------------------------------------------------
section("1. The four fields Todd named are the four the row carries")

check("field_2413 is the click-thru", KP.F_CLICK_THRU, "field_2413")
check("field_2414 is the display click-thru", KP.F_DISPLAY_CLICK, "field_2414")
check("field_2415 is the IO click-thru", KP.F_IO_CLICK, "field_2415")
check("field_2380 is the social URL", KP.F_SOCIAL_URL, "field_2380")
check("LINK_FIELDS names all four, each with a label a reader can act on",
      sorted(f for _k, _l, f in KP.LINK_FIELDS),
      ["field_2380", "field_2413", "field_2414", "field_2415"])
check("...and every label is distinct",
      len({lbl for _k, lbl, _f in KP.LINK_FIELDS}), 4)

row = KP._row({
    "id": "r1",
    KP.F_PRODUCT_NAME: "Programmatic Display",
    KP.F_CLIENT: "Acme",
    KP.F_CLICK_THRU: "https://acme.test/?utm_source=CIRQ&utm_medium=Display",
    KP.F_DISPLAY_CLICK: "https://acme.test/d?utm_source=CIRQ",
    KP.F_IO_CLICK: "https://acme.test/io?utm_source=CIRQ",
    KP.F_SOCIAL_URL: "https://acme.test/s?utm_source=CIRQ",
})
check("a flattened row carries io_url", row.get("io_url"),
      "https://acme.test/io?utm_source=CIRQ")
check("...and social_url", row.get("social_url"),
      "https://acme.test/s?utm_source=CIRQ")
check("FIELDS_VERSION was bumped past the build that had neither",
      KP.FIELDS_VERSION >= 3)

# ------------------------------------------------------------------------
section("2. What counts as tagged")

check("a query utm_ parameter is tagged",
      KP.is_tagged("https://a.test/x?utm_source=LGM"))
check("...wherever it sits in the query",
      KP.is_tagged("https://a.test/x?ref=1&utm_campaign=Sept23"))
check("a path containing 'utm' is not a tagged link",
      KP.is_tagged("https://a.test/autumn-sale"), False)
check("...nor is one that merely contains the word",
      KP.is_tagged("https://a.test/outmaneuver/utm"), False)
check("an untagged URL is not tagged", KP.is_tagged("https://a.test/"), False)
check("an empty value is not tagged", KP.is_tagged(""), False)

check("the parameters are read off the URL in the order they appear",
      list(KP._utm_params(
          "https://a.test/?utm_source=LGM&utm_medium=Prog&utm_campaign=Columbus"
      ).items()),
      [("utm_source", "LGM"), ("utm_medium", "Prog"), ("utm_campaign", "Columbus")])
check("a five-parameter link keeps utm_id and utm_content",
      sorted(KP._utm_params(
          "https://h.test/p/?utm_source=website&utm_medium=digital"
          "&utm_campaign=res&utm_id=py26&utm_content=url-001").keys()),
      ["utm_campaign", "utm_content", "utm_id", "utm_medium", "utm_source"])

# ------------------------------------------------------------------------
section("3. One link on eleven product lines is one link")

TAGGED = "https://acme.test/?utm_source=CIRQ&utm_medium=Display&utm_campaign=Sept23"
PRODUCTS = [
    {"product": "Programmatic Display", "campaign": "Sept23",
     "url": TAGGED, "display_url": "", "io_url": "", "social_url": ""},
    {"product": "Retargeting", "campaign": "Sept23",
     "url": TAGGED, "display_url": "", "io_url": "", "social_url": ""},
    {"product": "Meta Paid", "campaign": "Sept23 Social",
     "url": "", "display_url": "", "io_url": "",
     "social_url": "https://acme.test/s?utm_source=CIRQ&utm_medium=Facebook"},
    {"product": "SEM", "campaign": "Always on",
     "url": "https://acme.test/untagged", "display_url": "",
     "io_url": "", "social_url": ""},
]

_real_for_client = KP.for_client
KP.for_client = lambda c: {"client": c, "products": PRODUCTS,
                           "source": "knack", "age_minutes": 4, "note": ""}
out = KP.tagged_links("Acme")
KP.for_client = _real_for_client

check("two distinct links, not four rows", out["count"], 2)
check("the repeated one says how many product lines carry it",
      [l["count"] for l in out["links"] if l["url"] == TAGGED], [2])
check("...and keeps both products it was found on",
      sorted(next(l for l in out["links"] if l["url"] == TAGGED)["products"]),
      ["Programmatic Display", "Retargeting"])
check("the untagged click-thru is not carried",
      any("untagged" in l["url"] for l in out["links"]), False)
check("the social link names the field it came from",
      next(l["fields"] for l in out["links"] if "/s?" in l["url"]), ["Social"])
check("each row carries its parsed parameters",
      next(l["utm"]["utm_campaign"] for l in out["links"] if l["url"] == TAGGED),
      "Sept23")
check("the source is carried so the card can say how fresh it is",
      out["source"], "knack")

# ------------------------------------------------------------------------
section("4. A Knack that will not answer is not a client with no links")

check("the route keeps the Knack half in its own key",
      '"knack": knack' in INIT)
check("...and a failure there carries an error rather than raising",
      'knack = {"links": [], "count": 0,' in INIT)
check("the card reads that key", "d.knack||{}" in REC)
check("an unreadable Knack is said out loud rather than drawn as empty",
      "so any of those are missing from this card rather than absent" in REC)
check("the empty state distinguishes the two sources",
      "here or on this client's insertion orders" in REC
      or "here or on this client\\'s insertion orders" in REC)
check("the IO links are labeled as such rather than merged in",
      "Tagged on their insertion orders" in REC)
check("nothing in the card hard-codes a field id",
      bool(re.search(r"field_2(413|414|415|380)", REC)), False)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
