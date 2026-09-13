"""Whether the feeds are working, said where somebody will read it.

Every platform's watermark and its newest fact row were already on
``/reports/`` -- and only there. A native pull that had been failing for
three days, or a provider that had quietly stopped delivering new days, was
visible to whoever happened to open that one page, which is the failure this
Hub names about the scheduler panel: the thing the job refreshes is stale
whether it raised or simply never ran, and the only way to notice was to open
the page and do the arithmetic per row.

``feeds()`` is one reading of a platform's state, read by three screens:

* ``/status`` (``status_row()``) -- a warn when any feed is failing or stale,
  and an **error** when the module is on the SQLite fallback in production,
  because that file lives on the data disk that is wiped on every deploy;
* the dashboard (``scoreboard()``) -- the counts where people already look,
  the shape ``hub/social_status.py`` and ``hub/sales_status.py`` use;
* the module's own index -- the same state per platform, as a pill.

Four states, because they send somebody to four different places. **never**
is a platform nothing has ever written -- on a fresh deployment that is most
of them, and it is a fact rather than a fault. **failing** is a watermark
whose last run recorded an error, whatever its age. **stale** is a feed whose
newest fact day is older than ``STALE_DAYS`` although it has delivered
before: the platform reports through yesterday, so a lag past three days is
the feed having stopped, with no error anywhere to say so. **ok** is the
rest. A platform the pull skips as not configured records nothing and reads
as never, which is right: nothing was attempted.

Nothing here may raise. Every reader answers ``measured: False`` with a
sentence rather than an exception, because a health check that takes the
status page down is the confident wrong answer wearing a stethoscope.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone

from . import store

# A feed whose newest day is older than this, having delivered before, has
# stopped. Three days rather than one because every platform here reports
# through yesterday and several restate for a day or two after.
STALE_DAYS = 3

STATE_LABELS = {
    "ok": "Current", "failing": "Failing", "stale": "Stale", "never": "Never synced",
}


def in_production() -> bool:
    """Render sets ``RENDER=true`` on every service; a checkout does not."""
    return bool((os.environ.get("RENDER") or "").strip()
                or (os.environ.get("RENDER_SERVICE_ID") or "").strip())


def binding_problem() -> str:
    """The sentence when the reports store is somewhere it must not be in
    production, or ``""``. The SQLite fallback is a file on the data disk,
    which Render does not back up and which a plan change or a redeploy
    hands back empty -- so every fact row, mapping and budget line would go
    with it, and nothing on any screen would have said the module was there."""
    if not in_production():
        return ""
    if store.binding() == "sqlite":
        return ("Reports are on a SQLite file on the data disk, which is wiped on every "
                "deploy: set REPORTS_DATABASE_URL (or DATABASE_URL) on Render.")
    return ""


def _age_days(latest_date: str | None, today: date) -> int | None:
    if not latest_date:
        return None
    try:
        return (today - date.fromisoformat(latest_date[:10])).days
    except ValueError:
        return None


def feeds(today: date | None = None) -> dict:
    """Every platform's state, plus the counts.

    ``{"measured", "platforms": [...], "ok", "failing", "stale", "never",
    "binding", "binding_problem", "note"}``. A store that will not answer is
    ``measured: False`` with the reason, never an empty, healthy-looking list.
    """
    today = today or date.today()
    try:
        rows = store.platform_status()
    except Exception as exc:                                # noqa: BLE001
        return {"measured": False, "platforms": [], "ok": 0, "failing": 0, "stale": 0,
                "never": 0, "binding": store.binding(), "binding_problem": binding_problem(),
                "note": f"the reports store could not be read ({type(exc).__name__})"}
    out = []
    for r in rows:
        age = _age_days(r.get("latest_date"), today)
        if r.get("sync_error"):
            state = "failing"
            detail = str(r["sync_error"])[:200]
        elif not r.get("synced_at") and not r.get("sync_run_at"):
            state = "never"
            detail = "nothing has written this platform yet"
        elif age is not None and age > STALE_DAYS:
            state = "stale"
            detail = f"newest day is {r['latest_date']}, {age} days ago"
        else:
            state = "ok"
            detail = (f"through {r['latest_date']}" if r.get("latest_date") else "synced, no rows yet")
        out.append({"platform": r["platform"], "label": r["label"], "state": state,
                    "state_label": STATE_LABELS[state], "detail": detail,
                    "latest_date": r.get("latest_date"), "age_days": age,
                    "synced_at": r.get("synced_at"), "sync_run_at": r.get("sync_run_at"),
                    "source": r.get("sync_source")})
    counts = {s: sum(1 for p in out if p["state"] == s) for s in STATE_LABELS}
    return {"measured": True, "platforms": out, **counts,
            "binding": store.binding(), "binding_problem": binding_problem(), "note": ""}


def _held() -> int | None:
    """Rows held in quarantine, or None when the ledger cannot be read."""
    try:
        from . import quarantine
        return quarantine.counts()["held"]
    except Exception:                                       # noqa: BLE001
        return None


def _drifting() -> list[dict] | None:
    """Platform-months whose fact table does not add up to the platform's
    own figure, or None when the ledger cannot be read."""
    try:
        from . import reconcile
        return reconcile.drifting()
    except Exception:                                       # noqa: BLE001
        return None


def _alerts() -> int | None:
    """Lines alerting on the latest pacing run, or None when it cannot be read."""
    try:
        return sum(1 for r in store.latest_snapshots() if r.get("alert"))
    except Exception:                                       # noqa: BLE001
        return None


def status_row() -> tuple[str, str]:
    """``(state, message)`` for ``/api/status``'s ``add()``.

    ``error`` for the SQLite fallback in production; ``warn`` when a feed is
    failing or stale; ``skipped`` when nothing has ever synced (a module that
    has not been switched on is not a fault); ``ok`` otherwise. The message
    names the platforms, because a warn that says "2 feeds" sends somebody to
    a page to find out which two.
    """
    f = feeds()
    if f.get("binding_problem"):
        return "error", f["binding_problem"]
    if not f["measured"]:
        return "warn", f"Reports feeds could not be checked: {f['note']}."
    failing = [p["label"] for p in f["platforms"] if p["state"] == "failing"]
    stale = [p["label"] for p in f["platforms"] if p["state"] == "stale"]
    held = _held()
    drift = _drifting() or []
    if failing or stale or held or drift:
        parts = []
        if failing:
            parts.append("failing: " + ", ".join(failing))
        if stale:
            parts.append(f"stale (no new day in {STALE_DAYS}+ days): " + ", ".join(stale))
        if drift:
            # The fact table's month and the platform's own do not agree:
            # what a client reads is smaller or larger than what the
            # platform will invoice, and nothing else on the page says so.
            parts.append("not adding up to the platform's own month: "
                         + ", ".join(f"{d['label']} {d['month']} ({d['drift_pct']}%)" for d in drift[:4])
                         + (f" and {len(drift) - 4} more" if len(drift) > 4 else "")
                         + " (/reports/reconcile)")
        if held:
            # A held row is a day missing from somebody's page until a
            # person decides on it, which is work rather than a fault --
            # and work nobody is told about is work nobody does.
            parts.append(f"{held} row{'s' if held != 1 else ''} held in quarantine "
                         "(/reports/quarantine)")
        return "warn", "; ".join(parts) + " — see /reports/."
    if f["ok"] == 0:
        return "skipped", "No ad-performance feed has synced yet — nothing is being reported."
    return "ok", (f"{f['ok']} feed{'s' if f['ok'] != 1 else ''} current"
                  + (f", {f['never']} never synced" if f["never"] else "") + ".")


def scoreboard() -> dict:
    """The dashboard's reading: feeds by state, what is waiting on a person
    (campaigns filed under nobody, auto-mappings waiting for a confirmation
    before they reach a client's page, rows held in quarantine), and what is
    alerting.
    Every figure opens the rows behind it, and every zero says which kind of
    zero it is.

    Only reads: the watermarks, the fact table's latest day per platform, the
    unmapped and pending counts and the latest pacing snapshot. Nothing here
    reaches a provider, so it costs a page load a few small queries and never
    a call.
    """
    f = feeds()
    if f.get("binding_problem"):
        return {"measured": False, "error": f["binding_problem"], "url": "/reports/"}
    if not f["measured"]:
        return {"measured": False, "error": f["note"], "url": "/reports/"}
    try:
        unmapped = store.unmapped_count()
    except Exception:                                       # noqa: BLE001
        unmapped = None
    try:
        pending = store.pending_count()
    except Exception:                                       # noqa: BLE001
        pending = None
    held = _held()
    drift = _drifting()
    alerts = _alerts()
    ever = f["ok"] + f["failing"] + f["stale"]
    counts = {"ok": f["ok"], "failing": f["failing"], "stale": f["stale"], "never": f["never"],
              "unmapped": unmapped, "pending": pending, "held": held,
              "drift": len(drift) if drift is not None else None, "alerts": alerts}
    if ever == 0:
        line = ("No feed has synced yet: the native pulls need their keys and the provider "
                "its first table. Nothing is being reported to any client.")
        empty = "no_feeds"
    else:
        bits = [f"{f['ok']} of {ever} feed{'s' if ever != 1 else ''} current"]
        if f["failing"]:
            bits.append(f"{f['failing']} failing")
        if f["stale"]:
            bits.append(f"{f['stale']} stale")
        if unmapped:
            bits.append(f"{unmapped} campaign{'s' if unmapped != 1 else ''} filed under nobody")
        elif unmapped == 0:
            bits.append("every campaign filed")
        if pending:
            bits.append(f"{pending} filed from a name and waiting for confirmation")
        if held:
            bits.append(f"{held} row{'s' if held != 1 else ''} held in quarantine")
        if drift:
            bits.append(f"{len(drift)} platform-month{'s' if len(drift) != 1 else ''} not adding up "
                        "to the platform's own total")
        if alerts:
            bits.append(f"{alerts} line{'s' if alerts != 1 else ''} pacing off for three days")
        elif alerts == 0:
            bits.append("nothing pacing off")
        line = "; ".join(bits) + "."
        empty = ""
    return {
        "measured": True, "counts": counts, "empty": empty, "line": line,
        "binding": f["binding"],
        "urls": {"feeds": "/reports/", "unmapped": "/reports/unmapped",
                 "pending": "/reports/unmapped#pending", "held": "/reports/quarantine",
                 "drift": "/reports/reconcile",
                 "alerts": "/reports/pacing?band=under", "pacing": "/reports/pacing"},
        "platforms": [{"label": p["label"], "state": p["state"], "detail": p["detail"]}
                      for p in f["platforms"] if p["state"] in ("failing", "stale")]
                     + [{"label": f"{d['label']} {d['month']}", "state": "drift",
                         "detail": (f"our ${d['ours']:,.2f} against the platform's ${d['theirs']:,.2f}, "
                                    f"{d['drift_pct']}% apart")}
                        for d in (drift or [])],
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
