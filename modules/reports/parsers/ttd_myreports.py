"""A Trade Desk MyReports CSV into ``AdPerfDaily`` rows.

The same file arrives two ways today: ``modules/reports/ttd.py`` downloads
it from a scheduled MyReports execution, and the platform emails an identical
export to the ad ops inbox. Both go through ``parse()`` here, so the two
paths cannot come to disagree about which column is spend.

## The columns

MyReports names its columns as the template does, and the standard
performance template is what both paths are built on. Every field this
parser needs is matched against a short alias list, case-insensitively and
ignoring punctuation, because the export spells a column one of several
ways depending on the template and the currency setting:

* ``Date`` (``Day``) -- the day, ``YYYY-MM-DD`` or ``MM/DD/YYYY``;
* ``Advertiser ID`` and ``Advertiser`` (``Advertiser Name``);
* ``Campaign ID`` and ``Campaign`` (``Campaign Name``);
* spend -- ``Advertiser Cost (USD)``, ``Advertiser Cost (Adv Currency)``,
  ``Advertiser Cost``, ``Partner Cost (USD)``: the first present wins, in
  that order, so the advertiser figure is preferred over the partner one;
* ``Impressions``; ``Clicks``;
* conversions -- ``Total Conversions``, else the sum of ``Post-Click
  Conversions`` and ``Post-View Conversions`` (``Click Conversions`` /
  ``View Conversions``);
* video -- ``Player Starts`` (``Video Starts``) into ``video_views`` and
  ``Player Completed Views`` (``Video Completes``, ``Completes``) into
  ``completes``, only where the template carries them.

A row missing the day, the advertiser id or the campaign id is dropped and
**counted** (``skipped``), never invented: a fact row is keyed on those
three and one without them files under nothing. A file whose header carries
none of the required columns is refused with the columns it did carry, so
the error names the template that was scheduled rather than reading as an
empty pull.

## Restatement

The Trade Desk restates for up to four weeks, so every execution's file
re-covers the trailing window and a day appears in many files. Rows are keyed
on (platform, advertiser, campaign, day) -- the fact table's own key -- and
``store.upsert_rows()`` replaces, so the newest file's figure for a day is
the one kept. Within ONE file two rows for the same key (a template broken
out by a dimension this parser does not read) are summed, which is the
only reading that leaves the day's total right.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

PLATFORM = "ttd"
SOURCE = "native"

# Alias lists, most specific first. Matched on the normalized header.
ALIASES = {
    "date": ("date", "day", "report date"),
    "account_id": ("advertiser id", "advertiserid"),
    "account_name": ("advertiser", "advertiser name"),
    "campaign_id": ("campaign id", "campaignid"),
    "campaign_name": ("campaign", "campaign name"),
    "spend": ("advertiser cost usd", "advertiser cost adv currency", "advertiser cost",
              "advertiser spend", "partner cost usd", "partner cost", "spend", "cost"),
    "impressions": ("impressions", "imps"),
    "clicks": ("clicks",),
    "conversions": ("total conversions", "conversions"),
    "conv_click": ("post click conversions", "click conversions"),
    "conv_view": ("post view conversions", "view conversions"),
    "video_views": ("player starts", "video starts", "video views"),
    "completes": ("player completed views", "video completes", "completes",
                  "player 100 views", "completed views"),
}

REQUIRED = ("date", "account_id", "campaign_id")


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(header or "").lower()).strip()


def columns(header: list[str]) -> dict[str, str]:
    """{field: the header actually present}, for every field one is."""
    normed = {_norm(h): h for h in header if h is not None}
    out = {}
    for field, names in ALIASES.items():
        for n in names:
            if n in normed:
                out[field] = normed[n]
                break
    return out


def _num(value) -> float:
    s = str(value if value is not None else "").strip().replace(",", "").replace("$", "")
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


def parse(text: str | bytes) -> dict:
    """``{"rows": [...], "skipped": n, "columns": {...}, "error": str}``.

    ``rows`` are ``store.upsert_rows()`` dicts, one per (advertiser,
    campaign, day), platform ``ttd`` and source ``native``.
    """
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
        conv = _num(get(row, "conversions")) if "conversions" in cols else (
            _num(get(row, "conv_click")) + _num(get(row, "conv_view")))
        fact = keyed.get(key)
        if fact is None:
            fact = keyed[key] = {
                "platform": PLATFORM, "source": SOURCE, "date": day,
                "account_id": acct, "campaign_id": camp,
                "campaign_name": str(get(row, "campaign_name") or "").strip(),
                "spend": 0.0, "impressions": 0, "clicks": 0, "conversions": 0.0,
                "extras": {"advertiser_name": str(get(row, "account_name") or "").strip()},
            }
            if "video_views" in cols:
                fact["video_views"] = 0
            if "completes" in cols:
                fact["completes"] = 0
        fact["spend"] += _num(get(row, "spend"))
        fact["impressions"] += int(_num(get(row, "impressions")))
        fact["clicks"] += int(_num(get(row, "clicks")))
        fact["conversions"] += conv
        if "video_views" in cols:
            fact["video_views"] += int(_num(get(row, "video_views")))
        if "completes" in cols:
            fact["completes"] += int(_num(get(row, "completes")))
        if not fact["campaign_name"] and get(row, "campaign_name"):
            fact["campaign_name"] = str(get(row, "campaign_name")).strip()
    rows = list(keyed.values())
    for f in rows:
        f["spend"] = round(f["spend"], 2)
        f["conversions"] = round(f["conversions"], 2)
    return {"rows": rows, "skipped": skipped, "columns": cols, "error": ""}
