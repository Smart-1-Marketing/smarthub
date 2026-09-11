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
client.post("/creative-studio/api/templates",
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
    src = (ROOT / "modules/creative_studio/api.py").read_text()
    check("template_published is logged under the creative_studio module",
          'audit.log("creative_studio", "template_published"' in src, True)

# ---------------------------------------------------------------------------
# WO-CS3 -- binding a cs_project to the Commercial Builder's Storyboard
# Editor. "Do not build a new editor and do not build a timeline": every
# assertion here is about the join, not about a second scene editor.
section("WO-CS3: opening a project built from a template")

from modules.creative_studio import binder as cs_binder  # noqa: E402

with hub_app.app_context():
    r = client.post("/creative-studio/api/projects",
                    json={"name": "HVAC spring tune-up", "template_id": "hvac-30"})
    cs_project_id = r.get_json()["project"]["id"]

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/open")
check("opening a project built from hvac-30 succeeds", r.status_code, 200)
cb_project_id = r.get_json()["cb_project_id"]
check("  ...and hands back the Storyboard Editor's URL",
      r.get_json()["url"], f"/tools/commercial-builder/project/{cb_project_id}/blueprint")

with hub_app.app_context():
    from modules.commercial_builder.models import CommercialProject as CbProject, Scene as CbScene
    cb_project = CbProject.query.get(cb_project_id)
    scenes = cb_project.scenes.order_by(CbScene.order_index).all()
    check("hvac-30 opens with five scenes", len(scenes), 5)
    check("  ...summing to 30", round(sum(s.end - s.start for s in scenes), 2), 30.0)
    check("  ...each carrying the template's own layout_key",
          [(s.asset_meta or {}).get("layout_key") for s in scenes],
          ["hook_fullbleed", "problem_split", "proof_lower_third", "offer_card", "end_card"])
    check("  ...and the end card is marked as the CTA scene", scenes[-1].is_cta, True)
    first_scene_id = scenes[0].id

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/open")
check("reopening an already-bound project is a no-op",
      r.get_json()["cb_project_id"], cb_project_id)

with hub_app.app_context():
    from modules.creative_studio.models import CsProject as CsProjectModel
    row = CsProjectModel.query.get(cs_project_id)
    check("the cs_project remembers which storyboard it handed the work to",
          row.cb_project_id, cb_project_id)

r = client.post("/creative-studio/api/projects", json={"name": "No template yet",
                                                        "creative_type": "video_commercial"})
no_tmpl_id = r.get_json()["project"]["id"]
r = client.post(f"/creative-studio/api/projects/{no_tmpl_id}/open")
check("a project with no template refuses to open", r.status_code, 400)

section("WO-CS3: the layer panel is limited to what the layout allows")

r = client.get(f"/tools/commercial-builder/project/{cb_project_id}/blueprint")
check("the Blueprint page renders", r.status_code, 200)
body = r.get_data(as_text=True)
check("  ...carrying the layer panel's vocabulary", "cs-layout-data" in body, True)
check("  ...and the layer panel container itself", "cb-layer-panel" in body, True)

r = client.put(f"/tools/commercial-builder/api/projects/{cb_project_id}/scenes/{first_scene_id}",
              json={"layers": {"phone": {"value": "555-1234", "source": "manual"}}})
check("a layer the layout does not allow is refused server-side", r.status_code, 400)
check("  ...naming the layer it refused", "phone" in (r.get_json().get("error") or ""), True)

r = client.put(f"/tools/commercial-builder/api/projects/{cb_project_id}/scenes/{first_scene_id}",
              json={"layers": {"headline": {"value": "Beat the heat", "source": "manual"}}})
check("a layer the layout does allow is saved", r.status_code, 200)
check("  ...and reads back", r.get_json()["scene"]["asset_meta"]["layers"]["headline"]["value"],
      "Beat the heat")

# A project not built from Creative Studio at all (the ordinary Commercial
# Builder flow) must draw no layer panel -- WO-CS3 changed nothing about a
# spot nobody bound to a template.
with hub_app.app_context():
    from modules.commercial_builder.client_link import ensure_client
    from modules.commercial_builder import template_bind
    plain_client = ensure_client("Plain Commercial Client", "")
    plain_cb = template_bind.build_from_template(
        client_id=plain_client.id, client_name=plain_client.name, title="No layout here",
        length_seconds=15, platform="both", formats=["16:9"], commercial_type="stock_vo",
        scenes=[{"layout_key": "", "default_duration": 15, "layers": {}, "label": ""}])
    plain_cb_id = plain_cb.id

r = client.get(f"/tools/commercial-builder/project/{plain_cb_id}/blueprint")
check("a project Creative Studio never touched renders with no layer-panel data",
      "cs-layout-data" in r.get_data(as_text=True), False)

section("WO-CS3: overriding a variable changes the project, not the Brand Kit")

with hub_app.app_context():
    from modules.creative_studio import brand_ext as cs_brand_ext
    before = cs_brand_ext.get("")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/variables",
                json={"name": "phone", "value": "(317) 555-0100"})
check("overriding a variable on the project succeeds", r.status_code, 200)
check("  ...and the resolver now reads it back as manual",
      r.get_json()["resolved"]["phone"], {"value": "(317) 555-0100", "source": "manual", "required": True})

with hub_app.app_context():
    after = cs_brand_ext.get("")
    check("  ...without touching the Brand Kit", after, before)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/variables",
                json={"name": "phone", "value": ""})
check("clearing an override is allowed", r.status_code, 200)
check("  ...and the variable falls back through the resolver order",
      r.get_json()["resolved"]["phone"]["source"] != "manual", True)

section("WO-CS3: the binder degrades rather than raising")

with hub_app.app_context():
    from modules.creative_studio.models import CsProject as CsProjectModel2
    ghost = CsProjectModel2(name="Ghost template", creative_type="video_commercial",
                            template_id="not-a-real-template", status="Draft")
    from modules.creative_studio.db import db as cs_db
    cs_db.session.add(ghost)
    cs_db.session.commit()
    result = cs_binder.bind(ghost)
    check("a template that no longer exists is reported, not raised", result["ok"], False)

# ---------------------------------------------------------------------------
section("WO-CS4: the AI Tools registry is data, not a template restated")

with hub_app.app_context():
    from modules.creative_studio.models import CsAiTool
    check("the seed ran at boot -- rows exist with no test-side seeding",
          CsAiTool.query.count() >= 14, True)
    check("  ...script_generator is seeded live",
          CsAiTool.query.filter_by(key="script_generator").first().status, "live")
    check("  ...product_lifestyle is seeded coming_soon",
          CsAiTool.query.filter_by(key="product_lifestyle").first().status, "coming_soon")

r = client.get("/creative-studio/ai-tools")
check("the AI Tools page renders", r.status_code, 200)
check("  ...carrying a live tool's name", b"Script Generator" in r.data, True)
check("  ...and a coming_soon tile", b"Coming soon" in r.data, True)

r = client.get("/creative-studio/ai-tools?category=video")
check("filtering by category narrows the page",
      b"Script Generator" in r.data and b"Product Lifestyle" not in r.data, True)

# ---------------------------------------------------------------------------
section("WO-CS4: generating concepts and a script as queued jobs")

r = client.post("/creative-studio/api/projects",
                json={"name": "No brief yet", "creative_type": "video_commercial"})
no_brief_id = r.get_json()["project"]["id"]
r = client.post(f"/creative-studio/api/projects/{no_brief_id}/generate/storyboard")
check("generating concepts with no brief and no template is refused",
      r.status_code, 400)

r = client.post("/creative-studio/api/projects",
                json={"name": "Fall Tune-Up Radio Spot", "creative_type": "video_commercial",
                      "brief": {"what_advertising": "Fall furnace tune-ups"}})
brief_project_id = r.get_json()["project"]["id"]
check("a project with a brief and no template is created",
      r.get_json()["project"]["template_id"], "")

r = client.post(f"/creative-studio/api/projects/{brief_project_id}/generate/storyboard")
check("generating concepts from a brief enqueues a storyboard job", r.status_code, 200)
check("  ...of kind storyboard", r.get_json()["job"]["kind"], "storyboard")
storyboard_job_id = r.get_json()["job"]["id"]

