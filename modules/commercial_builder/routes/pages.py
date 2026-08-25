"""Server-rendered pages for the Commercial Builder wizard. Each step is a
thin template that boots vanilla JS talking to the JSON API in the sibling
route modules — no separate front-end build step, matching the rest of
Smart 1 Hub's Flask + Jinja + vanilla-JS tools (Image Creator, UTM Builder)."""

from flask import Blueprint, redirect, render_template, url_for

from ..config import (COMMERCIAL_LENGTHS, OUTPUT_FORMATS, COMMERCIAL_TYPES, TONE_OPTIONS,
                       MUSIC_MOODS, MUSIC_LEVELS, VOICE_STYLES, CTA_STYLES,
                       V2_PROVIDERS, PLATFORMS, QR_CODE_RULES,
                       LOGO_PERSISTENCE_RULES, get_structure, qr_eligible)
from ..models import Client, CommercialProject
from ..services import provider_check

bp = Blueprint("cb_pages", __name__)


def _provider_status():
    """Whether each provider has a key. Runway included.

    It was not: the dashboard read only V1_PROVIDERS for status and drew
    Runway from V1_5_PROVIDERS as a permanently grey "V1.5" chip. Runway has
    had a working service and a real key check since AI video scenes shipped,
    so that chip said "not connected" to somebody who had just connected it —
    a wrong answer that looks exactly like a right one. The V2 providers below
    genuinely have no service behind them and stay a static label.
    """
    return provider_check.status()


@bp.get("/")
def dashboard():
    clients = Client.query.order_by(Client.name).all()
    projects = CommercialProject.query.order_by(CommercialProject.updated_at.desc()).limit(25).all()
    return render_template(
        "commercial_dashboard.html", clients=clients, projects=projects,
        provider_status=_provider_status(), providers=provider_check.PROVIDERS,
        provider_labels=provider_check.LABELS, v2_providers=V2_PROVIDERS,
    )


@bp.get("/new")
def new_commercial():
    clients = Client.query.order_by(Client.name).all()
    return render_template(
        "commercial_new.html", clients=clients, lengths=COMMERCIAL_LENGTHS,
        formats=OUTPUT_FORMATS, types=COMMERCIAL_TYPES, platforms=PLATFORMS,
    )


@bp.get("/project/<int:project_id>/brief")
def brief(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template("commercial_brief.html", project=project, client=project.client,
                            tones=TONE_OPTIONS)


@bp.get("/project/<int:project_id>/concepts")
def concepts(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    if not project.brief or not project.brief.get("what_advertising"):
        return redirect(url_for("commercial_builder.cb_pages.brief", project_id=project_id))
    return render_template("commercial_concepts.html", project=project, client=project.client)


@bp.get("/project/<int:project_id>/storyboard")
def storyboard(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template(
        "commercial_storyboard.html", project=project, client=project.client,
        music_moods=MUSIC_MOODS, music_levels=list(MUSIC_LEVELS.keys()),
        voice_styles=VOICE_STYLES, cta_styles=CTA_STYLES,
        structure_beats=get_structure(project.length_seconds),
        qr_eligible=qr_eligible(project.length_seconds),
        qr_rules=QR_CODE_RULES, logo_rules=LOGO_PERSISTENCE_RULES,
    )


@bp.get("/project/<int:project_id>/preview")
def preview(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template("commercial_preview.html", project=project, client=project.client,
                            formats=OUTPUT_FORMATS)
