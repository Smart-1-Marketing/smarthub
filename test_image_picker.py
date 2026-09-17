"""The Image Picker: where a client's files come from, deleting a gallery, and
what a "General Business" client is asked.

    python3 test_image_picker.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

Three changes, and every failure below is one where a screen goes on looking
healthy:

  1. **The upload panel offered the poorest list the widget can draw.**
     `PICKER_UPLOAD_SOURCES` defaulted to `local,camera,url`, so a client asked
     for "your photos" was offered a file dialog — while the photos sat in
     their Instagram feed, the agency's Dropbox and a Drive folder somebody
     else set up. Cloudinary's widget already speaks all of those. Nothing had
     switched them on, and nothing said so.

  2. **A billed add-on offered without a subscription is worse than absent.**
     Shutterstock, Getty, iStock and Unsplash are Cloudinary add-ons. A tab
     that consents and then fails for a reason that is nothing to do with the
     client is exactly why Google Ads came off the Google Access list.

  3. **A source name we do not recognise must not be forwarded.** It draws a
     broken tab or no tab, and both read as our page being broken — so it is
     the one thing about the source list the admin page reports. A source that
     is working is not a finding: a roster of green ticks is read once and
     skipped for ever, and it pushed the client list below the fold.

  4. **The staff picker 500'd on every visit.** `/tools/image-picker/c/<id>`
     includes the upload panel, and the route never passed the panel its
     variables — `{{ sources|tojson }}` over an Undefined raises while Flask is
     *rendering*, so it was never a broken widget, it was the whole page. The
     same shape as `url_for('website_check_limits')` in Sites Admin.

  5. **Delete had no button at all**, and when it got one it had to be the one
     irreversible control in a row of four safe ones. The name is typed, the
     Cloudinary result is reported apart from the database's, and a brochure
     PDF is destroyed as `raw` — asking Cloudinary to destroy it as an `image`
     returns "not found", which the old signature reported as a clean success.

  6. **General Business hands out four generic chips**, and it is the busiest
     entry in the dropdown, because "none of the above" always is. A client who
     describes a marine upholstery shop must get chips about boats — and when
     the model cannot be reached, must be *told* that is what happened rather
     than shown a generic set as though somebody had chosen it.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1picker_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "picker-test-secret"
os.environ["IMAGE_PICKER_SIGNING_KEY"] = "picker-test-signing-key"
# No OpenAI key: the fallback path is the one a deployment without one takes,
# and it is the one that must not lie about what it built.
os.environ.pop("OPENAI_API_KEY", None)

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


import flask                                                     # noqa: E402
from modules.image_picker import app as picker                   # noqa: E402
from modules.image_picker import cloudinary_sink, profile, upload_sources  # noqa: E402
from modules.image_picker.models import (                        # noqa: E402
    PickerClient, SavedImage, new_token, session, unique_slug,
)

flask_app = flask.Flask(__name__)
flask_app.config["SECRET_KEY"] = "picker-test-secret"
picker.register_image_picker(flask_app)
http = flask_app.test_client()


def sign_in():
    with http.session_transaction() as s:
        s["logged_in"] = True
        s["hub_user"] = "tester@smart1marketing.com"


def make_gallery(name, industry="general"):
    db = session()
    c = PickerClient(name=name, slug=unique_slug(db, name),
                     industry_key=industry, share_token=new_token())
    db.add(c)
    db.commit()
    return c.id, c.share_token


def env(**kw):
    for k, v in kw.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


# =====================================================================
section("Every place a client already keeps their photos")
# =====================================================================

env(PICKER_UPLOAD_SOURCES=None, PICKER_STOCK_SOURCES=None)
default = upload_sources.enabled()

# The point of the change. A client asked for "your photos" reaches for the
# place the photos already are, and it is almost never a folder on a laptop.
for key in ("google_drive", "google_photos", "dropbox", "facebook",
            "instagram", "image_search"):
    check(f"{key} is offered by default", key in default, True)
for key in ("local", "camera", "url"):
    check(f"{key} is still there", key in default, True)

# A tickbox that consents and then fails is worse than an absent feature.
for key in ("shutterstock", "getty", "istock", "unsplash"):
    check(f"{key} needs its Cloudinary add-on, so it is off", key in default, False)

env(PICKER_STOCK_SOURCES="shutterstock, getty")
with_stock = upload_sources.enabled()
check("a paid library switches on by name", "shutterstock" in with_stock, True)
check("and only the ones named", "istock" in with_stock, False)
check("without dropping the rest", "instagram" in with_stock, True)
env(PICKER_STOCK_SOURCES=None)

# The escape hatch, and the shape the old default had.
env(PICKER_UPLOAD_SOURCES="local,camera,url")
check("an explicit list wins outright", upload_sources.enabled(), ["local", "camera", "url"])

# A name the widget does not know draws a broken tab or no tab, and both read
# as our page being broken. Dropped, and handed back so a screen can name it.
env(PICKER_UPLOAD_SOURCES="local,onedrive,dropbox")
keep, unknown = upload_sources.configured()
check("an unrecognized source is dropped", keep, ["local", "dropbox"])
check("and named rather than swallowed", unknown, ["onedrive"])
env(PICKER_UPLOAD_SOURCES=None)


# =====================================================================
section("A per-source key is an override, and the client signs in either way")
# =====================================================================

env(PICKER_DROPBOX_APP_KEY=None, PICKER_GOOGLE_DRIVE_CLIENT_ID=None,
    PICKER_INSTAGRAM_CLIENT_ID=None)
check("Dropbox is offered on Cloudinary's own app", "dropbox" in upload_sources.enabled(), True)
# An empty dropboxAppKey is worse than none: the widget takes it at its word
# and the tab fails against an app key of "".
check("no key set means no key sent", upload_sources.widget_options(), {})

env(PICKER_DROPBOX_APP_KEY="dbx-123")
check("a key that is set reaches the widget",
      upload_sources.widget_options(), {"dropboxAppKey": "dbx-123"})

# Whose app is on the consent screen is not a question a staff screen has to
# answer — the client signs in to their own account either way, and the Hub
# never sees the password. What matters is that a key we do not have never
# hides a tab.
env(PICKER_GOOGLE_DRIVE_CLIENT_ID=None)
check("a source with no key of ours is still offered",
      "google_drive" in upload_sources.enabled(), True)
check("and sends no option for it", "googleDriveClientId" in upload_sources.widget_options(), False)
env(PICKER_DROPBOX_APP_KEY=None)


# =====================================================================
section("What the client is promised is what is switched on")
# =====================================================================

env(PICKER_UPLOAD_SOURCES="local,camera,url,dropbox")
line = upload_sources.client_line()
check("the sentence names Dropbox", "Dropbox" in line, True)
# A paragraph naming Instagram on a deployment where Instagram is off is a
# promise the panel cannot keep.
check("and does not name Instagram", "Instagram" in line, False)
env(PICKER_UPLOAD_SOURCES=None)
check("with everything on it names Instagram too",
      "Instagram" in upload_sources.client_line(), True)


# =====================================================================
section("A source switched off does not relabel a file already uploaded")
# =====================================================================

env(PICKER_UPLOAD_SOURCES="local,camera,url")
# Recording asks whether the name is one of ours, NOT whether it is on right
# now: a source turned off between the widget opening and the file landing
# must not file a real Instagram upload as "local", which is the one thing the
# gallery's source column exists for.
check("instagram is off", "instagram" in upload_sources.enabled(), False)
check("and still a name we recognize", upload_sources.known("instagram"), True)
check("a name we have never heard of is not", upload_sources.known("myspace"), False)
env(PICKER_UPLOAD_SOURCES=None)


# =====================================================================
section("The staff picker renders at all")
# =====================================================================

sign_in()
cid, token = make_gallery("Testy Marine Trim")

# It 500'd on every visit: the route includes the upload panel and never passed
# it `sources`, and `|tojson` over an Undefined raises while Flask is rendering.
r = http.get(f"/tools/image-picker/c/{cid}")
check("staff pick page answers 200", r.status_code, 200)
body = r.data.decode()
# The widget is opened by one shared script now, and the source tabs come
# back with the signature rather than being written into each page.
check("with the shared upload script on it", "picker-upload.js" in body, True)
check("and a folder chooser", 'id="uploadFolder"' in body, True)

r = http.get(f"/tools/image-picker/pick/{token}")
check("so does the client's own link", r.status_code, 200)
check("the client page carries the upload panel", 'id="uploadPanel"' in r.data.decode(), True)
check("the admin page answers", http.get("/tools/image-picker/").status_code, 200)

# The panel builds every upload URL by concatenation, so the base has to be an
# origin-and-mount with no trailing slash. It used to strip "/admin" -- a path
# this blueprint does not have -- so the slash stayed on and each signed upload
# went to "//api/upload-signature". Werkzeug 308s that, which a browser replays
# as a POST, so it worked; a proxy that answers 302 instead turns the client's
# upload into a bodyless GET, and nothing on the page says so.
panel = http.get(f"/tools/image-picker/pick/{token}").data.decode()
m = re.search(r'var BASE = ("(?:[^"\\]|\\.)*")(\.replace\([^;]*\))?;', panel)
check("the panel declares a base", bool(m), True)
base = json.loads(m.group(1))
if m.group(2):
    base = base.rstrip("/")
check("with no trailing slash on it", base.endswith("/"), False)
# and the URL it therefore builds is served without a redirect
r = http.post(base + "/api/upload-signature", json={})
check("the signature URL routes directly", r.status_code in (301, 302, 307, 308), False)


# =====================================================================
section("General Business is asked what the business is")
# =====================================================================

body = http.get(f"/tools/image-picker/pick/{token}").data.decode()
check("the two questions are on the page",
      "Tell us about your business" in body, True)
check("and the second one asks what they sell",
      "What do you sell" in body, True)

# A trade picked from the dropdown already has chips somebody wrote for it, so
# it is not asked — and a client's description must not override it.
cid2, token2 = make_gallery("Testy Heating", industry="hvac")
hvac = http.get(f"/tools/image-picker/pick/{token2}").data.decode()
import re as _re                                                  # noqa: E402
_panel = _re.search(r'id="profilePanel"\s*(hidden)?>', hvac)
check("an HVAC gallery renders the panel", bool(_panel), True)
check("and hides it — the trade already has chips somebody wrote",
      bool(_panel and _panel.group(1)), True)
check("a General Business gallery does not hide it",
      bool(_re.search(r'id="profilePanel"\s*>', body)), True)


# =====================================================================
section("What the model returns is clamped, not trusted")
# =====================================================================

wild = profile.clamp([
    {"label": "Boat seat re-covering",
     "queries": ["boat seat upholstery repair", "marine vinyl <script>", "", "x"],
     "negative": ["car seat"]},
    {"label": "No queries at all", "queries": []},          # a chip with an empty grid
    {"label": "", "queries": ["something"]},                # a chip with no name
    {"label": "T" * 200, "queries": ["canvas bimini top boat"]},
] + [{"label": f"Filler {n}", "queries": [f"filler query {n}"]} for n in range(20)])

check("a collection with no usable query is dropped",
      [c["label"] for c in wild].count("No queries at all"), 0)
check("so is one with no label", len(wild) <= profile.MAX_COLLECTIONS, True)
check("labels are capped", max(len(c["label"]) for c in wild) <= profile.MAX_LABEL_CHARS, True)
check("queries are capped per collection",
      max(len(c["queries"]) for c in wild) <= profile.MAX_QUERIES, True)
# These strings are handed to three provider APIs with three quoting rules.
check("markup never reaches a provider query",
      any("<" in q or ">" in q for c in wild for q in c["queries"]), False)
check("a two-character query is not a search",
      any(len(q) < 3 for c in wild for q in c["queries"]), False)
check("the key is derived from the label",
      wild[0]["key"], "boat_seat_re_covering")


# =====================================================================
section('"We could not ask the model" is not "there are no topics"')
# =====================================================================

built, err = profile.build(category="marine upholstery shop",
                           profile="we re-cover boat seats and canvas tops")
check("chips are built either way", bool(built["topics"]), True)
# A generic set presented as though a model had chosen it is the confident
# wrong answer this codebase keeps having to undo.
check("and say they were not written for this client", built["source"], "typed")
check("with the reason kept for staff", bool(err), True)
check("what they typed is kept verbatim", built["category"], "marine upholstery shop")
check("nothing is built from nothing", profile.build(category="", profile="")[0], {})


# =====================================================================
section("With a model reachable, the chips are about this business")
# =====================================================================

import hub.ai as hub_ai                                          # noqa: E402

_real_chat_json = hub_ai.chat_json
_asked = {}


def fake_chat_json(messages, **kw):
    _asked["prompt"] = messages[-1]["content"]
    _asked["purpose"] = kw.get("purpose")
    return {
        "topics": [
            {"label": "Back on the water", "queries": ["boat on lake summer",
                                                       "family boating sunny day"]},
            {"label": "Worn out seats", "queries": ["cracked boat seat vinyl"]},
        ],
        "services": [
            {"label": "Seat re-covering", "queries": ["marine vinyl upholstery work"],
             "negative": ["car seat"]},
        ],
    }


hub_ai.chat_json = fake_chat_json
built, err = profile.build(category="marine upholstery shop",
                           profile="boat seats, canvas tops, custom cushions")
hub_ai.chat_json = _real_chat_json

check("no error when the model answers", err, "")
check("the chips are the model's", built["source"], "ai")
check("labeled for this business", built["topics"][0]["label"], "Back on the water")
check("services come across too", built["services"][0]["label"], "Seat re-covering")
check("with the negative terms kept", built["services"][0]["negative"], ["car seat"])
# for_prompt()'s lesson in Smart 1 Ads: the model is handed the answers, not a
# category somebody guessed at.
check("what the client typed is in the prompt",
      "boat seats, canvas tops" in _asked["prompt"], True)
check("and the spend is filed under a purpose",
      _asked["purpose"], "business_profile_topics")


# =====================================================================
section("An answer that was captured is used")
# =====================================================================

r = http.post(f"/tools/image-picker/api/profile?t={token}",
              json={"category": "marine upholstery shop",
                    "profile": "boat seats, canvas tops, custom cushions"})
d = r.get_json()
check("the client can answer over their own share link", r.status_code, 200)
check("the answers save", d["ok"], True)
check("chips come back", len(d["profile"]["topics"]) > 0, True)
check("and the page is told we fell back", d["fell_back"], True)
# The curated search terms are the part of this that took work. Same rule
# taxonomy.public_industries() follows.
check("no search terms are shipped to the browser",
      any("queries" in c for c in d["profile"]["topics"]), False)

db = session()
saved = db.get(PickerClient, cid)
check("the description is on the row", saved.business_category, "marine upholstery shop")
check("and the collections with it", bool(json.loads(saved.ai_collections)["topics"]), True)

# The proposal builder's lesson: four discovery questions asked and never read.
check("a free-text search is blended with what they told us",
      profile.search_hint(saved), "marine upholstery shop")
check("their own collections are what General Business now browses",
      profile.applies(saved, "general"), True)
# Staff switching the selector to a real trade are asking for that trade.
check("switching to a real trade is not overridden",
      profile.applies(saved, "hvac"), False)

first_key = d["profile"]["topics"][0]["key"]
check("a chip resolves to a collection with real queries",
      bool((profile.collection(saved, "topic", first_key) or {}).get("queries")), True)
check("a chip nobody offered resolves to nothing",
      profile.collection(saved, "topic", "not_a_key"), None)

# Nothing is written from an empty form.
check("an empty answer is refused",
      http.post(f"/tools/image-picker/api/profile?t={token}",
                json={"category": "", "profile": ""}).status_code, 400)


# =====================================================================
section("The two questions are answered from what the Hub already knows")
# =====================================================================

# A client opening their link finds their business described and a "Change
# this" button, not a form -- worked out once, from the site scan and the
# record, and marked as ours so the page says where the words came from.
auto_cid, auto_token = make_gallery("Lakeside Marine Canvas")
_calls = []


def fake_answer_and_build(messages, **kw):
    _calls.append(kw.get("purpose"))
    if kw.get("purpose") == "business_profile_autofill":
        check("the Hub's facts are in the prompt",
              "Muskegon" in messages[-1]["content"], True)
        return {"category": "marine canvas and upholstery shop",
                "profile": "Bimini tops, boat covers and seat re-covering for lake boats."}
    check("the location anchors the topic terms",
          "Where they are: Muskegon, MI" in messages[-1]["content"], True)
    check("and the industry", "Industry on file: Marine" in messages[-1]["content"], True)
    return {"topics": [{"label": "On the lake", "queries": ["boat on lake summer"]}],
            "services": [{"label": "Bimini tops", "queries": ["boat bimini top canvas"]}]}


_db = session()
auto_client = _db.get(PickerClient, auto_cid)
with patch.object(profile, "hub_context",
                  return_value=("Client: Lakeside Marine Canvas\nCity: Muskegon, MI\nIndustry: Marine",
                                {"industry": "Marine", "city": "Muskegon", "state": "MI"})), \
        patch.object(hub_ai, "chat_json", side_effect=fake_answer_and_build):
    auto_out = profile.autofill(_db, auto_client)
check("the answers were worked out", auto_out["filled"], True)
check("two model calls, one per question set", _calls,
      ["business_profile_autofill", "business_profile_topics"])
_db = session()
auto_client = _db.get(PickerClient, auto_cid)
check("the category is on the row", auto_client.business_category, "marine canvas and upholstery shop")
pub = profile.public(auto_client)
check("the page is told the words were ours", pub["auto"], True)
check("and gets the chips", pub["topics"][0]["label"], "On the lake")
check("the model's context is kept, not invented",
      json.loads(auto_client.ai_collections)["context"]["city"], "Muskegon")

# Once. A second open spends nothing; so does a gallery whose client typed.
with patch.object(hub_ai, "chat_json", side_effect=AssertionError("must not be asked")):
    check("a second open asks the model nothing", profile.autofill(_db, auto_client)["attempted"], False)
    typed = _db.get(PickerClient, cid)
    check("a client who already answered is left alone", profile.autofill(_db, typed)["attempted"], False)

# A failure is marked so the next page load does not try again for ever, and
# the client still gets the form.
fail_cid, fail_token = make_gallery("Nothing Known Co")
fail_client = _db.get(PickerClient, fail_cid)
with patch.object(profile, "hub_context", return_value=("", {})):
    first = profile.autofill(_db, fail_client)
check("with nothing on file the questions are left for the client", first["filled"], False)
check("and the attempt is written down", first["attempted"], True)
check("so it is not repeated", profile.autofill(_db, fail_client)["attempted"], False)
check("the form still opens for them", profile.public(fail_client).get("topics", []), [])
r = http.get(f"/tools/image-picker/pick/{fail_token}")
check("and the share link renders", r.status_code, 200)
r = http.get(f"/tools/image-picker/pick/{auto_token}")
check("the described client's link says the words were worked out",
      "worked out from your website" in r.data.decode(), True)

# A real trade never gets the General Business questions answered for it.
hvac_cid, _ = make_gallery("Riverside HVAC", industry="hvac")
with patch.object(hub_ai, "chat_json", side_effect=AssertionError("must not be asked")):
    check("a curated trade is not auto-described",
          profile.autofill(_db, _db.get(PickerClient, hvac_cid))["attempted"], False)


# =====================================================================
section("Deleting a gallery")
# =====================================================================

destroyed = []


def fake_destroy(public_id, resource_type="image"):
    destroyed.append((public_id, resource_type))
    return public_id != "smart1/testy/stuck.jpg"      # one refuses, on purpose


cloudinary_sink.destroy = fake_destroy
picker.cloudinary_sink.destroy = fake_destroy

db = session()
for pid, rtype in (("smart1/testy/one.jpg", "image"),
                   ("smart1/testy/brochure.pdf", "raw"),
                   ("smart1/testy/stuck.jpg", "image")):
    db.add(SavedImage(client_id=cid, provider="local", provider_image_id=pid,
                      cloudinary_public_id=pid, cloudinary_url="https://x/" + pid,
                      resource_type=rtype, ghl_status="sent"))
db.commit()

# The name is typed. An OK button means the same thing whichever row was
# mis-tapped, and for a file the client uploaded ours is often the only copy.
r = http.post(f"/tools/image-picker/api/clients/{cid}/delete", json={"confirm": ""})
check("a blank confirmation deletes nothing", r.status_code, 400)
r = http.post(f"/tools/image-picker/api/clients/{cid}/delete",
              json={"confirm": "Testy Marine"})
check("nor does most of the name", r.status_code, 400)
check("the gallery is still there", session().get(PickerClient, cid) is not None, True)

r = http.post(f"/tools/image-picker/api/clients/{cid}/delete",
              json={"confirm": "testy marine trim"})     # case is not the point
d = r.get_json()
check("the exact name deletes it", d["ok"], True)
check("the row is gone", session().get(PickerClient, cid), None)
check("and its images with it",
      session().query(SavedImage).filter(SavedImage.client_id == cid).count(), 0)

# A brochure PDF asked for as an `image` comes back "not found", which the old
# signature reported as a clean success: row gone, file still in the account.
check("a PDF is destroyed as raw", ("smart1/testy/brochure.pdf", "raw") in destroyed, True)
check("an image as an image", ("smart1/testy/one.jpg", "image") in destroyed, True)

# "Deleted" and "deleted, and one file is still in the account" are different
# outcomes, and one tick for both is how somebody learns not to trust the tick.
check("what Cloudinary removed is counted", d["cloudinary_removed"], 2)
check("what it would not is counted apart", d["cloudinary_left"], 1)
check("and said out loud", "still in the account" in d["note"], True)
# Once a file is in the client's media library it may be in a funnel already.
check("the Suite copies are named as staying", d["left_in_suite"], 3)
check("deleting a gallery that is not there is a 404",
      http.post(f"/tools/image-picker/api/clients/{cid}/delete",
                json={"confirm": "Testy Marine Trim"}).status_code, 404)


# =====================================================================
section("The full gallery is reachable from a name")
# =====================================================================

# Client 360 knows a client's NAME; this module keys galleries on its own id.
# The resolver is the join, under provisioning's rules — exactly one gallery
# or none, never a substring — and every outcome lands somewhere that shows
# the client's images rather than an error about our own bookkeeping.
from modules.image_picker import provisioning                    # noqa: E402

# Its own gallery: the section above deletes Testy Marine Trim's, which is
# exactly the state a resolver must not answer a stale link for.
full_cid, _full_tok = make_gallery("Fullerton Awnings")

check("the helper answers the one gallery",
      provisioning.full_gallery_url("Fullerton Awnings"),
      f"/tools/image-picker/gallery/{full_cid}")
check("and an unknown name answers nothing rather than a guess",
      provisioning.full_gallery_url("Nobody At All"), "")

r = http.get("/tools/image-picker/gallery/for-client?name=Fullerton%20Awnings")
check("one gallery redirects to it", r.status_code, 302)
check("to the full gallery",
      r.headers["Location"].endswith(f"/tools/image-picker/gallery/{full_cid}"), True)

# The way back to the record rides the redirect, or the reader lands one hop
# from the client whose record they came from with no way back to it.
r = http.get("/tools/image-picker/gallery/for-client"
             "?name=Fullerton%20Awnings&c360=Icon%20Solar")
check("c360 is carried through the redirect",
      "c360=Icon%20Solar" in r.headers["Location"], True)

# No full gallery yet: everything the Hub holds for them outside one is the
# SEO pipeline's archive, so land there scoped to the name.
r = http.get("/tools/image-picker/gallery/for-client?name=Fresh%20Prospect%20LLC")
check("no upload gallery still has a client asset home", r.status_code, 200)
check("scoped to the client", "Fresh Prospect LLC" in r.data.decode(), True)
# A GET that created a gallery would be one a prefetch creates without
# anybody asking — the rule the upload-link endpoint is a POST for.
check("and the visit created nothing",
      provisioning.full_gallery_url("Fresh Prospect LLC"), "")

# Two galleries that could both be this client refuse and name both: picking
# either sends somebody into another client's gallery reading as this one's.
make_gallery("Twin Peaks Dental")
make_gallery("Twin Peaks Dental")
r = http.get("/tools/image-picker/gallery/for-client?name=Twin%20Peaks%20Dental")
check("two candidates are refused, not guessed", r.status_code, 200)
check("naming both", r.data.decode().count("Twin Peaks Dental") >= 2, True)
check("and the helper offers no link either",
      provisioning.full_gallery_url("Twin Peaks Dental"), "")

check("no name at all is refused",
      http.get("/tools/image-picker/gallery/for-client").status_code, 400)

_anon = flask_app.test_client()
r = _anon.get("/tools/image-picker/gallery/for-client?name=Fullerton%20Awnings")
check("a stranger is sent to the login", r.status_code, 302)
check("not into a gallery", "/login" in r.headers["Location"], True)


# =====================================================================
section("The delete button is on the page, and nothing else shouts")
# =====================================================================

admin = http.get("/tools/image-picker/").data.decode()
# A control nobody can see is a control that does not exist.
check("every gallery row carries a delete", 'class="btn quiet small danger delete"' in admin, True)
check("it says what will go", "cannot be undone" in admin, True)

# Nothing is reported when nothing needs acting on. A roster of green ticks
# restating that the keys we have always had are still set is read once and
# skipped for ever, and it pushes the client list below the fold.
env(PICKER_UPLOAD_SOURCES=None)
quiet = http.get("/tools/image-picker/").data.decode()
check("no source roster on a healthy page", "Where clients can upload from" in quiet, False)
check("and no services tick-list", "<h2>Services</h2>" in quiet, False)

# A name the widget does not know draws a broken tab. That one is a finding,
# and it names the variable somebody has to correct.
env(PICKER_UPLOAD_SOURCES="local,onedrive")
loud = http.get("/tools/image-picker/").data.decode()
check("an unrecognized source is reported", "Unrecognized upload source" in loud, True)
check("naming the source", "onedrive" in loud, True)
check("and the variable to fix it", "PICKER_UPLOAD_SOURCES" in loud, True)
env(PICKER_UPLOAD_SOURCES=None)


# =====================================================================
section("A file the gallery already has: keep it here too, copy it, or move it")
# =====================================================================
# A duplicate was reported and nothing else, so the person uploading was told
# it was already there and offered no way to say what they meant by sending it
# again. Three things can be meant and they are three different statements
# about the file, so each is asserted against what it actually does to the
# rows -- above all that a "move" creates nothing and destroys nothing.
from modules.image_picker import filing                           # noqa: E402
from hub import audit as _audit_log                               # noqa: E402

dup_id, dup_token = make_gallery("Duplicate Choice Co")
ASSET = "smart1-client-images/duplicate-choice-co/storefront"
ASSET_URL = "https://res.cloudinary.com/demo/image/upload/v1/" + ASSET


from sqlalchemy import select as _select                          # noqa: E402


def rows_for(public_id=ASSET):
    """Every gallery row pointing at one Cloudinary asset, oldest first."""
    return session().execute(
        _select(SavedImage)
        .where(SavedImage.cloudinary_public_id == public_id)
        .order_by(SavedImage.id)
    ).scalars().all()


first = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="spring-refresh", label="Spring refresh",
    project_name="Spring refresh", push_to_suite=False, create_client=False)
check("the first filing lands", (first.get("ok"), first.get("duplicate")),
      (True, None))

# --- Saying nothing is exactly what it was ---------------------------------
# Eleven tools file through here and four of them are finishing work with
# nobody watching, so the default cannot have moved.
again = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="autumn-sale", label="Autumn sale",
    project_name="Autumn sale", push_to_suite=False, create_client=False)
check("no choice still reports a duplicate", again.get("duplicate"), True)
check("and writes no second row", len(rows_for()), 1)
check("and leaves the row where it was",
      rows_for()[0].project_name, "Spring refresh")
# The screen cannot offer three choices without being told where the file
# already is, so both travel with the answer.
check("the reply names the three choices",
      sorted(again.get("choices") or []), ["duplicate", "keep", "move"])
check("and where it is filed now", again.get("filed_under"), "Spring refresh")

# --- Keep: two rows, one asset --------------------------------------------
kept = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="autumn-sale", label="Autumn sale",
    project_name="Autumn sale", push_to_suite=False, create_client=False,
    on_duplicate="keep")
check("keep files a second row", (kept.get("ok"), kept.get("created")), (True, True))
check("two rows now", len(rows_for()), 2)
check("both pointing at one asset",
      len({r.cloudinary_public_id for r in rows_for()}), 1)
# The unique constraint is (client, provider, provider_image_id): two rows for
# one asset only exist because the second one's provider id is spelled with
# the project on the end.
check("with different provider ids",
      len({r.provider_image_id for r in rows_for()}), 2)
check("the original is untouched", rows_for()[0].project_name, "Spring refresh")
check("and the twin carries the new project",
      rows_for()[1].project_name, "Autumn sale")

# Pressing it again is how somebody checks the first press took. It must not
# be how a third row appears.
twice = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="autumn-sale", label="Autumn sale",
    project_name="Autumn sale", push_to_suite=False, create_client=False,
    on_duplicate="keep")
check("keeping it twice creates nothing",
      (twice.get("ok"), twice.get("created")), (True, False))
check("still two rows", len(rows_for()), 2)

# --- Move: nothing created, nothing deleted -------------------------------
before_ids = [r.id for r in rows_for()]
moved = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="winter-promo", label="Winter promo",
    project_name="Winter promo", push_to_suite=False, create_client=False,
    on_duplicate="move")
check("move reports itself", (moved.get("ok"), moved.get("action")),
      (True, "move"))
check("it creates no row", len(rows_for()), 2)
check("and deletes none", [r.id for r in rows_for()], before_ids)
check("the row is rewritten in place", rows_for()[0].project_name, "Winter promo")
check("folder with it", rows_for()[0].collection_label, "Winter promo")

# --- Duplicate: the one branch that spends storage ------------------------
# With no Cloudinary configured there is nowhere to put a second copy, and
# saying so is the answer -- a row filed against the original's public_id
# would be the original wearing a new row, which is the one outcome this
# branch must not produce, since somebody is about to edit or delete it.
refused = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="flyer", label="Flyer", project_name="Flyer",
    push_to_suite=False, create_client=False, on_duplicate="duplicate")
check("a copy that cannot be stored is refused", refused.get("ok"), False)
check("and says why", "could not be stored" in (refused.get("error") or ""), True)
check("with no row written for it", len(rows_for()), 2)

# And the happy path, with the shared storage layer answering.
import hub.storage as _storage                                    # noqa: E402

_real_put_remote = _storage.put_remote
COPY_ID = ASSET + "-copy-deadbeef"


def _fake_put_remote(kind, url, **kw):
    return _storage.StoredAsset(
        public_id=str(kw.get("public_id") or COPY_ID), url=ASSET_URL + "-copy",
        resource_type="image", bytes=4242, backend="cloudinary",
        folder="", checksum="")


_storage.put_remote = _fake_put_remote
copied = filing.file_asset(
    client_name="Duplicate Choice Co", public_id=ASSET, url=ASSET_URL,
    kind="upload", key="flyer", label="Flyer", project_name="Flyer",
    push_to_suite=False, create_client=False, on_duplicate="duplicate")
_storage.put_remote = _real_put_remote
check("duplicating files a row", (copied.get("ok"), copied.get("created")),
      (True, True))
check("against a second asset, not the first",
      copied["image"]["public_id"] != ASSET, True)
check("so the original still has two rows", len(rows_for()), 2)
check("and the copy is its own row", len(rows_for(copied["image"]["public_id"])), 1)

# --- What it wrote down ---------------------------------------------------
# A filing decision about a client's own file, with the client on it, or the
# record cannot say who chose what.
entries = _audit_log.read(limit=50, module="image_picker",
                          type_="gallery_duplicate")
choices_logged = sorted({e.get("choice") for e in entries})
check("every choice is recorded", choices_logged, ["duplicate", "keep", "move"])
check("against the client", {e.get("client") for e in entries},
      {"Duplicate Choice Co"})

# --- The panel asks; the client's own page does not -----------------------
# The choice is offered where somebody can act on it. On the share link the
# reply is what it always was, because "project" is our word and a client
# sending photographs in has no way to answer a filing question.
WIDGET = f"smart1-client-images/duplicate-choice-co/uploads/storefront"
WIDGET_URL = "https://res.cloudinary.com/demo/image/upload/v1/" + WIDGET
UPLOAD = {"public_id": WIDGET, "secure_url": WIDGET_URL, "source": "local",
          "original_filename": "storefront"}

sign_in()
# Staff uploading through the panel was refused outright: the helper behind
# both this route and the signature asked `g.hub_user`, which nothing in the
# Hub sets, so the widget never opened and the record never landed. Named
# here because a duplicate choice offered on a panel that cannot upload is a
# feature nobody can reach.
first_up = http.post("/tools/image-picker/api/uploads",
                     json=dict(UPLOAD, client_id=dup_id,
                               project="Spring refresh")).get_json()
check("a signed-in staff upload is recorded", first_up.get("ok"), True)
check("against them rather than the client",
      first_up["image"]["saved_by"], "tester@smart1marketing.com")
check("a named project lands on the row",
      rows_for(WIDGET)[0].project_name, "Spring refresh")

# Eleven tools file through file_asset(). The default has to be the answer
# they have always had, or a branch none of them asked for is taken on their
# behalf with nobody watching.
import inspect as _inspect                                        # noqa: E402

check("saying nothing is the default",
      _inspect.signature(filing.file_asset).parameters["on_duplicate"].default, "")
staff_say = http.post("/tools/image-picker/api/uploads",
                      json=dict(UPLOAD, client_id=dup_id,
                                project="Autumn sale")).get_json()
check("staff are offered the choices",
      sorted(staff_say.get("choices") or []), ["duplicate", "keep", "move"])
check("and told where it already is", staff_say.get("filed_under"), "Spring refresh")
staff_did = http.post("/tools/image-picker/api/uploads",
                      json=dict(UPLOAD, client_id=dup_id, project="Autumn sale",
                                on_duplicate="move")).get_json()
check("and the panel's choice is acted on", staff_did.get("action"), "move")
check("in place", (len(rows_for(WIDGET)), rows_for(WIDGET)[0].project_name),
      (1, "Autumn sale"))

with http.session_transaction() as s:
    s.clear()
client_say = http.post("/tools/image-picker/api/uploads",
                       json=dict(UPLOAD, token=dup_token, project="Spring refresh",
                                 on_duplicate="move")).get_json()
check("a client is offered none", client_say.get("choices"), [])
check("and their choice is not acted on", client_say.get("action"), None)
check("so nothing moved", rows_for(WIDGET)[0].project_name, "Autumn sale")
sign_in()

# The panel itself, on the page it is included on: the folder chooser is on
# BOTH pages now, by request -- a client sending "the new logo versions"
# wants them beside the logos they sent last month -- while the duplicate
# question (keep / copy / move) stays a staff control.
gallery_page = http.get(f"/tools/image-picker/gallery/{dup_id}").data.decode()
check("the staff panel asks for a folder", 'id="uploadFolder"' in gallery_page, True)
check("and has somewhere to ask the question", 'id="uploadDupes"' in gallery_page, True)
share_page = http.get(f"/tools/image-picker/pick/{dup_token}").data.decode()
check("the client's page offers the folder chooser too", 'id="uploadFolder"' in share_page, True)

# --- A client's folder lands, and the folders they see are their own -------
with http.session_transaction() as s:
    s.clear()
LOGO_UP = f"smart1-client-images/duplicate-choice-co/uploads/mark-blue"
client_folder = http.post("/tools/image-picker/api/uploads",
                          json=dict(UPLOAD, public_id=LOGO_UP,
                                    secure_url="https://res.cloudinary.com/demo/image/upload/v1/" + LOGO_UP,
                                    original_filename="mark-blue.png",
                                    token=dup_token, folder="Logos")).get_json()
check("a client's upload into a named folder is recorded", client_folder.get("ok"), True)
check("under the folder they chose", rows_for(LOGO_UP)[0].project_name, "Logos")
check("as a client upload, never internal",
      rows_for(LOGO_UP)[0].collection_kind, "upload")
folders = http.get(f"/tools/image-picker/api/folders?t={dup_token}").get_json()
check("the share link can read its own folders", folders.get("ok"), True)
by_label = {f["label"]: f for f in folders["folders"]}
check("the five sections are always offered",
      [f["label"] for f in folders["folders"] if f["default"]],
      ["Client Uploads", "Creative", "Hub Projects", "Logos", "Internal"])
check("and a logo typed into 'Logos' counts in the Logos section",
      by_label["Logos"]["count"], 1)
check("a client cannot claim the internal flag",
      http.post("/tools/image-picker/api/uploads",
                json=dict(UPLOAD, public_id=LOGO_UP + "-2",
                          secure_url="https://res.cloudinary.com/demo/image/upload/v1/" + LOGO_UP + "-2",
                          token=dup_token, internal=True)).get_json()["image"]["collection_kind"],
      "upload")
sign_in()
INTERNAL_UP = f"smart1-client-images/duplicate-choice-co/uploads/drive-logo"
staff_internal = http.post("/tools/image-picker/api/uploads",
                           json=dict(UPLOAD, public_id=INTERNAL_UP,
                                     secure_url="https://res.cloudinary.com/demo/image/upload/v1/" + INTERNAL_UP,
                                     original_filename="drive-logo.png",
                                     client_id=dup_id, internal=True, folder="Logos")).get_json()
check("a staff upload from our own resources is internal",
      staff_internal["image"]["collection_kind"], "internal")
from modules.image_picker import catalog as _catalog                # noqa: E402
organized = _catalog.organize(staff_internal["image"])
check("but a folder named Logos still lands in the Logos section",
      organized["section"], "logos")
check("under the one Logos folder", organized["folder"], "Logos")

# --- Every upload is queued for its SEO copy, and never made here ---------
from modules.image_picker import optimize as _optimize              # noqa: E402
from modules.image_picker.models import ImageOptimization           # noqa: E402
_db = session()
queued = _db.execute(_select(ImageOptimization).where(
    ImageOptimization.image_id == staff_internal["image"]["id"])).scalar_one_or_none()
check("the upload queued an SEO copy", queued is not None and queued.state, "pending")
check("and nothing was made in the request", queued.optimized_url, None)
prog = _optimize.progress(_db, dup_id)
check("progress is measured", prog["measured"], True)
check("and counts the pending copies", prog["pending"] >= 1, True)
check("a document is skipped rather than queued",
      _optimize.applies(SavedImage(resource_type="raw", filename="brochure",
                                   cloudinary_url="https://x.test/brochure")), False)
check("an SVG mark is left alone",
      _optimize.applies(SavedImage(resource_type="image", filename="mark.svg",
                                   cloudinary_url="https://x.test/mark.svg")), False)
check("a photograph applies",
      _optimize.applies(SavedImage(resource_type="image", filename="IMG_1.jpg",
                                   cloudinary_url="https://x.test/IMG_1.jpg")), True)
# Under load the sweep steps aside and says so, rather than running or
# silently doing nothing.
with patch.object(_optimize, "busy", return_value=(True, "load average 9.00 is above 3.00 on 2 cores")), \
        patch.object(_optimize, "_configured", return_value=True):
    deferred = _optimize.run_backlog()
check("the sweep defers under load", deferred.get("deferred"), True)
check("and names why", "load average" in deferred.get("why", ""), True)
check("without touching a row", _optimize.progress(session(), dup_id)["pending"], prog["pending"])
check("an unconfigured Hub skips rather than errors",
      "skipped" in _optimize.run_backlog(), True)


class _Stored:
    public_id = "smart1-client-images/duplicate-choice-co/optimized/blue-mark-1"
    url = "https://res.cloudinary.com/demo/image/upload/v1/optimized/blue-mark-1.webp"
    note = ""


def _png_bytes():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (20, 90, 160)).save(buf, "JPEG", quality=95)
    return buf.getvalue()


told = []
with patch.object(_optimize, "_configured", return_value=True), \
        patch.object(_optimize, "busy", return_value=(False, "")), \
        patch.object(_optimize, "_fetch", return_value=(_png_bytes(), "")), \
        patch("hub.storage.put", return_value=_Stored()), \
        patch("modules.image_picker.notices.optimized_all",
              side_effect=lambda name, n: told.append((name, n)) or 1):
    ran = _optimize.run_backlog(limit=50)
check("the sweep makes the copies", ran.get("optimized", 0) >= 1, True)
_db = session()
done = _db.execute(_select(ImageOptimization).where(
    ImageOptimization.image_id == staff_internal["image"]["id"])).scalar_one()
check("the row is done", done.state, "done")
check("with a web-ready name", done.seo_filename.endswith(".webp"), True)
check("a delivery URL", done.optimized_url.startswith("https://"), True)
check("and a smaller file than it started with",
      (done.bytes_after or 0) < (done.bytes_before or 0), True)
check("named by the fallback with no key set", done.named_by, "fallback")
original = _db.get(SavedImage, staff_internal["image"]["id"])
check("the original's URL is untouched",
      original.cloudinary_url, "https://res.cloudinary.com/demo/image/upload/v1/" + INTERNAL_UP)
check("and its filename", original.filename, "drive-logo.png")
check("the people on the account are told when every copy is made",
      told and told[0][0], "Duplicate Choice Co")
after = _optimize.progress(_db, dup_id)
check("the counter reads all done", after["pending"], 0)
copies = _optimize.copies_for(_db, [original.id])
check("the gallery can draw the copy beside the original",
      copies[original.id]["url"], _Stored.url)


# ---------------------------------------------------------------------------
# Late columns: a boolean column with a numeric default never lands on Postgres
# ---------------------------------------------------------------------------
# `external` shipped as "BOOLEAN DEFAULT 0". SQLite takes it, Postgres refuses
# an integer default on a boolean, the ALTER was swallowed, and every client
# gallery answered "Couldn't load the gallery" because `image_picker_images.
# external` did not exist. The DDL is written once here and run on two engines,
# so the one that only breaks in production has a test that fails locally.
from modules.image_picker import models as _models

_bad = [(t, c, d) for t, c, d in _models._LATE_COLUMNS
        if "BOOL" in d.upper() and any(ch.isdigit() for ch in d.split("DEFAULT")[-1])]
check("no boolean late column defaults to a number", _bad, [])

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