res = cs_jobs.job_sweep(hub_app)
check("the sweep reports ok", res.get("ok"), True)

with hub_app.app_context():
    job = CreativeJob.query.get(storyboard_job_id)
    check("the storyboard job completes", job.state, "complete")
    check("  ...carrying three concepts", len(job.output.get("concepts") or []), 3)

    from modules.creative_studio.models import CsProject as CsProjectModel3
    bound = CsProjectModel3.query.get(brief_project_id)
    check("binding for generation happened without a template",
          bound.cb_project_id is not None, True)
    brief_cb_id = bound.cb_project_id

    from modules.commercial_builder.models import CommercialProject as CbProject
    cb = CbProject.query.get(brief_cb_id)
    check("  ...and the bound storyboard carries the concepts",
          len(cb.concepts or []), 3)
    check("  ...with the brief passed straight through",
          cb.brief.get("what_advertising"), "Fall furnace tune-ups")

    from modules.creative_studio.models import CsUsageLog
    concept_usage = CsUsageLog.query.filter_by(
        project_id=brief_project_id, service="concepts").all()
    check("exactly one usage row for the concepts call", len(concept_usage), 1)
    check("  ...naming the provider", concept_usage[0].provider, "openai")

r = client.post(f"/creative-studio/api/projects/{brief_project_id}/generate/script")
check("generating a script (concepts already exist) enqueues a script job",
      r.status_code, 200)
script_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(script_job_id)
    check("the script job completes", job.state, "complete")
    check("  ...carrying scenes", len(job.output.get("script", {}).get("scenes") or []) > 0, True)

    cb = CbProject.query.get(brief_cb_id)
    check("  ...and Studio auto-selected the first concept, having no picker screen",
          cb.selected_concept_id, cb.concepts[0]["id"])
    check("  ...with Scene rows built from the script",
          cb.scenes.count() > 0, True)

    script_usage = CsUsageLog.query.filter_by(
        project_id=brief_project_id, service="script").all()
    check("exactly one usage row for the script call", len(script_usage), 1)

# A project already bound to a template (from the WO-CS3 section above) has
# no concepts, so asking it for a script is refused rather than the sweep
# discovering the problem a step later.
r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/script")
check("a template-bound project with no concepts refuses a script job",
      r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS4: generating per-scene AI stills")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/image",
                json={"scene_id": first_scene_id})
check("generating a still for a real scene enqueues an image job", r.status_code, 200)
image_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(image_job_id)
    check("the image job completes", job.state, "complete")
    check("  ...offering two mock options", len(job.output.get("options") or []), 2)

    from modules.creative_studio.models import CsMediaAsset as CsMediaAssetModel
    rows = CsMediaAssetModel.query.filter_by(
        project_id=cs_project_id, source="openai").all()
    check("one media row per successfully generated option", len(rows), 2)
    check("  ...tagged as an image", rows[0].asset_type, "image")

    image_usage = CsUsageLog.query.filter_by(
        project_id=cs_project_id, service="image").all()
    check("exactly one usage row for the image job", len(image_usage), 1)
    check("  ...counting both options", image_usage[0].quantity, 2)
    check("  ...priced from PROVIDER_RATES", image_usage[0].estimated_cost is not None, True)

r = client.post(f"/creative-studio/api/projects/{brief_project_id}/generate/image",
                json={"scene_id": first_scene_id})
check("a scene id belonging to a different project's storyboard is refused",
      r.status_code, 400)

r = client.post(f"/creative-studio/api/projects/{no_brief_id}/generate/image",
                json={"scene_id": first_scene_id})
check("an unbound project is refused before a scene is even looked up",
      r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS4: a generation job that times out fails readably, and a retry is a new row")

with hub_app.app_context():
    from datetime import datetime, timedelta
    from modules.creative_studio.db import db as cs_db2
    stuck = cs_jobs.enqueue("storyboard", project_id=brief_project_id,
                            client_name="", created_by="Todd")
    stuck.timeout_at = datetime.utcnow() - timedelta(minutes=1)
    cs_db2.session.commit()
    stuck_id = stuck.id
cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    refreshed = CreativeJob.query.get(stuck_id)
    check("an overdue generation job is swept as failed", refreshed.state, "failed")
    check("  ...with a readable error", bool(refreshed.error), True)

r = client.post(f"/creative-studio/api/projects/{brief_project_id}/generate/storyboard")
check("retrying after a failure enqueues a fresh job", r.status_code, 200)
check("  ...a different row from the one that timed out",
      r.get_json()["job"]["id"] != stuck_id, True)

# ---------------------------------------------------------------------------
section("WO-CS4: Usage & Costs reads cs_usage_logs, grouped by provider")

r = client.get("/creative-studio/usage")
check("the usage page renders", r.status_code, 200)
check("  ...carrying the openai provider row", b"openai" in r.data, True)
check("  ...and says every rate is an estimate", b"not measured" in r.data or b"Estimated cost" in r.data, True)

with hub_app.app_context():
    from modules.creative_studio import usage as cs_usage
    all_rows = CsUsageLog.query.all()
    totals = cs_usage.totals_by_provider(all_rows)
    check("totals_by_provider groups into one row per provider",
          len({t["provider"] for t in totals}), len(totals))
    openai_total = next(t for t in totals if t["provider"] == "openai")
    check("  ...with a measured total (every rate above is in PROVIDER_RATES)",
          openai_total["estimated_cost"] is not None, True)

    row = cs_usage.record("unpriced_provider", "unpriced_service")
    check("a (provider, service) with no rate is not measured, never zero",
          row.estimated_cost, None)

# ---------------------------------------------------------------------------
section("WO-CS5: generating a full voiceover as a queued job")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/voice")
check("generating a voiceover with no voice chosen is refused", r.status_code, 400)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/voice",
                json={"voice_id": "mock-voice-1"})
check("generating a voiceover enqueues a voice job", r.status_code, 200)
check("  ...of kind voice", r.get_json()["job"]["kind"], "voice")
voice_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(voice_job_id)
    check("the voice job completes -- ElevenLabs' own call is synchronous, "
          "so there is nothing to poll for", job.state, "complete")

    from modules.creative_studio.models import CsMediaAsset as CsMediaAssetModel2
    rows = CsMediaAssetModel2.query.filter_by(project_id=cs_project_id, source="elevenlabs").all()
    check("one voiceover media row is filed", len(rows), 1)
    check("  ...tagged as a voiceover", rows[0].asset_type, "voiceover")

    voice_usage_rows = CsUsageLog.query.filter_by(project_id=cs_project_id, service="voice").all()
    check("exactly one usage row for the voice call", len(voice_usage_rows), 1)
    check("  ...naming elevenlabs", voice_usage_rows[0].provider, "elevenlabs")

r = client.post(f"/creative-studio/api/projects/{no_brief_id}/generate/voice",
                json={"voice_id": "mock-voice-1"})
check("an unbound project is refused before a voice job is even enqueued",
      r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS5: a HeyGen spokesperson clip, and what a mock generation reports")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/heygen",
                json={"scene_id": first_scene_id})
check("generating a spokesperson clip with no presenter chosen is refused",
      r.status_code, 400)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/heygen",
                json={"scene_id": first_scene_id, "avatar_id": "mock-avatar-1"})
check("generating a spokesperson clip enqueues a heygen job", r.status_code, 200)
check("  ...of kind heygen", r.get_json()["job"]["kind"], "heygen")
heygen_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(heygen_job_id)
    check("with no HEYGEN_API key set, the mock generation fails readably "
          "rather than filing an empty clip", job.state, "failed")
    check("  ...with a readable error rather than a bare provider code",
          bool(job.error) and len(job.error) > 10, True)

    from modules.creative_studio.models import CsMediaAsset as CsMediaAssetModel3
    filed = CsMediaAssetModel3.query.filter_by(project_id=cs_project_id, source="heygen").all()
    check("  ...and nothing was filed to the Media Library for it", len(filed), 0)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/generate/heygen",
                json={"scene_id": 999999, "avatar_id": "mock-avatar-1"})
check("a scene id not on this project's storyboard is refused before "
      "a job is even enqueued", r.status_code, 400)

