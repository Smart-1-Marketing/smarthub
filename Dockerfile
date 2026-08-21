FROM python:3.12-slim

# Ghostscript + qPDF for the PDF optimizer.
#
# Chromium used to be installed here so one landing page could render its PDF
# from an HTML template. That was roughly 300 MB on the image and slower builds
# for every tool, to serve a single module. Tourism now builds its PDF with
# reportlab like the other six landing pages, so the browser is gone.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ghostscript qpdf curl ca-certificates \
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

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node dependencies before the rest of the source, so a Python-only change does
# not reinstall sharp. devDependencies are needed at build time for typescript,
# and NODE_ENV=production would otherwise make npm skip them.
COPY modules/ad_builder/package.json modules/ad_builder/package-lock.json ./modules/ad_builder/
RUN cd modules/ad_builder && npm ci --include=dev --no-audit --no-fund

COPY . .

# The start script must be executable or the container never boots -- and it
# is checked out on Windows, where the file mode does not always survive. One
# chmod costs nothing and removes a failure that takes the whole Hub down, not
# just the ad builder.
RUN chmod +x /app/docker-start.sh

# Compile the TypeScript once, at build time. Doing it at boot would make every
# cold start pay for it and would put tsc on the critical path of a deploy.
RUN cd modules/ad_builder && npm run build && npm prune --omit=dev

ENV PYTHONUNBUFFERED=1 \
    NODE_ENV=production \
    ADBUILDER_PORT=8791
EXPOSE 8000

# Both processes. See docker-start.sh for why this is a script rather than two
# CMDs — a container has one PID 1, and the wrong supervisor turns a crashed
# renderer into a silently half-working Hub.
CMD ["/app/docker-start.sh"]
