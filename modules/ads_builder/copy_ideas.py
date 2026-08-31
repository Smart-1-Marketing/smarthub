"""The Pickaxe workshop tools, on the proposal screen: ad copy, extensions,
SEM quote help.

Three helpers for the working screen, all internal — nothing any of them
writes reaches the client estimate, and the panel says so, because a
model-written CPC or offer on the document a client reads is the exact thing
this module's provenance rules exist to stop.

Two of the three are Pickaxes ABSORBED into the Hub (hub/prompts_harvested.py
— "Ad Copy Creation" and "Suggest Ad Extension Ideas", near-verbatim from
prompts that produced accepted output for two years). The third, SEM Quote
Help, keeps its knowledge base in Pickaxe — Google Ads benchmarks and a
1,586-chunk responsive search ad report the Hub cannot hold — so it is a LIVE
call through hub/pickaxe.py, with the Hub's own AI as the fallback: the
button works whether or not Pickaxe is configured, and the answer says which
source wrote it, because a benchmark-backed suggestion and a model's general
knowledge are different confidences and only the screen can say which the rep
is reading.

Prefills follow the package's own rule: every placeholder the Hub can answer
is answered from the campaign, and one it cannot is labeled "not provided"
with an instruction not to invent — never left for the model to guess at.
"""
from __future__ import annotations

from hub import target_areas

from .campaign_ai import GenerationError


def _hub_ai():
    try:
        from hub import ai
        return ai
    except Exception as exc:                            # noqa: BLE001
        raise GenerationError(
            "The Hub's AI client is not available in this deployment.") from exc


def _chat_text(prompt: str, *, purpose: str, temperature: float) -> str:
    ai = _hub_ai()
    try:
        text = ai.chat([{"role": "user", "content": prompt}],
                       module="ads", purpose=purpose, temperature=temperature)
    except ai.AIUnavailable as exc:
        raise GenerationError(str(exc)) from exc
    if not (text or "").strip():
        raise GenerationError("The model returned nothing.")
    return text.strip()


def _given(value, absent: str) -> str:
    """A campaign value, or an honest absence the model cannot mistake for
    an answer. "not provided" plus what not to do about it, never a blank a
    model fills in with something plausible."""
    v = str(value or "").strip()
    return v if v else absent


_NO_INVENTED_CLAIMS = ("not provided — do not invent specific claims, awards, "
                       "prices or offers; keep claims generic to the industry")


def prefill_ad_copy(campaign: dict) -> dict:
    intake = campaign.get("intake") or {}
    return {
        "client": _given(campaign.get("businessName"), "not provided"),
        "landing_page": _given(campaign.get("websiteUrl"), "not provided"),
        "industry": _given(campaign.get("sector"), "not provided"),
        "objective": _given(campaign.get("objective"), "not provided"),
        "audience": _given(campaign.get("targetAudience")
                           or intake.get("audienceType"),
                           "not provided — write for the general local buyer"),
        "products": _given(intake.get("productOrService"), "not provided"),
        # The one USP-shaped fact the intake actually captures. Tri-state on
        # purpose: an unanswered question is not "no", and neither is an
        # invitation to invent one.
        "usp": _given("Locally owned and operated"
                      if intake.get("locallyOwned") else "",
                      _NO_INVENTED_CLAIMS),
        "cta": _given(intake.get("promotion"),
                      "not provided — suggest one per variation, without "
                      "inventing a specific offer, price or deadline"),
    }


def ad_copy_ideas(campaign: dict) -> dict:
    from hub.prompts_harvested import AD_COPY
    text = _chat_text(AD_COPY["prompt"].format(**prefill_ad_copy(campaign)),
                      purpose=AD_COPY["purpose"],
                      temperature=AD_COPY["temperature"])
    return {"text": text,
            "source_note": "Written by the Hub's own AI from the campaign's "
                           "answers. Internal working notes — nothing here "
                           "reaches the client estimate."}