# A scene with real narration on it (the script-generated project from the
# WO-CS4 section above), so the "no HEYGEN_API key" branch is what actually
# fires rather than being masked by the scene having nothing to read.
with hub_app.app_context():
    narrated_scene = CbScene.query.filter_by(project_id=brief_cb_id).order_by(CbScene.order_index).first()
    check("the script-generated project has a scene carrying narration",
          bool(narrated_scene.narration), True)
    narrated_scene_id = narrated_scene.id

r = client.post(f"/creative-studio/api/projects/{brief_project_id}/generate/heygen",
                json={"scene_id": narrated_scene_id, "avatar_id": "mock-avatar-1"})
check("generating a spokesperson clip for a narrated scene enqueues a job",
      r.status_code, 200)
narrated_heygen_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(narrated_heygen_job_id)
    check("with real narration and no HEYGEN_API key, it is the mock branch "
          "that fails it", job.state, "failed")
    check("  ...naming the missing key", "HEYGEN_API" in (job.error or ""), True)

# ---------------------------------------------------------------------------
section("WO-CS5: a render request with failing QC is refused")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/render", json={"format": "16:9"})
check("an untouched storyboard with no media fails QC and the render is refused",
      r.status_code, 409)
check("  ...carrying the qc_results", "qc_results" in r.get_json(), True)

with hub_app.app_context():
    check("nothing was enqueued for the refused render",
          CreativeJob.query.filter_by(kind="render", project_id=cs_project_id).count(), 0)

# ---------------------------------------------------------------------------
section("WO-CS5: two consecutive renders produce V1 and V2, and V1 is unchanged")

from modules.commercial_builder.services import creatomate_service as _creatomate  # noqa: E402
from modules.commercial_builder.services import qc_service as _qc_service  # noqa: E402
from modules.creative_studio.models import CsProjectVersion  # noqa: E402

_orig_submit = _creatomate.submit_render
_orig_check = _creatomate.check_render
_orig_run_qc = _qc_service.run_qc

_passing_qc = {"_all_passed": True, "scene_assets": {"passed": True},
              "media_integrity": {"passed": True}}
_qc_service.run_qc = lambda *a, **k: dict(_passing_qc)
_creatomate.submit_render = lambda source: {
    "id": "rend_cs5", "status": "rendering", "url": None, "error": None}
_creatomate.check_render = lambda rid: {
    "id": rid, "status": "succeeded", "url": "https://cdn.example.test/out.mp4", "error": None}

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/render", json={"format": "16:9"})
check("a render request with passing QC is enqueued", r.status_code, 200)
render_job_1 = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)   # queued -> rendering: builds the source, submits
with hub_app.app_context():
    job = CreativeJob.query.get(render_job_1)
    check("the render job is rendering after its first tick", job.state, "rendering")
    check("  ...attempts counted once, at submission", job.attempts, 1)

cs_jobs.job_sweep(hub_app)   # rendering -> uploading -> complete: polls, uploads, versions
with hub_app.app_context():
    job = CreativeJob.query.get(render_job_1)
    check("the render job completes on its second tick", job.state, "complete")
    check("  ...attempts still just one -- a poll is not a fresh attempt", job.attempts, 1)
    check("  ...producing version 1", job.output.get("version"), 1)

    versions = (CsProjectVersion.query.filter_by(project_id=cs_project_id)
               .order_by(CsProjectVersion.version).all())
    check("exactly one version exists", len(versions), 1)
    v1_url = versions[0].render_url
    check("  ...carrying a render_url", bool(v1_url), True)
    v1_id = versions[0].id

    from modules.creative_studio.models import CsProject as CsProjectModel5
    proj = CsProjectModel5.query.get(cs_project_id)
    check("the project moves to Internal Review", proj.status, "Internal Review")

    render_usage_rows = CsUsageLog.query.filter_by(project_id=cs_project_id, service="render").all()
    check("exactly one usage row for this render", len(render_usage_rows), 1)
    check("  ...naming creatomate", render_usage_rows[0].provider, "creatomate")

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/render", json={"format": "16:9"})
check("a second render request is also enqueued", r.status_code, 200)
render_job_2 = r.get_json()["job"]["id"]
cs_jobs.job_sweep(hub_app)
cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    job = CreativeJob.query.get(render_job_2)
    check("a second render also completes", job.state, "complete")
    check("  ...producing version 2", job.output.get("version"), 2)

    versions = (CsProjectVersion.query.filter_by(project_id=cs_project_id)
               .order_by(CsProjectVersion.version).all())
    check("now there are two versions", len(versions), 2)
    check("  ...V1's own row is unchanged", versions[0].id, v1_id)
    check("  ...V1's render_url is unchanged", versions[0].render_url, v1_url)
    check("  ...V1 and V2 are different rows", versions[0].id != versions[1].id, True)

_creatomate.submit_render = _orig_submit
_creatomate.check_render = _orig_check
_qc_service.run_qc = _orig_run_qc

# ---------------------------------------------------------------------------
section("WO-CS5: a render that never completes ends failed at timeout_at, with no version row")

_qc_service.run_qc = lambda *a, **k: dict(_passing_qc)
_creatomate.submit_render = lambda source: {
    "id": "rend_stuck", "status": "rendering", "url": None, "error": None}
_creatomate.check_render = lambda rid: {
    "id": rid, "status": "rendering", "url": None, "error": None}

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/render", json={"format": "16:9"})
stuck_render_job_id = r.get_json()["job"]["id"]
cs_jobs.job_sweep(hub_app)   # queued -> rendering: submits, then Creatomate never resolves

with hub_app.app_context():
    from datetime import datetime as _dt5, timedelta as _td5
    from modules.creative_studio.db import db as _cs_db5
    stuck_job = CreativeJob.query.get(stuck_render_job_id)
    check("the stuck render is rendering after its first tick", stuck_job.state, "rendering")
    stuck_job.timeout_at = _dt5.utcnow() - _td5(minutes=1)
    _cs_db5.session.commit()

cs_jobs.job_sweep(hub_app)   # the overdue check fails it -- never polled again
with hub_app.app_context():
    stuck_job = CreativeJob.query.get(stuck_render_job_id)
    check("an overdue render is swept as failed", stuck_job.state, "failed")
    check("  ...with a readable error", "did not finish in time" in (stuck_job.error or ""), True)

    versions = CsProjectVersion.query.filter_by(project_id=cs_project_id).all()
    check("no version row carries the stuck render's provider id",
          any(v.creatomate_render_id == "rend_stuck" for v in versions), False)

_creatomate.submit_render = _orig_submit
_creatomate.check_render = _orig_check
_qc_service.run_qc = _orig_run_qc

# ---------------------------------------------------------------------------
section("WO-CS5: the QR Amazon caution copy is still present on the CTA control")

amazon_result = _qc_service._check_publisher_rules(
    {"brief": {"publishers": ["amazon"]}, "cta": {"qr_enabled": True}})
check("Amazon + a QR code enabled fails the publisher-rules check",
      amazon_result["passed"], False)
check("  ...naming what Amazon's specs actually say",
      "does not support QR codes" in amazon_result["message"], True)
check("  ...and what to do about it",
      "Turn the code off" in amazon_result["message"], True)

# ---------------------------------------------------------------------------
section("WO-CS5: approving a version files THAT version to Client 360")

with hub_app.app_context():
    from modules.creative_studio.models import CsProject as CsProjectModel6
    from modules.creative_studio.db import db as cs_db4
    from modules.creative_studio import binder as cs_binder3

    filed_project = CsProjectModel6(name="Fall HVAC spot", creative_type="video_commercial",
                                    client_name="Acme Plumbing", status="Draft")
    cs_db4.session.add(filed_project)
    cs_db4.session.commit()
    bind_result = cs_binder3.bind_for_generation(filed_project)
    check("binding a real-client project for generation succeeds", bind_result.get("ok"), True)
    filed_project_id = filed_project.id

    v1 = CsProjectVersion(project_id=filed_project_id, version=1,
                          render_url="https://cdn.example.test/v1.mp4", created_by="Todd")
    cs_db4.session.add(v1)
    cs_db4.session.commit()

