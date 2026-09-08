"""Client voice capture shared by Commercial Builder and Radio Promo.

Staff creates a secure link. The client needs no SmartHub login: the public
page can record in-browser with a teleprompter or accept an existing audio
file. Submitted source audio is stored in the client's Cloudinary audio folder
and remains attached to the request for the team to review before creating an
ElevenLabs clone.

The public URLs intentionally live below /review/. Commercial Builder's login
guard already exempts that prefix for client review links, and the Hub strips
its staff chrome there as well.
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, abort, jsonify, render_template, request, url_for
from werkzeug.utils import secure_filename

from ..db import db
from ..models import Client, CommercialProject
from ..services import cloudinary_service
from ..voice_capture_models import VoiceCaptureRequest


bp = Blueprint("cb_voice_capture", __name__)

MAX_AUDIO_BYTES = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {"mp3", "wav", "m4a", "aac", "ogg", "webm", "mp4", "mpeg", "mpga"}
DEFAULT_EXPIRES_DAYS = 30

DEFAULT_VOICE_SCRIPT = """Hi, and thanks for taking a minute to listen.

I'm recording this sample so my voice can be used to create clear, natural-sounding messages and advertisements.

When I talk with customers, I like to sound friendly, confident, and conversational. I want people to feel like they're hearing from a real person, not someone simply reading an advertisement.

Every business has a story to tell. Sometimes the message is simple: stop in today, give us a call, visit our website, or learn more about what we can do for you.

Other times, we might have something exciting to announce.

This weekend only, save twenty percent on selected products and services. The offer starts Friday morning and ends Sunday evening. Call today to learn more and schedule an appointment.

We're open Monday through Friday from eight in the morning until six in the evening, and Saturday from nine until three.

Appointments are available today, tomorrow, and throughout the week.

You can call us at six-one-four, five-five-five, one-two-three-four, or visit our website for more information.

Sometimes a message needs a little more energy.

Don't miss your chance! This is one of our biggest events of the year, and we'd love to see you there.

And sometimes the message should be calmer and more reassuring.

We know you have choices, and we appreciate the opportunity to earn your business. Our team is here to answer your questions and help you find the solution that's right for you.

Whether we're talking about a special offer, an upcoming event, a new product, or simply inviting someone to learn more, I want my voice to sound natural, trustworthy, positive, and approachable.

Thanks for listening. I'm looking forward to putting my voice to work."""

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone development
    def _cb_log(*_a, **_k):
        return None


