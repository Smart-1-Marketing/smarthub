#!/usr/bin/env bash
# Start the Hub and the Display Ad Builder in one container.
#
# The Hub is the service Render health-checks and the only thing bound to the
# public port. The ad builder listens on loopback only, and Flask proxies
# /tools/display-ads/* to it — so it is reachable through the Hub login and not
# otherwise reachable at all.
#
# Why a script rather than two CMDs: a container has one PID 1, and if that is
# gunicorn then a crashed renderer leaves the Hub up and quietly broken —
# every ad request 502s and nothing says why. Here the renderer is supervised:
# if it dies it is restarted, and if it cannot stay up the log says so on every
# attempt rather than once at boot.
set -uo pipefail

ADBUILDER_PORT="${ADBUILDER_PORT:-8791}"
AD_DIR=/app/modules/ad_builder

start_adbuilder() {
  # Loopback only. Binding this to 0.0.0.0 would publish an unauthenticated
  # renderer on the same host as the Hub.
  cd "$AD_DIR" || return 1
  PORT="$ADBUILDER_PORT" HOST=127.0.0.1 node dist/src/server.js
}

if [ -f "$AD_DIR/dist/src/server.js" ]; then
  (
    attempt=0
    while true; do
      attempt=$((attempt + 1))
      echo "[adbuilder] starting on 127.0.0.1:${ADBUILDER_PORT} (attempt ${attempt})"
      start_adbuilder
      code=$?
      echo "[adbuilder] exited with ${code} — the Display Ad Builder is unavailable until it restarts"
      # Back off a little so a crash loop does not spin the CPU and drown the
      # log. Five seconds is short enough that a transient failure self-heals
      # before anyone notices.
      sleep 5
    done
  ) &
else
  # Not fatal. Every other tool in the Hub still works, and the proxy returns a
  # plain explanation rather than a 502 nobody can interpret.
  echo "[adbuilder] dist/src/server.js is missing — the build did not run. The rest of the Hub will start normally."
fi

exec gunicorn wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 --threads 8 --timeout 180
