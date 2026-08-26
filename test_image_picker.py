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
import shutil
import sys
import tempfile
from pathlib import Path

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
check("an unrecognised source is dropped", keep, ["local", "dropbox"])
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
check("and still a name we recognise", upload_sources.known("instagram"), True)
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
check("with the widget's sources in it", '"instagram"' in body, True)

r = http.get(f"/tools/image-picker/pick/{token}")
check("so does the client's own link", r.status_code, 200)
check("the client page carries the upload panel", 'id="uploadPanel"' in r.data.decode(), True)
check("the admin page answers", http.get("/tools/image-picker/").status_code, 200)


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
check("labelled for this business", built["topics"][0]["label"], "Back on the water")
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
check("an unrecognised source is reported", "Unrecognised upload source" in loud, True)
check("naming the source", "onedrive" in loud, True)
check("and the variable to fix it", "PICKER_UPLOAD_SOURCES" in loud, True)
env(PICKER_UPLOAD_SOURCES=None)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
