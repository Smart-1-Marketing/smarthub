"""Industry factory pages and public widgets, using the shared DB and lead flow."""
import json
import re
import uuid
from flask import Blueprint, abort, jsonify, make_response, render_template, request
from hub.extensions import db
from hub import industry_config as config
from hub.blueprint_guard import install

bp = Blueprint("industry_factory", __name__)
install(bp, public=("/industry/p/", "/industry/widget/"))
QA = {"copy": "Roofing copy, service and market reviewed", "claims": "No unsupported damage, savings or insurance claims", "mobile": "Desktop and mobile preview checked", "lead": "Lead form and follow-up configuration reviewed"}


class IndustryPage(db.Model):
    __tablename__ = "hub_industry_pages"
    id = db.Column(db.String(32), primary_key=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    parent_id = db.Column(db.String(32), default="")
    status = db.Column(db.String(20), default="draft")
    config_json = db.Column(db.Text, nullable=False)
    pack_json = db.Column(db.Text, nullable=False)
    artifacts_json = db.Column(db.Text, default="{}")
    qa_json = db.Column(db.Text, default="[]")
    creative_job_id = db.Column(db.Integer)

    def data(self):
        artifacts = json.loads(self.artifacts_json)
        creative = "not_started"
        if self.creative_job_id:
            from hub.creative_jobs import CreativeJob
            job = db.session.get(CreativeJob, self.creative_job_id)
            creative = job.state if job else "failed"
            if job:
                artifacts["creative"] = job.result()
                artifacts["creative_error"] = job.error
        return dict(id=self.id, version=self.version, parent_id=self.parent_id, status=self.status,
                    config=json.loads(self.config_json), pack=json.loads(self.pack_json), artifacts=artifacts,
                    qa=json.loads(self.qa_json), states={"page": "ready" if artifacts.get("page") else "not_started",
                    "report": "ready" if artifacts.get("report") else "not_started", "creative": creative})


def get_page(page_id):
    page = db.session.get(IndustryPage, page_id)
    if page is None:
        abort(404)
    return page


def metadata(page):
    data = page.data()
    pack, selected = data["pack"], data["config"]
    return dict(selected, industry_family=pack["industry_family"], page_id=page.id,
                page_version=page.version, trigger_profile=pack["trigger_profile"],
                creative_profile=pack["creative"]["id"])


def lead_metadata(page, attribution=None):
    meta = metadata(page)
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "referrer"):
        meta[key] = str((attribution or {}).get(key, ""))[:500]
    meta["tags"] = ["industry-" + meta["industry_id"], "family-" + meta["industry_family"].replace("_", "-"),
                    "market-" + re.sub(r"[^a-z0-9]+", "-", meta["market"].lower()).strip("-"),
                    page.data()["pack"]["widget"]["offer_tag"]]
    return meta


def report_for(data):
    pack, selected = data["pack"], data["config"]
    return dict(title=pack["widget"]["title"], market=selected["market"], radius=selected["radius"],
                service=pack["services"][selected["service"]], kind="planning_recommendations",
                triggers=[r for r in config.triggers(pack) if r["id"] in selected["trigger_ids"]],
                audience=pack["audience"], media=pack["media"], messaging=pack["messaging"],
                weather_note=pack["weather_note"], disclaimer=pack["disclaimer"])


@bp.get("/sales/industry-factory")
def factory():
    return render_template("industry_factory.html", packs=config.catalog(), qa=QA)


@bp.route("/api/industry-factory/pages", methods=["GET", "POST"])
def pages():
    if request.method == "GET":
        return jsonify(pages=[p.data() for p in IndustryPage.query.all()])
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Expected a JSON object"), 400
    try:
        pack, selected = config.selection(body)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    page = IndustryPage(id=uuid.uuid4().hex, config_json=json.dumps(selected), pack_json=json.dumps(pack))
    parent = body.get("parent_id")
    if parent:
        original = get_page(parent)
        page.parent_id, page.version = original.id, original.version + 1
    db.session.add(page)
    db.session.commit()
    return jsonify(page.data()), 201


@bp.post("/api/industry-factory/pages/<page_id>/<action>")
def action(page_id, action):
    page = get_page(page_id)
    if page.status == "published":
        return jsonify(error="Published versions are immutable. Clone to revise."), 409
    data = page.data()
    artifacts = json.loads(page.artifacts_json)
    if action == "generate-page":
        artifacts["page"] = True
    elif action == "generate-report":
        artifacts["report"] = report_for(data)
    elif action == "generate-creative":
        from hub.creative_jobs import enqueue_for_lead
        if data["states"]["creative"] in ("queued", "running", "done"):
            return jsonify(data)
        job = enqueue_for_lead({"id": "industry-page-" + page.id, "source": "landing", "meta": metadata(page)})
        if job is None:
            return jsonify(error="Creative queue unavailable; retry generation."), 503
        page.creative_job_id = job.id
    elif action == "publish":
        body = request.get_json(silent=True) or {}
        checks = body.get("qa") if isinstance(body, dict) else None
        if not isinstance(checks, list) or any(not isinstance(c, str) for c in checks) or set(checks) != set(QA) or not artifacts.get("page") or not artifacts.get("report"):
            return jsonify(error="Generate the page and report and complete every QA check before publishing."), 400
        page.qa_json = json.dumps(checks)
        page.status = "published"
    else:
        abort(404)
    page.artifacts_json = json.dumps(artifacts)
    db.session.commit()
    return jsonify(page.data())


def render_public(page, widget=False, preview=False):
    from hub.embed import framable, with_reporter
    data = page.data()
    if not data["artifacts"].get("page"):
        abort(404)
    html = render_template("industry_page.html", page=data, widget=widget, preview=preview)
    response = make_response(with_reporter(html.encode("utf-8")))
    response.mimetype = "text/html"
    response.headers["Cache-Control"] = "no-store"
    if preview or widget:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return framable(response)


@bp.get("/sales/industry-factory/preview/<page_id>")
def preview(page_id):
    return render_public(get_page(page_id), preview=True)


@bp.get("/industry/p/<page_id>")
@bp.get("/industry/widget/<page_id>")
@bp.get("/industry/widget/<page_id>/embed")
def public(page_id):
    page = get_page(page_id)
    if page.status != "published":
        abort(404)
    return render_public(page, widget=request.path.startswith("/industry/widget/"))


@bp.get("/industry/widget/<page_id>/embed.js")
def widget_loader(page_id):
    from hub.embed import loader_js
    page = get_page(page_id)
    if page.status != "published":
        abort(404)
    response = make_response(loader_js(page.data()["pack"]["widget"]["title"], 920, forward_attribution=True))
    response.mimetype = "application/javascript"
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/industry/p/<page_id>/report")
def report(page_id):
    page = get_page(page_id)
    if page.status != "published":
        abort(404)
    return render_template("industry_report.html", report=page.data()["artifacts"]["report"])


def resolve_capture(body):
    """Canonical metadata is resolved on the server, never trusted from a widget."""
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    page = db.session.get(IndustryPage, str(meta.get("page_id", "")))
    if page is None or page.status != "published":
        raise ValueError("This industry page is not published")
    fields = body.get("fields") or {}
    pack = page.data()["pack"]
    if any(not str(fields.get(k, "")).strip() for k in pack["widget"]["required"]):
        raise ValueError("Business, name and email are required")
    meta = lead_metadata(page, meta)
    meta["report_url"] = request.url_root.rstrip("/") + "/industry/p/" + page.id + "/report"
    body.update(source="landing", page="industry-" + page.id, meta=meta)
    return body
