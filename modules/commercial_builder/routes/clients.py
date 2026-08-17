"""Client Brand Profile endpoints (spec section 2)."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client
from ..services import openai_service, cloudinary_service

bp = Blueprint("cb_clients", __name__, url_prefix="/api/clients")


def _slugify(name):
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


@bp.get("")
def list_clients():
    q = (request.args.get("q") or "").strip().lower()
    query = Client.query
    if q:
        query = query.filter(Client.name.ilike(f"%{q}%"))
    clients = query.order_by(Client.name).all()
    return jsonify({"ok": True, "clients": [c.to_dict() for c in clients]})


@bp.post("")
def create_client():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Client name is required."}), 400

    slug = _slugify(name)
    base_slug, i = slug, 2
    while Client.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{i}"
        i += 1

    client = Client(
        name=name, slug=slug, website=data.get("website"),
        primary_color=data.get("primary_color"), secondary_color=data.get("secondary_color"),
        phone=data.get("phone"), address=data.get("address"), cta=data.get("cta"),
        tagline=data.get("tagline"), industry=data.get("industry"),
        service_area=data.get("service_area"), brand_voice=data.get("brand_voice"),
        preferred_music_style=data.get("preferred_music_style"),
        preferred_spokesperson_id=data.get("preferred_spokesperson_id"),
        logo_url=data.get("logo_url"),
    )
    client.fonts = data.get("fonts") or []
    client.pronunciation_dict = data.get("pronunciation_dict") or {}
    db.session.add(client)
    db.session.commit()
    return jsonify({"ok": True, "client": client.to_dict()}), 201


@bp.get("/<int:client_id>")
def get_client(client_id):
    client = Client.query.get_or_404(client_id)
    return jsonify({"ok": True, "client": client.to_dict()})


@bp.put("/<int:client_id>")
def update_client(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json(force=True) or {}
    for field in ["website", "logo_url", "primary_color", "secondary_color", "phone", "address",
                  "cta", "tagline", "industry", "service_area", "brand_voice",
                  "preferred_voiceover_id", "preferred_music_style", "preferred_spokesperson_id"]:
        if field in data:
            setattr(client, field, data[field])
    if "fonts" in data:
        client.fonts = data["fonts"]
    if "pronunciation_dict" in data:
        client.pronunciation_dict = data["pronunciation_dict"]
    db.session.commit()
    return jsonify({"ok": True, "client": client.to_dict()})


@bp.delete("/<int:client_id>")
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    db.session.delete(client)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/analyze-website")
def analyze_website():
    """OpenAI reads the client's site and pre-populates the brand profile form."""
    data = request.get_json(force=True) or {}
    url = (data.get("website") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "website is required"}), 400
    profile = openai_service.analyze_website(url)
    return jsonify({"ok": True, "profile": profile, "live": openai_service.is_live()})


@bp.get("/<int:client_id>/assets")
def client_assets(client_id):
    client = Client.query.get_or_404(client_id)
    category = request.args.get("category", "photo")
    assets = cloudinary_service.list_client_assets(client.slug, category)
    return jsonify({"ok": True, "assets": assets, "live": cloudinary_service.is_live()})


@bp.post("/<int:client_id>/assets/upload")
def upload_client_asset(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json(force=True) or {}
    source_url = data.get("url")
    category = data.get("category", "photo")
    if not source_url:
        return jsonify({"ok": False, "error": "url is required"}), 400
    result = cloudinary_service.upload_asset(source_url, client.slug, category)
    return jsonify({"ok": True, "asset": result})
