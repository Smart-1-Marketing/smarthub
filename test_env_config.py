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
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
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
#
# The file is a string rather than a file. This block used to write
# `hub/_env_drift_probe.py` into the repo and delete it in a `finally`, and both
# halves of that were wrong: a concurrent `tools/preflight.py` listed the probe
# and read it after the delete (`compile every module` failed once with
# FileNotFoundError on a file that had never been committed), and a run killed
# between the write and the finally left it in the working tree. So the check
# takes its sources, the way check_shadowed_model_query() and
# check_ai_callers() already did.
def drift(*src):
    return [f for f in integrity.check_provider_key_drift(
        sources=[("hub/_env_drift_probe.py", "".join(src))])
        if f["file"].endswith("_env_drift_probe.py")]


hit = drift("import os\n", 'KEY = os.environ.get("PEXELS_API_KEY")\n')
check("a file reading one spelling is reported", len(hit), 1)
check("the report names the spellings it did not read",
      "PEXELS_API" in (hit[0]["detail"] if hit else ""), True)

# 5. And prose is not a call site. Three modules explain the drift they no
# longer have by quoting os.environ["PEXELS_API_KEY"] in a docstring.
check("a docstring describing the fix is not a finding",
      drift("import os\n",
            '"""This used to read os.environ["PEXELS_API_KEY"] and was wrong."""\n',
            "from hub.config import settings\n",
            "KEY = settings.pexels_key\n"), [])

# 6. os.getenv is the same read.
check("os.getenv is read the same as os.environ",
      len(drift("import os\n",
                'KEY = os.getenv("SMART1SUITE_PRIVATE_TOKEN", "")\n')), 1)

# A fallback that lists the whole group resolves what config would, so it is
# not drift. Flagging it is how a check gets ignored.
check("reading every name in the group is not drift",
      drift("import os\n",
            'KEY = (os.environ.get("GHL_PRIVATE_TOKEN")\n',
            '       or os.environ.get("SMART1SUITE_PRIVATE_TOKEN") or "")\n'), [])

# Handing the check its sources means the clean bill of health below no longer
# proves the default walk reaches anything, so that is asserted rather than
# assumed -- it is the same failure the regression above was: a sweep that has
# stopped sweeping reports no findings, which reads identically to no defects.
_walked = {rel for rel, _ in integrity._sources()}
check("the default walk reaches hub/", "hub/config.py" in _walked, True)
check("...and modules/", any(r.startswith("modules/") for r in _walked), True)

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
#
# The tree is a throwaway one, for the reason given above the drift probe. It
# is laid out like this repo -- a hub/templates and a modules/*/templates --
# because the two globs the check walks are exactly what the layout has to
# match, and a fixture that flattened them would pass while the real walk found
# nothing.
TREE = Path(TMP) / "orphan_tree"


def tree(**files):
    """A throwaway repository holding exactly these files."""
    shutil.rmtree(TREE, ignore_errors=True)
    for rel, body in files.items():
        p = TREE / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return TREE


def orphans(**files):
    return sorted(f["file"] for f in
                  integrity.check_orphan_templates(root=tree(**files)))


PROBE = "hub/templates/_probe.html"
MOD_PROBE = "modules/widget/templates/_widget_probe.html"
DEAD = "<p>nothing renders this</p>\n"

found = integrity.check_orphan_templates(
    root=tree(**{PROBE: DEAD, MOD_PROBE: DEAD}))
check("a template no route can produce is reported",
      sorted(f["file"] for f in found), sorted([PROBE, MOD_PROBE]))
check("and the finding says why that is invisible otherwise",
      all("no request can produce it" in f["detail"] for f in found), True)
check("each is filed under the module it sits in",
      sorted(f["module"] for f in found), ["hub", "widget"])

# ...which is a real reading of this repo only if this repo is laid out that
# way, so that is asserted rather than assumed.
check("and the Hub really is laid out like the fixture",
      (ROOT / "hub" / "templates").is_dir()
      and bool(list((ROOT / "modules").glob("*/templates"))), True)

# A name chosen in a conditional and passed in a variable is still a render.
# modules/scans does exactly this to pick between widget.html and
# widget_audit.html, and a check reading only the literal arguments of a
# render_template() call reports its two most client-facing pages as dead --
# which is how somebody comes to delete a live page.
check("a computed template name is not an orphan",
      orphans(**{PROBE: DEAD, "hub/pick.py":
                 "def pick(kind):\n"
                 '    return "_probe.html" if kind else "other.html"\n'}), [])

# But a test naming a template is not a route rendering it -- the rule
# check_provider_key_drift() works to one step over. Left in, a test that
# merely mentions an orphan hides it for ever, which is not hypothetical: the
# sweep that restyled the dead site_detail.html added a test naming it.
check("a test naming it does not render it",
      orphans(**{PROBE: DEAD, "test_probe.py":
                 'def test_page(c):\n    assert "_probe.html"\n'}), [PROBE])

# Reached by {% include %} rather than by a route: a partial has no route of
# its own and must not be read as dead. modules/scans/_scan_mark.html is the
# real one -- three client-facing pages import it. The file that includes it,
# which nothing renders, still is an orphan.
check("a partial reached by include is not an orphan, and its host still is",
      orphans(**{PROBE: DEAD,
                 "hub/templates/host.html":
                 '{% include "_probe.html" %}\n'}), ["hub/templates/host.html"])

# ...but a file that documents its own include line is not rendered by saying
# so. The include pass had no "not its own name" guard -- the one the bare-.html
# pass beside it has always had -- so a template whose header comment reads
# `drop {% include "me.html" %} into the dashboard` registered itself as
# rendered and was invisible to this check. `_scorecard_stale_creative.html`
# did exactly that and sat there included by nothing, with the check reporting
# no orphans at all.
check("a template that quotes its own include line is still an orphan",
      orphans(**{PROBE: '{# drop {% include "_probe.html" %} into the '
                        'dashboard #}\n<p>still nothing renders this</p>\n'}),
      [PROBE])

shutil.rmtree(TREE, ignore_errors=True)

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

# Measured as a delta rather than by emptying the log first: the backend is
# the database, so truncating AUDIT_LOG_PATH empties a file nothing reads and
# every count below would then be of whatever the run had already written.
_before = len(audit.read(limit=200, module="display_ads"))
ad_builder_proxy._record("POST", "api/project/abc123/deliver", 200, "todd")
ad_builder_proxy._record("GET", "api/project/abc123", 200, "todd")
ad_builder_proxy._record("POST", "api/project/abc123/deliver", 500, "todd")
rows = audit.read(limit=200, module="display_ads")
rows = rows[:max(0, len(rows) - _before)]
check("a delivered pack is recorded once", len(rows), 1)
check("under an action somebody can read", rows[0]["type"], "ads_delivered")
check("with the project it was", rows[0].get("ref"), "abc123")
check("and who did it", rows[0].get("actor"), "todd")

# A route added in TypeScript later cannot be silent: unnamed writes are still
# recorded, under their own path.
_before = len(audit.read(limit=200, module="display_ads"))
ad_builder_proxy._record("POST", "api/something/new", 201, "todd")
rows = audit.read(limit=200, module="display_ads")
rows = rows[:max(0, len(rows) - _before)]
check("an unnamed write is still attributable",
      (len(rows), rows[0]["type"] if rows else ""), (1, "ads_api_something_new"))


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