def _actor():
    try:
        from hub import auth as _hub_auth
        user = _hub_auth.user_from_environ(request.environ)
        return (getattr(user, "name", None) or getattr(user, "email", None)
                or str(user) or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


def _log(event, client="", detail="", **extra):
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


def _capture_url(token):
    try:
        path = url_for("commercial_builder.cb_voice_capture.client_voice_capture", token=token)
    except Exception:  # noqa: BLE001 — standalone fallback
        path = f"/tools/commercial-builder/review/voice/{token}"
    return request.host_url.rstrip("/") + path


def _public_request(token):
    row = VoiceCaptureRequest.query.filter_by(token=token).first()
    if row is None or not row.still_open():
        abort(404)
    return row


def _extension(filename):
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _request_values(data):
    script = str(data.get("script") or DEFAULT_VOICE_SCRIPT).strip()
    if len(script) < 100:
        return None, None, (jsonify({"ok": False, "error": "The recording script is too short."}), 400)
    if len(script) > 12000:
        return None, None, (jsonify({"ok": False, "error": "The recording script is too long."}), 400)
    try:
        expires_days = int(data.get("expires_days") or DEFAULT_EXPIRES_DAYS)
    except (TypeError, ValueError):
        expires_days = DEFAULT_EXPIRES_DAYS
    expires_days = max(1, min(expires_days, 90))
    return script, expires_days, None


def _as_item(row):
    item = row.to_dict()
    item["url"] = _capture_url(row.token) if row.still_open() else ""
    return item


def _client_identity(row):
    """Stable client name/folder for both builder-native and shared requests."""
    client = Client.query.get(row.client_id) if row.client_id else None
    name = (row.client_name or (client.name if client else "") or "Client").strip()
    slug = (row.client_slug or (client.slug if client else "") or "client").strip()
    return SimpleNamespace(name=name, slug=slug)


# ---------------------------------------------------------------------------
# Staff routes — normal Commercial Builder login required
# ---------------------------------------------------------------------------
@bp.get("/api/projects/<int:project_id>/voice-capture-requests")
def list_voice_capture_requests(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    rows = (VoiceCaptureRequest.query.filter_by(project_id=project.id)
            .order_by(VoiceCaptureRequest.id.desc()).limit(20).all())
    return jsonify({"ok": True, "requests": [_as_item(r) for r in rows],
                    "default_script": DEFAULT_VOICE_SCRIPT})


@bp.post("/api/projects/<int:project_id>/voice-capture-requests")
def create_voice_capture_request(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(silent=True) or {}
    script, expires_days, error = _request_values(data)
    if error:
        return error

    row = VoiceCaptureRequest(
        token=secrets.token_urlsafe(36),
        project_id=project.id,
        client_id=project.client_id,
        source_module="commercial_builder",
        source_ref=str(project.id),
        client_name=project.client.name,
        client_slug=project.client.slug,
        context_label=project.title or "Commercial Builder",
        script_text=script,
        created_by=_actor(),
        expires_at=datetime.utcnow() + timedelta(days=expires_days),
    )
    db.session.add(row)
    db.session.commit()

    _log("voice_capture_requested", client=project.client.name,
         detail=f"Client voice recording link created for {project.title or 'commercial'}",
         project_id=project.id, request_id=row.id)
    return jsonify({"ok": True, "request": _as_item(row)}), 201


@bp.get("/api/radio-projects/<radio_project_id>/voice-capture-requests")
def list_radio_voice_capture_requests(radio_project_id):
    from modules.radio_promo import store as radio_store
    project = radio_store.get(radio_project_id)
    if not project:
        abort(404)
    rows = (VoiceCaptureRequest.query
            .filter_by(source_module="radio_promo", source_ref=radio_project_id)
            .order_by(VoiceCaptureRequest.id.desc()).limit(20).all())
    return jsonify({"ok": True, "requests": [_as_item(r) for r in rows],
                    "default_script": DEFAULT_VOICE_SCRIPT})


@bp.post("/api/radio-projects/<radio_project_id>/voice-capture-requests")
def create_radio_voice_capture_request(radio_project_id):
    from modules.radio_promo import store as radio_store
    project = radio_store.get(radio_project_id)
    if not project:
        abort(404)
    client_name = str(project.get("client") or "").strip()
    if not client_name:
        return jsonify({"ok": False,
                        "error": "Attach this radio project to a client before requesting their voice."}), 400

    data = request.get_json(silent=True) or {}
    script, expires_days, error = _request_values(data)
    if error:
        return error

    row = VoiceCaptureRequest(
        token=secrets.token_urlsafe(36),
        source_module="radio_promo",
        source_ref=radio_project_id,
        client_name=client_name,
        client_slug=str(project.get("client_slug") or radio_store.slugify(client_name)),
        context_label=str(project.get("project_name") or project.get("company") or "Radio Promo")[:300],
        script_text=script,
        created_by=_actor(),
        expires_at=datetime.utcnow() + timedelta(days=expires_days),
    )
    db.session.add(row)
    db.session.commit()

    _log("voice_capture_requested", client=client_name,
         detail="Client voice recording link created from Radio Promo",
         radio_project_id=radio_project_id, request_id=row.id)
    return jsonify({"ok": True, "request": _as_item(row)}), 201


@bp.post("/api/voice-capture-requests/<int:capture_id>/revoke")
def revoke_voice_capture_request(capture_id):
    row = VoiceCaptureRequest.query.get_or_404(capture_id)
    row.revoked = True
    if row.status == "pending":
        row.status = "revoked"
    db.session.commit()
    _log("voice_capture_revoked", client=_client_identity(row).name,
         detail="Client voice recording link revoked", request_id=row.id)
    return jsonify({"ok": True, "request": row.to_dict()})


# ---------------------------------------------------------------------------
# Client routes — no Hub account required; token is the capability
# ---------------------------------------------------------------------------
@bp.get("/review/voice/<token>")
def client_voice_capture(token):
    row = _public_request(token)
    client = _client_identity(row)
    project = CommercialProject.query.get(row.project_id) if row.project_id else None

    row.opened_count = int(row.opened_count or 0) + 1
    row.last_opened_at = datetime.utcnow()
    db.session.commit()

    return render_template(
        "voice_capture_public.html",
        capture=row,
        project=project,
        client=client,
    )


@bp.post("/review/voice/<token>/submit")
def submit_client_voice_capture(token):
    row = _public_request(token)
    client = _client_identity(row)

    if request.content_length and request.content_length > MAX_AUDIO_BYTES + (1024 * 1024):
        return jsonify({"ok": False, "error": "That file is larger than the 50 MB limit."}), 413

    if str(request.form.get("consent") or "").lower() not in {"1", "true", "yes", "on"}:
        return jsonify({"ok": False, "error": "Please confirm voice-use permission before submitting."}), 400

    submitter_name = str(request.form.get("submitter_name") or "").strip()[:200]
    if not submitter_name:
        return jsonify({"ok": False, "error": "Please enter your name."}), 400

    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        return jsonify({"ok": False, "error": "Please record or upload an audio file."}), 400

    filename = secure_filename(audio.filename) or "client-voice.webm"
    ext = _extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return jsonify({"ok": False, "error": f"Please use an audio file in one of these formats: {allowed}."}), 400

    blob = audio.read(MAX_AUDIO_BYTES + 1)
    if not blob:
        return jsonify({"ok": False, "error": "The audio file is empty."}), 400
    if len(blob) > MAX_AUDIO_BYTES:
        return jsonify({"ok": False, "error": "That file is larger than the 50 MB limit."}), 413

    source_type = str(request.form.get("source_type") or "uploaded").lower()
    if source_type not in {"recorded", "uploaded"}:
        source_type = "uploaded"

    public_id = f"voice-capture-{row.id}-{int(datetime.utcnow().timestamp())}"
    stored = cloudinary_service.upload_asset(
        blob,
        client.slug,
        "audio",
        public_id=public_id,
        resource_type="video",
        client_name=client.name,
        filename=filename,
    )
    if not stored.get("secure_url"):
        return jsonify({
            "ok": False,
            "error": "We couldn't store the recording. Please try again or contact Smart 1 Marketing.",
        }), 503

    try:
        duration = float(request.form.get("duration_seconds") or 0) or None
    except (TypeError, ValueError):
        duration = None

    row.status = "submitted"
    row.submitted_at = datetime.utcnow()
    row.submitter_name = submitter_name
    row.submitter_email = str(request.form.get("submitter_email") or "").strip()[:300]
    row.source_type = source_type
    row.original_filename = filename
    row.mime_type = str(audio.mimetype or "")[:120]
    row.file_size = len(blob)
    row.duration_seconds = duration
    row.audio_url = stored.get("secure_url") or ""
    row.cloudinary_public_id = stored.get("public_id") or ""
    row.consent = True
    db.session.commit()

    extra = {"request_id": row.id, "audio_url": row.audio_url,
             "source_module": row.source_module or ""}
    if row.project_id:
        extra["project_id"] = row.project_id
    if row.source_module == "radio_promo":
        extra["radio_project_id"] = row.source_ref
    _log("voice_capture_submitted", client=client.name,
         detail=f"{submitter_name} submitted a {source_type} voice sample", **extra)

    return jsonify({
        "ok": True,
        "message": "Your voice sample was sent to Smart 1 Marketing.",
    })
