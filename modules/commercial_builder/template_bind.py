"""Building a spot from an external template's own scene list, rather than
through the script pipeline.

Every scene this module has ever built came out of `openai_service` writing
beats and shots against a brief. Creative Studio's WO-CS3 needs a second way
in: a `cs_project` opens the Storyboard Editor with scenes already laid out
from its `cs_template`'s own scenes, at the durations the template author
set, scaled to the length the rep actually chose. No brief, no concept, no
model call -- the template already says what each scene is for.

This is the one place that writes those scenes, because `modules/creative_studio`'s
own model docstring says it "never writes to modules.commercial_builder's
tables, it only remembers which row it handed the work to." Creative Studio
hands this function a template's scenes, already resolved against that
project's Brand Kit and brief (`resolver.resolve()` is its job, not this
module's), and everything from here on is this module's own write.

`asset_meta["layout_key"]` and `asset_meta["layers"]` are what the Storyboard
Editor's layer panel reads and writes back through the ordinary
`PUT /api/projects/<id>/scenes/<id>` route -- a scene built this way is not a
different kind of scene, it is an ordinary `Scene` row that happens to start
with a layout and some resolved text already on it. Sourcing the actual
footage still goes through Find Stock / Generate AI / Use Spokesperson /
Upload / Use Client Asset exactly as it does for a scene the script pipeline
wrote -- this module never fills in `asset_url`.
"""
from __future__ import annotations

from .db import db
from .models import CommercialProject, Scene

try:  # deliverables belong on the client's 360 record
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _cb_log(*_a, **_k):
        return None


def _log(event, client="", detail="", **extra):
    """Never costs the write it describes -- `audit.log()`'s first
    positional is `module`, and `for_module` binds it; CLAUDE.md names the
    trap twice, and both places build the detail string in the caller for
    the reason `submit_render` first paid for: an f-string over an attribute
    that does not exist raises before the swallow can apply to it."""
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


def build_from_template(*, client_id: int, client_name: str, title: str,
                        length_seconds: int, platform: str, formats: list[str],
                        commercial_type: str, scenes: list[dict]) -> CommercialProject:
    """One `CommercialProject` with scenes built from `scenes`, each a dict
    of `{layout_key, default_duration, layers, label, needs_background}`.

    Durations scale proportionally to `length_seconds` -- the same shape
    `routes/scripts._resequence()` already uses for an edited scene's
    duration change, so a template scene's authored timing and a rep's own
    edit are stretched by the identical rule. The last scene absorbs
    whatever rounding is left, so the total is always exactly the chosen
    length rather than a few hundredths short of it.

    Nothing here reads a brief or calls a model: the caller has already
    resolved every `{{variable}}` a scene's layers reference, and this
    function's only job is to write rows.
    """
    project = CommercialProject(
        client_id=client_id, title=(title or "Untitled spot")[:300],
        length_seconds=length_seconds, commercial_type=commercial_type,
        platform=platform, status="draft",
    )
    project.formats = formats or ["16:9"]
    db.session.add(project)
    db.session.flush()

    raw_total = sum(float(s.get("default_duration") or 0) for s in scenes) or 1.0
    scale = float(length_seconds) / raw_total
    cursor = 0.0
    last = len(scenes) - 1
    for i, spec in enumerate(scenes):
        dur = float(spec.get("default_duration") or 0) * scale
        end = float(length_seconds) if i == last else round(cursor + dur, 2)
        start = round(cursor, 2)
        scene = Scene(
            project_id=project.id, order_index=i, start=start, end=end,
            narration="", visual_description=spec.get("label") or "",
            is_cta=(spec.get("layout_key") == "end_card"),
        )
        scene.asset_meta = {
            "layout_key": spec.get("layout_key") or "",
            "layers": spec.get("layers") or {},
            "beat": spec.get("label") or "", "beat_index": i,
            "needs_background": spec.get("needs_background") or None,
        }
        db.session.add(scene)
        cursor = end
    db.session.commit()

    _log("cb_commercial_started", client=client_name or "",
        detail=(f"1 spot started for {title} from a Creative Studio template "
                f"on {platform}: :{length_seconds:02d}."))
    return project


def build_blank(*, client_id: int, client_name: str, title: str,
                length_seconds: int, platform: str, formats: list[str],
                commercial_type: str, brief: dict) -> CommercialProject:
    """One `CommercialProject` with no scenes yet -- the other way a
    `cs_project` can bind (WO-CS4), for a project nobody picked a template
    for. `brief` is `cs_project.brief` passed straight through: both modules'
    briefs are the same free-form vocabulary (`what_advertising`,
    `primary_cta`, `landing_page`, `phone`, `target_audience`, `tone`), so
    there is no mapping to keep in step, only a value handed across a module
    boundary.

    Scenes here come from `modules.commercial_builder.generation.run_script`,
    not from this function -- a project bound this way has nothing to build
    from until a concept is generated and a script written, which is the
    whole reason it takes the `creative_jobs` path rather than the
    synchronous one `build_from_template` above answers to.
    """
    project = CommercialProject(
        client_id=client_id, title=(title or "Untitled spot")[:300],
        length_seconds=length_seconds, commercial_type=commercial_type,
        platform=platform, status="draft",
    )
    project.formats = formats or ["16:9"]
    project.brief = brief or {}
    db.session.add(project)
    db.session.commit()

    _log("cb_commercial_started", client=client_name or "",
        detail=(f"1 spot started for {title} from a Creative Studio brief "
                f"on {platform}: :{length_seconds:02d}."))
    return project
