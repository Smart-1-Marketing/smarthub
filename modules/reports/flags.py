"""The flag rules: which movements in a client's numbers are worth saying.

**Computed here, never by the model.** Ask SmartHub reads these tools and
writes the sentence a person reads. A model asked to decide what counts as a
significant drop will decide differently on two runs of one question, and a
figure it decided about is a figure this Hub did not measure --
``hub/audit_summary.py``'s house rule. So the tool computes the flag, hands
the model a plain-English ``text`` to lift verbatim, and the model may
explain *why* a flag fired; it may not change *whether* one did.

The thresholds are module constants rather than numbers buried in a
comparison, and every payload that carries flags carries ``thresholds()``
beside them, so an answer can quote the rule it applied: "CPA moved more than
the 15% this Hub flags at" rather than "CPA moved a lot".

Every rule has a floor, and the floors are the point. A campaign whose CTR
"fell 80%" on 12 impressions has not told anybody anything, and a queue full
of those is a queue nobody reads. Pure functions, no I/O: the fact reading
happens in ``mcp_gateway/v2_tools.py`` and the GA4 breakdown reuses the same
constants rather than picking its own.
"""
from __future__ import annotations

# A move in any headline metric worth remarking on at all.
METRIC_MOVE_PCT = 15.0

# A campaign's click-through rate falling, with enough impressions on BOTH
# sides of the comparison for the rate to mean anything.
CTR_DROP_PCT = 20.0
CTR_MIN_IMPRESSIONS = 500

# A campaign paying multiples of what the account pays per conversion, with
# enough conversions that the multiple is not one lucky click.
CPA_MULTIPLE = 2.0
CPA_MIN_CONVERSIONS = 3

# Spend with nothing to show for it. A house constant: below this, a campaign
# with no conversions is a campaign that has not had its chance yet.
NO_CONV_MIN_SPEND = 100.0

# The pacing reads. These two mirror the board's own bands rather than
# re-deriving them -- ``pacing.band_for`` owns under/on/over -- and add the
# half the board cannot see: whether the line that is under-spending is one
# we would WANT to spend more on.
UNDER_UTILIZATION_PCT = 80.0
OVER_PACE = 1.15

# GA4: the share of sessions arriving with no attribution at all, above which
# the answer is a tagging problem rather than an audience insight.
DIRECT_NONE_SHARE_PCT = 40.0

# The metrics a totals-or-platform move is judged on, and how each reads in a
# sentence. Order is the order flags come out in.
MOVE_METRICS = (
    ("spend", "Spend", "money"),
    ("impressions", "Impressions", "count"),
    ("clicks", "Clicks", "count"),
    ("conversions", "Conversions", "count"),
    ("ctr", "CTR", "rate"),
    ("cpc", "Average CPC", "money"),
    ("cpa", "Cost per conversion", "money"),
    ("conv_rate", "Conversion rate", "rate"),
)

# Which direction is good news. Paying less per click or per conversion is an
# improvement; everything else rising is. Said in words so the sentence
# handed to the model reads as a person would say it -- "CPA improved 18.4%"
# rather than "CPA moved -18.4%", which a reader has to stop and decode.
LOWER_IS_BETTER = ("cpc", "cpa")


def thresholds() -> dict:
    """Every constant a flag was judged against, for the payload to echo."""
    return {
        "metric_move_pct": METRIC_MOVE_PCT,
        "ctr_drop_pct": CTR_DROP_PCT,
        "ctr_min_impressions": CTR_MIN_IMPRESSIONS,
        "cpa_multiple": CPA_MULTIPLE,
        "cpa_min_conversions": CPA_MIN_CONVERSIONS,
        "no_conv_min_spend": NO_CONV_MIN_SPEND,
        "under_utilization_pct": UNDER_UTILIZATION_PCT,
        "over_pace": OVER_PACE,
        "direct_none_share_pct": DIRECT_NONE_SHARE_PCT,
    }


