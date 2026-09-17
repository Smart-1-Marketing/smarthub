"""The ad-performance card on Client 360: the Reports module read for one
Hub client, as one payload a card can draw.

Everything the Reports module knows about a client -- which campaigns are
filed under them, what those spent this month, whether the sold lines are
pacing, whether a day is held in quarantine, whether the client has a live
link and has opened it -- lived on ``/reports/client/<key>``, a page a rep
reaches by knowing the module exists and then knowing the client's key. The
record a rep actually opens for a client said nothing about any of it.

Three things a card has to get right, and each is a way to be confidently
wrong on the one screen everybody reads:

* **A client is filed under more than one spelling, and all of them are
  read.** The staff screens and the auto-mapper file a campaign under the
  Hub-wide key from ``hub/client_key.py`` (``d:acme.com`` or ``n:acme``);
  ``hub/proposal_adapters/reports.py`` mints a link and budget lines under
  the client's **display name**. Both are real rows about one business, so
  ``candidate_keys()`` gathers every spelling the store may hold this client
  under -- the registry-derived key, the name key, the raw name, and any
  key whose stored display name is an exact normalised match -- and the
  card reads across all of them. Exact or not at all: never a substring,
  for the reason ``hub/client_key.py`` gives at length.

* **Four kinds of nothing, kept apart**, because only one of them means
  there is nothing to do. The store would not answer (``measured: False``);
  no campaign is filed under this client at all (``no_campaigns``); every
  campaign filed is still waiting for a person to confirm the filing, so
  nothing reaches a figure yet (``all_pending``); or the campaigns are
  confirmed and simply spent nothing in the period (``nothing_this_period``).
  A card drawing the first three as the fourth is a report answering zero
  when it could not look.

* **Confirmed campaigns and nothing else reach a figure.** ``store.facts_for``
  already enforces that for every reader, and this one goes through
  ``client_view.staff_totals`` and ``client_view.pacing`` rather than
  summing fact rows itself -- two readings of one question drift the day
  either is edited, which is the failure ``client_view.pacing`` records
  about its own first draft.

Read-only. Nothing here writes a mapping, a link or a line.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from . import client_view, health, pacing as pacing_mod, quarantine, store

STATES = ("ok", "nothing_this_period", "all_pending", "no_campaigns")


def _num(v):
    """A Decimal (or anything numeric) as a float for JSON; None stays None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(name: str) -> str:
    try:
        from hub import client_key as ck
        return ck.normalise_name(name)
    except Exception:                                       # noqa: BLE001
        return str(name or "").strip().lower()


def _filed() -> list[tuple[str, str]]:
    """Every (key, display name) pair the store files something under: the
    campaign mappings and the budget lines. A link alone rides on one of
    those keys in practice, and the raw name is a candidate anyway."""
    out = [(r["client"], r.get("client_name") or "") for r in store.clients_with_campaigns()]
    out += [(b["client"], b.get("client_name") or "") for b in store.all_budget_lines()]
    return out


def candidate_keys(names, url: str = "", filed: list[tuple[str, str]] | None = None) -> list[str]:
    """Every key this client's rows may be filed under, most specific first.

    ``names`` is the Hub client name, or the names of a client group. The
    order matters only for which key the staff link opens: the first key
    that actually holds something wins, and the registry-derived key is the
    fallback for a client nothing is filed under yet.
    """
    names = [names] if isinstance(names, str) else list(names or [])
    names = [str(n or "").strip() for n in names]
    names = [n for n in names if n]
    out: list[str] = []

    def add(k):
        k = str(k or "").strip()
        if k and k not in out:
            out.append(k)

    try:
        from hub import client_key as ck
    except Exception:                                       # noqa: BLE001
        ck = None
    for n in names:
        if ck is not None:
            hit = None
            try:
                from hub import clients_registry
                hit = clients_registry.find_client(n)
            except Exception:                               # noqa: BLE001
                hit = None
            if hit:
                add(hit.get("key") or ck.client_key(hit.get("name") or n,
                                                    hit.get("url") or hit.get("domain") or ""))
            if url:
                add(ck.client_key(n, url))
            add(ck.name_key(n))
        # hub/proposal_adapters/reports.py files the link and the budget
        # lines under the display name itself.
        add(n)
    wanted = {_norm(n) for n in names}
    for key, cname in (filed if filed is not None else []):
        if key in out:
            continue
        if (cname and _norm(cname) in wanted) or _norm(key) in wanted:
            add(key)
    return out


