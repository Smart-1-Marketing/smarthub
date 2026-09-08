"""Staff-managed customer voice library shared by every ElevenLabs creator.

Only metadata is persisted. Cloning reservations survive timeouts so resubmitting
the same upload cannot create a second paid voice at the provider.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from hub import jsonstore, audit
from hub.blueprint_guard import install

bp = Blueprint("customer_voices", __name__)
install(bp)
log = audit.for_module("customer_voices")
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 50 * 1024 * 1024
EXTENSIONS = {"mp3", "wav", "m4a", "aac", "ogg", "webm"}


class LibraryError(ValueError):
    pass


def _path():
    return os.path.join(jsonstore.data_root(), "customer-voices", "library.json")


def records():
    return list(jsonstore.read_json(_path(), default={}).values())


def public(row):
    return {k: row.get(k) for k in ("id", "voice_id", "name", "client", "description",
            "status", "preview_url", "created_at", "error")}


def ensure_usable(voice_id):
    """Stock voices pass through; managed voices must have cleared verification."""
    for row in records():
        if row.get("voice_id") == voice_id and row.get("status") != "ready":
            raise LibraryError("This customer voice is not ready. Open Customer Voices to check its status.")


def _save(record_id, changes):
    def change(rows):
        rows[record_id].update(changes)
        return rows
    jsonstore.update_json(_path(), change, default={})


def _status(voice):
    return "ready" if voice.get("requires_verification") is False else "verification_required"


@bp.get("/tools/customer-voices/")
def page():
    return render_template("customer_voices.html")


@bp.get("/api/customer-voices")
def listing():
    rows = records()
    if request.args.get("ready") == "1":
        rows = [r for r in rows if r.get("status") == "ready"]
    return jsonify(ok=True, voices=[public(r) for r in sorted(rows, key=lambda r: r.get("created_at", ""), reverse=True)])


def _capture_rows():
    from modules.commercial_builder.voice_capture_models import VoiceCaptureRequest
    query = VoiceCaptureRequest.query.filter_by(status="submitted", consent=True, revoked=False)
    capture_id = request.args.get("capture_id", type=int)
    if capture_id:
        query = query.filter_by(id=capture_id)
    return query.order_by(VoiceCaptureRequest.id.desc()).limit(100).all()


@bp.get("/api/customer-voices/captures")
def captures():
    return jsonify(ok=True, captures=[dict(id=r.id, client=r.client_name or (r.client.name if hasattr(r, "client") and r.client else ""),
        name=r.submitter_name, filename=r.original_filename, audio_url=r.audio_url) for r in _capture_rows()])


def _capture_sample(capture_id):
    from urllib.parse import urlsplit
    import requests
    from modules.commercial_builder.voice_capture_models import VoiceCaptureRequest
    row = VoiceCaptureRequest.query.get(capture_id)
    if not row or row.status != "submitted" or not row.consent or row.revoked:
        raise LibraryError("Choose a submitted recording with the speaker's permission.")
    url = urlsplit(row.audio_url or "")
    if url.scheme != "https" or url.hostname != "res.cloudinary.com" or url.username or url.password or url.port not in (None, 443):
        raise LibraryError("This recording cannot be imported directly. Download it from the capture panel and upload it here.")
    try:
        with requests.get(row.audio_url, timeout=(10, 45), stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise LibraryError("Could not read the submitted recording. Try again later.")
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_FILE:
                    raise LibraryError("Trim this recording to under 25 MB before uploading it here.")
    except requests.RequestException as exc:
        raise LibraryError("Could not read the submitted recording. Try again later.") from exc
    if not data:
        raise LibraryError("The submitted recording is empty.")
    return row, (row.original_filename or "recording.webm", bytes(data), row.mime_type or "audio/webm")


@bp.errorhandler(LibraryError)
def invalid(exc):
    return jsonify(ok=False, error=str(exc)), 400


@bp.errorhandler(RequestEntityTooLarge)
def too_large(exc):
    return jsonify(ok=False, error="Keep recordings under 25 MB each and 50 MB total."), 413


@bp.post("/api/customer-voices")
def create():
    from modules.radio_promo import voices
    request.max_content_length = MAX_TOTAL + 1024 * 1024
    form = request.form
    name, client = form.get("name", "").strip(), form.get("client", "").strip()
    description = form.get("description", "").strip()
    if not name or not client or len(name) > 120 or len(client) > 200 or len(description) > 1000:
        raise LibraryError("Enter a customer and voice name (up to 200 and 120 characters). Keep the description under 1,000 characters.")
    if form.get("authorized") != "true":
        raise LibraryError("Confirm that you own this voice or have the speaker's written permission.")
    record_id = form.get("request_id", "")
    try:
        record_id = str(uuid.UUID(record_id))
    except ValueError:
        raise LibraryError("Reload the page before submitting this voice.") from None
    existing_id = form.get("voice_id", "").strip()
    if existing_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", existing_id):
        raise LibraryError("Enter a valid ElevenLabs voice ID.")
    capture_id = form.get("capture_id", "").strip()
    samples = []
    uploads = request.files.getlist("files")
    if (existing_id and capture_id) or ((existing_id or capture_id) and uploads):
        raise LibraryError("Choose recordings or an existing voice ID.")
    if capture_id:
        if not capture_id.isdigit():
            raise LibraryError("Choose a submitted customer recording.")
        capture, sample = _capture_sample(int(capture_id))
        client = capture.client_name or client
        samples.append(sample)
    if not existing_id and not capture_id and not 1 <= len(uploads) <= 5:
        raise LibraryError("Upload between one and five recordings.")
    total = 0
    for upload in uploads:
        filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if filename.rsplit(".", 1)[-1].lower() not in EXTENSIONS:
            raise LibraryError("Use MP3, WAV, M4A, AAC, OGG, or WebM recordings.")
        data = upload.read(MAX_FILE + 1)
        total += len(data)
        if not data or len(data) > MAX_FILE or total > MAX_TOTAL:
            raise LibraryError("Recordings must be nonempty, under 25 MB each and 50 MB total.")
        samples.append((filename, data, upload.mimetype))
    fingerprint = hashlib.sha256(json.dumps([name, client, description, existing_id,
        [hashlib.sha256(s[1]).hexdigest() for s in samples]], sort_keys=True).encode()).hexdigest()
    from hub.auth import user_from_environ
    user = user_from_environ(request.environ) or {}
    row = dict(id=record_id, name=name, client=client, description=description, voice_id=existing_id or None,
               status="creating", created_at=datetime.now(timezone.utc).isoformat(),
               fingerprint=fingerprint, capture_id=capture_id or None, permission_confirmed=True,
               permission_by=str(user or "staff"))
    prior = []

    def reserve(rows):
        if record_id in rows:
            old = rows[record_id]
            if old.get("status") != "failed" or old.get("fingerprint") != fingerprint:
                prior.append(old)
                return None
            # A retry still checks other tabs for an accepted or in-flight clone.
        if not existing_id:
            matching = next((r for r in rows.values() if r.get("fingerprint") == fingerprint and r.get("status") != "failed"), None)
            if matching:
                prior.append(matching)
                return None
        if existing_id and any(r.get("voice_id") == existing_id and r.get("status") not in ("failed", "needs_review") for r in rows.values()):
            raise LibraryError("That voice is already saved in Customer Voices.")
        if existing_id:
            for key in list(rows):
                if rows[key].get("voice_id") == existing_id and rows[key].get("status") in ("failed", "needs_review"):
                    del rows[key]  # Replace a failed read-only import, preserving one guard per voice.
        rows[record_id] = row
        return rows

    jsonstore.update_json(_path(), reserve, default={})
    if prior:
        old = prior[0]
        if old.get("fingerprint") != fingerprint:
            return jsonify(ok=False, error="This submission already exists. Start a new voice for different recordings."), 409
        if old.get("status") in ("creating", "needs_review"):
            return jsonify(ok=False, voice=public(old), error="This submission is awaiting confirmation. Check ElevenLabs before starting another clone; save its existing voice ID if it completed."), 409
        return jsonify(ok=True, voice=public(old))
    try:
        voice = voices.get_voice(existing_id) if existing_id else voices.clone_voice(name, samples, description)
    except (voices.VoiceError, ValueError) as exc:
        safe_to_retry = bool(existing_id) or isinstance(exc, voices.VoiceRequestRejected)
        message = str(exc) if isinstance(exc, voices.VoiceError) else "ElevenLabs returned an unreadable voice response."
        if not safe_to_retry:
            message += " The result is uncertain. Check ElevenLabs and use its existing voice ID if it was created; this submission will not create another clone."
        _save(record_id, dict(status="failed" if safe_to_retry else "needs_review", error=message))
        return jsonify(ok=False, error=message), 502
    changes = dict(voice_id=voice["voice_id"], status=_status(voice), preview_url=voice.get("preview_url") or "", error=None)
    _save(record_id, changes)
    row.update(changes)
    log("voice_saved", client=client, detail=name, voice_id=row["voice_id"])
    return jsonify(ok=True, voice=public(row)), 201


@bp.post("/api/customer-voices/<record_id>/refresh")
def refresh(record_id):
    from modules.radio_promo import voices
    row = next((r for r in records() if r["id"] == record_id), None)
    if not row or not row.get("voice_id"):
        raise LibraryError("This submission has no confirmed voice ID. Check ElevenLabs and save the existing voice ID if it completed.")
    try:
        voice = voices.get_voice(row["voice_id"])
    except voices.VoiceError as exc:
        return jsonify(ok=False, error=str(exc)), 502
    changes = dict(status=_status(voice), preview_url=voice.get("preview_url") or "", error=None)
    _save(record_id, changes)
    row.update(changes)
    log("voice_refreshed", client=row["client"], detail=row["name"], voice_id=row["voice_id"])
    return jsonify(ok=True, voice=public(row))