def _num(value):
    """A float, or None. A string that is not a number is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value) -> str:
    return f"${abs(value):,.2f}"


def _phrase(metric: str, label: str, kind: str, delta: float) -> str:
    """One flag's sentence, in the words a person would use."""
    direction = "rose" if delta > 0 else "fell"
    if metric in LOWER_IS_BETTER:
        direction = "increased" if delta > 0 else "improved"
    return f"{label} {direction} {abs(delta):.1f}%"


def metric_moves(deltas: dict, *, scope: str = "totals",
                 platform: str = "", label: str = "",
                 window_label: str = "") -> list[dict]:
    """`metric_move_gt_15` for one set of period-over-period percentages.

    ``deltas`` is {metric: percent or None}. A None delta is a comparison
    that could not be made -- the compare window held no rows -- and never
    a move of zero, so it raises no flag at all.
    """
    out = []
    versus = f" vs {window_label}" if window_label else ""
    where = f" on {label}" if label else ""
    for metric, name, kind in MOVE_METRICS:
        delta = _num(deltas.get(metric))
        if delta is None or abs(delta) <= METRIC_MOVE_PCT:
            continue
        out.append({
            "code": "metric_move_gt_15", "metric": metric, "scope": scope,
            "platform": platform or None, "campaign": None,
            "delta": round(delta, 2),
            "text": f"{_phrase(metric, name, kind, delta)}{where}{versus}.",
        })
    return out


def campaign_flags(campaigns: list[dict], blended_cpa,
                   *, window_label: str = "",
                   conversion_platforms: set | None = None) -> dict[str, list[dict]]:
    """Every campaign-scope flag, keyed by campaign row index as a string.

    Returned per campaign rather than as one list so the caller can hang each
    flag on its own row AND repeat the account-level ones at the top without
    computing them twice.
    """
    blended = _num(blended_cpa)
    reports_conversions = conversion_platforms if conversion_platforms is not None else None
    versus = f" vs {window_label}" if window_label else ""
    out: dict[str, list[dict]] = {}
    for index, row in enumerate(campaigns or []):
        name = str(row.get("campaign") or "").strip() or "an unnamed campaign"
        flags: list[dict] = []
        deltas = row.get("delta") if isinstance(row.get("delta"), dict) else {}

        # CTR drop, with the impression floor on BOTH windows: a campaign
        # that ran 40 impressions last week and 30 this week has a CTR that
        # swings on one click, and flagging it buries the ones that matter.
        ctr_delta = _num(deltas.get("ctr"))
        imps = _num(row.get("impressions")) or 0.0
        prior_imps = _num(row.get("compare_impressions"))
        if (ctr_delta is not None and ctr_delta < -CTR_DROP_PCT
                and imps >= CTR_MIN_IMPRESSIONS
                and prior_imps is not None and prior_imps >= CTR_MIN_IMPRESSIONS):
            flags.append({
                "code": "ctr_drop_gt_20", "metric": "ctr", "scope": "campaign",
                "campaign": name, "platform": row.get("platform"),
                "delta": round(ctr_delta, 2),
                "text": (f"CTR on {name} fell {abs(ctr_delta):.1f}%{versus}, "
                         f"on {int(imps):,} impressions."),
            })

        # CPA against the account's own blended figure, never an outside
        # benchmark: what "expensive" means is what the rest of this client's
        # money is buying.
        cpa = _num(row.get("cpa"))
        convs = _num(row.get("conversions")) or 0.0
        if (cpa is not None and blended is not None and blended > 0
                and convs >= CPA_MIN_CONVERSIONS
                and cpa > blended * CPA_MULTIPLE):
            flags.append({
                "code": "cpa_gt_2x_account", "metric": "cpa", "scope": "campaign",
                "campaign": name, "platform": row.get("platform"),
                "delta": None, "value": round(cpa, 2),
                "text": (f"{name} is paying {_money(cpa)} per conversion, "
                         f"{cpa / blended:.1f}x this client's blended "
                         f"{_money(blended)}."),
            })

        # Spend with no conversions -- but only where the platform reports
        # conversions at all. A Streaming TV buy that reports none has not
        # failed to convert; it has not been asked to.
        spend = _num(row.get("spend")) or 0.0
        platform = str(row.get("platform") or "")
        platform_counts = (reports_conversions is None
                           or platform in reports_conversions)
        if spend >= NO_CONV_MIN_SPEND and convs == 0 and platform_counts:
            flags.append({
                "code": "spend_no_conversions", "metric": "conversions",
                "scope": "campaign", "campaign": name, "platform": row.get("platform"),
                "delta": None, "value": round(spend, 2),
                "text": (f"{name} spent {_money(spend)} with no conversions "
                         f"recorded."),
            })
        if flags:
            out[str(index)] = flags
    return out


