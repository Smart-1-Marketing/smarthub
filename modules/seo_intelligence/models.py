"""Persistent weekly Search Console intelligence for SmartHub SEO clients."""
from __future__ import annotations

from datetime import datetime, timezone

from hub.extensions import db


def _now():
    return datetime.now(timezone.utc)


class SEOProperty(db.Model):
    __tablename__ = "seo_properties"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(180), nullable=False, index=True)
    site_url = db.Column(db.String(1000), nullable=False, unique=True)
    display_name = db.Column(db.String(255), nullable=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    last_sync_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_sync_status = db.Column(db.String(40), nullable=True)
    last_sync_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class SEOSnapshot(db.Model):
    __tablename__ = "seo_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    property_id = db.Column(db.Integer, db.ForeignKey("seo_properties.id"), nullable=False, index=True)
    week_start = db.Column(db.Date, nullable=False, index=True)
    period_start = db.Column(db.Date, nullable=False)
    period_end = db.Column(db.Date, nullable=False)
    metrics_json = db.Column(db.Text, nullable=False, default="{}")
    queries_json = db.Column(db.Text, nullable=False, default="[]")
    pages_json = db.Column(db.Text, nullable=False, default="[]")
    sitemaps_json = db.Column(db.Text, nullable=False, default="[]")
    inspections_json = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    __table_args__ = (db.UniqueConstraint("property_id", "week_start", name="uq_seo_snapshot_property_week"),)


class SEOMemory(db.Model):
    __tablename__ = "seo_memory"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(180), nullable=False, unique=True, index=True)
    memory_json = db.Column(db.Text, nullable=False, default="{}")
    source_week = db.Column(db.Date, nullable=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class SEORecommendation(db.Model):
    __tablename__ = "seo_recommendations"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(180), nullable=False, index=True)
    property_id = db.Column(db.Integer, db.ForeignKey("seo_properties.id"), nullable=False, index=True)
    recommendation_key = db.Column(db.String(500), nullable=False, index=True)
    kind = db.Column(db.String(60), nullable=False, index=True)
    title = db.Column(db.String(500), nullable=False)
    page_url = db.Column(db.String(1500), nullable=True)
    # The table's column is `query` and the Python attribute deliberately is
    # not. `db.Model` carries Flask-SQLAlchemy's `query` descriptor -- the
    # thing every `Model.query.filter_by(...)` in this Hub reads -- and a
    # mapped attribute of that name on a subclass shadows it for that one
    # class: `SEORecommendation.query` answered the column's
    # InstrumentedAttribute, `.filter_by()` on it raised AttributeError, and
    # that sat inside `_save_recommendations()` and both read routes. So
    # every weekly refresh rolled back before the snapshot or the memory was
    # written, and the action queue answered 500 to a page that then drew
    # nothing. The column name is kept so the table `create_all()` already
    # made needs no migration; only the attribute moved.
    # `hub/integrity.check_shadowed_model_query()` refuses the next one.
    search_query = db.Column("query", db.String(1000), nullable=True)
    priority_score = db.Column(db.Integer, nullable=False, default=0, index=True)
    impact = db.Column(db.String(20), nullable=False, default="medium")
    effort = db.Column(db.String(20), nullable=False, default="medium")
    evidence_json = db.Column(db.Text, nullable=False, default="{}")
    actions_json = db.Column(db.Text, nullable=False, default="[]")
    status = db.Column(db.String(30), nullable=False, default="open", index=True)
    first_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    __table_args__ = (db.UniqueConstraint("property_id", "recommendation_key", name="uq_seo_recommendation_key"),)


class SEOAction(db.Model):
    __tablename__ = "seo_actions"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.String(180), nullable=False, index=True)
    recommendation_id = db.Column(db.Integer, db.ForeignKey("seo_recommendations.id"), nullable=True, index=True)
    action_type = db.Column(db.String(80), nullable=False)
    page_url = db.Column(db.String(1500), nullable=True)
    before_json = db.Column(db.Text, nullable=False, default="{}")
    after_json = db.Column(db.Text, nullable=False, default="{}")
    external_ref = db.Column(db.String(500), nullable=True)
    actor = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_now)
    measured_at = db.Column(db.DateTime(timezone=True), nullable=True)
    outcome_json = db.Column(db.Text, nullable=False, default="{}")
