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

from hub import client_brand  # noqa: E402

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

# ---------------------------------------------------------------------------
section("WO-CS2: layouts are a fixed vocabulary, not free text")

from modules.creative_studio import layouts as cs_layouts  # noqa: E402

check("hook_fullbleed accepts a headline", "headline" in cs_layouts.layers_for("hook_fullbleed"), True)
check("hook_fullbleed does not accept a phone number",
      "phone" not in cs_layouts.layers_for("hook_fullbleed"), True)
check("a layout with an unaccepted layer is invalid",
      cs_layouts.validate_layers("hook_fullbleed", {"phone": "x"}), ["phone"])
check("a layout with only accepted layers is valid",
      cs_layouts.validate_layers("hook_fullbleed", {"headline": "x"}), [])
check("an unknown layout key rejects every layer",
      cs_layouts.validate_layers("not-a-real-layout", {"headline": "x"}), ["headline"])
check("vertical_caption is 9:16 only",
      cs_layouts.LAYOUTS["vertical_caption"]["aspect_ratios"], ("9:16",))
check("all 8 layouts from the build spec exist",
      set(cs_layouts.LAYOUTS) == {"hook_fullbleed", "problem_split", "proof_lower_third",
                                  "offer_card", "end_card", "logo_reveal",
                                  "social_follow", "vertical_caption"}, True)

# ---------------------------------------------------------------------------
section("WO-CS2: the resolver's priority order")

from modules.creative_studio import resolver as cs_resolver  # noqa: E402
from modules.creative_studio.models import CsTemplate, CsTemplateVariable  # noqa: E402

with hub_app.app_context():
    from modules.creative_studio.db import db as cs_db
    rtmpl = CsTemplate(id="resolver-test", name="Resolver Test", creative_type="video_commercial",
                       duration=15, aspect_ratio="16:9", status="published", version=1)
    cs_db.session.add(rtmpl)
    cs_db.session.add(CsTemplateVariable(template_id="resolver-test", name="headline",
                                         source="brief", default="Default headline", required=True))
    cs_db.session.add(CsTemplateVariable(template_id="resolver-test", name="phone",
                                         source="brand", default="", required=True))
    cs_db.session.add(CsTemplateVariable(template_id="resolver-test", name="nothing_anywhere",
                                         source="brief", default="", required=True))
    cs_db.session.add(CsTemplateVariable(template_id="resolver-test", name="weather_headline",
                                         source="weather", default="", required=False))
    cs_db.session.commit()

    tmpl = CsTemplate.query.get("resolver-test")

    class _FakeProject:
        resolved_vars = {"headline": "Manual override headline"}
        brief = {"headline": "Brief headline", "nothing_anywhere": ""}

    resolved = cs_resolver.resolve(tmpl, _FakeProject(), client="", domain="")
    check("a manual override wins over the brief",
          resolved["headline"]["value"], "Manual override headline")
    check("  ...and is reported as such", resolved["headline"]["source"], "manual")

    resolved_no_override = cs_resolver.resolve(
        tmpl, type("P", (), {"resolved_vars": {}, "brief": {"headline": "Brief headline"}})(),
        client="", domain="")
    check("with no override, the brief wins", resolved_no_override["headline"]["value"], "Brief headline")
    check("  ...and is reported as such", resolved_no_override["headline"]["source"], "brief")

    resolved_no_brief = cs_resolver.resolve(tmpl, None, client="", domain="")
    check("with nothing typed, the template default is used",
          resolved_no_brief["headline"]["value"], "Default headline")
    check("  ...and is reported as such", resolved_no_brief["headline"]["source"], "template_default")

    check("a required variable with no source anywhere is unresolved",
          resolved_no_brief["nothing_anywhere"]["source"], "unresolved")
    check("unresolved_required() finds it", "nothing_anywhere" in cs_resolver.unresolved_required(resolved_no_brief), True)
    check("  ...but not a resolved one", "headline" not in cs_resolver.unresolved_required(resolved_no_brief), True)

    check("weather_headline resolves like its base name (no active variant concept yet)",
          resolved_no_brief["weather_headline"]["value"], "")  # "headline" has no default under this name
    # weather_headline's OWN base is "headline", so it should read the brief's headline field too
    resolved_weather = cs_resolver.resolve(
        tmpl, type("P", (), {"resolved_vars": {}, "brief": {"headline": "Brief headline"}})(),
        client="", domain="")
    check("weather_headline reads the base 'headline' brief field",
          resolved_weather["weather_headline"]["value"], "Brief headline")

# ---------------------------------------------------------------------------
section("WO-CS2: the 12 seed templates")

from modules.creative_studio import seed_templates as cs_seed  # noqa: E402