r = client.post(f"/creative-studio/api/projects/{filed_project_id}/versions/1/approve")
check("approving an existing version succeeds", r.status_code, 200)
check("  ...and the project status moves to Approved",
      r.get_json()["project"]["status"], "Approved")

r = client.post(f"/creative-studio/api/projects/{filed_project_id}/versions/99/approve")
check("approving a version number that does not exist 404s", r.status_code, 404)

with hub_app.app_context():
    unchanged = CsProjectVersion.query.filter_by(project_id=filed_project_id, version=1).first()
    check("the version row itself is never mutated by approving it",
          unchanged.render_url, "https://cdn.example.test/v1.mp4")

    from hub import client_brand as cb_work
    log = cb_work.work_log("Acme Plumbing")
    matches = [it for it in log["items"]
              if "Fall HVAC spot" in it["detail"] and "V1" in it["detail"]]
    check("the approval reaches Acme Plumbing's Client 360 work log", len(matches) >= 1, True)
    check("  ...filed under Creative Studio", matches[0]["kind"] if matches else "", "Creative Studio")

# ---------------------------------------------------------------------------
section("WO-CS5: the credit meter reads a running estimated cost from cs_usage_logs")

r = client.get(f"/creative-studio/api/projects/{cs_project_id}/usage-summary")
check("the usage summary route answers", r.status_code, 200)
body = r.get_json()
check("  ...measured (every provider used above is in PROVIDER_RATES)", body["measured"], True)
check("  ...carrying a positive running cost", body["estimated_cost"] > 0, True)
check("  ...and a call count", body["calls"] > 0, True)

r = client.get("/creative-studio/api/projects/999999/usage-summary")
check("a project that does not exist 404s", r.status_code, 404)

# ---------------------------------------------------------------------------
section("WO-CS6: sending a version for approval")

r = client.post(f"/creative-studio/api/projects/{filed_project_id}/versions/1/share")
check("sending a version for approval succeeds", r.status_code, 200)
share_body = r.get_json()["share"]
check("  ...and mints a token", bool(share_body.get("token")), True)
review_token = share_body["token"]
check("  ...the url is bare /review/, not under /creative-studio/",
      share_body["url"].endswith(f"/review/{review_token}"), True)
check("  ...round 1 of 4", share_body["round_state"]["label"], "Round 1 of 4")
check("  ...delivery is skipped -- no contact email was given",
      share_body["delivery"]["state"], "skipped")

with hub_app.app_context():
    project_after_send = CsProjectModel6.query.get(filed_project_id)
    check("the project moves to Client Review on send",
          project_after_send.status, "Client Review")

    from modules.creative_studio.models import CreativeJob as CreativeJobModel6
    jobs_after_send = CreativeJobModel6.query.filter_by(project_id=filed_project_id).all()
    check("sending for approval never enqueues a render job",
          all(j.kind != "render" for j in jobs_after_send), True)

# ---------------------------------------------------------------------------
section("WO-CS6: the client's own page carries none of the Hub's chrome")

anon = Client(wsgi.application)
r = anon.get(f"/review/{review_token}")
check("the review page renders with no session at all", r.status_code, 200)
page = r.data
check("  ...no sidebar", b"s1hub-sb" in page, False)
check("  ...no hub-help.js", b"hub-help.js" in page, False)
check("  ...no feedback tab", b"s1hub-feedback" in page, False)
check("  ...noindex, so a search engine never files a client's own version",
      b'name="robots"' in page and b"noindex" in page, True)
check("  ...carries the version content",
      b"cdn.example.test/v1.mp4" in page, True)
check("  ...and the round label", b"Round 1 of 4" in page, True)
check("  ...only two outcomes are offered -- no middle 'approved with changes'",
      b"approved_with_changes" in page, False)

r = anon.get("/review/not-a-real-token-at-all")
check("a token that never existed answers a bare 404", r.status_code, 404)

# ---------------------------------------------------------------------------
section("WO-CS6: comment and decide")

r = anon.post(f"/review/{review_token}/comment", json={"text": ""})
check("an empty comment is refused", r.status_code, 400)

r = anon.post(f"/review/{review_token}/comment",
              json={"text": "The end card feels rushed", "name": "Pat Owner"})
check("a comment with text and a name is accepted", r.status_code, 200)

r = anon.post(f"/review/{review_token}/decide",
              json={"outcome": "approved_with_changes", "name": "Pat Owner",
                    "email": "pat@acmeplumbing.test"})
check("an outcome outside the two shown is refused", r.status_code, 400)

r = anon.post(f"/review/{review_token}/decide",
              json={"outcome": "changes_required", "name": "Pat Owner",
                    "email": "pat@acmeplumbing.test", "note": "Fix the end card"})
check("requesting changes is accepted", r.status_code, 200)
check("  ...and resolves to changes_required",
      r.get_json()["verdict"]["outcome"], "changes_required")

with hub_app.app_context():
    after_changes = CsProjectModel6.query.get(filed_project_id)
    check("the project status follows the decision",
          after_changes.status, "Changes Requested")

r = anon.post(f"/review/{review_token}/decide",
              json={"outcome": "approved", "name": "Pat Owner",
                    "email": "pat@acmeplumbing.test"})
check("the same reviewer (matched by email) can correct their own answer",
      r.status_code, 200)

with hub_app.app_context():
    after_approve = CsProjectModel6.query.get(filed_project_id)
    check("  ...and the project status follows the correction",
          after_approve.status, "Approved")

    from modules.creative_studio.models import CsShareDecision as CsShareDecisionModel6
    decisions_for_pat = [d for d in CsShareDecisionModel6.query.all()
                        if (d.reviewer_email or "").lower() == "pat@acmeplumbing.test"]
    check("correcting an answer replaces it rather than adding a second row",
          len(decisions_for_pat), 1)

# ---------------------------------------------------------------------------
section("WO-CS6: the round counter, and round 5")

from modules.creative_studio.models import CsShare as CsShareModel6  # noqa: E402

# Each send is scoped to this same version -- `subject_id=version.id` -- so
# sending again revokes round 1's own token (review_token), which the next
# section relies on.
last_round_state = None
for round_no in range(2, 6):
    r = client.post(f"/creative-studio/api/projects/{filed_project_id}/versions/1/share")
    check(f"round {round_no} can be sent", r.status_code, 200)
    last_round_state = r.get_json()["share"]["round_state"]

check("round 5 is offered rather than refused", last_round_state["round"], 5)
check("  ...and reads as over the cap", last_round_state["over"], True)

# ---------------------------------------------------------------------------
section("WO-CS6: revoked reads differently from never-existed")

r = anon.get(f"/review/{review_token}")
check("the round-1 token, now revoked by the later rounds, answers 410", r.status_code, 410)
check("  ...with a real page explaining why",
      b"replaced" in r.data, True)

r = anon.get("/review/still-not-a-real-token")
check("a token that never existed still answers a bare 404", r.status_code, 404)

# ---------------------------------------------------------------------------
section("WO-CS6: revoking a share directly")

with hub_app.app_context():
    live_share = (CsShareModel6.query
                  .filter_by(project_id=filed_project_id, revoked=False)
                  .order_by(CsShareModel6.id.desc()).first())
    live_share_id = live_share.id

r = client.post(f"/creative-studio/api/projects/{filed_project_id}/shares/{live_share_id}/revoke")
check("revoking a share succeeds", r.status_code, 200)
check("  ...and reports it revoked", r.get_json()["share"]["revoked"], True)

# ---------------------------------------------------------------------------
section("WO-CS6: the approvals and usage pages carry real content now")

r = client.get("/creative-studio/approvals")
check("the approvals page renders", r.status_code, 200)
check("  ...listing what is out with clients rather than the old placeholder",
      b"Out with clients" in r.data or b"Waiting on us" in r.data, True)

r = client.get("/creative-studio/usage")
check("the usage page renders", r.status_code, 200)
for tile in (b"Total generations", b"Videos rendered", b"Images generated",
            b"Voiceovers generated", b"Estimated API cost"):
    check(f"  ...carries the {tile.decode()} tile", tile in r.data, True)

r = client.get("/creative-studio/usage?period=today")
check("the usage page still renders filtered by period", r.status_code, 200)

