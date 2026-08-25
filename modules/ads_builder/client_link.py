"""Joining a generated campaign to the client it is for.

The generator used to take a business name as free text and stop there. The
campaign was real, the proposal was real, and neither existed as far as the
client's own record was concerned: a rep opened Client 360 the next morning and
found no sign that anything had been quoted. That is the failure this module
exists to close, and it is the same shape as ``hub/domain_links.py`` — one act
with several writes behind it, each reported separately, because "filed" and
"filed in one of two places" are different outcomes and one tick for both is
how somebody learns not to trust the tick.

Three rules, each learned elsewhere in this codebase and restated here because
this is a new call site for all of them:

* **Look the client up; never match on a substring.** ``hub/client_key.py``
  resolves on domain first and then an exact normalised name, and refuses a
  near match it cannot be sure of. Attributing one company's campaign to
  another is the worst thing this module could do quietly.

* **Never store the derived key.** ``create_all()`` adds no column to an
  existing table, so a ``client_key`` column would be silently absent on the
  live Postgres while every local test passed — and a client renamed in Knack
  should re-join on the next read rather than leave a stale copy behind. What
  is kept is the name and the URL, inside the campaign JSON, and the key is
  derived when it is needed.

* **A prospect is a lead, not a client record.** A business we have just quoted
  is not in Knack and must not be written into the Hub's client registry to
  make it look like one — ``hub/client_urls.py`` records what happened when an
  earlier tool reused ``house_clients()`` for a job like this and relabelled
  real Knack clients as ours. It goes into Smart 1 Suite as a lead, which is
  where prospects live, and the work and the proposal are filed under the name
  and domain, so the day the client record exists the two join themselves.
"""
from __future__ import annotations


def _mount_url(path: str) -> str:
    """Absolute where the Hub knows its own address, root-relative otherwise.

    The proposal link is opened from Client 360 in a new tab, so a relative
    path would resolve against whatever page is showing it. An invented host
    would be worse: ``PUBLIC_BASE_URL`` unset means we do not know, and a
    root-relative link still works for anyone already in the Hub.
    """
    try:
        from hub.config import settings
        base = (settings.public_base_url or "").rstrip("/")
    except Exception:  # noqa: BLE001
        base = ""
    return (base + path) if base else path


def search(query: str, limit: int = 12) -> dict:
    """Existing clients matching what has been typed.

    A source that could not be read is named rather than coming back as an
    empty list: "no such client" and "the client list is unavailable" send a
    rep to two different places, and only one of them is "type it as new".
    """
    try:
        from hub import clients_registry
    except Exception as exc:  # noqa: BLE001 — the module runs outside the Hub
        return {"clients": [], "available": False,
                "note": f"The Hub client list could not be read: {exc}"}
    try:
        rows = clients_registry.search_clients(str(query or ""), limit=limit)
    except Exception as exc:  # noqa: BLE001
        return {"clients": [], "available": False,
                "note": f"The Hub client list could not be read: {exc}"}
    return {
        "clients": [
            {
                "name": r.get("name", ""),
                "url": r.get("url", ""),
                "domain": r.get("domain", ""),
                "source": r.get("source", ""),
                "is_seo": bool(r.get("is_seo")),
                "products": r.get("product_count", 0),
                "running": r.get("running_count", 0),
            }
            for r in rows
        ],
        "available": True,
        "note": "",
    }


def resolve(name: str = "", url: str = "") -> dict:
    """Is this a client we already know? Domain first, exact name second."""
    try:
        from hub.client_key import resolve as _resolve
        r = _resolve(name=name or "", url=url or "")
        return {"known": bool(r.get("known")), "client": r.get("client") or "",
                "matched_on": r.get("matched_on") or "", "why": r.get("why") or ""}
    except Exception as exc:  # noqa: BLE001
        return {"known": False, "client": "", "matched_on": "",
                "why": f"Could not be checked: {exc}"}


