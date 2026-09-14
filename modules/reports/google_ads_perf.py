"""The native Google Ads pull: daily campaign figures for every client account.

Reads through ``modules/ads_builder/google_ads`` -- the one Google Ads client
in this Hub, with the OAuth refresh, the developer token, the MCC expansion
and the per-call quota note already on it -- rather than a second one. One
GAQL query per client account under the manager, over the trailing
``DAYS`` days, and the rows land as ``platform="google"``, ``source="native"``
so the provider's copy of the same day defers to them (``normalize.py``'s
native-wins rule).

**Not connected is a sentence, never a stack trace.** On this deployment
Google Ads has no approved developer token and no refresh token yet, so the
ordinary answer of ``pull()`` today is ``{"error": NOT_CONNECTED}``: the
same words ``google_ads.NotConnected`` carries, pointing at the settings
screen where Connect Google Ads lives. ``/reports/`` prints it. The quota is
asked before the loop the way ``monitoring.sweep()`` asks it -- a headroom
nobody published is *not measured* and does not stop a pull; an exhausted
one does, by name.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from . import store

log = logging.getLogger(__name__)

DAYS = 30
NOT_CONNECTED = "not connected — Connect Google Ads in /tools/ads/settings"


def gaql(start: date, end: date) -> str:
    """The query, one place: campaign, its channel type, day, the five
    metrics the fact table names, and the video completion rate. Cost
    comes back in micros and is divided by ``google_ads.micros()`` on the
    way in, nowhere else.

    ``campaign.advertising_channel_type`` is what tells a YouTube buy from
    a search one: without it every Google Ads campaign whose name carries
    no product segment files as Paid Search, and a TrueView campaign reads
    as search on the client's own page. ``metrics.video_quartile_p100_rate``
    is the share of impressions watched to the end; times impressions it
    is the completes figure the "Video ads completed" tile draws for the
    Trade Desk, and Google publishes no count of its own."""
    return (
        "SELECT campaign.id, campaign.name, campaign.advertising_channel_type, "
        "segments.date, "
        "metrics.cost_micros, metrics.impressions, metrics.clicks, "
        "metrics.conversions, metrics.video_trueview_views, metrics.video_quartile_p100_rate "
        "FROM campaign "
        f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
        "AND campaign.status != 'REMOVED'"
    )


def _client():
    """(google_ads, ads_store) or (None, why)."""
    try:
        from modules.ads_builder import google_ads, store as ads_store
    except Exception as exc:                            # noqa: BLE001
        return None, f"Smart 1 Ads is not importable ({type(exc).__name__})"
    return google_ads, ads_store


def status() -> dict:
    """What /reports/ prints: google_ads.connection_status() with the
    module's own sentence beside it."""
    ga, ads_store = _client()
    if ga is None:
        return {"connected": False, "configured": False, "line": f"Google Ads: {ads_store}",
                "missing": [], "last_pull": None, "last_error": ""}
    try:
        st = ga.connection_status(ads_store)
    except Exception as exc:                            # noqa: BLE001
        st = {"connected": False, "configured": False, "missing": [],
              "error": f"{type(exc).__name__}"}
    sync = store.sync_status().get("google") or {}
    native = sync if sync.get("source") == "native" else {}
    ready = bool(st.get("deploy_ready"))
    if ready:
        line = "Google Ads: connected, last pull " + (native.get("last_run_at") or "never")
        if native.get("error"):
            line += f" -- {native['error']}"
    else:
        line = "Google Ads: " + NOT_CONNECTED
        if st.get("missing"):
            line += " (" + ", ".join(st["missing"]) + " unset)"
    return {"connected": ready, "configured": bool(st.get("configured")),
            "missing": st.get("missing") or [], "line": line,
            "last_pull": native.get("last_run_at"), "last_rows": native.get("rows"),
            "last_error": native.get("error") or ""}


