"""Persistence for client voice recording/upload requests.

Kept in its own model module so the feature can add a new table safely on
existing SmartHub databases. The Hub's normal create_all pass creates missing
tables; no existing Commercial Builder table needs a column migration.

The request stores its client/context labels as well as optional Commercial
Builder foreign keys so the same capture can be reused by Radio Promo and
other SmartHub creative tools without needing another voice-upload system.
"""

from datetime import datetime

from .db import db


class VoiceCaptureRequest(db.Model):
    __tablename__ = "cb_voice_capture_requests"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(96), unique=True, index=True, nullable=False)

    # Optional native Commercial Builder references. Shared tools such as
    # Radio Promo can instead use source_module/source_ref plus the stored
    # client identity below.
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id"), nullable=True, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("cb_clients.id"), nullable=True, index=True)
    source_module = db.Column(db.String(60), default="commercial_builder", nullable=False)
    source_ref = db.Column(db.String(160), default="")
    client_name = db.Column(db.String(240), default="")
    client_slug = db.Column(db.String(240), default="")
    context_label = db.Column(db.String(300), default="")

    script_text = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.String(200), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, default=False, nullable=False)

    opened_count = db.Column(db.Integer, default=0, nullable=False)
    last_opened_at = db.Column(db.DateTime, nullable=True)

    status = db.Column(db.String(30), default="pending", nullable=False)
    submitted_at = db.Column(db.DateTime, nullable=True)
    submitter_name = db.Column(db.String(200), default="")
    submitter_email = db.Column(db.String(300), default="")
    source_type = db.Column(db.String(30), default="")  # recorded | uploaded
    original_filename = db.Column(db.String(300), default="")
    mime_type = db.Column(db.String(120), default="")
    file_size = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    audio_url = db.Column(db.String(800), default="")
    cloudinary_public_id = db.Column(db.String(500), default="")
    consent = db.Column(db.Boolean, default=False, nullable=False)

    def is_available(self, now=None):
        now = now or datetime.utcnow()
        if self.revoked or self.status != "pending":
            return False
        if self.expires_at and self.expires_at < now:
            return False
        return True

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "client_id": self.client_id,
            "source_module": self.source_module or "",
            "source_ref": self.source_ref or "",
            "client_name": self.client_name or "",
            "client_slug": self.client_slug or "",
            "context_label": self.context_label or "",
            "status": self.status,
            "revoked": bool(self.revoked),
            "created_by": self.created_by or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "opened_count": int(self.opened_count or 0),
            "last_opened_at": self.last_opened_at.isoformat() if self.last_opened_at else None,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "submitter_name": self.submitter_name or "",
            "submitter_email": self.submitter_email or "",
            "source_type": self.source_type or "",
            "original_filename": self.original_filename or "",
            "mime_type": self.mime_type or "",
            "file_size": self.file_size,
            "duration_seconds": self.duration_seconds,
            "audio_url": self.audio_url or "",
            "cloudinary_public_id": self.cloudinary_public_id or "",
            "consent": bool(self.consent),
        }