def pacing_flags(pacing: list[dict], blended_cpa) -> list[dict]:
    """The three pacing reads.

    ``pacing_alert`` is a passthrough of the board's own three-day alert and
    is NOT recomputed here: the board decides what an alert is, and a second
    engine deciding it differently is the drift ``client_view.pacing``
    records about its own first draft.
    """
    blended = _num(blended_cpa)
    out = []
    for row in pacing or []:
        product = str(row.get("product") or "").strip() or "an unnamed line"
        util = _num(row.get("utilization_pct"))
        pace = _num(row.get("pace"))
        line_cpa = _num(row.get("line_cpa"))
        cheap = (line_cpa is not None and blended is not None
                 and blended > 0 and line_cpa < blended)
        dear = (line_cpa is not None and blended is not None
                and blended > 0 and line_cpa > blended)

        if util is not None and util < UNDER_UTILIZATION_PCT and cheap:
            out.append({
                "code": "pacing_under_strong", "metric": "utilization_pct",
                "scope": "pacing", "product": product,
                "platform": row.get("platform"), "delta": None,
                "value": round(util, 1),
                "text": (f"{product} has used {util:.0f}% of its budget while "
                         f"converting at {_money(line_cpa)}, below this client's "
                         f"blended {_money(blended)}."),
            })
        if pace is not None and pace > OVER_PACE and dear:
            out.append({
                "code": "pacing_over_weak", "metric": "pace", "scope": "pacing",
                "product": product, "platform": row.get("platform"),
                "delta": None, "value": round(pace, 4),
                "text": (f"{product} is pacing at {pace:.2f}x while converting at "
                         f"{_money(line_cpa)}, above this client's blended "
                         f"{_money(blended)}."),
            })
        if row.get("alert"):
            band = str(row.get("band") or "").strip()
            days = int(_num(row.get("trend_days")) or 0)
            out.append({
                "code": "pacing_alert", "metric": "band", "scope": "pacing",
                "product": product, "platform": row.get("platform"),
                "delta": None, "value": band,
                "text": (f"{product} has been {band} for {days} days running; "
                         f"the pacing board is alerting on it."),
            })
    return out


def compute_flags(totals: dict, campaigns: list[dict], pacing: list[dict],
                  compare_present: bool, *, by_platform: list[dict] | None = None,
                  window_label: str = "",
                  conversion_platforms: set | None = None) -> list[dict]:
    """Every account-level flag for one performance payload, in reading order.

    ``compare_present`` is False when no comparison window was read or the
    comparison window held no rows. Every move flag is then skipped outright
    rather than computed against zero, which would report a client's first
    month as an infinite rise in everything.
    """
    out: list[dict] = []
    if compare_present:
        deltas = totals.get("delta") if isinstance(totals.get("delta"), dict) else {}
        out += metric_moves(deltas, scope="totals", window_label=window_label)
        for row in by_platform or []:
            row_deltas = row.get("delta") if isinstance(row.get("delta"), dict) else {}
            out += metric_moves(row_deltas, scope="platform",
                                platform=row.get("platform") or "",
                                label=str(row.get("label") or ""),
                                window_label=window_label)
    blended = totals.get("cpa")
    for flags in campaign_flags(
            campaigns, blended, window_label=window_label,
            conversion_platforms=conversion_platforms).values():
        out += flags
    out += pacing_flags(pacing, blended)
    return out