def _merge_totals(per_key: list[list[dict]]) -> list[dict]:
    """Platform totals across every key, summed per platform. The billed
    figure is summed only where every part of it was priced; a platform
    with one unpriced part reads None rather than a smaller number."""
    by: dict[str, dict] = {}
    for rows in per_key:
        for t in rows:
            # store.platform_label, not client_view's: the client's page calls
            # Google "Paid Search" and this card names the platform beside a
            # campaign count that already says "Google Ads". One spelling.
            p = by.setdefault(t["platform"], {
                "platform": t["platform"], "label": store.platform_label(t["platform"]),
                "raw_spend": Decimal(0), "impressions": 0, "clicks": 0,
                "client_price": Decimal(0), "priced": True, "rule": t.get("rule")})
            p["raw_spend"] += Decimal(t["raw_spend"] or 0)
            p["impressions"] += int(t["impressions"] or 0)
            p["clicks"] += int(t["clicks"] or 0)
            if t.get("client_price") is None:
                p["priced"] = False
            elif p["priced"]:
                p["client_price"] += Decimal(t["client_price"])
    out = []
    for p in sorted(by.values(), key=lambda r: -r["raw_spend"]):
        imps = p["impressions"]
        rule = p.get("rule") or {}
        out.append({
            "platform": p["platform"], "label": p["label"],
            "raw_spend": _num(p["raw_spend"]), "impressions": imps, "clicks": p["clicks"],
            "ctr": (round(p["clicks"] / imps * 100, 2) if imps else None),
            "client_price": _num(p["client_price"]) if p["priced"] else None,
            "rule": ("markup" if "markup" in rule else "cpm" if "cpm" in rule else ""),
        })
    return out


def _pacing_rows(keys: list[str], today: date) -> list[dict]:
    out = []
    for k in keys:
        for r in client_view.pacing(k, today):
            band = r.get("band") or "on"
            out.append({
                "client": k, "product": r.get("product") or "",
                "platform_label": r.get("platform_label") or "",
                "band": band, "band_label": pacing_mod.BAND_LABELS.get(band, ("", band))[1],
                "pace": _num(r.get("pace")), "alert": bool(r.get("alert")),
                "trend_days": int(r.get("trend_days") or 1),
                "monthly_budget": _num(r.get("monthly_budget")),
                "spent": _num(r.get("actual_to_date")),
                "expected": _num(r.get("expected_to_date")),
                "days_remaining": int(r.get("days_remaining") or 0),
                "pending_campaigns": int(r.get("pending_campaigns") or 0),
            })
    order = {"stalled": 0, "under": 1, "over": 2, "unmapped": 3, "on": 4}
    out.sort(key=lambda r: (order.get(r["band"], 5), r["pace"] if r["pace"] is not None else 0))
    return out


def _feed_notes(platforms: set[str], today: date) -> list[dict] | None:
    """The feeds this client's campaigns ride on that are not current. None
    when the health reading itself would not answer."""
    if not platforms:
        return []
    try:
        f = health.feeds(today)
    except Exception:                                       # noqa: BLE001
        return None
    if not f.get("measured"):
        return None
    return [{"platform": p["platform"], "label": p["label"], "state": p["state"],
             "state_label": p["state_label"], "detail": p["detail"]}
            for p in f["platforms"] if p["platform"] in platforms and p["state"] != "ok"]


def _public_url(token: str) -> str:
    try:
        from hub.config import public_base_origin
        base = public_base_origin()
    except Exception:                                       # noqa: BLE001
        base = ""
    return f"{base}/reports/r/c/{token}"


def _staff_url(key: str) -> str:
    from urllib.parse import quote
    return "/reports/client/" + quote(key, safe="")