def extension_ideas(campaign: dict, observed: dict) -> dict:
    """Extension suggestions read off the fetched page, never off a guess.

    The original Pickaxe told the model to open the URL, which a model cannot
    do — it had been reviewing its own guess about the page. The fetched text
    is the analysis input here, so a page that could not be read is a refusal
    with the reason, not an analysis of nothing.
    """
    from hub.prompts_harvested import AD_EXTENSIONS
    observed = observed or {}
    if not observed.get("measured"):
        raise GenerationError(
            "The landing page could not be read, so no page text could be "
            "retrieved — and extension ideas invented without the page are "
            "worse than none. "
            + (observed.get("error") or "No landing page URL on this campaign."))
    page_text = (observed.get("text") or "").strip()
    if not page_text:
        raise GenerationError(
            "The page was fetched but no readable text could be retrieved "
            "from it, so there is nothing to base extension ideas on.")
    text = _chat_text(
        AD_EXTENSIONS["prompt"].format(
            client=_given(campaign.get("businessName"), "not provided"),
            landing_page=_given(campaign.get("websiteUrl"), "not provided"),
            industry=_given(campaign.get("sector"), "not provided"),
            page_text=page_text),
        purpose=AD_EXTENSIONS["purpose"],
        temperature=AD_EXTENSIONS["temperature"])
    return {"text": text,
            "source_note": "Read from the landing page's fetched copy — "
                           "wording only, not a rendered view of the page."}


# The fallback deliberately forbids cost figures. This module already carries
# the sector benchmark with its provenance (spec.CPC_SOURCES), and a
# model-invented CPC beside it would be a fourth kind of number with no
# provenance at all. The live Pickaxe is different: its figures come off its
# own benchmarks knowledge base, and the panel names it as the source.
_SEM_FALLBACK_PROMPT = (
    "You are a Google Ads planner for Smart 1 Marketing.\n"
    "Company: {company}\n"
    "Website: {website}\n"
    "What the company does: {does}\n"
    "Campaign focus: {focus}\n"
    "Monthly budget: {budget}\n"
    "Geography: {geo}\n\n"
    "Suggest keyword themes with match-type guidance, negative keywords worth "
    "adding, ad angles worth testing, and how you would structure the account "
    "for this business and geography. Do NOT state cost-per-click or cost "
    "figures — the estimate already carries a sector benchmark with its "
    "provenance, and a number from you would have none. Do not invent offers, "
    "prices or claims about the company."
)


def sem_prefill(campaign: dict) -> dict:
    intake = campaign.get("intake") or {}
    budget = campaign.get("monthlyBudget") or 0
    geo = (campaign.get("geography")
           or target_areas.summary(campaign.get("targetAreas") or []))
    return {
        "company": _given(campaign.get("businessName"), "not provided"),
        "website": _given(campaign.get("websiteUrl"), "not provided"),
        "does": _given(intake.get("productOrService")
                       or campaign.get("sector"), "not provided"),
        "focus": _given(campaign.get("objective"), "not provided"),
        "budget": f"${budget:,.0f}/mo" if budget else "not provided",
        "geo": _given(geo, "not provided"),
    }


def sem_quote(campaign: dict, *, user: str = "") -> dict:
    """SEM Quote Help — the Pickaxe if it is configured, the Hub's AI if not.

    The two sources are different confidences and the answer says which:
    the Pickaxe's suggestions come off its Google Ads benchmarks and RSA
    report, the fallback is the model's general knowledge with cost figures
    forbidden. A rep reading one as the other is the confusion the label
    exists to stop.
    """
    fields = sem_prefill(campaign)
    try:
        from hub import pickaxe
        from hub.pickaxe_registry import SEM_QUOTE_HELP, fill
        text = pickaxe.ask(
            SEM_QUOTE_HELP["pickaxe_id"], module="ads", purpose="sem_quote",
            workspace_id=SEM_QUOTE_HELP["workspace_id"],
            user_id=user or None,
            inputs=fill(SEM_QUOTE_HELP, **fields))
        return {"text": text, "source": "pickaxe",
                "source_note": "Answered by the SEM Quote Help assistant, "
                               "which works from its own Google Ads benchmark "
                               "library. Internal working notes — nothing "
                               "here reaches the client estimate."}
    except Exception as exc:                            # noqa: BLE001
        # Not configured, unreachable, or the endpoint shape is wrong —
        # every one of those costs the Pickaxe answer and never the button.
        reason = str(exc) if type(exc).__name__ == "PickaxeUnavailable" else \
            "Pickaxe could not be reached."
    text = _chat_text(_SEM_FALLBACK_PROMPT.format(**fields),
                      purpose="sem_quote_fallback", temperature=0.4)
    return {"text": text, "source": "hub_ai",
            "source_note": f"Written by the Hub's own AI ({reason}) — general "
                           "knowledge, no benchmark library behind it, so it "
                           "deliberately quotes no cost figures. Internal "
                           "working notes — nothing here reaches the client "
                           "estimate."}
