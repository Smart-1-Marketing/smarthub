"""The brief -> concepts -> script -> per-scene stills pipeline, as plain
functions rather than three Flask view bodies.

WO-CS4's own words: "Reuse the existing generators; return the same
structured JSON they return today." Before this file, that logic lived
entirely inside `routes/projects.py::generate_concepts`/`generate_script`
and `routes/assets.py::generate_ai_footage` -- correct for the Storyboard
Editor's own synchronous buttons, and unreachable from anywhere else. A
`creative_jobs` runner has no `project_id` in a URL and no request to read
one from, so Creative Studio's own job kinds (WO-CS4) needed the identical
mutations available as a function call. Copying the route bodies into
`modules/creative_studio` would be the exact drift CLAUDE.md spends its own
history undoing -- "two readings of one question drift the day either is
edited" -- so the logic moved here instead, and both the synchronous routes
and the async job runners call it. There is one implementation.

Every function here takes model rows, never a project_id or a Flask
`request` -- a job runner has app context but no request context, and a
route that already has the rows loaded should not pay for a second query.
A validation failure raises `ValueError` with the same sentence the route
used to hand back as a 400; a caller with a `jsonify` decides the status
code, a caller with a `CreativeJob` decides the failure message.
"""
from __future__ import annotations

from .config import DEFAULT_SHOT_GRAMMAR, SHOT_NUMBER_STEP, qr_eligible
from .db import db
from .models import Scene
from .services import openai_service


def with_hub_facts(client) -> dict:
    """The adopted brand profile, plus what the rest of the Hub holds.

    The profile is a copy taken at adoption -- fonts, pronunciation, preferred
    voice -- and it is deliberately one-way, so it does not move when the
    client record does. What it never had is the client's live products, the
    industry on their Knack record and what their last site scan read off
    their own pages, and a model writing a :30 for a client of eleven years
    was working from a name, a color and a tagline.

    `hub/client_context.for_prompt()` is the one reader every AI feature in
    the Hub appends, so a fact added there reaches the commercial, the
    campaign generator and the blog writer alike. It carries what is *not* on
    file with it: a gap a model cannot see is a gap it fills in.

    Never raises, and never writes back to the profile -- adopting is a copy,
    and the copy is the one a person edited.
    """
    profile = client.to_dict()
    try:
        from hub.client_context import for_prompt
        known = for_prompt(client.name or "", client.website or "")
        if known:
            profile["hub_record"] = known
    except Exception:                                       # noqa: BLE001
        pass
    return profile


def run_concepts(project, client) -> list[dict]:
    """Three materially different concepts from `project.brief`.

    Raises `ValueError` when there is no brief yet -- generating concepts
    against nothing to advertise is the empty-input failure this line
    refuses rather than sending a model a blank brief and reporting whatever
    it invents as a real answer.
    """
    if not project.brief or not project.brief.get("what_advertising"):
        raise ValueError("Save a commercial brief before generating concepts.")

    concepts = openai_service.generate_concepts(
        project.brief, with_hub_facts(client), project.commercial_type)
    project.concepts = concepts
    project.selected_concept_id = None
    project.status = "concepts"
    db.session.commit()
    return concepts


def run_script(project, client, *, concept_id: str | None = None) -> dict:
    """The timed script for the selected concept, and the Scene rows it
    implies.

    `concept_id` lets a caller with no concept-picker screen (Creative
    Studio's own jobs, in Phase 1) name which of `project.concepts` to use;
    the Storyboard Editor's own button leaves it `None` and relies on
    `project.selected_concept_id`, exactly as it always has. Raises
    `ValueError` when neither names a real concept -- a script has to be
    written from *something*, and picking one silently would be a decision
    a rep never made.
    """
    if concept_id:
        project.selected_concept_id = concept_id
    concept = next((c for c in (project.concepts or [])
                    if c["id"] == project.selected_concept_id), None)
    if not concept:
        raise ValueError("Select a concept before generating a script.")

    qr_enabled = (bool((project.cta or {}).get("qr_enabled")) if project.cta
                  else qr_eligible(project.length_seconds))
    script = openai_service.generate_script(
        concept, project.length_seconds, project.brief, client.to_dict(),
        platform=project.platform, qr_enabled=qr_enabled)
    project.script = script
    project.status = "scripted"

    # (Re)build Scene rows from the script. Regenerating the script replaces
    # unlocked scenes only, so a user's manually-approved footage choices
    # for earlier scenes survive a script tweak.
    existing = {s.order_index: s for s in project.scenes.all()}
    for idx, sc in enumerate(script["scenes"]):
        is_last = idx == len(script["scenes"]) - 1
        scene = existing.get(idx)
        if scene and scene.locked:
            continue
        if not scene:
            scene = Scene(project_id=project.id, order_index=idx)
            db.session.add(scene)
        scene.start = sc["start"]
        scene.end = sc["end"]
        scene.narration = sc["voiceover"]
        scene.visual_description = sc["visual"]
        scene.is_cta = is_last
        meta = scene.asset_meta or {}
        # A Scene row is a SHOT now, not a beat. What holds a beat together is
        # this metadata: every shot in a beat carries the same label and index,
        # and the Blueprint groups on it. Written here rather than inferred
        # later, because the beat is the model's answer and re-deriving it from
        # timings would be guessing at an argument we were told.
        meta["beat"] = sc.get("beat")
        meta["beat_index"] = sc.get("beat_index")
        meta["grammar"] = sc.get("grammar") or dict(DEFAULT_SHOT_GRAMMAR)
        # Numbered in tens, the way a storyboard is, so a shot inserted between
        # 20 and 30 does not renumber the board.
        meta["shot_no"] = (idx + 1) * SHOT_NUMBER_STEP
        scene.asset_meta = meta
        if is_last:
            scene.asset_type = "cta"
    # drop stale scenes beyond the new scene count
    for idx, scene in existing.items():
        if idx >= len(script["scenes"]) and not scene.locked:
            db.session.delete(scene)

    db.session.commit()
    return script


def run_stills(scene, client, *, option_count: int = 2) -> list[dict]:
    """AI-generated still options for one scene's background.

    Never the logo: this hands the model only `scene.visual_description`, so
    there is no path by which a client's own mark reaches an image model for
    recreation -- the logo slot always takes a Brand Kit logo, drawn by the
    Blueprint's own layer panel, and this function has no way to touch it.
    """
    options = openai_service.generate_ai_stills(
        scene.visual_description or "", client.to_dict(), option_count=option_count)
    meta = scene.asset_meta or {}
    meta["ai_options"] = options
    scene.asset_meta = meta
    db.session.commit()
    return options