# --------------------------------------------------------------------------
# GA4. Same constants, same discipline: the breakdown tool does not invent a
# second idea of what a tagging problem looks like.
# --------------------------------------------------------------------------

def _variant_key(text: str) -> str:
    """What two spellings of one campaign or source share.

    Case is folded, and the handful of network nicknames that are one source
    written two ways are folded together. Everything else is left alone: two
    campaigns that genuinely differ are not the same campaign because their
    names look similar, which is the substring trap ``hub/client_key.py``
    spends a page on.
    """
    key = " ".join(str(text or "").split()).lower()
    swaps = {"fb": "facebook", "ig": "instagram", "goog": "google",
             "yt": "youtube", "li": "linkedin"}
    parts = [swaps.get(p, p) for p in key.replace("/", " / ").split(" ")]
    return " ".join(p for p in parts if p)


def ga4_flags(rows: list[dict], *, breakdown: str = "channel",
              total_sessions: int = 0, property_conv_rate=None) -> list[dict]:
    """`high_volume_low_conv`, `direct_none_gt_40` and `utm_case_variants`.

    ``rows`` carry at least ``label``, ``sessions`` and ``conv_rate``.
    """
    out: list[dict] = []
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    sessions = [(_num(r.get("sessions")) or 0.0) for r in rows]
    total = float(total_sessions or sum(sessions) or 0)
    average = _num(property_conv_rate)

    # Top-quintile volume converting below the property's own average.
    if rows and average is not None:
        ranked = sorted(sessions, reverse=True)
        cut = ranked[max(0, int(len(ranked) * 0.2) - 1)] if len(ranked) >= 5 else (ranked[0] if ranked else 0)
        for row in rows:
            vol = _num(row.get("sessions")) or 0.0
            rate = _num(row.get("conv_rate"))
            if vol >= cut and vol > 0 and rate is not None and rate < average:
                out.append({
                    "code": "high_volume_low_conv", "metric": "conv_rate",
                    "scope": breakdown, "label": row.get("label"),
                    "delta": None, "value": round(rate, 2),
                    "text": (f"{row.get('label')} sent {int(vol):,} sessions and "
                             f"converted at {rate:.2f}%, below the property average "
                             f"of {average:.2f}%."),
                })

    # Direct / (none): a share this high is a tagging problem, not an audience.
    if total > 0:
        direct = 0.0
        for row in rows:
            key = _variant_key(row.get("label") or "")
            if key in ("direct / (none)", "(direct) / (none)", "(direct)", "direct"):
                direct += _num(row.get("sessions")) or 0.0
        share = direct / total * 100
        if share > DIRECT_NONE_SHARE_PCT:
            out.append({
                "code": "direct_none_gt_40", "metric": "sessions",
                "scope": breakdown, "label": "(direct) / (none)",
                "delta": None, "value": round(share, 2),
                "text": (f"{share:.1f}% of sessions arrive as direct with no "
                         f"medium, above the {DIRECT_NONE_SHARE_PCT:.0f}% at which "
                         f"this is read as a tagging problem rather than an audience."),
            })

    # Two spellings of one campaign or source, which split one number in two.
    groups: dict[str, list[str]] = {}
    for row in rows:
        label = str(row.get("label") or "").strip()
        if not label:
            continue
        groups.setdefault(_variant_key(label), []).append(label)
    for variants in groups.values():
        unique = sorted(set(variants))
        if len(unique) > 1:
            out.append({
                "code": "utm_case_variants", "metric": "sessions",
                "scope": breakdown, "label": unique[0],
                "delta": None, "value": unique,
                "text": ("One source is tagged several ways and is counted as "
                         f"several rows: {', '.join(unique)}."),
            })
    return out