# ---------------------------------------------------------------------------
section("WO-CS6: the shared rate limiter refuses the client's own page too")

from modules.creative_studio import review_routes as cs_review_routes  # noqa: E402

_orig_rate_limited = cs_review_routes._hub_leads.rate_limited
cs_review_routes._hub_leads.rate_limited = lambda *a, **k: True
try:
    r = anon.get(f"/review/{review_token}")
    check("a rate-limited GET answers 429", r.status_code, 429)
    r = anon.post(f"/review/{review_token}/comment", json={"text": "x", "name": "x"})
    check("a rate-limited comment POST answers 429", r.status_code, 429)
finally:
    cs_review_routes._hub_leads.rate_limited = _orig_rate_limited

# ---------------------------------------------------------------------------
section("WO-CS7: elements_for() re-flows into each aspect's own safe zone")

hook_16x9 = cs_layouts.elements_for("hook_fullbleed", "16:9",
                                    {"headline": {"value": "Beat the heat"}})
hook_9x16 = cs_layouts.elements_for("hook_fullbleed", "9:16",
                                    {"headline": {"value": "Beat the heat"}})
check("a headline draws at 16:9", any(e.get("text") == "Beat the heat" for e in hook_16x9), True)
check("  ...and at 9:16, at a different position", any(e.get("text") == "Beat the heat" for e in hook_9x16), True)
check("  ...the two are not the same composition -- re-flowed to the taller frame's own width",
      [e.get("width") for e in hook_16x9] != [e.get("width") for e in hook_9x16], True)
check("a layer with nothing typed in draws nothing",
      cs_layouts.elements_for("hook_fullbleed", "16:9", {}), [
          e for e in cs_layouts.elements_for("hook_fullbleed", "16:9", {})
          if e.get("type") != "text"])

check("every real layout's every supported aspect is clean against its own safe zone", all(
    not cs_layouts.check_safe_zone(key, aspect, {ln: {"value": "x" * 6} for ln in cs_layouts.layers_for(key)},
                                   logo_url="https://cdn.example.test/logo.png",
                                   phone="555-1234", website="example.test")
    for key in cs_layouts.LAYOUTS for aspect in cs_layouts.LAYOUTS[key]["aspect_ratios"]
), True)

check("an unrecognized aspect is reported by name, not passed silently",
      bool(cs_layouts.check_safe_zone("hook_fullbleed", "21:9", {"headline": {"value": "x"}})), True)

# A deliberately oversized headline pushed past the 9:16 side margin --
# proving the checker actually fires rather than only ever returning [].
_orig_variant_for = cs_layouts.variant_for
cs_layouts.variant_for = lambda key, aspect: (
    {"headline": {"x": "50%", "y": "50%", "width": "90%", "x_anchor": "50%"}}
    if (key, aspect) == ("hook_fullbleed", "9:16") else _orig_variant_for(key, aspect))
try:
    forced = cs_layouts.check_safe_zone("hook_fullbleed", "9:16", {"headline": {"value": "x"}})
    check("a headline placed inside 14/35 still crosses a too-wide side margin",
          any("side margin" in f["reason"] for f in forced), True)
finally:
    cs_layouts.variant_for = _orig_variant_for

check("a structural split-frame panel is never itself a safe-zone finding",
      all(f["layer"] != "shape" for f in
          cs_layouts.check_safe_zone("problem_split", "9:16", {"headline": {"value": "x"}})), True)

# ---------------------------------------------------------------------------
section("WO-CS7: binder.bind_variation copies the parent's scenes, reframed")

with hub_app.app_context():
    from modules.commercial_builder.models import CommercialProject as CbProject7
    from modules.commercial_builder.models import Scene as CbScene7
    from modules.creative_studio.models import CsProject as CsProjectModel7
    from modules.creative_studio.db import db as cs_db7

    parent = CsProjectModel7.query.get(cs_project_id)
    parent_cb = CbProject7.query.get(parent.cb_project_id)
    parent_scene_count = parent_cb.scenes.count()
    parent_version_count_before = CsProjectVersion.query.filter_by(project_id=cs_project_id).count()

    variant_row = CsProjectModel7(
        name="HVAC spring tune-up — 9:16", creative_type=parent.creative_type,
        client_name=parent.client_name, template_id=parent.template_id,
        template_version=parent.template_version, duration=parent.duration,
        aspect_ratio="9:16", status="Draft",
        parent_project_id=parent.id, variation_kind="aspect", created_by="Todd")
    cs_db7.session.add(variant_row)
    cs_db7.session.commit()

    result = cs_binder.bind_variation(variant_row)
    check("bind_variation succeeds against a real parent storyboard", result.get("ok"), True)

    variant_row = CsProjectModel7.query.get(variant_row.id)
    check("  ...and the variation remembers its own storyboard",
          bool(variant_row.cb_project_id), True)
    check("  ...a different storyboard than the parent's",
          variant_row.cb_project_id != parent.cb_project_id, True)

    variant_cb = CbProject7.query.get(variant_row.cb_project_id)
    check("the copy carries the same number of scenes as the parent",
          variant_cb.scenes.count(), parent_scene_count)
    variant_scenes = variant_cb.scenes.order_by(CbScene7.order_index).all()
    check("  ...every scene copied a text_overlay computed for 9:16",
          all("text_overlay" in (s.asset_meta or {}) for s in variant_scenes), True)

    check("bind_variation is idempotent -- already-bound is a no-op",
          cs_binder.bind_variation(variant_row).get("cb_project_id"), variant_row.cb_project_id)
    variant_cb_project_id = variant_row.cb_project_id

    check("the parent's own version rows are untouched by creating a variation",
          CsProjectVersion.query.filter_by(project_id=cs_project_id).count(),
          parent_version_count_before)

    orphan = CsProjectModel7(name="No parent", creative_type="video_commercial", status="Draft")
    cs_db7.session.add(orphan)
    cs_db7.session.commit()
    orphan_result = cs_binder.bind_variation(orphan)
    check("a variation with no parent_project_id is refused rather than guessed at",
          orphan_result.get("ok"), False)
    orphan_id = orphan.id

# ---------------------------------------------------------------------------
section("WO-CS7: a still preview never plays audio")

with hub_app.app_context():
    from modules.commercial_builder.models import Scene as CbScene7b
    from modules.commercial_builder.services import creatomate_service as cs_cta

    variant_cb2 = CbProject7.query.get(variant_cb_project_id)
    scenes7 = [s.to_dict() for s in variant_cb2.scenes.order_by(CbScene7b.order_index).all()]
    still_source = cs_cta.build_source(variant_cb2.to_dict(include_scenes=False), scenes7,
                                       "9:16", still=True)
    check("a still render asks for a jpg", still_source.get("output_format"), "jpg")
    check("  ...and carries no audio elements",
          any(e.get("type") == "audio" for e in still_source.get("elements", [])), False)

    video_source = cs_cta.build_source(variant_cb2.to_dict(include_scenes=False), scenes7, "9:16")
    check("the ordinary (non-still) render is unaffected -- no output_format override",
          video_source.get("output_format") in (None, "mp4"), True)

# ---------------------------------------------------------------------------
section("WO-CS7: the Create Variations API")

_orig_cs7_submit = cs_cta.submit_render
_orig_cs7_check = cs_cta.check_render
cs_cta.submit_render = lambda source: {
    "id": "prev_cs7", "status": "succeeded",
    "url": "https://cdn.example.test/preview.jpg", "error": None}
cs_cta.check_render = lambda rid: {
    "id": rid, "status": "succeeded", "url": "https://cdn.example.test/preview.jpg", "error": None}

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/versions/1/variations",
                json={"aspects": ["1:1", "4:5"], "link_image": True})
check("creating variations from a real version succeeds", r.status_code, 200)
body = r.get_json()
check("  ...three rows created: two aspects and the link image", len(body["created"]), 3)
check("  ...none refused (this layout is clean at every aspect)", body["refused"], [])

created_kinds = sorted((c["project"]["variation_kind"], c["project"]["aspect_ratio"])
                      for c in body["created"])
check("  ...one aspect variation each and one link image",
      created_kinds, [("aspect", "1:1"), ("aspect", "4:5"), ("link_image", "1200x628")])

