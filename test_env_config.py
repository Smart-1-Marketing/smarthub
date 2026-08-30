"""hub/config.py — one setting, every name it answers to.

    python3 test_env_config.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one.

## Why this file exists

This deployment's environment is assembled from linked Render env groups plus
service-level variables, and the same setting is spelled differently in
different groups: PEXELS_API here and PEXELS_API_KEY there, GHL_PRIVATE_TOKEN
beside SMART1SUITE_PRIVATE_TOKEN, SECRET_KEY beside FLASK_SECRET_KEY beside
SESSION_SECRET. `hub/config.py` accepts all of them, which is the only reason
any of it works — and it is also what makes every failure here silent. A module
reading one spelling reports a key that is plainly set as missing, degrades to
mock data or a template, and every screen looks healthy.

So each check below is a way that goes wrong without erroring:

  1.  every spelling resolves       — the whole point of the table
  2.  precedence is the table's     — not whichever module read first
  3.  one table, three readers      — config, /api/integrity and env_report
                                      cannot hold different lists
  4.  the check reads the table     — the regression this file was written
                                      after: the check used to regex `_first(…)`
                                      out of config's source, so replacing
                                      those calls with a table left it finding
                                      no groups, reporting nothing, and reading
                                      as a clean bill of health
  5.  prose is not a call site      — the fix for a drift is described in a
                                      docstring in three modules, and a check
                                      that flags the description teaches people
                                      to ignore it
  6.  os.getenv counts              — the same read, spelled differently
  7.  Cloudinary's two forms        — a three-part credential is a configured
                                      Cloudinary, not local-disk-only
  8.  no value ever leaves          — env_report is rendered into a page
  9.  a conflict is named           — two names, two values, one silently wins
  10. PUBLIC_BASE_URL is an origin  — a path in it reaches every built URL
  11. a module logs under its name  — including the one that is not Python
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1envcfg_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(TMP, "db.sqlite3"))

from hub import config as cfg                            # noqa: E402
from hub import integrity                                # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


def with_env(**names):
    """A fresh Settings with exactly these variables set.

    Every name in the table is cleared first, so a check cannot pass because
    the machine running it happens to carry a real key.
    """
    saved = {}
    for group in cfg.ALIASES.values():
        for n in group:
            saved[n] = os.environ.pop(n, None)
    for n in ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
              "CLOUDINARY_API_SECRET", "PUBLIC_BASE_URL"):
        saved[n] = os.environ.pop(n, None)
    try:
        for n, v in names.items():
            os.environ[n] = v
        return cfg.Settings(), saved
    finally:
        pass


def restore(saved):
    for n, v in saved.items():
        if v is None:
            os.environ.pop(n, None)
        else:
            os.environ[n] = v


# ------------------------------------------- 1-2. every spelling, in order
section("A setting resolves under every name it answers to")

for setting, names in sorted(cfg.ALIASES.items()):
    ok = True
    for name in names:
        s, saved = with_env(**{name: f"value-via-{name}"})
        got = getattr(s, setting, None)
        restore(saved)
        if got != f"value-via-{name}":
            ok = False
            print(f"          {setting} did not resolve from {name}")
    check(f"{setting}: all of {', '.join(names)}", ok, True)

# Precedence belongs to the table, not to whichever caller reads first.
names = cfg.ALIASES["ghl_token"]
s, saved = with_env(**{n: f"v-{n}" for n in names})
check("the first name in the table wins", s.ghl_token, f"v-{names[0]}")
restore(saved)

# The one that had a genuine bug behind it: config knew SECRET_KEY and
# FLASK_SECRET_KEY, hub/auth.py knew SECRET_KEY and SESSION_SECRET. A Hub
# carrying only FLASK_SECRET_KEY signed its cookies with an ephemeral secret
# while the status page called the secret configured, so every session died at
# every restart and nothing said why.
s, saved = with_env(FLASK_SECRET_KEY="only-this-one")
check("FLASK_SECRET_KEY alone is a configured secret", s.secret_key, "only-this-one")
restore(saved)


# ------------------------------------------- 3-4. one table, and it is read
section("The table is the only list, and the check reads it")

check("the alias table is not empty", bool(cfg.ALIASES), True)
check("every entry has more than one spelling",
      sorted(k for k, v in cfg.ALIASES.items() if len(v) < 2), [])
check("env_report covers every setting in the table",
      sorted(r["setting"] for r in cfg.Settings().env_report()),
      sorted(cfg.ALIASES))

# The regression. The check used to read `_first("A", "B")` calls out of
# config's source; the day those became a table it found no groups and reported
# nothing at all, which looks exactly like a Hub with no drift in it. Feed it a
# file that plainly drifts and require it to say so.
_probe = ROOT / "hub" / "_env_drift_probe.py"
_probe.write_text(
    "import os\n"
    'KEY = os.environ.get("PEXELS_API_KEY")\n', encoding="utf-8")
try:
    found = integrity.check_provider_key_drift()
    hit = [f for f in found if f["file"].endswith("_env_drift_probe.py")]
    check("a file reading one spelling is reported", len(hit), 1)
    check("the report names the spellings it did not read",
          "PEXELS_API" in (hit[0]["detail"] if hit else ""), True)

    # 5. And prose is not a call site. Three modules explain the drift they no
    # longer have by quoting os.environ["PEXELS_API_KEY"] in a docstring.
    _probe.write_text(
        "import os\n"
        '"""This used to read os.environ["PEXELS_API_KEY"] and was wrong."""\n'
        "from hub.config import settings\n"
        "KEY = settings.pexels_key\n", encoding="utf-8")
    found = integrity.check_provider_key_drift()
    check("a docstring describing the fix is not a finding",
          [f for f in found if f["file"].endswith("_env_drift_probe.py")], [])

    # 6. os.getenv is the same read.
    _probe.write_text(
        "import os\n"
        'KEY = os.getenv("SMART1SUITE_PRIVATE_TOKEN", "")\n', encoding="utf-8")
    found = integrity.check_provider_key_drift()
    check("os.getenv is read the same as os.environ",
          len([f for f in found if f["file"].endswith("_env_drift_probe.py")]), 1)

    # A fallback that lists the whole group resolves what config would, so it
    # is not drift. Flagging it is how a check gets ignored.
    _probe.write_text(
        "import os\n"
        'KEY = (os.environ.get("GHL_PRIVATE_TOKEN")\n'
        '       or os.environ.get("SMART1SUITE_PRIVATE_TOKEN") or "")\n',
        encoding="utf-8")
    found = integrity.check_provider_key_drift()
    check("reading every name in the group is not drift",
          [f for f in found if f["file"].endswith("_env_drift_probe.py")], [])
finally:
    _probe.unlink(missing_ok=True)

check("and the Hub itself is clean", integrity.check_provider_key_drift(), [])


# ------------------------------------------ 6b. A template nothing renders
section("A page that exists is not a page anybody can reach")

# The same shape as the drift check above and for the same reason: a check
# that can be satisfied by an edit somewhere else is worse than no check, so
# it is handed a template that is plainly unreachable and required to say so.
#
# What it cost before it existed: modules/sites_admin/templates/site_detail.html
# was rendered by nothing and was restyled anyway in the sweep that made Sites
# read like the rest of the Hub, and google_finder's reports.html was
# byte-identical to gtm_logs.html apart from its <title>. Reading the
# directory, all three looked like features.
_orphan = ROOT / "hub" / "templates" / "_integrity_orphan_probe.html"
_orphan.write_text("<p>nothing renders this</p>\n", encoding="utf-8")
try:
    found = integrity.check_orphan_templates()
    hit = [f for f in found if f["file"].endswith("_integrity_orphan_probe.html")]
    check("a template no route can produce is reported", len(hit), 1)
    check("and the finding says why that is invisible otherwise",
          "no request can produce it" in (hit[0]["detail"] if hit else ""), True)

    # A name chosen in a conditional and passed in a variable is still a
    # render. modules/scans does exactly this to pick between widget.html and
    # widget_audit.html, and a check reading only the literal arguments of a
    # render_template() call reports its two most client-facing pages as dead
    # -- which is how somebody comes to delete a live page.
    _caller = ROOT / "hub" / "_integrity_orphan_caller.py"
    _caller.write_text(
        "def pick(kind):\n"
        '    return "_integrity_orphan_probe.html" if kind else "other.html"\n',
        encoding="utf-8")
    try:
        found = integrity.check_orphan_templates()
        check("a computed template name is not an orphan",
              [f for f in found
               if f["file"].endswith("_integrity_orphan_probe.html")], [])
    finally:
        _caller.unlink(missing_ok=True)

    # Reached by {% include %} rather than by a route: a partial has no route
    # of its own and must not be read as dead. modules/scans/_scan_mark.html
    # is the real one -- three client-facing pages import it.
    _includer = ROOT / "hub" / "templates" / "_integrity_orphan_host.html"
    _includer.write_text(
        '{% include "_integrity_orphan_probe.html" %}\n', encoding="utf-8")
    try:
        found = integrity.check_orphan_templates()
        check("a partial reached by include is not an orphan",
              [f for f in found
               if f["file"].endswith("_integrity_orphan_probe.html")], [])
        # ...and the host itself, which nothing renders, still is.
        check("while the file that includes it, which nothing renders, is",
              len([f for f in found
                   if f["file"].endswith("_integrity_orphan_host.html")]), 1)
    finally:
        _includer.unlink(missing_ok=True)

    # ...but a file that documents its own include line is not rendered by
    # saying so. The include pass had no "not its own name" guard -- the one
    # the bare-.html pass beside it has always had -- so a template whose
    # header comment reads `drop {% include "me.html" %} into the dashboard`
    # registered itself as rendered and was invisible to this check.
    # `_scorecard_stale_creative.html` did exactly that and sat there included
    # by nothing, with the check reporting no orphans at all.
    _orphan.write_text(
        '{# drop {% include "_integrity_orphan_probe.html" %} '
        'into the dashboard #}\n<p>still nothing renders this</p>\n',
        encoding="utf-8")
    found = integrity.check_orphan_templates()
    check("a template that quotes its own include line is still an orphan",
          len([f for f in found
               if f["file"].endswith("_integrity_orphan_probe.html")]), 1)
finally:
    _orphan.unlink(missing_ok=True)

# It started empty, which is the only way it was worth adding: the three it
# found were deleted in the same change.
check("and no template in the Hub is unreachable",
      integrity.check_orphan_templates(), [])


# ------------------------------------------------- 7. Cloudinary's two forms
section("Cloudinary is configured either way it is published")

s, saved = with_env(CLOUDINARY_CLOUD_NAME="smart1snap",
                    CLOUDINARY_API_KEY="12345", CLOUDINARY_API_SECRET="s3cr3t")
check("three parts are a configured Cloudinary", s.cloudinary_ready, True)
check("and the cloud name still parses out", s.cloudinary_cloud_name, "smart1snap")
restore(saved)

s, saved = with_env(CLOUDINARY_URL="cloudinary://k:v@named",
                    CLOUDINARY_CLOUD_NAME="ignored")
check("an explicit URL wins over the parts", s.cloudinary_cloud_name, "named")
restore(saved)

s, saved = with_env(CLOUDINARY_CLOUD_NAME="smart1snap", CLOUDINARY_API_KEY="12345")
check("two of the three parts is not configured", s.cloudinary_ready, False)
restore(saved)

# hub/storage.py and nine modules call cloudinary.config() with no arguments,
# which reads CLOUDINARY_URL out of the environment. A composed credential that
# only hub.config can see would leave every one of them writing to the local
# disk that is wiped on each redeploy, without erroring.
saved = {n: os.environ.pop(n, None) for n in
         ("CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
          "CLOUDINARY_API_SECRET")}
try:
    os.environ.update(CLOUDINARY_CLOUD_NAME="smart1snap",
                      CLOUDINARY_API_KEY="12345", CLOUDINARY_API_SECRET="s3cr3t")
    cfg.export_cloudinary_url(cfg.Settings())
    check("the composed URL reaches the Cloudinary SDK's own reader",
          os.environ.get("CLOUDINARY_URL"), "cloudinary://12345:s3cr3t@smart1snap")

    os.environ["CLOUDINARY_URL"] = "cloudinary://explicit:v@chosen"
    cfg.export_cloudinary_url(cfg.Settings())
    check("and never overwrites one somebody set",
          os.environ["CLOUDINARY_URL"], "cloudinary://explicit:v@chosen")
finally:
    restore(saved)


# --------------------------------------------- 8-9. what env_report may say
section("env_report names the variable and never the value")

s, saved = with_env(PEXELS_API="first-value", PEXELS_API_KEY="second-value")
row = [r for r in s.env_report() if r["setting"] == "pexels_key"][0]
check("it names which spelling answered", row["resolved"], "PEXELS_API")
check("and which were set and ignored", row["ignored"], ["PEXELS_API_KEY"])
check("two names, two values, is a conflict", row["conflict"], True)
blob = repr(s.env_report())
check("no value appears anywhere in the report",
      ("first-value" in blob) or ("second-value" in blob), False)
check("the conflict reaches the warnings a page reads",
      any("PEXELS_API_KEY" in w["detail"] for w in s.placeholder_warnings()), True)
restore(saved)

# The same value under two names is somebody being thorough, not a fault.
s, saved = with_env(PEXELS_API="same", PEXELS_API_KEY="same")
row = [r for r in s.env_report() if r["setting"] == "pexels_key"][0]
check("the same value twice is not a conflict", row["conflict"], False)
restore(saved)

s, saved = with_env()
row = [r for r in s.env_report() if r["setting"] == "pexels_key"][0]
check("unset says so rather than naming a variable", (row["set"], row["resolved"]),
      (False, ""))
restore(saved)


# ------------------------------------------- 10. PUBLIC_BASE_URL is an origin
section("PUBLIC_BASE_URL is the origin and nothing else")

s, saved = with_env(PUBLIC_BASE_URL="https://smart1-hub.onrender.com/tools/ads/oauth/callback")
check("a path in it is reported",
      any(w["name"] == "PUBLIC_BASE_URL" for w in s.placeholder_warnings()), True)
restore(saved)

s, saved = with_env(PUBLIC_BASE_URL="https://smart1-hub.onrender.com")
check("the correct value is not",
      any(w["name"] == "PUBLIC_BASE_URL" for w in s.placeholder_warnings()), False)
restore(saved)

s, saved = with_env(PUBLIC_BASE_URL="https://smart1-hub.onrender.com/")
check("a bare trailing slash is not a path",
      any(w["name"] == "PUBLIC_BASE_URL" for w in s.placeholder_warnings()), False)
restore(saved)


# ---------------------------------- 11. every module's work is attributable
section("A module logs under its own name, including the one that is not Python")

from hub import audit                                    # noqa: E402

check("no module is silent", integrity.check_silent_modules(), [])

# The declaration is not the answer on its own: the name has to be logged
# somewhere. Point it at a name nothing writes and the check must say so.
_real = dict(audit.LOG_NAMES)
try:
    audit.LOG_NAMES.clear()
    audit.LOG_NAMES["ad_builder"] = "a_name_nothing_writes"
    check("a declared name nothing logs under is still silent",
          [f["module"] for f in integrity.check_silent_modules()], ["ad_builder"])
finally:
    audit.LOG_NAMES.clear()
    audit.LOG_NAMES.update(_real)

# And the renderer's writes reach the log through the proxy, which is the only
# point all of them pass through. A GET is not an action; a failure is not one
# either.
from hub import ad_builder_proxy                         # noqa: E402

open(os.environ["AUDIT_LOG_PATH"], "w").close()
ad_builder_proxy._record("POST", "api/project/abc123/deliver", 200, "todd")
ad_builder_proxy._record("GET", "api/project/abc123", 200, "todd")
ad_builder_proxy._record("POST", "api/project/abc123/deliver", 500, "todd")
rows = audit.read(limit=50, module="display_ads")
check("a delivered pack is recorded once", len(rows), 1)
check("under an action somebody can read", rows[0]["type"], "ads_delivered")
check("with the project it was", rows[0].get("ref"), "abc123")
check("and who did it", rows[0].get("actor"), "todd")

# A route added in TypeScript later cannot be silent: unnamed writes are still
# recorded, under their own path.
open(os.environ["AUDIT_LOG_PATH"], "w").close()
ad_builder_proxy._record("POST", "api/something/new", 201, "todd")
rows = audit.read(limit=50, module="display_ads")
check("an unnamed write is still attributable",
      (len(rows), rows[0]["type"] if rows else ""), (1, "ads_api_something_new"))


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
