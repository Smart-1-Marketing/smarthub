"""Unattended changes to a client's live Google Ads account.

Nobody presses anything at the moment one of these happens, so what is
asserted here is mostly the refusals:

* auto-apply is **off** unless somebody turned it on for that account — the
  default is the important case, because a feature that arrives switched on is
  one nobody decided to run;
* it acts only on the two lowest-blast-radius categories, only on the actions
  that reach Google, and only on high-severity findings;
* it stops at a per-run cap and inside the day's Google operation budget;
* every applied change writes exactly one activity row naming the client, the
  finding and the mutate — with nobody having clicked, that row is the only
  account of what changed;
* and the new pause detector fires on a keyword that spends and never
  converts, with a confirmation word that says pause rather than approve.

    python3 test_ads_optimization_autoapply.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_auto_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ.pop("CLOUDINARY_URL", None)

from hub import audit                                     # noqa: E402
from modules.ads_builder import google_ads, monitoring, optimization, store  # noqa: E402
from modules.ads_builder.google_ads import GoogleAdsError  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


CID = "1111111111"


def keyword_row(text, clicks, cost, conversions=0, status="ENABLED", criterion="9"):
    return {"campaign": {"id": "1", "name": "Search"},
            "adGroup": {"id": "2", "name": "Repairs"},
            "adGroupCriterion": {"criterionId": criterion, "status": status,
                                 "negative": False,
                                 "keyword": {"text": text, "matchType": "BROAD"}},
            "metrics": {"costMicros": str(int(cost * 1_000_000)), "clicks": clicks,
                        "impressions": clicks * 10, "ctr": 0.1,
                        "conversions": conversions}}


def run():
    print("\nAuto-applying low-risk Google Ads findings\n" + "=" * 60)

    # -------------------------------------------------- the pause detector
    print("\nA keyword that spends and never converts")
    rows = [keyword_row("roof repair", 12, 90.0),
            keyword_row("roof replacement", 40, 300.0, conversions=4, criterion="10"),
            keyword_row("gutter cleaning", 2, 4.0, criterion="11"),
            keyword_row("paused already", 30, 200.0, status="PAUSED", criterion="12")]
    result = optimization.analyse_rows(CID, "LAST_30_DAYS",
                                       {"keywords": rows, "campaigns": []})
    pauses = [i for i in result["items"] if i["category"] == "keyword_pauses"]
    check("a spending keyword with no conversions is a finding",
          len(pauses) == 1 and "roof repair" in pauses[0]["title"], [p["title"] for p in pauses])
    check("a converting keyword is left alone",
          all("replacement" not in p["title"] for p in pauses))
    check("and so is one with barely any clicks",
          all("gutter" not in p["title"] for p in pauses))
    check("an already-paused keyword is not proposed for pausing again",
          all("paused already" not in p["title"] for p in pauses))
    check("it is high severity", pauses[0]["severity"] == "high", pauses[0]["severity"])
    check("the confirmation says pause, not approve",
          pauses[0]["confirmation"] == "PAUSE"
          and optimization.ACTION_CONFIRMATIONS["pause_keyword"] == "PAUSE",
          pauses[0]["confirmation"])
    check("and it is the vocabulary a rep already types to pause a campaign",
          optimization.PAUSE_CONFIRMATION == google_ads.STATUS_CONFIRMATIONS["PAUSED"])

    # ------------------------------------------------------- the mutate shape
    print("\nThe mutate it sends")
    sent = {}

    def fake_request(method, path, body=None, store=None, customer_id=None):
        sent.update(method=method, path=path, body=body)
        return {"results": [{"resourceName": "x"}]}

    real_request = optimization.google_ads.request
    optimization.google_ads.request = fake_request
    try:
        out = optimization.apply_action(
            CID, "pause_keyword",
            {"ad_group_id": "2", "criterion_id": "9", "text": "roof repair",
             "confirmation": "PAUSE"})
    finally:
        optimization.google_ads.request = real_request
    op = (sent["body"]["operations"] or [{}])[0]
    check("it updates the criterion rather than removing it",
          "update" in op and "remove" not in op, op)
    check("the status is PAUSED with an explicit mask",
          op["update"]["status"] == "PAUSED" and op["updateMask"] == "status", op)
    check("addressed as adGroupId~criterionId",
          op["update"]["resourceName"].endswith("/adGroupCriteria/2~9"), op)
    check("on the criterion endpoint", sent["path"].endswith("adGroupCriteria:mutate"), sent["path"])
    check("and the wrong confirmation is refused",
          _refuses(lambda: optimization.apply_action(
              CID, "pause_keyword",
              {"ad_group_id": "2", "criterion_id": "9", "confirmation": "APPROVE"})))
    check("as is no confirmation at all",
          _refuses(lambda: optimization.apply_action(
              CID, "pause_keyword", {"ad_group_id": "2", "criterion_id": "9"})))
    check("a successful apply reports the action", out["action"] == "pause_keyword", out)

    # ------------------------------------------------------------- the default
    print("\nOff until somebody turns it on")
    check("an account nobody has configured is off",
          store.auto_apply_settings(CID)["enabled"] is False)
    check("with nothing allowed",
          store.auto_apply_settings(CID)["categories"] == [])

    proposal = store.create_proposal("Northside Roofing", {"adGroups": []},
                                     google_customer_id=CID)
    store.mark_deployed(proposal["id"], {"ok": True})

    scan = optimization.analyse_rows(CID, "LAST_30_DAYS",
                                     {"keywords": rows, "campaigns": []})
    applied = []

    def fake_apply(cid, action, payload, store_arg=None):
        applied.append((cid, action, payload.get("criterion_id") or payload.get("text")))
        return {"ok": True, "customer_id": cid, "action": action,
                "detail": {"criterion_id": payload.get("criterion_id", "")}}

    real_apply = monitoring.optimization.apply_action
    real_scan = monitoring.optimization.scan_account
    real_head = monitoring._headroom
    monitoring.optimization.apply_action = fake_apply
    monitoring.optimization.scan_account = lambda *a, **k: scan
    monitoring._headroom = lambda: (13500, {"measured": True, "remaining": 13500})

    out = monitoring.sweep(actor="test")
    check("a sweep of an unconfigured account changes nothing at all",
          applied == [] and out["auto_apply"]["applied"] == 0, (applied, out["auto_apply"]))
    check("and it is not counted as an auto-apply account",
          out["auto_apply"]["accounts"] == 0, out["auto_apply"])

    # --------------------------------------------------------- switched on
    print("\nSwitched on for one category")
    store.set_auto_apply(CID, enabled=True, categories=["keyword_pauses"], actor="todd")
    applied.clear()
    out = monitoring.sweep(actor="test")
    check("the allowed finding is applied", len(applied) == 1, applied)
    check("as a pause", applied and applied[0][1] == "pause_keyword", applied)
    check("and the run reports it", out["auto_apply"]["applied"] == 1, out["auto_apply"])

    log = [r for r in audit.read(limit=400, module="ads_builder")
           if r.get("type") == "optimization_auto_applied"]
    check("every applied change writes exactly one activity row", len(log) == 1, len(log))
    row = log[0] if log else {}
    check("naming the client", row.get("client") == "Northside Roofing", row.get("client"))
    check("the finding it acted on", "roof repair" in str(row.get("finding", "")), row.get("finding"))
    check("why the detector raised it", bool(row.get("why")), row)
    check("and what actually changed", bool(row.get("criterion_id")), row)

    # ------------------------------------------------------- the allowlists
    print("\nWhat it will not act on")
    store.set_auto_apply(CID, enabled=True, categories=["keyword_pauses"], actor="todd")
    mixed = {"items": [
        {"category": "keyword_pauses", "action": "pause_keyword", "severity": "medium",
         "title": "medium", "data": {"cost": 99}},
        {"category": "click_costs", "action": "pause_keyword", "severity": "high",
         "title": "wrong category", "data": {"cost": 99}},
        {"category": "keyword_pauses", "action": "set_target_cpa", "severity": "high",
         "title": "wrong action", "data": {"cost": 99}},
    ]}
    picked = monitoring._auto_appliable(mixed, ["keyword_pauses"])
    check("a medium-severity finding is left for a person", not picked, picked)
    check("a category nobody switched on is skipped",
          not monitoring._auto_appliable(mixed, ["search_terms"]))
    check("a category with no allowed action in it applies nothing",
          not monitoring._auto_appliable(
              {"items": [{"category": "keyword_pauses", "action": "set_target_cpa",
                          "severity": "high", "data": {}}]}, ["keyword_pauses"]))
    check("a category outside the allowlist cannot even be stored",
          store.set_auto_apply(CID, enabled=True,
                               categories=["keyword_pauses", "set_target_cpa"],
                               actor="t")["categories"] == ["keyword_pauses"])

    # ------------------------------------------------------------- the cap
    print("\nThe per-run cap and the day's budget")
    many = {"items": [
        {"category": "keyword_pauses", "action": "pause_keyword", "severity": "high",
         "title": f"k{n}", "data": {"criterion_id": str(n), "cost": n}}
        for n in range(25)]}
    picked = monitoring._auto_appliable(many, ["keyword_pauses"])
    check("no more than the cap in one run",
          len(picked) == store.AUTO_APPLY_MAX_PER_RUN, len(picked))
    check("costliest first, so a capped run stops the most waste",
          picked[0]["data"]["cost"] > picked[-1]["data"]["cost"], picked[0]["data"])

    monitoring.optimization.scan_account = lambda *a, **k: many
    applied.clear()
    # Room for three operations only: three land, the rest are skipped.
    out = monitoring.sweep(actor="test")
    check("the cap holds through a real sweep",
          len(applied) == store.AUTO_APPLY_MAX_PER_RUN, len(applied))

    applied.clear()
    monitoring._headroom = lambda: (monitoring._cost_per_account() + 3,
                                    {"measured": True, "remaining": 9})
    out = monitoring.sweep(actor="test")
    check("and an exhausted daily budget stops it mid-account",
          len(applied) == 3, (len(applied), out["auto_apply"]))

    # -------------------------------------------------- a refused apply
    print("\nA change Google refuses")
    def refusing(cid, action, payload, store_arg=None):
        raise GoogleAdsError("Google refused this change.", code="PERMISSION_DENIED")

    monitoring.optimization.apply_action = refusing
    monitoring.optimization.scan_account = lambda *a, **k: scan
    monitoring._headroom = lambda: (13500, {"measured": True, "remaining": 13500})
    before = len([r for r in audit.read(limit=800, module="ads_builder")
                  if r.get("type") == "optimization_auto_applied"])
    out = monitoring.sweep(actor="test")
    check("a refusal is counted rather than swallowed",
          out["auto_apply"]["failed"] == 1 and out["auto_apply"]["applied"] == 0,
          out["auto_apply"])
    after = [r for r in audit.read(limit=800, module="ads_builder")
             if r.get("type") == "optimization_auto_applied"]
    check("and it still writes a row saying so",
          len(after) == before + 1 and bool(after[0].get("error")), after[0] if after else None)
    check("the sweep itself still succeeds", out["scanned"] == 1, out)

    # ------------------------------------- a scan that failed applies nothing
    print("\nNothing is applied on the strength of a scan that failed")
    def raising(*a, **k):
        raise GoogleAdsError("no", code="X")
    monitoring.optimization.apply_action = fake_apply
    monitoring.optimization.scan_account = raising
    applied.clear()
    out = monitoring.sweep(actor="test")
    check("a failed scan auto-applies nothing",
          applied == [] and out["auto_apply"]["applied"] == 0, (applied, out))

    monitoring.optimization.apply_action = real_apply
    monitoring.optimization.scan_account = real_scan
    monitoring._headroom = real_head

    # ----------------------------------------------------------- the control
    print("\nThe control on the page")
    panel = monitoring.account_panel()
    check("the panel says whether each account is switched on",
          all("auto_apply" in a for a in panel["accounts"]), panel["accounts"])
    check("and serves the allowed categories rather than leaving the page to type them",
          panel["auto_apply_categories"] == list(store.AUTO_APPLY_CATEGORIES),
          panel.get("auto_apply_categories"))
    check("with the cap, so the screen states the same number the sweep enforces",
          panel["auto_apply_cap"] == store.AUTO_APPLY_MAX_PER_RUN)
    page = (ROOT / "modules/ads_builder/templates/ads_optimization.html").read_text()
    check("the page draws a toggle", "data-auto-on=" in page)
    check("and reads the categories from the server, not a list of its own",
          "monitor.auto_apply_categories" in page and "keyword_pauses'" not in page)

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


def _refuses(fn) -> bool:
    try:
        fn()
    except GoogleAdsError:
        return True
    except Exception:                                     # noqa: BLE001
        return False
    return False


if __name__ == "__main__":
    sys.exit(run())
