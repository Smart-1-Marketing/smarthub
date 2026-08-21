"""Local dev server. Not used in production — Render runs gunicorn on wsgi:application."""
import os

os.environ.setdefault("SECRET_KEY", "local-dev-secret")
os.environ.setdefault("PANEL_PASSWORD", "localdev")

from werkzeug.serving import run_simple          # noqa: E402
from wsgi import application                     # noqa: E402

if __name__ == "__main__":
    run_simple("127.0.0.1", 5055, application, use_reloader=False, threaded=True)
