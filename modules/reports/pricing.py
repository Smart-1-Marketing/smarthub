"""The spend rule: what a client is shown for a platform's delivery.

One function, ``client_price()``, and it is the only place raw spend is
turned into a client-facing figure. Every reader -- the page's data.json,
the PDF, the staff preview -- goes through it, so the number on the screen,
the number in the document and the number a rep quotes cannot come to
disagree.

The rule, in order:

1. the link's own ``markup_json[platform]`` -- a per-client override;
2. else the ``PlatformMarkup`` row for the platform;
3. else **None**.

A markup bills ``raw_spend * (1 + markup)``; a fixed CPM bills
``impressions / 1000 * cpm`` and ignores raw spend entirely. **None means no
figure is shown for that platform** -- delivery only -- and the caller writes
``audit.log(..., action="reports_markup_missing")`` so somebody sets one.
Never raw spend: what Smart 1 pays a platform is not what the client is
billed, and a page that fell back to the raw number would put the agency's
cost in front of the client under the word "Investment". That is the one
mistake here that cannot be undone by editing a row.

The figures are Decimal, half-up at cents, and the word for them anywhere a
client reads is *Investment*.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import store

CENT = Decimal("0.01")


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def rule_for(platform: str, link) -> dict | None:
    """Which rule prices this platform for this link: ``{"markup": Decimal}``,
    ``{"cpm": Decimal}``, or None. ``link`` may be a ReportLink row, its
    ``as_dict()``, or None (platform-wide rule only)."""
    overrides = {}
    if link is not None:
        overrides = (link.get("markup_json") if isinstance(link, dict)
                     else getattr(link, "markups", None)) or {}
    own = overrides.get(platform) if isinstance(overrides, dict) else None
    if isinstance(own, dict):
        m, c = _dec(own.get("markup")), _dec(own.get("cpm"))
        if m is not None:
            return {"markup": m, "source": "link"}
        if c is not None:
            return {"cpm": c, "source": "link"}
    row = _platform_rule(platform)
    if row:
        return row
    return None


def _platform_rule(platform: str) -> dict | None:
    db = store.SessionLocal()
    try:
        row = db.get(store.PlatformMarkup, platform)
        if row is None:
            return None
        if row.markup is not None:
            return {"markup": Decimal(row.markup), "source": "platform"}
        if row.cpm is not None:
            return {"cpm": Decimal(row.cpm), "source": "platform"}
        return None
    finally:
        db.close()


def client_price(platform: str, raw_spend, impressions, link) -> Decimal | None:
    """The client's Investment for one platform over a period, or None.

    ``raw_spend`` and ``impressions`` are the period totals for that
    platform; ``link`` is the ReportLink (row or dict) whose overrides apply.
    """
    rule = rule_for(platform, link)
    if rule is None:
        return None
    if "markup" in rule:
        spend = _dec(raw_spend) or Decimal(0)
        figure = spend * (Decimal(1) + rule["markup"])
    else:
        imps = _dec(impressions) or Decimal(0)
        figure = imps / Decimal(1000) * rule["cpm"]
    return figure.quantize(CENT, rounding=ROUND_HALF_UP)
