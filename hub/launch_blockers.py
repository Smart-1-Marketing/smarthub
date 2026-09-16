"""What a client's site is missing before a paid campaign can be measured.

A campaign we cannot measure is a campaign we cannot prove worked. `find()`
reads `hub.client_brief.build()`'s `digital` section and names each thing
that would leave a paid buy unmeasurable or untracked -- no analytics tool
at all, still on Universal Analytics (which stopped collecting in 2023), no
Google tag, no Consent Mode v2, no pixel of either kind, an invalid
certificate, or mixed content. Nothing here is billed or written by
arriving; it is offered as a list, and a single web ticket naming all of it
is created only when a person presses the button.

Read first: `hub/prospect.py`'s `convert()`, `hub/knack_api.create_ticket()`
(the Knack write path every web ticket in this Hub already goes through --
an existing mechanism, not a new Knack data source), `hub/client_context.py`.
"""
from __future__ import annotations

from typing import Any

# code -> (label, why, brief-digital-key, "want" value that IS a blocker).
# `want` is what a healthy site should read; a key missing from the brief
# entirely (not measured) is never treated as a blocker -- absent is not a
# finding, the rule this whole Hub keeps returning to.
_CHECKS: tuple[tuple[str, str, str, str, Any], ...] = (
    ("no_analytics", "No analytics tool detected",
     "Nothing running on the site can be measured, so a paid campaign would "
     "be reporting on itself.", "analytics_tool", None),
    ("universal_ga", "Still on Universal Analytics",
     "Universal Analytics stopped collecting in 2023, so whatever traffic "
     "is being read is not this year's.", "uses_universal_ga", True),
    ("no_google_tag", "No Google tag on the site",
     "Google Ads conversions cannot be tracked without the tag.",
     "has_google_tag", False),
    ("no_consent_mode", "Not running Consent Mode v2",
     "Google now models away conversions it cannot confirm consent for; "
     "without it, reported performance understates what the campaign "
     "actually did.", "uses_consent_mode_v2", False),
    ("no_facebook_pixel", "No Meta pixel on the site",
     "A Meta campaign cannot attribute a conversion back to the ad without "
     "one.", "has_facebook_pixel", False),
    ("no_google_pixel", "No Google remarketing tag on the site",
     "Retargeting and Google Ads conversion tracking both need this.",
     "has_google_pixel", False),
    ("ssl_invalid", "The site's SSL certificate is not valid",
     "Most ad platforms refuse to run traffic to an insecure page, and "
     "browsers warn visitors off it before the campaign gets a click.",
     "ssl_valid", False),
    ("mixed_content", "The site serves mixed content",
     "Insecure resources on an https page get silently blocked by the "
     "browser, which can break the very tag a campaign depends on.",
     "mixed_content", True),
)

# hub/client_brief.py does not carry ssl_valid/mixed_content today (neither
# is in the "digital" fields it reads) -- named here rather than silently
# skipped, so `find()` can say plainly that those two checks are not
# measured yet rather than pretending they always pass.
_NOT_IN_BRIEF = {"ssl_valid", "mixed_content"}


def find(brief: dict) -> list[dict]:
    """Which launch blockers a client brief's `digital` section shows.

    `brief` is `hub.client_brief.build()`'s own output (or `{}`). Returns a
    list of `{"code", "label", "why"}` -- one entry per blocker actually
    found. A field the scan never measured is never a blocker: "not
    checked" and "checked and it's broken" must read differently, or a site
    that was simply never scanned reports as launch-ready.

    Never raises.
    """
    if not isinstance(brief, dict):
        return []
    digital = brief.get("digital") or {}
    if digital.get("measured") is False:
        return []

    out: list[dict] = []
    for code, label, why, key, bad_value in _CHECKS:
        if key in _NOT_IN_BRIEF:
            continue
        fact = digital.get(key)
        if not isinstance(fact, dict) or fact.get("value") is None:
            continue                       # not measured -- never a blocker
        value = fact["value"]
        if bad_value is None:
            # "no_analytics" — an empty/absent analytics_tool string.
            is_blocker = not str(value or "").strip()
        else:
            is_blocker = value == bad_value
        if is_blocker:
            out.append({"code": code, "label": label, "why": why})
    return out


def ticket_body(client: str, blockers: list[dict]) -> str:
    """The ticket description listing every blocker, in the order found."""
    if not blockers:
        return ""
    lines = [f"Before a paid campaign to {client or 'this client'} can be "
             "measured, the following need fixing on their site:", ""]
    for b in blockers:
        lines.append(f"- {b['label']}: {b['why']}")
    return "\n".join(lines)
