"""Live competitor ad intelligence — the interface, not a vendor.

`campaign_ai.research_competitors()` asks a model who a business competes
with. It is deliberately honest about what that is: the answer comes back
split into `named` (what the client themselves said, echoed verbatim) and
`researched` (the model's own guess, carrying a note on every screen saying
nobody has checked it). What it cannot do is say who is *actually bidding* on
these searches this week, because nothing here has ever subscribed to a data
source that knows.

That subscription is a decision and a recurring cost rather than something to
guess at in code, so this file is the shape of the answer with no vendor
behind it yet. **Unconfigured it is invisible**: `verified_competitor_data()`
returns None, `research_competitors()` grows no third bucket, and nothing on
any screen promises a client a competitive picture the Hub cannot produce.
That is the state a fresh deployment is in, and the one this ships in.

Four rules, each of which is how a data feed goes wrong quietly.

**Absent is not empty, and neither is an error.** None means *we did not
look* — no provider, no key, or the lookup failed — and is drawn as nothing at
all. An empty list from a provider that answered means *this domain is not in
their index*, which is a real finding about a small local business and is a
different sentence. The two are kept apart by `state`, never collapsed.

**A verified name never merges into the model's guesses.** They are two
claims of different strength about the same question, and a reader has to be
able to tell "somebody's crawler saw this ad running" from "a model thought
of this name" — the rule `hub/scan_facts.py` applies to a logo photographed
off a page, one tool over. It is a third bucket, and the existing two are
returned byte-for-byte unchanged.

**Every row says where it came from and when.** A competitive picture with no
date on it is read as this week's, which on ad spend is the whole value of
it.

**Nothing here raises.** A provider that is down, slow or refusing must cost
the campaign a bucket and never the proposal — the answer
`services/provider_check.py` gives one shelf over.

`PROVIDERS` is what a swap costs: one entry, one fetch function, and no
caller outside this file changes. The HTTP for whichever vendor is chosen
lives behind `_fetch` alone.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Every provider this file knows how to talk to. Empty of implementations on
# purpose: no vendor has been chosen or paid for, and writing the HTTP for a
# guess is how a deployment ends up half-wired to something nobody subscribed
# to. Adding one means an entry here and a `_fetch_<key>` beside it -- nothing
# outside this file moves.
PROVIDERS: dict[str, dict] = {
    "spyfu": {
        "label": "SpyFu",
        "key_env": "AD_INTEL_API_KEY",
        "built": False,
        "note": ("Publishes historical paid keywords and ad copy per domain. "
                 "The most likely candidate for an agency this size; not "
                 "wired, because nothing here has a key for it."),
    },
    "semrush": {
        "label": "Semrush",
        "key_env": "AD_INTEL_API_KEY",
        "built": False,
        "note": ("Broader dataset and a per-line API credit model. Not wired."),
    },
}


def provider_name() -> str:
    """Which provider this deployment is configured for, if any."""
    return (os.environ.get("AD_INTEL_PROVIDER") or "").strip().lower()


def api_key() -> str:
    return (os.environ.get("AD_INTEL_API_KEY") or "").strip()


def status() -> dict:
    """Why this feature is or is not available, in words somebody can act on.

    Four answers rather than a boolean, because "nobody has chosen a
    provider", "the provider named is one this file cannot talk to", "the key
    is missing" and "ready" send somebody to four different places — the rule
    `services/provider_check.py` gives about a refused key and an unreachable
    service.
    """
    key = provider_name()
    if not key:
        return {"configured": False, "state": "not_configured", "provider": "",
                "note": ("No competitor ad-intelligence provider is set. "
                         "Set AD_INTEL_PROVIDER and AD_INTEL_API_KEY to turn "
                         "this on.")}
    entry = PROVIDERS.get(key)
    if not entry:
        return {"configured": False, "state": "unknown_provider", "provider": key,
                "note": (f'AD_INTEL_PROVIDER is set to "{key}", which is not a '
                         f'provider this Hub can talk to. Known: '
                         f'{", ".join(sorted(PROVIDERS)) or "none"}.')}
    if not entry.get("built"):
        return {"configured": False, "state": "not_built", "provider": key,
                "note": (f'{entry["label"]} is a provider this Hub knows about '
                         f'and has no implementation for yet. {entry["note"]}')}
    if not api_key():
        return {"configured": False, "state": "no_key", "provider": key,
                "note": (f'{entry["label"]} is selected but {entry["key_env"]} '
                         f'is not set.')}
    return {"configured": True, "state": "ready", "provider": key,
            "note": f'{entry["label"]} is configured.'}


def _domain_of(value: str) -> str:
    """The canonical domain a URL belongs to.

    Through hub/client_context.py, which is the one place in this Hub that
    decides what a domain means -- a second reading here would join a
    competitive picture to a different business from every other domain-keyed
    report. Falls back to a bare strip so this module stays runnable outside
    the Hub, which is how the rest of ads_builder is written.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    try:
        from hub.client_context import canonical_domain
        return canonical_domain(raw) or ""
    except Exception:                                    # noqa: BLE001
        for prefix in ("https://", "http://", "www."):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        return raw.split("/")[0].split("?")[0]


def _fetch(provider: str, domain: str) -> dict:
    """The one place a vendor's HTTP would live.

    Deliberately unimplemented. Whichever provider is chosen returns its own
    shape here and `verified_competitor_data()` normalises it, so swapping
    vendors is this function and nothing else.
    """
    raise NotImplementedError(
        f"No implementation for {provider}; choose a provider first.")


def verified_competitor_data(domain: str, provider: str | None = None) -> dict | None:
    """Who is actually bidding against this domain, or None if we did not look.

    None on every unconfigured, unknown or failed path — never an empty
    result, and never an exception out of this function. A caller that gets
    None has learned nothing and must say nothing.
    """
    domain = _domain_of(domain)
    if not domain:
        return None
    key = (provider or provider_name()).strip().lower()
    entry = PROVIDERS.get(key)
    if not entry or not entry.get("built") or not api_key():
        return None
    try:
        raw = _fetch(key, domain)
    except Exception as exc:                             # noqa: BLE001
        # A feed that is down costs the campaign a bucket, never the proposal.
        log.warning("ad_intel: %s lookup failed for %s: %s", key, domain, exc)
        return None
    return normalise(raw, provider=key, domain=domain)


def normalise(raw: dict, *, provider: str, domain: str) -> dict:
    """One shape whatever answered, with the claim's source on every row.

    Split out from the fetch so a vendor swap is a parser rather than a
    rewrite, and so this can be exercised without a key or a network.
    """
    raw = raw or {}
    rows = []
    for item in (raw.get("competitors") or [])[:20]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("domain") or "").strip()[:120]
        if not name:
            continue
        rows.append({
            "name": name,
            "domain": str(item.get("domain") or "").strip().lower()[:200],
            "overlap": item.get("overlap"),
            "paid_keywords": item.get("paid_keywords"),
            "estimated_monthly_spend": item.get("estimated_monthly_spend"),
            # On the row, not only in a header: a competitive picture with no
            # date on it is read as this week's.
            "source": PROVIDERS.get(provider, {}).get("label") or provider,
            "observed": str(raw.get("observed") or "")[:40],
        })
    return {
        "provider": provider,
        "label": PROVIDERS.get(provider, {}).get("label") or provider,
        "domain": domain,
        "observed": str(raw.get("observed") or "")[:40],
        "competitors": rows,
        # "They publish nothing for this domain" is a real answer about a small
        # local business, and it is not the same as never having looked.
        "state": "measured" if rows else "none_in_index",
    }
