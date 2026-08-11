import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

from config import SETTINGS


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _dsn():
    url = (SETTINGS.database_url or "").strip()

    # Tolerate a value pasted with surrounding quotes.
    if len(url) >= 2 and url[0] == url[-1] and url[0] in "\"'":
        url = url[1:-1].strip()

    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Create a Render Postgres database and put its "
            "Internal Database URL (it starts with 'postgresql://') in the DATABASE_URL "
            "environment variable."
        )

    # Render's scheme is 'postgresql://'. Accept the legacy 'postgres://' alias too.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]

    # A valid psycopg2 DSN is either a 'postgresql://...' URI or a 'key=value ...'
    # keyword string. A bare value like the database *name* is neither, and psycopg2
    # fails deep inside with a cryptic 'missing "=" ...' error. Catch it here instead.
    looks_like_uri = url.startswith("postgresql://")
    looks_like_kv = "=" in url.split(" ", 1)[0]
    if not (looks_like_uri or looks_like_kv):
        preview = url[:24] + ("…" if len(url) > 24 else "")
        raise RuntimeError(
            "DATABASE_URL does not look like a valid Postgres connection string "
            f"(got '{preview}'). It must be the full Internal Database URL from Render, "
            "starting with 'postgresql://user:password@host/dbname' — not just the "
            "database name or hostname. Copy it from your Render Postgres instance "
            "(Connect → Internal Database URL) and update the DATABASE_URL env var."
        )

    return url


def db_path():
    """A non-secret label describing the database, for /health display."""
    url = SETTINGS.database_url or ""
    if not url:
        return "postgres (DATABASE_URL not set)"
    try:
        u = urlparse(url)
        return f"postgres://{u.hostname or '?'}{u.path or ''}"
    except Exception:
        return "postgres"


class _Cursor:
    """Adapter so call sites written for sqlite3 keep working on psycopg2:

    - translates '?' placeholders to '%s'
    - .execute() returns the underlying cursor, which is iterable and has
      fetchone()/fetchall(), matching sqlite3's execute() behaviour.
    """

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        self._cur.execute(sql.replace("?", "%s"), params or ())
        return self._cur

    def __getattr__(self, name):
        return getattr(self._cur, name)


@contextmanager
def connection():
    conn = psycopg2.connect(_dsn())
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield _Cursor(cur)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _columns(c, table):
    rows = c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name=?",
        (table,),
    ).fetchall()
    return {r["column_name"] for r in rows}


def _add_column(c, table, definition):
    # Postgres supports ADD COLUMN IF NOT EXISTS, so this is idempotent.
    c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {definition}")


