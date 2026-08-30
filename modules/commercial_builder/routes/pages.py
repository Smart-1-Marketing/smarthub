"""Server-rendered pages for the Commercial Builder wizard. Each step is a
thin template that boots vanilla JS talking to the JSON API in the sibling
route modules — no separate front-end build step, matching the rest of
Smart 1 Hub's Flask + Jinja + vanilla-JS tools (Image Creator, UTM Builder).

## Seven steps, not five

The wizard used to end in a step called Storyboard that was four jobs stacked
on one page: the scenes and their footage, the Voice Studio, Music, and the
CTA Builder — roughly a screen and a half of scrolling, with the CTA controls
(which decide whether there is a QR code at all, and therefore whether the
spot has a response mechanism) below every other card on the page. Everything
on it was reachable and most of it was not found.

They are their own steps now, in the order the work is actually done:

    Start → Brief → Concepts → **Blueprint** → **Voice & music** → **CTA** → Preview

Blueprint first because nothing downstream can be decided without it: a voice
is cast against a script, and a CTA card is built to hold whatever the last
scene turned out to be. `/storyboard` still answers — it redirects — because
that URL is in people's history and a wizard step that 404s reads as the tool
being broken.

`STEPS` is the one description of the sequence. Every template draws its
stepper from it rather than carrying its own copy, which is how the old
templates came to disagree about how many steps there were.
"""

from flask import Blueprint, redirect, render_template, url_for

from ..config import (COMMERCIAL_LENGTHS, OUTPUT_FORMATS, COMMERCIAL_TYPES, TONE_OPTIONS,
                       MUSIC_MOODS, MUSIC_LEVELS, VOICE_STYLES, CTA_STYLES,
                       V2_PROVIDERS, PLATFORMS, QR_CODE_RULES, LENGTH_NOTES,
                       LOGO_PERSISTENCE_RULES, SOCIAL_RULES, get_structure,
                       qr_eligible, qr_required, qr_default_on, is_social,
                       length_warning, CTV_PUBLISHERS, SHOT_SIZES, SHOT_ANGLES,
                       SHOT_MOVES)
from ..services import abcd_service
from ..models import Client, CommercialProject
from ..services import provider_check
from .. import library_spec

bp = Blueprint("cb_pages", __name__)


# The wizard, once. `endpoint` is what a stepper links to; a step with no
# endpoint (Start, before a project exists) is drawn without a link rather
# than pointing at a route that would 404 without a project id.
STEPS = [
    {"key": "start", "label": "Start", "endpoint": ""},
    {"key": "brief", "label": "Brief", "endpoint": "brief"},
    {"key": "concepts", "label": "Concepts", "endpoint": "concepts"},
    {"key": "blueprint", "label": "Blueprint", "endpoint": "blueprint"},
    {"key": "voice", "label": "Voice & music", "endpoint": "voice"},
    {"key": "cta", "label": "CTA", "endpoint": "cta"},
    {"key": "preview", "label": "Preview & render", "endpoint": "preview"},
]

# Which step a project's status drops you back into. Read by the dashboard's
# Open button; kept here beside STEPS so the two cannot drift.
STATUS_STEP = {
    "draft": "brief", "brief": "brief", "concepts": "concepts",
    "scripted": "blueprint", "storyboard": "blueprint",
    "voice": "voice", "cta": "cta",
    "qc": "preview", "rendering": "preview", "complete": "preview",
}


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


def _wizard(project, current):
    """The stepper for one project, as data.

    Every step carries its own URL, so a person who has come back to a spot can
    jump to the part they want rather than clicking forward through four
    screens they have already finished.
    """
    steps = []
    seen_current = False
    for step in STEPS:
        state = "done"
        if step["key"] == current:
            state = "active"
            seen_current = True
        elif seen_current:
            state = ""
        steps.append({
            "label": step["label"], "state": state,
            "url": (url_for("commercial_builder.cb_pages." + step["endpoint"],
                            project_id=project.id)
                    if step["endpoint"] and project else ""),
        })
    return steps


@bp.get("/")
def dashboard():
    clients = Client.query.order_by(Client.name).all()
    projects = CommercialProject.query.order_by(CommercialProject.updated_at.desc()).limit(25).all()
    return render_template(
        "commercial_dashboard.html", clients=clients, projects=projects,
        provider_status=_provider_status(), providers=provider_check.PROVIDERS,
        provider_labels=provider_check.LABELS, v2_providers=V2_PROVIDERS,
        status_step=STATUS_STEP,
    )


@bp.get("/new")
def new_commercial():
    clients = Client.query.order_by(Client.name).all()
    return render_template(
        "commercial_new.html", clients=clients, lengths=COMMERCIAL_LENGTHS,
        formats=OUTPUT_FORMATS, types=COMMERCIAL_TYPES, platforms=PLATFORMS,
        length_notes=LENGTH_NOTES, publishers=CTV_PUBLISHERS,
        wizard=[{"label": s["label"], "state": "active" if s["key"] == "start" else "",
                 "url": ""} for s in STEPS],
    )


