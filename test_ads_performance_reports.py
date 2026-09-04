"""Recurring client Google Ads performance reports.

The commitment half is asserted hardest: a report arriving in a client's inbox
on a schedule is a promise, so it is off for every account until somebody
turns it on, and a deployed campaign inherits nothing.

The rest:

* the report reads `optimization.py`'s own GAQL, so a report and a scan taken
  minutes apart cannot quote different spend for one month;
* a period that could not be read is `None` and never zero — a baseline nobody
  measured printed as 0 makes every figure beside it read "up 100%";
* a month that could not be measured is **not sent**, because a client reading
  zeros because Google refused cannot be un-sent;
* one client's failure does not cost the rest of the book their report;
* delivery stops at `hub/leads.py` and this Hub claims to have emailed nobody;
* and the client's page is reachable with no Hub login, carries no staff
  chrome, and answers the same 404 for revoked, deleted and never-existed.

    python3 test_ads_performance_reports.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_rep_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["HUB_LEADS_FILE"] = os.path.join(TMP, "leads.json")
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
os.environ.pop("CLOUDINARY_URL", None)

from hub import audit                                     # noqa: E402
from modules.ads_builder import (app as ads_app, monitoring,  # noqa: E402
                                 performance_pdf, performance_report, store)
from modules.ads_builder.google_ads import GoogleAdsError   # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


CID = "1111111111"


def campaign_rows(cost, clicks, conversions, name="Search | Repairs"):
    return [{"campaign": {"id": "1", "name": name, "status": "ENABLED"},
             "metrics": {"costMicros": str(int(cost * 1_000_000)), "clicks": clicks,
                         "impressions": clicks * 20, "ctr": 0.05,
                         "conversions": conversions, "costPerConversion": "0"}}]


SUMMARY = [{"customer": {"id": CID, "descriptiveName": "Northside Roofing",
                         "currencyCode": "USD"}, "metrics": {}}]


def run():
    print("\nRecurring Google Ads performance reports\n" + "=" * 60)

    # ------------------------------------------------------ the commitment
    print("\nOff until somebody turns it on")
    check("an account nobody configured has no cadence",
          store.report_schedule(CID)["enabled"] is False)
    check("and nothing is due", store.due_report_accounts() == [])

    proposal = store.create_proposal("Northside Roofing", {"adGroups": []},
                                     google_customer_id=CID)
    store.mark_deployed(proposal["id"], {"ok": True})
    check("a deployed campaign inherits no schedule",
          store.report_schedule(CID)["enabled"] is False and not store.due_report_accounts())

    refused = ""
    try:
        store.set_report_schedule(CID, cadence="daily", recipient="a@b.com")
    except ValueError as exc:
        refused = str(exc)
    check("a cadence nobody offers is refused by name rather than stored",
          "daily" in refused and store.report_schedule(CID)["enabled"] is False, refused)
    check("and the refusal says which cadences there are",
          "weekly" in refused and "monthly" in refused, refused)

    # ------------------------------------------------------------ the report
    print("\nThe report reads the scanner's own queries")
    calls = []

    def fake_search(cid, query, store=None):
        calls.append(query)
        if "FROM customer" in query:
            return SUMMARY
        if "segments.date BETWEEN" in query:
            return campaign_rows(3000.0, 400, 20)
        return campaign_rows(4000.0, 500, 25)

    real_search = performance_report.google_ads.search
    performance_report.google_ads.search = fake_search
    try:
        r = performance_report.report(CID, client_name="Northside Roofing")
    finally:
        performance_report.google_ads.search = real_search

    from modules.ads_builder import optimization
    check("the current window is the scanner's own campaign query",
          any(q == optimization.CAMPAIGNS_QUERY.format(date_range="LAST_30_DAYS")
              for q in calls), calls[:1])
    check("the totals are the campaigns' own",
          r["totals"]["cost"] == 4000.0 and r["totals"]["clicks"] == 500, r["totals"])
    check("cost per conversion is derived, not taken on trust",
          r["totals"]["cost_per_conversion"] == 160.0, r["totals"])
    headline = r["sections"][0]
    spend = next(row for row in headline["rows"] if row["key"] == "cost")
    check("spend is compared with the previous window",
          spend["change_percent"] == round(100 * 1000 / 3000, 1), spend)
    check("and the report says it was compared", r["compared"] is True)
    check("it is measured", r["measured"] is True)

    # ------------------------------------- a baseline nobody could read
    print("\nA period that could not be read is not a period of zero")

    def half_blind(cid, query, store=None):
        if "segments.date BETWEEN" in query:
            raise GoogleAdsError("Google refused the earlier window.", code="X")
        if "FROM customer" in query:
            return SUMMARY
        return campaign_rows(4000.0, 500, 25)

    performance_report.google_ads.search = half_blind
    try:
        blind = performance_report.report(CID, client_name="Northside Roofing")
    finally:
        performance_report.google_ads.search = real_search
    check("the previous totals are None, never an empty total",
          blind["previous_totals"] is None, blind["previous_totals"])
    check("so no figure carries a change",
          all(row["change_percent"] is None for row in blind["sections"][0]["rows"]),
          blind["sections"][0]["rows"])
    check("the report says it was not compared", blind["compared"] is False)
    check("and names the window it could not read", "previous" in blind["errors"],
          blind["errors"])
    check("while the current month is still measured and still rendered",
          blind["measured"] is True and blind["totals"]["cost"] == 4000.0)
    check("a percentage from zero is refused rather than reported as 100%",
          performance_report._delta(5, 0) is None)

    # -------------------------------------------------------- the PDF
    print("\nThe document")
    pdf = performance_pdf.build(r)
    check("it is a PDF", pdf.startswith(b"%PDF-") and len(pdf) > 1200, len(pdf))
    check("one that renders with nothing compared too",
          performance_pdf.build(blind).startswith(b"%PDF-"))
    check("no HTML-to-PDF engine or new dependency",
          "reportlab" in (ROOT / "modules/ads_builder/performance_pdf.py").read_text())
    check("the filename names the client and the month",
          "Northside-Roofing" in performance_report.filename(r),
          performance_report.filename(r))

    # ------------------------------------------------------------ sending
    print("\nSending stops at hub/leads.py")
    delivered = []

    def fake_deliver(**kwargs):
        delivered.append(kwargs)
        return {"ok": True, "lead_id": "l1", "delivered": True,
                "note": "Created in Smart 1 Suite."}

    import hub.leads as hub_leads_module
    real_deliver = hub_leads_module.capture_and_deliver
    hub_leads_module.capture_and_deliver = fake_deliver
    performance_report.google_ads.search = fake_search
    try:
        store.set_report_schedule(CID, cadence="monthly",
                                 recipient="owner@northside.example.com", actor="todd")
        out = monitoring.send_report(
            {"customer_id": CID, "client_name": "Northside Roofing",
             "proposal_id": proposal["id"]},
            cadence="monthly", recipient="owner@northside.example.com", actor="test")
        check("a report is built and stored", out["ok"] and out["token"], out)
        check("the client's link is built from the Hub's own origin",
              out["report_url"].startswith("https://smart1.agency/tools/ads/r/"),
              out["report_url"])
        check("and the PDF is that link plus .pdf",
              out["pdf_url"] == out["report_url"] + ".pdf", out)
        check("exactly one delivery, through hub/leads.py", len(delivered) == 1, delivered)
        sent = delivered[0]
        check("filed under its own source", sent["source"] == "ads_reports", sent)
        check("to the recorded address", sent["fields"]["email"] == "owner@northside.example.com")
        check("carrying the report link in meta and the PDF as pdf_url",
              sent["meta"]["report_url"] == out["report_url"]
              and sent["pdf_url"] == out["pdf_url"], sent)
        check("and the client, so it lands on their record",
              sent["client"] == "Northside Roofing", sent)

        rows = [x for x in audit.read(limit=400, module="ads_builder")
                if x.get("type") == "performance_report"]
        check("the send is logged against the client",
              len(rows) == 1 and rows[0].get("client") == "Northside Roofing", rows)
        check("with what the month actually was",
              rows[0].get("spend") == 4000.0, rows[0])

        check("the account is stamped, so it is not due again immediately",
              store.report_schedule(CID)["last_sent_at"] is not None)
        check("and nothing is due now", store.due_report_accounts() == [],
              store.due_report_accounts())

        # ------------------------------- a month that could not be measured
        print("\nA month that could not be measured is not sent")
        delivered.clear()
        stamp_before = store.report_schedule(CID)["last_sent_at"]

        def all_blind(cid, query, store=None):
            raise GoogleAdsError("Google refused this account.", code="X")

        performance_report.google_ads.search = all_blind
        out = monitoring.send_report(
            {"customer_id": CID, "client_name": "Northside Roofing"},
            cadence="monthly", recipient="owner@northside.example.com", actor="test")
        check("nothing is sent", not out.get("ok") and delivered == [], (out, delivered))
        check("and it says why rather than reporting a clean send",
              out.get("skipped") == "not measured" or out.get("error"), out)
        check("the account is not stamped, so it is due again",
              store.report_schedule(CID)["last_sent_at"] == stamp_before)

        # ---------------------------------------------------- the sweep
        print("\nOne client's failure does not cost the rest theirs")
        # Two accounts neither of which has ever been sent to, so both are due.
        for cid, name in (("2222222222", "Riverside HVAC"),
                          ("3333333333", "Buckeye Marina")):
            row = store.create_proposal(name, {"adGroups": []}, google_customer_id=cid)
            store.mark_deployed(row["id"], {"ok": True})
            store.set_report_schedule(cid, cadence="weekly",
                                      recipient=f"ops@{cid}.example.com", actor="todd")

        def one_bad(cid, query, store=None):
            if cid == "3333333333":
                raise GoogleAdsError("refused", code="X")
            return fake_search(cid, query, store)

        performance_report.google_ads.search = one_bad
        delivered.clear()
        result = monitoring.report_sweep(actor="test")
        check("the healthy account still gets its report",
              result["sent"] == 1 and len(delivered) == 1, (result, delivered))
        check("and the failure is counted rather than swallowed",
              result["failed"] == 1 and result["failures"], result)
        check("the failed account is still due, so it is retried",
              any(a["customer_id"] == "3333333333" for a in store.due_report_accounts()),
              store.due_report_accounts())

        performance_report.google_ads.search = fake_search
        real_head = monitoring._headroom
        monitoring._headroom = lambda: (0, {"measured": True, "remaining": 0})
        delivered.clear()
        result = monitoring.report_sweep(actor="test")
        check("no report is built with no operation budget left",
              result["sent"] == 0 and delivered == [], result)
        check("and the skip is reported", result["quota_skipped"] >= 1, result)
        monitoring._headroom = real_head

        print("\nNothing switched on is a state, not a failure")
        for cid in ("2222222222", "3333333333"):
            store.set_report_schedule(cid, cadence="", recipient="", actor="todd")
        store.set_report_schedule(CID, cadence="", recipient="", actor="todd")
        result = monitoring.report_sweep(actor="test")
        check("an empty book is reported as a skip",
              result.get("skipped") and result["due"] == 0, result)
    finally:
        hub_leads_module.capture_and_deliver = real_deliver
        performance_report.google_ads.search = real_search

    # ------------------------------------------------ no mailer is claimed
    print("\nThis Hub claims to have emailed nobody")
    for path in ("modules/ads_builder/monitoring.py",
                 "modules/ads_builder/templates/ads_performance_report.html"):
        text = (ROOT / path).read_text().lower()
        check(f"{path} sends no mail",
              "smtplib" not in text and "sendgrid" not in text
              and "we'll email" not in text and "we will email" not in text, path)

    # ------------------------------------------------------ the client page
    print("\nThe page the client opens")
    token = store.performance_reports_for(CID)[0]["token"]
    client = ads_app.app.test_client()
    page = client.get(f"/r/{token}")
    check("it opens with no Hub login", page.status_code == 200, page.status_code)
    body = page.get_data(as_text=True)
    check("and carries the client's own figures", "$4,000" in body or "4000" in body,
          body[:120])
    check("no staff nav, help layer or feedback tab reaches it",
          "hub-sidebar" not in body and "hub-help" not in body
          and "s1hub" not in body)
    check("the PDF is served at the same token", client.get(f"/r/{token}.pdf")
          .headers.get("Content-Type") == "application/pdf")
    check("an invented token answers 404",
          client.get("/r/nosuchtokenatall").status_code == 404)
    check("and it is the same page a revoked one would answer with — never "
          "'that one expired'",
          b"unavailable" in client.get("/r/nosuchtokenatall").data.lower())
    check("both are declared public on the module rather than only on a route",
          "/r/" in ads_app.PUBLIC_PREFIXES, ads_app.PUBLIC_PREFIXES)

    # ----------------------------------------------------------- the job
    print("\nThe scheduler sends them")
    from hub import scheduler
    check("a job is registered", "ads_reports" in scheduler.JOBS)
    every, fn, desc = scheduler.JOBS["ads_reports"]
    check("and it is the sweep", fn.__name__ == "job_ads_performance_reports")
    check("with a description", bool(desc.strip()), desc)
    check("due is decided per account, not by the job's tick",
          "due_report_accounts" in (ROOT / "modules/ads_builder/monitoring.py").read_text())

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
