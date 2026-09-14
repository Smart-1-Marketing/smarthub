"""Publication history, local technical QA and measured factory reporting."""
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import uuid

from flask import current_app, request, render_template
from hub.extensions import db


class Publication(db.Model):
    __tablename__ = "hub_industry_publications"
    id = db.Column(db.String(32), primary_key=True)
    active_id = db.Column(db.String(32), nullable=False, default="")


class PageView(db.Model):
    __tablename__ = "hub_industry_views"
    id = db.Column(db.String(32), primary_key=True)
    created = db.Column(db.DateTime, nullable=False, index=True)
    page_id = db.Column(db.String(32), nullable=False, index=True)
    attribution = db.Column(db.Text, nullable=False)


class Qualification(db.Model):
    __tablename__ = "hub_industry_qualifications"
    lead_id = db.Column(db.String(100), primary_key=True)
    qualified = db.Column(db.Boolean, nullable=False)
    actor = db.Column(db.String(120), nullable=False)
    updated = db.Column(db.DateTime, nullable=False)


def root_id(page):
    return json.loads(page.config_json).get("publication_id") or page.id


def live_page(page):
    from hub.industry_factory import IndustryPage
    publication = db.session.get(Publication, root_id(page))
    if publication is None:
        return page if page.status == "published" else None
    active = db.session.get(IndustryPage, publication.active_id) if publication.active_id else None
    return active if active and active.status == "published" else None


def activate(page):
    """Caller holds the root page lock, serializing revision and publish operations."""
    from hub.industry_factory import IndustryPage
    rid = root_id(page)
    publication = db.session.get(Publication, rid)
    if publication is None:
        publication = Publication(id=rid, active_id="")
        db.session.add(publication)
    # Legacy published roots predate the pointer table.
    previous = db.session.get(IndustryPage, publication.active_id or rid)
    if previous and previous.id != page.id and previous.status == "published":
        previous.status = "archived"
    publication.active_id = page.id
    page.status = "published"


def technical_qa(data):
    from hub import industry_config, ghl_contacts, leads
    from hub.embed import frame_ancestors
    checks = []
    def check(key, label, ok, detail, blocking=True):
        checks.append(dict(id=key, label=label, state="pass" if ok else "fail" if blocking else "warning", detail=detail, blocking=blocking))
    try:
        industry_config.selection(data["config"])
        industry_config.triggers(data["pack"])
        valid = True
    except (ValueError, TypeError, KeyError):
        valid = False
    check("config", "Service, market and weather rules", valid, "Selections match supported industry and weather catalogs.")
    check("outputs", "Page and planning report", bool(data["artifacts"].get("page") and data["artifacts"].get("report")), "Generate both required outputs before publishing.")
    class Inspect(HTMLParser):
        def __init__(self):
            super().__init__(); self.fields = set(); self.links = []; self.responsive = False
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag in ("input", "textarea") and "required" in attrs:
                self.fields.add(attrs.get("name"))
            if tag in ("script", "link"):
                self.links.append(attrs.get("src") or attrs.get("href") or "")
            if tag == "meta" and attrs.get("name") == "viewport":
                self.responsive = "width=device-width" in attrs.get("content", "")
    inspector = Inspect()
    try:
        inspector.feed(render_template("industry_page.html", page=data, preview=True, widget=False))
        check("fields", "Required lead fields", set(data["pack"]["widget"]["required"]) <= inspector.fields, "Rendered form includes the pack's required fields.")
        assets = [url for url in inspector.links if url]
        check("assets", "Page assets and responsive markup", inspector.responsive and bool(assets) and all(url.startswith("/assets/") and (Path(__file__).parent / "static" / url.removeprefix("/assets/")).is_file() for url in assets), "Styles and scripts exist; mobile viewport is present. Visual review is still required.")
    except (ValueError, TypeError, KeyError):
        check("render", "Page rendering", False, "The page could not be rendered for inspection.")
    endpoints = {r.rule for r in current_app.url_map.iter_rules()}
    check("routes", "Preview, report and widget links", all(p in endpoints for p in ("/sales/industry-factory/preview/<page_id>", "/industry/p/<page_id>/report", "/industry/widget/<page_id>/embed.js")), "Destinations are registered in the application.")
    check("capture", "Central lead capture endpoint", "/api/leads/capture" in endpoints, "The form posts to the existing central lead system.")
    # No external API request or test contact is made by automatic QA.
    mode = leads.delivery_mode()
    try:
        configured = bool(ghl_contacts.token() and ghl_contacts.location_id())
    except ghl_contacts.NotConfigured:
        configured = False
    check("lead_route", "GHL delivery configuration", configured and mode == "api", "Configuration only, not a live delivery test. Review Leads → delivery readiness for provider access.", False)
    mapped = all(os.environ.get("GHL_INDUSTRY_" + key + "_FIELD_ID") for key in ("INDUSTRY_ID", "PAGE_ID", "PAGE_VERSION", "MARKET", "UTM_CAMPAIGN"))
    check("mapping", "Core GHL attribution fields", mapped, "Unmapped attribution stays in Hub lead records.", False)
    check("workflow", "Roofing follow-up workflow", bool(data["pack"]["widget"].get("workflow")), "A workflow identifier does not verify enrollment. Confirm follow-up in GHL.", False)
    ancestors = frame_ancestors()
    check("embed", "Website embed policy", "https://smart1marketing.com" in ancestors or "https://*.smart1marketing.com" in ancestors, "Allowed hosts: " + ancestors, False)
    return dict(checks=checks, ready=not any(c["state"] == "fail" for c in checks))


