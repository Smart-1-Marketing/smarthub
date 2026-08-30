"""Routes and Jinja glue for the help / tour / demo layer.

Register once in the Hub factory:

    from hub.help_routes import register_help
    register_help(app)

Then in your base template, once:

    <link rel="stylesheet" href="{{ url_for('help_bp.static_css') }}">
    <script defer src="{{ url_for('help_bp.static_js') }}"></script>
    <body data-screen="seo_images.upload">
    {{ demo_banner()|safe }}

...and anywhere you want a bubble:

    <label>Longest edge {{ help_dot('seo_images.upload.max_edge') }}</label>

The `data-screen` attribute is the only thing that triggers a tour, and the
tour fires once per browser. Add `data-tour-start="seo_images.upload"` to any
button to replay it on demand — put one in the page header so people can find
it again.
"""
from __future__ import annotations

import os

from flask import Blueprint, jsonify, request, send_from_directory
from markupsafe import Markup, escape

from hub import audit, demo, demos, help as help_registry

bp = Blueprint("help_bp", __name__)

_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@bp.route("/hub-help.js")
def static_js():
    return send_from_directory(_STATIC, "hub-help.js", mimetype="application/javascript",
                               max_age=3600)


@bp.route("/hub-demo.js")
def static_demo_js():
    return send_from_directory(_STATIC, "hub-demo.js", mimetype="application/javascript",
                               max_age=3600)


@bp.route("/hub-autofill.js")
def static_autofill_js():
    return send_from_directory(_STATIC, "hub-autofill.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-accordion.js")
def static_accordion_js():
    return send_from_directory(_STATIC, "hub-accordion.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-dates.js")
def static_hub_dates_js():
    """mm-dd-yy for the browser, the counterpart to hub/dates.py.

    Root-level so mounted modules can load it too — the uploads gallery and
    the radio library are not hub pages and were each formatting dates their
    own way."""
    return send_from_directory(_STATIC, "hub-dates.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/ask-analytics.js")
def static_ask_analytics_js():
    """Served from the hub root so mounted modules can use it too.

    DispatcherMiddleware routes by prefix, so a module page under /google can
    still load a root-level script — which is the point: one Ask Analytics
    widget, not one per page that wants it."""
    return send_from_directory(_STATIC, "ask-analytics.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/knack-form.js")
def static_knack_form_js():
    """The controls a form built from a live Knack object draws, shared by
    the web ticket, campaign request and ad copy forms.

    Root-level for the same reason as the scripts around it, and shared for
    the reason CLAUDE.md gives twice over: the next fix to how a connection
    picker or a dropdown is drawn should land once."""
    return send_from_directory(_STATIC, "knack-form.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/campaign-request.js")
def static_campaign_request_js():
    """The Campaign Change / Support / Ad Copy form, shared by Client 360 and
    the dashboard. Root-level for the same reason as the scripts above: one
    copy of the form, reachable from any page that needs it."""
    return send_from_directory(_STATIC, "campaign-request.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/ad-copy.js")
def static_ad_copy_js():
    """The Ad Copy Request form, shared by Client 360 and the dashboard.

    Its own object rather than a Campaign Change Request with a pre-written
    subject: the campaign team's form has fourteen fields and the old one
    asked four questions, with the client, the campaign, the order number
    and the media partner retyped out of the record on the screen behind
    it."""
    return send_from_directory(_STATIC, "ad-copy.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/web-ticket.js")
def static_web_ticket_js():
    """The New Web Ticket / Manage Ticket form, root-level for the same reason
    as campaign-request.js: one copy of the form, reachable from any page that
    raises a ticket."""
    return send_from_directory(_STATIC, "web-ticket.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-cheers.js")
def static_cheers_js():
    """The birthday / work-anniversary popup.

    Root-level for the same reason as the scripts above, though today only
    base.html loads it: the popup belongs on the page somebody lands on after
    signing in, and that is a hub page. Serving it from the root means a
    mounted module can opt in later without a second copy."""
    return send_from_directory(_STATIC, "hub-cheers.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-thinking.js")
def static_thinking_js():
    """The one mark that says something is running.

    Root-level and served here rather than from any module's own static
    folder, for the reason hub-crumbs.js gives: it is loaded by base.html on
    hub pages and injected by HubBar into every mounted module, so a tool
    added next month gets it without being edited. Nine separate copies of a
    border spinner is what it replaces."""
    return send_from_directory(_STATIC, "hub-thinking.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-crumbs.js")
def static_crumbs_js():
    return send_from_directory(_STATIC, "hub-crumbs.js",
                               mimetype="application/javascript", max_age=3600)


@bp.route("/hub-help.css")
def static_css():
    return send_from_directory(_STATIC, "hub-help.css", mimetype="text/css",
                               max_age=3600)


@bp.route("/api/help")
def api_help():
    """Everything the front end needs, in one request."""
    payload = help_registry.as_json()
    payload["tours"] = {s: help_registry.tour(s) for s in help_registry.screens()}
    return jsonify(payload)


@bp.route("/api/help/search")
def api_help_search():
    return jsonify({"results": help_registry.search(request.args.get("q", ""))})


@bp.route("/api/help/tour-event", methods=["POST"])
def api_tour_event():
    """Where people abandon a tour is a UX bug report you'd never otherwise get.

    Written to the activity log, so 'top abandoned tour steps' is a report you
    can build off the same table as everything else.
    """
    body = request.get_json(silent=True) or {}
    audit.log("help", "tour", screen=str(body.get("screen"))[:80],
              step=body.get("step"), action=str(body.get("action"))[:20])
    return ("", 204)