def file_proposal(proposal: dict, mount: str, actor: str = "") -> dict:
    """Put the campaign on the client's record, in the proposals section.

    Filed as a **link**, not a snapshot: the proposal is a live page that gains
    comments and changes status, and an uploaded copy of it would sit on the
    client record contradicting the thing it is a copy of.
    """
    client = (proposal.get("client_name") or "").strip()
    if not client:
        return {"ok": False, "note": "The proposal carries no client name."}
    campaign = proposal.get("campaign") or {}
    try:
        from hub import proposals as hub_proposals
        rec = hub_proposals.add_link_proposal(
            client,
            url=_mount_url(f"{mount}/proposal/{proposal['id']}/client"),
            title=f"Google Ads search campaign — ${float(campaign.get('monthlyBudget') or 0):,.0f}/mo",
            ref=proposal["id"],
            module="ads_builder",
            note=(campaign.get("strategySummary") or "")[:280],
            actor=actor,
            value=str(campaign.get("monthlyBudget") or 0),
            term="monthly",
            status="draft",
        )
        return {"ok": True, "quote_number": rec.get("quote_number", ""),
                "note": "Filed on the client record."}
    except Exception as exc:  # noqa: BLE001 — a filing failure must not lose the campaign
        return {"ok": False, "note": f"Could not be filed on the client record: {exc}"}


def create_lead(client_name: str, website: str, contact: dict, campaign: dict,
                proposal_id: str = "") -> dict:
    """A new business becomes a lead in Smart 1 Suite.

    Refused rather than faked when there is no email and no phone: a lead
    nobody can contact is worse than no lead, because it reads as a live
    prospect on every count that follows.
    """
    contact = contact or {}
    email = str(contact.get("email") or "").strip()
    phone = str(contact.get("phone") or "").strip()
    if not (email or phone):
        return {"ok": False, "created": False,
                "note": "No lead created — a new client needs an email or a phone number."}
    try:
        from hub import leads as hub_leads
        out = hub_leads.capture_and_deliver(
            source="ads_builder",
            page="Smart 1 Ads campaign generator",
            fields={
                "name": str(contact.get("name") or "").strip(),
                "email": email,
                "phone": phone,
                "company": client_name,
                "website": website,
            },
            client=client_name,
            meta={
                "proposal": proposal_id,
                "monthly_budget": campaign.get("monthlyBudget"),
                "sector": campaign.get("sector"),
                "objective": campaign.get("objective"),
            },
        )
        return {"ok": bool(out.get("ok")), "created": True,
                "lead_id": out.get("lead_id", ""),
                "delivered": bool(out.get("delivered")),
                "note": out.get("note") or "Captured."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "created": False, "note": f"Lead capture failed: {exc}"}


def attach(proposal: dict, mount: str, *, is_new_client: bool = False,
           contact: dict = None, actor: str = "", work: dict = None) -> dict:
    """Every write this join makes, each reported by name.

    ``work`` is what the generation event's own Hub mirror reported. It is
    passed in rather than assumed: the work log entry is that audit row, and
    claiming it landed without asking is what let a broken mirror go unnoticed
    for months.
    """
    campaign = proposal.get("campaign") or {}
    client = (proposal.get("client_name") or "").strip()
    website = campaign.get("websiteUrl") or ""

    result = {
        "client": client,
        "website": website,
        "known_client": resolve(client, website),
        "filed": file_proposal(proposal, mount, actor=actor),
        # The work entry IS the generation audit row: it carries client=, and
        # hub/client_brand.py lists "ads_builder" in WORK_KINDS, so Client 360
        # reads it without anything further here — provided it was written.
        "work": ({"ok": True, "note": "Recorded on the client's work log."}
                 if (work or {}).get("mirrored")
                 else {"ok": False,
                       "note": "Not recorded on the client's work log — "
                               + ((work or {}).get("error")
                                  or "the Hub activity log did not accept it.")}),
        "lead": {"ok": True, "created": False,
                 "note": "Existing client — no lead created."},
    }
    if is_new_client:
        result["lead"] = create_lead(client, website, contact or {}, campaign,
                                     proposal_id=proposal.get("id", ""))
    result["all_ok"] = all(result[k].get("ok") for k in ("filed", "work", "lead"))
    return result