with hub_app.app_context():
    for c in body["created"]:
        row = CsProjectModel7.query.get(c["project"]["id"])
        check(f"  ...variation {c['project']['aspect_ratio']} carries parent_project_id",
              row.parent_project_id, cs_project_id)

    check("the parent's own version rows are still untouched after creating variations",
          CsProjectVersion.query.filter_by(project_id=cs_project_id).count(),
          parent_version_count_before)

r = client.get(f"/creative-studio/api/projects/{cs_project_id}/variations")
check("listing variations succeeds", r.status_code, 200)
check("  ...returns at least the three just created",
      len(r.get_json()["variations"]) >= 3, True)

# Advance every "variant" job the create call queued -- one tick each is
# enough: the mocked submit_render already answers succeeded+url.
for _ in body["created"]:
    cs_jobs.job_sweep(hub_app)
with hub_app.app_context():
    for c in body["created"]:
        job = CreativeJob.query.get(c["job"]["id"])
        check(f"the variant job for {c['project']['aspect_ratio']} completes",
              job.state, "complete")
        row = CsProjectModel7.query.get(c["project"]["id"])
        check(f"  ...and the project carries a preview_url", bool(row.preview_url), True)

link_image_entry = next(c for c in body["created"] if c["project"]["variation_kind"] == "link_image")
with hub_app.app_context():
    link_cb = CbProject7.query.get(CsProjectModel7.query.get(link_image_entry["project"]["id"]).cb_project_id)
    check("the link image's storyboard is a single scene (the end card alone)",
          link_cb.scenes.count(), 1)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/versions/1/variations",
                json={"aspects": [], "link_image": False})
check("asking for nothing is refused rather than silently doing nothing", r.status_code, 400)

r = client.post(f"/creative-studio/api/projects/{cs_project_id}/versions/999/variations",
                json={"aspects": ["1:1"]})
check("a version number that does not exist 404s", r.status_code, 404)

with hub_app.app_context():
    no_storyboard = CsProjectModel7(name="Never opened", creative_type="video_commercial", status="Draft")
    cs_db7.session.add(no_storyboard)
    cs_db7.session.commit()
    v_no_sb = CsProjectVersion(project_id=no_storyboard.id, version=1,
                               render_url="https://cdn.example.test/x.mp4")
    cs_db7.session.add(v_no_sb)
    cs_db7.session.commit()
    no_storyboard_id = no_storyboard.id

r = client.post(f"/creative-studio/api/projects/{no_storyboard_id}/versions/1/variations",
                json={"aspects": ["1:1"]})
check("a project never opened in the Storyboard Editor refuses to create variations",
      r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS7: a 9:16 variation with a genuine safe-zone violation is refused, never built")

from modules.creative_studio import api as cs_api  # noqa: E402

_orig_findings = cs_api._safe_zone_findings
cs_api._safe_zone_findings = lambda project, aspect: (
    [{"layer": "headline", "reason": "crosses the 6% side margin"}] if aspect == "9:16" else [])
try:
    with hub_app.app_context():
        variation_count_before = CsProjectModel7.query.filter_by(parent_project_id=cs_project_id).count()
    r = client.post(f"/creative-studio/api/projects/{cs_project_id}/versions/1/variations",
                    json={"aspects": ["9:16", "1:1"]})
    check("a mixed request still succeeds overall", r.status_code, 200)
    body9 = r.get_json()
    check("  ...the 9:16 aspect is refused", [x["aspect"] for x in body9["refused"]], ["9:16"])
    check("  ...carrying the safe-zone finding", body9["refused"][0]["findings"][0]["reason"],
          "crosses the 6% side margin")
    check("  ...while 1:1 still builds", [c["project"]["aspect_ratio"] for c in body9["created"]], ["1:1"])
    with hub_app.app_context():
        check("no cs_projects row was created for the refused 9:16 variation",
              CsProjectModel7.query.filter_by(parent_project_id=cs_project_id).count(),
              variation_count_before + 1)
finally:
    cs_api._safe_zone_findings = _orig_findings

cs_cta.submit_render = _orig_cs7_submit
cs_cta.check_render = _orig_cs7_check

# ---------------------------------------------------------------------------
section("WO-CS7: the project detail page offers Create Variations only once a version exists")

r = client.get(f"/creative-studio/projects/{cs_project_id}")
check("the project page renders", r.status_code, 200)
check("  ...carrying the Create Variations panel", b"Create Variations" in r.data, True)
check("  ...with all four aspect checkboxes", b'value="9:16"' in r.data and b'value="4:5"' in r.data, True)

r = client.get(f"/creative-studio/projects/{orphan_id}")
check("a project with no rendered version yet gets no Create Variations panel",
      b"Create Variations" not in r.data, True)

# ---------------------------------------------------------------------------
section("WO-CS8: campaign_spec -- status derived, never stored")

from modules.creative_studio import campaign_spec  # noqa: E402

check("no assets is Empty, not Draft", campaign_spec.status_of([]), "Empty")
check("one Draft asset reads Draft", campaign_spec.status_of(["Draft"]), "Draft")
check("Draft beats Approved -- the campaign is only as far along as its "
      "least-advanced asset", campaign_spec.status_of(["Draft", "Approved"]), "Draft")
check("all Approved reads Approved", campaign_spec.status_of(["Approved", "Approved"]), "Approved")
check("Changes Requested outranks everything", campaign_spec.status_of(
    ["Client Review", "Changes Requested", "Approved"]), "Changes Requested")

check("an asset with no offer typed of its own has not departed",
      campaign_spec.asset_differs({}, "Campaign offer", "Campaign CTA"),
      {"offer": False, "cta": False})
check("an asset whose own offer matches the campaign's has not departed",
      campaign_spec.asset_differs({"offer": "Campaign offer"}, "Campaign offer", ""),
      {"offer": False, "cta": False})
check("an asset whose own offer differs from the campaign's is flagged",
      campaign_spec.asset_differs({"offer": "Something else"}, "Campaign offer", ""),
      {"offer": True, "cta": False})

check("the render estimate is one creatomate.render unit per asset",
      campaign_spec.render_estimate(4), 2.0)
check("a small batch needs no confirmation", campaign_spec.needs_confirmation(2.0), False)
check("a batch over the threshold needs confirmation", campaign_spec.needs_confirmation(30.0), True)

# ---------------------------------------------------------------------------
section("WO-CS8: creating a campaign -- nothing invented against the client book")

r = client.post("/creative-studio/api/campaigns",
                json={"name": "Fall Push", "client_name": "Some Business Nobody Has Heard Of"})
check("a typed client that resolves to nobody is refused", r.status_code, 400)

r = client.post("/creative-studio/api/campaigns", json={"name": "Generic Campaign"})
check("a blank client (generic Smart 1 campaign) is allowed", r.status_code, 200)
check("  ...and files with no client name", r.get_json()["campaign"]["client_name"], "")

r = client.post("/creative-studio/api/campaigns", json={"name": ""})
check("an unnamed campaign is refused", r.status_code, 400)

# ---------------------------------------------------------------------------
section("WO-CS8: resolving a seed template by industry/duration/aspect/type")

with hub_app.app_context():
    exact_tmpl, exact = cs_binder.resolve_seed_template("hvac", 30, "16:9", "video_commercial")
    check("an exact match returns the exact template", exact_tmpl.id if exact_tmpl else None, "hvac-30")
    check("  ...and says so", exact, True)

    general_tmpl, exact2 = cs_binder.resolve_seed_template("restaurant", 30, "9:16",
                                                           "social_video")
    check("no restaurant template at 9:16 falls back to the same shape in general",
          general_tmpl.id if general_tmpl else None, "social-ugc-vertical-30")
    check("  ...and says it was not an exact match", exact2, False)

    none_tmpl, _exact3 = cs_binder.resolve_seed_template("hvac", 999, "16:9", "video_commercial")
    check("no template fits a made-up duration", none_tmpl, None)

# ---------------------------------------------------------------------------
section("WO-CS8: a real campaign, its assets, and the differs chip")

with hub_app.app_context():
    from modules.creative_studio.models import CsCampaign as CsCampaignModel8
    from modules.creative_studio.models import CsCampaignAsset as CsCampaignAssetModel8
    from modules.creative_studio.db import db as cs_db8

    campaign = CsCampaignModel8(client_name="Acme Plumbing", name="Spring Push",
                                offer="$79 seasonal tune-up", cta="Call today",
                                created_by="Todd")
    cs_db8.session.add(campaign)
    cs_db8.session.commit()
    campaign_id = campaign.id

asset1, err1 = None, None
with hub_app.app_context():
    campaign = CsCampaignModel8.query.get(campaign_id)
    asset1, err1 = cs_binder.create_campaign_asset(
        campaign, industry="hvac", duration=30, aspect_ratio="16:9",
        creative_type="video_commercial", channel="ctv", created_by="Todd")
    check("adding an asset with a real seed template succeeds", err1, "")
    check("  ...and joins the campaign", asset1.campaign_id, campaign_id)
    asset1_project_id = asset1.project_id

    with_no_template, err_no_tmpl = cs_binder.create_campaign_asset(
        campaign, industry="hvac", duration=999, aspect_ratio="16:9",
        creative_type="video_commercial", channel="social", created_by="Todd")
    check("an asset with no matching seed template is refused",
          with_no_template, None)
    check("  ...with a readable reason", bool(err_no_tmpl), True)

r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/assets",
                json={"industry": "hvac", "duration": 15, "aspect_ratio": "16:9",
                     "creative_type": "video_commercial", "channel": "social"})
