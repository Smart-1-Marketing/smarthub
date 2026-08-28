"""Starting a commercial for a client the agency already has.

The Start page offered a `<select>` of `cb_clients` — this module's own table
— plus "create new client". On a Hub where the client book is 500-odd
businesses in Knack, that dropdown is **empty on the first visit and never
contains anybody it was not typed into**, so the only way to start a
commercial for a client of eleven years' standing was to retype their name and
create a second record of them. The tool then filed the finished spot under
that typed name, which joins to nothing: no products, no scans, no Client 360
card, no logo, no phone number — all of it already on file, none of it reachable.

So this module is the join. It is the same shape as
`modules/ads_builder/client_link.py` and inherits its rules rather than
restating the arguments for them:

* **Look the client up; never match on a substring.** `hub/client_key.py`
  resolves on domain first, exact normalised name second, and refuses a near
  match it cannot be sure of. Attributing one company's commercial to another
  is the worst thing available here.

* **Never store the derived key.** `create_all()` adds no column to an
  existing table, so a `client_key` column would be silently absent on the
  live Postgres while every local test passed. What is stored is the name and
  the website — columns `cb_clients` already has — and the key is derived on
  read, so a client renamed in Knack re-joins rather than leaving a stale copy.

* **A source that could not be read is named.** "This business is not on the
  client list" and "we could not reach the client list" send a person to two
  different places, and only the first one means *create them as new*.

## Adopting is a copy, and it is deliberately a one-way one

`adopt()` writes a `cb_clients` row carrying the registry's name, website and
phone. That is a copy of somebody else's record, which this codebase is
generally against — but the alternative is a foreign key to a registry that is
a JSON overlay over a Knack export, on a module that also has to run
standalone, and `create_all()` will not add the column to the live table
anyway.

What makes the copy safe is that it is **only ever refreshed from the
registry, never written back to it**. Knack owns the client record and this
Hub does not write to it (`hub/client_urls.py` says so at length). The brand
profile fields this module adds on top — fonts, pronunciation, preferred
voice, spokesperson — exist nowhere else and are this table's own.

And **a business already adopted is found again, not adopted twice.** Two
`cb_clients` rows for one company splits their commercials across two
histories, and the second one looks exactly like a client with no work.
"""
from __future__ import annotations


def _registry():
    from hub import clients_registry
    return clients_registry


def search(query: str, limit: int = 12) -> dict:
    """Clients matching what has been typed, from the agency's own book.

    Returns `available: False` with a reason rather than an empty list when
    the registry could not be read at all.
    """
    try:
        rows = _registry().search_clients(str(query or ""), limit=limit)
    except Exception as exc:  # noqa: BLE001 — standalone, or the registry is down
        return {"clients": [], "available": False,
                "note": f"The Hub client list could not be read: {exc}"}
    return {
        "clients": [
            {
                "name": r.get("name", ""),
                "url": r.get("url", "") or r.get("domain", ""),
                "domain": r.get("domain", ""),
                "source": r.get("source", ""),
                "products": r.get("product_count", 0),
                "running": r.get("running_count", 0),
            }
            for r in rows
        ],
        "available": True,
        "note": "",
    }


def find_in_registry(name: str) -> dict | None:
    """One registry row by exact name, or None.

    Exact, through the registry's own lookup — never a substring, for the
    reason `hub/client_key.py` gives: "Riverside HVAC" must not collect
    "Riverside HVAC Supply".
    """
    try:
        return _registry().find_client(str(name or ""))
    except Exception:  # noqa: BLE001
        return None


def same_client(a_name: str, a_url: str, b_name: str, b_url: str) -> bool:
    """Whether two (name, url) pairs are the same business.

    Through `hub/client_key.same_client`, which joins on canonical domain
    first and exact normalised name second. Falls back to an exact name
    comparison outside the Hub rather than to a substring test — the loose
    version of this rule is the bug, so the degraded path is stricter, not
    looser.
    """
    try:
        from hub.client_key import same_client as _same
        return bool(_same(a_name or "", a_url or "", b_name or "", b_url or ""))
    except Exception:  # noqa: BLE001
        return bool(a_name) and (a_name or "").strip().lower() == (b_name or "").strip().lower()


