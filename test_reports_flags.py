"""The deterministic flag rules Ask SmartHub reports rather than decides.

    python3 test_reports_flags.py

No pytest, no database: modules/reports/flags.py is pure functions and
nothing here touches a store. Every threshold is asserted from BOTH sides,
because a rule that only ever gets tested with an obvious case is a rule
whose boundary nobody has read.

What it holds:

  * the boundary itself: 14.9% raises nothing, 15.1% raises one, and the
    text the model is handed quotes the figure it fired on;
  * a delta of None -- a comparison that could not be made -- raises no flag
    at all, and is never read as a move of zero;
  * the CTR floor applies to BOTH windows, the CPA floor needs three
    conversions, and a blended CPA of None disables the multiple entirely;
  * spend with no conversions is silent on a platform that does not report
    conversions;
  * the pacing reads need the CPA half as well as the utilization half;
    pacing_alert is the board's own alert passed through, not recomputed;
  * the GA4 reads: the direct/(none) share, tagging variants that split one
    source into several rows, and volume converting below the average;
  * thresholds() carries every constant the rules were judged against, so a
    payload can quote the rule it applied.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from modules.reports import flags                          # noqa: E402

_passed = _failed = 0


def check(label, got, want=True, note=None):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}"
              + (f"\n          note: {note!r}" if note is not None else ""))


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


def codes(rows):
    return sorted(r["code"] for r in rows)


section("metric_move_gt_15 at its own boundary")
check("14.9% raises nothing", flags.metric_moves({"cpa": -14.9}), [])
check("exactly 15.0% raises nothing", flags.metric_moves({"cpa": 15.0}), [])
one = flags.metric_moves({"cpa": -18.4}, window_label="the previous 15 days")
check("15.1% raises one", len(flags.metric_moves({"cpa": 15.1})), 1)
check("one flag, one code", codes(one), ["metric_move_gt_15"])
check("...naming the metric", one[0]["metric"], "cpa")
check("...carrying the delta", one[0]["delta"], -18.4)
check("...with a sentence a person would say",
      one[0]["text"], "Cost per conversion improved 18.4% vs the previous 15 days.")
check("a rising CPA is not an improvement",
      flags.metric_moves({"cpa": 22.0})[0]["text"],
      "Cost per conversion increased 22.0%.")
check("clicks rising reads as rising",
      flags.metric_moves({"clicks": 30.0})[0]["text"], "Clicks rose 30.0%.")
check("clicks falling reads as falling",
      flags.metric_moves({"clicks": -30.0})[0]["text"], "Clicks fell 30.0%.")

section("A comparison that could not be made is not a move of zero")
check("a None delta raises nothing", flags.metric_moves({"cpa": None}), [])
check("an absent metric raises nothing", flags.metric_moves({}), [])
check("text is not a number", flags.metric_moves({"cpa": "lots"}), [])
check("compare_present False skips every move flag",
      codes(flags.compute_flags({"cpa": 40.0, "delta": {"spend": 90.0}},
                                [], [], False)), [])
check("...and True raises them",
      codes(flags.compute_flags({"cpa": 40.0, "delta": {"spend": 90.0}},
                                [], [], True)), ["metric_move_gt_15"])

section("Every headline metric is judged, in reading order")
allmoves = flags.metric_moves({m: 40.0 for m, _l, _k in flags.MOVE_METRICS})
check("all eight fire", len(allmoves), 8)
check("...in the declared order", [r["metric"] for r in allmoves],
      [m for m, _l, _k in flags.MOVE_METRICS])

section("ctr_drop_gt_20 respects the impression floor on both windows")


def ctr_row(ctr_delta, imps, prior):
    return {"campaign": "Brand", "platform": "google", "impressions": imps,
            "compare_impressions": prior, "conversions": 0, "spend": 0,
            "delta": {"ctr": ctr_delta}}


def campaign_codes(rows, blended=None, **kw):
    out = []
    for group in flags.campaign_flags(rows, blended, **kw).values():
        out += group
    return codes(out)


check("a 25% drop on 800/800 impressions fires",
      campaign_codes([ctr_row(-25.0, 800, 800)]), ["ctr_drop_gt_20"])
check("...a 19% drop does not", campaign_codes([ctr_row(-19.0, 800, 800)]), [])
check("...exactly 20% does not", campaign_codes([ctr_row(-20.0, 800, 800)]), [])
check("under the floor this window is silent",
      campaign_codes([ctr_row(-80.0, 499, 800)]), [])
check("under the floor in the COMPARE window is silent too",
      campaign_codes([ctr_row(-80.0, 800, 499)]), [])
check("exactly the floor on both sides fires",
      campaign_codes([ctr_row(-25.0, 500, 500)]), ["ctr_drop_gt_20"])
check("an unknown compare volume is silent",
      campaign_codes([ctr_row(-80.0, 800, None)]), [])
check("a CTR RISE is not a drop", campaign_codes([ctr_row(40.0, 800, 800)]), [])

section("cpa_gt_2x_account respects the conversion floor and the blended figure")


def cpa_row(cpa, convs):
    return {"campaign": "Generic", "platform": "google", "impressions": 0,
            "conversions": convs, "cpa": cpa, "spend": 0, "delta": {}}


check("3x the account on 4 conversions fires",
      campaign_codes([cpa_row(120.0, 4)], 40.0), ["cpa_gt_2x_account"])
check("...on 3 conversions, the floor itself, fires",
      campaign_codes([cpa_row(120.0, 3)], 40.0), ["cpa_gt_2x_account"])
check("...on 2 conversions does not",
      campaign_codes([cpa_row(120.0, 2)], 40.0), [])
check("exactly 2x does not fire", campaign_codes([cpa_row(80.0, 9)], 40.0), [])
check("just over 2x fires",
      campaign_codes([cpa_row(80.01, 9)], 40.0), ["cpa_gt_2x_account"])
check("a blended CPA of None disables the rule",
      campaign_codes([cpa_row(120.0, 9)], None), [])
check("a blended CPA of zero disables it too",
      campaign_codes([cpa_row(120.0, 9)], 0), [])
check("the text quotes both figures and the multiple",
      flags.campaign_flags([cpa_row(120.0, 4)], 40.0)["0"][0]["text"],
      "Generic is paying $120.00 per conversion, 3.0x this client's blended $40.00.")

section("spend_no_conversions, where conversions are reported at all")


def spend_row(spend, convs, platform="google"):
    return {"campaign": "Display", "platform": platform, "impressions": 0,
            "conversions": convs, "spend": spend, "delta": {}}


check("$100 with nothing, at the floor, fires",
      campaign_codes([spend_row(100.0, 0)]), ["spend_no_conversions"])
check("$99.99 does not", campaign_codes([spend_row(99.99, 0)]), [])
check("spend WITH a conversion does not",
      campaign_codes([spend_row(900.0, 1)]), [])
check("a platform that does not report conversions is silent",
      campaign_codes([spend_row(900.0, 0, "ttd")],
                     conversion_platforms={"google", "bing"}), [])
check("...and one that does is not",
      campaign_codes([spend_row(900.0, 0, "google")],
                     conversion_platforms={"google", "bing"}),
      ["spend_no_conversions"])
check("with no platform list, every platform counts",
      campaign_codes([spend_row(900.0, 0, "ttd")]), ["spend_no_conversions"])

section("Flags are hung on the campaign they are about")
many = flags.campaign_flags(
    [spend_row(50.0, 0), ctr_row(-40.0, 900, 900), spend_row(500.0, 0)], 40.0)
check("only the flagged rows have entries", sorted(many), ["1", "2"])
check("row 1 is the CTR drop", codes(many["1"]), ["ctr_drop_gt_20"])
check("row 2 is the dead spend", codes(many["2"]), ["spend_no_conversions"])
check("a campaign can carry two flags",
      codes(flags.campaign_flags(
          [{"campaign": "Both", "platform": "google", "impressions": 900,
            "compare_impressions": 900, "conversions": 0, "spend": 400.0,
            "cpa": None, "delta": {"ctr": -50.0}}], 40.0)["0"]),
      ["ctr_drop_gt_20", "spend_no_conversions"])
check("an unnamed campaign is still named in the sentence",
      "an unnamed campaign" in flags.campaign_flags(
          [{"campaign": "", "platform": "google", "conversions": 0,
            "spend": 500.0, "delta": {}}], None)["0"][0]["text"])

section("The pacing reads need both halves")


def line(**kw):
    row = {"product": "Paid Search", "platform": "google", "utilization_pct": 71.0,
           "pace": 0.71, "line_cpa": 20.0, "alert": False, "trend_days": 1,
           "band": "under"}
    row.update(kw)
    return row


check("under-utilized AND converting cheaply fires",
      codes(flags.pacing_flags([line()], 40.0)), ["pacing_under_strong"])
check("...under-utilized but converting DEARLY does not",
      codes(flags.pacing_flags([line(line_cpa=90.0)], 40.0)), [])
check("...at 80% utilization, the threshold itself, does not",
      codes(flags.pacing_flags([line(utilization_pct=80.0)], 40.0)), [])
check("over pace AND converting dearly fires",
      codes(flags.pacing_flags([line(pace=1.4, line_cpa=90.0)], 40.0)),
      ["pacing_over_weak"])
check("...over pace while converting cheaply does not",
      codes(flags.pacing_flags([line(pace=1.4, utilization_pct=95.0)], 40.0)), [])
check("...at exactly 1.15 does not",
      codes(flags.pacing_flags(
          [line(pace=1.15, utilization_pct=95.0, line_cpa=90.0)], 40.0)), [])
check("no line CPA disables both reads",
      codes(flags.pacing_flags([line(line_cpa=None)], 40.0)), [])
check("no blended CPA disables both reads",
      codes(flags.pacing_flags([line()], None)), [])

section("pacing_alert is the board's own alert, passed through")
check("the board alerting raises it",
      codes(flags.pacing_flags([line(alert=True, line_cpa=None)], None)),
      ["pacing_alert"])
check("...quoting the board's band and day count",
      flags.pacing_flags([line(alert=True, band="under", trend_days=4,
                               line_cpa=None)], None)[0]["text"],
      "Paid Search has been under for 4 days running; the pacing board is "
      "alerting on it.")
check("an off-pace line the board is NOT alerting on raises no alert",
      "pacing_alert" not in codes(flags.pacing_flags([line(alert=False)], 40.0)))

section("GA4 reads")
ga_rows = [{"label": "google / cpc", "sessions": 900, "conv_rate": 1.0},
           {"label": "(direct) / (none)", "sessions": 1200, "conv_rate": 4.0},
           {"label": "bing / cpc", "sessions": 100, "conv_rate": 5.0}]
check("direct/(none) over 40% of sessions is a tagging read",
      "direct_none_gt_40" in codes(flags.ga4_flags(ga_rows, property_conv_rate=3.0)))
check("...and under it is not",
      "direct_none_gt_40" not in codes(flags.ga4_flags(
          [{"label": "(direct) / (none)", "sessions": 100, "conv_rate": 4.0},
           {"label": "google / cpc", "sessions": 900, "conv_rate": 4.0}],
          property_conv_rate=3.0)))
# The top fifth by volume, so the row count matters: five rows, and the
# biggest of them converts below the property's own average.
vol_rows = [{"label": "google / cpc", "sessions": 5000, "conv_rate": 1.0},
            {"label": "bing / cpc", "sessions": 400, "conv_rate": 9.0},
            {"label": "google / organic", "sessions": 300, "conv_rate": 9.0},
            {"label": "newsletter / email", "sessions": 200, "conv_rate": 9.0},
            {"label": "linkedin / paid", "sessions": 100, "conv_rate": 9.0}]
check("the biggest source converting below the average is flagged",
      "high_volume_low_conv" in codes(flags.ga4_flags(vol_rows, property_conv_rate=3.0)))
check("...naming it, its volume and both rates",
      [r["text"] for r in flags.ga4_flags(vol_rows, property_conv_rate=3.0)
       if r["code"] == "high_volume_low_conv"],
      ["google / cpc sent 5,000 sessions and converted at 1.00%, below the "
       "property average of 3.00%."])
check("...a small source converting badly is NOT flagged",
      [r["label"] for r in flags.ga4_flags(
          vol_rows[:1] + [{"label": "linkedin / paid", "sessions": 10,
                           "conv_rate": 0.1}] + vol_rows[1:4],
          property_conv_rate=3.0) if r["code"] == "high_volume_low_conv"],
      ["google / cpc"])
check("...and needs a property average to compare against",
      "high_volume_low_conv" not in codes(flags.ga4_flags(vol_rows)))
variants = flags.ga4_flags(
    [{"label": "Spring Sale", "sessions": 50, "conv_rate": 1.0},
     {"label": "spring sale", "sessions": 40, "conv_rate": 1.0}])
check("two spellings of one campaign are one flag",
      codes(variants), ["utm_case_variants"])
check("...listing both spellings", variants[0]["value"],
      ["Spring Sale", "spring sale"])
check("fb and facebook are one source written twice",
      "utm_case_variants" in codes(flags.ga4_flags(
          [{"label": "fb / paid", "sessions": 50, "conv_rate": 1.0},
           {"label": "facebook / paid", "sessions": 40, "conv_rate": 1.0}])))
check("two genuinely different campaigns are not variants",
      "utm_case_variants" not in codes(flags.ga4_flags(
          [{"label": "Spring Sale", "sessions": 50, "conv_rate": 1.0},
           {"label": "Spring Sale 2026", "sessions": 40, "conv_rate": 1.0}])))
check("no rows, no flags", flags.ga4_flags([]), [])

section("thresholds() carries every constant the rules used")
t = flags.thresholds()
check("the nine constants are published", sorted(t), [
    "cpa_min_conversions", "cpa_multiple", "ctr_drop_pct", "ctr_min_impressions",
    "direct_none_share_pct", "metric_move_pct", "no_conv_min_spend",
    "over_pace", "under_utilization_pct"])
check("...and are the module's own values, not a second copy",
      (t["metric_move_pct"], t["ctr_drop_pct"], t["cpa_multiple"],
       t["under_utilization_pct"], t["over_pace"]),
      (flags.METRIC_MOVE_PCT, flags.CTR_DROP_PCT, flags.CPA_MULTIPLE,
       flags.UNDER_UTILIZATION_PCT, flags.OVER_PACE))

section("compute_flags puts the whole payload together")
whole = flags.compute_flags(
    {"cpa": 40.0, "delta": {"spend": 30.0, "ctr": None}},
    [spend_row(500.0, 0)], [line(alert=True)], True,
    by_platform=[{"platform": "google", "label": "Google Ads",
                  "delta": {"clicks": -40.0}}],
    window_label="the previous 15 days")
check("totals, platform, campaign and pacing flags all present",
      codes(whole),
      ["metric_move_gt_15", "metric_move_gt_15", "pacing_alert",
       "pacing_under_strong", "spend_no_conversions"])
check("the platform flag names its platform",
      [r["platform"] for r in whole if r["scope"] == "platform"], ["google"])
check("...and its label in the sentence",
      [r["text"] for r in whole if r["scope"] == "platform"],
      ["Clicks fell 40.0% on Google Ads vs the previous 15 days."])
check("every flag carries a code, a scope and a text",
      all(r.get("code") and r.get("scope") and r.get("text") for r in whole))

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
