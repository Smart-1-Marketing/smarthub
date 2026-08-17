#!/usr/bin/env python3
"""Install Smart 1 Ads (v6.1ads) into a Smart 1 Hub checkout.

Copies ``modules/ads_builder`` into the Hub and makes the four small edits that
register it: the wsgi mount, the sidebar entry, the Tools tile and the
env.example block.

Every edit is idempotent — running it twice changes nothing the second time —
and it writes a .bak of anything it touches.

    python3 install_into_hub.py /path/to/smarthub-main
    python3 install_into_hub.py /path/to/smarthub-main --dry-run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "modules" / "ads_builder"

MOUNT = "/tools/ads"

# --------------------------------------------------------------- fragments
WSGI_MOUNT_ACTIVE = '    "/tools/ads": "ads",\n'

WSGI_LOADER = '''
try:
    import importlib as _il3
    adsb = _il3.import_module("modules.ads_builder.app")
    adsb_fb = None
except Exception as _ads_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    adsb, adsb_fb = None, _fallback_app("Smart 1 Ads", str(_ads_exc))

'''

WSGI_DISPATCH = '    "/tools/ads": _mount(adsb.app, "/tools/ads") if adsb else adsb_fb,\n'

SIDEBAR_ITEMS = '''    ("_secads", "", "", "Ads"),
    ("ads", "/tools/ads/", "&#9679;", "Smart 1 Ads"),
'''

TOOLS_TILE = '''  <a class="tool-tile" href="/tools/ads/">
    <div class="t-ico">&#128200;</div>
    <h3>Smart 1 Ads</h3>
    <p>Google Ads campaign operations &mdash; monitor, pause and enable live campaigns, generate a full
       search campaign with an AI strategist, run it through the approval hub, then deploy it paused
       into the client account. Client-ready PDF proposal included.</p>
  </a>
'''

ENV_BLOCK = '''
# ================= Smart 1 Ads (Google Ads) =================
# Separate from the Google Finder credentials above on purpose, so the two
# modules can never fight over one OAuth client. If GOOGLE_ADS_CLIENT_ID is
# blank the module falls back to GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET.
GOOGLE_ADS_CLIENT_ID=
GOOGLE_ADS_CLIENT_SECRET=
# Google Ads MANAGER account > Tools > API Center
GOOGLE_ADS_DEVELOPER_TOKEN=
# Your MCC id, digits only, no dashes
GOOGLE_ADS_LOGIN_CUSTOMER_ID=
# Must match the Authorised redirect URI in Google Cloud Console exactly
GOOGLE_ADS_REDIRECT_URI=https://YOUR-HUB-URL/tools/ads/oauth/callback
# Shown once after you click Connect Google Ads. Paste it back here to pin it.
GOOGLE_ADS_REFRESH_TOKEN=
# Optional. v21-v25 are documented as of Aug 2026; v25 is current.
GOOGLE_ADS_API_VERSION=v25
'''


class Result:
    def __init__(self):
        self.changed, self.skipped, self.failed = [], [], []


def patch(path: Path, marker: str, apply, res: Result, label: str, dry: bool):
    """Apply ``apply(text) -> text`` unless ``marker`` is already present."""
    if not path.exists():
        res.failed.append(f"{label}: {path} not found")
        return
    text = path.read_text(encoding="utf-8")
    if marker in text:
        res.skipped.append(f"{label}: already present")
        return
    try:
        new_text = apply(text)
    except ValueError as exc:
        res.failed.append(f"{label}: {exc}")
        return
    if new_text == text:
        res.failed.append(f"{label}: nothing matched — patch by hand (see INSTALL.md)")
        return
    if not dry:
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        path.write_text(new_text, encoding="utf-8")
    res.changed.append(f"{label}: patched {path.name}")


def insert_before(text: str, anchor: str, block: str, what: str) -> str:
    idx = text.find(anchor)
    if idx == -1:
        raise ValueError(f"could not find {what}")
    return text[:idx] + block + text[idx:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hub_root", help="path to the smarthub-main checkout")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    hub = Path(args.hub_root).expanduser().resolve()
    dry = args.dry_run
    res = Result()

    wsgi = hub / "wsgi.py"
    if not wsgi.exists():
        print(f"error: {hub} does not look like a Smart 1 Hub checkout (no wsgi.py)")
        return 2

    # ------------------------------------------------------- 1. the module
    dest = hub / "modules" / "ads_builder"
    if dry:
        res.changed.append(f"copy module: would write {dest}")
    else:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.db"))
        res.changed.append(f"copy module: {dest}")

    # ------------------------------------------------------ 2. wsgi mounts
    def do_wsgi(text: str) -> str:
        # a. sidebar-highlight map
        anchor = '    "/tools/image": "tools", "/tools/pdf": "tools",'
        if anchor in text:
            text = text.replace(anchor, anchor + "\n" + WSGI_MOUNT_ACTIVE.rstrip("\n"), 1)
        else:
            text = insert_before(text, "}\n\n\nclass AuthGuard", WSGI_MOUNT_ACTIVE,
                                 "the _MOUNT_ACTIVE map")
        # b. module loader, just before the _mount helper
        text = insert_before(text, "def _mount(", WSGI_LOADER.lstrip("\n"), "the _mount helper")
        # c. dispatch table entry
        text = insert_before(text, '    "/tools/pdf":', WSGI_DISPATCH,
                             "the /tools/pdf dispatch entry")
        return text

    patch(wsgi, '"/tools/ads"', do_wsgi, res, "wsgi.py mount", dry)

    # --------------------------------------------------------- 3. sidebar
    def do_sidebar(text: str) -> str:
        return insert_before(text, '    ("_sec2", "", "", "Sales"),', SIDEBAR_ITEMS,
                             "the Sales section of _ITEMS")

    patch(hub / "hub" / "sidebar.py", '"/tools/ads/"', do_sidebar, res, "sidebar entry", dry)

    # ------------------------------------------------------ 4. tools tile
    def do_tools(text: str) -> str:
        return insert_before(text, "</div>\n{% endblock %}", TOOLS_TILE, "the tool-tiles block")

    patch(hub / "hub" / "templates" / "tools.html", "/tools/ads/", do_tools, res,
          "Tools page tile", dry)

    # ------------------------------------------------------- 5. env sample
    def do_env(text: str) -> str:
        return text.rstrip("\n") + "\n" + ENV_BLOCK

    patch(hub / "env.example", "GOOGLE_ADS_DEVELOPER_TOKEN", do_env, res,
          "env.example block", dry)

    # ---------------------------------------------------------- 6. report
    print(f"\nSmart 1 Ads → {hub}{'   (dry run)' if dry else ''}\n")
    for line in res.changed:
        print("  +", line)
    for line in res.skipped:
        print("  =", line)
    for line in res.failed:
        print("  !", line)

    print("\nNo new Python dependencies — Flask, requests and SQLAlchemy are already "
          "in requirements.txt.")
    if res.failed:
        print("\nSome edits need doing by hand. INSTALL.md has the exact snippets.")
        return 1
    print("\nNext: set the GOOGLE_ADS_* variables, redeploy, then open /tools/ads/settings "
          "and click Connect Google Ads.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
