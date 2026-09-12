"""Adapter: ``paid_search_ads`` -> a real Google Ads search campaign, built by
the same generator ``/tools/ads`` uses, filed on the client's own record.

The work order's own pointer for this adapter (``hub/ad_copy.py``) turned out
to name the wrong module: that file is the Knack *Ad Copy Request* ticket
form -- it files a request asking a person to write ad copy, which is the
``brief`` adapter's own failure wearing a different form. The real RSA
generator, with real headlines, descriptions, keywords and a negative
keyword vault, is ``modules/ads_builder``. This adapter calls it.

Deliberately **not** the whole of ``/api/generate``'s side effects. That route
also decides whether the business is a *new* client and, if so, writes a lead
into Smart 1 Suite (``client_link.create_lead``). A Proposal Execution run
only exists because ``create_run()`` was handed a real, already-known client
and a real proposal on that client's own record -- so this adapter always
calls ``client_link.attach(..., is_new_client=False)``. Treating an existing
client as new here would write a duplicate lead for a business we already
have, which is exactly the mistake ``client_link.py``'s own docstring spends
a section warning against for the human-driven path.

``execution_mode="approval"`` -- like ``radio_scripts`` -- because this is AI
copy a person has to read before it reaches a real ad platform, never
something that completes itself.
"""
from __future__ import annotations

from hub.proposal_execution import Adapter, register_adapter


def _channel_budget(channel: dict) -> float:
    """A monthly figure from the proposal's own budgets, or none.

    ``analysis()["channels"][*]["budgets"]`` is free-text money the model read
    off the proposal document -- often a flight total, sometimes a monthly
    figure, sometimes empty. Guessing which is which invents a number nobody
    quoted, so only a value that already parses as a plain monthly figure is
    used; anything else is left for the generator's own no-budget path, which
    sizes tiers instead of pretending to have one.
    """
    import re

    for raw in channel.get("budgets") or []:
        if not isinstance(raw, str):
            continue
        match = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*(?:/\s*mo(?:nth)?)\b", raw, re.I)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return 0.0


def run(run, task):
    from modules.ads_builder import campaign_ai, client_link, landing_page, store
    from modules.ads_builder.app import MOUNT

    inputs = run.inputs() or {}
    landing_url = str(inputs.get("landing_url") or "").strip()
    if not landing_url:
        raise ValueError(
            "Paid Search ad copy needs the primary landing URL. Answer it "
            "under · 2 · Shared information needed, then re-run this task.")
    primary_cta = str(inputs.get("primary_cta") or "").strip()
    if not primary_cta:
        raise ValueError(
            "Paid Search ad copy needs the primary call to action. Answer it "
            "under · 2 · Shared information needed, then re-run this task.")

    channel = task.payload().get("channel") or {}
    payload = {
        "businessName": run.client,
        "websiteUrl": landing_url,
        "budget": _channel_budget(channel),
        "objective": inputs.get("conversion_goal") or "",
        "targetAudience": "",
        "geography": inputs.get("target_geography") or "",
        "notes": f"Primary call to action: {primary_cta}.",
        "campaignType": "SEARCH",
    }

    observed = landing_page.observe(landing_url)
    campaign = campaign_ai.generate_campaign(payload, observed_page=observed)
    campaign["landingPageObserved"] = observed

    try:
        campaign["budgetTiers"] = campaign_ai.budget_tiers(
            campaign, campaign.get("sectorKey") or "general")
    except campaign_ai.GenerationError as exc:
        campaign["budgetTiers"] = {"tiers": [], "error": str(exc)}

    if not campaign.get("monthlyBudget"):
        recommended = next((t for t in (campaign["budgetTiers"].get("tiers") or [])
                            if t.get("recommended")), None)
        if recommended:
            campaign["monthlyBudget"] = recommended["monthly"]
            campaign["budgetSource"] = {
                "stated": False, "tier": recommended["key"],
                "note": (f"The client has not named a budget. Costed at the "
                         f"{recommended['label']} tier the generator recommends "
                         f"(${recommended['monthly']:,.0f}/month)."),
            }
        else:
            campaign["budgetSource"] = {
                "stated": False, "tier": "",
                "note": "The client has not named a budget and no tier could be sized."}
    else:
        campaign["budgetSource"] = {"stated": True, "tier": "", "note": ""}

    campaign["createdBy"] = "proposal-execution"
    campaign["editLog"] = []
    campaign["clientLink"] = {"is_new_client": False, "picked_from_lookup": True, "contact": {}}

    proposal = store.create_proposal(client_name=run.client, campaign=campaign,
                                     created_by="proposal-execution")

    # client= is what work_log() reads -- hub/client_brand.py lists
    # "ads_builder" in WORK_KINDS and matches on this field.
    work = store.log_event(
        "GENERATION_SUCCESS", "proposal-execution", proposal=proposal["id"], client=run.client,
        detail=(f"Campaign generated via Proposal Execution #{run.id} — "
                f"{proposal['ad_group_count']} ad groups, {proposal['keyword_count']} keywords"),
        ad_groups=proposal["ad_group_count"], keywords=proposal["keyword_count"])

    link = client_link.attach(proposal, MOUNT, is_new_client=False,
                              actor="proposal-execution", work=work)
    proposal = store.record_client_link(proposal["id"], link) or proposal

    return {
        "summary": (f"Built a {proposal['ad_group_count']}-ad-group Google Ads search "
                    f"campaign for {run.client} — {proposal['keyword_count']} keywords, "
                    "filed on the client's own record."),
        "proposal_id": proposal["id"],
        "ad_groups": proposal["ad_group_count"],
        "keywords": proposal["keyword_count"],
        "monthly_budget": campaign.get("monthlyBudget"),
        "artifact_url": f"{MOUNT}/proposal/{proposal['id']}",
        "filed": bool((link or {}).get("filed", {}).get("ok")),
        "qa": ["Read every headline and description before this campaign reaches Google Ads — this is AI-drafted copy, not published copy.",
               "Confirm the keyword list and negative keyword vault match this client's actual business.",
               "Confirm the budget tier matches what was quoted in the proposal."],
    }


register_adapter(Adapter("search_ads", "Paid Search Ad Copy", "approval", run))
