"""The tags a Hub lead carries into Smart 1 Suite, written down once.

Every lead the Hub delivers is tagged by `hub/ghl_contacts.payload_for()`
with three strings: `smart1-hub` on every lead, the *source* -- the tool
that captured it -- and the *page*, the specific form or placement. A Suite
workflow triggers on those tags and does the sending: the Hub has no mail
sender and is not getting one, so "email the report" means "a workflow on
this tag emails the report", the way the industry landing pages have
worked all along.

That makes the source tag load-bearing, and until this file it was
whatever string a call site happened to pass. Nothing validated it and
nothing said which workflow read it, so a tool could invent a tag at its
call site with no workflow behind it and every lead it captured would sit
untriggered with the panel reading "delivered". This is the vocabulary:
one entry per source the Hub deliberately emits, each naming the Suite
workflow that consumes it -- or `None`, which is the honest answer today
for every one of them, because the workflows are built in Suite by a
person and none has been yet. `test_lead_delivery.py` sweeps every
`capture_and_deliver` call site and fails on a source in neither this
table nor its exemptions; `test_scan_run.py` refuses a client-facing page
that promises a message on a tag whose `workflow` is None.

The page tag stays free: a placement tag on the scan widget, a calculator's
title, a landing page's name. It rides *beside* the source tag, so a
workflow can trigger on the tool or on the one placement, and it is the
half a rep chooses.
"""
from __future__ import annotations

# On every lead. The master audience and the suppression anchor -- never a
# trigger, because it fires for all of them.
HUB_TAG = "smart1-hub"

# source tag -> what consumes it. `workflow` is the name of the Suite
# workflow triggered by the tag, or None where nobody has built one yet.
# Adding a source here is the declaration; a call site passing a source
# that is not here fails the sweep.
SOURCES: dict[str, dict] = {
    "scan_widget": {
        "what": "the AI-visibility check a prospect runs on a client's site (modules/scans)",
        "workflow": None,   # to build: email the report link on this tag
    },
    "website_audit": {
        "what": "the full website audit placement, and the staff audit tool (hub/website_audit_routes.py)",
        "workflow": None,   # to build: email the audit page link on this tag
    },
    "calculators": {
        "what": "a media calculator unlocked with a contact (modules/calculators)",
        "workflow": None,
    },
    "landing": {
        "what": "a page built by the Landing Page Maker, posting from its own JS (hub/landing_render.py)",
        "workflow": None,
    },
    "ads_builder": {
        "what": "a new business quoted in Smart 1 Ads (modules/ads_builder/client_link.py)",
        "workflow": None,
    },
    "google_access": {
        "what": "a new business asking for Google access (modules/google_access)",
        "workflow": None,
    },
    "ads_grader": {
        "what": "a prospect who connected their own Google Ads account to the "
                "public grader (modules/ads_grader)",
        # To build: email the scored report link on this tag. Until it exists
        # the report URL is on the contact under the two custom fields and a
        # rep opens it -- backed() is what stops any page promising a message
        # nothing sends.
        "workflow": None,
    },
    "ads_reports": {
        "what": "the recurring monthly Google Ads performance report for a "
                "client we manage (modules/ads_builder/monitoring.py)",
        # To build: email the report link on this tag. This is the one source
        # here that is a CLIENT rather than a prospect -- it upserts onto their
        # existing Suite contact by email, which is why the schedule cannot be
        # switched on without one.
        "workflow": None,
    },
    # The nine industry landing tools. Each is its own source because each
    # page's follow-up is its own sequence; the page tag names the plan.
    "boat": {"what": "Boat Dealer Weather Marketing (modules/boat)", "workflow": None},
    "tourism": {"what": "Tourism Marketing Plan (modules/tourism)", "workflow": None},
    "legal": {"what": "Legal Market Plan (modules/legal)", "workflow": None},
    "restaurant": {"what": "Restaurant Market Plan (modules/restaurant)", "workflow": None},
    "ski": {"what": "Ski Resort Market Plan (modules/ski)", "workflow": None},
    "hvac": {"what": "HVAC Market Plan (modules/hvac)", "workflow": None},
    "recruit": {"what": "Recruitment Market Plan (modules/recruit)", "workflow": None},
    "stadium": {"what": "Stadium to Screen (modules/stadium)", "workflow": None},
    "rv": {"what": "RV Dealer Demand Plan (modules/rv)", "workflow": None},
    "msa": {"what": "a Master Services Agreement signed on the client page (modules/msa)", "workflow": None},
    "commercial_review": {
        "what": "a Commercial Builder review link sent to the client's contact "
                "(modules/commercial_builder/routes/review.py)",
        "workflow": None,   # to build: email the review link on this tag
    },
}

# Sources that write a lead row and deliberately never deliver it, so they
# emit no tag. Named so the sweep can tell "decided" from "forgotten".
CAPTURE_ONLY: dict[str, str] = {
    "display_ads": "a prospect opened in the Display Ad Builder is a note on "
                   "the record, not a contact anybody asked to be written",
}


def known(source: str) -> bool:
    return str(source or "") in SOURCES


def workflow_for(source: str) -> str | None:
    """The Suite workflow that consumes this source tag, or None."""
    return (SOURCES.get(str(source or "")) or {}).get("workflow")


def backed(source: str) -> bool:
    """Is there a workflow recorded for this tag? A page may promise a
    message only where this is True."""
    return bool(workflow_for(source))


def tags_for(row: dict) -> list[str]:
    """The tag array for one stored lead row: hub, source, page."""
    return [t for t in (HUB_TAG, str(row.get("source") or ""),
                        str(row.get("page") or "")) if t][:10]
