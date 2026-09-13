"""Adapter G: monthly YouTube sales video -> the real Commercial Builder.

`youtube_video_concept` ran as `brief`: AI-written text describing a video
concept nobody had actually drafted. This creates one real
`CommercialProject` through `modules.commercial_builder.client_link.
ensure_client()` and its own model classes -- the same rows
`routes/projects.py`'s `start_commercial()` creates for the Storyboard
Editor -- and generates real concepts through `generation.run_concepts()`,
the identical plain function that route's own "Generate Concepts" button
calls.

**Deliberately stops at concepts.** Picking one, writing the timed script,
casting a voice, choosing music and a CTA, and reviewing every scene is a
person's job -- exactly the reasoning `hub/proposal_adapters/display_ads.py`
gives for stopping at "start a build" rather than picking sizes and
rendering. `approval` mode: a person opens the project's own Concepts
screen and continues the wizard from there.

`generation.run_concepts()` never raises for a missing OpenAI key -- the
whole Commercial Builder is built to degrade to deterministic mock
concepts rather than error, so the tool stays clickable with no live key.
This adapter follows that: a project with real (or, with no key
configured, honestly mock) concepts is still a real, useful artifact for
a person to open, and the result says which it got. It raises `ValueError`
only for what it cannot proceed without -- the primary CTA this task's
own `needs` already requires.
"""
from __future__ import annotations

from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter

MOUNT = "/tools/commercial-builder"


def run(run, task):
    from hub import audit
    from modules.commercial_builder import client_link, generation
    from modules.commercial_builder.db import db
    from modules.commercial_builder.models import Client, CommercialProject
    from modules.commercial_builder.services import openai_service

    inputs = run.inputs() or {}
    primary_cta = str(inputs.get("primary_cta") or "").strip()
    if not primary_cta:
        raise ValueError(
            "The YouTube sales video concept needs the primary call to "
            "action. Answer it under 2 - Shared information needed, then "
            "re-run this task."
        )

    landing_url = str(inputs.get("landing_url") or "").strip()
    channel = task.payload().get("channel") or {}
    title = channel.get("name") or "Monthly YouTube Sales Video"

    client_row = client_link.ensure_client(run.client, landing_url)

    project = CommercialProject(
        client_id=client_row.id,
        title=f"{run.client} — {title}",
        length_seconds=30,
        commercial_type="stock_vo",
        platform="youtube",
        status="draft",
    )
    project.formats = ["16:9"]
    project.brief = {
        "what_advertising": primary_cta,
        "primary_cta": primary_cta,
        "landing_page": landing_url,
    }
    db.session.add(project)
    db.session.commit()

    try:
        audit.log(
            "commercial_builder",
            "cb_commercial_started",
            actor="proposal-execution",
            client=client_row.name,
            detail=f"1 spot started for {client_row.name} on youtube: :30.",
        )
    except Exception:  # noqa: BLE001 -- attribution must never cost the save
        pass

    concepts = generation.run_concepts(project, client_row)

    return {
        "summary": (
            f"Started a monthly YouTube sales video for {run.client} with "
            f"{len(concepts)} concept option(s) to choose between."
        ),
        "project_id": project.id,
        "concepts": len(concepts),
        "live": openai_service.is_live(),
        "artifact_url": f"{MOUNT}/project/{project.id}/concepts",
        "qa": [
            "Pick a concept and confirm it matches the proposal's promise before writing the script.",
            "Confirm no invented offers, prices, dates or claims appear in the concepts.",
        ],
    }


register_adapter(Adapter("commercial_builder", "Monthly Sales Video Concept", "approval", run))
