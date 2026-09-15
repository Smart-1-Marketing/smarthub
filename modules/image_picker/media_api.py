"""SmartHub-wide API for the canonical client Media Library."""
from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from .app import db_guard, hub_user, staff_only
from .models import (
    ImageDescription, MediaAssetDetail, MediaAssetLink, MediaCollection,
    MediaCollectionAsset, MediaSearchDocument, MediaUsage, SavedImage, session,
)
from .platform import (
    ASSET_TYPES, BRAND_ASSET_TYPES, ENTITY_TYPES, RIGHTS_STATUSES, asset_dict,
    backfill_registered_clients, collection_rows, detail_for, library_summary,
    resolve_library,
)

bp = Blueprint("media_api", __name__)


def _library(db, ref, *, create=True):
    client = resolve_library(db, ref, create=create)
    if client is None:
        return None, (jsonify({"ok": False, "error": "Client media library not found."}), 404)
    return client, None


def _assets(db, client_id: int):
    rows = db.execute(
        select(SavedImage).where(SavedImage.client_id == client_id)
        .order_by(SavedImage.created_at.desc())
    ).scalars().all()
    ids = [r.id for r in rows]
    details = {d.asset_id: d for d in db.execute(
        select(MediaAssetDetail).where(MediaAssetDetail.asset_id.in_(ids))
    ).scalars().all()} if ids else {}
    observations = {d.image_id: d for d in db.execute(
        select(ImageDescription).where(ImageDescription.image_id.in_(ids))
    ).scalars().all()} if ids else {}
    return rows, details, observations


def _relevance(payload: dict, indexed_text: str, query: str) -> int:
    """Stable metadata relevance until the embedding column is populated."""
    phrase = " ".join(query.lower().split())
    if not phrase:
        return 1
    terms = phrase.split()
    haystack = indexed_text or " ".join(str(payload.get(key) or "") for key in (
        "original_filename", "original_alt", "ai_alt_text", "ai_description",
        "ai_category", "ai_tags", "subjects", "service_product", "source",
        "orientation", "indoor_outdoor", "brand_asset_type", "rights_status",
    )).lower()
    if not all(term in haystack for term in terms):
        return 0
    score = sum(min(5, haystack.count(term)) for term in terms) * 10
    if phrase in haystack:
        score += 50
    if phrase in str(payload.get("ai_description") or "").lower():
        score += 25
    if phrase in str(payload.get("original_alt") or "").lower():
        score += 20
    return score


@bp.get("/api/clients/<client_ref>/media/search")
@bp.get("/api/clients/<client_ref>/media")
@staff_only
@db_guard
def list_media(client_ref):
    db = session()
    client, error = _library(db, client_ref)
    if error:
        return error
    rows, details, observations = _assets(db, client.id)
    payloads = [asset_dict(row, details.get(row.id), observation=observations.get(row.id))
                for row in rows]
    asset_ids = [row.id for row in rows]
    documents = {doc.asset_id: doc for doc in db.execute(
        select(MediaSearchDocument).where(MediaSearchDocument.asset_id.in_(asset_ids))
    ).scalars().all()} if asset_ids else {}

    asset_type = str(request.args.get("asset_type") or "").lower()
    rights = str(request.args.get("rights_status") or "")
    orientation = str(request.args.get("orientation") or "").lower()
    query = str(request.args.get("q") or "").strip()
    if asset_type:
        payloads = [p for p in payloads if p["asset_type"] == asset_type]
    if rights:
        payloads = [p for p in payloads if p["rights_status"] == rights]
    if orientation:
        payloads = [p for p in payloads if p["orientation"] == orientation]
    indoor_outdoor = str(request.args.get("indoor_outdoor") or "").lower()
    if indoor_outdoor:
        payloads = [p for p in payloads if p["indoor_outdoor"] == indoor_outdoor]
    people = request.args.get("people_count")
    if people is not None:
        try:
            people = int(people)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "people_count must be a number."}), 400
        payloads = [p for p in payloads if p["people_count"] == people]
    minimum_quality = request.args.get("min_quality")
    if minimum_quality is not None:
        try:
            minimum_quality = max(0, min(100, int(minimum_quality)))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "min_quality must be a number."}), 400
        payloads = [p for p in payloads if (p["quality_score"] or 0) >= minimum_quality]
    if request.args.get("approved_for_paid_media") in {"1", "true"}:
        payloads = [p for p in payloads if p["approved_for_paid_media"] is True]
    if query:
        ranked = [(_relevance(p, (documents.get(p["id"]).search_text
                                 if documents.get(p["id"]) else ""), query), p)
                  for p in payloads]
        payloads = [{**p, "search_score": score} for score, p in ranked if score]
        payloads.sort(key=lambda p: (p["search_score"], p.get("created_at") or ""),
                      reverse=True)
    if request.args.get("unused") in {"1", "true"}:
        used_ids = set(db.execute(select(MediaUsage.asset_id).where(
            MediaUsage.client_id == client.id)).scalars().all())
        payloads = [p for p in payloads if p["id"] not in used_ids]

    collection_id = request.args.get("collection_id", type=int)
    if collection_id:
        member_ids = set(db.execute(
            select(MediaCollectionAsset.asset_id).join(
                MediaCollection,
                MediaCollection.id == MediaCollectionAsset.collection_id)
            .where(MediaCollection.id == collection_id,
                   MediaCollection.client_id == client.id)
        ).scalars().all())
        payloads = [p for p in payloads if p["id"] in member_ids]

    try:
        limit = max(1, min(int(request.args.get("limit") or 100), 500))
        offset = max(0, int(request.args.get("offset") or 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "limit and offset must be numbers."}), 400
    from .intelligence import semantic_status
    index_status = semantic_status(db, asset_ids)
    semantic_requested = request.args.get("semantic") in {"1", "true"}
    return jsonify({
        "ok": True, "client": client.to_dict(),
        "summary": library_summary(db, client),
        "collections": collection_rows(db, client.id),
        "assets": payloads[offset:offset + limit],
        "matched": len(payloads), "limit": limit, "offset": offset,
        # Metadata ranking remains authoritative until a vector ranker is
        # connected. Expose readiness without claiming embeddings were used.
        "search": {"mode": "metadata", "semantic_requested": semantic_requested,
                   **index_status},
    })