def existing_row(rows, name: str, url: str):
    """A `cb_clients` row already standing for this business, or None.

    Takes the rows rather than querying, so this stays testable without a
    database and so the caller decides how much of the table to scan.
    """
    for row in rows:
        if same_client(name, url, getattr(row, "name", ""), getattr(row, "website", "") or ""):
            return row
    return None


def profile_from_registry(row: dict) -> dict:
    """The registry's row as the fields `cb_clients` keeps.

    Only what the registry actually holds. Nothing is inferred: no logo
    guessed from the domain, no phone invented, no industry read off the name
    — `modules/ads_builder/logo.py` says at length why a wrong logo on a
    client-facing asset is worse than none, and the same is true of a phone
    number on an end card.
    """
    url = (row.get("url") or row.get("domain") or "").strip()
    return {
        "name": (row.get("name") or "").strip(),
        "website": url,
        "phone": (row.get("phone") or "").strip(),
        "industry": (row.get("industry") or "").strip(),
    }


def refresh_note(client_row, registry_row: dict | None) -> str:
    """What the registry says that this brand profile does not, in words.

    Not applied automatically. A staff member who corrected a phone number on
    the brand profile has better information than an export does — the rule
    `hub/user_directory.py` works to — so the difference is reported and a
    person decides. Empty string where there is nothing to say, so a screen
    with nothing to report prints nothing rather than a reassurance.
    """
    if not registry_row:
        return ""
    gaps = []
    if not (getattr(client_row, "website", "") or "").strip():
        if (registry_row.get("url") or registry_row.get("domain") or "").strip():
            gaps.append("a website")
    if not (getattr(client_row, "phone", "") or "").strip():
        if (registry_row.get("phone") or "").strip():
            gaps.append("a phone number")
    if not gaps:
        return ""
    return ("The client record has " + " and ".join(gaps)
            + " that this brand profile does not. Copy it across on the profile "
              "if the commercial should carry it.")


def hub_client_context(name: str, url: str = "") -> dict:
    """What the Hub knows about this business, for the screens that say so.

    `known` is the honest tri-state again: matched, not matched, or could not
    be checked. A screen that draws "new business" over a registry we failed
    to read tells a rep to create a duplicate of a client we have had for
    years.
    """
    try:
        from hub.client_key import resolve as _resolve
        found = _resolve(name=name or "", url=url or "")
        return {"known": bool(found.get("known")),
                "client": found.get("client") or "",
                "matched_on": found.get("matched_on") or "",
                "checked": True,
                "why": found.get("why") or ""}
    except Exception as exc:  # noqa: BLE001
        return {"known": False, "client": "", "matched_on": "", "checked": False,
                "why": f"Could not be checked: {exc}"}


def suite_location_id(name: str, url: str = "") -> str:
    """This client's Smart 1 Suite sub-account id, where one is on file.

    `hub/suite_accounts.location_for()` is the mapping now — the Social
    Content Planner needs the identical answer to know which Social Planner a
    post belongs to, and two readings of "which sub-account is this client"
    is how one of them comes to publish onto another client's page. The name
    is kept so every caller here is unchanged, the way
    `modules/radio_promo/voices.py` re-exports `hub/voice_casting.py`.

    Empty string on any failure, and on the two answers that are not a
    location: no sub-account recorded, and a mapping that could not be read.
    They are different situations and `location_for()` keeps them apart — this
    signature cannot, which is exactly why the shared one returns a state.
    This is called while saving a CTA and must never raise or block.
    """
    try:
        from hub.suite_accounts import location_for
    except Exception:  # noqa: BLE001
        return ""
    try:
        return location_for(name, url).get("location_id", "") or ""
    except Exception:  # noqa: BLE001
        return ""
