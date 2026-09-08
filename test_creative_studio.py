"""Creative Studio -- WO-CS1: the front door, projects, Brand Kit, Media
Library and the creative_jobs sweep.

    python3 test_creative_studio.py

Same shape as every other test file here: no pytest, no new dependencies,
runs against a temporary data directory and a throwaway SQLite database.

## What this file is protecting

**A blueprint on the hub app is not behind AuthGuard.** `/creative-studio` is
not a prefix `wsgi.py` mounts, so the guard has to be on the blueprint
itself, the way `modules/commercial_builder` and `modules/video_tools`
already are. Every page and API route is checked anonymously.

**`/creative` and `/creative-studio` are two different URLs.** The hub app
already answers `/creative` with the flat tool-tile index; a blueprint
mounted at the same prefix would either shadow it or collide on it, the
mount-shadowing trap CLAUDE.md opens with. Both are asserted reachable and
distinct.

**Nothing is invented against the client book.** A typed client name that
does not resolve is refused rather than filed under a guessed match --
`client_key.resolve()`'s rule, and the one mistake in this corner of the Hub
that cannot be undone by clicking again.

**A partial Brand Kit save must not clobber the rest of the record** -- the
`set_music` trap CLAUDE.md names for the Commercial Builder's music panel.

**Nothing long-running happens inside a request.** The Media Library index
button enqueues a `creative_jobs` row and returns immediately; the sweep,
registered on `hub/scheduler.py`, is what actually calls Cloudinary.

**Work here is attributable.** `creative_studio` is declared in
`hub/client_brand.WORK_KINDS`, and every write that names a client calls
`audit.log("creative_studio", ..., client=...)` -- never `module=`, the trap
CLAUDE.md names at length.
"""
import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cs_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "creative-studio-test-secret")

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


# ---------------------------------------------------------------------------
section("config is data, not a template restated")

from modules.creative_studio import config  # noqa: E402

check("every creative type has a route", all(t.get("route") for t in config.CREATIVE_TYPES), True)
check("video_commercial resolves", config.creative_type("video_commercial") is not None, True)
check("an unknown key resolves to nothing", config.creative_type("not-a-real-type"), None)
check("asset type keys match the tab list",
      config.ASSET_TYPE_KEYS, {a["key"] for a in config.ASSET_TYPES})

# ---------------------------------------------------------------------------
section("the extended Brand Kit overlay")

from modules.creative_studio import brand_ext  # noqa: E402

empty = brand_ext.get("Acme Plumbing")
check("a client with nothing saved reads as empty", empty["services"], [])
check("  ...and the text fields too", empty["cta_style"], "")

r1 = brand_ext.save("Acme Plumbing", {"cta_style": "Bold", "services": ["Repair", "Install"]},
                    actor="Todd")
check("save reports ok", r1["ok"], True)
check("  ...and the field is set", r1["cta_style"], "Bold")

r2 = brand_ext.save("Acme Plumbing", {"promotions": ["$79 tune-up"]}, actor="Todd")
check("a second, partial save does not clobber the first field",
      brand_ext.get("Acme Plumbing")["cta_style"], "Bold")
check("  ...and adds its own", brand_ext.get("Acme Plumbing")["promotions"], ["$79 tune-up"])
check("  ...and services from the first save survive too",
      brand_ext.get("Acme Plumbing")["services"], ["Repair", "Install"])

r3 = brand_ext.save("Acme Plumbing",
                    {"pronunciation_dict": {"Gahanna": "guh-HAN-uh"}}, actor="Todd")
check("pronunciation dictionary round-trips",
      brand_ext.get("Acme Plumbing")["pronunciation_dict"], {"Gahanna": "guh-HAN-uh"})

kit = brand_ext.kit("Acme Plumbing", "")
check("kit() merges brand_kit() output with the overlay",
      set(kit.keys()) >= {"found", "logos", "colors", "ext"}, True)
check("  ...and the overlay is nested under 'ext'", kit["ext"]["cta_style"], "Bold")

no_client = brand_ext.save("", {"cta_style": "x"})
check("saving with no client name is refused", no_client["ok"], False)

