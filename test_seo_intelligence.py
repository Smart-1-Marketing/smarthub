"""modules/seo_intelligence -- the recommendation half, which had never once run.

    python3 test_seo_intelligence.py

`SEORecommendation` declared a column named `query`, because a Search Console
search term is the obvious thing to call one. `db.Model` carries
Flask-SQLAlchemy's `query` descriptor -- the thing every
`Model.query.filter_by(...)` in this Hub reads -- and a mapped attribute of
that name on a subclass shadows it for that one class. So
`SEORecommendation.query` answered the column's InstrumentedAttribute, and
`.filter_by()` on it raised:

    AttributeError: Neither 'InstrumentedAttribute' object nor 'Comparator'
    object associated with SEORecommendation.query has an attribute 'filter_by'

Three things sat behind that one line, and every screen looked fine:

* `service._save_recommendations()` reaches it unconditionally, so **every
  weekly refresh rolled back** before the snapshot or the memory was written,
  and the property's row recorded the error where no page drew it.
* `/api/action-queue` and `/api/clients/<id>/overview` answered **500**; the
  overview page `await r.json()`-ed the HTML, the promise rejected unhandled,
  and the Agency Action Queue stayed blank -- not even its own empty state.
* The weekly job folded each property's failure into a list and returned
  normally, so the scheduler panel drew a green pill over a run that had
  refreshed nobody.

The fix keeps the column's *name* -- `search_query = db.Column("query", ...)`
maps the same table with no migration -- and moves only the attribute. What
this file holds: the descriptor is back, the refresh path lands, the routes
answer, the page says when they do not, the job reads red when every refresh
failed, and `hub/integrity.check_shadowed_model_query()` refuses the next
model that does this.
"""
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-seo-intel-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["SECRET_KEY"] = "seo-intel-test"
os.environ["PANEL_PASSWORD"] = "test"

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


from flask import Flask                                            # noqa: E402
from sqlalchemy import text                                        # noqa: E402

from hub import auth, integrity                                    # noqa: E402
from hub.extensions import db                                      # noqa: E402
from modules.seo_intelligence import register_seo_intelligence     # noqa: E402
from modules.seo_intelligence import models as M                   # noqa: E402
from modules.seo_intelligence import service                       # noqa: E402
from modules.seo_intelligence import scheduler as job_mod          # noqa: E402

app = Flask(__name__)
app.config.update(TESTING=True, SECRET_KEY="test",
                  SQLALCHEMY_DATABASE_URI=os.environ["DATABASE_URL"])
db.init_app(app)
register_seo_intelligence(app)
_ctx = app.app_context()
_ctx.push()
db.create_all()


def row(query, page, impressions, clicks, ctr, position):
    return {"keys": [query, page], "impressions": impressions, "clicks": clicks,
            "ctr": ctr, "position": position}


# The two rows the recommendation tests already use: one that produces a
# title/meta opportunity and one that produces an FAQ.
LOW_CTR = row("ac repair columbus", "https://example.com/ac-repair", 5000, 50, .01, 4.2)
QUESTION = row("how much does furnace repair cost", "https://example.com/furnace", 900, 20, .022, 8.5)

_google = {"rows": [LOW_CTR, QUESTION]}
service.gsc.all_search_analytics = lambda token, site, start, end: list(_google["rows"])
service.gsc.list_sitemaps = lambda token, site: [{"path": "https://example.com/sitemap.xml"}]

