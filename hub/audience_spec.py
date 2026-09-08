"""One confirmed audience per client, and the readers of it.

The Audience Finder is the second of the two Pickaxes that earn a live call
(hub/pickaxe_registry.py): its knowledge base is the agency's own audience
catalog and topics taxonomy, which neither this Hub nor a plain model call
holds. The Proposal Builder already asks it per campaign
(/sales/builder/api/find-audiences) and tick-gates what comes back into
S.audiences. What nothing held was the *client's* answer — who this business
is actually for, confirmed once, readable everywhere a campaign for them is
built. Without it, the same question is re-asked (and re-billed) on every
campaign, and the ad-copy prompt's {audience} field has been "typed until
then" since the day it was harvested (hub/prompts_harvested.py's own prefill
note names this module).

One audience, four readers:

  * **Client 360** — the Target audience card: propose (a billed button),
    tick, keep. The confirmation lives here because the client record is
    where a rep decides facts about a client rather than about one campaign.
  * **The Proposal Builder** — the audience step offers the confirmed
    segments as one-press adds beside its own research. Offered, never
    added: a plan that grows a line by itself is a plan somebody has to
    audit before every send.
  * **The IO Builder** — the audiences question includes the confirmed
    segments among its options and says where they came from. Ticking is
    still the person's press.
  * **The ad-copy prompt** — `for_prompt()` fills AD_COPY's {audience}
    placeholder when the campaign typed nothing. A typed value wins, the
    overlay rule `hub/client_urls.py` works to: the rep on this campaign is
    a better source than a confirmation made about the client in general.

Rules, each one this corner of the Hub keeps having to relearn:

**The model proposes, a person ticks, and nothing is written by proposing.**
Every candidate arrives `accepted: False` — the competitor-research shape.
`confirm()` is the only write, it records who and when, and it *replaces*
the stored set: the confirmed audience is the current answer, not an
accumulation nobody curates.

**Proposing is a button, never a page load.** The Pickaxe call is billed per
use; `get()` and `for_prompt()` are jsonstore reads and cost nothing, which
is what makes it safe for four readers to ask on every render.

**A failed lookup is an error, never an empty audience.** The Pickaxe falls
back to the Hub's own model with the source named — the agency's catalog and
a model's general knowledge are different confidences and the screen says
which — and both failing answers `ok: False` rather than "no audiences".

**Stored by name, keyed by slug, never a derived key in the row** — the
`hub/brand_template.py` arrangement, for `hub/client_key.py`'s reasons.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from hub import jsonstore

# A confirmed audience is a short list somebody curated, not a dump of
# everything the catalog offered. Twelve is more layers than any IO here
# carries; a proposal batch stays at the Pickaxe's own twenty.
MAX_SEGMENTS = 12
MAX_CANDIDATES = 20
NAME_MAX = 120


def _key(client: str) -> str:
    from hub.client_key import name_slug
    return name_slug(client) or "client"


def _path(client: str) -> str:
    return os.path.join(jsonstore.data_dir("audience_spec"), _key(client) + ".json")


def parse_reply(reply: str) -> list:
    """A Pickaxe chat reply, split into one audience candidate per line.

    The reply is prose from a chat agent, not JSON — `hub/pickaxe.py`'s own
    VERIFY note says the response shape is transcribed from Pickaxe's
    published examples rather than exercised, so this reads defensively:
    one candidate per line, a leading bullet, dash or number stripped, and
    a line that reads like a heading or a whole sentence rather than a
    short name is left out rather than offered as an audience nobody could
    act on. One reading — the Proposal Builder's /api/find-audiences imports
    this rather than keeping the copy it started with.
    """
    out = []
    for raw in str(reply or "").split("\n"):
        line = re.sub(r"^[-•*]\s*", "", raw.strip())
        line = re.sub(r"^\d+[.)]\s*", "", line).strip()
        if not line or line.endswith(":") or len(line) > NAME_MAX:
            continue
        out.append(line)
    return out


def candidates(names, existing=(), cap: int = MAX_CANDIDATES) -> list:
    """Shape raw names into tick-gated candidate rows.

    Deduped case-insensitively against themselves and against what is
    already on the campaign or the record, capped, and every row arrives
    `accepted: False` — nothing reaches anything until a person ticks it.
    """
    seen = {str(n).strip().lower() for n in (existing or ()) if str(n).strip()}
    out = []
    for name in names or []:
        name = str(name).strip()[:NAME_MAX]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "accepted": False})
        if len(out) >= cap:
            break
    return out


def get(client: str) -> dict:
    """The confirmed audience for a client, or an empty shell.

    `picked` is its own field rather than the caller inferring it from an
    empty list, so "nobody has confirmed anything yet" reads differently
    from an audience later cleared — `hub/brand_template.py`'s rule.
    Costs nothing: a jsonstore read, which is what lets four readers ask
    on every render without a billed call anywhere near a page load.
    """
    row = jsonstore.read_json(_path(client), default=None)
    if not isinstance(row, dict):
        row = {}
    auds = [str(a).strip() for a in (row.get("audiences") or [])
            if str(a).strip()][:MAX_SEGMENTS]
    return {
        "client": client,
        "audiences": auds,
        "target": str(row.get("target") or ""),
        "source": str(row.get("source") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "picked": bool(auds),
    }


def for_prompt(client: str) -> str:
    """The confirmed audience as one line for a prompt, or "".

    Empty when nobody has confirmed one — the caller says "not provided"
    in its own words rather than this module inventing an audience, and a
    typed value on the campaign always wins over this.
    """
    try:
        return ", ".join(get(client)["audiences"])
    except Exception:                                   # noqa: BLE001
        return ""


def propose(client: str, *, sells: str = "", user: str = "") -> dict:
    """Audience candidates for a client — the Pickaxe, or the Hub's own AI.

    The target the Pickaxe is asked about is built from what the Hub already
    holds (`client_context.tool_context()` — industry and the business
    description off their own record and scan) plus whatever the rep typed
    into the card's one box; nothing is retyped that Client 360 already
    knows. Falls back to the Hub's model on PickaxeUnavailable, the contract
    every Pickaxe caller uses, and the response names which one answered.
    Billed, so this runs from a button and never a page load.
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}
    sells = str(sells or "").strip()

    industry = description = ""
    try:
        from hub import client_context
        ctx = client_context.tool_context(client, gallery=False)
        industry = str(ctx.get("industry") or "").strip()
        description = str(ctx.get("description") or "").strip()[:200]
    except Exception:                                   # noqa: BLE001
        pass
    parts = [p for p in (sells, industry, description) if p]
    target = "; ".join(parts) or "this client's likely customers"

    existing = get(client)["audiences"]
    source, names = "pickaxe", []
    try:
        from hub import pickaxe
        from hub.pickaxe_registry import AUDIENCE_FINDER, fill
        reply = pickaxe.ask(
            AUDIENCE_FINDER["pickaxe_id"], module="client360",
            purpose="audience_finder",
            workspace_id=AUDIENCE_FINDER["workspace_id"],
            user_id=user or None,
            inputs=fill(AUDIENCE_FINDER, client=client, target=target))
        names = parse_reply(reply)
    except Exception:                                   # noqa: BLE001
        # Not configured, unreachable, or the endpoint shape is wrong — every
        # one of those costs the Pickaxe answer and never the button.
        source = "ai"
        prompt = (
            "You are a media planner deciding the audience targeting for a "
            "local advertising client.\n"
            f"Client: {client}\n"
            f"Who to reach / what they sell: {target}\n\n"
            "Suggest up to 20 audience segments worth targeting — behavioral, "
            "contextual, demographic and CRM/lookback layers. Short names "
            "only, no explanation. Do not invent facts about the client.\n"
            'Return STRICT JSON only: {"audiences":["..."]}')
        try:
            from hub import ai
            raw = ai.chat([{"role": "user", "content": prompt}],
                          module="client360", purpose="audience_finder_fallback",
                          json_mode=True, temperature=0.4)
            names = [str(a) for a in (json.loads(raw).get("audiences") or [])]
        except Exception as exc2:                       # noqa: BLE001
            return {"ok": False,
                    "error": "The audience research did not run — nothing "
                             "has been changed on this client's record.",
                    "detail": str(exc2)}

    out = candidates(names, existing)
    return {
        "ok": True, "audiences": out, "source": source, "target": target,
        "note": ("Nothing came back for this client. That is an answer — it "
                 "does not mean the search failed." if not out else
                 (("From the agency audience catalog" if source == "pickaxe"
                   else "Suggested by the Hub's own AI — the agency catalog "
                        "could not be reached, so this is general knowledge, "
                        "not the catalog")
                  + ". Tick what fits and press Keep; nothing is saved "
                    "until you do.")),
    }


