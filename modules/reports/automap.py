"""File a campaign under a client from its name, when the name says whose it is.

The rename shape ``store.RENAME_SHAPE`` -- ``S1M | <ClientKey> | <Product> |
<anything>`` -- is shown beside every row in the unmapped queue so whoever
can rename the campaign in the platform writes it in a form this module can
read. ``parse_name()`` reads it; ``run()`` files every unmapped campaign
whose name parses and whose client resolves.

Rules, each a way to file spend under the wrong client:

* **The first segment must be the literal ``S1M``**, case-insensitive and
  whitespace-tolerant, and there must be at least two segments -- the mark
  and the client. ``SIM`` is not it -- a near-miss is left in the unmapped
  queue for a person, because a parser that forgives a typo in the one
  token that marks a name as ours will eventually read a campaign that was
  never meant for it. A name with no product segment (or an empty one)
  files under ``products.DEFAULT_PRODUCT_FOR_PLATFORM`` for its platform,
  and the row says so (``auto_rule="name_v1+default_product"``).
* **The client resolves exactly or not at all.** The second segment is
  tried as a Hub key (``d:example.com`` / ``n:slug``, the same key
  ``/reports/api/clients`` hands the picker) and then as a client name,
  case-insensitively, against the registry. No substring and no fuzzy pass:
  the ``hub/client_key.py`` rule, because attributing one company's spend to
  another is the worst outcome available here.
* **A human mapping is never overwritten.** Only a campaign with NO
  ``CampaignMap`` row is considered; a row somebody made, and a row this
  module made on an earlier run, both stand. Re-mapping is a person's press
  on the unmapped queue.

Every mapping it writes is ``mapped_by="auto"`` with ``auto_rule="name_v1"``
on the row, so a report can tell a filed-by-name campaign from one a person
chose, and an activity row under the client's name says it happened.
"""
from __future__ import annotations

import logging
import re

from . import store

log = logging.getLogger(__name__)

RULE = "name_v1"
MAPPED_BY = "auto"

_MARK = re.compile(r"^\s*s1m\s*$", re.IGNORECASE)


def parse_name(name: str) -> dict | None:
    """``S1M | <ClientKey> | <Product> | <anything>`` -> the parts, or None.

    Pipe-separated, first segment the S1M literal (any case, any spacing),
    at least two segments, client non-empty. ``product`` is "" when the
    name carries none; the caller fills in the platform's default.
    """
    if not name or "|" not in str(name):
        return None
    parts = [p.strip() for p in str(name).split("|")]
    if len(parts) < 2 or not _MARK.match(parts[0]):
        return None
    client = parts[1]
    product = parts[2] if len(parts) > 2 else ""
    if not client:
        return None
    return {"client": client, "product": product,
            "rest": " | ".join(parts[3:]).strip()}


class RegistryUnavailable(RuntimeError):
    """The client registry could not be read, which is a different answer
    from a token naming nobody -- and only the second means rename it."""


def resolve_client(token: str) -> tuple[str, str] | None:
    """(Hub key, display name) for the second segment, or None.

    Exact key first -- the registry row already carries the key the picker
    hands over, derived the way /reports/api/clients derives it -- then an
    exact, case-insensitive name. Never a substring.
    """
    token = (token or "").strip()
    if not token:
        return None
    try:
        from hub import client_key as ck
        from hub import clients_registry
        rows = clients_registry.all_clients()
    except Exception as exc:               # noqa: BLE001 - registry unavailable
        log.warning("reports automap: client registry unreadable: %s", exc)
        # Not "no such client": nothing can be resolved this run, and
        # naming every campaign unresolved would send somebody to rename
        # campaigns that are fine. run() stops on it and says so.
        raise RegistryUnavailable(f"{type(exc).__name__}: {exc}"[:200]) from exc
    low = token.lower()
    for r in rows:
        name = r.get("name") or ""
        key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
        if key and key.lower() == low:
            return key, name
    for r in rows:
        name = r.get("name") or ""
        if name.lower() == low:
            key = r.get("key") or ck.client_key(name, r.get("url") or r.get("domain") or "")
            return key, name
    return None


def run(actor: str = "scheduler", limit: int = 5000) -> dict:
    """File every unmapped campaign whose name parses and resolves.

    Returns ``{"mapped": n, "unparsed": n, "unresolved": [...], "clients":
    {key: name}}``. ``unresolved`` names the client tokens that parsed and
    matched nobody, because those are the ones a rename typo produces and
    the unmapped queue is where somebody meets them.
    """
    out = {"mapped": 0, "unparsed": 0, "unresolved": [], "clients": {}}
    try:
        from hub import audit as hub_audit
    except Exception:                      # noqa: BLE001 - standalone
        hub_audit = None
    cache: dict[str, tuple[str, str] | None] = {}
    for row in store.unmapped_campaigns(days=3650, limit=limit):
        parsed = parse_name(row.get("campaign_name") or "")
        if not parsed:
            out["unparsed"] += 1
            continue
        token = parsed["client"]
        if token.lower() not in cache:
            try:
                cache[token.lower()] = resolve_client(token)
            except RegistryUnavailable as exc:
                out["registry_error"] = str(exc)
                break
        hit = cache[token.lower()]
        if not hit:
            if token not in out["unresolved"]:
                out["unresolved"].append(token)
            continue
        key, name = hit
        from . import products as _products
        product, rule = parsed["product"], RULE
        typed = product
        if product and _products.normalize(product) not in _products.PRODUCTS:
            # A segment naming no product in the catalog ("Strming TV") must
            # not become a product: it draws a bar on the client's page that
            # no budget line can ever pace. The platform default, and the
            # rule says the segment was not understood.
            product, rule = "", RULE + "+unknown_product"
        if not product:
            product = _products.default_for(row["platform"])
            if rule == RULE:
                rule = RULE + "+default_product"
        try:
            store.map_campaign(row["platform"], row["account_id"], row["campaign_id"],
                               client=key, client_name=name, product=product,
                               mapped_by=MAPPED_BY, auto_rule=rule,
                               campaign_name=row.get("campaign_name") or "")
        except ValueError as exc:
            log.warning("reports automap: %s", exc)
            continue
        out["mapped"] += 1
        out["clients"][key] = name
        if hub_audit is not None:
            try:
                hub_audit.log("reports", "campaign_automapped", actor=actor,
                              client=name, client_key=key, action="campaign_automapped",
                              platform=row["platform"], campaign_id=row["campaign_id"],
                              product=product, rule=rule,
                              detail=f"{store.platform_label(row['platform'])} campaign "
                                     f"{row.get('campaign_name') or row['campaign_id']} "
                                     f"filed under {name} from its name"
                                     + (f" (product segment {typed!r} is not in the catalog; "
                                        f"filed under {product or 'no product'})"
                                        if "unknown_product" in rule else ""))
            except Exception:              # noqa: BLE001 - a log line is not the mapping
                pass
    return out
