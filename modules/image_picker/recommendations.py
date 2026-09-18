"""Explainable Phase 5 recommendations and Media Health for one client.

These are inventory and suitability rules, not measured campaign performance.
No AI calls are made on reads, and an unapproved asset is never recommended.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .models import MediaAssetDetail, MediaUsage, SavedImage
from .platform import asset_dict, inferred_asset_type


def _assets(db, client_id: int) -> tuple[list[dict], Counter]:
    rows = db.execute(select(SavedImage).where(
        SavedImage.client_id == client_id)).scalars().all()
    ids = [row.id for row in rows]
    details = {row.asset_id: row for row in db.execute(select(
        MediaAssetDetail).where(MediaAssetDetail.asset_id.in_(ids)
    )).scalars().all()} if ids else {}
    uses = Counter(dict(db.execute(select(
        MediaUsage.asset_id, func.count(MediaUsage.id)).where(
            MediaUsage.client_id == client_id,
            MediaUsage.asset_id.in_(ids),
        ).group_by(MediaUsage.asset_id)).all())) if ids else Counter()
    items = []
    for row in rows:
        detail = details.get(row.id)
        item = asset_dict(row, detail)
        item["usage_count"] = uses[row.id]
        item["asset_type"] = detail.asset_type if detail else inferred_asset_type(row)
        items.append(item)
    return items, uses


def _eligible(item: dict, approval: str, *, resource: str = "image") -> bool:
    expiration = item.get("license_expiration")
    if expiration:
        try:
            if datetime.fromisoformat(expiration.replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                return False
        except ValueError:
            return False
    return (item.get("resource_type") == resource
            and bool(item.get("storage_url"))
            and not item.get("duplicate_of")
            and item.get("rights_status") not in {"Do Not Use", "Expired"}
            and item.get(approval) is True)


def _text(item: dict) -> str:
    return " ".join(str(item.get(field) or "") for field in (
        "original_filename", "original_alt", "ai_alt_text", "ai_description",
        "caption", "ai_category", "ai_tags", "subjects", "service_product",
        "brand_asset_type", "collection_label",
    )).lower()


def _wide(item: dict) -> bool:
    return bool(item.get("width") and item.get("height") and
                item["width"] >= 1600 and item["height"] >= 900 and
                item["width"] / item["height"] >= 1.35)


def _sized(item: dict) -> bool:
    return bool(item.get("width") and item.get("height") and
                max(item["width"], item["height"]) >= 1200)


def _team(item: dict) -> bool:
    return any(word in _text(item) for word in (
        "team", "staff", "employee", "owner", "leadership", "technician",
    ))


def _service(item: dict) -> bool:
    return bool(item.get("service_product") or any(word in _text(item) for word in (
        "service", "installation", "repair", "product", "worksite",
    )))


def media_health(db, client_id: int) -> dict:
    """Five equally weighted coverage checks, with visible evidence and gaps."""
    items, _ = _assets(db, client_id)
    web = [item for item in items if _eligible(item, "approved_for_web")]
    social = [item for item in items if _eligible(item, "approved_for_social")]
    video = [item for item in items if _eligible(
        item, "approved_for_paid_media", resource="video")]
    logos = [item for item in web if item.get("asset_type") == "logo" or
             item.get("brand_asset_type") in {"Primary Logo", "Secondary Logo",
                                               "White Logo", "Dark Logo"}]
    heroes = [item for item in web if _wide(item)]
    teams = [item for item in web if _team(item) and _sized(item)]
    services = [item for item in web if _service(item) and _sized(item)]
    vertical = [item for item in social if item.get("width") and item.get("height")
                and item["height"] > item["width"]]
    checks = [
        ("brand", "Approved logo", bool(logos), len(logos)),
        ("hero", "Desktop-ready website hero", bool(heroes), len(heroes)),
        ("team", "High-resolution team or people photography",
         bool(teams), len(teams)),
        ("service", "High-resolution service or product photography",
         bool(services), len(services)),
        ("video", "Approved video for campaigns", bool(video), len(video)),
    ]
    # A known-old team photo is inventory, but not current coverage. An asset
    # without a capture date is not called recent; it is usable until reviewed.
    cutoff = datetime.now(timezone.utc) - timedelta(days=730)
    current_team = [item for item in teams if not item.get("captured_at") or
                    datetime.fromisoformat(item["captured_at"].replace("Z", "+00:00")) >= cutoff]
    checks[2] = ("team", checks[2][1], bool(current_team), len(current_team))
    categories = [{"key": key, "label": label, "points": 20 if present else 0,
                   "maximum": 20, "matching_assets": count}
                  for key, label, present, count in checks]
    gaps = []
    gap_rules = [
        ("logo", not logos, "Upload an approved primary logo.", "brand"),
        ("hero", not heroes, "Request a landscape exterior or service image at least 1600 × 900 for a desktop hero.", "hero"),
        ("team", not current_team, "Request a current, high-resolution team, owner, or staff photo.", "team"),
        ("service", not services, "Request high-resolution photography showing a service or product.", "service"),
        ("vertical_social", not vertical, "Request a vertical photo approved for social posts or stories.", "social"),
        ("video", not video, "Request a client video clip and confirm paid-media approval.", "video"),
    ]
    for key, missing, action, category in gap_rules:
        if missing:
            gaps.append({"key": key, "category": category, "action": action,
                         "evidence": "No eligible asset matches this inventory check."})
    return {"score": sum(row["points"] for row in categories), "maximum": 100,
            "method": "Five equal 20-point inventory coverage checks; no performance data.",
            "categories": categories, "opportunities": gaps,
            "asset_count": len(items), "eligible_web": len(web),
            "eligible_social": len(social), "eligible_video": len(video)}


def recommend(db, client_id: int, *, use: str = "website", query: str = "",
              limit: int = 25) -> dict:
    """Rank eligible assets by suitability, campaign terms, and reuse count."""
    rules = {
        "website": ("approved_for_web", "website_score", "image"),
        "hero": ("approved_for_web", "hero_score", "image"),
        "social": ("approved_for_social", "social_score", "image"),
        "advertising": ("approved_for_paid_media", "advertising_score", "image"),
        "campaign": ("approved_for_paid_media", "advertising_score", "image"),
    }
    if use not in rules:
        return {"ok": False, "error": "Unknown recommendation use."}
    approval, score_field, resource = rules[use]
    items, _ = _assets(db, client_id)
    terms = [term for term in query.lower().split() if term]
    ranked = []
    for item in items:
        if not _eligible(item, approval, resource=resource):
            continue
        if use == "hero" and not _wide(item):
            continue
        searchable = _text(item)
        matches = sum(term in searchable for term in terms)
        if terms and not matches:
            continue
        suitability = item.get(score_field)
        if suitability is None:
            suitability = item.get("quality_score") or 0
        relevance = round(20 * matches / len(terms)) if terms and use == "campaign" else 0
        reuse_penalty = min(item["usage_count"], 10) * 2 if use == "campaign" else 0
        score = max(0, min(100, int(suitability) + relevance - reuse_penalty))
        reasons = [f"{score_field.replace('_', ' ')} {suitability}/100"]
        if matches:
            reasons.append(f"matches {matches} of {len(terms)} campaign terms")
        if item["usage_count"]:
            reasons.append(f"used {item['usage_count']} time(s)")
        else:
            reasons.append("not previously used")
        ranked.append({**item, "recommendation_score": score,
                       "reasons": reasons})
    ranked.sort(key=lambda item: (item["recommendation_score"],
                                  item.get("created_at") or ""), reverse=True)
    return {"ok": True, "use": use, "query": query,
            "method": ("Suitability plus metadata relevance minus reuse; not performance-based."
                       if use == "campaign" else "Stored suitability score; not performance-based."),
            "recommendations": ranked[:max(1, min(limit, 100))]}


def usage_history(db, client_id: int, *, limit: int = 100, offset: int = 0) -> dict:
    """Newest recorded uses for one client, including asset identity."""
    total = db.scalar(select(func.count(MediaUsage.id)).where(
        MediaUsage.client_id == client_id)) or 0
    rows = db.execute(select(MediaUsage, SavedImage).join(
        SavedImage, SavedImage.id == MediaUsage.asset_id).where(
        MediaUsage.client_id == client_id,
        SavedImage.client_id == client_id,
    ).order_by(MediaUsage.used_at.desc(), MediaUsage.id.desc())
        .offset(max(0, offset)).limit(max(1, min(limit, 500)))).all()
    return {"ok": True, "total": total, "offset": offset,
            "usage": [{"id": use.id, "asset_id": asset.id,
                       "filename": asset.filename or "", "thumbnail_url": asset.to_dict()["thumb"],
                       "tool": use.tool, "campaign_id": use.campaign_id or "",
                       "creative_id": use.creative_id or "",
                       "placement": use.placement or "",
                       "used_at": use.used_at.isoformat()}
                      for use, asset in rows]}
