FROM python:3.14-slim

# Ghostscript + qPDF for the PDF optimizer.
#
# Chromium used to be installed here so one landing page could render its PDF
# from an HTML template. That was roughly 300 MB on the image and slower builds
# for every tool, to serve a single module. Tourism now builds its PDF with
# reportlab like the other six landing pages, so the browser is gone.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ghostscript qpdf curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Node, for the Display Ad Builder.
#
# The ad builder is ~10,000 lines of TypeScript with a native image pipeline:
# sharp rasterises SVG and steps a quality ladder down until each ad fits the
# platform's file-weight limit (Amazon allows 40 KB for some placements). That
# is not a thing to re-derive in Pillow — a port would change the creative
# clients already receive — so the runtime comes to the image instead.
#
# THE COST, stated plainly because Chromium taught us to: Node plus sharp adds
# roughly 150-200 MB and a second build step that every deploy pays for, and
# the container now runs two processes rather than one. Revisit this if builds
# start timing out or memory gets tight on the Render plan — the ad builder
# ships its own render.yaml and can be split back out into its own service
# without changing its code, only the proxy target in hub/ad_builder_proxy.py.
# ---------------------------------------------------------------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# ffmpeg + Chromium, for hf-render-service (Paint Animation, Vox Explainer).
#
# hub/hyperframes.py's own docstring is explicit that Puppeteer, Chromium and
# FFmpeg do not belong in this image -- they were meant to run as their own
# Render service. They are here anyway, as a second background process in
# this same container (see docker-start.sh), because standing up and paying
# for a whole second Render service is a bigger ask than the CPU headroom
# this deployment now has. If that stops being true -- renders make the Hub
# itself feel slow, or builds start timing out -- hf_render_service ships its
# own render.yaml and README describing exactly that split; nothing about the
# module's code has to change, only where HF_RENDER_SERVICE_URL points.
#
# THE COST, stated the way the Node block above states its own: this is
# roughly 400-500 MB on the image (Chromium's own package plus its shared
# libraries) and it is the heaviest single addition here. CI installs the
# shared libraries directly rather than this package -- Ubuntu's own
# `chromium` is a Snap wrapper, which is pointless weight for a runner that
# never launches it -- and relies on Puppeteer's own bundled download
# instead; its real end-to-end render is what proves that path works there.
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium ffmpeg fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Puppeteer's own bundled-Chrome download-and-cache path (see
# PUPPETEER_CACHE_DIR below) has landed a Chrome revision at launch time that
# does not match what capture.ts's puppeteer.launch() went looking for --
# "Could not find Chrome (ver. ...)" -- more than once on this platform, in a
# way nothing here could reproduce off it: a fresh `npm ci` in CI and in a
# plain dev checkout both download and launch a working Chrome every time.
# The package installed above already ships a real, working Chromium binary
# that this Dockerfile was paying the weight of anyway, purely for its shared
# libraries until now. Pointing Puppeteer straight at it removes the failure
# mode rather than chasing it: nothing is downloaded at launch time to land in
# the wrong place or drift from what actually shipped, because what runs is
# the exact binary this RUN step just installed. capture.ts still falls back
# to Puppeteer's own resolution when this is unset -- a plain `npm run dev` or
# `npm test` outside this image has no `/usr/bin/chromium` to point at, and
# both already download and launch their own Chrome correctly.
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node dependencies before the rest of the source, so a Python-only change does
# not reinstall sharp. devDependencies are needed at build time for typescript,
# and NODE_ENV=production would otherwise make npm skip them.
COPY modules/ad_builder/package.json modules/ad_builder/package-lock.json ./modules/ad_builder/
RUN cd modules/ad_builder && npm ci --include=dev --no-audit --no-fund

# Puppeteer resolves its download-and-launch cache directory from $HOME by
# default (~/.cache/puppeteer), and this Dockerfile never pinned it -- so
# `npm ci` below downloads Chrome into whatever $HOME resolves to during the
# *build* stage (root, building the image: /root), and capture.ts's
# puppeteer.launch() looks for it in whatever $HOME resolves to when the
# *container actually runs* on Render. Nothing here asserted those are the
# same value, and on this platform they are not: a paint animation request
# failed at launch with "Could not find Chrome (ver. ...)" on production,
# and nothing calls captureFrames() at boot to have surfaced it any sooner --
# the mismatch is invisible until the first real render request.
# Pinned to an absolute path here, so the download and the later launch
# agree regardless of what $HOME turns out to mean at either point. A
# Dockerfile ENV applies to every layer built after it and is baked into the
# image itself, so nothing in docker-start.sh has to repeat this for it to
# hold at runtime too.
ENV PUPPETEER_CACHE_DIR=/opt/puppeteer-cache

COPY modules/hf_render_service/package.json modules/hf_render_service/package-lock.json ./modules/hf_render_service/
RUN cd modules/hf_render_service && npm ci --include=dev --no-audit --no-fund

# ---------------------------------------------------------------------------
# The Marketing Efficiency Audit -- the accounting-partner lead form (see
# hub/marketing_audit_proxy.py). Plain Express, no TypeScript and no build
# step, so unlike the two blocks above this is the whole of what it needs:
# install once here, before COPY . ., for the same layer-caching reason.
# ---------------------------------------------------------------------------
COPY modules/marketing_audit/package.json modules/marketing_audit/package-lock.json ./modules/marketing_audit/
RUN cd modules/marketing_audit && npm ci --omit=dev --no-audit --no-fund

COPY . .

# The start script must be executable or the container never boots -- and it
# is checked out on Windows, where the file mode does not always survive. One
# chmod costs nothing and removes a failure that takes the whole Hub down, not
# just the ad builder.
RUN chmod +x /app/docker-start.sh

# Compile the TypeScript once, at build time. Doing it at boot would make every
# cold start pay for it and would put tsc on the critical path of a deploy.
RUN cd modules/ad_builder && npm run build && npm prune --omit=dev
RUN cd modules/hf_render_service && npm run build && npm prune --omit=dev

ENV PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    ADBUILDER_PORT=8791 \
    HF_RENDER_PORT=8792
EXPOSE 8000

# Both processes. See docker-start.sh for why this is a script rather than two
# CMDs — a container has one PID 1, and the wrong supervisor turns a crashed
# renderer into a silently half-working Hub.
CMD ["/app/docker-start.sh"]