def init_db():
    with connection() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS plans(
                plan_id TEXT PRIMARY KEY,
                name TEXT,
                monthly_price REAL,
                yearly_price REAL,
                bg_monthly_price REAL,
                bg_yearly_price REAL,
                base_plan TEXT,
                hidden INTEGER,
                visible INTEGER,
                pages INTEGER,
                storage REAL,
                bandwidth REAL,
                contributors INTEGER,
                store_products INTEGER,
                websites INTEGER,
                raw_json TEXT,
                updated_at TEXT
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS projects(
                project_id TEXT PRIMARY KEY,
                name TEXT,
                status TEXT,
                plan_id TEXT,
                plan_name TEXT,
                created TEXT,
                billing_period TEXT,
                last_billing_date TEXT,
                next_billing_date TEXT,
                recurring_manual_subscription INTEGER,
                in_free_month INTEGER,
                overcharge_emails INTEGER,
                bandwidth_period TEXT,
                last_invoice_id TEXT,
                raw_json TEXT,
                updated_at TEXT,
                source TEXT DEFAULT 'API'
            )"""
        )
        _add_column(c, "projects", "source TEXT DEFAULT 'API'")

        c.execute(
            """CREATE TABLE IF NOT EXISTS websites(
                website_id TEXT PRIMARY KEY,
                project_id TEXT,
                name TEXT,
                domain TEXT,
                subdomain TEXT,
                active INTEGER,
                type TEXT,
                created TEXT,
                has_template INTEGER,
                sent_emails INTEGER,
                marketing_allowance INTEGER,
                marketing_risk INTEGER,
                marketing_subscribers INTEGER,
                raw_json TEXT,
                updated_at TEXT
            )"""
        )
        for definition in [
            "has_template INTEGER",
            "sent_emails INTEGER",
            "marketing_allowance INTEGER",
            "marketing_risk INTEGER",
            "marketing_subscribers INTEGER",
        ]:
            _add_column(c, "websites", definition)

        c.execute(
            """CREATE TABLE IF NOT EXISTS project_meta(
                project_id TEXT PRIMARY KEY,
                client_price REAL,
                platform_cost REAL,
                partner TEXT,
                notes TEXT DEFAULT '',
                internal_client_name TEXT DEFAULT '',
                lifecycle_state TEXT DEFAULT '',
                cancelled_at TEXT DEFAULT '',
                updated_at TEXT
            )"""
        )
        for definition in [
            "client_price REAL",
            "platform_cost REAL",
            "partner TEXT DEFAULT ''",
            "notes TEXT DEFAULT ''",
            "internal_client_name TEXT DEFAULT ''",
            "lifecycle_state TEXT DEFAULT ''",
            "cancelled_at TEXT DEFAULT ''",
            "updated_at TEXT DEFAULT ''",
        ]:
            _add_column(c, "project_meta", definition)
        cols = _columns(c, "project_meta")
        if "retail_price" in cols:
            c.execute(
                "UPDATE project_meta SET client_price=COALESCE(client_price,retail_price)"
            )

        c.execute(
            """CREATE TABLE IF NOT EXISTS templates(
                template_id TEXT PRIMARY KEY,
                name TEXT,
                primary_categories TEXT,
                categories TEXT,
                visible INTEGER,
                system_template INTEGER,
                preview_url TEXT,
                thumb TEXT,
                raw_json TEXT,
                updated_at TEXT
            )"""
        )

        c.execute(
            """CREATE TABLE IF NOT EXISTS sync_runs(
                id SERIAL PRIMARY KEY,
                started_at TEXT,
                completed_at TEXT,
                status TEXT,
                projects_count INTEGER,
                plans_count INTEGER,
                templates_count INTEGER DEFAULT 0,
                details TEXT
            )"""
        )
        _add_column(c, "sync_runs", "templates_count INTEGER DEFAULT 0")


def upsert_plan(p):
    vals = (
        str(p.get("id", "")),
        p.get("name", ""),
        p.get("monthlyPrice"),
        p.get("yearlyPrice"),
        p.get("bgMonthlyPrice"),
        p.get("bgYearlyPrice"),
        p.get("basePlan", ""),
        int(bool(p.get("hidden"))),
        int(bool(p.get("visible", True))),
        p.get("pages"),
        p.get("storage"),
        p.get("bandwidth"),
        p.get("contributors"),
        p.get("storeProducts"),
        p.get("websites"),
        json.dumps(p),
        now_iso(),
    )
    with connection() as c:
        c.execute(
            """INSERT INTO plans VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(plan_id) DO UPDATE SET
                 name=excluded.name,
                 monthly_price=excluded.monthly_price,
                 yearly_price=excluded.yearly_price,
                 bg_monthly_price=COALESCE(NULLIF(excluded.bg_monthly_price,0), plans.bg_monthly_price),
                 bg_yearly_price=COALESCE(NULLIF(excluded.bg_yearly_price,0), plans.bg_yearly_price),
                 base_plan=excluded.base_plan,
                 hidden=excluded.hidden,
                 visible=excluded.visible,
                 pages=excluded.pages,
                 storage=excluded.storage,
                 bandwidth=excluded.bandwidth,
                 contributors=excluded.contributors,
                 store_products=excluded.store_products,
                 websites=excluded.websites,
                 raw_json=excluded.raw_json,
                 updated_at=excluded.updated_at""",
            vals,
        )


def upsert_template(t):
    vals = (
        str(t.get("id", "")),
        t.get("name", ""),
        t.get("primaryCategories", ""),
        t.get("categories", ""),
        int(bool(t.get("visible", True))),
        int(bool(t.get("systemTemplate"))),
        t.get("previewUrl", ""),
        t.get("thumb", ""),
        json.dumps(t),
        now_iso(),
    )
    with connection() as c:
        c.execute(
            """INSERT INTO templates VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(template_id) DO UPDATE SET
                 name=excluded.name,
                 primary_categories=excluded.primary_categories,
                 categories=excluded.categories,
                 visible=excluded.visible,
                 system_template=excluded.system_template,
                 preview_url=excluded.preview_url,
                 thumb=excluded.thumb,
                 raw_json=excluded.raw_json,
                 updated_at=excluded.updated_at""",
            vals,
        )


def upsert_project(p, source="API"):
    if not p or p.get("id") in (None, ""):
        return
    plan = p.get("plan") or {}
    recurring = p.get("recurringManualSubscription")
    free_month = p.get("inFreeMonth")
    overcharge = p.get("overchargeEmails")
    vals = (
        str(p.get("id")),
        p.get("name", ""),
        p.get("status", ""),
        str(plan.get("id", "")) if plan.get("id") is not None else "",
        plan.get("name", ""),
        p.get("created", ""),
        p.get("billingPeriod", ""),
        p.get("lastBillingDate", ""),
        p.get("nextBillingDate", ""),
        None if recurring is None else int(bool(recurring)),
        None if free_month is None else int(bool(free_month)),
        None if overcharge is None else int(bool(overcharge)),
        p.get("bandwidthPeriod", ""),
        str(p.get("lastInvoiceId", "")) if p.get("lastInvoiceId") is not None else "",
        json.dumps(p),
        now_iso(),
        source,
    )
    with connection() as c:
        c.execute(
            """INSERT INTO projects(
                 project_id,name,status,plan_id,plan_name,created,billing_period,
                 last_billing_date,next_billing_date,recurring_manual_subscription,
                 in_free_month,overcharge_emails,bandwidth_period,last_invoice_id,
                 raw_json,updated_at,source
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE projects.name END,
                 status=CASE WHEN excluded.status<>'' THEN excluded.status ELSE projects.status END,
                 plan_id=CASE WHEN excluded.plan_id<>'' THEN excluded.plan_id ELSE projects.plan_id END,
                 plan_name=CASE WHEN excluded.plan_name<>'' THEN excluded.plan_name ELSE projects.plan_name END,
                 created=CASE WHEN excluded.created<>'' THEN excluded.created ELSE projects.created END,
                 billing_period=CASE WHEN excluded.billing_period<>'' THEN excluded.billing_period ELSE projects.billing_period END,
                 last_billing_date=CASE WHEN excluded.last_billing_date<>'' THEN excluded.last_billing_date ELSE projects.last_billing_date END,
                 next_billing_date=CASE WHEN excluded.next_billing_date<>'' THEN excluded.next_billing_date ELSE projects.next_billing_date END,
                 recurring_manual_subscription=COALESCE(excluded.recurring_manual_subscription,projects.recurring_manual_subscription),
                 in_free_month=COALESCE(excluded.in_free_month,projects.in_free_month),
                 overcharge_emails=COALESCE(excluded.overcharge_emails,projects.overcharge_emails),
                 bandwidth_period=CASE WHEN excluded.bandwidth_period<>'' THEN excluded.bandwidth_period ELSE projects.bandwidth_period END,
                 last_invoice_id=CASE WHEN excluded.last_invoice_id<>'' THEN excluded.last_invoice_id ELSE projects.last_invoice_id END,
                 raw_json=excluded.raw_json,
                 updated_at=excluded.updated_at,
                 source=excluded.source""",
            vals,
        )


def replace_websites(project_id, items):
    with connection() as c:
        existing = {
            r["website_id"]: dict(r)
            for r in c.execute(
                "SELECT * FROM websites WHERE project_id=?", (str(project_id),)
            )
        }
        seen = set()
        for x in items:
            wid = str(x.get("id", ""))
            if not wid:
                continue
            seen.add(wid)
            old = existing.get(wid, {})
            active = x.get("active")
            if active is None:
                active = old.get("active")
            has_template = x.get("hasTemplate")
            if has_template is None:
                has_template = old.get("has_template")
            vals = (
                wid,
                str(project_id),
                x.get("name", old.get("name", "")),
                x.get("domain", old.get("domain", "")) or "",
                x.get("subdomain", old.get("subdomain", "")) or "",
                None if active is None else int(bool(active)),
                x.get("type", old.get("type", "")),
                x.get("created", old.get("created", "")),
                None if has_template is None else int(bool(has_template)),
                x.get("sentEmails", old.get("sent_emails")),
                x.get("marketingAllowance", old.get("marketing_allowance")),
                None if x.get("marketingRisk") is None else int(bool(x.get("marketingRisk"))),
                x.get("marketingSubscribers", old.get("marketing_subscribers")),
                json.dumps(x),
                now_iso(),
            )
            c.execute(
                """INSERT INTO websites(
                    website_id,project_id,name,domain,subdomain,active,type,created,
                    has_template,sent_emails,marketing_allowance,marketing_risk,
                    marketing_subscribers,raw_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(website_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    name=excluded.name,
                    domain=excluded.domain,
                    subdomain=excluded.subdomain,
                    active=COALESCE(excluded.active,websites.active),
                    type=excluded.type,
                    created=CASE WHEN excluded.created<>'' THEN excluded.created ELSE websites.created END,
                    has_template=COALESCE(excluded.has_template,websites.has_template),
                    sent_emails=COALESCE(excluded.sent_emails,websites.sent_emails),
                    marketing_allowance=COALESCE(excluded.marketing_allowance,websites.marketing_allowance),
                    marketing_risk=COALESCE(excluded.marketing_risk,websites.marketing_risk),
                    marketing_subscribers=COALESCE(excluded.marketing_subscribers,websites.marketing_subscribers),
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at""",
                vals,
            )
        # Official API is authoritative for the current project's site list.
        if seen:
            placeholders = ",".join("?" for _ in seen)
            c.execute(
                f"DELETE FROM websites WHERE project_id=? AND website_id NOT IN ({placeholders})",
                [str(project_id), *sorted(seen)],
            )
        else:
            c.execute("DELETE FROM websites WHERE project_id=?", (str(project_id),))


def save_meta(project_id, client_price=None, platform_cost=None, partner="", notes="", internal_client_name=""):
    with connection() as c:
        c.execute(
            """INSERT INTO project_meta(
                project_id,client_price,platform_cost,partner,notes,internal_client_name,
                lifecycle_state,cancelled_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET
                client_price=excluded.client_price,
                platform_cost=excluded.platform_cost,
                partner=excluded.partner,
                notes=excluded.notes,
                internal_client_name=excluded.internal_client_name,
                updated_at=excluded.updated_at""",
            (
                str(project_id),
                client_price,
                platform_cost,
                partner,
                notes,
                internal_client_name,
                "",
                "",
                now_iso(),
            ),
        )


def set_lifecycle(project_id, state):
    state = state or ""
    cancelled = now_iso() if state == "CANCELLED" else ""
    with connection() as c:
        c.execute(
            """INSERT INTO project_meta(project_id,lifecycle_state,cancelled_at,updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 lifecycle_state=excluded.lifecycle_state,
                 cancelled_at=excluded.cancelled_at,
                 updated_at=excluded.updated_at""",
            (str(project_id), state, cancelled, now_iso()),
        )


def set_plan_cost(plan_id, monthly_cost):
    """Set the platform (wholesale) cost for a plan/package. Stored in
    bg_monthly_price, which every effective-cost calc already uses as the
    per-site default. Preserved across catalog syncs (see upsert_plan)."""
    with connection() as c:
        c.execute(
            "UPDATE plans SET bg_monthly_price=?, updated_at=? WHERE plan_id=?",
            (monthly_cost, now_iso(), str(plan_id)),
        )


def set_platform_cost(project_id, cost):
    """Set only the per-site actual platform cost override, without touching
    any other Smart 1 metadata (used by the invoice-cost import)."""
    with connection() as c:
        c.execute(
            """INSERT INTO project_meta(project_id,platform_cost,updated_at)
               VALUES(?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 platform_cost=excluded.platform_cost,
                 updated_at=excluded.updated_at""",
            (str(project_id), cost, now_iso()),
        )


def infer_partner(name):
    n = (name or "").strip()
    if " - " in n:
        p = n.split(" - ", 1)[0].strip()
        return {"S1M": "Smart 1", "TMRG": "TMRG"}.get(p, p)
    if n.lower().startswith("smart 1"):
        return "Smart 1"
    return "Direct / Other"


def get_project(pid):
    with connection() as c:
        row = c.execute(
            """SELECT p.*,
                      pl.monthly_price,pl.yearly_price,pl.bg_monthly_price,pl.bg_yearly_price,
                      pl.base_plan,pl.pages,pl.storage,pl.bandwidth,pl.contributors,
                      pl.store_products,pl.hidden,pl.visible,
                      m.client_price,m.platform_cost,m.partner,m.notes,m.internal_client_name,
                      m.lifecycle_state,m.cancelled_at
               FROM projects p
               LEFT JOIN plans pl ON pl.plan_id=p.plan_id
               LEFT JOIN project_meta m ON m.project_id=p.project_id
               WHERE p.project_id=?""",
            (str(pid),),
        ).fetchone()
        return dict(row) if row else None


def get_websites(pid):
    with connection() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM websites WHERE project_id=? ORDER BY name", (str(pid),)
            )
        ]


def get_website(website_id):
    with connection() as c:
        r = c.execute(
            "SELECT * FROM websites WHERE website_id=?", (str(website_id),)
        ).fetchone()
        return dict(r) if r else None


def list_plans_for_ui():
    with connection() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM plans ORDER BY hidden, monthly_price, name"
            )
        ]


def list_templates_for_ui():
    with connection() as c:
        return [
            dict(r)
            for r in c.execute(
                "SELECT * FROM templates WHERE visible=1 ORDER BY system_template,name"
            )
        ]


def known_project_ids():
    with connection() as c:
        return [
            r["project_id"]
            for r in c.execute("SELECT project_id FROM projects ORDER BY project_id")
        ]


def query_projects(q="", status="", plan="", partner="", page=1, per_page=50):
    where = []
    args = []
    if q:
        like = f"%{q}%"
        where.append(
            "(p.name ILIKE ? OR w.domain ILIKE ? OR p.project_id ILIKE ? OR w.website_id ILIKE ?)"
        )
        args += [like, like, like, like]
    if status:
        where.append("p.status=?")
        args.append(status)
    if plan:
        where.append("p.plan_id=?")
        args.append(plan)
    if partner:
        where.append("(m.partner=? OR p.name ILIKE ?)")
        args += [partner, partner + " - %"]
    wc = "WHERE " + " AND ".join(where) if where else ""
    off = (page - 1) * per_page
    with connection() as c:
        rows = [
            dict(r)
            for r in c.execute(
                f"""SELECT DISTINCT p.*,
                            pl.monthly_price,pl.yearly_price,pl.bg_monthly_price,pl.bg_yearly_price,
                            m.client_price,m.platform_cost,m.partner,m.notes,m.internal_client_name,
                            m.lifecycle_state,m.cancelled_at,
                            (SELECT COALESCE(NULLIF(ww.domain,''), NULLIF(ww.subdomain,''), NULLIF(ww.name,''))
                               FROM websites ww WHERE ww.project_id=p.project_id
                               ORDER BY ww.website_id LIMIT 1) AS domain,
                            (CASE WHEN p.project_id ~ '^[0-9]+$' THEN CAST(p.project_id AS BIGINT) ELSE 0 END) AS _ord
                     FROM projects p
                     LEFT JOIN plans pl ON pl.plan_id=p.plan_id
                     LEFT JOIN project_meta m ON m.project_id=p.project_id
                     LEFT JOIN websites w ON w.project_id=p.project_id
                     {wc}
                     ORDER BY _ord DESC
                     LIMIT ? OFFSET ?""",
                args + [per_page, off],
            )
        ]
        count = c.execute(
            f"""SELECT COUNT(DISTINCT p.project_id) AS n
                FROM projects p
                LEFT JOIN project_meta m ON m.project_id=p.project_id
                LEFT JOIN websites w ON w.project_id=p.project_id
                {wc}""",
            args,
        ).fetchone()["n"]
    for r in rows:
        r["partner_display"] = r.get("partner") or infer_partner(r.get("name"))
    return rows, count


def metrics():
    with connection() as c:
        statuses = {
            r["status"]: r["n"]
            for r in c.execute("SELECT status,COUNT(*) AS n FROM projects GROUP BY status")
        }
        total = c.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
        plans = [
            dict(r)
            for r in c.execute(
                "SELECT plan_id,name,monthly_price,yearly_price,hidden,visible FROM plans ORDER BY monthly_price,name"
            )
        ]
        meta = [
            dict(r)
            for r in c.execute(
                """SELECT p.project_id,p.name,p.status,p.plan_id,pl.monthly_price,
                          pl.bg_monthly_price,m.client_price,m.platform_cost,m.partner,
                          m.lifecycle_state
                   FROM projects p
                   LEFT JOIN plans pl ON pl.plan_id=p.plan_id
                   LEFT JOIN project_meta m ON m.project_id=p.project_id"""
            )
        ]
        last = c.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
        template_count = c.execute("SELECT COUNT(*) AS n FROM templates").fetchone()["n"]
    return total, statuses, plans, meta, (dict(last) if last else None), template_count


def alert_rows(limit=100):
    today = datetime.now().date()
    soon = today + timedelta(days=7)
    with connection() as c:
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM projects ORDER BY CASE WHEN project_id ~ '^[0-9]+$' THEN CAST(project_id AS BIGINT) ELSE 0 END DESC"
            )
        ]
    out = []
    for r in rows:
        kind = None
        if r["status"] == "TRIAL":
            kind = "Trial needs review"
        elif r["status"] == "EXPIRED":
            kind = "Expired site"
        elif r["status"] == "ACTIVE" and r["recurring_manual_subscription"] == 0:
            kind = "Active but non-recurring"
        if not kind and r.get("next_billing_date"):
            for fmt in ("%m/%d/%Y", "%B %d, %Y"):
                try:
                    d = datetime.strptime(r["next_billing_date"], fmt).date()
                    if today <= d <= soon:
                        kind = "Billing within 7 days"
                    break
                except ValueError:
                    pass
        if kind:
            r["alert_kind"] = kind
            out.append(r)
            if len(out) >= limit:
                break
    return out


def start_sync():
    with connection() as c:
        row = c.execute(
            "INSERT INTO sync_runs(started_at,status,projects_count,plans_count,templates_count,details) VALUES(?,?,?,?,?,?) RETURNING id",
            (now_iso(), "RUNNING", 0, 0, 0, ""),
        ).fetchone()
        return row["id"]


def finish_sync(run_id, status, projects_count=0, plans_count=0, templates_count=0, details=""):
    with connection() as c:
        c.execute(
            """UPDATE sync_runs SET completed_at=?,status=?,projects_count=?,plans_count=?,templates_count=?,details=? WHERE id=?""",
            (now_iso(), status, projects_count, plans_count, templates_count, details, run_id),
        )