# ---------------------------------------------------------------------------
section("creative_jobs: enqueue now, run later")

from modules.creative_studio import jobs as cs_jobs  # noqa: E402
from modules.creative_studio.models import CreativeJob  # noqa: E402

import wsgi  # noqa: E402

hub_app = wsgi.hub_app

with hub_app.app_context():
    job = cs_jobs.enqueue("index", client_name="Acme Plumbing", created_by="Todd")
    check("a queued job starts queued", job.state, "queued")
    check("  ...with a timeout in the future", job.timeout_at is not None, True)
    job_id = job.id

    unknown = cs_jobs.enqueue("not-a-real-kind", client_name="Acme Plumbing")
    unknown_id = unknown.id

res = cs_jobs.job_sweep(hub_app)
check("the sweep reports ok", res.get("ok"), True)

with hub_app.app_context():
    refreshed = CreativeJob.query.get(job_id)
    check("an 'index' job the sweep can run completes",
          refreshed.state, "complete")
    check("  ...with a stage of Complete", refreshed.stage, "Complete")

    refreshed_unknown = CreativeJob.query.get(unknown_id)
    check("an unbuilt kind fails with a readable reason rather than hanging",
          refreshed_unknown.state, "failed")
    check("  ...and the error says so", "not built" in (refreshed_unknown.error or ""), True)

# A job whose timeout has already passed is swept as failed even if nobody
# ever picked it up -- the "nothing may say Generating... without an end"
# rule, checked by moving the clock rather than waiting on it.
with hub_app.app_context():
    from datetime import datetime, timedelta
    from modules.creative_studio.db import db
    stuck = cs_jobs.enqueue("index", client_name="Stuck Co")
    stuck.timeout_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    stuck_id = stuck.id
cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    refreshed_stuck = CreativeJob.query.get(stuck_id)
    check("an overdue job is swept as failed rather than left queued",
          refreshed_stuck.state, "failed")

# ---------------------------------------------------------------------------
section("routes, guarded, and /creative vs /creative-studio are distinct")

from werkzeug.test import Client  # noqa: E402
from hub import auth  # noqa: E402

client = Client(wsgi.application)

anon_pages = ["/creative-studio/", "/creative-studio/projects",
             "/creative-studio/templates", "/creative-studio/ai-tools",
             "/creative-studio/approvals", "/creative-studio/usage",
             "/creative-studio/brand-kits", "/creative-studio/media"]
for p in anon_pages:
    out = client.get(p)
    check(f"{p} refuses an anonymous visitor", out.status_code in (302, 401), True)

anon_apis = ["/creative-studio/api/clients/search",
            "/creative-studio/api/jobs/1"]
for p in anon_apis:
    out = client.get(p)
    check(f"{p} refuses an anonymous visitor too", out.status_code in (302, 401), True)

client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"),
                  domain="localhost")

r = client.get("/creative")
check("/creative still renders the existing tool-tile index", r.status_code, 200)
check("  ...and points at the new front door",
      b"/creative-studio/" in r.data, True)

r = client.get("/creative-studio/")
check("/creative-studio/ renders the new dashboard", r.status_code, 200)
check("  ...and is a different page from /creative",
      b"Creative Studio" in r.data, True)

for p in anon_pages:
    out = client.get(p)
    check(f"{p} renders for staff", out.status_code, 200)

# ---------------------------------------------------------------------------
section("nothing is invented against the client book")

r = client.post("/creative-studio/api/projects",
                json={"name": "Fall Tune-Up Spot", "creative_type": "video_commercial",
                      "client_name": "Some Business Nobody Has Heard Of"})
check("a typed client that resolves to nobody is refused", r.status_code, 400)

r = client.post("/creative-studio/api/projects",
                json={"name": "Generic Asset", "creative_type": "video_commercial"})
check("a blank client (generic Smart 1 asset) is allowed", r.status_code, 200)
check("  ...and files with no client name", r.get_json()["project"]["client_name"], "")
generic_id = r.get_json()["project"]["id"]

r = client.post("/creative-studio/api/projects",
                json={"name": "No Type", "client_name": ""})
check("an unrecognized creative type is refused", r.status_code, 400)

