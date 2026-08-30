#!/bin/bash
#
# Make a Claude Code on the web container able to run this repo's checks.
#
# CLAUDE.md prescribes a list of commands ("Verifying a change") and
# .github/workflows/checks.yml runs the same ones. Neither works in a fresh
# web container until two things are done, and both fail in ways that look
# like a defect in the Hub rather than a missing setup step:
#
#   1. `pip install -r requirements.txt` aborts on the DISTRO-packaged
#      blinker — "Cannot uninstall blinker 1.7.0, RECORD file not found" —
#      so nothing installs and every test file dies on `No module named
#      'flask'`, which reads as the app being broken.
#
#   2. Sites Admin refuses to start without a real Postgres and serves
#      wsgi.py's 503 fallback instead. `pagecheck.py --strict` then FAILS on
#      /sites/ and a whole module silently drops out of every check that
#      boots the app. checks.yml supplies a Postgres service for exactly this
#      reason; a web container has to start its own.
#
# Idempotent: safe on startup, resume, clear and compact.
set -euo pipefail

# Web sessions only. A developer's own machine has its own environment and
# this must never reach into it.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

say() { printf '  %s\n' "$*"; }

# --- 1. Python dependencies (essential — a failure here is fatal) ----------
say "Installing Python dependencies…"
# --ignore-installed blinker steps over the Debian-managed copy rather than
# trying to uninstall it. Without this the whole install aborts on that one
# package and NOTHING gets installed.
python3 -m pip install --quiet --ignore-installed blinker -r requirements.txt
say "Python dependencies ready."

# --- 2. Postgres (best effort — only /sites/ needs it) --------------------
# Never fatal: if this cannot come up the rest of the Hub still tests fine,
# and a hook that aborts the session over one module is worse than the gap.
PGUSER_NAME=smarthub
PGDB=smarthub_ci
if command -v pg_ctlcluster >/dev/null 2>&1; then
  if pg_lsclusters 2>/dev/null | grep -qE '^16\s+main\s+.*online'; then
    say "Postgres already running."
  else
    say "Starting Postgres…"
    pg_ctlcluster 16 main start 2>/dev/null || say "WARN: could not start Postgres (only /sites/ needs it)."
  fi

  # Wait for the socket rather than sleeping a guessed number of seconds.
  for _ in $(seq 1 20); do
    su postgres -c 'psql -tAc "select 1"' >/dev/null 2>&1 && break
    sleep 0.5
  done

  if su postgres -c 'psql -tAc "select 1"' >/dev/null 2>&1; then
    # Both creations are idempotent: re-running must not error.
    su postgres -c "psql -tAc \"select 1 from pg_roles where rolname='${PGUSER_NAME}'\"" \
      2>/dev/null | grep -q 1 \
      || su postgres -c "psql -q -c \"create user ${PGUSER_NAME} with password '${PGUSER_NAME}' superuser;\"" >/dev/null 2>&1 \
      || true
    su postgres -c "psql -tAc \"select 1 from pg_database where datname='${PGDB}'\"" \
      2>/dev/null | grep -q 1 \
      || su postgres -c "createdb -O ${PGUSER_NAME} ${PGDB}" >/dev/null 2>&1 \
      || true
    say "Postgres ready (${PGDB})."
  else
    say "WARN: Postgres did not come up; /sites/ will serve its 503 fallback."
  fi
else
  say "WARN: no Postgres in this image; /sites/ will serve its 503 fallback."
fi

# --- 3. Display Ad Builder (best effort — the one module that is not Python)
# Its tests need an npm install, which is why test_display_ads.py exists as a
# pure-Python substitute. Installing it here means `npm test` and `npx tsc
# --noEmit` are available too, the way checks.yml runs them.
if command -v npm >/dev/null 2>&1 && [ -f modules/ad_builder/package.json ]; then
  if [ -d modules/ad_builder/node_modules ]; then
    say "Ad builder dependencies already present."
  else
    say "Installing ad builder dependencies…"
    # `install` rather than `ci`: the container image is cached after this
    # hook, and install reuses what is already there.
    (cd modules/ad_builder && npm install --silent --no-audit --no-fund) \
      || say "WARN: npm install failed; test_display_ads.py still covers this module."
  fi
fi

# --- 4. Settings the checks expect ----------------------------------------
# The same throwaway values checks.yml uses. NOT credentials: SECRET_KEY and
# PANEL_PASSWORD are only needed so the app will boot and a test can sign in.
# No real key belongs in this file — the deployment sets its own on Render.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo "export DATABASE_URL=\"postgresql://${PGUSER_NAME}:${PGUSER_NAME}@127.0.0.1:5432/${PGDB}\""
    echo 'export SECRET_KEY="dev-not-a-real-secret"'
    echo 'export PANEL_PASSWORD="dev-not-a-real-password"'
  } >> "$CLAUDE_ENV_FILE"
  say "Environment written for this session."
fi

say "Ready: python3 test_commercial_wizard.py, tools/pagecheck.py --strict, etc."