@bp.get("/api/clients/<client_ref>/media/recommendations")
@staff_only
@db_guard
def media_recommendations(client_ref):
    db = session()
    client, error = _library(db, client_ref)
    if error:
        return error
    use = str(request.args.get("use") or "website").lower()
    score_field = {"website": "website_score", "social": "social_score",
                   "advertising": "advertising_score",
                   "hero": "hero_score"}.get(use, "website_score")
    rows, details, observations = _assets(db, client.id)
    ranked = []
    for row in rows:
        payload = asset_dict(row, details.get(row.id), observation=observations.get(row.id))
        if payload["rights_status"] in {"Do Not Use", "Expired"}:
            continue
        # A social import is provenance, not blanket marketing permission.
        # Recommendations fail closed until the requested use is explicitly
        # approved, which prevents a convenient suggestion becoming an
        # accidental rights assertion.
        approval_field = {"website": "approved_for_web",
                          "hero": "approved_for_web",
                          "social": "approved_for_social",
                          "advertising": "approved_for_paid_media"}.get(use)
        if approval_field and payload.get(approval_field) is not True:
            continue
        score = payload.get(score_field)
        if score is None:
            score = payload.get("quality_score") or 0
        ranked.append((score, payload))
    ranked.sort(key=lambda pair: (pair[0], pair[1].get("created_at") or ""), reverse=True)
    return jsonify({"ok": True, "client": client.to_dict(), "use": use,
                    "recommendations": [{**p, "recommendation_score": s}
                                        for s, p in ranked[:25]]})


@bp.get("/api/clients/<client_ref>/media/collections")
@staff_only
@db_guard
def list_collections(client_ref):
    db = session()
    client, error = _library(db, client_ref)
    if error:
        return error
    return jsonify({"ok": True, "client": client.to_dict(),
                    "collections": collection_rows(db, client.id)})


@bp.post("/api/clients/<client_ref>/media/collections")
@staff_only
@db_guard
def create_collection(client_ref):
    db = session()
    client, error = _library(db, client_ref)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()[:200]
    if not name:
        return jsonify({"ok": False, "error": "Collection name is required."}), 400
    existing = db.execute(select(MediaCollection).where(
        MediaCollection.client_id == client.id, MediaCollection.name == name
    )).scalar_one_or_none()
    if existing:
        return jsonify({"ok": True, "created": False, "collection": {
            "id": existing.id, "name": existing.name,
            "description": existing.description or "",
            "collection_type": existing.collection_type,
        }})
    ctype = str(body.get("collection_type") or "user").lower()
    if ctype not in {"user", "ai", "brand", "campaign", "system"}:
        ctype = "user"
    collection = MediaCollection(
        client_id=client.id, name=name,
        description=str(body.get("description") or "").strip() or None,
        collection_type=ctype, created_by=hub_user(),
    )
    db.add(collection)
    db.commit()
    return jsonify({"ok": True, "created": True, "collection": {
        "id": collection.id, "name": collection.name,
        "description": collection.description or "",
        "collection_type": collection.collection_type,
    }}), 201


