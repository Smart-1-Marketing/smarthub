"""The Vox explainer step — a beat list instead of a storyboard.

Every other commercial type in this module is a sequence of scenes each
holding a piece of footage, assembled by Creatomate. A Vox explainer is not:
it is an editorial collage rendered **whole** by `hub/hyperframes.py` from a
beat list, so it takes this one step in place of Concepts, Blueprint, Voice
and CTA.

Four rules here, each of them a way this goes quietly wrong.

**Nothing is rendered by arriving.** Generating the beats is one press and
rendering is another, because a render is minutes of headless Chrome and the
beat list is the only thing anybody can correct before it. Same "approve
before spend" shape every expensive step in this Hub uses.

**An edit is validated exactly as a generation is.** `vox_spec.validate()`
runs over what a person typed as well as over what the model wrote — a rule
the form keeps while the write breaks it is not a rule, and this is the join
where a typed `seconds` of "about 8" reaches a template that will read it as
zero.

**Every write to `project.script` merges.** Assigning a fresh dict over it is
the `set_music` trap this module has already paid for once, where a
voiceover URL written by one panel was wiped by the next save on another.

**The beats live in `script_json`, never in a new column.** `create_all()`
creates missing tables and never adds a column to an existing one, so a
`beats` column would exist on every local SQLite run and be silently absent
on the live Postgres — the trap `hub/client_key.py` states at length.
"""

from flask import Blueprint, jsonify, request

from .. import vox_spec
from ..db import db
from ..models import Client, CommercialProject
from ..services import openai_service
from hub import hyperframes

bp = Blueprint("cb_vox", __name__, url_prefix="/api")


# Writes on this blueprint that deliberately record nothing, with the reason.
# The line this module records on is whether something reaches the client's
# own Cloudinary tree or changes their record; a beat list is a draft of a
# piece nobody has rendered yet, and `submit_render` records the render.
HOUSEKEEPING_ROUTES = {
    "generate_vox_beats": "drafts a beat list for somebody to read. Billed as "
                          "an OpenAI call, which `hub/ai.py` records, and "
                          "nothing has been made for the client until it is "
                          "rendered.",
    "save_vox_beats": "edits that draft.",
}


def _is_vox(project):
    return (project.commercial_type or "") == vox_spec.COMMERCIAL_TYPE


def _not_vox():
    return jsonify({"ok": False, "error": (
        "This spot is not a Vox explainer, so it has no beat list — it is "
        "built as a storyboard of scenes.")}), 400


def _state(project):
    """Everything a screen needs about this project's beats, in one shape.

    Composed here rather than in the template so the page cannot come to
    disagree with the route about whether a list is renderable — the same
    reason `preview.js` was made to read the server's own severity rather
    than keeping a set of its own.
    """
    script = project.script or {}
    beats = script.get("beats") or []
    seconds = vox_spec.beats_seconds(beats)
    return {
        "beats": beats,
        "source": script.get("beats_source") or "",
        "seconds": seconds,
        "duration": hyperframes.vox_duration_verdict(seconds if beats else None),
        "enough_beats": len(beats) >= vox_spec.MIN_BEATS,
        "min_beats": vox_spec.MIN_BEATS, "max_beats": vox_spec.MAX_BEATS,
        "treatments": vox_spec.TREATMENTS,
        "source_kinds": vox_spec.SOURCE_KINDS,
        "render_service": hyperframes.check(),
    }


@bp.get("/projects/<int:project_id>/vox")
def read_vox(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    if not _is_vox(project):
        return _not_vox()
    return jsonify({"ok": True, **_state(project)})


@bp.post("/projects/<int:project_id>/vox/beats")
def generate_vox_beats(project_id):
    """Draft the beat list from a topic, a document or a link.

    The one step in this feature that needs a model. What comes back is
    validated against the template's contract before it is stored, because
    the model writes JSON and the template consumes JSON and nothing between
    them otherwise checks that the two agree.
    """
    project = CommercialProject.query.get_or_404(project_id)
    if not _is_vox(project):
        return _not_vox()
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    kind = vox_spec.clean_source_kind(data.get("source_kind"))
    text = str(data.get("source_text") or "").strip()
    link = str(data.get("link") or "").strip()

    result = openai_service.generate_vox_beats(
        kind, text, client.to_dict(),
        title=project.title or client.name,
        total_seconds=project.length_seconds,
        link=link)

    if not result.get("beats"):
        # Nothing usable came back and nothing is stored. A beat list a
        # person cannot read is not a draft, and overwriting a good one with
        # it would lose work to a bad answer from a provider.
        return jsonify({"ok": False, "error": result.get("error")
                        or "Nothing could be built into an explainer from that.",
                        "dropped": result.get("dropped") or []}), 502

    # Merged, never assigned. The `set_music` trap: a fresh dict over
    # `script` would destroy anything else written there.
    script = dict(project.script or {})
    script["beats"] = result["beats"]
    script["beats_source"] = result.get("source") or ""
    script["vox_source"] = {"kind": kind, "link": link, "text": text[:4000]}
    project.script = script
    if project.status == "draft":
        project.status = "scripted"
    db.session.commit()

    return jsonify({"ok": True, "dropped": result.get("dropped") or [],
                    "note": result.get("error") or "", **_state(project)})


@bp.put("/projects/<int:project_id>/vox/beats")
def save_vox_beats(project_id):
    """Keep what somebody edited, held to the same contract.

    Validated rather than trusted for the same reason the generated list is:
    a `seconds` typed as text and a treatment nobody offered both render, and
    neither errors.
    """
    project = CommercialProject.query.get_or_404(project_id)
    if not _is_vox(project):
        return _not_vox()
    data = request.get_json(force=True) or {}

    checked = vox_spec.validate(data.get("beats"),
                                total_seconds=project.length_seconds)
    if not checked["beats"]:
        return jsonify({"ok": False, "error": (
            "That leaves no beats at all. An explainer needs at least "
            f"{vox_spec.MIN_BEATS}."), "dropped": checked["dropped"]}), 400

    script = dict(project.script or {})
    script["beats"] = checked["beats"]
    # An edited list is somebody's, whoever drafted it. Left reading "ai" it
    # would go on crediting the model for a beat a person wrote.
    script["beats_source"] = "edited"
    project.script = script
    db.session.commit()
    return jsonify({"ok": True, "dropped": checked["dropped"], **_state(project)})
