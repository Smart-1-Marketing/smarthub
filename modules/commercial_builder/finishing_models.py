"""Additive production tables; no ALTER TABLE required on existing installs."""
from datetime import datetime
from .db import db
from .models import JSONField


class RenderInspection(db.Model):
    __tablename__ = "cb_render_inspections"
    job_id = db.Column(db.Integer, db.ForeignKey("cb_render_jobs.id", ondelete="CASCADE"), primary_key=True)
    expected_json = db.Column(db.Text)
    result_json = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    output_url = db.Column(db.Text)
    checked_at = db.Column(db.DateTime)
    expected = JSONField("expected_json")
    result = JSONField("result_json")


class BrandPreset(db.Model):
    __tablename__ = "cb_brand_presets"
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("cb_clients.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    source_project_id = db.Column(db.Integer, nullable=False)
    approved_by = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    snapshot_json = db.Column(db.Text, nullable=False)
    snapshot = JSONField("snapshot_json")


class ProductionUsage(db.Model):
    __tablename__ = "cb_production_usage"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = db.Column(db.String(40), nullable=False)
    operation = db.Column(db.String(120))
    units = db.Column(db.Float)
    cost_usd = db.Column(db.Float)  # None means not priced; never implies free.
    cached = db.Column(db.Boolean, default=False)
    ok = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class ProjectBudget(db.Model):
    __tablename__ = "cb_project_budgets"
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id", ondelete="CASCADE"), primary_key=True)
    limit_cents = db.Column(db.Integer, nullable=False)
    prior_cents = db.Column(db.Integer)  # None: past spending has not been reconciled.
    reserved_cents = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class BudgetReservation(db.Model):
    __tablename__ = "cb_budget_reservations"
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("cb_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    operation = db.Column(db.String(120), nullable=False)
    cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