@bp.get("/api/clients/<client_ref>/media/brand")
@staff_only
@db_guard
def brand_media(client_ref):
    db = session()
    client, error = _library(db, client_ref)
    if error:
        return error
    rows, details, observations = _assets(db, client.id)
    assets = [asset_dict(row, details.get(row.id), observation=observations.get(row.id)) for row in rows
              if (details.get(row.id) and details[row.id].brand_asset_type)
              or "logo" in (row.provider or "").lower()]
    brand = {}
    try:
        from hub import client_brand, clients_registry
        registry = clients_registry.find_client(client.name) or {}
        brand = client_brand.brand_kit(
            client.name, registry.get("domain") or registry.get("url") or "")
    except Exception:
        brand = {"found": False, "note": "Brand profile could not be read."}
    return jsonify({"ok": True, "client": client.to_dict(),
                    "brand": brand, "assets": assets})


@bp.get("/api/media/<int:asset_id>")
@staff_only
@db_guard
def get_asset(asset_id):
    db = session()
    asset = db.get(SavedImage, asset_id)
    if not asset:
        return jsonify({"ok": False, "error": "Media asset not found."}), 404
    detail = detail_for(db, asset)
    observation = db.execute(select(ImageDescription).where(
        ImageDescription.image_id == asset.id)).scalar_one_or_none()
    collections = [{"id": c.id, "name": c.name} for c in db.execute(
        select(MediaCollection).join(
            MediaCollectionAsset,
            MediaCollectionAsset.collection_id == MediaCollection.id)
        .where(MediaCollectionAsset.asset_id == asset.id)
    ).scalars().all()]
    links = [{"id": row.id, "entity_type": row.entity_type,
              "entity_id": row.entity_id, "usage_type": row.usage_type or "",
              "created_at": row.created_at.isoformat()}
             for row in db.execute(select(MediaAssetLink).where(
                 MediaAssetLink.asset_id == asset.id)).scalars().all()]
    usage = [{"id": row.id, "tool": row.tool, "campaign_id": row.campaign_id or "",
              "creative_id": row.creative_id or "", "placement": row.placement or "",
              "used_at": row.used_at.isoformat()}
             for row in db.execute(select(MediaUsage).where(
                 MediaUsage.asset_id == asset.id)
                 .order_by(MediaUsage.used_at.desc()).limit(100)).scalars().all()]
    return jsonify({"ok": True, "asset": asset_dict(
        asset, detail, collections=collections, links=links, usage=usage,
        observation=observation)})


@bp.patch("/api/media/<int:asset_id>")
@staff_only
@db_guard
def update_asset(asset_id):
    db = session()
    asset = db.get(SavedImage, asset_id)
    if not asset:
        return jsonify({"ok": False, "error": "Media asset not found."}), 404
    body = request.get_json(silent=True) or {}
    detail = detail_for(db, asset, create=True)

    if "asset_type" in body:
        value = str(body["asset_type"]).lower()
        if value not in ASSET_TYPES:
            return jsonify({"ok": False, "error": "Invalid asset_type."}), 400
        detail.asset_type = value
    if "rights_status" in body:
        value = str(body["rights_status"])
        if value not in RIGHTS_STATUSES:
            return jsonify({"ok": False, "error": "Invalid rights_status."}), 400
        detail.rights_status = value
    if "brand_asset_type" in body:
        value = str(body["brand_asset_type"] or "")
        if value and value not in BRAND_ASSET_TYPES:
            return jsonify({"ok": False, "error": "Invalid brand_asset_type."}), 400
        detail.brand_asset_type = value or None
    for field in ("rights_notes", "caption", "source_account", "source_post_url",
                  "mime_type"):
        if field in body:
            setattr(detail, field, str(body[field] or "").strip() or None)
    for field in ("approved_for_web", "approved_for_social", "approved_for_paid_media"):
        if field in body:
            if body[field] is not None and not isinstance(body[field], bool):
                return jsonify({"ok": False, "error": f"{field} must be true, false, or null."}), 400
            setattr(detail, field, body[field])
    for field in ("quality_score", "website_score", "social_score",
                  "advertising_score", "hero_score"):
        if field in body:
            value = body[field]
            if value is not None and (isinstance(value, bool) or
                                      not isinstance(value, int) or not 0 <= value <= 100):
                return jsonify({"ok": False, "error": f"{field} must be 0–100."}), 400
            setattr(detail, field, value)
    if "license_expiration" in body:
        raw = str(body["license_expiration"] or "").strip()
        try:
            detail.license_expiration = datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None
        except ValueError:
            return jsonify({"ok": False, "error": "license_expiration must be ISO-8601."}), 400
    detail.updated_at = datetime.now(timezone.utc)
    observation = db.execute(select(ImageDescription).where(
        ImageDescription.image_id == asset.id)).scalar_one_or_none()
    from .intelligence import index_asset
    index_asset(db, asset, detail, observation)
    db.commit()
    return jsonify({"ok": True, "asset": asset_dict(asset, detail)})