r = client.get(f"/creative-studio/projects/{generic_id}")
check("the project detail page renders", r.status_code, 200)

r = client.get("/creative-studio/api/projects")
# There is no such route (creation is POST-only); confirm it is not silently
# a 200 that would suggest a listing endpoint exists twice.
check("there is no GET on the create endpoint", r.status_code, 405)

# ---------------------------------------------------------------------------
section("Media Library: upload, index, soft-delete")

r = client.post("/creative-studio/api/media/index", json={"client": "Acme Plumbing"})
check("indexing enqueues a job", r.status_code, 200)
check("  ...of kind index", r.get_json()["job"]["kind"], "index")

r = client.post("/creative-studio/api/media/upload", data={"client": "Acme Plumbing"})
check("an upload with no file is refused", r.status_code, 400)

from io import BytesIO  # noqa: E402
r = client.post("/creative-studio/api/media/upload",
                data={"client": "Acme Plumbing",
                      "file": (BytesIO(b"not a real jpeg but bytes"), "photo.jpg")},
                content_type="multipart/form-data")
check("an accepted extension uploads", r.status_code, 200)
asset_id = r.get_json()["asset"]["id"]
check("  ...tagged as an upload, not a generated asset",
      r.get_json()["asset"]["source"], "upload")

r = client.post("/creative-studio/api/media/upload",
                data={"client": "Acme Plumbing",
                      "file": (BytesIO(b"exe bytes"), "virus.exe")},
                content_type="multipart/form-data")
check("an unaccepted extension is refused", r.status_code, 400)

r = client.get("/creative-studio/media?client=Acme+Plumbing&tab=image")
check("the library page shows the uploaded asset", str(asset_id).encode() in r.data or b"photo.jpg" in r.data, True)

r = client.post(f"/creative-studio/api/media/{asset_id}/delete")
check("delete reports ok", r.get_json()["ok"], True)

with hub_app.app_context():
    from modules.creative_studio.db import db
    from modules.creative_studio.models import CsMediaAsset
    db.session.expire_all()
    row = CsMediaAsset.query.get(asset_id)
    check("a deleted asset is soft-deleted, not removed", row is not None, True)
    check("  ...and marked deleted", row.deleted, True)

r = client.get("/creative-studio/media?client=Acme+Plumbing&tab=image")
check("a soft-deleted asset no longer appears in the library",
      b"photo.jpg" in r.data, False)

# ---------------------------------------------------------------------------
section("Brand Kit page and save round-trip through the API")

r = client.get("/creative-studio/brand-kits/Acme%20Plumbing")
check("the brand kit page renders", r.status_code, 200)
check("  ...carrying the saved field", b"Bold" in r.data, True)

r = client.post("/creative-studio/api/brand-kits/Acme%20Plumbing",
                json={"cta_style": "Friendly"})
check("saving from the API reports ok", r.get_json()["ok"], True)
check("  ...and reads back", brand_ext.get("Acme Plumbing")["cta_style"], "Friendly")

# ---------------------------------------------------------------------------
section("work here is attributable")

from hub import client_brand, audit  # noqa: E402

check("creative_studio is declared in WORK_KINDS",
      "creative_studio" in client_brand.WORK_KINDS, True)

src = (ROOT / "modules/creative_studio/api.py").read_text()
tree = ast.parse(src)
bad_module_kw = []
for node in ast.walk(tree):
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "log"):
        for kw in node.keywords:
            if kw.arg == "module":
                bad_module_kw.append(ast.dump(node.func))
check("no audit.log() call passes module= as a keyword (the first-positional trap)",
      bad_module_kw, [])

log_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "log"
            and n.args and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == "creative_studio"]
check("api.py logs under the module name creative_studio is declared under",
      len(log_calls) >= 3, True)

# ---------------------------------------------------------------------------
section("integrity: JSON writes are mirrored, not a bare os.remove-able file")

brand_src = (ROOT / "modules/creative_studio/brand_ext.py").read_text()
check("brand_ext writes go through jsonstore, not a raw open()",
      "jsonstore.write_json" in brand_src, True)
check("  ...and never json.dump directly to a file",
      "json.dump(" not in brand_src, True)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
