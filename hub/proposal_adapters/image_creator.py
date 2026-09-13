"""Adapter F: Meta carousel creative -> the real Image Creator.

`meta_carousel` used to come back as `brief`: AI-written text describing
what a Meta carousel ad should look like, with nothing anywhere that had
actually drawn one. This generates one real image through
`hub.ai.image()` -- the same billed OpenAI call
`modules/image_creator/app.py`'s own `/api/ai/image` route makes -- and
saves it as a real Image Creator project through
`modules/image_creator/projects.save_project()`, so the task's artifact is
a project a person can open, review and finish (add the rest of the
carousel's cards, adjust the crop, add text) rather than a paragraph
describing one.

One image per run, never one per `product_destinations` line. A carousel
buy is several cards; generating all of them here would multiply a billed
image call by however many products a client listed, which is the same
unbounded-fan-out shape `hub/webargs.py`'s docstring already names for a
caller-controlled loop. The first destination (if any) seeds the prompt;
the project itself is where a person builds out the rest of the carousel.

Attribution goes through `hub.audit.log()` directly rather than through
`modules.image_creator.app._log()` -- that wrapper reads `flask.g`/
`request.environ` for the acting user and silently drops the entry when
called with no request in flight, exactly the `flask.g` trap this
codebase already paid for once in Google Finder. `save_project()` itself
takes `actor` as a plain argument and needs no such fix.
"""
from __future__ import annotations

from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter


def run(run, task):
    from hub import ai as hub_ai
    from hub import audit
    from hub.client_key import name_slug
    from modules.image_creator import projects

    inputs = run.inputs() or {}
    primary_cta = str(inputs.get("primary_cta") or "").strip()
    if not primary_cta:
        raise ValueError(
            "Meta carousel creative needs the primary call to action. "
            "Answer it under 2 - Shared information needed, then re-run this task."
        )

    destinations = [
        line.strip()
        for line in str(inputs.get("product_destinations") or "").splitlines()
        if line.strip()
    ]

    channel = task.payload().get("channel") or {}
    campaign_name = channel.get("name") or "Meta Carousel"

    prompt = f"Social ad creative for {run.client}, promoting: {primary_cta}."
    if destinations:
        prompt += f" Featuring: {destinations[0]}."
    prompt += (
        " Photorealistic, bright and inviting, no text overlay, "
        "no invented logos or brand marks."
    )

    try:
        raw = hub_ai.image(
            prompt[:3800],
            module="image_creator",
            purpose="proposal_execution",
            size="1024x1024",
            client=run.client,
        )
    except hub_ai.AIUnavailable as exc:
        raise ValueError(f"Could not generate creative: {exc}") from exc

    import base64

    data_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    canvas = {
        "width": 1024,
        "height": 1024,
        "background": "#ffffff",
        "objects": [
            {
                "type": "image",
                "src": data_url,
                "left": 0,
                "top": 0,
                "width": 1024,
                "height": 1024,
                "scaleX": 1,
                "scaleY": 1,
            }
        ],
    }

    record = projects.save_project(
        name=f"{run.client} — {campaign_name}",
        canvas=canvas,
        preview=data_url,
        client=run.client,
        client_slug=name_slug(run.client),
        tags="proposal-execution,meta-carousel",
        width=1024,
        height=1024,
        actor="proposal-execution",
    )

    try:
        audit.log(
            "image_creator",
            "project_saved",
            actor="proposal-execution",
            detail=record["name"],
            client=run.client,
            project=record["id"],
        )
    except Exception:  # noqa: BLE001 -- attribution must never cost the save
        pass

    return {
        "summary": (
            f"Generated a real image and saved a Meta carousel creative "
            f"project for {run.client}."
        ),
        "project_id": record["id"],
        "artifact_url": f"/tools/image-creator/?project={quote(record['id'])}",
        "qa": [
            "Review the generated image and add every carousel card this buy needs before approving.",
            "Confirm no invented claims, logos or text appear in the generated image.",
        ],
    }


register_adapter(Adapter("image_creator", "Meta Carousel Creative", "approval", run))
