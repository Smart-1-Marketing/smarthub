"""Client Brand Profile endpoints (spec section 2)."""

from flask import Blueprint, jsonify, request

from .. import client_link
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


# ---------------------------------------------------------------------------
# Starting a commercial for a client the agency already has.
#
# The Start page's dropdown listed `cb_clients` -- this module's own table --
# which is empty on a fresh install and only ever contains businesses somebody
# typed into it. So on a Hub whose client book is several hundred businesses in
# Knack, "pick an existing client" was a thing the page appeared to offer and
# could not do, and the way round it was to retype a client of eleven years'
# standing as a new one. The commercial was then filed under a name that joins
# to nothing: no products, no scans, no Client 360 card, no phone number.
# ---------------------------------------------------------------------------
@bp.get("/hub-search")
def hub_search():
    """Type-ahead over the agency's real client list.

    The limit goes through hub.webargs.clamp_int rather than int(): ?limit=-1
    was a 500 on Postgres and a full table dump on SQLite, and /api/integrity
    has a check that names any route taking a raw one.
    """
    query = (request.args.get("q") or "").strip()
    found = client_link.search(query, limit=_limit(request.args.get("limit"), 12, 50))
    return jsonify({"ok": True, **found})


def _limit(raw, default, high):
    try:
        from hub.webargs import clamp_int
    except Exception:  # noqa: BLE001 — standalone, no hub to import from
        try:
            return max(1, min(int(raw), high))
        except (TypeError, ValueError):
            return default
    return clamp_int(raw, default, 1, high)


@bp.post("/adopt")
def adopt_hub_client():
    """Make a brand profile for a client that is already on the Hub's books.

    Three rules, each a way to be wrong quietly:

    * **The name has to match a registry row exactly.** A name that matches
      nothing is refused by name, with "create as new" named as the way out —
      the shape `modules/google_access` uses. Adopting a business the registry
      does not have would file the commercial under a client nothing joins to
      while reading as a clean success.
    * **A business already adopted is returned, not adopted again.** Two
      `cb_clients` rows for one company split its commercials across two
      histories, and the second one looks exactly like a client with no work.
      The comparison is `hub/client_key.same_client` — domain first, exact
      normalised name second, never a substring.
    * **Nothing is written back to the registry.** Knack owns the client
      record and this Hub does not write to it. What this creates is a brand
      profile — fonts, pronunciation, preferred voice — which exists nowhere
      else and is this table's own.
    """
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "A client name is required."}), 400

    row = client_link.find_in_registry(name)
    if not row:
        return jsonify({"ok": False, "error": (
            f"\u201c{name}\u201d is not on the Hub client list, so there is nothing to "
            f"adopt. Check the spelling, or create it as a new client — a business "
            f"we are pitching has no client record yet, and that is the normal case."
        )}), 404

    profile = client_link.profile_from_registry(row)
    existing = client_link.existing_row(Client.query.all(), profile["name"],
                                        profile["website"])
    if existing:
        return jsonify({"ok": True, "client": existing.to_dict(), "created": False,
                        "note": client_link.refresh_note(existing, row)})

    slug = _slugify(profile["name"])
    base_slug, i = slug, 2
    while Client.query.filter_by(slug=slug).first():
        slug = f"{base_slug}-{i}"
        i += 1

    client = Client(name=profile["name"], slug=slug, website=profile["website"] or None,
                    phone=profile["phone"] or None, industry=profile["industry"] or None)
    db.session.add(client)
    db.session.commit()
    return jsonify({"ok": True, "client": client.to_dict(), "created": True, "note": ""}), 201


@bp.get("/<int:client_id>/hub-context")
def client_hub_context(client_id):
    """Whether this brand profile joins to a client the Hub already knows.

    Tri-state, and the third state is the point: `checked: False` means the
    registry could not be read, which must not be drawn as "new business" —
    that is what sends somebody to create a duplicate of a client we have had
    for years.
    """
    client = Client.query.get_or_404(client_id)
    context = client_link.hub_client_context(client.name, client.website or "")
    return jsonify({"ok": True, "client_id": client.id, **context})
