"""Planning and theme matching for the Smart 1 Sites Builder.

The browser receives structured data because it needs to draw the preview, but
customers never see the structure itself.  This module keeps the planning
rules testable and gives the tool a useful result when AI or Simvoly is down.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

import requests


GOAL_CTA = {
    "calls": "Call Today",
    "appointments": "Book an Appointment",
    "quotes": "Get My Free Quote",
    "orders": "Order Online",
    "reservations": "Reserve a Table",
    "visits": "Get Directions",
    "leads": "Get Started",
}


def recipe_for(business_type: str = "", description: str = "") -> dict:
    text = f"{business_type} {description}".lower()
    if re.search(r"restaurant|pizza|wing|cafe|bar|food|bakery", text):
        return {"industry": "Restaurant", "pages": ["Home", "Menu", "Order Online", "About", "Reviews", "Contact"],
                "sections": ["Customer Favorites", "Featured Offers", "Why Guests Choose Us", "Our Story", "Visit Us"]}
    if re.search(r"hvac|heating|cooling|plumb|roof|electric|contractor|home service", text):
        return {"industry": "Home Services", "pages": ["Home", "Services", "Service Area", "Reviews", "About", "Contact"],
                "sections": ["Services", "Why Homeowners Choose Us", "Reviews", "Service Area", "Helpful Answers"]}
    if re.search(r"law|lawyer|attorney|legal", text):
        return {"industry": "Legal", "pages": ["Home", "Practice Areas", "Attorneys", "Results", "Reviews", "Contact"],
                "sections": ["How We Can Help", "Experience You Can Trust", "Results", "Meet the Team", "Helpful Answers"]}
    if re.search(r"dent|doctor|clinic|medical|wellness|chiro|therapy", text):
        return {"industry": "Health & Wellness", "pages": ["Home", "Services", "Meet the Team", "Patient Info", "Reviews", "Contact"],
                "sections": ["Care Built Around You", "Services", "Meet the Team", "Patient Stories", "Your First Visit"]}
    if re.search(r"real estate|realtor|property|broker", text):
        return {"industry": "Real Estate", "pages": ["Home", "Properties", "Buy", "Sell", "About", "Contact"],
                "sections": ["Featured Properties", "Find Your Next Home", "Local Expertise", "Client Stories", "Meet Your Agent"]}
    return {"industry": "Local Business", "pages": ["Home", "Services", "About", "Reviews", "FAQ", "Contact"],
            "sections": ["What We Do", "Why Choose Us", "Customer Stories", "Our Story", "Helpful Answers"]}


def normalize_template(raw: dict) -> dict:
    def first(*names, default=""):
        for name in names:
            if raw.get(name) not in (None, ""):
                return raw.get(name)
        return default
    return {
        "id": first("id", "template_id"),
        "name": str(first("name", default="Smart 1 Design")),
        "categories": " ".join(str(first(name)) for name in
                               ("primaryCategories", "primary_categories", "categories")),
        "visible": bool(first("visible", default=True)),
        "custom": not bool(first("systemTemplate", "system_template", default=False)),
        "preview_url": str(first("previewUrl", "preview_url")),
        "thumbnail": str(first("thumb", "thumbnail", "image")),
    }


def _live_templates() -> list[dict]:
    """Read the catalog through the already-mounted Sites app when possible."""
    sites_app = sys.modules.get("sites_app")
    client = getattr(sites_app, "client", None) if sites_app else None
    if client:
        return list(client.list_templates() or [])

    # create_hub_app() is imported before wsgi mounts Sites Admin in tests and
    # some one-off commands, so retain a direct read rather than coupling the
    # builder to module load order.
    from hub.config import settings
    key = (getattr(settings, "simvoly_key", "") or "").strip()
    if not key:
        return []
    base = (os.environ.get("SIMVOLY_API_BASE_URL") or
            "https://api.smart1sites.com").rstrip("/")
    response = requests.get(
        f"{base}/api/v1/templates",
        headers={"Accept": "application/json", "X-CLIENT-KEY": key,
                 "User-Agent": "Smart1Hub-SitesBuilder/1.0"}, timeout=25)
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else payload
    return data if isinstance(data, list) else []


def templates() -> tuple[list[dict], str]:
    try:
        rows = [normalize_template(item) for item in _live_templates()
                if isinstance(item, dict)]
        rows = [item for item in rows if item["visible"]]
        return rows, ""
    except Exception as exc:  # catalog trouble must not cost the whole preview
        return [], type(exc).__name__


def _words(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def rank_templates(rows: list[dict], intake: dict) -> list[dict]:
    query = _words(" ".join(str(intake.get(key) or "") for key in
                            ("business_type", "description", "goal")))
    ranked = []
    for item in rows:
        words = _words(f"{item.get('name', '')} {item.get('categories', '')}")
        score = len(query & words) * 12
        if item.get("custom"):
            score += 5
        if item.get("thumbnail"):
            score += 3
        if item.get("preview_url"):
            score += 2
        ranked.append({**item, "_score": score})
    return sorted(ranked, key=lambda item: (-item["_score"], item["name"].lower()))


def fallback_plan(intake: dict) -> dict:
    name = str(intake.get("business_name") or "Your Business").strip()[:100]
    city = str(intake.get("city") or "").strip()[:100]
    description = str(intake.get("description") or "").strip()[:400]
    recipe = recipe_for(intake.get("business_type", ""), description)
    service = str(intake.get("business_type") or recipe["industry"]).strip()
    location = f" in {city}" if city else ""
    return {
        "business_name": name,
        "industry": recipe["industry"],
        "headline": f"A Better {service.title()} Experience{location}",
        "subheadline": description or f"Friendly, dependable {service.lower()} from a local team that puts customers first.",
        "cta": GOAL_CTA.get(str(intake.get("goal") or "leads"), "Get Started"),
        "pages": recipe["pages"],
        "sections": recipe["sections"],
        "proof": ["Built for mobile", "Clear next steps", "Easy to explore"],
    }


def make_plan(intake: dict) -> dict:
    fallback = fallback_plan(intake)
    try:
        from hub import ai
        answer = ai.chat_json([
            {"role": "system", "content": (
                "You write the customer-facing copy for a Smart 1 Sites website preview. "
                "Return JSON only. Never invent awards, years in business, ratings, prices, "
                "guarantees, or customer claims. Make the copy specific, warm, plain-English, "
                "and conversion-focused. Required keys: headline, subheadline, cta, proof. "
                "proof is exactly three short phrases. Keep every other value from the supplied plan.")},
            {"role": "user", "content": str({"intake": intake, "plan": fallback})},
        ], module="sites_builder", purpose="website_preview", max_tokens=500,
           temperature=0.45)
        if isinstance(answer, dict):
            for key in ("headline", "subheadline", "cta"):
                value = answer.get(key)
                if isinstance(value, str) and value.strip():
                    fallback[key] = value.strip()[:240]
            proof = answer.get("proof")
            if isinstance(proof, list) and len(proof) >= 3:
                fallback["proof"] = [str(value).strip()[:60] for value in proof[:3]]
    except Exception:  # the rules path is a complete product, not an error page
        pass
    return fallback


def build_preview(intake: dict) -> dict:
    plan = make_plan(intake)
    catalog, catalog_error = templates()
    ranked = rank_templates(catalog, intake)[:6]
    recommended = ranked[0]["id"] if ranked else None
    for item in ranked:
        item.pop("_score", None)
    media = []
    client = str(intake.get("business_name") or "").strip()
    if client:
        try:
            from modules.image_picker.integrations import assets_for
            media = assets_for(client, "sites", limit=12).get("assets", [])
        except Exception:                                # noqa: BLE001
            media = []
    return {"plan": plan, "themes": ranked, "media": media,
            "recommended_theme_id": recommended,
            "catalog_available": bool(catalog),
            "catalog_error": catalog_error}
