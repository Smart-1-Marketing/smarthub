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
from modules.image_picker import collectors, intelligence, vision  # noqa: E402
from modules.image_picker.models import (  # noqa: E402
    MediaAssetDetail, MediaImportRun, MediaSearchDocument, PickerClient,
    SavedImage, new_token, session, unique_slug,
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

# Phase 3 connectors: social/manual sources reuse the signed widget; website
# collection is queued and records into these same canonical rows.
imports = http.get(f"/api/clients/{client_id}/media/imports").get_json()
connector_modes = {row["key"]: row["mode"] for row in imports["connectors"]}
check("Facebook reuses the signed upload connector",
      connector_modes["facebook"], "upload_widget")
check("Instagram reuses the signed upload connector",
      connector_modes["instagram"], "upload_widget")
check("website collection is a queued connector",
      connector_modes["website"], "queued")

private_site = http.post(f"/api/clients/{client_id}/media/imports/website",
                         json={"url": "http://127.0.0.1/private"})
check("unconfigured website collection fails closed", private_site.status_code, 503)

original_storage_ready = collectors.cloudinary_sink.configured
collectors.cloudinary_sink.configured = lambda: True
private_site = http.post(f"/api/clients/{client_id}/media/imports/website",
                         json={"url": "http://127.0.0.1/private"})
check("private website targets are rejected", private_site.status_code, 400)

queued = http.post(f"/api/clients/{client_id}/media/imports/website",
                   json={"url": "https://client.example"})
check("website collection is accepted asynchronously", queued.status_code, 202)
run_id = queued.get_json()["run"]["id"]
queued_again = http.post(f"/api/clients/{client_id}/media/imports/website",
                         json={"url": "https://client.example/"})
check("the same active website run is idempotent",
      queued_again.get_json()["created"], False)
collectors.cloudinary_sink.configured = original_storage_ready

pages = {
    "https://client.example/robots.txt": ("User-agent: *\nDisallow:", "text/plain"),
    "https://client.example/sitemap.xml": ("not xml", "application/xml"),
    "https://client.example/": (
        '<html><head><meta property="og:image" content="/hero.jpg"></head>'
        '<body><img src="/team.jpg" alt="Service team"><a href="/services">Services</a>'
        '<a href="https://other.example/offsite">Elsewhere</a></body></html>', "text/html"),
    "https://client.example/services": (
        '<html><body><img src="/team.jpg"><img src="/repair.webp" alt="AC repair"></body></html>',
        "text/html"),
}


def fake_fetch(url):
    body, content_type = pages[url]
    return body, content_type, url


def fake_upload(**kwargs):
    name = kwargs["public_id"]
    return {"public_id": "clients/media-test/website/" + name,
            "secure_url": "https://res.cloudinary.com/demo/image/upload/" + name + ".jpg",
            "delivery_url": "https://res.cloudinary.com/demo/image/upload/" + name + ".jpg",
            "width": 1600, "height": 900, "bytes": 120000}


collected = collectors.process_website_run(
    run_id, fetcher=fake_fetch, uploader=fake_upload,
    url_validator=lambda url: url, max_seconds=10)
check("website run completes", collected["state"], "completed")
check("unique images across pages are imported once", collected["imported"], 3)
with session() as phase3_db:
    website_assets = phase3_db.query(SavedImage).filter_by(
        client_id=client_id, provider="website").all()
    run = phase3_db.get(MediaImportRun, run_id)
    repair = next(row for row in website_assets if row.source_url.endswith("repair.webp"))
    repair_detail = phase3_db.query(MediaAssetDetail).filter_by(asset_id=repair.id).one()
check("website assets use stable source identities",
      all(row.provider_image_id.startswith("sha256:") for row in website_assets), True)
check("original image URL is retained", repair.source_url,
      "https://client.example/repair.webp")
check("source page provenance is retained", repair_detail.source_post_url,
      "https://client.example/services")
check("import history records discovered assets", run.discovered, 3)

# Phase 4 consumers all read through one rights-aware contract. Pending assets,
# expired rights, and duplicate rows never leak into production pickers.
sites_media = http.get(
    f"/api/clients/{client_id}/media/for/sites").get_json()
check("Sites receives the approved canonical asset", sites_media["matched"], 1)
check("consumer response carries a stable asset identity",
      sites_media["assets"][0]["media_asset_id"], asset_id)
check("duplicate and pending assets are withheld", sites_media["withheld"], 4)

social_media = http.get(
    f"/api/clients/{client_id}/media/for/social").get_json()
check("web approval does not imply social approval", social_media["matched"], 0)
http.patch(f"/api/media/{asset_id}", json={"approved_for_social": True})
social_media = http.get(
    f"/api/clients/{client_id}/media/for/social?q=air+conditioner").get_json()
check("social receives an explicitly approved asset", social_media["matched"], 1)
for consumer in ("sites", "landing_pages", "creative_builder",
                 "proposal_builder", "social", "email", "video_ctv"):
    response = http.get(
        f"/api/clients/{client_id}/media/for/{consumer}").get_json()
    check(f"{consumer} shares the canonical media contract",
          response["assets"][0]["media_asset_id"], asset_id)

use_body = {
    "consumer": "social", "entity_id": "post-123",
    "usage_type": "post_image", "campaign_id": "fall-2026",
    "creative_id": "post-123", "placement": "feed image",
}
consumer_use = http.post(f"/api/media/{asset_id}/use", json=use_body).get_json()
check("consumer use creates a project link", consumer_use["link_created"], True)
check("consumer use creates an attribution event", consumer_use["usage_created"], True)
consumer_use_again = http.post(
    f"/api/media/{asset_id}/use", json=use_body).get_json()
check("consumer project links are idempotent",
      consumer_use_again["link_created"], False)
check("consumer attribution events are idempotent",
      consumer_use_again["usage_created"], False)

phase4_detail = http.get(f"/api/media/{asset_id}").get_json()["asset"]
check("consumer link uses the tool entity type",
      any(row["entity_type"] == "social_post" and
          row["entity_id"] == "post-123" for row in phase4_detail["links"]), True)
check("consumer use records the normalized tool key",
      any(row["tool"] == "social" and
          row["creative_id"] == "post-123" for row in phase4_detail["usage"]), True)
unknown_consumer = http.get(
    f"/api/clients/{client_id}/media/for/not-a-tool")
check("unknown consumers fail closed", unknown_consumer.status_code, 400)

# Phase 5: deterministic inventory health, explainable recommendations, and
# client-wide usage history. These deliberately make no performance claim.
health = http.get(f"/api/clients/{client_id}/media/health").get_json()["health"]
check("Media Health is a bounded coverage score", health["score"], 60)
check("Media Health exposes the five equal checks", len(health["categories"]), 5)
check("missing logo is an actionable collection opportunity",
      "logo" in {gap["key"] for gap in health["opportunities"]}, True)
check("missing video is visible rather than invented",
      "video" in {gap["key"] for gap in health["opportunities"]}, True)

hero = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=hero").get_json()
check("hero suggestions require desktop-ready approved media",
      hero["recommendations"][0]["id"], asset_id)
campaign = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=campaign&q=technician").get_json()
check("campaign recommendations explain metadata relevance",
      "matches" in " ".join(campaign["recommendations"][0]["reasons"]), True)
check("campaign score is labelled non-performance",
      "not performance-based" in campaign["method"], True)

history = http.get(f"/api/clients/{client_id}/media/usage").get_json()
check("usage history includes all recorded tools", history["total"], 2)
check("usage history retains canonical asset identity",
      {row["asset_id"] for row in history["usage"]}, {asset_id})
check("usage history exposes campaign and placement",
      any(row["campaign_id"] == "fall-2026" and row["placement"] == "feed image"
          for row in history["usage"]), True)

http.patch(f"/api/media/{asset_id}", json={"rights_status": "Do Not Use"})
withheld = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=website").get_json()
check("recommendations respect a revoked right", withheld["recommendations"], [])
http.patch(f"/api/media/{asset_id}", json={"rights_status": "Client Approved"})
http.patch(f"/api/media/{asset_id}", json={
    "license_expiration": "2020-01-01T00:00:00Z"})
expired = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=website").get_json()
check("an expired license is not recommended", expired["recommendations"], [])
http.patch(f"/api/media/{asset_id}", json={"license_expiration": None})
http.patch(f"/api/media/{duplicate_id}", json={
    "rights_status": "Client Approved", "approved_for_web": True})
deduplicated = http.get(
    f"/api/clients/{client_id}/media/recommendations?use=website").get_json()
check("an approved duplicate is still not recommended",
      [row["id"] for row in deduplicated["recommendations"]], [asset_id])
check("an unknown recommendation use fails closed", http.get(
    f"/api/clients/{client_id}/media/recommendations?use=unknown").status_code, 400)
check("Media Health requires staff authentication", app.test_client().get(
    f"/api/clients/{client_id}/media/health").status_code, 401)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
