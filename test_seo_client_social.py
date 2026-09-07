"""The SEO client page's Social Media card -- the same catalog, brought here.

    python3 test_seo_client_social.py

Client 360 got the catalog-driven treatment first (see
test_client360_social.py): only links that exist render, and the rest are one
press away in an add menu backed by /api/client/social. seo_client.html read
the same store through a nine-key hard-coded grid the whole time, which meant
a link added on Client 360 was saved and invisible here -- the same client,
two different lists of what they have.

This page now reads the same catalog through the same endpoint. The catalog
itself (unique keys, labels, the nine survivors, the listing sites, save and
delete semantics) is already asserted by test_client360_social.py against
hub/seo.py, which both pages read -- this file does not repeat that. What it
guards is specific to this page:

  * **The page inventing its own list again.** The nine-key SOCIAL_KEYS array
    this page used to hard-code must not come back.
  * **A blank row rendering.** Driven in node against this page's own
    renderSocial(), the arrangement test_client360_layout.py uses.
  * **Save posting labels as well as URLs**, and the add control and the
    per-row remove button actually being on the page.
  * **The Brandfetch merge surviving the change.** btnBrandSearch folds a
    brand lookup's social profiles into whatever is already on the form via
    currentSocial() -- that function has to still exist and still return a
    plain {key: url} map, or the merge silently drops what was typed.
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

TMP = tempfile.mkdtemp(prefix="s1seosocial_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "seo-social-test"
os.environ["PANEL_PASSWORD"] = "seo-social-pass"

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


TPL = (ROOT / "hub" / "templates" / "seo_client.html").read_text(encoding="utf-8")

from hub import seo  # noqa: E402


# --------------------------------------------------------------- the page
section("The template")

check("the page no longer hard-codes a nine-platform list",
      "SOCIAL_KEYS=[" not in TPL.replace(" ", ""))
check("the catalog is read from /api/client/social",
      "fetch('/api/client/social" in TPL)
check("the catalog and labels come off the response",
      "SOCIAL_CATALOG=d.catalog" in TPL.replace(" ", "")
      and "SOCIAL_LABELS=d.labels" in TPL.replace(" ", ""))
check("there is an add control", 'id="socialAdd"' in TPL)
check("rows carry a remove button", 'class="s-del"' in TPL)
check("the save posts labels alongside the urls", "state.labels" in TPL)
check("deletion still works by posting every loaded key back as empty",
      "socialLoaded.forEach" in TPL)
check("the Brandfetch merge still reads a plain key/value map",
      "currentSocial()" in TPL and "function currentSocial(){" in TPL)


# ------------------------------------------- the renderer, driven in node
section("renderSocial() in node")

i = TPL.index("  // ---------------- social media urls ----------------")
j = TPL.index("  // ------- Find client info: what we know first, then their GMB -------")
CHUNK = TPL[i:j]

HARNESS = """
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
let HTML = '';
const node = { set innerHTML(v){ HTML = v; }, get innerHTML(){ return HTML; },
               querySelector(){ return null; } };
function $(id){ return node; }
const document = { getElementById(){ return null; },
                   querySelectorAll(){ return []; } };
function post(){ return Promise.resolve({}); }
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
              'id="socialAdd"' in filled)
        check("a platform already on the record is not offered again",
              '<option value="facebook"' not in filled)
        check("one that is not on the record IS offered",
              '<option value="yelp"' in filled)
        check("a custom option is always offered",
              'value="__custom"' in filled)
        check("the scan note names the platform, not the key",
              "Healthgrades)" in filled)
        check("a client with no links says so rather than drawing boxes",
              "No links saved" in empty and 'data-sk=' not in empty)
        check("the add menu is still there when there is nothing saved",
              'id="socialAdd"' in empty)

shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
