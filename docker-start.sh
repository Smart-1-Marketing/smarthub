#!/usr/bin/env bash
# Start the Hub, the Display Ad Builder, hf-render-service and the
# Marketing Efficiency Audit in one container.
#
# The Hub is the service Render health-checks and the only thing bound to the
# public port. The ad builder listens on loopback only, and Flask proxies
# /tools/display-ads/* to it — so it is reachable through the Hub login and not
# otherwise reachable at all. The Marketing Efficiency Audit listens on
# loopback the same way, proxied at /tools/marketing-audit/* -- with the whole
# prefix public, since the visitor is an accounting or bookkeeping partner
# with no Hub account (see hub/marketing_audit_proxy.py). hf-render-service
# listens on loopback too, and is not proxied at all: hub/hyperframes.py talks
# to it server-to-server, over HF_RENDER_SERVICE_URL, exactly the way it would
# talk to a hosted API — there is no browser-facing route on it to proxy.
#
# Why a script rather than four CMDs: a container has one PID 1, and if that
# is gunicorn then a crashed renderer leaves the Hub up and quietly broken —
# every ad request 502s and every paint animation reports "not configured" and
# nothing says why. Here every renderer is supervised: if one dies it is
# restarted, and if it cannot stay up the log says so on every attempt rather
# than once at boot.
set -uo pipefail

ADBUILDER_PORT="${ADBUILDER_PORT:-8791}"
AD_DIR=/app/modules/ad_builder

HF_RENDER_PORT="${HF_RENDER_PORT:-8792}"
HF_RENDER_DIR=/app/modules/hf_render_service

MARKETING_AUDIT_PORT="${MARKETING_AUDIT_PORT:-8793}"
MARKETING_AUDIT_DIR=/app/modules/marketing_audit

# The Hub's own bind port, captured before anything below reassigns $PORT for
# a child process's environment. This is how the audit tool reaches the Hub's
# /api/leads/capture over loopback -- server-to-server, the same container,
# never through the public internet.
HUB_PORT="${PORT:-8000}"

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

# hf-render-service's own finished files, on the same test hub/extensions.py
# and the ad builder above already use: the mounted disk when there is one,
# a repo-local fallback otherwise.
if [ -z "${HF_OUTPUT_DIR:-}" ] && [ -d /var/data ]; then
  export HF_OUTPUT_DIR=/var/data/hf-render-out
fi
if [ -n "${HF_OUTPUT_DIR:-}" ]; then
  mkdir -p "$HF_OUTPUT_DIR" || echo "[hf-render] could not create HF_OUTPUT_DIR ${HF_OUTPUT_DIR}"
  echo "[hf-render] output dir: ${HF_OUTPUT_DIR}"
fi

# "Set HF_RENDER_SERVICE_URL and they appear on their own" is
# hub/hyperframes.why_unavailable()'s own promise, and this is what makes it
# true with nothing to configure on an ordinary deploy: pointed at the
# process this script is about to start, on loopback, the moment the build
# for it actually exists. An explicitly-set value always wins — a deployment
# that has genuinely split this out to its own Render service (see
# modules/hf_render_service/render.yaml) sets HF_RENDER_SERVICE_URL itself,
# and must not have it silently overwritten with the in-container address.
#
# Deliberately conditioned on the build existing, not merely on the port:
# setting this unconditionally would turn "the render service failed to
# build" into "paint animations time out against a socket nobody is
# listening on" instead of the honest, tested "not configured" state
# hub/hyperframes.is_configured() already answers gracefully.
if [ -z "${HF_RENDER_SERVICE_URL:-}" ] && [ -f "$HF_RENDER_DIR/dist/src/server.js" ]; then
  export HF_RENDER_SERVICE_URL="http://127.0.0.1:${HF_RENDER_PORT}"
  echo "[hf-render] HF_RENDER_SERVICE_URL defaulted to ${HF_RENDER_SERVICE_URL}"
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

start_hf_render() {
  # Loopback only, same reasoning as the ad builder above — and here there is
  # not even a proxy in front of it to enforce a login, so a leak to 0.0.0.0
  # would be an unauthenticated video renderer on the open internet.
  cd "$HF_RENDER_DIR" || return 1
  PORT="$HF_RENDER_PORT" HOST=127.0.0.1 node dist/src/server.js
}

if [ -f "$HF_RENDER_DIR/dist/src/server.js" ]; then
  (
    attempt=0
    while true; do
      attempt=$((attempt + 1))
      echo "[hf-render] starting on 127.0.0.1:${HF_RENDER_PORT} (attempt ${attempt})"
      start_hf_render
      code=$?
      echo "[hf-render] exited with ${code} — paint animations and Vox explainers are unavailable until it restarts"
      sleep 5
    done
  ) &