@bp.post("/api/media/<int:asset_id>/collections")
@staff_only
@db_guard
def add_asset_to_collection(asset_id):
    db = session()
    asset = db.get(SavedImage, asset_id)
    body = request.get_json(silent=True) or {}
    collection = db.get(MediaCollection, body.get("collection_id"))
    if not asset or not collection or collection.client_id != asset.client_id:
        return jsonify({"ok": False, "error": "Asset and collection must belong to the same client."}), 400
    existing = db.execute(select(MediaCollectionAsset).where(
        MediaCollectionAsset.collection_id == collection.id,
        MediaCollectionAsset.asset_id == asset.id,
    )).scalar_one_or_none()
    if not existing:
        db.add(MediaCollectionAsset(collection_id=collection.id, asset_id=asset.id,
                                    added_by=hub_user()))
        db.commit()
    return jsonify({"ok": True, "created": existing is None})


@bp.post("/api/media/<int:asset_id>/links")
@staff_only
@db_guard
def link_asset(asset_id):
    db = session()
    asset = db.get(SavedImage, asset_id)
    if not asset:
        return jsonify({"ok": False, "error": "Media asset not found."}), 404
    body = request.get_json(silent=True) or {}
    etype = str(body.get("entity_type") or "").lower()
    entity_id = str(body.get("entity_id") or "").strip()[:160]
    usage_type = str(body.get("usage_type") or "").strip()[:80]
    if etype not in ENTITY_TYPES or not entity_id:
        return jsonify({"ok": False, "error": "Valid entity_type and entity_id are required."}), 400
    existing = db.execute(select(MediaAssetLink).where(
        MediaAssetLink.asset_id == asset.id, MediaAssetLink.entity_type == etype,
        MediaAssetLink.entity_id == entity_id, MediaAssetLink.usage_type == (usage_type or None),
    )).scalar_one_or_none()
    created = existing is None
    if created:
        existing = MediaAssetLink(asset_id=asset.id, client_id=asset.client_id,
                                  entity_type=etype, entity_id=entity_id,
                                  usage_type=usage_type or None)
        db.add(existing)
        db.commit()
    return jsonify({"ok": True, "created": created,
                    "link_id": existing.id})


@bp.post("/api/media/<int:asset_id>/usage")
@staff_only
@db_guard
def record_asset_usage(asset_id):
    db = session()
    asset = db.get(SavedImage, asset_id)
    if not asset:
        return jsonify({"ok": False, "error": "Media asset not found."}), 404
    body = request.get_json(silent=True) or {}
    tool = str(body.get("tool") or "").strip()[:80]
    if not tool:
        return jsonify({"ok": False, "error": "tool is required."}), 400
    usage = MediaUsage(
        asset_id=asset.id, client_id=asset.client_id, tool=tool,
        campaign_id=str(body.get("campaign_id") or "").strip()[:160] or None,
        creative_id=str(body.get("creative_id") or "").strip()[:160] or None,
        placement=str(body.get("placement") or "").strip()[:160] or None,
        used_at=datetime.now(timezone.utc),
    )
    db.add(usage)
    db.commit()
    return jsonify({"ok": True, "usage_id": usage.id}), 201


@bp.post("/api/media/admin/backfill")
@staff_only
@db_guard
def backfill_media_libraries():
    return jsonify({"ok": True, **backfill_registered_clients()})