with hub_app.app_context():
    published = CsTemplate.query.filter_by(status="published").count()
    check("at least the 12 build-spec templates are published", published >= 12, True)
    again = cs_seed.seed()
    check("re-running the seed creates nothing new (idempotent)", again, 0)
    check("re-running left the count unchanged",
          CsTemplate.query.filter_by(status="published").count(), published)

    for tid in ("general-30", "hvac-15", "restaurant-30", "home_services-30",
               "social-vertical-promo-15", "social-ugc-vertical-30",
               "brand-logo-intro-5", "brand-cta-outro-5"):
        t = CsTemplate.query.get(tid)
        check(f"{tid} exists and is published", t is not None and t.status == "published", True)
        check(f"  ...with at least one scene", t.scenes.count() >= 1, True)
        check(f"  ...and every scene's layers pass its own layout",
              all(not cs_layouts.validate_layers(s.layout_key, s.layers) for s in t.scenes), True)
        total = sum(s.default_duration for s in t.scenes)
        check(f"  ...whose scene durations sum to its own duration",
              abs(total - t.duration) < 0.01, True)

# ---------------------------------------------------------------------------
section("WO-CS2: gallery, preview and admin routes")

r = client.get("/creative-studio/templates")
check("the gallery renders", r.status_code, 200)
check("  ...listing a published template", b"General :30" in r.data, True)

r = client.get("/creative-studio/templates?type=video_commercial&industry=hvac")
# The filter dropdowns always list every industry on the book (computed from
# the unfiltered rows, the same DISTINCT-values rule every gallery filter in
# this Hub follows) so a bare "Restaurant" is present in the <option> list
# regardless of what is selected -- the card grid is the thing that narrows.
check("filtering by type and industry narrows the list",
      b"HVAC :15" in r.data and b"HVAC :30" in r.data
      and b"Restaurant :15" not in r.data and b"Restaurant :30" not in r.data, True)

r = client.get("/creative-studio/templates/general-30")
check("the preview page renders", r.status_code, 200)
check("  ...showing its variable table", b"template_default" in r.data or b"unresolved" in r.data, True)

r = client.get("/creative-studio/templates/not-a-real-template")
check("an unknown template 404s rather than 500ing", r.status_code, 404)

r = client.get("/creative-studio/templates/admin")
check("Template Admin renders for the (shared-password) test session", r.status_code, 200)

r = client.get("/creative-studio/templates/admin/new")
check("the new-template form renders", r.status_code, 200)

r = client.get("/creative-studio/api/templates/layouts")
check("the layouts API answers", r.status_code, 200)
check("  ...with all 8 layouts", len(r.get_json()["layouts"]), 8)

# ---------------------------------------------------------------------------
section("WO-CS2: creating, editing and publishing a template")

r = client.post("/creative-studio/api/templates",
                json={"id": "wo-cs2-test", "name": "WO-CS2 Test",
                      "creative_type": "video_commercial", "duration": 20})
check("creating a template works", r.status_code, 200)
check("  ...and starts as a draft", r.get_json()["template"]["status"], "draft")

r = client.post("/creative-studio/api/templates",
                json={"id": "wo-cs2-test", "name": "dup", "creative_type": "video_commercial"})
check("a duplicate slug id is refused", r.status_code, 400)

r = client.post("/creative-studio/api/templates",
                json={"id": "Not A Valid Slug!", "name": "x", "creative_type": "video_commercial"})
check("an invalid slug id is refused", r.status_code, 400)

r = client.post("/creative-studio/api/templates/wo-cs2-test/scenes",
                json={"layout_key": "hook_fullbleed", "default_duration": 5,
                      "layers": {"cta": "not allowed here"}})
check("a scene whose layer the layout does not accept is refused", r.status_code, 400)

r = client.post("/creative-studio/api/templates/wo-cs2-test/scenes",
                json={"layout_key": "hook_fullbleed", "default_duration": 5,
                      "layers": {"headline": "{{headline}}", "background": "slot:image"}})
check("a valid scene is added", r.status_code, 200)
scene_id = r.get_json()["scene"]["id"]

r = client.post("/creative-studio/api/templates/wo-cs2-test/variables",
                json={"name": "headline", "source": "brief", "default": "Hi", "required": True})
check("a variable is added", r.status_code, 200)

r = client.post("/creative-studio/api/templates/wo-cs2-test/publish")
check("publishing succeeds once there is a scene", r.status_code, 200)
check("  ...and bumps the version", r.get_json()["template"]["version"], 2)

r = client.get("/creative-studio/templates?type=video_commercial")
check("a published template appears in the gallery", b"WO-CS2 Test" in r.data, True)

