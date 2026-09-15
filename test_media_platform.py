"""Core Media Library schema and HTTP contract.

Run directly so it matches the repository's dependency-light regression tests.
"""
import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
TMP = tempfile.mkdtemp(prefix="s1media_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "media.db")
os.environ["SECRET_KEY"] = "media-platform-test"

import flask  # noqa: E402
from PIL import Image  # noqa: E402
from modules.image_picker import app as picker  # noqa: E402
from modules.image_picker import intelligence, vision  # noqa: E402
from modules.image_picker.models import (  # noqa: E402
    MediaAssetDetail, MediaSearchDocument, PickerClient, SavedImage, new_token,
    session, unique_slug,
)

passed = failed = 0


def check(label, got, want):
    global passed, failed
    if got == want:
        passed += 1
        print(f"  ok    {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


app = flask.Flask(__name__)
app.config["SECRET_KEY"] = "media-platform-test"
picker.register_image_picker(app)
http = app.test_client()

db = session()
client = PickerClient(name="Media Test Co", slug=unique_slug(db, "Media Test Co"),
                      industry_key="general", kind="client",
                      hub_client_id="n:media-test", share_token=new_token())
db.add(client)
db.flush()
asset = SavedImage(
    client_id=client.id, provider="instagram", provider_image_id="post-1",
    source_url="https://instagram.example/post-1", filename="technician.jpg",
    alt_text="Technician repairing an air conditioner", resource_type="image",
    cloudinary_public_id="clients/media-test/technician",
    cloudinary_url="https://res.cloudinary.com/demo/image/upload/technician.jpg",
    width=1800, height=1200, bytes=450000, saved_by="client",
)
db.add(asset)
db.commit()
client_id, asset_id = client.id, asset.id

unauth = http.get(f"/api/clients/{client_id}/media")
check("Media API fails closed", unauth.status_code, 401)

with http.session_transaction() as state:
    state["logged_in"] = True
    state["hub_user"] = "tester@smart1marketing.com"

listed = http.get(f"/api/clients/{client_id}/media").get_json()
check("canonical asset is listed", listed["assets"][0]["id"], asset_id)
check("summary identifies a social asset", listed["summary"]["types"]["social"], 1)
check("source provenance survives", listed["assets"][0]["source"], "instagram")

found = http.get(f"/api/clients/{client_id}/media/search?q=air+conditioner").get_json()
check("metadata search finds the asset", found["matched"], 1)

created = http.post(f"/api/clients/{client_id}/media/collections",
                    json={"name": "AC Repair", "collection_type": "campaign"})
check("collection is created", created.status_code, 201)
collection_id = created.get_json()["collection"]["id"]
membership = http.post(f"/api/media/{asset_id}/collections",
                       json={"collection_id": collection_id}).get_json()
check("asset joins collection", membership["created"], True)

unapproved = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=advertising").get_json()
check("social provenance is not advertising approval",
      unapproved["recommendations"], [])

updated = http.patch(f"/api/media/{asset_id}", json={
    "rights_status": "Client Approved", "approved_for_web": True,
    "approved_for_paid_media": True, "brand_asset_type": "Approved Background",
    "website_score": 92,
}).get_json()["asset"]
check("rights are explicit", updated["rights_status"], "Client Approved")
check("web approval is stored", updated["approved_for_web"], True)

link = http.post(f"/api/media/{asset_id}/links", json={
    "entity_type": "website_page", "entity_id": "ac-repair",
    "usage_type": "hero",
}).get_json()
check("project link is created", link["created"], True)
link_again = http.post(f"/api/media/{asset_id}/links", json={
    "entity_type": "website_page", "entity_id": "ac-repair",
    "usage_type": "hero",
}).get_json()
check("project link is idempotent", link_again["created"], False)

used = http.post(f"/api/media/{asset_id}/usage", json={
    "tool": "smart-1-sites", "campaign_id": "summer-2026",
    "creative_id": "homepage-v2", "placement": "homepage hero",
})
check("usage event is accepted", used.status_code, 201)

detail = http.get(f"/api/media/{asset_id}").get_json()["asset"]
check("collection appears on asset", detail["collections"][0]["name"], "AC Repair")
check("link appears on asset", detail["links"][0]["entity_id"], "ac-repair")
check("usage appears on asset", detail["usage"][0]["creative_id"], "homepage-v2")

recommended = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=website").get_json()
check("stored suitability drives recommendations",
      recommended["recommendations"][0]["recommendation_score"], 92)

# Phase 2 local intelligence is deterministic and requires no AI tokens.
pixels = BytesIO()
Image.new("RGB", (1800, 1000), (80, 140, 190)).save(pixels, format="JPEG")
image_bytes = pixels.getvalue()
with session() as phase2_db:
    first = phase2_db.get(SavedImage, asset_id)
    intelligence.inspect_asset(phase2_db, first, downloader=lambda _: image_bytes)
    duplicate = SavedImage(
        client_id=client_id, provider="upload", provider_image_id="upload-2",
        filename="technician-copy.jpg", resource_type="image",
        cloudinary_url="https://res.cloudinary.com/demo/image/upload/technician-copy.jpg",
    )
    phase2_db.add(duplicate)
    phase2_db.flush()
    intelligence.inspect_asset(phase2_db, duplicate, downloader=lambda _: image_bytes)
    phase2_db.commit()
    duplicate_id = duplicate.id

with session() as phase2_db:
    first_detail = phase2_db.query(MediaAssetDetail).filter_by(asset_id=asset_id).one()
    duplicate_detail = phase2_db.query(MediaAssetDetail).filter_by(
        asset_id=duplicate_id).one()
    search_document = phase2_db.query(MediaSearchDocument).filter_by(
        asset_id=asset_id).one()
check("exact content gets one stable SHA-256 fingerprint",
      len(first_detail.duplicate_hash or ""), 64)
check("duplicate detection preserves a canonical asset",
      duplicate_detail.duplicate_of, asset_id)
check("local quality scoring is persisted",
      isinstance(first_detail.quality_score, int), True)
check("metadata search document is materialized",
      "air conditioner" in search_document.search_text, True)

original_chat_json = vision._hub_ai.chat_json
vision._hub_ai.chat_json = lambda *args, **kwargs: {
    "description": "A technician repairs an outdoor air conditioner.",
    "alt": "Image of a technician repairing an air conditioner",
    "tags": ["technician", "hvac", "invented-tag"],
    "subjects": ["Technician", "air conditioner", "Technician"],
    "category": "service", "people_count": 1,
    "indoor_outdoor": "outdoor", "service_product": "AC repair",
    "seo_filename": "Technician AC Repair.jpg", "website_score": 88,
    "social_score": 82, "advertising_score": 79, "hero_score": 91,
    "composition_open_space": "left side",
}
try:
    analyzed = vision.describe_image(asset)
finally:
    vision._hub_ai.chat_json = original_chat_json
check("AI analysis is methodology-versioned",
      analyzed["analysis_version"].startswith("media-vision-v2:"), True)
check("AI subjects are normalized and de-duplicated",
      analyzed["subjects"], ["Technician", "air conditioner"])
check("SEO filename retains one source extension",
      analyzed["seo_filename"], "technician-ac-repair.jpg")
check("AI suitability scores are bounded and returned",
      analyzed["hero_score"], 91)

filtered = http.get(
    f"/api/clients/{client_id}/media?min_quality=1&unused=true").get_json()
check("quality and unused filters compose", filtered["matched"], 1)
check("API exposes semantic-index readiness",
      filtered["search"]["indexed"] >= 1, True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