check("adding a second asset through the API succeeds", r.status_code, 200)
asset2_project_id = r.get_json()["asset"]["project_id"]

with hub_app.app_context():
    p1 = CsProjectModel7.query.get(asset1_project_id)
    p1.brief = {"offer": "A different offer entirely", "cta": campaign.cta}
    cs_db8.session.commit()

r = client.get(f"/creative-studio/api/campaigns/{campaign_id}")
check("reading the campaign back succeeds", r.status_code, 200)
row = r.get_json()["campaign"]
check("  ...carrying both assets", len(row["assets"]), 2)
differing = next(a for a in row["assets"] if a["project_id"] == asset1_project_id)
check("  ...the edited asset's offer chip fires", differing["differs"]["offer"], True)
untouched = next(a for a in row["assets"] if a["project_id"] == asset2_project_id)
check("  ...the untouched asset's does not", untouched["differs"]["offer"], False)
check("  ...status is Empty-to-Draft, i.e. Draft (every asset still a fresh Draft)",
      row["status"], "Draft")

r = client.get(f"/creative-studio/campaigns/{campaign_id}")
check("the campaign detail page renders", r.status_code, 200)
check("  ...naming the campaign", b"Spring Push" in r.data, True)
check("  ...with the offer-differs chip visible", b"offer differs" in r.data, True)

r = client.get("/creative-studio/campaigns")
check("the campaigns list page renders", r.status_code, 200)
check("  ...listing the campaign", b"Spring Push" in r.data, True)

# ---------------------------------------------------------------------------
section("WO-CS8: generate all drafts -- one campaign-level call, every asset derived")

from modules.creative_studio import campaign_generation  # noqa: E402

_cs8_calls = []


def _fake_chat_json(messages, *, module, purpose, **kw):
    _cs8_calls.append((module, purpose))
    return {"headline": "Beat the Ohio heat", "subheadline": "Local and licensed",
            "body": "Same-day service", "offer": "$79 seasonal tune-up", "cta": "Call today"}


from hub import ai as _hub_ai  # noqa: E402
_orig_chat_json = _hub_ai.chat_json
_hub_ai.chat_json = _fake_chat_json

r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/generate-all")
check("generate-all enqueues a job", r.status_code, 200)
gen_job_id = r.get_json()["job"]["id"]

cs_jobs.job_sweep(hub_app)

with hub_app.app_context():
    job = CreativeJob.query.get(gen_job_id)
    check("the campaign_draft job completes in one tick", job.state, "complete")
    check("  ...exactly one OpenAI call for the whole campaign", len(_cs8_calls), 1)
    check("  ...filed under this module and purpose",
          _cs8_calls[0], ("creative_studio", "campaign_draft"))

    campaign = CsCampaignModel8.query.get(campaign_id)
    check("the campaign's own brief is written", campaign.brief.get("headline"),
          "Beat the Ohio heat")

    p1 = CsProjectModel7.query.get(asset1_project_id)
    check("asset 1's own already-typed offer is kept -- a rep's answer beats a derived one",
          p1.brief.get("offer"), "A different offer entirely")
    check("  ...but its empty headline is filled from the campaign brief",
          p1.brief.get("headline"), "Beat the Ohio heat")
    check("  ...and its storyboard was auto-built", bool(p1.cb_project_id), True)

    p2 = CsProjectModel7.query.get(asset2_project_id)
    check("asset 2 had nothing typed, so its whole brief is derived",
          p2.brief.get("offer"), "$79 seasonal tune-up")
    check("  ...and its storyboard was auto-built too", bool(p2.cb_project_id), True)

_hub_ai.chat_json = _orig_chat_json

# ---------------------------------------------------------------------------
section("WO-CS8: batch render -- shared batch_id, one asset failing never cancels the rest")

from modules.commercial_builder.services import creatomate_service as _cs8_cta
from modules.commercial_builder.services import qc_service as _cs8_qc

_orig_run_qc8 = _cs8_qc.run_qc
_orig_submit8 = _cs8_cta.submit_render
_orig_check8 = _cs8_cta.check_render
_cs8_qc.run_qc = lambda *a, **k: {"_all_passed": True}
_cs8_cta.submit_render = lambda source: {
    "id": "rend_cs8", "status": "rendering", "url": None, "error": None}
_cs8_cta.check_render = lambda rid: {
    "id": rid, "status": "succeeded", "url": "https://cdn.example.test/cs8.mp4", "error": None}

with hub_app.app_context():
    unopened = CsProjectModel7(name="Never opened for batch", creative_type="video_commercial",
                               client_name="Acme Plumbing", status="Draft")
    cs_db8.session.add(unopened)
    cs_db8.session.commit()
    unopened_id = unopened.id
    unopened_asset = CsCampaignAssetModel8(campaign_id=campaign_id, project_id=unopened_id,
                                           channel="ott")
    cs_db8.session.add(unopened_asset)
    cs_db8.session.commit()

r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/render",
                json={"project_ids": [asset1_project_id, asset2_project_id, unopened_id]})
check("a small batch (below the confirm threshold) renders without confirmation",
      r.status_code, 200)
batch_body = r.get_json()
check("  ...two of three assets queued", len(batch_body["rendered"]), 2)
check("  ...the unopened one refused, not silently dropped", len(batch_body["refused"]), 1)
check("  ...naming the unopened project", batch_body["refused"][0]["project_id"], unopened_id)
check("  ...every queued job shares one batch_id",
      len({j["job"]["batch_id"] for j in batch_body["rendered"]}), 1)
batch_id = batch_body["batch_id"]

r = client.get(f"/creative-studio/api/campaigns/{campaign_id}/batch/{batch_id}")
check("batch status answers before any tick", r.status_code, 200)
check("  ...none done yet", r.get_json()["done"], 0)

for _ in batch_body["rendered"]:
    cs_jobs.job_sweep(hub_app)
    cs_jobs.job_sweep(hub_app)

r = client.get(f"/creative-studio/api/campaigns/{campaign_id}/batch/{batch_id}")
check("both queued jobs complete", r.get_json()["done"], 2)

r = client.get(f"/creative-studio/api/campaigns/{campaign_id}/batch/not-a-real-batch")
check("a batch id nothing recognizes 404s", r.status_code, 404)

# A batch above the threshold needs the campaign's own name typed back.
os.environ["CS_BATCH_CONFIRM_USD"] = "0.10"
r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/render",
                json={"project_ids": [asset1_project_id]})
check("above the (lowered) threshold, an unconfirmed batch is refused", r.status_code, 409)
check("  ...naming the reason", r.get_json()["error"], "confirm_required")
r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/render",
                json={"project_ids": [asset1_project_id], "confirm": "not the campaign name"})
