"""Client Brand Profile endpoints (spec section 2).

**A change to a client's own record needs a name against it.** Sites Admin
recorded deleting a client's website and not creating one, and Google Finder
recorded deploying a tag and not deploying a pixel into the same container —
in both, the *creating* half of a create/destroy pair was the half left out.
This file was worse than either: it created, updated, adopted and destroyed a
client and recorded none of it, so the brand profile a rep spent an afternoon
on appeared from nowhere and left the same way.

And what it destroyed was not one row. `Client.projects` cascades, so one
unconfirmed DELETE took every project, scene and render job with it — while
the render approvals, the review shares and the compliance acknowledgments,
which are keyed on a project and are *not* in that cascade, stayed behind
pointing at ids that no longer resolve. Those three are the records that
exist precisely for the day a client says **"we never signed off on that"**,
and they were being left as fragments naming nothing while the thing they
described was gone. The route answered `{"ok": true}` and said nothing about
any of it.

So a client with work behind them is refused, with the counts named, and a
`confirm` carrying the client's exact name is what forces it — the rule
`modules/image_picker` already applies to deleting a gallery, where the name
is typed rather than an OK button pressed. Refusing outright is what would
have been wrong: a check that refuses the correct thing is one somebody
switches off, and switching this off costs the recording too.
"""

from flask import Blueprint, jsonify, request

from .. import client_link, teardown
from ..db import db
from ..models import Client
from ..services import openai_service, cloudinary_service

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _cb_log(*_a, **_k):
        return None

# Writes on this blueprint that deliberately record nothing, each with the
# reason. Declared rather than left as an absence, so the remainder is a
# decision somebody made rather than one nobody has noticed yet — and an
# entry naming a route that is gone, or one that has since started logging,
# is a caller's to reject.
HOUSEKEEPING_ROUTES = {
    "analyze_website": "reads the client's public site and fills a form in "
                       "the browser; nothing is written until Save, which is "
                       "create_client or update_client and is recorded there.",
    "upload_client_asset": "stores a logo the rep is still choosing between. "
                           "What reaches the client's record is the "
                           "`logo_url` on the profile, written by "
                           "update_client.",
}


bp = Blueprint("cb_clients", __name__, url_prefix="/api/clients")


def _slugify(name):
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def _log(event, client=None, detail="", **extra):
    """Never costs the write it describes.

    `audit.log()`'s first positional is `module` and `for_module` binds it —
    the trap CLAUDE.md names twice. The detail is built by the caller and
    passed in, because `submit_render` proved the swallow protects the *call*
    and not the arguments: an f-string over an attribute the model does not
    have raises before the guard can apply.
    """
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


bp = Blueprint("cb_clients", __name__, url_prefix="/api/clients")


def _slugify(name):
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def _log(event, client=None, detail="", **extra):
    """Never costs the write it describes.

    `audit.log()`'s first positional is `module` and `for_module` binds it —
    the trap CLAUDE.md names twice. The detail is built by the caller and
    passed in, because `submit_render` proved the swallow protects the *call*
    and not the arguments: an f-string over an attribute the model does not
    have raises before the guard can apply.
    """
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


def _work_behind(client_id):
    """What would go with this client, counted before anything is deleted.

    Read as counts rather than rows: this answers a refusal and an activity
    entry, and neither is improved by carrying a client's whole book into a
    response or a log line.
    """
    out = {"projects": 0, "scenes": 0, "render_jobs": 0, "approved_cuts": 0,
           "review_rounds": 0, "compliance_acks": 0, "variations": 0}
    try:
        pids = [row[0] for row in db.session.query(CommercialProject.id)
                .filter(CommercialProject.client_id == client_id).all()]
        out["projects"] = len(pids)
        if not pids:
            return out
        for key, model in (("scenes", Scene), ("render_jobs", RenderJob),
                           ("approved_cuts", RenderApproval),
                           ("review_rounds", ReviewShare),
                           ("compliance_acks", ComplianceAck)):
            out[key] = (model.query
                        .filter(model.project_id.in_(pids)).count())
        out["variations"] = (Variation.query.filter(
            Variation.parent_project_id.in_(pids)
            | Variation.child_project_id.in_(pids)).count())
    except Exception:  # noqa: BLE001 — a count that cannot be taken must not
        pass                                  # cost the refusal it informs.
    return out


def _work_summary(counts):
    """The counts as a sentence, naming only what is actually there."""
    parts = []
    for key, one, many in (("projects", "spot", "spots"),
                           ("approved_cuts", "approved cut", "approved cuts"),
                           ("review_rounds", "review round", "review rounds"),
                           ("compliance_acks", "compliance sign-off",
                            "compliance sign-offs")):
        n = counts.get(key) or 0
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


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
    # After the commit, so a create the database refused is not written down
    # as one that happened — the shape `approve_render` already uses.
    _log("cb_client_created", client=client.name,
         detail=f"Brand profile created for {client.name}.")
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
    # Which fields moved, not their values: this is a brand profile, and the
    # entry is read into a page. "Somebody changed the preferred voice" is
    # what a reader needs; the value is on the record.
    touched = sorted(k for k in data
                     if k in ("website", "logo_url", "primary_color",
                              "secondary_color", "phone", "address", "cta",
                              "tagline", "industry", "service_area",
                              "brand_voice", "preferred_voiceover_id",
                              "preferred_music_style",
                              "preferred_spokesperson_id", "fonts",
                              "pronunciation_dict"))
    _log("cb_client_updated", client=client.name,
         detail=(f"Brand profile updated: {', '.join(touched)}."
                 if touched else "Brand profile saved with no changes."))
    return jsonify({"ok": True, "client": client.to_dict()})


@bp.delete("/<int:client_id>")
def delete_client(client_id):
    client = Client.query.get_or_404(client_id)
    # The name is read **before** the delete and it is the row's own, never a
    # name the caller passed: an activity entry naming the wrong client is
    # worse than one naming none, which is what `modules/suite_panel` had to
    # undo on the route that deletes a Suite sub-account.
    name = client.name
    pids = teardown.project_ids_for_client(client_id)
    counts = teardown.work_behind(pids)
    summary = teardown.summarize(counts)

    if summary and not teardown.confirmed(request, name):
        return jsonify({"ok": False,
                        "error": teardown.confirmation_error(name, summary),
                        "needs_confirmation": True, "client": name,
                        "counts": counts}), 409

    removed_orphans = teardown.sweep_orphans(pids)
    db.session.delete(client)
    db.session.commit()
    # After the commit. This entry is the only record that the deletion
    # happened and the only place the counts survive, so it is what somebody
    # reconstructs from.
    _log("cb_client_deleted", client=name,
         detail=("Client and brand profile deleted"
                 + (f", with {summary}." if summary else ".")),
         counts=counts)
    return jsonify({"ok": True, "client": name, "counts": counts,
                    "removed_orphans": removed_orphans})


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
    # The name as well as the slug: the gallery is keyed on the client's name
    # and this module's slug is its own, so resolving one back would be a guess.
    result = cloudinary_service.upload_asset(source_url, client.slug, category,
                                             client_name=client.name)
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
    # Adopting is the moment a commercial becomes attributable to a client on
    # the Hub's books, so it is the join worth recording — and it is recorded
    # only where a row was actually created, because returning an existing
    # profile changes nothing and an entry for it would read as a second
    # adoption of a client already adopted.
    _log("cb_client_adopted", client=client.name,
         detail=(f"Brand profile created for {client.name} from the Hub "
                 f"client list."))
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
