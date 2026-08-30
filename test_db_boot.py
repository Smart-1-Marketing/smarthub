"""A database blip at boot, and the four ways it used to become permanent.

    python3 test_db_boot.py

Same shape as the other test files: no pytest, no new dependencies, and it runs
against a temporary data directory and a throwaway SQLite database, so it never
touches /var/data or the real one.

## Why this file exists

The live service logged this on a deploy, for about six seconds, across four
modules and both gunicorn workers:

    psycopg2.OperationalError: connection to server ... failed:
    SSL connection has been closed unexpectedly

and then came up and served. Nothing was misconfigured; the database was
briefly not answering while the deploy raced it awake. Every consequence below
outlived the fault, and each is invisible from the screen it breaks:

  1.  the boot DDL did not retry     - one connection error a second before the
                                       database would have answered, and the
                                       module carries the verdict for the life
                                       of the worker

  2.  a verdict was never re-asked   - modules/scans 503s EVERY route from a
                                       before_request, which is the widget on a
                                       client's own website and the audit a
                                       prospect reads; modules/image_picker
                                       raises from session(), which is the
                                       client upload link. pool_pre_ping means
                                       every request after the blip would have
                                       reconnected happily. Nothing asked.

  3.  scans logged nothing at all    - it recorded a module-wide 503 and wrote
                                       no line to the deploy log, so its half
                                       was invisible from either end

  4.  sign-in answered 500           - the account lookup in /login is a
                                       database read wrapped in `except
                                       ImportError`, so an unreachable Postgres
                                       escaped the route as "Internal Server
                                       Error" on the one page nobody can
                                       already be signed in to read. That is
                                       the whole company locked out, being told
                                       to check the one thing that is not
                                       wrong.

The retry is deliberately narrow: a password Postgres refuses answers the same
however many times it is asked, and retrying it is four failed logins and a
slower boot. So `is_transient()` is asserted in both directions, and so is the
one ordering mistake that would quietly undo the sign-in fix -- a broad
`except` placed above the wrong-password arm turns every bad password into
"the database is down".
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1dbboot_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "db-boot-test-secret"
os.environ.pop("PANEL_PASSWORD", None)

from werkzeug.test import Client                                    # noqa: E402

from hub import extensions                                          # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ------------------------------------------- which failures can answer again
section("A blip and a misconfiguration are different answers")

# The exact string the live service logged, verbatim.
LIVE = ("(psycopg2.OperationalError) connection to server at "
        '"dpg-d9lp695bedkc73c1ipr0-a.ohio-postgres.render.com" '
        "(18.118.220.241), port 5432 failed: "
        "SSL connection has been closed unexpectedly")

check("the error the deploy log actually carried is transient",
      extensions.is_transient(LIVE), True)
for text in ("could not connect to server: Connection refused",
             "FATAL:  the database system is starting up",
             "FATAL:  too many connections for role",
             "server closed the connection unexpectedly"):
    check(f"...so is {text[:38]!r}", extensions.is_transient(text), True)

# The other direction is what keeps the retry from being a slow boot on a
# deployment that is genuinely misconfigured -- and what keeps a re-check off
# a verdict that cannot change.
for text in ('FATAL:  password authentication failed for user "hub"',
             'relation "hub_users" does not exist',
             'column quotes.pdf_url does not exist',
             'FATAL:  database "smarthub" does not exist'):
    check(f"{text[:40]!r} is NOT transient",
          extensions.is_transient(text), False)

check("an exception object is read as well as a string",
      extensions.is_transient(OSError("SSL connection has been closed "
                                      "unexpectedly")), True)
check("and nothing at all is not a failure",
      extensions.is_transient(""), False)


# ----------------------------------------------------- the boot DDL retries
section("The boot DDL retries a database that is still coming up")

_calls = {"n": 0}


class _FakeEngine:
    class dialect:
        name = "sqlite"


def _flaky():
    """Fails transiently twice, then succeeds -- the deploy racing the database."""
    _calls["n"] += 1
    if _calls["n"] < 3:
        raise OSError("SSL connection has been closed unexpectedly")


extensions._boot_wait_spent = 0.0
_err = extensions._locked_create_all(_FakeEngine(), _flaky)
check("a transient failure is retried until it answers", _err, "")
check("...which took three attempts", _calls["n"], 3)

_perm = {"n": 0}


def _refused():
    _perm["n"] += 1
    raise OSError('FATAL:  password authentication failed for user "hub"')


extensions._boot_wait_spent = 0.0
_err = extensions._locked_create_all(_FakeEngine(), _refused)
check("a refusal is returned rather than retried", bool(_err), True)
check("...on the first attempt, so boot is not slowed for an answer that "
      "cannot change", _perm["n"], 1)

# The budget is process-wide on purpose: create_all runs from nine places at
# import, so a per-call budget would multiply by nine on the one deploy where
# the database is genuinely down -- and gunicorn boots the app inside its own
# timeout.
_down = {"n": 0}


def _always_down():
    _down["n"] += 1
    raise OSError("could not connect to server: Connection refused")


extensions._boot_wait_spent = 0.0
_t0 = time.monotonic()
extensions._locked_create_all(_FakeEngine(), _always_down)
_first = time.monotonic() - _t0
_after_first = extensions.boot_wait_spent()

_t0 = time.monotonic()
extensions._locked_create_all(_FakeEngine(), _always_down)
_second = time.monotonic() - _t0

check("a database that never answers is given up on inside the budget",
      _first <= extensions._BOOT_WAIT_BUDGET + 1.0, True)
check("the budget is spent process-wide, not per call, so the second module "
      "waits only what is left of it", _second < _first, True)
check("...and nine modules cannot add up to nine budgets, which is the whole "
      "point: gunicorn boots the app inside its own timeout",
      _first + _second <= extensions._BOOT_WAIT_BUDGET + 1.0, True)
check("and the budget is reported, so a slow boot is explicable",
      _after_first > 0, True)


# The retry belongs to boot and nowhere else. hub/jsonstore.py re-initialises
# its mirror lazily, on the write path, so the budget would be spent inside
# somebody's save -- a database that is down costing every writer the full
# backoff in turn, which is the per-visit timeout this codebase refuses to
# carry on a page load. test_jsonstore.py catches the symptom ("saves stay
# fast"); this catches the cause, so the reason survives the next edit.
_lazy = {"n": 0}


def _lazy_down():
    _lazy["n"] += 1
    raise OSError("could not connect to server: Connection refused")


extensions._boot_wait_spent = 0.0
_t0 = time.monotonic()
extensions._locked_create_all(_FakeEngine(), _lazy_down, retry=False)
check("a caller that is not boot does not wait at all",
      (_lazy["n"], time.monotonic() - _t0 < 0.2), (1, True))
check("...and the budget is untouched by it",
      extensions.boot_wait_spent(), 0.0)
check("the one lazy caller asks for that by name, with the reason",
      "create_all_metadata(meta, retry=False)" in
      Path("hub/jsonstore.py").read_text(), True)


# modules/sites_admin talks to psycopg2 directly and creates its schema with
# its own SQL, so it could not share any of the above and gave up on the first
# connection error like everything else. Nothing there reads the verdict -- its
# pages open a fresh connection per call and recover by themselves -- but the
# schema step got no second chance.
_sites = {"n": 0}


def _sites_boot():
    _sites["n"] += 1
    if _sites["n"] < 3:
        raise OSError("SSL connection has been closed unexpectedly")


extensions._boot_wait_spent = 0.0
check("a boot step that raises is retried on the same budget",
      extensions.boot_retry(_sites_boot, label="test"), "")
check("...until it answers", _sites["n"], 3)

_sites_hard = {"n": 0}


def _sites_refused():
    _sites_hard["n"] += 1
    raise OSError('FATAL:  password authentication failed for user "hub"')


extensions._boot_wait_spent = 0.0
check("and a refusal is returned rather than retried",
      bool(extensions.boot_retry(_sites_refused, label="test")), True)
check("...on the first attempt", _sites_hard["n"], 1)

check("Sites Admin goes through it",
      "_boot_retry(init_db" in Path("modules/sites_admin/app.py").read_text(),
      True)
check("...and still carries a fallback for running outside the Hub",
      "standalone, outside the Hub" in
      Path("modules/sites_admin/app.py").read_text(), True)


# ------------------------------------------------------- the verdict is revised
section("A transient verdict is re-asked; a permanent one is not")

_answers = {"value": "SSL connection has been closed unexpectedly"}
_asked = {"n": 0}


def _rerun():
    _asked["n"] += 1
    return _answers["value"]


probe = extensions.BootProbe(_rerun, cooldown=0.0, label="test")
probe.record("SSL connection has been closed unexpectedly")
check("the standing verdict is what boot reported",
      "SSL connection" in probe.error(), True)
check("...and it is marked as one that could change", probe.transient(), True)

_answers["value"] = ""
check("once the database answers, the module comes back by itself",
      probe.error(), "")
check("and it stops re-asking a verdict that is now clear",
      (_asked["n"], probe.error()), (_asked["n"], ""))

_asked["n"] = 0
hard = extensions.BootProbe(_rerun, cooldown=0.0, label="test")
hard.record('relation "hub_users" does not exist')
hard.error()
hard.error()
check("a permanent verdict is never re-asked", _asked["n"], 0)
check("...and is returned unchanged, which is exactly today's behavior",
      hard.error(), 'relation "hub_users" does not exist')

# A database that is up and slow must not cost every visitor the full connect
# timeout in turn -- the per-visit pull hub/domain_purchase.py refuses.
_asked["n"] = 0
_answers["value"] = "connection refused"
cooled = extensions.BootProbe(_rerun, cooldown=60.0, label="test")
cooled.record("connection refused")
for _ in range(25):
    cooled.error()
check("a burst of requests produces no re-check inside the cooldown",
      _asked["n"], 0)


def _explodes():
    raise RuntimeError("the re-check itself fell over")


blew = extensions.BootProbe(_explodes, cooldown=0.0, label="test")
blew.record("connection refused")
check("a re-check that raises keeps the verdict rather than the exception",
      blew.error(), "connection refused")


# ------------------------------------------------- what the modules do with it
section("The two modules that gate everything can recover")

from modules.scans import app as scans                              # noqa: E402
from modules.image_picker import models as picker                   # noqa: E402

check("scans exposes the verdict as a question, not a constant",
      callable(getattr(scans, "db_error", None)), True)
check("...and holds a probe rather than a string alone",
      isinstance(getattr(scans, "_DB_PROBE", None), extensions.BootProbe), True)
check("its boot DDL is one re-runnable function, so a recovery brings the "
      "late columns with it",
      callable(getattr(scans, "_create_scan_tables", None)), True)
check("the before_request asks the probe rather than reading the global",
      "if db_error()" in Path("modules/scans/app.py").read_text(), True)
check("and a boot failure is logged, which it never was",
      "scans: database not ready at boot" in
      Path("modules/scans/app.py").read_text(), True)

check("the image picker asks too", callable(getattr(picker, "db_error", None)),
      True)
check("...from session(), which is what the client upload link goes through",
      "err = db_error()" in Path("modules/image_picker/models.py").read_text(),
      True)


# -------------------------------------------------- sign-in says what is wrong
section("Sign-in answers a database failure in words, not a 500")

from wsgi import application                                        # noqa: E402
from hub import users as hub_users                                  # noqa: E402

_real_by_email = hub_users.by_email
_real_authenticate = hub_users.authenticate


def _unreachable(*_a, **_k):
    raise OSError("(psycopg2.OperationalError) connection to server ... "
                  "SSL connection has been closed unexpectedly")


client = Client(application)
_ok = client.post("/login", data={"email": "nobody@smart1marketing.com",
                                  "password": "x", "next": "/"})
check("a wrong password is refused, plainly", _ok.status_code, 401)

hub_users.by_email = _unreachable
try:
    down = client.post("/login", data={"email": "todd@smart1marketing.com",
                                       "password": "x", "next": "/"})
    body = down.get_data(as_text=True)
finally:
    hub_users.by_email = _real_by_email

check("a database that cannot be reached is 503, not 500", down.status_code, 503)
check("...and the page says the account is not the problem",
      "Nothing is wrong with your account" in body, True)
check("...and points at the diagnostic that needs no login",
      "/login/health" in body, True)

# The failure that would quietly undo all of it: a broad `except` placed above
# the wrong-password arm reads every bad password as a database fault, which
# reads as fixed on the one path anybody tests.
_again = client.post("/login", data={"email": "nobody@smart1marketing.com",
                                     "password": "x", "next": "/"})
check("a wrong password is still a wrong password afterwards",
      _again.status_code, 401)

hub_users.authenticate = _unreachable
try:
    alias = client.post("/signin", data={"email": "todd@smart1marketing.com",
                                         "password": "x"})
finally:
    hub_users.authenticate = _real_authenticate
check("/signin, the alias old bookmarks still point at, answers the same",
      alias.status_code, 503)

health = client.get("/login/health")
check("and /login/health is readable with no session at all",
      health.status_code, 200)
check("...and names the sign-in failure somebody actually met",
      "login_db_error" in health.get_data(as_text=True), True)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
