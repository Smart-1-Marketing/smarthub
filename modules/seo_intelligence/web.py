"""Internal SmartHub SEO overview dashboard."""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("seo_intelligence_web", __name__, url_prefix="/seo/intelligence", template_folder="templates")


@bp.get("/")
def overview():
    return render_template("seo_intelligence/overview.html")