check("a wrong typed confirmation is still refused", r.status_code, 409)
r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/render",
                json={"project_ids": [asset1_project_id], "confirm": "Spring Push"})
check("typing the campaign's own name confirms it", r.status_code, 200)
os.environ["CS_BATCH_CONFIRM_USD"] = "25"

_cs8_qc.run_qc = _orig_run_qc8
_cs8_cta.submit_render = _orig_submit8
_cs8_cta.check_render = _orig_check8

# ---------------------------------------------------------------------------
section("WO-CS8: send campaign for approval -- per-asset decisions, one round counter")

r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/share")
check("sending the campaign for approval succeeds", r.status_code, 200)
campaign_share = r.get_json()["share"]
check("  ...kind is campaign", campaign_share["kind"], "campaign")
check("  ...round 1", campaign_share["round"], 1)
campaign_token = campaign_share["token"]

with hub_app.app_context():
    p1 = CsProjectModel7.query.get(asset1_project_id)
    p2 = CsProjectModel7.query.get(asset2_project_id)
    check("every asset moves to Client Review", (p1.status, p2.status),
          ("Client Review", "Client Review"))

r = anon.get(f"/review/{campaign_token}")
check("the client campaign review page renders with no session at all", r.status_code, 200)
check("  ...no sidebar", b"s1hub-sidebar" not in r.data and b"hub-sidebar" not in r.data, True)
check("  ...naming the first asset", b"Spring Push \xe2\x80\x94 Connected TV" in r.data, True)
check("  ...and the second, independently", b"Spring Push \xe2\x80\x94 Social" in r.data, True)
check("  ...carries the campaign name", b"Spring Push" in r.data, True)

r = anon.post(f"/review/{campaign_token}/decide",
              json={"outcome": "approved", "name": "Pat", "email": "pat@acmeplumbing.test"})
check("deciding with no asset_project_id is refused -- a campaign decision "
      "must say which asset it is about", r.status_code, 400)

r = anon.post(f"/review/{campaign_token}/decide",
              json={"outcome": "approved", "name": "Pat", "email": "pat@acmeplumbing.test",
                   "asset_project_id": 999999})
check("an asset_project_id not on this campaign is refused", r.status_code, 400)

r = anon.post(f"/review/{campaign_token}/decide",
              json={"outcome": "approved", "name": "Pat", "email": "pat@acmeplumbing.test",
                   "asset_project_id": asset1_project_id})
check("approving asset 1 succeeds", r.status_code, 200)

r = anon.post(f"/review/{campaign_token}/decide",
              json={"outcome": "changes_required", "name": "Pat", "email": "pat@acmeplumbing.test",
                   "asset_project_id": asset2_project_id, "note": "Wrong phone number"})
check("requesting changes on asset 2 succeeds independently", r.status_code, 200)

with hub_app.app_context():
    p1 = CsProjectModel7.query.get(asset1_project_id)
    p2 = CsProjectModel7.query.get(asset2_project_id)
    check("asset 1 is Approved", p1.status, "Approved")
    check("  ...asset 2 is Changes Requested, independently", p2.status, "Changes Requested")

    from modules.creative_studio.models import CsShareDecision as CsShareDecisionModel8
    same_reviewer_rows = CsShareDecisionModel8.query.filter_by(
        share_id=campaign_share["id"], reviewer_email="pat@acmeplumbing.test").all()
    check("one reviewer answering about two assets leaves two rows, not one "
          "overwriting the other", len(same_reviewer_rows), 2)

# Answering again on the SAME asset replaces that answer rather than adding a row.
r = anon.post(f"/review/{campaign_token}/decide",
              json={"outcome": "approved", "name": "Pat", "email": "pat@acmeplumbing.test",
                   "asset_project_id": asset2_project_id})
check("correcting the same reviewer's answer on the same asset succeeds", r.status_code, 200)
with hub_app.app_context():
    rows = CsShareDecisionModel8.query.filter_by(
        reviewer_email="pat@acmeplumbing.test", asset_project_id=asset2_project_id).all()
    check("  ...and replaces it rather than adding a second row", len(rows), 1)
    check("  ...reading the corrected outcome", rows[0].outcome, "approved")

r = client.post(f"/creative-studio/api/campaigns/{campaign_id}/share")
check("sending a second round succeeds", r.status_code, 200)
check("  ...round 2", r.get_json()["share"]["round"], 2)

r = anon.get(f"/review/{campaign_token}")
check("round 1's now-revoked token answers 410, not a bare 404", r.status_code, 410)

r = client.get(f"/creative-studio/api/campaigns/{campaign_id}/shares")
check("listing campaign shares succeeds", r.status_code, 200)
check("  ...both rounds present", len(r.get_json()["shares"]), 2)

r = client.post("/creative-studio/api/campaigns/999999/share")
check("sending a campaign that does not exist 404s", r.status_code, 404)

# ---------------------------------------------------------------------------
section("WO-CS8: radio assets appear read-only, matched by exact client name")

try:
    from modules.radio_scripts.db import db as _rs_db
    from modules.radio_scripts.models import RadioScriptSet
    _radio_available = True
except Exception:                                             # noqa: BLE001
    _radio_available = False

if _radio_available:
    with hub_app.app_context():
        rs = RadioScriptSet(client_name="Acme Plumbing", actor="Todd")
        rs.brief_json = '{"market": "Columbus", "package": "Drive Time"}'
        _rs_db.session.add(rs)
        _rs_db.session.commit()

    r = client.get(f"/creative-studio/campaigns/{campaign_id}")
    check("the campaign page lists the exactly-matched radio set", r.status_code, 200)
    check("  ...marked read-only", b"Read-only" in r.data, True)
else:
    print("  (skipped -- modules.radio_scripts not importable in this environment)")

# ---------------------------------------------------------------------------
section("WO-CS8: migrating a Commercial Builder Campaign row")

with hub_app.app_context():
    from modules.commercial_builder.models import Campaign as CbCampaignModel8
    from modules.commercial_builder.models import CommercialProject as CbProjectModel8
    from modules.commercial_builder.models import Client as CbClientModel8
    from modules.commercial_builder.db import db as cb_db8

    cb_client = CbClientModel8.query.filter_by(name="Acme Plumbing").first()
    if cb_client is None:
        cb_client = CbClientModel8(name="Acme Plumbing")
        cb_db8.session.add(cb_client)
        cb_db8.session.commit()

    cb_campaign = CbCampaignModel8(client_id=cb_client.id, name="Legacy Multi-Length Build")
    cb_db8.session.add(cb_campaign)
    cb_db8.session.commit()

    # A CB project this module has never heard of -- must not be migrated in.
    cb_db8.session.add(CbProjectModel8(client_id=cb_client.id, campaign_id=cb_campaign.id,
                                       title="Untouched by Studio", length_seconds=30,
                                       commercial_type="stock_vo", status="draft"))
    # A CB project Creative Studio HAS bound -- but NOT already a member of
    # some other cs_campaign (a project belongs to at most one). Reuse the
    # WO-CS7 project's own storyboard, which has a cb_project_id and has
    # never been added to any campaign.
    standalone = CsProjectModel7.query.get(cs_project_id)
    linked_cb_project = CbProjectModel8.query.get(standalone.cb_project_id)
    linked_cb_project.campaign_id = cb_campaign.id
    cb_db8.session.commit()
    cb_campaign_id = cb_campaign.id

    migrated = campaign_spec.migrate_cb_campaigns(actor="system")
    check("migrating finds the one legacy campaign with a bound project", migrated, 1)

    new_row = CsCampaignModel8.query.filter_by(cb_campaign_id=cb_campaign_id).first()
    check("  ...creates a cs_campaigns row for it", new_row is not None, True)
    check("  ...naming it after the CB campaign", new_row.name, "Legacy Multi-Length Build")
    linked_assets = CsCampaignAssetModel8.query.filter_by(campaign_id=new_row.id).all()
    check("  ...joining only the ONE project Studio actually bound "
          "(never the untouched sibling)", len(linked_assets), 1)
    check("  ...that project is the one Studio knows", linked_assets[0].project_id, cs_project_id)

    migrated_again = campaign_spec.migrate_cb_campaigns(actor="system")
    check("running the migration again is a no-op", migrated_again, 0)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
