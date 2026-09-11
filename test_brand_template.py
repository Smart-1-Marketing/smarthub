"""A rep's confirmed pick, replacing the guess every reader of brand_kit made.

    python3 test_brand_template.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## What this holds

`hub/client_brand.brand_kit()` has always merged several logos and several
colours into one card with no way to say which one is *the* brand — Brandfetch
itself "frequently does not say which entry is the brand colour", so
`brand_guide_payload()` and `hub/client_context.py` have always taken position
zero and called it the answer. `hub/brand_template.py` is the pick that
replaces the guess, and this is worth asserting from both ends:

* **A logo is never invented; a colour can be typed.** A logo pick has to be
  one of the tiles `brand_kit()` is offering *right now* — the exact URL — or
  it is refused, named, and nothing is written. A colour only has to be a
  well-formed hex: the automated palette is a guess and a rep with the
  client's real brand colour is the authority it exists to be corrected by.
* **Read by nothing until this, so it is used.** `brand_kit()` promotes a
  confirmed pick to position zero without brand_guide_payload() or
  client_context.py changing a line, and Magic Resize's project reference
  (`store.brand_for()`) resolves from the client's name rather than a stored
  key that could go stale.
* **A stale logo pick never raises.** A logo confirmed against last month's
  Brandfetch answer, which has since changed, is simply not found in the
  current list — the order stands as it was, and nothing crashes reading it
  back. A colour pick has no such staleness: it was never tied to being
  currently observed, so it keeps leading the card even after the automated
  answer moves on.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-brandtemplate-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "brand-template-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, got, want=True):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print(f"  FAIL {label}\n         got  {got!r}\n         want {want!r}")


def section(title):
    print("\n" + title)
    print("-" * 60)


from hub import brand_template, client_brand, seo               # noqa: E402
from modules.magic_resize import store as mr_store               # noqa: E402


PAYLOAD = {
    "name": "Acme Plumbing", "domain": "acmeplumbing.com",
    "logos": [
        {"type": "logo", "theme": "light",
         "formats": [{"src": "https://cdn/acme-light.svg", "format": "svg",
                      "width": 400}]},
        {"type": "icon", "theme": "dark",
         "formats": [{"src": "https://cdn/acme-dark.png", "format": "png",
                      "width": 200}]},
    ],
    "colors": [{"hex": "#1a2b3c", "type": "accent"},
               {"hex": "#ffcc00", "type": "brand"}],
    "fonts": [{"name": "Inter", "type": "title"}],
}
seo.save_brandfetch("acmeplumbing.com", PAYLOAD, client="Acme Plumbing")


# =====================================================================
section("Nobody has confirmed anything yet")
# =====================================================================

empty = brand_template.get("Acme Plumbing")
check("picked is False", empty["picked"], False)
check("logo_url is blank", empty["logo_url"], "")
check("every color role is blank", any(empty["colors"].values()), False)

kit = client_brand.brand_kit("Acme Plumbing", "acmeplumbing.com")
check("brand_kit carries the empty shell too", kit["template"]["picked"], False)
check("no tile reads as confirmed yet",
      any(t.get("confirmed") for t in kit["logo_tiles"]), False)


# =====================================================================
section("A logo is never invented — it must be on offer right now")
# =====================================================================

bogus = brand_template.save("Acme Plumbing", "acmeplumbing.com", "logo",
                            "https://cdn/somebody-elses-logo.png")
check("a logo nobody has seen for this client is refused", bogus["ok"], False)
check("and named as such", "not one this Hub" in bogus["error"], True)

bogus_field = brand_template.save("Acme Plumbing", "acmeplumbing.com",
                                  "spokesperson", "anyone")
check("a field this cannot confirm is refused by name",
      "is not something this can confirm" in bogus_field["error"], True)

not_a_hex = brand_template.save("Acme Plumbing", "acmeplumbing.com",
                                "primary", "not a color")
check("a value that isn't a hex is refused", not_a_hex["ok"], False)

check("none of that wrote anything",
      brand_template.get("Acme Plumbing")["picked"], False)


# =====================================================================
section("A colour needs no offer to type — it is a rep's own answer")
# =====================================================================

typed = brand_template.save("Acme Plumbing", "acmeplumbing.com",
                            "secondary", "#00ff00")
check("a color nobody has seen for this client is accepted anyway",
      typed["ok"], True)
check("normalized the same way", typed["template"]["colors"]["secondary"],
      "#00FF00")
typed_kit = client_brand.brand_kit("Acme Plumbing", "acmeplumbing.com")
check("it draws on the card as its own swatch",
      any(c["hex"] == "#00FF00" for c in typed_kit["palette"]), True)
typed_swatch = next(c for c in typed_kit["palette"] if c["hex"] == "#00FF00")
check("labelled as typed in, not observed", typed_swatch["origin"], "manual")
check("clearing it always works",
      brand_template.save("Acme Plumbing", "acmeplumbing.com",
                          "secondary", "")["ok"], True)


# =====================================================================
section("A pick that IS on offer is confirmed")
# =====================================================================

logo_pick = brand_template.save("Acme Plumbing", "acmeplumbing.com", "logo",
                                "https://cdn/acme-dark.png", actor="jane")
check("the second logo, which is genuinely on the card, is accepted",
      logo_pick["ok"], True)
check("its theme travels with it", logo_pick["template"]["logo_theme"], "dark")

# A hex is matched case-insensitively and without the leading #, because
# that's how a rep is going to type one back in from a swatch's tooltip.
color_pick = brand_template.save("Acme Plumbing", "acmeplumbing.com",
                                 "primary", "ffcc00", actor="jane")
check("a bare hex, lowercase, still matches the stored swatch",
      color_pick["ok"], True)
check("normalized to uppercase with the #",
      color_pick["template"]["colors"]["primary"], "#FFCC00")
check("who confirmed it is on the record",
      color_pick["template"]["updated_by"], "jane")

picked = brand_template.get("Acme Plumbing")
check("picked is now True", picked["picked"], True)


# =====================================================================
section("brand_kit() promotes the pick — every caller that takes [0] "
        "already gets it")
# =====================================================================

promoted = client_brand.brand_kit("Acme Plumbing", "acmeplumbing.com")
check("the confirmed logo is first, not wherever Brandfetch put it",
      promoted["logos"][0]["url"], "https://cdn/acme-dark.png")
check("the confirmed color is first",
      promoted["colors"][0]["hex"], "#FFCC00")

# The tile and the swatch on the card say which one is confirmed, and which
# role, so the card can draw it without a second round trip.
confirmed_tile = next(t for t in promoted["logo_tiles"]
                      if t["url"] == "https://cdn/acme-dark.png")
check("the tile is tagged confirmed", confirmed_tile["confirmed"], True)
other_tile = next(t for t in promoted["logo_tiles"]
                  if t["url"] == "https://cdn/acme-light.svg")
check("the other tile is not", other_tile["confirmed"], False)

confirmed_swatch = next(c for c in promoted["palette"] if c["hex"] == "#FFCC00")
check("the swatch is tagged confirmed", confirmed_swatch["confirmed"], True)
check("and says which role", confirmed_swatch["role"], "primary")

# No caller changed — brand_guide_payload() and client_context.py both take
# position zero, and that is now the confirmed pick.
guide = client_brand.brand_guide_payload("Acme Plumbing", "acmeplumbing.com")
check("the Suite push carries the confirmed logo",
      guide["brand_logo_url"], "https://cdn/acme-dark.png")
check("and the confirmed color",
      guide["brand_primary_color"], "#FFCC00")


# =====================================================================
section("Clearing a pick always succeeds, and the guess returns")
# =====================================================================

cleared = brand_template.save("Acme Plumbing", "acmeplumbing.com", "logo", "")
check("clearing needs no validation", cleared["ok"], True)
check("logo_url is blank again", cleared["template"]["logo_url"], "")
check("the color pick is untouched", cleared["template"]["colors"]["primary"],
      "#FFCC00")

back_to_guess = client_brand.brand_kit("Acme Plumbing", "acmeplumbing.com")
check("with nothing confirmed, the raw order (svg first) is back",
      back_to_guess["logos"][0]["url"], "https://cdn/acme-light.svg")


# =====================================================================
section("Brandfetch answering differently does not undo a typed colour")
# =====================================================================

# Simulate Brandfetch answering differently since the pick was confirmed:
# the confirmed hex is no longer anywhere in the payload.
STALE = {"name": "Acme Plumbing", "domain": "acmeplumbing.com",
        "logos": PAYLOAD["logos"], "colors": [{"hex": "#112233", "type": "accent"}]}
seo.save_brandfetch("acmeplumbing.com", STALE, client="Acme Plumbing")
stale_kit = client_brand.brand_kit("Acme Plumbing", "acmeplumbing.com")
check("the typed color still leads — it was never tied to being observed",
      stale_kit["colors"][0]["hex"], "#FFCC00")
check("and the template still reports what was confirmed",
      brand_template.get("Acme Plumbing")["colors"]["primary"], "#FFCC00")
check("its swatch is still marked confirmed",
      any(c.get("confirmed") for c in stale_kit["palette"]), True)


# =====================================================================
section("A client with no brand data on file at all")
# =====================================================================

nothing = brand_template.get("Nobody Ltd")
check("still an honest empty shell", nothing["picked"], False)
no_client = brand_template.save("", "", "logo", "https://x")
check("no client named is refused, not a 500", no_client["ok"], False)


# =====================================================================
section("Magic Resize resolves the client's brand on read, never stores it")
# =====================================================================

one_off = mr_store.create(name="Spring banners", client="", source={
    "width": 300, "height": 250, "objects": []})
check("a one-off project has no client to resolve",
      mr_store.brand_for(one_off)["picked"], False)
check("the FK the build plan asked for was never the answer -- "
      "brand_profile_ref is apply_brand()'s own audit field, empty until "
      "a brand has actually been pulled in",
      one_off["brand_profile_ref"], "")

# Restore the confirmed logo pick, and start a project for that client.
brand_template.save("Acme Plumbing", "acmeplumbing.com", "logo",
                    "https://cdn/acme-light.svg")
tied = mr_store.create(name="Spring banners", client="Acme Plumbing", source={
    "width": 300, "height": 250, "objects": []})
resolved = mr_store.brand_for(tied)
check("the project resolves the client's confirmed brand",
      resolved["picked"], True)
check("the same logo a rep confirmed on Client 360",
      resolved["logo_url"], "https://cdn/acme-light.svg")

# Confirm a *different* logo after the project already exists — nothing was
# stored on the project, so opening it again reads the change.
brand_template.save("Acme Plumbing", "acmeplumbing.com", "logo",
                    "https://cdn/acme-dark.png")
check("re-reading the same project sees the later pick",
      mr_store.brand_for(tied)["logo_url"], "https://cdn/acme-dark.png")


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
