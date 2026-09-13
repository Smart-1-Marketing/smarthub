"""What a campaign IS, read by the routes, the templates and the batch
render gate alike -- WO-CS8. The same split `hub/proposal_spec.py` and
`modules.commercial_builder.review_spec` already use: data and pure
functions here, Flask in `api.py`, so a rule read by three places (the
Campaigns screen, the client review page, a test) cannot drift into three
answers.
"""
from __future__ import annotations

from . import config

# A campaign is only as far along as its least-advanced asset -- the same
# "most restrictive wins" reading `modules.commercial_builder.review_spec
# .verdict()` applies to several reviewers answering one share. Ordered
# worst (least done, or needing attention) first; the first status any
# asset carries is the campaign's own.
_PRECEDENCE = ("Changes Requested", "Draft", "Internal Review", "Client Review", "Approved")


def status_of(asset_statuses: list[str]) -> str:
    """One label for the whole campaign, derived from its assets' own
    statuses -- never stored (CLAUDE.md's rule for a fact computed from
    other rows). "Empty" is a campaign with nothing on it yet, which is
    not the same as "Draft": a rep who has created the campaign row and
    added nothing is still deciding what belongs on it."""
    statuses = set(s for s in asset_statuses if s)
    if not statuses:
        return "Empty"
    for label in _PRECEDENCE:
        if label in statuses:
            return label
    return "Draft"


def asset_differs(project_brief: dict, campaign_offer: str, campaign_cta: str) -> dict:
    """Which of a campaign asset's own offer/CTA depart from the campaign's
    -- the "differs from campaign" chip, WO-CS8 item 2. Only a project whose
    OWN brief explicitly names a value counts: a blank brief field resolves
    through the Brand Kit / template default at render time and is not a
    departure from anything, the same reading `resolver.resolve()` already
    gives an unset override."""
    out = {"offer": False, "cta": False}
    own_offer = str((project_brief or {}).get("offer") or "").strip()
    if own_offer and campaign_offer and own_offer != str(campaign_offer).strip():
        out["offer"] = True
    own_cta = str((project_brief or {}).get("cta") or "").strip()
    if own_cta and campaign_cta and own_cta != str(campaign_cta).strip():
        out["cta"] = True
    return out


def render_estimate(asset_count: int) -> float:
    """The batch render button's own estimate -- one `("creatomate",
    "render")` unit per asset, `config.PROVIDER_RATES`'s own number rather
    than a second one typed here. A rate not yet priced (`None`) reads as
    "not measured" rather than zero, the `hub/quotas.py` rule this module's
    own `config.PROVIDER_RATES` docstring already states."""
    rate = config.PROVIDER_RATES.get(("creatomate", "render"))
    if rate is None:
        return 0.0
    return round(rate * max(0, int(asset_count)), 2)


def needs_confirmation(estimate_usd: float) -> bool:
    return estimate_usd > config.batch_confirm_threshold_usd()


def migrate_cb_campaigns(actor: str = "system") -> int:
    """Bring in every `modules.commercial_builder.models.Campaign` row that
    has at least one project Creative Studio has bound (`cb_project_id`),
    and has not already been migrated -- WO-CS8 item 1's own words. One
    legacy campaign failing to migrate must not cost the others in the same
    boot: each is its own transaction, caught and rolled back on its own,
    the "one asset failing never cancels the rest" rule one level up from
    where the work order states it.

    Idempotent on `cb_campaign_id`: a campaign already migrated is skipped,
    so running this on every boot (the way `seed()` already runs) costs one
    query per CB campaign and nothing once they are all in.
    """
    from .db import db
    from .models import CsCampaign, CsCampaignAsset, CsProject

    try:
        from modules.commercial_builder.models import Campaign as CbCampaign
    except Exception:                                       # noqa: BLE001
        return 0

    try:
        already_migrated = {row.cb_campaign_id for row in
                            CsCampaign.query.filter(CsCampaign.cb_campaign_id.isnot(None)).all()}
        # A project already on SOME cs_campaign -- native or migrated in
        # earlier -- must not be claimed a second time: `CsCampaignAsset
        # .project_id` is unique, because a project asked to be two
        # channels' asset at once has no single answer to "does this
        # differ from the campaign".
        already_claimed = {row.project_id for row in CsCampaignAsset.query.all()}
        cb_campaigns = CbCampaign.query.all()
    except Exception:                                       # noqa: BLE001
        return 0

    migrated = 0
    for cb_campaign in cb_campaigns:
        if cb_campaign.id in already_migrated:
            continue
        try:
            cb_project_ids = [p.id for p in cb_campaign.projects.all()]
            if not cb_project_ids:
                continue
            cs_projects = [p for p in CsProject.query.filter(
                CsProject.cb_project_id.in_(cb_project_ids)).all()
                if p.id not in already_claimed]
            if not cs_projects:
                # A CB campaign nothing here has ever bound to (or every
                # project it bound is already claimed elsewhere) -- not
                # this module's to adopt: migrating it would give a
                # campaign row to projects a rep never opened here, or
                # take a project off the campaign it is already on.
                continue
            row = CsCampaign(
                client_name=cs_projects[0].client_name or "",
                name=cb_campaign.name or f"Campaign #{cb_campaign.id}",
                offer="", cta="", cb_campaign_id=cb_campaign.id, created_by=actor)
            db.session.add(row)
            db.session.flush()
            for project in cs_projects:
                db.session.add(CsCampaignAsset(campaign_id=row.id, project_id=project.id))
                already_claimed.add(project.id)
            db.session.commit()
            migrated += 1
        except Exception:                                   # noqa: BLE001
            db.session.rollback()
    return migrated
