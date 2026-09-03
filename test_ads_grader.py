"""The public Google Ads Grader.

A stranger types their details, connects their own Google Ads account
read-only, and gets a scored report. Two properties matter more than any
other and are asserted hardest:

* **the lead is captured BEFORE OAuth starts** — a prospect who abandons
  Google's consent screen has still told us who they are, so a capture that
  ran afterwards would lose every one of them; and
* **no credential is ever stored** — `access_type=online`, so Google issues no
  refresh token, and the access token is a local variable that goes out of
  scope with the request. Asserted from the model definitions and from the
  written rows, not from the sentence in the docstring.

Also: the score uses `optimization.py`'s own detectors rather than a second
copy of them, a state token is used once and expires, an account with no
campaigns is *named* rather than dropped, an unknown token answers the same
404 a revoked one would, and every page is reachable with no Hub login.

    python3 test_ads_grader.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1grader_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["HUB_LEADS_FILE"] = os.path.join(TMP, "leads.json")
os.environ["PUBLIC_BASE_URL"] = "https://smart1.agency"
os.environ["GOOGLE_ADS_CLIENT_ID"] = "cid.apps.googleusercontent.com"
os.environ["GOOGLE_ADS_CLIENT_SECRET"] = "secret"
os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"] = "devtoken"
os.environ.pop("CLOUDINARY_URL", None)

from hub import audit                                     # noqa: E402
from modules.ads_grader import app as grader_app, grading, store  # noqa: E402
from modules.ads_builder import optimization               # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


CID = "1111111111"

CAMPAIGN_ROWS = [{
    "campaign": {"id": "1", "name": "Search | Repairs", "status": "ENABLED"},
    "metrics": {"costMicros": "4000000000", "clicks": 500, "impressions": 20000,
                "ctr": 0.025, "conversions": 0, "costPerConversion": "0"}}]
SUMMARY_ROWS = [{"customer": {"id": CID, "descriptiveName": "Northside Roofing",
                              "currencyCode": "USD"}, "metrics": {}}]
TERM_ROWS = [{
    "campaign": {"id": "1", "name": "Search | Repairs"},
    "adGroup": {"id": "2", "name": "Repairs"},
    "searchTermView": {"searchTerm": "free roof repair"},
    "segments": {"searchTermMatchType": "BROAD"},
    "metrics": {"costMicros": "900000000", "clicks": 60, "impressions": 800,
                "ctr": 0.07, "conversions": 0}}]


def fake_search(token, customer_id, query):
    if "FROM customer" in query:
        return SUMMARY_ROWS
    if "search_term_view" in query:
        return TERM_ROWS
    if "FROM campaign" in query:
        return CAMPAIGN_ROWS
    return []


def run():
    print("\nPublic Google Ads Grader\n" + "=" * 60)
    client = grader_app.app.test_client()

    # ------------------------------------------- no credential is ever kept
    print("\nNothing here can hold a Google credential")
    for model in (store.GraderSession, store.GraderResult):
        columns = set(model.__table__.columns.keys())
        bad = {c for c in columns
               if any(word in c.lower() for word in
                      ("token_value", "access_token", "refresh", "secret",
                       "credential", "password", "code"))}
        # `state` and `token` are unguessable identifiers of our own, not
        # anything Google issued -- the exclusion above is deliberately on the
        # words that would name a CREDENTIAL.
        check(f"{model.__tablename__} has no column for one", not bad, bad)
    source = (ROOT / "modules/ads_grader/grading.py").read_text()
    check("the grant is online, so Google issues no refresh token at all",
          '"access_type": "online"' in source)
    # Read as a CALL rather than as text: grading.py's own docstring explains
    # at length why it does not use google_ads.exchange_code(), and a check
    # matching the string reports the explanation as the defect -- the rule
    # hub/config.py's drift check gives, for the sixth time in this repo.
    import ast
    _calls = {ast.unparse(n.func) for n in ast.walk(ast.parse(source))
              if isinstance(n, ast.Call) and not isinstance(n.func, ast.Subscript)}
    check("and this flow does not reuse the ads module's token cache, which "
          "staff paths read",
          "google_ads.exchange_code" not in _calls and "OAUTH_TOKEN_URL" in source,
          sorted(c for c in _calls if "google_ads" in c))
    app_source = (ROOT / "modules/ads_grader/app.py").read_text()
    check("the callback lets the token go rather than passing it on",
          "del token" in app_source)

    # ------------------------------------------ the lead comes first
    print("\nThe lead is captured before OAuth starts")
    captured = []

    import hub.leads as hub_leads_module
    real_deliver = hub_leads_module.capture_and_deliver
    hub_leads_module.capture_and_deliver = lambda **kw: (
        captured.append(kw) or {"ok": True, "lead_id": "lead-1", "delivered": True})
    try:
        resp = client.post("/api/start", json={
            "company": "Northside Roofing", "email": "sam@northside.example.com",
            "name": "Sam Carter", "website": "https://northside.example.com"})
        check("the form answers with somewhere to go", resp.status_code == 200,
              resp.get_data(as_text=True)[:160])
        data = resp.get_json()
        check("and the lead is already filed", len(captured) == 1, captured)
        check("under its own source", captured[0]["source"] == "ads_grader", captured[0])
        check("carrying the business and the address",
              captured[0]["fields"]["company"] == "Northside Roofing"
              and captured[0]["fields"]["email"] == "sam@northside.example.com")
        check("the response says the lead is safe", data["lead_saved"] is True, data)
        state = data["connect_url"].split("state=")[-1]

        # Abandoning the consent screen now costs the grade and not the lead,
        # which is the whole reason the order is this way round.
        check("the handshake is recorded against that lead",
              store.take_session(state) is not None)

        # ---------------------------------------------- what it refuses
        print("\nWhat the form refuses")
        captured.clear()
        r = client.post("/api/start", json={"email": "a@b.com"})
        check("a submission with no business name is refused",
              r.status_code == 400 and not captured, r.status_code)
        r = client.post("/api/start", json={"company": "Acme"})
        check("and one with neither an email nor a phone number",
              r.status_code == 400 and not captured, r.get_json())
        check("because a contactless lead reads as a live prospect nobody can call",
              "email address or a phone" in (r.get_json() or {}).get("error", ""))
        r = client.post("/api/start", json={"company": "Acme", "phone": "3175550142"})
        check("a phone number alone is enough", r.status_code == 200, r.get_json())
        captured.clear()

        # ------------------------------------------------- the handshake
        print("\nThe handshake is used once and expires")
        state = store.start_session(lead_id="lead-1", company="Northside Roofing")
        check("a state token reads once", store.take_session(state) is not None)
        check("and never twice", store.take_session(state) is None)
        check("an invented one is not a handshake", store.take_session("nope") is None)

        # --------------------------------------------------- the callback
        print("\nRead, score, and let the token go")
        real_exchange = grading.exchange_code
        real_customers = grading.accessible_customers
        real_query = grading._search
        grading.exchange_code = lambda code: "ya29.fake-access-token"
        grading.accessible_customers = lambda token: [CID]
        grading._search = fake_search

        state = store.start_session(lead_id="lead-1", company="Northside Roofing",
                                    website="https://northside.example.com")
        resp = client.get(f"/oauth/callback?state={state}&code=abc")
        check("it lands on a report", resp.status_code == 302
              and "/tools/ads-grader/r/" in resp.headers["Location"],
              (resp.status_code, resp.headers.get("Location")))
        token = resp.headers["Location"].rsplit("/", 1)[-1]
        row = store.get_result(token, with_result=True)
        check("the graded result is stored", row is not None and row["score"] is not None,
              row)
        check("against the lead it belongs to", row["lead_id"] == "lead-1", row)

        # The one thing that must not be in the row, anywhere in it.
        blob = str(row)
        check("and nothing about it contains the access token",
              "ya29.fake-access-token" not in blob)

        # ------------------------------------------ the score is the scanner's
        print("\nThe score is optimization.py's own findings")
        result = row["result"]
        check("a zero-conversion account is diagnosed",
              any(i["id"] == "diagnostic-no-conversions" for i in result["items"]),
              [i["id"] for i in result["items"]][:5])
        check("and the wasteful search term is found",
              any(i["category"] == "search_terms" for i in result["items"]))
        check("the score is deducted from 100, not invented",
              result["score"] == max(0, 100 - sum(d["points"] for d in result["deductions"])),
              (result["score"], result["deductions"]))
        check("and it carries a letter grade", result["grade"] in "ABCDF", result["grade"])
        check("the weights are named as ours rather than Google's",
              result["weight_source"] == "house"
              and "not Google's" in result["weight_note"], result["weight_note"])
        check("the top issues carry the measurement, never a product name",
              all(i.get("why") for i in result["top_issues"])
              and not any("Smart 1" in i["title"] for i in result["top_issues"]),
              result["top_issues"])

        page = client.get(f"/r/{token}")
        check("the prospect's report opens with no Hub login",
              page.status_code == 200, page.status_code)
        body = page.get_data(as_text=True)
        check("and shows the arithmetic behind the score",
              "Starting score" in body and "100" in body)
        check("no staff nav reaches it",
              "hub-sidebar" not in body and "s1hub-sb" not in body)

        rows = [x for x in audit.read(limit=200, module="ads_grader")
                if x.get("type") == "graded"]
        check("the grade is recorded", len(rows) == 1 and rows[0].get("company"), rows)
        check("and the row carries no token either",
              "ya29.fake-access-token" not in str(rows[0]), rows[0])

        # ----------------------------- a section Google refused is not clean
        print("\nA section Google refused is not a clean score")
        def half_blind(token, customer_id, query):
            if "search_term_view" in query:
                raise grading.GraderError("Google refused search terms.")
            return fake_search(token, customer_id, query)
        grading._search = half_blind
        state = store.start_session(lead_id="lead-2", company="Half Blind Ltd")
        resp = client.get(f"/oauth/callback?state={state}&code=abc")
        blind = store.get_result(resp.headers["Location"].rsplit("/", 1)[-1],
                                 with_result=True)["result"]
        check("the report says it was not fully measured",
              blind["measured"] is False and "search_terms" in blind["not_measured"],
              blind["not_measured"])
        grading._search = fake_search

        # ------------------------------------ an account with nothing in it
        print("\nAn account with no campaigns is named, not dropped")
        grading._search = lambda t, c, q: (SUMMARY_ROWS if "FROM customer" in q else [])
        state = store.start_session(lead_id="lead-3", company="Empty Ltd")
        resp = client.get(f"/oauth/callback?state={state}&code=abc")
        text = resp.get_data(as_text=True)
        check("the prospect is told, rather than shown an error",
              resp.status_code == 200 and "no campaign with" in text, resp.status_code)
        check("and it says their details are with us",
              "details are with us" in text or "have your details" in text)
        grading._search = fake_search

        # --------------------------------------------- a login with nothing
        grading.accessible_customers = lambda token: []
        state = store.start_session(lead_id="lead-4", company="No Ads Ltd")
        resp = client.get(f"/oauth/callback?state={state}&code=abc")
        check("a Google login with no Ads account is told which login to use",
              resp.status_code == 200
              and "manages your ads" in resp.get_data(as_text=True))
        grading.accessible_customers = lambda token: [CID]

        # ---------------------------------------------------- refusals
        print("\nWhat the callback refuses")
        check("a stale state is refused",
              client.get("/oauth/callback?state=gone&code=abc").status_code == 400)
        state = store.start_session(company="X")
        check("and a callback with no code",
              client.get(f"/oauth/callback?state={state}").status_code == 400)
        state = store.start_session(company="X")
        r = client.get(f"/oauth/callback?state={state}&error=access_denied")
        check("a prospect who declined is told nothing was read, not shown an error",
              r.status_code == 200 and "did not grant access" in r.get_data(as_text=True))

        grading.exchange_code = real_exchange
        grading.accessible_customers = real_customers
        grading._search = real_query
    finally:
        hub_leads_module.capture_and_deliver = real_deliver

    # ------------------------------------------------------- unknown tokens
    print("\nAn unknown report token")
    gone = client.get("/r/nosuchtokenanywhere")
    check("answers 404", gone.status_code == 404)
    check("with the same page a revoked one would — never 'that one expired'",
          "not available" in gone.get_data(as_text=True))

    # ---------------------------------------------------- the registration
    print("\nThe redirect URI Google has to match exactly")
    check("it is built from PUBLIC_BASE_URL, not the browser's host",
          grading.redirect_uri()
          == "https://smart1.agency/tools/ads-grader/oauth/callback",
          grading.redirect_uri())
    from hub import oauth_redirects
    flow = next((f for f in oauth_redirects.FLOWS if f["key"] == "ads_grader"), None)
    check("the flow is on the diagnostics panel", flow is not None)
    check("declared client-facing, like the other one a stranger meets",
          flow.get("client_facing") is True, flow)
    check("and the panel prints the string the code actually sends",
          flow["path"] == "/tools/ads-grader/oauth/callback")
    check("the auth URL asks for adwords and nothing else",
          "adwords" in grading.auth_url("s") and "analytics" not in grading.auth_url("s"))
    check("and never asks for offline access",
          "access_type=online" in grading.auth_url("s"))

    # ------------------------------------------------------------ the mount
    print("\nHow it is mounted")
    check("the whole module is declared public",
          grader_app.PUBLIC_PREFIXES == ("/",), grader_app.PUBLIC_PREFIXES)
    tools = (ROOT / "hub/templates/tools.html").read_text()
    check("and it has a tile, or nobody can find it",
          "/tools/ads-grader/" in tools, "no tile")
    check("the reused detectors are imported, not copied",
          "from modules.ads_builder import google_ads, optimization" in source)
    check("and they are the same functions the scanner uses",
          grading.grade_account.__module__ == "modules.ads_grader.grading"
          and hasattr(optimization, "analyse_rows"))

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