def _facts(account_id: str, rows: list[dict], micros) -> list[dict]:
    out = []
    for row in rows:
        c = row.get("campaign") or {}
        m = row.get("metrics") or {}
        seg = row.get("segments") or {}
        day = seg.get("date")
        cid = str(c.get("id") or "")
        if not day or not cid:
            continue
        imps = int(float(m.get("impressions") or 0))
        views = int(float(m.get("videoTrueviewViews", m.get("videoViews")) or 0))
        channel = str(c.get("advertisingChannelType") or "").strip().upper()
        fact = {
            "platform": "google", "source": "native", "date": day,
            "account_id": account_id, "campaign_id": cid,
            "campaign_name": c.get("name") or "",
            "spend": round(micros(m.get("costMicros")), 2),
            "impressions": imps,
            "clicks": int(float(m.get("clicks") or 0)),
            "conversions": float(m.get("conversions") or 0),
            "video_views": views,
        }
        if channel:
            # Carried on the row, where the auto-mapper and the unmapped
            # queue read it: the channel is a fact about the campaign that
            # the campaign's name does not have to carry.
            fact["extras"] = {"channel_type": channel}
        # Completes = the p100 rate x impressions, and only for a campaign
        # that serves video (the VIDEO channel, or a row carrying views).
        # A search campaign's rate is 0, and "0 completes" on it would be a
        # measurement of a metric that does not apply -- absent is not
        # measured, which is what the tile gates on.
        rate = m.get("videoQuartileP100Rate")
        if rate not in (None, "") and (channel == "VIDEO" or views > 0):
            try:
                fact["completes"] = int(round(float(rate) * imps))
            except (TypeError, ValueError):
                # A rate Google sent that is not a number costs the
                # completes figure and never the row: the key is left
                # off, which the tile reads as not measured rather than
                # as a nought.
                pass
        out.append(fact)
    return out


def pull(days: int = DAYS, today: date | None = None) -> dict:
    """Every client account under the manager, the trailing ``days`` days."""
    out = {"ok": False, "rows": 0, "accounts": 0, "errors": {}, "error": "", "skipped": []}
    ga, ads_store = _client()
    if ga is None:
        out["error"] = ads_store
        return out
    try:
        st = ga.connection_status(ads_store)
    except Exception as exc:                            # noqa: BLE001
        out["error"] = f"could not read the Google Ads connection ({type(exc).__name__})"
        return out
    if not st.get("deploy_ready"):
        # Clean, and not recorded on the watermark: nothing was attempted, so
        # a provider row for google is still the current one.
        out["error"] = NOT_CONNECTED
        return out

    try:
        from hub import quotas
        head = quotas.ads_headroom()
        if head.get("measured") and head.get("exhausted"):
            out["error"] = ("today's Google Ads operation budget is used up "
                            f"({head.get('used_today')} of {head.get('daily_quota')}); "
                            "the pull will run on the next tick")
            store.record_sync("google", rows=0, error=out["error"], source="native")
            return out
    except Exception:                                   # noqa: BLE001 - no ceiling published
        pass

    today = today or date.today()
    start = today - timedelta(days=int(days))
    query = gaql(start, today)
    try:
        accounts = ga.list_client_accounts(ads_store)
    except Exception as exc:                            # noqa: BLE001
        msg = getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"
        out["error"] = f"could not list the client accounts: {msg}"[:400]
        store.record_sync("google", rows=0, error=out["error"], source="native")
        return out

    total = 0
    for acct in accounts:
        if acct.get("is_manager") or acct.get("error"):
            out["skipped"].append(acct.get("id") or "")
            continue
        cid = str(acct.get("id") or "")
        try:
            rows = ga.search(cid, query, store=ads_store, login_customer_id=acct.get("manager_id"))
            facts = _facts(cid, rows, ga.micros)
            total += store.upsert_rows(facts) if facts else 0
            out["accounts"] += 1
        except Exception as exc:                        # noqa: BLE001 - one account, not the pull
            out["errors"][cid] = (getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}")[:300]
    out["rows"] = total
    out["ok"] = not out["errors"] or out["accounts"] > 0
    err = ""
    if out["errors"]:
        err = f"{len(out['errors'])} account(s) refused: " + "; ".join(
            f"{k}: {v}" for k, v in list(out["errors"].items())[:3])
    store.record_sync("google", rows=total, error=err[:2000], source="native")
    return out
