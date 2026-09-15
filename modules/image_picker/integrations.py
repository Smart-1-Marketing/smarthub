"""One internal Media Library contract for every SmartHub consumer.

Consumers select canonical assets here and record links/usage here. This keeps
rights gates, duplicate suppression, identity resolution, and response shape
the same across Sites, landing pages, creative, proposals, social, email, and
video instead of letting each tool grow its own gallery reader.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from .models import (
    ImageDescription, MediaAssetDetail, MediaAssetLink, MediaUsage, SavedImage,
    session,
)
from .platform import asset_dict, resolve_library

CONSUMERS = {
    "sites": {"entity_type": "website_page", "approval": "approved_for_web",
              "score": "website_score", "resources": {"image"}},
    "landing_pages": {"entity_type": "landing_page", "approval": "approved_for_web",
                      "score": "website_score", "resources": {"image"}},
    "creative_builder": {"entity_type": "creative", "approval": "approved_for_paid_media",
                         "score": "advertising_score", "resources": {"image", "video"}},
    "proposal_builder": {"entity_type": "proposal", "approval": "approved_for_web",
                         "score": "website_score", "resources": {"image"}},
    "social": {"entity_type": "social_post", "approval": "approved_for_social",
               "score": "social_score", "resources": {"image", "video"}},
    "email": {"entity_type": "email", "approval": "approved_for_web",
              "score": "website_score", "resources": {"image"}},
    "video_ctv": {"entity_type": "video", "approval": "approved_for_paid_media",
                  "score": "advertising_score", "resources": {"image", "video"}},
}


def consumer_names() -> list[str]:
    return list(CONSUMERS)


def assets_for(client_ref: str, consumer: str, *, limit: int = 60,
               query: str = "", include_pending: bool = False) -> dict:
    """Return eligible canonical assets for one named SmartHub consumer."""
    consumer_key = str(consumer or "").strip().lower()
    rule = CONSUMERS.get(consumer_key)
    if rule is None:
        return {"ok": False, "assets": [], "error": "Unknown media consumer."}
    try:
        db = session()
        client = resolve_library(db, client_ref, create=False)
        if client is None:
            return {"ok": True, "assets": [], "withheld": 0,
                    "note": f"No Media Library is linked to {client_ref}."}
        rows = db.execute(select(SavedImage).where(
            SavedImage.client_id == client.id,
            SavedImage.resource_type.in_(rule["resources"]),
        ).order_by(SavedImage.created_at.desc())).scalars().all()
        ids = [row.id for row in rows]
        details = {row.asset_id: row for row in db.execute(
            select(MediaAssetDetail).where(MediaAssetDetail.asset_id.in_(ids))
        ).scalars().all()} if ids else {}
        observations = {row.image_id: row for row in db.execute(
            select(ImageDescription).where(ImageDescription.image_id.in_(ids))
        ).scalars().all()} if ids else {}
        terms = [term for term in str(query or "").lower().split() if term]
        eligible, withheld = [], 0
        for row in rows:
            detail = details.get(row.id)
            payload = asset_dict(row, detail, observation=observations.get(row.id))
            if payload["rights_status"] in {"Do Not Use", "Expired"}:
                withheld += 1
                continue
            expiration = detail.license_expiration if detail else None
            if expiration and expiration.replace(tzinfo=None) <= datetime.now(timezone.utc).replace(tzinfo=None):
                withheld += 1
                continue
            if payload.get("duplicate_of"):
                withheld += 1
                continue
            if not payload.get("storage_url"):
                withheld += 1
                continue
            approval = payload.get(rule["approval"])
            if approval is not True and not include_pending:
                withheld += 1
                continue
            haystack = " ".join(str(payload.get(key) or "") for key in (
                "original_filename", "original_alt", "ai_alt_text", "ai_description",
                "ai_category", "ai_tags", "subjects", "service_product",
                "brand_asset_type", "caption",
            )).lower()
            if terms and not all(term in haystack for term in terms):
                continue
            payload.update({
                "media_asset_id": row.id,
                "consumer": consumer_key,
                "approval_state": "approved" if approval is True else "pending",
                "use_endpoint": f"/api/media/{row.id}/use",
                "consumer_score": payload.get(rule["score"]),
            })
            eligible.append(payload)
        eligible.sort(key=lambda item: (
            item.get("consumer_score") is not None,
            item.get("consumer_score") or 0,
            item.get("created_at") or "",
        ), reverse=True)
        cap = max(1, min(int(limit or 60), 500))
        return {"ok": True, "client": client.to_dict(), "consumer": consumer_key,
                "assets": eligible[:cap], "matched": len(eligible),
                "withheld": withheld, "include_pending": bool(include_pending),
                "note": "" if eligible else "No approved, eligible assets are available."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "assets": [],
                "error": f"Media Library read failed ({type(exc).__name__})."}


def record_use(asset_id: int, consumer: str, entity_id: str, *,
               usage_type: str = "", campaign_id: str = "",
               creative_id: str = "", placement: str = "") -> dict:
    """Idempotently link and record one consumer's use of a canonical asset."""
    consumer_key = str(consumer or "").strip().lower()
    rule = CONSUMERS.get(consumer_key)
    entity_id = str(entity_id or "").strip()[:160]
    if rule is None:
        return {"ok": False, "error": "Unknown media consumer."}
    if not entity_id:
        return {"ok": False, "error": "entity_id is required."}
    db = session()
    asset = db.get(SavedImage, int(asset_id))
    if asset is None:
        return {"ok": False, "error": "Media asset not found."}
    detail = db.execute(select(MediaAssetDetail).where(
        MediaAssetDetail.asset_id == asset.id)).scalar_one_or_none()
    approval = getattr(detail, rule["approval"], None) if detail else None
    expired = bool(detail and detail.license_expiration and
                   detail.license_expiration.replace(tzinfo=None) <=
                   datetime.now(timezone.utc).replace(tzinfo=None))
    if (not detail or detail.rights_status in {"Do Not Use", "Expired"}
            or expired or detail.duplicate_of or approval is not True):
        return {"ok": False, "error": "This asset is not approved for that use."}

    usage_type = str(usage_type or "").strip()[:80]
    link = db.execute(select(MediaAssetLink).where(
        MediaAssetLink.asset_id == asset.id,
        MediaAssetLink.entity_type == rule["entity_type"],
        MediaAssetLink.entity_id == entity_id,
        MediaAssetLink.usage_type == (usage_type or None),
    )).scalar_one_or_none()
    link_created = link is None
    if link is None:
        link = MediaAssetLink(asset_id=asset.id, client_id=asset.client_id,
                              entity_type=rule["entity_type"], entity_id=entity_id,
                              usage_type=usage_type or None)
        db.add(link)
        db.flush()

    campaign = str(campaign_id or "").strip()[:160] or None
    creative = str(creative_id or entity_id).strip()[:160] or None
    where = str(placement or usage_type).strip()[:160] or None
    usage = db.execute(select(MediaUsage).where(
        MediaUsage.asset_id == asset.id, MediaUsage.tool == consumer_key,
        MediaUsage.campaign_id == campaign, MediaUsage.creative_id == creative,
        MediaUsage.placement == where,
    )).scalar_one_or_none()
    usage_created = usage is None
    if usage is None:
        usage = MediaUsage(asset_id=asset.id, client_id=asset.client_id,
                           tool=consumer_key, campaign_id=campaign,
                           creative_id=creative, placement=where,
                           used_at=datetime.now(timezone.utc))
        db.add(usage)
        db.flush()
    db.commit()
    return {"ok": True, "asset_id": asset.id, "link_id": link.id,
            "usage_id": usage.id, "link_created": link_created,
            "usage_created": usage_created}
