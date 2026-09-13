"""Adapter: ``tracking_plan`` -> real tagged links, saved in the UTM Builder's
own book.

Every activation task on the graph depends on `tracking_plan`, and the
`brief` adapter it used to run under produced a paragraph describing what
a tracking plan should contain rather than a URL anybody could paste into
an ad. This calls `modules.utm_builder.app.save_batch()` -- the same
function the tool's own "Save" button calls -- so what lands here is a real
row in the same book `/tools/utm` reads, found by the same search, subject
to the same 8,000-row cap.

One link per channel this proposal actually sells, not per task: several
tasks (`stadium_audio_scripts`, `stadium_banners`, `stadium_activation`)
share one tracking destination, and building three near-identical links
for one media buy is exactly the "campaign split three ways in Analytics"
failure `modules/utm_builder/app.py`'s own docstring opens with.

Deliberately only the five channels the work order names -- stadium, meta,
YouTube ads, paid search and retargeting/display -- because those are the
five with a real destination and a real UTM source in
`DEFAULT_VOCAB["utm_source"]`. SEO, organic social, the monthly YouTube
sales video and an AI-advertising channel with no platform chosen yet are
not ad placements with a tagged link to build; a channel that is not one of
the five is silently left out rather than guessed at with an invented
source.
"""
from __future__ import annotations

from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter

# key in hub.proposal_execution's own CHANNEL_PATTERNS -> (utm_source,
# utm_medium), spelled the way modules.utm_builder.app.DEFAULT_VOCAB
# already spells them. Never a source of our own: "facebook" here and
# "meta" on a link built by hand in the tool is the same campaign read as
# two in Analytics.
CHANNEL_SOURCES = {
    "stadium_audio": ("stadium", "audio"),
    "meta": ("facebook", "paid-social"),
    "youtube_ads": ("youtube", "video"),
    "paid_search": ("google", "cpc"),
    "retargeting": ("google", "display"),
}


def run(run, task):
    from modules.utm_builder import app as utm_app
    from hub.client_key import name_slug

    landing_url = str((run.inputs() or {}).get("landing_url") or "").strip()
    if not landing_url:
        raise ValueError(
            "Tracking needs the primary landing URL. Answer it under "
            "· 2 · Shared information needed, then re-run this task.")
    try:
        utm_app.clean_base_url(landing_url)
    except ValueError as exc:
        raise ValueError(
            f"The landing URL on file ({landing_url!r}) is not a usable "
            f"URL: {exc}") from exc

    channels = {c.get("key") for c in (run.analysis().get("channels") or [])
                if isinstance(c, dict)}
    wanted = [key for key in CHANNEL_SOURCES if key in channels]
    if not wanted:
        # Not a missing input somebody can answer -- the proposal's own
        # channels decide this, and retrying it three times and then
        # marking it FAILED (run_one()'s only two outcomes for an
        # exception) would read as a defect rather than as the true
        # answer: this run's mix has nothing that needs a tagged link.
        return {"summary": (f"{run.client}'s proposal has no Stadium, Meta, "
                            "YouTube Ads, Paid Search or Retargeting line, "
                            "so there is no tracked link to build here."),
                "links": [], "saved": 0, "already_saved": 0}

    campaign = utm_app.normalise(run.proposal_title or run.client)
    client_slug = name_slug(run.client)
    existing_urls = {r.get("url") for r in utm_app.load_links()}

    to_build, already_saved = [], []
    for key in wanted:
        source, medium = CHANNEL_SOURCES[key]
        url = utm_app.build_url(landing_url, {
            "utm_source": source, "utm_medium": medium,
            "utm_campaign": campaign, "utm_content": utm_app.normalise(key)})
        row = {"channel": key, "url": url, "utm_source": source, "utm_medium": medium,
               "utm_campaign": campaign, "utm_content": utm_app.normalise(key)}
        (already_saved if url in existing_urls else to_build).append(row)

    result = utm_app.save_batch(
        run.client, product="", label=f"Proposal Execution #{run.id}",
        base_url=landing_url, incoming=to_build, client_slug=client_slug,
        actor="proposal-execution")

    return {
        "summary": (f"Built and saved {len(result['saved'])} tracked link(s) "
                    f"for {run.client} — {len(already_saved)} already "
                    "existed and were left as-is."),
        "campaign": campaign,
        "links": [f"{row['channel']}: {row['url']}" for row in to_build + already_saved],
        "saved": len(result["saved"]), "already_saved": len(already_saved),
        "artifact_url": f"/tools/utm?q={quote(run.client)}",
        "qa": [("Confirm each destination URL is correct before the link goes "
                "into any ad platform."),
               ("Confirm the UTM source/medium spelling matches what Analytics "
                "already reports for this client.")],
    }


register_adapter(Adapter("utm", "UTM Tracking Links", "auto", run))