@bp.route("/api/help/coverage")
def api_coverage():
    """Which screens still have no help written. Feeds the audit suite."""
    expected = ["hub.dashboard", "hub.client360", "seo_images.upload",
                "seo_images.review", "seo_images.results", "image_creator.canvas",
                "image_creator.photos", "image_creator.logos", "image_creator.layers",
                "image_creator.export", "bg_remover.upload", "utm.form",
                "scans.new", "scans.table", "seo.schema", "seo.faq", "qa.reports",
                "ads_builder.generator", "ads_builder.proposal",
                "ads_builder.approvals", "ads_builder.campaigns",
                "ads_builder.settings", "ads_builder.activity"]
    return jsonify({"covered": help_registry.screens(),
                    "missing": help_registry.missing_for(expected)})


# ---------------------------------------------------------------------------
# Guided demos — walkthroughs that run inside each tool
# ---------------------------------------------------------------------------

@bp.route("/api/demos")
def api_demos():
    module = request.args.get("module")
    return jsonify({"scenarios": demos.for_module(module) if module else demos.catalogue(),
                    "modules": demos.modules_covered(),
                    "total_minutes": demos.total_minutes()})


@bp.route("/api/demos/<path:key>")
def api_demo(key):
    sc = demos.get(key)
    if not sc:
        return jsonify({"error": "No walkthrough by that name."}), 404
    return jsonify(sc.as_dict())


@bp.route("/api/demos/event", methods=["POST"])
def api_demo_event():
    """Which step people quit on is the tool's usability report."""
    body = request.get_json(silent=True) or {}
    audit.log("demo", "walkthrough", scenario=str(body.get("scenario"))[:80],
              step=body.get("step"), action=str(body.get("action"))[:20])
    return ("", 204)


@bp.route("/api/demos/coverage")
def api_demo_coverage():
    expected = ["seo_images", "image_creator", "bg_remover", "utm_builder",
                "scans", "seo", "hub", "qa", "ads_builder", "sales_builder",
                "sites_admin", "suite_panel", "proposal_builder",
                "image_optimizer", "pdf_optimizer", "google_finder",
                "calculators", "image_picker", "page_image_optimizer",
                "google_access", "fan_radio", "commercial_builder"]
    return jsonify({"covered": demos.modules_covered(),
                    "missing": demos.missing_for(expected),
                    "catalogue": demos.catalogue()})


# ---------------------------------------------------------------------------
# Jinja helpers
# ---------------------------------------------------------------------------

def help_dot(key: str) -> Markup:
    """A bubble placeholder. The JS swaps it for a real one on load.

    If the key doesn't exist the placeholder is removed client-side rather than
    leaving a dead '?' on the page — a help icon that explains nothing is worse
    than no icon.
    """
    return Markup(f'<span data-help="{escape(key)}"></span>')


def help_text(key: str) -> str:
    return help_registry.text(key)


def demo_launcher(module: str = "", key: str = "", label: str = "") -> Markup:
    """Button that starts a walkthrough of the tool you're standing in.

    Put one in each tool's header. With no arguments it starts that module's
    first scenario, read from <body data-module="...">.
    """
    available = demos.for_module(module) if module else []
    if module and not available and not key:
        return Markup("")
    text = label or (f"Walk me through {available[0]['title'].split(' for ')[0].lower()}"
                     if available else "Walk me through this")
    attr = f'data-demo-start="{escape(key)}"' if key else "data-demo-start"
    return Markup(f'<button type="button" class="s1-demo-launch" {attr}>'
                  f'<span aria-hidden="true">\u25b6</span> {escape(text)}</button>')


def demo_banner_html(user=None) -> Markup:
    data = demo.banner(user)
    if not data:
        return Markup("")
    return Markup(
        '<div class="s1-demo-banner" data-tour="demo-banner" role="status">'
        f'<span class="s1-demo-chip">{escape(data["title"])}</span>'
        f'<span>{escape(data["body"])}</span>'
        f'<a class="s1-demo-cta" href="{escape(data["cta_href"])}">{escape(data["cta"])}</a>'
        "</div>")


def install_template_helpers(app) -> None:
    """Make help_dot / demo_launcher available to a module's own Jinja env.

    Every module is a separate Flask app with its own environment, so globals
    registered on the hub app are invisible to them. Putting {{ help_dot(...) }}
    into a module template therefore raised UndefinedError and returned a 500 —
    which is exactly what happened to Site Scans and the SEO Image Pipeline.

    Called from wsgi.py for every mounted module, so a template can use these
    without knowing which app it belongs to.
    """
    app.jinja_env.globals.setdefault("help_dot", help_dot)
    app.jinja_env.globals.setdefault("help_text", help_text)
    app.jinja_env.globals.setdefault("demo_launcher", demo_launcher)
    app.jinja_env.globals.setdefault("demo_banner", lambda *a, **k: Markup(""))
    app.jinja_env.globals.setdefault("hub_sidebar", lambda *a, **k: Markup(""))


def register_help(app, current_user_fn=None) -> None:
    """Mount the blueprint and expose the template helpers."""
    app.register_blueprint(bp)

    def _banner():
        user = None
        if current_user_fn is not None:
            try:
                user = current_user_fn()
            except Exception:               # noqa: BLE001 — never break a page
                user = None
        return demo_banner_html(user)

    app.jinja_env.globals.update(
        help_dot=help_dot,
        help_text=help_text,
        demo_banner=_banner,
        demo_launcher=demo_launcher,
        demo_scenarios=demos.for_module,
        hub_help_screens=help_registry.screens,
    )