def summary(names, url: str = "", today: date | None = None) -> dict:
    """The card's whole payload for one client (or a client group's names).

    ``measured`` is False with a sentence when the store would not answer;
    otherwise ``state`` is one of ``STATES`` and every section is present.
    ``held`` and ``feeds`` are their own readings and are ``None`` -- not
    measured -- when only they could not be read, because a card should
    not lose the spend table over a quarantine ledger that blinked.
    """
    today = today or date.today()
    names = [names] if isinstance(names, str) else list(names or [])
    names = [str(n or "").strip() for n in names]
    names = [n for n in names if n]
    if not names:
        return {"measured": False, "error": "No client was named.", "state": "unread",
                "keys": [], "campaigns": {"confirmed": 0, "pending": 0}}
    try:
        filed = _filed()
        keys = candidate_keys(names, url, filed)
        campaigns = store.campaign_maps_for(keys)
        lines = store.budget_lines_for(keys, active_only=True)
        links = [l for l in (store.link_for_client(k) for k in keys) if l is not None]
        rng = client_view.period_range("mtd", today)
        per_key = []
        for k in keys:
            link = next((l for l in links if l.client == k), None)
            per_key.append(client_view.staff_totals(k, rng["start"], rng["end"], link))
        totals = _merge_totals(per_key)
        pacing_rows = _pacing_rows(keys, today)
    except Exception as exc:                                # noqa: BLE001
        return {"measured": False,
                "error": f"the ad-performance store could not be read ({type(exc).__name__})",
                "state": "unread", "keys": [], "campaigns": {"confirmed": 0, "pending": 0}}

    confirmed = [m for m in campaigns if not m.get("pending")]
    pending = [m for m in campaigns if m.get("pending")]
    filed_keys = {k for k, _ in filed} | {l.client for l in links}
    resolved = [k for k in keys if k in filed_keys]
    # The staff page is opened on the key that holds something; with nothing
    # filed yet, on the registry's own key (first in the list), so the page
    # a rep lands on is the one the first mapping will be filed under.
    primary = resolved[0] if resolved else keys[0]

    if not campaigns and not lines and not links:
        state = "no_campaigns"
    elif campaigns and not confirmed:
        state = "all_pending"
    elif confirmed and not totals:
        state = "nothing_this_period"
    elif not campaigns:
        # Budget lines or a link and no campaign at all -- the adapter's
        # shape: sold, set up, and nothing filed to pace it against.
        state = "no_campaigns"
    else:
        state = "ok"

    try:
        held = sum(len(quarantine.held_for_client(k)) for k in keys)
    except Exception:                                       # noqa: BLE001
        held = None

    link = links[0] if links else None
    link_out = None
    if link is not None:
        link_out = {"url": _public_url(link.token), "views": int(link.view_count or 0),
                    "last_viewed_at": store.iso(link.last_viewed_at),
                    "show_spend": bool(link.show_spend)}

    plat = {m["platform"] for m in confirmed}
    return {
        "measured": True, "error": "", "state": state,
        "keys": keys, "resolved": resolved, "primary": primary,
        "staff_url": _staff_url(primary),
        "unmapped_url": "/reports/unmapped",
        "campaigns": {"confirmed": len(confirmed), "pending": len(pending),
                      "platforms": sorted(store.platform_label(p) for p in plat)},
        "budget_lines": len(lines),
        "period": {"key": rng["key"], "label": rng["label"],
                   "start": rng["start"].isoformat(), "end": rng["end"].isoformat()},
        "totals": totals,
        "spend_total": _num(sum((Decimal(str(t["raw_spend"])) for t in totals), Decimal(0))),
        "billed_total": (_num(sum((Decimal(str(t["client_price"])) for t in totals), Decimal(0)))
                         if totals and all(t["client_price"] is not None for t in totals) else None),
        "pacing": pacing_rows,
        "pacing_url": "/reports/pacing",
        "held": held,
        "link": link_out,
        "feeds": _feed_notes(plat, today),
        "as_of": today.isoformat(),
    }
