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

# The two processes named the same secret differently, and nothing bridged
# them: the Hub reads ADBUILDER_ADMIN_TOKEN (hub/ad_builder_proxy.py) and
# render.yaml declares only that name, while the renderer reads ADMIN_TOKEN
# and nothing else (modules/ad_builder/src/auth.ts). So setting the documented
# variable satisfied the Hub -- the "not configured" notice disappeared -- and
# the renderer went on refusing every internal route. The tool loads and no
# button works, which is the hardest kind of broken to diagnose.
#
# Bridged here rather than renamed on either side. The renderer ships its own
# render.yaml and its README documents ADMIN_TOKEN, so it keeps that name for
# when it moves to its own service; the Hub keeps the prefixed name that says
# which tool the secret belongs to. An explicitly set ADMIN_TOKEN still wins.
export ADMIN_TOKEN="${ADMIN_TOKEN:-${ADBUILDER_ADMIN_TOKEN:-}}"

# Cloudinary, same story. The Hub is configured with one CLOUDINARY_URL and
# hub/storage.py hands it straight to the SDK; the renderer reads three
# separate variables and nothing else, so with only the Hub's variable set it
# silently has no Cloudinary at all. That matters more than it looks: finished
# ads are filed into the client gallery by recording the public_id the renderer
# already created, so no upload means no filing, and the failure appears at the
# end of a long render rather than at the start.
#
# Split here rather than adding a second set of variables to Render: two places
# holding the same credential is how they drift apart.
if [ -z "${CLOUDINARY_CLOUD_NAME:-}" ] && [ -n "${CLOUDINARY_URL:-}" ]; then
  _cld="${CLOUDINARY_URL}"
  # Render stores quotes literally, so a value pasted with them arrives with
  # them attached -- the same trap that broke SCANS_CALLBACK_TOKEN.
  _cld="${_cld%\"}"; _cld="${_cld#\"}"
  _cld="${_cld%\'}"; _cld="${_cld#\'}"
  case "$_cld" in
    "cloudinary://API_KEY:API_SECRET@CLOUD_NAME")
      # The documented placeholder. Every "is it configured?" check says yes
      # and every upload fails, so say so rather than passing it through.
      echo "[adbuilder] CLOUDINARY_URL is still the placeholder value, so the renderer has no Cloudinary. Set the real one in Render."
      ;;
    cloudinary://*:*@*)
      _rest="${_cld#cloudinary://}"
      export CLOUDINARY_API_KEY="${_rest%%:*}"
      _rest="${_rest#*:}"
      export CLOUDINARY_API_SECRET="${_rest%%@*}"
      export CLOUDINARY_CLOUD_NAME="${_rest##*@}"
      echo "[adbuilder] Cloudinary configured for cloud ${CLOUDINARY_CLOUD_NAME}"
      ;;
    *)
      echo "[adbuilder] CLOUDINARY_URL is set but is not in cloudinary://key:secret@cloud form, so the renderer has no Cloudinary."
      ;;
  esac
fi

# Links the renderer puts in notification emails and webhooks. Its default is
# http://localhost:8791, which is correct standalone and useless in a mail --
# nobody can open the loopback address of a Render container. Under the Hub the
# tool lives behind the proxy mount, so build the public form from the URL the
# Hub already knows.
if [ -z "${PUBLIC_URL:-}" ] && [ -n "${PUBLIC_BASE_URL:-}" ]; then
  export PUBLIC_URL="${PUBLIC_BASE_URL%/}/tools/display-ads"
fi

# Renders, project state and the brand cache. The renderer's own default is a
# directory inside the image, which Render replaces on every deploy, so a
# finished ad package would vanish with the next release.
#
# This was set in render.yaml and never arrived. render.yaml is a Blueprint
# file, and this service takes its environment from the dashboard instead, so
# a value declared only in the blueprint is documentation rather than
# configuration -- the boot log said `output dir: /app/modules/ad_builder/out`
# for exactly that reason. Defaulting it here means the durable path does not
# depend on anyone remembering a dashboard field.
#
# Same test hub/extensions.py uses to place the database: the mounted disk when
# there is one, and the repo-local fallback when there is not, so a laptop run
# still works.
if [ -z "${OUTPUT_DIR:-}" ] && [ -d /var/data ]; then
  export OUTPUT_DIR=/var/data/adbuilder-out
fi
if [ -n "${OUTPUT_DIR:-}" ]; then
  mkdir -p "$OUTPUT_DIR" || echo "[adbuilder] could not create OUTPUT_DIR ${OUTPUT_DIR}"
  echo "[adbuilder] output dir: ${OUTPUT_DIR}"
fi

# SmartForecast keeps its append-only event history on the persistent disk.
# The JSON backup is also mirrored into the managed database, so a replacement
# disk can rebuild this SQLite database during application boot.
if [ -z "${SMARTFORECAST_DB_PATH:-}" ] && [ -d /var/data ]; then
  export SMARTFORECAST_DB_PATH=/var/data/smartforecast/smartforecast.sqlite3
fi
if [ -n "${SMARTFORECAST_DB_PATH:-}" ]; then
  mkdir -p "$(dirname "$SMARTFORECAST_DB_PATH")" || echo "[smartforecast] could not create database directory"
  echo "[smartforecast] database: ${SMARTFORECAST_DB_PATH}"
fi

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
