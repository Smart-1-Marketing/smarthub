"""Stale Creative's row actions: Evergreen, New, Create — and who may read it.

    python3 test_stale_creative.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

The report said how long it had been and offered nothing to do about it. Three
actions now sit at the end of each row, the Source column has gone, and every
failure below is one where a screen would go on looking healthy:

  1. **A mark taken in one gunicorn worker must be honoured by the other.**
     The audit is cached for five minutes. Bake the evergreen overlay into that
     cache and a mark made in worker A is ignored by worker B until its own
     cache expires — a button that appears to do nothing, which is the failure
     `hub/client_urls.missing()` had to undo. So the overlay is applied on
     every *read* of the cache, and this file proves it by marking a client
     while the cache is warm and asking again.

  2. **Nothing disappears in silence.** A marked client leaves the stale list
     and appears under Evergreen, carrying the group it came from, who marked
     it and when, with one press to put it back. A list that quietly gets
     shorter cannot be told from a list that failed to load.

  3. **Every count on the page moves with it**, the dashboard scorecard
     included. A row pulled from the list and still counted in the total is a
     wrong number that looks exactly like a right one.

  4. **The mark is stored against the client's name, never the derived match
     key** — `hub/client_key.py` gives the reason at length — so it is
     re-matched on read and survives the report's matcher being tightened.

  5. **The write route and the report are behind a login.** The blueprint had
     no guard at all: `wsgi.py` wraps only dispatcher-mounted modules in
     AuthGuard, and the hub app guards its own views one at a time.

  6. **The buttons open the tools that already exist** — the Campaign Change
     Request form from /campaign-request.js, and the Display Ad Builder with
     the client filled in — rather than a second copy of either.

  7. **Every source in SOURCES actually reports.** Three of the four did not,
     each silently and each differently: the Image Picker entry reflected over
     `SavedImage` expecting Flask-SQLAlchemy's `.query`, which a plain
     declarative model does not have; Image Creator asked for `created_at` /
     `updated_at` where the index writes `created` / `updated`, so every row
     was dropped for having no date; and Commercial Builder asked for
     `client_name` where the row carries `client_id`, which is the worst of the
     three because the rows were *not* empty — the source counted as live,
     inflated the creative total, and every record was then dropped for having
     no client. A client whose gallery, canvas graphics and commercial were all
     produced this month read as "No creative on file", on the report whose
     entire purpose is that question.

     So this is swept rather than asserted about the three that were wrong: the
     stores are seeded and **every** entry in SOURCES is required to produce a
     record with a client, a date and a title. The field names are no longer
     declared here either — `hub/image_audit.py` reads these same stores and
     had them right all along, so the two modules guessing separately at one
     store's columns is the drift that caused this.

  8. **"We could not look" is not "nobody is overdue."** `measured` was on the
     payload for exactly this and no template read it, so a morning where every
     source refused drew the full six tiles and a "No creative on file" section
     naming the whole book. It now covers both halves of the join — a client
     list that refused returned `[]` and the audit reported nought clients while
     reading `measured: True` — and the page draws it.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1stale_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "stale-test-secret"

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from hub import auth, create_hub_app                          # noqa: E402
from hub import creative_evergreen as evergreen               # noqa: E402
from hub import stale_creative                                # noqa: E402

app = create_hub_app()

signed_in = app.test_client()
signed_in.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test Rep"))
anon = app.test_client()


def audit():
    return signed_in.get("/api/qa/stale-creative").get_json()


def group(data, key):
    return [g for g in data["groups"] if g["key"] == key][0]


def listed(data):
    """Every client still on the stale list, evergreen excluded."""
    return [c["client"] for g in data["groups"] if g["key"] != "evergreen"
            for c in g["clients"]]


# =====================================================================
section("Nobody reads this report without signing in")
# =====================================================================

r = anon.get("/qa/stale-creative")
check("the page redirects to the sign-in", r.status_code, 302)
check("naming where it was going", "/login?next=/qa/stale-creative" in
      (r.headers.get("Location") or ""), True)

r = anon.get("/api/qa/stale-creative")
# A fetch gets a JSON refusal, not a login page it would report as bad data.
check("the API refuses in JSON", r.status_code, 401)
check("and says why", bool((r.get_json() or {}).get("error")), True)

r = anon.post("/api/qa/stale-creative/evergreen",
              json={"client": "Anybody", "evergreen": True})
check("and so does the write route", r.status_code, 401)
check("nothing was written", evergreen.marks(), [])


# =====================================================================
section("The list carries actions, and no longer carries Source")
# =====================================================================

html = signed_in.get("/qa/stale-creative").get_data(as_text=True)
check("the page renders", "<h1>Stale Creative</h1>" in html, True)
check("the Source column is gone", "<th>Source</th>" in html, False)
check("an Actions column is there", '<th class="acts">Actions</th>' in html, True)
check("Evergreen is offered on a row", 'data-evergreen="1"' in html, True)
check("so is New", 'data-campaign-change="1"' in html, True)
check("New opens the one campaign form, not a copy",
      '<script src="/campaign-request.js"></script>' in html, True)
check("Create opens the ad builder with the client filled in",
      "/tools/display-ads/_hub/start?client=" in html, True)

# Which of our tools filed it is not a decision a rep makes — but the panel
# they open is where it belongs, so it is dropped from the row and kept there.
_template = (ROOT / "hub" / "templates" / "stale_creative.html").read_text(
    encoding="utf-8")
check("each creative still names its own source",
      "{{ it.source }}" in _template, True)

# The route the Create button points at has to exist on the composed app.
rules = {str(r.rule) for r in app.url_map.iter_rules()}
check("the evergreen route is registered",
      "/api/qa/stale-creative/evergreen" in rules, True)


# =====================================================================
section("Marking one evergreen takes it off the list, not out of sight")
# =====================================================================

before = audit()
target = None
for g in before["groups"]:
    if g["key"] != "evergreen" and g["clients"]:
        target = g["clients"][0]
        from_group = g["label"]
        break

if target is None:
    # No client book in this checkout: the overlay itself is still testable,
    # and saying so beats a section that silently asserts nothing.
    print("  ..    no clients in this checkout — testing the store directly")
    check("a mark is written", evergreen.set_mark(
        "Acme Plumbing", True, actor="Test Rep").get("ok"), True)
    check("and read back under its own name",
          [m["client"] for m in evergreen.marks()], ["Acme Plumbing"])
    check("who took it is kept", evergreen.marks()[0]["by"], "Test Rep")
    check("clearing it is case-insensitive on the name",
          evergreen.set_mark("acme plumbing", False).get("ok"), True)
    check("and it is gone", evergreen.marks(), [])
else:
    name = target["client"]

    # The audit above warmed the cache. Marking now and asking again is the
    # two-worker case: if the overlay were inside the cache this would still
    # show the client on the stale list.
    check("the cache is warm", stale_creative._CACHE["data"] is not None, True)
    r = signed_in.post("/api/qa/stale-creative/evergreen",
                       json={"client": name, "evergreen": True})
    check("the mark is accepted", r.status_code, 200)

    after = audit()
    ever = group(after, "evergreen")
    check("the client is off the stale list", name in listed(after), False)
    check("and on the evergreen one",
          [c["client"] for c in ever["clients"]], [name])
    check("carrying the group it came from",
          ever["clients"][0]["evergreen"]["from_group"], from_group)
    check("and who marked it", ever["clients"][0]["evergreen"]["by"], "Test Rep")
    check("with the date", len(ever["clients"][0]["evergreen"]["at"]) >= 10, True)
    check("the elapsed time travels with it",
          ever["clients"][0]["days_since"], target["days_since"])

    check("the listed total drops by one",
          after["totals"]["clients"], before["totals"]["clients"] - 1)
    check("the evergreen total rises by one", after["totals"]["evergreen"], 1)
    check("and evergreen is not counted as needing attention",
          after["totals"]["needs_attention"] <= before["totals"]["needs_attention"],
          True)

    # The dashboard tile reads the same cache through the same overlay, or the
    # two screens would disagree about how many clients are behind.
    sc = signed_in.get("/api/qa/stale-creative/scorecard").get_json()
    check("the dashboard scorecard agrees", sc["clients"], after["totals"]["clients"])
    check("and does not list an evergreen client among the worst",
          name in [w["client"] for w in sc["worst"]], False)

    page = signed_in.get("/qa/stale-creative").get_data(as_text=True)
    check("the page offers the way back", "Not evergreen" in page, True)
    check("and says who marked it", "marked by Test Rep" in page, True)

    # Stored against the name, never the derived key: the file holds what a
    # person marked, and the match is re-made on read.
    check("the store holds the name",
          [m["client"] for m in evergreen.marks()], [name])
    check("and no derived key", "key" in evergreen.marks()[0], False)

    r = signed_in.post("/api/qa/stale-creative/evergreen",
                       json={"client": name, "evergreen": False})
    check("the mark comes off", r.status_code, 200)
    back = audit()
    check("the client is back on the stale list", name in listed(back), True)
    check("evergreen is empty again", group(back, "evergreen")["count"], 0)
    check("and the total is what it was",
          back["totals"]["clients"], before["totals"]["clients"])


# =====================================================================
section("A mark that cannot be attributed is refused, not guessed at")
# =====================================================================

r = signed_in.post("/api/qa/stale-creative/evergreen", json={"client": "   "})
check("a blank client is refused", r.status_code, 400)
check("by name", (r.get_json() or {}).get("error"), "No client named.")

check("re-pressing keeps the first mark's author", (
    evergreen.set_mark("Repeat Co", True, actor="First"),
    evergreen.set_mark("Repeat Co", True, actor="Second", note="still on"),
    evergreen.marks()[0]["by"])[2], "First")
check("and takes the newer note", evergreen.marks()[0]["note"], "still on")
check("one row per client", len(evergreen.marks()), 1)
evergreen.set_mark("Repeat Co", False)

# The overlay must never cost the report. A store that will not read reports
# nothing rather than raising into a page nobody can then open at all.
_real = evergreen.marks
evergreen.marks = lambda: (_ for _ in ()).throw(RuntimeError("disk gone"))
try:
    data = audit()
    check("an unreadable overlay still renders the report",
          isinstance(data.get("groups"), list), True)
    check("with nothing marked", group(data, "evergreen")["count"], 0)
finally:
    evergreen.marks = _real


# ---------------------------------------------------------------------------
section("Every source reports, and none of them guesses at another module's columns")
# ---------------------------------------------------------------------------
import datetime as _dt                                        # noqa: E402

from hub import image_audit                                   # noqa: E402

# The shape `hub/image_audit.py`'s readers emit. A `store` source may name
# these keys and nothing else -- naming a column of the underlying table is
# how this file came to be guessing at four modules it does not own.
AUDIT_KEYS = {"id", "client", "public_id", "label", "url", "when", "where",
              "kind_of_client"}

_store_keys = {s["key"] for s in image_audit.STORES}
for src in stale_creative.SOURCES:
    store = src.get("store")
    if not store:
        continue
    check(f"{src['key']} names a real image_audit store",
          store in _store_keys, True)
    named = set()
    for field in ("client", "when", "title", "url", "note", "alt"):
        named.update(src.get(field) or ())
    check(f"{src['key']} reads only that reader's own shape",
          sorted(named - AUDIT_KEYS), [])

check("no source reflects over a model's columns any more",
      [s["key"] for s in stale_creative.SOURCES if s.get("models")], [])
check("and every source declares something that can be read",
      [s["key"] for s in stale_creative.SOURCES
       if not (s.get("store") or s.get("callable"))], [])


# Seed each store, then require every source to produce a record. A sweep, so a
# source added next month cannot go quiet without failing this run.
with app.app_context():
    from modules.image_picker.models import PickerClient, SavedImage, session
    _db = session()
    _pc = PickerClient(name="Icon Solar", slug="icon-solar", kind="client")
    _db.add(_pc)
    _db.commit()
    _db.add(SavedImage(
        client_id=_pc.id, filename="panel.jpg", provider="upload",
        provider_image_id="u1", source_url="https://x/u.jpg",
        cloudinary_url="https://res.cloudinary.com/d/image/upload/v1/x.jpg",
        cloudinary_public_id="x", collection_kind="upload",
        created_at=_dt.datetime(2026, 8, 1, 9, 0)))
    _db.commit()
    _db.close()

    from hub import jsonstore
    from modules.image_creator import projects as _icp
    jsonstore.write_json(_icp._index_path(), [{
        "id": "p1", "client": "Icon Solar", "name": "Summer banner",
        "preview_url": "https://res.cloudinary.com/d/image/upload/v1/b.png",
        "created": "2026-07-02 10:00", "updated": "2026-08-05 11:30"}])

    from modules.seo_images import app as _seoapp
    jsonstore.write_json(_seoapp._INDEX_PATH, [{
        "id": "s1", "company": "Icon Solar", "filename": "hero.webp",
        "public_id": "seo/hero",
        "url": "https://res.cloudinary.com/d/image/upload/v1/hero.webp",
        "saved_at": "2026-08-20T14:00:00", "project": "Summer refresh"}])

    from hub.extensions import db as _hubdb
    from modules.commercial_builder.models import (                # noqa: E402
        Client as _CBClient, CommercialProject as _CBProject,
        RenderApproval as _CBApproval, RenderJob as _CBJob)
    _cb = _CBClient(name="Northgate Dental", slug="northgate-dental")
    _hubdb.session.add(_cb)
    _hubdb.session.commit()
    _proj = _CBProject(client_id=_cb.id, title="Spring promo",
                       length_seconds=30, platform="ctv")
    _hubdb.session.add(_proj)
    _hubdb.session.commit()
    _job = _CBJob(project_id=_proj.id, format="16:9", status="succeeded")
    _hubdb.session.add(_job)
    _hubdb.session.commit()
    _hubdb.session.add(_CBApproval(
        project_id=_proj.id, render_job_id=_job.id, approved_by="Todd",
        approved_at=_dt.datetime(2026, 8, 10, 12, 0),
        stored_url="https://res.cloudinary.com/d/video/upload/v1/spot.mp4"))
    _hubdb.session.commit()

    for src in stale_creative.SOURCES:
        rows = stale_creative._load_source(src)
        check(f"{src['key']} produces a record", bool(rows), True)
        if not rows:
            continue
        r = rows[0]
        check(f"{src['key']} names a client", bool(r["client_raw"]), True)
        check(f"{src['key']} carries a date", r["uploaded_at"] is not None, True)
        check(f"{src['key']} carries a title", r["title"] != "Untitled", True)

    # The commercial is an *approved* render rather than a project row: a cut
    # nobody has watched is not creative the client received, which is the
    # distinction approve_render already draws.
    _cbrows = stale_creative._commercial_rows()
    check("the commercial resolves its client through cb_clients",
          [r["client"] for r in _cbrows], ["Northgate Dental"])
    check("and carries the Cloudinary copy, not the provider URL",
          _cbrows[0]["url"].endswith("/spot.mp4"), True)

    # The thumbnail rule is hub/storage.preview_url()'s, not a second one here.
    _img = stale_creative._thumb(
        "https://res.cloudinary.com/d/image/upload/v1/x.jpg")
    check("a tile is capped, never center-cropped", "c_limit" in _img, True)
    check("and does not carry the old c_fill crop", "c_fill" in _img, False)
    check("a video is left alone -- it is not an image transformation",
          stale_creative._thumb(
              "https://res.cloudinary.com/d/video/upload/v1/spot.mp4"),
          "https://res.cloudinary.com/d/video/upload/v1/spot.mp4")


# ---------------------------------------------------------------------------
section("A source that could not look never reads as nobody being overdue")
# ---------------------------------------------------------------------------
PAGE = (ROOT / "hub" / "templates" / "stale_creative.html").read_text()
check("the page reads `measured` at all", "data.measured" in PAGE, True)


def _audit_flags(**patched):
    """build_audit() with some of its readers replaced, then put back."""
    originals = {k: getattr(stale_creative, k) for k in patched}
    for k, v in patched.items():
        setattr(stale_creative, k, v)
    try:
        with app.app_context():
            return stale_creative.build_audit()
    finally:
        for k, v in originals.items():
            setattr(stale_creative, k, v)


good = _audit_flags()
check("a run that read both halves is measured", good["measured"], True)

no_clients = _audit_flags(_registry_clients=lambda: [])
check("a client list that refused is not measured",
      no_clients["measured"], False)
check("and says which half it was", no_clients["clients_measured"], False)

no_sources = _audit_flags(_load_source=lambda src: [],
                          _load_knack_creative=lambda: [],
                          _load_cloudinary=lambda: [])
check("every creative source refusing is not measured",
      no_sources["measured"], False)
check("and says which half that was", no_sources["sources_measured"], False)

# report_cache reads the same flag, so an unmeasured run is not frozen into the
# day's answer -- connecting the source an hour later is not lost until tomorrow.
from hub import report_cache                                   # noqa: E402
check("so it is never stored as the day's answer",
      report_cache.is_answer(no_sources), False)
check("while a good run is", report_cache.is_answer(good), True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