else
  echo "[hf-render] dist/src/server.js is missing — the build did not run. The rest of the Hub will start normally."
fi

# Where the audit tool posts captured leads: the Hub's own /api/leads/capture,
# over loopback, on the port this script captured as HUB_PORT before PORT was
# reassigned for any child process below. An explicitly-set HUB_BASE_URL still
# wins, for a deployment that has split this out to its own Render service.
if [ -z "${HUB_BASE_URL:-}" ]; then
  export HUB_BASE_URL="http://127.0.0.1:${HUB_PORT}"
fi
# The shared secret hub/leads.py already reads from every standalone landing
# app's egress address (LEADS_SOURCE_TOKEN) -- this process posts from its own
# server too, so without it every partner's lead would share one address and
# trip the Hub's per-visitor rate limit within the hour.
if [ -z "${HUB_LEADS_SOURCE_TOKEN:-}" ] && [ -n "${LEADS_SOURCE_TOKEN:-}" ]; then
  export HUB_LEADS_SOURCE_TOKEN="${LEADS_SOURCE_TOKEN}"
fi
# Who may iframe the audit. Unset, the tool's own default is "*" -- fine while
# testing, wrong once this is live on the marketing site, because it would
# also let anyone else's page frame the lead form. Named here rather than
# left to be discovered the way EMBED_ALLOWED_ORIGINS was for the standalone
# build.
export EMBED_ALLOWED_ORIGINS="${EMBED_ALLOWED_ORIGINS:-https://smart1marketing.com https://www.smart1marketing.com}"

start_marketing_audit() {
  # Loopback only, same reasoning as the two renderers above.
  cd "$MARKETING_AUDIT_DIR" || return 1
  # PUBLIC_BASE_URL is set ONLY in this child process's own environment, never
  # exported into the script's global environment: the Hub's own Python code
  # reads that exact name (hub/config.py's public_base_origin(), every OAuth
  # redirect it builds) and appending this tool's own prefix onto it here
  # would corrupt every one of those for the Hub itself -- the audit tool
  # only ever uses its own PUBLIC_BASE_URL as a fallback link for a
  # locally-stored PDF (Cloudinary is the normal path and needs none of
  # this; see cloudinary.js and the README's "Where PDFs are stored").
  PORT="$MARKETING_AUDIT_PORT" HOST=127.0.0.1 \
    PUBLIC_BASE_URL="${PUBLIC_BASE_URL:+${PUBLIC_BASE_URL%/}/tools/marketing-audit}" \
    node server.js
}

if [ -f "$MARKETING_AUDIT_DIR/server.js" ]; then
  (
    attempt=0
    while true; do
      attempt=$((attempt + 1))
      echo "[marketing-audit] starting on 127.0.0.1:${MARKETING_AUDIT_PORT} (attempt ${attempt})"
      start_marketing_audit
      code=$?
      echo "[marketing-audit] exited with ${code} — the Marketing Efficiency Audit is unavailable until it restarts"
      sleep 5
    done
  ) &
else
  echo "[marketing-audit] server.js is missing — npm install did not run. The rest of the Hub will start normally."
fi

# --threads only takes effect under the gthread worker class -- gunicorn's
# default is "sync", which silently ignores it. Without this line the Hub was
# never running at "2 workers x 8 threads": it was running at 2, full stop,
# because a sync worker handles exactly one request at a time on its own
# thread. Every request behind those two -- another tab, another rep, a
# background fetch from a page already open -- queued for the free worker,
# and with this Hub's own scheduler and modules making plenty of genuinely
# slow, synchronous outbound calls (Insites, Knack, Google, OpenAI,
# Cloudinary, ghostscript/qpdf subprocesses), one such request tied up a
# whole half of the Hub's total capacity for as long as it ran. A third
# concurrent request had nowhere to go but the socket backlog, where it sat
# until either the caller gave up, Render's own proxy gave up (502), or
# gunicorn's --timeout gave up and killed the worker mid-request (also a 502,
# and it takes whatever else that worker was doing down with it). Threads
# share memory rather than duplicating a whole Python process's worth of
# imports the way another worker would, so this is the cheap fix: real
# concurrency for I/O-bound waits without doubling RSS.
exec gunicorn wsgi:application \
  --bind "0.0.0.0:${PORT:-8000}" \
  --workers 2 --worker-class gthread --threads 8 --timeout 180