try:
    # ------------------------------------------------------------- 1. the descriptor
    print("\nModel.query is the query again")
    print("-" * 46)
    check("SEORecommendation.query is a Query, not the column",
          hasattr(M.SEORecommendation.query, "filter_by"),
          type(M.SEORecommendation.query).__name__)
    cols = {c.name for c in M.SEORecommendation.__table__.columns}
    check("the table still has a column named query -- no migration",
          "query" in cols and "search_query" not in cols, sorted(cols))
    check("and search_query is the attribute that maps it",
          M.SEORecommendation.search_query.property.columns[0].name == "query")
    check("a filter_by over the model answers",
          M.SEORecommendation.query.filter_by(status="open").count() == 0)

    # ------------------------------------------------------------ 2. the refresh path
    print("\nthe weekly refresh lands")
    print("-" * 46)
    prop = service.upsert_property("acme-hvac", "sc-domain:example.com", "Acme HVAC")
    result = service.refresh_property(prop, "tok", end_date=date(2026, 9, 7))
    n = result["recommendations"]
    check("a refresh returns recommendations", n >= 2, result)
    written = M.SEORecommendation.query.filter_by(property_id=prop.id).count()
    check("every one of them is written", written == n, (written, n))
    stored = db.session.execute(text(
        "select query from seo_recommendations where query is not null")).fetchall()
    check("the search term lands in the column named query",
          any(r[0] == "how much does furnace repair cost" for r in stored), stored)
    check("the memory row is written", M.SEOMemory.query.filter_by(client_id="acme-hvac").first() is not None)
    check("the snapshot is written", M.SEOSnapshot.query.filter_by(property_id=prop.id).count() == 1)
    check("the property reads ok with no error",
          prop.last_sync_status == "ok" and not prop.last_sync_error,
          (prop.last_sync_status, prop.last_sync_error))

    service.refresh_property(prop, "tok", end_date=date(2026, 9, 7))
    check("a second refresh updates rather than duplicates",
          M.SEORecommendation.query.filter_by(property_id=prop.id).count() == n)

    faq = M.SEORecommendation.query.filter_by(property_id=prop.id, kind="faq").first()
    title = M.SEORecommendation.query.filter_by(property_id=prop.id, kind="title_meta").first()
    check("the FAQ row carries its search term", faq is not None and faq.search_query == QUESTION["keys"][0])
    title.status = "dismissed"
    db.session.commit()
    _google["rows"] = [LOW_CTR]                       # the question stops appearing
    service.refresh_property(prop, "tok", end_date=date(2026, 9, 7))
    db.session.refresh(faq)
    db.session.refresh(title)
    check("a condition that disappeared is auto-resolved", faq.status == "auto_resolved", faq.status)
    check("a human dismissal is never reopened", title.status == "dismissed", title.status)
    _google["rows"] = [LOW_CTR, QUESTION]
    service.refresh_property(prop, "tok", end_date=date(2026, 9, 7))
    db.session.refresh(faq)
    check("and one that came back is reopened", faq.status == "open", faq.status)

    # ------------------------------------------------------------------ 3. the routes
    print("\nthe routes answer, and the page says when they do not")
    print("-" * 46)
    client = app.test_client()
    client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Tester"))
    r = client.get("/seo/intelligence/api/action-queue?status=open")
    check("the action queue answers 200", r.status_code == 200, r.status_code)
    d = r.get_json() or {}
    check("with the open rows", d.get("count", 0) >= 1, d.get("count"))
    check("and each row carries its search term under the wire key `query`",
          d.get("items") and all("query" in it for it in d["items"])
          and any(it["query"] == QUESTION["keys"][0] for it in d["items"]))
    r = client.get("/seo/intelligence/api/clients/acme-hvac/overview")
    check("the client overview answers 200", r.status_code == 200, r.status_code)
    d = r.get_json() or {}
    check("with its open opportunities counted", d.get("open_opportunities", 0) >= 1)
    check("and the property's sync state on it",
          (d.get("properties") or [{}])[0].get("last_sync_status") == "ok")
    stranger = app.test_client()
    r = stranger.get("/seo/intelligence/api/action-queue")
    check("a stranger is still refused", r.status_code in (301, 302, 401, 403), r.status_code)

    r = client.get("/seo/intelligence/")
    page = r.get_data(as_text=True)
    check("the overview page renders", r.status_code == 200, r.status_code)
    check("a refused read is said rather than left blank",
          "couldNotRead(" in page and "if(!r.ok)" in page)
    check("and each property's last refresh is drawn, error included",
          "syncHtml(" in page and "last_sync_error" in page and "never refreshed" in page)

    rec_id = faq.id
    r = client.post(f"/seo/intelligence/api/recommendations/{rec_id}/status", json={"status": "dismissed"})
    check("a status change answers", r.status_code == 200 and (r.get_json() or {}).get("status") == "dismissed")

    # ------------------------------------------------------------ 4. the sweep
    print("\nthe next model that does this is refused")
    print("-" * 46)
    check("the check is registered at high",
          any(k == "shadowed_model_query" and sev == "high"
              for k, _l, sev, _fn in integrity.CHECKS))
    check("the repo carries no finding", integrity.check_shadowed_model_query() == [],
          integrity.check_shadowed_model_query())

    def found(src, rel="modules/probe/models.py"):
        return integrity.check_shadowed_model_query([(rel, src)])

    bad = ("from hub.extensions import db\n"
           "class Rec(db.Model):\n"
           "    id = db.Column(db.Integer, primary_key=True)\n"
           "    query = db.Column(db.String(10))\n")
    hit = found(bad)
    check("a column named query on a db.Model is a finding", len(hit) == 1, hit)
    check("named by class, file and line",
          hit and hit[0]["file"] == "modules/probe/models.py" and hit[0]["line"] == 4
          and "Rec.query" in hit[0]["detail"])
    annotated = ("from hub.extensions import db\n"
                 "from sqlalchemy.orm import Mapped, mapped_column\n"
                 "class Rec(db.Model):\n"
                 "    query: Mapped[str] = mapped_column()\n")
    check("an annotated mapping is the same finding", len(found(annotated)) == 1)
    fixed = ("from hub.extensions import db\n"
             "class Rec(db.Model):\n"
             '    search_query = db.Column("query", db.String(10))\n')
    check("the fix -- the column keeps its name -- is not", found(fixed) == [])
    base = ("from sqlalchemy.orm import declarative_base\n"
            "from sqlalchemy import Column, String\n"
            "Base = declarative_base()\n"
            "class Rec(Base):\n"
            "    __tablename__ = 'r'\n"
            "    query = Column(String(10), primary_key=True)\n")
    check("a classic declarative Base has nothing to shadow", found(base) == [])
    prose = ("from hub.extensions import db\n"
             '"""It used to read `query = db.Column(db.String(10))` on a Model."""\n'
             "class Rec(db.Model):\n"
             '    search_query = db.Column("query", db.String(10))\n')
    check("prose is not a mapping", found(prose) == [])
    check("a file that does not parse is skipped rather than raised on",
          found("class Rec(db.Model):\n    query = \n") == [])

    # ---------------------------------------------------------- 5. the job's verdict
    print("\nthe weekly job reads red when every refresh failed")
    print("-" * 46)
    job_mod.discover_and_backfill_existing_clients = lambda: {
        "refreshed_immediately": 0, "refresh_errors": []}
    job_mod.token_for_property = lambda prop: "tok"
    job_mod.mirror_client = lambda client_id: False

    def failing(prop, token):
        raise RuntimeError("Neither 'InstrumentedAttribute' object nor 'Comparator' object")

    prop.last_sync_status = "error"
    prop.last_sync_at = datetime.now(timezone.utc) - timedelta(days=9)
    db.session.commit()
    job_mod.refresh_property = failing
    try:
        job_mod.job_refresh_seo_intelligence(app)
    except RuntimeError as exc:
        msg = str(exc)
        check("a run that refreshed nobody raises", True)
        check("naming how many were attempted and the first error",
              "1 attempted" in msg and "InstrumentedAttribute" in msg
              and "sc-domain:example.com" in msg, msg)
    else:
        check("a run that refreshed nobody raises", False, "returned a dict instead")

    job_mod.refresh_property = lambda prop, token: None
    out = job_mod.job_refresh_seo_intelligence(app)
    check("a run that landed returns its counts", out.get("refreshed") == 1 and not out.get("errors"), out)

    second = service.upsert_property("beta-roofing", "sc-domain:beta.example", "Beta Roofing")
    second.last_sync_status = "error"
    db.session.commit()
    prop.last_sync_status = "error"
    db.session.commit()
    calls = {"n": 0}

    def half(p, token):
        calls["n"] += 1
        if p.id == second.id:
            raise RuntimeError("revoked grant")

    job_mod.refresh_property = half
    out = job_mod.job_refresh_seo_intelligence(app)
    check("one property failing beside one that landed is that row's state, not the job's",
          out.get("refreshed") == 1 and len(out.get("errors") or []) == 1, out)

    prop.last_sync_status = second.last_sync_status = "ok"
    prop.last_sync_at = second.last_sync_at = datetime.now(timezone.utc)
    db.session.commit()
    out = job_mod.job_refresh_seo_intelligence(app)
    check("and a run with nothing due is not a failure", out.get("skipped_not_due") == 2 and out.get("refreshed") == 0, out)

    job_mod.discover_and_backfill_existing_clients = lambda: {
        "refreshed_immediately": 0,
        "refresh_errors": [{"client": "Gamma", "site_url": "sc-domain:gamma.example", "error": "RuntimeError: x"}]}
    try:
        job_mod.job_refresh_seo_intelligence(app)
    except RuntimeError as exc:
        check("a first-time backfill that failed every refresh counts too", "sc-domain:gamma.example" in str(exc), exc)
    else:
        check("a first-time backfill that failed every refresh counts too", False, "returned a dict")
finally:
    _ctx.pop()
    shutil.rmtree(_TMP, ignore_errors=True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
