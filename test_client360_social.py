"""Client 360's Social & links card -- the link catalog and the record.

    python3 test_client360_social.py

The card used to render nine hard-coded rows whether or not a client had
any of them: nine empty boxes on most records, and nowhere at all to put
the listing URLs that carry a local client's reputation -- Yelp was there,
Better Business Bureau, Healthgrades, Angi and thirty others were not.

Now the card renders only links that exist and offers the rest through an
add menu, and the catalog those come from lives in hub/seo.py and ships
to the browser through /api/client/social. That is the thing this file
guards, worst failure first:

  * **The page inventing its own list.** If the template hard-codes
    platforms again, the menu and the server's accepted keys drift, and a
    row saves into a key the server drops on read -- silently, because
    get_social() filters unknown keys rather than erroring. So the template
    is asserted to contain no platform list of its own.
  * **A blank row rendering.** The whole point of the change. Driven in
    node against the template's own renderSocial(), the arrangement
    test_client360_layout.py uses.
  * **Deletion actually deleting.** An empty value is the delete, and the
    save posts every key the page loaded with -- otherwise a removed row
    comes back on reload.
  * **Custom links keeping their name.** A hand-added link stores its label
    or it reloads as "c_chamber-of-commerce" and means nothing.
  * **Keys staying stable.** Labels are display; keys are storage. A key
    that changes orphans every saved link, so their shape is asserted.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1c360social_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "c360-social-test"
os.environ["PANEL_PASSWORD"] = "c360-social-pass"

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


TPL = (ROOT / "hub" / "templates" / "client360.html").read_text(encoding="utf-8")

from hub import seo  # noqa: E402


# --------------------------------------------------------------- catalog
section("The catalog")

keys = [k for k, _l, _g in seo.SOCIAL_PLATFORMS]
check("keys are unique", len(keys) == len(set(keys)))
check("every key is a stable slug",
      all(re.fullmatch(r"[a-z][a-z0-9]{1,24}", k) for k in keys))
check("every entry has a label and a group",
      all(l.strip() and g.strip() for _k, l, g in seo.SOCIAL_PLATFORMS))
check("the nine the card shipped with all survived",
      set(["facebook", "instagram", "linkedin", "twitter", "youtube",
           "tiktok", "pinterest", "yelp", "gbp"]).issubset(set(keys)))
check("the listing sites from the source list are in it",
      set(["bbb", "trustpilot", "healthgrades", "angi", "houzz", "tripadvisor",
           "capterra", "avvo", "cargurus", "zillow", "theknot", "zocdoc",
           "yellowpages", "opentable", "indeed", "imdb"]).issubset(set(keys)))
check("_SOCIAL_KEYS is derived from the catalog, not restated",
      tuple(keys) == seo._SOCIAL_KEYS)
cat = seo.social_catalog()
check("social_catalog() keeps the catalog's order",
      [c["key"] for c in cat] == keys)
check("social_catalog() carries the group for the menu's optgroups",
      all(c["group"] for c in cat))


# ------------------------------------------------------------- key handling
section("Keys: catalog and custom")

check("a catalog key is accepted", seo.is_social_key("healthgrades"))
check("an unknown bare word is refused", seo.is_social_key("myspace"), False)
check("a custom key is accepted", seo.is_social_key("c_chamber-of-commerce"))
check("a custom key with punctuation is refused",
      seo.is_social_key("c_a<script>"), False)
check("an over-long custom key is refused",
      seo.is_social_key("c_" + "a" * 60), False)
check("custom_social_key slugifies a typed label",
      seo.custom_social_key("Chamber of Commerce!"), "c_chamber-of-commerce")
check("custom_social_key refuses an empty label",
      seo.custom_social_key("   "), "")
check("custom_social_key refuses a collision with a catalog key",
      seo.custom_social_key("yelp"), "c_yelp")  # namespaced, so no collision
check("social_label() prefers the catalog",
      seo.social_label("bbb"), "Better Business Bureau")
check("social_label() falls back to the client's own label",
      seo.social_label("c_rotary", {"c_rotary": "Rotary Club"}), "Rotary Club")


# ------------------------------------------------------------ the record
section("Saving, deleting and labeling")

CLIENT = "Social Catalog Test Client"

saved = seo.set_social(CLIENT, {
    "facebook": "https://facebook.com/example",
    "healthgrades": "https://healthgrades.com/example",
    "c_chamber-of-commerce": "https://chamber.example.org/example",
    "myspace": "https://myspace.com/example",
}, {"c_chamber-of-commerce": "Chamber of Commerce"})

check("a new catalog key saves", saved.get("healthgrades"),
      "https://healthgrades.com/example")
check("a custom key saves", saved.get("c_chamber-of-commerce"),
      "https://chamber.example.org/example")
check("an unknown key is dropped rather than stored",
      "myspace" not in saved)
check("the custom label is kept",
      seo.get_social_labels(CLIENT).get("c_chamber-of-commerce"),
      "Chamber of Commerce")
check("a catalog label is NOT stored per client",
      "healthgrades" not in seo.get_social_labels(CLIENT))

try:
    seo.set_social(CLIENT, {"bbb": "javascript:alert(1)"})
    _bad = "no error"
except ValueError as exc:
    _bad = "rejected"
check("an unsafe scheme is still refused", _bad, "rejected")

after = seo.set_social(CLIENT, {"healthgrades": "",
                                "c_chamber-of-commerce": ""})
check("an empty value deletes the link", "healthgrades" not in after)
check("the deleted custom link's label goes with it",
      seo.get_social_labels(CLIENT), {})
check("the untouched link survives the delete",
      after.get("facebook"), "https://facebook.com/example")
check("what get_social reads back matches what was saved",
      seo.get_social(CLIENT), after)


# --------------------------------------------------------------- the page
section("The template")

check("the page no longer hard-codes a platform list",
      "SOCIAL_KEYS=[" not in TPL.replace(" ", ""))
check("the catalog is read from the API response",
      "SOCIAL_CATALOG=d.catalog" in TPL.replace(" ", "").replace("\n", "")
      or "SOCIAL_CATALOG=d.catalog||[]" in TPL)
check("there is an add control", 'id="c-social-add"' in TPL)
check("rows carry a remove button", 'class="s-del"' in TPL)
check("the save posts labels alongside the urls", "labels:labels" in TPL)


# ------------------------------------------- the renderer, driven in node
section("renderSocial() in node")

i = TPL.index("  // Social Media, and every other listing")
j = TPL.index("  // Social content — requests, ideas")
CHUNK = TPL[i:j]
# Drop the fetch that runs on load; the harness calls the renderer directly.
CHUNK = CHUNK[:CHUNK.index("  fetchJson('/api/client/social")]

HARNESS = """
const doc = { rows: [] };
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function safeExternalUrl(u){ return /^https?:\\/\\//.test(String(u||'')) ? u : ''; }
let HTML = '';
const node = { set innerHTML(v){ HTML = v; }, get innerHTML(){ return HTML; },
               querySelector(){ return null; } };
function $(id){ return node; }
const document = { getElementById(){ return null; },
                   querySelectorAll(){ return []; } };
__CHUNK__
renderSocial({facebook:'https://facebook.com/x', bbb:'', healthgrades:
  'https://healthgrades.com/x'}, ['healthgrades']);
const filled = HTML;
renderSocial({}, null);
const empty = HTML;
console.log(JSON.stringify({filled, empty,
  catalog_used: SOCIAL_CATALOG.length}));
"""

script = HARNESS.replace("__CHUNK__", CHUNK).replace(
    "SOCIAL_CATALOG=[]",
    "SOCIAL_CATALOG=" + json.dumps(seo.social_catalog()))

node = shutil.which("node")
if not node:
    print("  skip  node is not installed — renderer not driven")
else:
    path = os.path.join(TMP, "render.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script)
    out = subprocess.run([node, path], capture_output=True, text=True)
    if out.returncode != 0:
        check("the renderer runs", out.stderr.strip()[:400], "")
    else:
        got = json.loads(out.stdout)
        filled, empty = got["filled"], got["empty"]
        check("a link with a value renders", "https://facebook.com/x" in filled)
        check("a key present but blank does NOT render a row",
              'data-sk="bbb"' not in filled)
        check("the label comes from the catalog, not the key",
              "Healthgrades" in filled)
        check("a filled row gets an open link", 'target="_blank"' in filled)
        check("the add menu renders under the rows",
              'id="c-social-add"' in filled)
        check("a platform already on the record is not offered again",
              '<option value="facebook"' not in filled)
        check("one that is not on the record IS offered",
              '<option value="yelp"' in filled)
        check("a custom option is always offered",
              'value="__custom"' in filled)
        check("the scan note names the platform, not the key",
              "Healthgrades)" in filled)
        check("a client with no links says so rather than drawing boxes",
              "No links saved" in empty and 'class="s-row"' not in empty)
        check("the add menu is still there when there is nothing saved",
              'id="c-social-add"' in empty)

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
