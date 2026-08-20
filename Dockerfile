FROM python:3.12-slim

# Ghostscript + qPDF for the PDF optimizer.
#
# Chromium is here for the landing pages whose PDF is an HTML template
# rendered by a browser (tourism first). It is a deliberate cost: roughly
# 300 MB on the image and slower builds, paid by every tool. The alternative
# was rebuilding those documents in reportlab, which changes a PDF clients
# already receive — Todd chose to keep them identical.
#
# --no-install-recommends matters: without it Chromium drags in a desktop
# stack and the image roughly doubles again.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ghostscript qpdf \
        chromium fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Playwright would download its own browser; point it at the system one so
# the image isn't carrying two copies of Chromium.
ENV CHROME_BIN=/usr/bin/chromium \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD gunicorn wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 8 --timeout 180
