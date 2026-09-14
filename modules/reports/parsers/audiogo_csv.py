"""An AudioGo campaign export (CSV from the UI) into ``AdPerfDaily`` rows.

The UI's export columns are not published, so every field is matched
against an alias list the way ``ttd_myreports.py`` matches its, case-
insensitively and ignoring punctuation. The aliases cover the spellings an
audio platform uses -- ``Listens``, ``Completed Listens``, ``LTR`` -- and
the ordinary ones (``Impressions``, ``Clicks``, ``Spend`` / ``Cost`` /
``Media Cost``). When a real export arrives, a header it does not match is
added to ``ALIASES`` and nothing else changes.

Rows land ``platform="audiogo"``, ``source="csv"``; listens land in the
fact table's ``completes`` column (completed listens preferred) and every
audio figure also rides in ``extras`` under its own name. A row missing the
day, the advertiser or the campaign is dropped and counted; a file carrying
none of the key columns is refused naming the columns it has.

``parse(text, platform=...)`` is also the reader behind the staff upload on
``/reports/`` for **any** platform: the ordinary columns (day, account,
campaign, spend, impressions, clicks, conversions) are what every
platform's export carries, and the audio ones are simply absent from the
others. One reader rather than a copy per platform, so a header alias
added for one export is matched for all of them; the platform is the
caller's and is checked against ``store.PLATFORMS`` before it lands.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

PLATFORM = "audiogo"
SOURCE = "csv"

ALIASES = {
    "date": ("date", "day", "report date", "delivery date"),
    "account_id": ("advertiser id", "advertiserid", "account id", "accountid", "brand id"),
    "account_name": ("advertiser", "advertiser name", "account", "account name", "brand"),
    "campaign_id": ("campaign id", "campaignid"),
    "campaign_name": ("campaign", "campaign name"),
    "spend": ("spend", "cost", "media cost", "media spend", "total spend", "amount spent", "budget spent"),
    "impressions": ("impressions", "imps", "ad impressions"),
    "clicks": ("clicks", "click throughs", "clickthroughs"),
    "listens": ("listens", "ad listens", "plays", "starts", "audio starts"),
    "completed_listens": ("completed listens", "completes", "completions", "full listens",
                          "audio completions", "listens completed"),
    "ltr": ("ltr", "listen through rate", "listen thru rate", "completion rate"),
    "conversions": ("conversions", "total conversions"),
}

REQUIRED = ("date", "account_id", "campaign_id")


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(header or "").lower()).strip()


def columns(header: list[str]) -> dict[str, str]:
    normed = {_norm(h): h for h in header if h is not None}
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in normed:
                out[field] = normed[n]
                break
    return out


def _num(value) -> float:
    s = str(value if value is not None else "").strip().replace(",", "").replace("$", "").replace("%", "")
    if not s or s in ("-", "--"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _day(value) -> date | None:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%y"):
        try:
            return datetime.strptime(s[:19] if "T" in s or " " in s else s, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse(text: str | bytes, platform: str = PLATFORM) -> dict:
    """``{"rows": [...], "skipped": n, "columns": {...}, "error": str}``.
    ``platform`` is what the rows land as; AudioGo by default."""
    platform = str(platform or PLATFORM).strip().lower() or PLATFORM
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    header = None
    for row in reader:
        if row and any(str(c).strip() for c in row):
            header = row
            break
    if header is None:
        return {"rows": [], "skipped": 0, "columns": {}, "error": "The file is empty."}
    cols = columns(header)
    missing = [f for f in REQUIRED if f not in cols]
    if missing:
        return {"rows": [], "skipped": 0, "columns": cols,
                "error": ("The file does not carry " + ", ".join(missing)
                          + " -- its columns are: " + ", ".join(str(h) for h in header)[:400])}
    idx = {f: header.index(h) for f, h in cols.items()}

    def get(row, field):
        i = idx.get(field)
        return row[i] if i is not None and i < len(row) else None

    keyed: dict[tuple, dict] = {}
    skipped = 0
    for row in reader:
        if not row or not any(str(c).strip() for c in row):
            continue
        day = _day(get(row, "date"))
        acct = str(get(row, "account_id") or "").strip()
        camp = str(get(row, "campaign_id") or "").strip()
        if day is None or not acct or not camp:
            skipped += 1
            continue
        key = (acct, camp, day)
        fact = keyed.get(key)
        if fact is None:
            fact = keyed[key] = {
                "platform": platform, "source": SOURCE, "date": day,
                "account_id": acct, "campaign_id": camp,
                "campaign_name": str(get(row, "campaign_name") or "").strip(),
                "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0,
                "extras": {"advertiser_name": str(get(row, "account_name") or "").strip()},
            }
        fact["spend"] += _num(get(row, "spend"))
        fact["impressions"] += int(_num(get(row, "impressions")))
        fact["clicks"] += int(_num(get(row, "clicks")))
        fact["conversions"] += _num(get(row, "conversions")) if "conversions" in cols else 0
        for k in ("listens", "completed_listens"):
            if k in cols:
                fact["extras"][k] = int(fact["extras"].get(k, 0) + _num(get(row, k)))
        if "ltr" in cols:
            fact["extras"]["ltr"] = _num(get(row, "ltr"))
        if not fact["campaign_name"] and get(row, "campaign_name"):
            fact["campaign_name"] = str(get(row, "campaign_name")).strip()
    rows = list(keyed.values())
    for f in rows:
        f["spend"] = round(f["spend"], 2)
        f["conversions"] = round(f["conversions"], 2)
        ex = f["extras"]
        if "completed_listens" in ex:
            f["completes"] = ex["completed_listens"]
        elif "listens" in ex:
            f["completes"] = ex["listens"]
    return {"rows": rows, "skipped": skipped, "columns": cols, "error": ""}
