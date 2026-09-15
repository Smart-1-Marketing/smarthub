"""Core client-media platform services.

This module deliberately wraps the existing Image Picker records.  Every
SmartHub producer already files canonical Cloudinary assets there, so making a
second library would split identity, provenance, and storage on day one.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select

from . import provisioning, taxonomy
from .models import (
    MediaAssetDetail, MediaAssetLink, MediaCollection, MediaCollectionAsset,
    MediaUsage, PickerClient, SavedImage, new_token, session, unique_slug,
)

ASSET_TYPES = {"photo", "video", "logo", "graphic", "social", "document", "other"}
RIGHTS_STATUSES = {
    "Unknown", "Client Owned", "Client Approved", "Licensed", "Stock",
    "AI Generated", "Do Not Use", "Expired",
}
BRAND_ASSET_TYPES = {
    "Primary Logo", "Secondary Logo", "White Logo", "Dark Logo", "Icon",
    "Favicon", "Brand Pattern", "Approved Background", "Brand Guidelines",
}
ENTITY_TYPES = {
    "website", "website_page", "campaign", "creative", "social_post",
    "email", "proposal", "landing_page", "video", "client_project",
}


def _orientation(row: SavedImage) -> str:
    if not row.width or not row.height:
        return ""
    ratio = row.width / row.height
    if ratio > 1.08:
        return "landscape"
    if ratio < .92:
        return "portrait"
    return "square"


def inferred_asset_type(row: SavedImage) -> str:
    provider = (row.provider or "").lower()
    kind = (row.collection_kind or "").lower()
    if row.resource_type == "video":
        return "video"
    if "logo" in provider or kind == "logo":
        return "logo"
    if provider in {"social_request", "instagram", "facebook"}:
        return "social"
    if provider in {"display_ad", "display_ads", "ad_builder", "graphic",
                    "image_creator", "gpt_ads", "magic_resize"}:
        return "graphic"
    if row.resource_type == "raw":
        return "document"
    return "photo"


def detail_for(db, asset: SavedImage, *, create: bool = False) -> MediaAssetDetail | None:
    detail = db.execute(
        select(MediaAssetDetail).where(MediaAssetDetail.asset_id == asset.id)
    ).scalar_one_or_none()
    if detail is None and create:
        detail = MediaAssetDetail(
            asset_id=asset.id,
            asset_type=inferred_asset_type(asset),
            original_filename=asset.filename,
            original_alt=asset.alt_text,
            orientation=_orientation(asset) or None,
            rights_status="Unknown",
            imported_at=asset.created_at or datetime.now(timezone.utc),
        )
        db.add(detail)
        db.flush()
    return detail


def asset_dict(asset: SavedImage, detail: MediaAssetDetail | None = None,
               *, collections: list[dict] | None = None,
               links: list[dict] | None = None,
               usage: list[dict] | None = None,
               observation=None) -> dict:
    base = asset.to_dict()
    dtype = detail.asset_type if detail else inferred_asset_type(asset)
    base.update({
        "asset_type": dtype,
        "source": asset.provider,
        "source_id": asset.provider_image_id,
        "source_account": detail.source_account if detail else "",
        "source_post_url": detail.source_post_url if detail else "",
        "storage_url": asset.cloudinary_url,
        "thumbnail_url": base.get("thumb"),
        "original_filename": ((detail.original_filename if detail else "")
                              or asset.filename or ""),
        "mime_type": detail.mime_type if detail else "",
        "file_size": asset.bytes,
        "duration": detail.duration if detail else None,
        "caption": detail.caption if detail else "",
        "description": ((detail.ai_description if detail else "")
                        or (observation.description if observation else "") or ""),
        "original_alt": ((detail.original_alt if detail else "") or asset.alt_text or ""),
        "ai_alt_text": ((detail.ai_alt_text if detail else "")
                        or (observation.alt_suggestion if observation else "") or ""),
        "ai_description": ((detail.ai_description if detail else "")
                           or (observation.description if observation else "") or ""),
        "ai_category": detail.ai_category if detail else "",
        "ai_tags": [t for t in (((detail.ai_tags if detail else "")
                                  or (observation.tags if observation else "") or "")).split(",") if t],
        "analysis_version": ((detail.analysis_version if detail else "")
                             or (observation.model if observation else "") or ""),
        "subjects": [t for t in ((detail.subjects if detail else "") or "").split(",") if t],
        "service_product": detail.service_product if detail else "",
        "seo_filename_suggestion": detail.seo_filename_suggestion if detail else "",
        "orientation": ((detail.orientation if detail else "") or _orientation(asset)),
        "people_count": detail.people_count if detail else None,
        "indoor_outdoor": detail.indoor_outdoor if detail else "",
        "quality_score": detail.quality_score if detail else None,
        "website_score": detail.website_score if detail else None,
        "social_score": detail.social_score if detail else None,
        "advertising_score": detail.advertising_score if detail else None,
        "hero_score": detail.hero_score if detail else None,
        "composition_open_space": detail.composition_open_space if detail else "",
        "brand_asset_type": detail.brand_asset_type if detail else "",
        "duplicate_hash": detail.duplicate_hash if detail else "",
        "perceptual_hash": detail.perceptual_hash if detail else "",
        "duplicate_of": detail.duplicate_of if detail else None,
        "rights_status": detail.rights_status if detail else "Unknown",
        "rights_notes": detail.rights_notes if detail else "",
        "license_expiration": _iso(detail.license_expiration) if detail else None,
        "approved_for_web": detail.approved_for_web if detail else None,
        "approved_for_social": detail.approved_for_social if detail else None,
        "approved_for_paid_media": detail.approved_for_paid_media if detail else None,
        "captured_at": _iso(detail.captured_at) if detail else None,
        "imported_at": _iso(detail.imported_at) if detail else base.get("created_at"),
        "updated_at": _iso(detail.updated_at) if detail else base.get("created_at"),
        "collections": collections or [],
        "links": links or [],
        "usage": usage or [],
    })
    return base


def _iso(value) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _registry_match(ref: str) -> dict | None:
    try:
        from hub import clients_registry
        from hub.client_key import normalise_name
        wanted = normalise_name(ref)
        for row in clients_registry.all_clients():
            if ref in {str(row.get("key") or ""), str(row.get("slug") or ""),
                       str(row.get("name") or "")}:
                return row
            if wanted and normalise_name(row.get("name") or "") == wanted:
                return row
    except Exception:  # registry outage must not hide an existing library
        return None
    return None


def resolve_library(db, client_ref: str, *, create: bool = True) -> PickerClient | None:
    """Resolve a library by gallery id, Hub key/id, slug, or exact client name."""
    ref = str(client_ref or "").strip()
    if not ref:
        return None
    if ref.isdigit():
        found = db.get(PickerClient, int(ref))
        if found:
            return found
    found = db.execute(
        select(PickerClient).where(
            (PickerClient.hub_client_id == ref) | (PickerClient.slug == ref)
        )
    ).scalars().first()
    if found:
        return found

    registry = _registry_match(ref)
    name = str((registry or {}).get("name") or ref).strip()
    url = str((registry or {}).get("url") or (registry or {}).get("domain") or "")
    matches, _ = provisioning.find(db, name, url)
    if len(matches) == 1:
        found = matches[0]
        key = str((registry or {}).get("key") or "")
        if key and not found.hub_client_id:
            found.hub_client_id = key[:64]
            found.kind = "client"
            db.commit()
        return found
    if len(matches) > 1 or not create or registry is None:
        return None

    key = str(registry.get("key") or "")
    found = PickerClient(
        name=name[:200], slug=unique_slug(db, name),
        industry_key=taxonomy.guess_industry(name), kind="client",
        hub_client_id=key[:64] or None, share_token=new_token(),
    )
    db.add(found)
    db.commit()
    return found


def backfill_registered_clients(*, limit: int = 5000) -> dict:
    """Idempotently attach/provision a library for every registry client."""
    from hub import clients_registry
    from hub.client_key import normalise_name

    rows = list(clients_registry.all_clients())[:max(1, min(limit, 20000))]
    db = session()
    galleries = db.execute(select(PickerClient)).scalars().all()
    by_hub = {g.hub_client_id: g for g in galleries if g.hub_client_id}
    by_name: dict[str, list[PickerClient]] = {}
    for gallery in galleries:
        by_name.setdefault(normalise_name(gallery.name), []).append(gallery)

    created = attached = existing = ambiguous = 0
    for row in rows:
        name = str(row.get("name") or "").strip()
        key = str(row.get("key") or "").strip()
        if not name:
            continue
        gallery = by_hub.get(key) if key else None
        if gallery:
            existing += 1
            continue
        candidates = by_name.get(normalise_name(name), [])
        if len(candidates) > 1:
            ambiguous += 1
            continue
        if candidates:
            gallery = candidates[0]
            if key and not gallery.hub_client_id:
                gallery.hub_client_id = key[:64]
                gallery.kind = "client"
                by_hub[key] = gallery
                attached += 1
            else:
                existing += 1
            continue
        gallery = PickerClient(
            name=name[:200], slug=unique_slug(db, name),
            industry_key=taxonomy.guess_industry(name), kind="client",
            hub_client_id=key[:64] or None, share_token=new_token(),
        )
        db.add(gallery)
        db.flush()
        galleries.append(gallery)
        by_name.setdefault(normalise_name(name), []).append(gallery)
        if key:
            by_hub[key] = gallery
        created += 1
    db.commit()
    return {"clients": len(rows), "created": created, "attached": attached,
            "existing": existing, "ambiguous": ambiguous}


def library_summary(db, client: PickerClient) -> dict:
    assets = db.execute(
        select(SavedImage).where(SavedImage.client_id == client.id)
    ).scalars().all()
    details = {d.asset_id: d for d in db.execute(
        select(MediaAssetDetail).where(MediaAssetDetail.asset_id.in_([a.id for a in assets]))
    ).scalars().all()} if assets else {}
    types = Counter((details.get(a.id).asset_type if details.get(a.id)
                     else inferred_asset_type(a)) for a in assets)
    low_res = sum(1 for a in assets if a.resource_type == "image" and
                  (not a.width or not a.height or max(a.width, a.height) < 1200))
    duplicates = sum(1 for d in details.values() if d.duplicate_of)
    missing_alt = sum(1 for a in assets if a.resource_type == "image" and not a.alt_text)
    rights_unknown = sum(1 for a in assets
                         if not details.get(a.id) or details[a.id].rights_status == "Unknown")
    return {
        "total": len(assets),
        "types": {k: types.get(k, 0) for k in
                  ("photo", "video", "logo", "graphic", "social", "document", "other")},
        "warnings": {"low_resolution": low_res, "potential_duplicates": duplicates,
                     "missing_alt": missing_alt, "rights_unknown": rights_unknown,
                     "missing_requests": 0},
    }


def collection_rows(db, client_id: int) -> list[dict]:
    rows = db.execute(
        select(MediaCollection, func.count(MediaCollectionAsset.id))
        .outerjoin(MediaCollectionAsset,
                   MediaCollectionAsset.collection_id == MediaCollection.id)
        .where(MediaCollection.client_id == client_id)
        .group_by(MediaCollection.id)
        .order_by(MediaCollection.name)
    ).all()
    return [{"id": c.id, "name": c.name, "description": c.description or "",
             "collection_type": c.collection_type, "count": count,
             "created_at": _iso(c.created_at)} for c, count in rows]