@bp.get("/project/<int:project_id>/brief")
def brief(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    archetype, source = library_spec.archetype_for(project.brief,
                                                  project.commercial_type or "")
    suggested = library_spec.suggested_archetypes(
        getattr(project.client, "industry", "") or "")
    # Labels rather than keys: the note is read by a person, and a key is not
    # a name. Built here so the browser holds no second copy of the table.
    suggested = dict(suggested,
                     labels=[library_spec.ARCHETYPES[k]["label"]
                             for k in suggested["keys"]])
    # Whatever has already been answered for any archetype's needs, so
    # switching archetype and back does not lose what somebody typed.
    brief = project.brief or {}
    archetype_answers = {k: brief.get(k, "") for k in library_spec.NEED_KEYS
                         if brief.get(k)}
    return render_template("commercial_brief.html", project=project, client=project.client,
                            tones=TONE_OPTIONS, wizard=_wizard(project, "brief"),
                            archetypes=library_spec.ARCHETYPES,
                            archetype=archetype, archetype_source=source,
                            suggested=suggested,
                            archetype_answers=archetype_answers,
                            method=library_spec.PRODUCTION_METHODS[
                                library_spec.production_method(project.commercial_type or "")],
                            needs_by_archetype={
                                k: v["needs"] for k, v in library_spec.ARCHETYPES.items()})


@bp.get("/project/<int:project_id>/concepts")
def concepts(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    if not project.brief or not project.brief.get("what_advertising"):
        return redirect(url_for("commercial_builder.cb_pages.brief", project_id=project_id))
    return render_template("commercial_concepts.html", project=project, client=project.client,
                            wizard=_wizard(project, "concepts"))


@bp.get("/project/<int:project_id>/blueprint")
def blueprint(project_id):
    """The scenes, their footage and their narration — and the checks.

    The checks are here as well as on Preview because Preview was where they
    lived and every one of them is about something on *this* screen: a scene
    with no footage, a clip shorter than the scene it sits in, narration
    outside the word budget. Finding that out on the last step means going
    back, and the Render button re-running the same checks a person had just
    read was the tool asking a question it had already answered.
    """
    project = CommercialProject.query.get_or_404(project_id)
    return render_template(
        "commercial_blueprint.html", project=project, client=project.client,
        structure_beats=get_structure(project.length_seconds, project.platform),
        social=is_social(project.platform), social_rules=SOCIAL_RULES,
        length_note=LENGTH_NOTES.get(project.length_seconds, {}),
        length_warning=length_warning(project.length_seconds),
        shot_sizes=SHOT_SIZES, shot_angles=SHOT_ANGLES, shot_moves=SHOT_MOVES,
        shot_targets=abcd_service.shot_targets(project.length_seconds),
        lift=abcd_service.MEASURED_LIFT,
        wizard=_wizard(project, "blueprint"),
    )


@bp.get("/project/<int:project_id>/storyboard")
def storyboard(project_id):
    """The old address for what is now the Blueprint step.

    Kept because it is in browser history and in at least one saved link. A
    wizard step that 404s reads as the whole tool being broken, which is what
    `/sites/projects/<id>` looked like for however long its BuildError stood.
    """
    return redirect(url_for("commercial_builder.cb_pages.blueprint", project_id=project_id))


@bp.get("/project/<int:project_id>/voice")
def voice(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template(
        "commercial_voice.html", project=project, client=project.client,
        music_moods=MUSIC_MOODS,
        # The dB pair, not just the label. The level picker draws what ducking
        # actually does -- bed level and ducked level -- and it can only draw
        # the real numbers if it is given them.
        music_levels=MUSIC_LEVELS,
        voice_styles=VOICE_STYLES, wizard=_wizard(project, "voice"),
    )


@bp.get("/project/<int:project_id>/cta")
def cta(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template(
        "commercial_cta.html", project=project, client=project.client,
        cta_styles=CTA_STYLES,
        qr_eligible=qr_eligible(project.length_seconds),
        qr_required=qr_required(project.length_seconds, project.platform),
        qr_default_on=qr_default_on(project.length_seconds, project.platform),
        qr_rules=QR_CODE_RULES, logo_rules=LOGO_PERSISTENCE_RULES,
        social=is_social(project.platform),
        wizard=_wizard(project, "cta"),
    )


@bp.get("/project/<int:project_id>/preview")
def preview(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return render_template("commercial_preview.html", project=project, client=project.client,
                            formats=OUTPUT_FORMATS, wizard=_wizard(project, "preview"))