r = client.post("/creative-studio/api/templates/wo-cs2-test/disable")
check("archiving succeeds", r.status_code, 200)
check("  ...and sets status archived", r.get_json()["template"]["status"], "archived")

r = client.get("/creative-studio/templates")
check("an archived template leaves the public gallery", b"WO-CS2 Test" not in r.data, True)

r = client.post("/creative-studio/api/templates/wo-cs2-test/duplicate",
                json={"id": "wo-cs2-test-copy"})
check("duplicating carries scenes and variables across", r.status_code, 200)
check("  ...the copy starts as a draft", r.get_json()["template"]["status"], "draft")
with hub_app.app_context():
    copy = CsTemplate.query.get("wo-cs2-test-copy")
    check("  ...with the same scene count", copy.scenes.count(), 1)
    check("  ...and the same variable count", copy.variables.count(), 1)

r = client.post(f"/creative-studio/api/templates/wo-cs2-test/scenes/{scene_id}/delete")
check("a scene can be deleted", r.status_code, 200)
with hub_app.app_context():
    remaining = CsTemplate.query.get("wo-cs2-test").scenes.count()
    check("  ...and is actually gone", remaining, 0)

# Cannot publish a template that publishing would leave with no scenes to
# begin with (an empty draft), because nothing there could ever resolve into
# a project a rep could build from.
r = client.post("/creative-studio/api/templates",
                json={"id": "wo-cs2-empty", "name": "Empty", "creative_type": "video_commercial"})
r = client.post("/creative-studio/api/templates/wo-cs2-empty/publish")
check("a template with no scenes cannot be published", r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS2: creating a project from a template pins its version")

r = client.post("/creative-studio/api/projects",
                json={"name": "Pinned version test", "template_id": "general-30"})
check("creating a project from a template succeeds", r.status_code, 200)
pinned_project = r.get_json()["project"]
check("  ...pins template_version", pinned_project["template_version"], 1)
check("  ...and inherits the template's duration", pinned_project["duration"], 30)
check("  ...and its aspect ratio", pinned_project["aspect_ratio"], "16:9")

r = client.get(f"/creative-studio/projects/{pinned_project['id']}")
check("the project detail page shows its resolved variables", r.status_code, 200)

r = client.post("/creative-studio/api/projects",
                json={"name": "Bad template", "template_id": "not-a-real-template"})
check("creating a project from an unknown template is refused", r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS2: Template Admin is admin-only")

with hub_app.app_context():
    from hub.users import User, db as udb
    from werkzeug.security import generate_password_hash
    member = User(email="cs-member@example.com", name="Member", role="member",
                 status="active", password_hash=generate_password_hash("x"))
    udb.session.add(member)
    udb.session.commit()
    from hub.users_routes import issue_cookie as issue_account_cookie
    member_cookie = issue_account_cookie(member)

from hub.users_routes import COOKIE_NAME as ACCOUNT_COOKIE  # noqa: E402
member_client = Client(wsgi.application)
member_client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Member"), domain="localhost")
member_client.set_cookie(ACCOUNT_COOKIE, member_cookie, domain="localhost")

r = member_client.get("/creative-studio/templates/admin")
check("a General Access (member) account is refused Template Admin", r.status_code, 403)
r = member_client.post("/creative-studio/api/templates",
                       json={"id": "member-cannot", "name": "x", "creative_type": "video_commercial"})
check("  ...and cannot create a template through the API either", r.status_code, 403)
r = member_client.get("/creative-studio/templates")
check("  ...but the public gallery is still open to them", r.status_code, 200)

with hub_app.app_context():
    from hub.users import User, db as udb
    # Re-query rather than reuse the `member` object from the block above --
    # Flask-SQLAlchemy's scoped session is torn down at app-context exit, so
    # the earlier instance is detached here and a commit against it would
    # silently not persist, which is exactly the failure mode this promotion
    # is meant to prove closes (a role change taking effect on the next click).
    member = User.query.filter_by(email="cs-member@example.com").first()
    member.role = "admin"
    udb.session.commit()
    admin_cookie = issue_account_cookie(member)
admin_client = Client(wsgi.application)
admin_client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Member"), domain="localhost")
admin_client.set_cookie(ACCOUNT_COOKIE, admin_cookie, domain="localhost")
r = admin_client.get("/creative-studio/templates/admin")
check("promoted to admin, the same account reaches Template Admin", r.status_code, 200)

# ---------------------------------------------------------------------------
section("WO-CS2: attribution and integrity")

with hub_app.app_context():
    log_events = {"template_published"}
    src = (ROOT / "modules/creative_studio/api.py").read_text()
    check("template_published is logged under the creative_studio module",
          'audit.log("creative_studio", "template_published"' in src, True)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
