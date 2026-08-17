"""Headless pass over every Smart 1 Ads page, with the Hub sidebar simulated.

Fails on any console error or page exception, and writes screenshots so the
pages can be eyeballed before they go near the real Hub.

    python3 ui_check.py
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_ui_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "ui.db")
os.environ["GOOGLE_ADS_REDIRECT_URI"] = "http://localhost:8098/tools/ads/oauth/callback"

from werkzeug.middleware.dispatcher import DispatcherMiddleware  # noqa: E402

from modules.ads_builder import app as ads  # noqa: E402
from modules.ads_builder import store  # noqa: E402
from test_ads_module import SAMPLE, StubAuthGuard  # noqa: E402

PORT = 8098
MOUNT = "/tools/ads"

# A stand-in for hub/sidebar.py's render_sidebar output, so the screenshots
# show the real 224px offset the Hub applies to every module page.
SIDEBAR = b"""
<style>
@media (min-width:950px){ body{ margin-left:224px !important } }
.s1hub-sb{position:fixed;top:0;bottom:0;left:0;width:224px;z-index:99990;background:#1a2e58;
  color:#c9d4ea;font:13.5px 'Segoe UI',system-ui,sans-serif;overflow-y:auto}
.s1hub-sb .lg{display:flex;align-items:center;gap:10px;padding:18px;border-bottom:1px solid rgba(255,255,255,.08)}
.s1hub-sb .mk{width:34px;height:34px;border-radius:10px;background:rgba(255,255,255,.12);color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px}
.s1hub-sb .nm{font-weight:700;font-size:16px;color:#fff}
.s1hub-sb .sec{padding:14px 18px 4px;font-size:10.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:1px;color:#7d8db2}
.s1hub-sb a{display:flex;gap:10px;padding:9px 18px;color:#c9d4ea;text-decoration:none;
  border-left:3px solid transparent}
.s1hub-sb a.on{background:rgba(255,255,255,.1);color:#fff;border-left-color:#5b8bff;font-weight:600}
</style>
<nav class="s1hub-sb">
  <div class="lg"><div class="mk">S1</div><span class="nm">Smart 1 Hub</span></div>
  <div class="sec">Overview</div>
  <a href="/">Dashboard</a><a href="/client360">Client 360</a>
  <div class="sec">Modules</div>
  <a href="/clients">Clients</a><a href="/google/">Google</a><a href="/sites/">Sites</a><a href="/suite/">Suite</a>
  <div class="sec">Scans</div><a href="/scans/">Scans</a>
  <div class="sec">SEO</div><a href="/seo">SEO Clients</a>
  <div class="sec">Ads</div><a class="on" href="/tools/ads/">Smart 1 Ads</a>
  <div class="sec">Sales</div><a href="/sales/builder/">Sales Builder</a><a href="/sales/proposals/">Proposal Builder</a>
  <div class="sec">Utilities</div><a href="/tools">Tools</a><a href="/qa">QA Reports</a>
  <a href="/activity">Activity Log</a><a href="/status">System Status</a>
</nav>
"""


class HubBar:
    """Mirrors the Hub's HubBar middleware: injects the sidebar into module HTML."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        cap = {}

        def _start(status, headers, exc_info=None):
            cap.update(status=status, headers=headers, exc_info=exc_info)
            return lambda *_: None

        result = self.app(environ, _start)
        headers = cap.get("headers", [])
        ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
        if "text/html" not in ctype:
            start_response(cap["status"], headers, cap.get("exc_info"))
            return result

        body = b"".join(result)
        body = body.replace(b"</body>", SIDEBAR + b"</body>", 1) if b"</body>" in body else body + SIDEBAR
        headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
        headers.append(("Content-Length", str(len(body))))
        start_response(cap["status"], headers, cap.get("exc_info"))
        return [body]


def hub_root(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/html")])
    return [b"<html><body>Hub</body></html>"]


application = DispatcherMiddleware(hub_root, {MOUNT: StubAuthGuard(HubBar(ads.app))})


class QuietServer(WSGIServer):
    def handle_error(self, request, client_address):
        pass


def main():
    from playwright.sync_api import sync_playwright

    proposal = store.create_proposal("Northside Roofing Co", SAMPLE,
                                     created_by="todd@smart1marketing.com")
    store.set_status(proposal["id"], "APPROVED")
    store.add_comment(proposal["id"], "Todd", "Cut ad group 2 — budget is too thin for two.")
    pid = proposal["id"]

    server = make_server("127.0.0.1", PORT, application, server_class=QuietServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.4)

    shots = ROOT / "shots"
    shots.mkdir(exist_ok=True)
    problems = []

    print("\nUI check\n")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        page = browser.new_page(viewport={"width": 1440, "height": 940})
        page.on("console", lambda m: problems.append(f"console: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        for name, path, wait in (
            ("01-campaigns", "/", "text=Google Ads is not connected yet"),
            ("02-generator", "/generator", "text=Client and objective"),
            ("03-approvals", "/approvals", "text=Northside Roofing Co"),
            ("04-proposal", f"/proposal/{pid}", "text=Negative keyword vault"),
            ("05-client-proposal", f"/proposal/{pid}/client", "text=Paid Search Proposal"),
            ("06-activity", "/activity", "text=Activity"),
            ("07-settings", "/settings", "text=Environment reference"),
        ):
            page.goto(f"http://127.0.0.1:{PORT}{MOUNT}{path}", wait_until="networkidle")
            page.wait_for_selector(wait, timeout=8000)
            page.wait_for_timeout(500)
            page.screenshot(path=str(shots / f"{name}.png"), full_page=True)
            print(f"  captured {name}")

        # exercise the budget slider so the viability engine is proven in-browser
        page.goto(f"http://127.0.0.1:{PORT}{MOUNT}/generator", wait_until="networkidle")
        page.select_option("#sector", "legal")
        page.eval_on_selector("#budget", """el => {
            const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
            set.call(el, '600'); el.dispatchEvent(new Event('input', {bubbles:true}));
        }""")
        page.wait_for_selector(".n-bad", timeout=6000)
        page.wait_for_timeout(400)
        page.screenshot(path=str(shots / "08-budget-critical.png"), full_page=True)
        print("  captured 08-budget-critical")

        browser.close()

    server.shutdown()

    real = [p for p in problems if "favicon" not in p]
    if real:
        print(f"\n{len(real)} browser problem(s):")
        for p in real:
            print("  -", p)
        return 1
    print("\nNo console errors, no page exceptions.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