def record_view(page):
    """Count served public page/widget views, not unique visitors or staff previews."""
    attribution = {key: str(request.args.get(key, ""))[:200] for key in ("utm_source", "utm_medium", "utm_campaign")}
    db.session.add(PageView(id=uuid.uuid4().hex, created=datetime.now(timezone.utc).replace(tzinfo=None), page_id=page.id, attribution=json.dumps(attribution)))
    db.session.commit()


def report_metrics(root):
    from hub.industry_factory import IndustryPage
    from hub import leads
    pages = [p for p in IndustryPage.query.all() if root_id(p) == root_id(root)]
    ids = {p.id for p in pages}
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    views = PageView.query.filter(PageView.page_id.in_(ids), PageView.created >= cutoff.replace(tzinfo=None)).all()
    # Read the authoritative lead store without its panel's 500-row display limit.
    rows = []
    for row in leads._read_all(strict=True):
        if row.get("merged_into") or row.get("source") != "landing" or (row.get("meta") or {}).get("page_id") not in ids:
            continue
        try:
            created = datetime.fromisoformat(row["created"]).replace(tzinfo=timezone.utc)
        except (KeyError, ValueError, TypeError):
            continue
        if created >= cutoff:
            rows.append(row)
    qualified = {q.lead_id for q in Qualification.query.filter_by(qualified=True).all()}
    groups = {}
    def group(pid, meta):
        key = (pid, str(meta.get("utm_source") or "direct / unknown"), str(meta.get("utm_medium") or "—"), str(meta.get("utm_campaign") or "unattributed"))
        if key not in groups:
            groups[key] = dict(page_id=pid, source=key[1], medium=key[2], campaign=key[3], views=0, leads=0, qualified=0)
        return groups[key]
    for view in views:
        group(view.page_id, json.loads(view.attribution))["views"] += 1
    for row in rows:
        item = group(row["meta"]["page_id"], row["meta"])
        item["leads"] += 1
        item["qualified"] += row["id"] in qualified
    return dict(days=30, views=len(views), leads=len(rows), qualified=sum(r["id"] in qualified for r in rows), groups=list(groups.values()),
                recent_leads=[dict(id=r["id"], page_id=r["meta"]["page_id"], created=r["created"], company=(r.get("fields") or {}).get("company", "Lead"), qualified=r["id"] in qualified) for r in sorted(rows, key=lambda r: r["created"], reverse=True)[:100]],
                note="Last 30 days. Views count served pages and widgets, including reloads and bots; they are not unique people. Tracking starts with this release. Qualification is recorded by staff here, not synced from GHL.")
