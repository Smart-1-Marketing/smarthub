"""Form submissions from Smart 1 Suite, compared against the prior period.

A client's Suite account may have twenty forms and three that anyone actually
fills in. Listing all twenty buries the signal, so **forms with no submissions
in the period are left out entirely** — an empty row tells you nothing you
couldn't infer from its absence.

The comparison is the point. "14 submissions" is a number; "14, down 30% from
20" is something you act on. Every period is compared against the equivalent
one before it — this month against last month, this quarter against last
quarter — so the shapes are alike. Comparing a part-finished month against a
whole one would show a fall every time, which is the classic way this kind of
panel misleads.

## Endpoints

    GET /forms/               list forms for a location
    GET /forms/submissions    submissions, filtered by form and date range

Both need the `forms.readonly` scope on the Private Integration Token.
"""
from __future__ import annotations

import os
from calendar import monthrange
from datetime import date, timedelta

BASE = "https://services.leadconnectorhq.com"
VERSION = "2021-07-28"
TIMEOUT = 25

PERIODS = ("this_month", "last_month", "this_quarter", "this_year", "last_30")


def _token() -> str:
    for name in ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"):
        v = (os.environ.get(name) or "").strip().strip('"').strip("'")
        if v and v != "pit-...":
            return v
    return ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Version": VERSION,
            "Accept": "application/json"}


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(d: date) -> date:
    return d.replace(day=monthrange(d.year, d.month)[1])


def window(period: str, today: date | None = None) -> tuple[date, date, date, date, str]:
    """(start, end, prev_start, prev_end, label) for a named period.

    The previous window is always the same *kind* of period, not simply the
    same number of days back. Last month against this month compares like
    with like even though one is 28 days and the other 31.
    """
    t = today or date.today()

    if period == "last_month":
        end = _month_start(t) - timedelta(days=1)
        start = _month_start(end)
        p_end = start - timedelta(days=1)
        return start, end, _month_start(p_end), p_end, start.strftime("%B %Y")

    if period == "this_quarter":
        q = (t.month - 1) // 3
        start = date(t.year, q * 3 + 1, 1)
        end = t
        p_start = (date(start.year - 1, 10, 1) if q == 0
                   else date(start.year, (q - 1) * 3 + 1, 1))
        # Same number of days INTO the previous quarter, not the whole of it.
        # 50 days of this quarter against 91 of the last would report a fall
        # every single time — the panel would be wrong more often than right.
        p_end = min(p_start + timedelta(days=(end - start).days),
                    start - timedelta(days=1))
        return start, end, p_start, p_end, f"Q{q + 1} {t.year}"

    if period == "this_year":
        start, end = date(t.year, 1, 1), t
        p_start = date(t.year - 1, 1, 1)
        # Year to date against the same span last year, for the same reason.
        p_end = min(p_start + timedelta(days=(end - start).days),
                    date(t.year - 1, 12, 31))
        return start, end, p_start, p_end, f"{t.year} to date"

    if period == "last_30":
        start, end = t - timedelta(days=29), t
        return start, end, start - timedelta(days=30), start - timedelta(days=1), "Last 30 days"

    # default: this month so far, against the same number of days last month.
    start, end = _month_start(t), t
    p_start = _month_start(start - timedelta(days=1))
    # Same day-count, so a part-month isn't compared against a whole one.
    p_end = min(p_start + timedelta(days=(end - start).days), _month_end(p_start))
    return start, end, p_start, p_end, t.strftime("%B %Y")


def _get(path: str, params: dict) -> dict:
    import requests
    try:
        r = requests.get(f"{BASE}{path}", headers=_headers(), params=params,
                         timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
    if r.status_code in (401, 403):
        raise RuntimeError(
            "Smart 1 Suite rejected the request. The Private Integration "
            "Token probably lacks the forms.readonly scope.")
    if not r.ok:
        raise RuntimeError(f"Smart 1 Suite returned HTTP {r.status_code}.")
    try:
        return r.json()
    except ValueError:
        return {}


def forms(location_id: str) -> list[dict]:
    data = _get("/forms/", {"locationId": location_id, "limit": 100, "skip": 0})
    return data.get("forms") or data.get("data") or []


def _count(location_id: str, form_id: str, start: date, end: date) -> int:
    """Submissions for one form in one window.

    Counts rather than collecting: the card shows totals, and pulling every
    submission body for twenty forms across two periods would be slow and
    would drag personal data into a page that doesn't display it.
    """
    total, page = 0, 1
    while page <= 20:
        data = _get("/forms/submissions", {
            "locationId": location_id, "formId": form_id,
            "startAt": start.isoformat(), "endAt": end.isoformat(),
            "page": page, "limit": 100})
        rows = data.get("submissions") or data.get("data") or []
        total += len(rows)
        meta = data.get("meta") or {}
        if meta.get("total") is not None and page == 1:
            return int(meta["total"])       # trust the API's own count
        if len(rows) < 100:
            break
        page += 1
    return total


def _delta(now: int, before: int) -> dict:
    """Direction and percentage change, honest about a zero baseline."""
    if before == 0:
        return {"direction": "up" if now else "flat",
                "percent": None,
                "text": f"{now} vs 0" if now else "none either period",
                "note": "No submissions in the previous period, so a "
                        "percentage would be meaningless."}
    pct = round((now - before) / before * 100)
    return {"direction": "up" if pct > 0 else "down" if pct < 0 else "flat",
            "percent": abs(pct),
            "text": f"{'+' if pct > 0 else ''}{pct}% vs {before}"}


def summary(client: str, location_id: str = "", period: str = "this_month") -> dict:
    """Forms with submissions in the period, against the one before it."""
    loc = (location_id or os.environ.get("GHL_COMPANY_ID")
           or os.environ.get("SUITE_COMPANY_ID") or "").strip()
    if not _token() or not loc:
        return {"error": "No Smart 1 Suite token or location id for this client.",
                "forms": [], "period": period}

    start, end, p_start, p_end, label = window(period)
    try:
        all_forms = forms(loc)
    except RuntimeError as exc:
        return {"error": str(exc), "forms": [], "period": period}

    rows, skipped = [], 0
    for f in all_forms:
        fid = f.get("id") or f.get("_id")
        if not fid:
            continue
        try:
            now = _count(loc, fid, start, end)
        except RuntimeError:
            continue
        if not now:
            # A form nobody filled in is noise, not information.
            skipped += 1
            continue
        try:
            before = _count(loc, fid, p_start, p_end)
        except RuntimeError:
            before = 0
        rows.append({"id": fid, "name": f.get("name") or "(unnamed form)",
                     "submissions": now, "previous": before,
                     **_delta(now, before)})

    rows.sort(key=lambda r: -r["submissions"])
    total_now = sum(r["submissions"] for r in rows)
    total_before = sum(r["previous"] for r in rows)
    return {
        "client": client, "period": period, "label": label,
        "range": f"{start.isoformat()} to {end.isoformat()}",
        "compared_to": f"{p_start.isoformat()} to {p_end.isoformat()}",
        "forms": rows,
        "total": total_now,
        "total_previous": total_before,
        **{f"total_{k}": v for k, v in _delta(total_now, total_before).items()},
        "with_submissions": len(rows),
        "no_submissions": skipped,
        "note": (f"{skipped} form(s) had no submissions this period and are "
                 f"left out." if skipped else ""),
    }
