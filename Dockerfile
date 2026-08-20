FROM python:3.12-slim

# Ghostscript + qPDF for the PDF optimizer.
#
# Chromium used to be installed here so one landing page could render its PDF
# from an HTML template. That was roughly 300 MB on the image and slower builds
# for every tool, to serve a single module. Tourism now builds its PDF with
# reportlab like the other six landing pages, so the browser is gone.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ghostscript qpdf \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD gunicorn wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 8 --timeout 180