def confirm(client: str, names, *, target: str = "", source: str = "",
            actor: str = "") -> dict:
    """Set the confirmed audience — the one write, and a person's press.

    Replaces the stored set rather than appending: the confirmed audience is
    the current answer, and the card sends the whole list it shows, so
    removing a segment is the same call as adding one. Refuses an empty list
    by name — clearing is `clear()`, its own verb, because "take this off
    the record" and "save what is ticked" are different statements and one
    control for both cannot say which it did (`hub/client_owner.py`'s rule).
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}
    auds, seen = [], set()
    for n in (names or []):
        n = str(n).strip()[:NAME_MAX]
        if n and n.lower() not in seen:
            seen.add(n.lower())
            auds.append(n)
    if not auds:
        return {"ok": False,
                "error": "Nothing ticked — nothing was saved. To take the "
                         "confirmed audience off this record, use Clear."}
    dropped = max(0, len(auds) - MAX_SEGMENTS)
    auds = auds[:MAX_SEGMENTS]

    row = {
        "client": client,
        "audiences": auds,
        "target": str(target or "").strip()[:400],
        "source": str(source or "").strip()[:40],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "updated_by": str(actor or ""),
    }
    jsonstore.write_json(_path(client), row)
    _log("audience_confirmed", client, actor,
         f"{len(auds)} segment{'s' if len(auds) != 1 else ''}")
    out = {"ok": True, "audience": get(client)}
    if dropped:
        # Bounded, and never in silence — hub/drafts.py's rule. Twelve layers
        # is already more than any IO here carries.
        out["note"] = (f"Kept the first {MAX_SEGMENTS}; {dropped} more were "
                       "not saved. A confirmed audience is a short list "
                       "somebody curated, not the whole catalog.")
    return out


def clear(client: str, *, actor: str = "") -> dict:
    """Take the confirmed audience off the record. Always succeeds."""
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}
    jsonstore.delete_json(_path(client))
    _log("audience_cleared", client, actor, "")
    return {"ok": True, "audience": get(client)}


def _log(event: str, client: str, actor: str, detail: str) -> None:
    # "hub" is in client_brand.NOT_WORK: confirming an audience is a decision
    # recorded about a client, not a deliverable made for them.
    try:
        from hub import audit
        audit.log("hub", event, actor=actor or None, client=client,
                  detail=detail)
    except Exception:                                   # noqa: BLE001
        pass
