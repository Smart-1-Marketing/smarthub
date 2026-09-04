"""Dead Air Cutter and Vertical Reframe.

    python3 test_video_tools.py

Same shape as the other test files here: no pytest, no new dependencies, and
it runs against a temporary data directory and a throwaway SQLite database, so
it never touches /var/data or the real one. Nothing in it calls Cloudinary or
OpenAI — the network-facing halves are exercised through the pure functions
they hand their answers to, which is where every decision this module makes
actually lives.

## What this file is protecting

**The cut list is arithmetic on a client's video.** `silence.plan()` decides
what disappears from a finished commercial, and it is a hundred lines of
offsets that all look plausible when wrong. So the first section builds
synthetic waveforms whose answers are known by construction — a clip with one
two-second hole in the middle has exactly one cut, at a place this file can
compute — and checks the arithmetic rather than checking that it ran.

**A transformation that is subtly wrong 400s at Cloudinary, minutes later.**
Folder separators inside a layer reference become colons; an offset of `8.0`
is rejected where `8` is accepted. Both are the kind of thing that is right
the day it is written and wrong the day somebody edits it, and neither shows
up until a render has been waited for. They are asserted as strings here.

**Cutting nothing must not render anything.** A plan with no cuts produces a
transformation that is a copy of the source. Rendering it would spend a
derivation, file a duplicate in a client's gallery, and look like it worked.

**The two tools are two tiles, two help entries and two guarded mounts.**
`test_menu_layout.py` names the failure — a tool with no tile is invisible —
and `hub/help_audit.py` names the other one: a help key placed on a page and
absent from the registry is removed client-side, so the page reads as helped
and shows nothing. Both are checked here for both tools, because both were
added in one commit and a commit that adds two tools forgets one of them.

**A blueprint on the hub app is not behind AuthGuard.** `wsgi.py` wraps only
dispatcher-mounted modules. These are blueprints, so the guard is on the
blueprint, and the last section is the proof that it is actually on.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1vt_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "video-tools-test-secret")

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


from modules.video_tools import config, reframe, silence, sources  # noqa: E402


def levels(spec, columns_per_second=20):
    """A synthetic waveform from (seconds, loud?) pairs.

    Loud is 1.0 and quiet is 0.0, which is the clearest possible signal — the
    threshold is not what these tests are about, and a fixture that sat near
    it would make every assertion below a test of the threshold instead.
    """
    out = []
    for seconds, loud in spec:
        out += [1.0 if loud else 0.0] * int(round(seconds * columns_per_second))
    return out


SPC = 1 / 20.0

# ---------------------------------------------------------------------------
section("Finding the dead air")

# Two seconds of speech, a two-second hole, two more seconds of speech.
one_hole = levels([(2, True), (2, False), (2, True)])
plan = silence.plan(one_hole, seconds_per_column=SPC, duration=6.0,
                    gap=0.6, breath=0.0, sensitivity="normal", trim_ends=True)
check("one hole is one cut", len(plan["cuts"]), 1)
check("  ...starting where the speech stops", round(plan["cuts"][0]["start"], 1), 2.0)
check("  ...ending where it starts again", round(plan["cuts"][0]["end"], 1), 4.0)
check("  ...removing the whole hole", round(plan["removed"], 1), 2.0)
check("  ...leaving the rest", round(plan["kept"], 1), 4.0)
check("  ...as two segments", len(plan["segments"]), 2)

# The same hole with breathing room. Half of it is left at each side, so the
# cut is shorter by exactly one breath and the kept audio is longer by it.
padded = silence.plan(one_hole, seconds_per_column=SPC, duration=6.0,
                      gap=0.6, breath=0.4, sensitivity="normal")
check("breathing room shortens the cut", round(padded["removed"], 1), 1.6)
check("  ...and lands half of it either side",
      (round(padded["cuts"][0]["start"], 1), round(padded["cuts"][0]["end"], 1)),
      (2.2, 3.8))

# A pause shorter than the gap is speech rhythm and must survive untouched.
short = levels([(2, True), (0.3, False), (2, True)])
check("a pause under the gap is not a cut",
      silence.plan(short, seconds_per_column=SPC, duration=4.3)["cuts"], [])

# Head and tail are a different edit from a pause in the middle, and the
# switch that says so has to actually reach them.
ends = levels([(1.5, False), (2, True), (1.5, False)])
check("head and tail trim when asked",
      len(silence.plan(ends, seconds_per_column=SPC, duration=5.0,
                       trim_ends=True)["cuts"]), 2)
check("  ...and are left alone when not",
      silence.plan(ends, seconds_per_column=SPC, duration=5.0,
                   trim_ends=False)["cuts"], [])

# Silence with sound under it is not dead air. This is the whole reason the
# module reads a waveform rather than a transcript: a music bed sits at a low
# level, and only the aggressive setting should reach down to it.
bed = levels([(2, True), (2, False), (2, True)])
bed = [0.05 if v == 0.0 else v for v in bed]
check("a quiet music bed survives 'gentle'",
      silence.plan(bed, seconds_per_column=SPC, duration=6.0,
                   sensitivity="gentle")["cuts"], [])
check("  ...and is cut by 'aggressive'",
      len(silence.plan(bed, seconds_per_column=SPC, duration=6.0,
                       sensitivity="aggressive")["cuts"]), 1)

# ---------------------------------------------------------------------------
section("The budget on how many cuts one edit may carry")

many = []
for _ in range(config.MAX_CUTS + 8):
    many += [(0.5, True), (1.0, False)]
many += [(0.5, True)]
duration = sum(s for s, _ in many)
capped = silence.plan(levels(many), seconds_per_column=SPC, duration=duration,
                      breath=0.0, trim_ends=False)
check("more gaps than the budget is capped", capped["capped"], True)
check("  ...at the budget", len(capped["cuts"]), config.MAX_CUTS)
check("  ...and says how many it saw", capped["considered"], config.MAX_CUTS + 8)
check("  ...and says so on the page", any("longest" in n for n in capped["notes"]), True)

# The cap takes the LONGEST gaps, not the first ones. A clip whose worst dead
# air is at the end must not come back still baggy at the end.
mixed = levels([(1, True), (3.0, False), (1, True)]
               + [(1, True), (0.7, False)] * (config.MAX_CUTS + 4))
mixed_duration = 5.0 + (config.MAX_CUTS + 4) * 1.7
worst = silence.plan(mixed, seconds_per_column=SPC, duration=mixed_duration,
                     breath=0.0, trim_ends=False)
check("the cap keeps the longest gap", worst["cuts"][0]["removed"] > 2.5, True)

# ---------------------------------------------------------------------------
section("The transformation that carries the cuts")

tx = silence.concat_transformation(
    "acme/videos/spot", [{"start": 0, "end": 3.25}, {"start": 5, "end": 8}])
check("first segment is a trim on the asset itself",
      tx.startswith("so_0,eo_3.25/"), True)
# Folder separators inside a layer reference are colons. This is the single
# most common way a correct-looking Cloudinary transformation 400s.
check("  ...and later ones are spliced layers with colon ids",
      "fl_splice,l_video:acme:videos:spot/so_5,eo_8/fl_layer_apply" in tx, True)
# Cloudinary takes 8 and 8.5 in an offset and rejects 8.0.
check("  ...with no trailing .0 on a whole-second offset", ".0" in tx, False)
check("one segment needs no splice at all",
      silence.concat_transformation("x", [{"start": 0, "end": 5}]), "so_0,eo_5")
check("no segments is no transformation",
      silence.concat_transformation("x", []), "")

# ---------------------------------------------------------------------------
section("Reframing, and what it admits to")

crop = reframe.plan(source_width=1920, source_height=1080, ratio="9:16",
                    mode="crop", focus="auto")
check("9:16 is 1080x1920", (crop["width"], crop["height"]), (1080, 1920))
# On VIDEO, Cloudinary refuses `g_auto` inline: "g_auto must be in a
# transformation component by itself". Both forms were submitted to the live
# account -- the inline one failed, the split one returned a 1080x1920 file --
# so the split is asserted here rather than left to be rediscovered by a
# render that fails minutes after somebody asked for it.
check("  ...filled, with automatic gravity",
      crop["transformation"], "g_auto/w_1080,h_1920,c_fill,q_auto")
# 16:9 to 9:16 keeps (9/16) / (16/9) = 31.6% of the width. The tool says so
# rather than showing a preview and letting somebody assume.
check("  ...keeping 32% of the width", crop["loss"]["kept_width_pct"], 32)
check("  ...and saying so on the page",
      any("width survives" in n for n in crop["notes"]), True)

faces = reframe.plan(source_width=1920, source_height=1080, focus="faces")
check("the faces option asks for faces",
      faces["transformation"].startswith("g_auto:faces/"), True)
# g_center is not g_auto and needs no component of its own; splitting it would
# be a second component for no reason.
check("a fixed gravity stays inline",
      "c_fill,g_center" in reframe.plan(source_width=1920, source_height=1080,
                                        focus="center")["transformation"], True)

blur = reframe.plan(source_width=1920, source_height=1080, ratio="9:16",
                    mode="blur")
check("padding uses a blurred blow-up",
      "c_pad" in blur["transformation"] and "b_blurred" in blur["transformation"], True)
check("  ...and chooses no gravity, because nothing is discarded",
      "g_auto" in blur["transformation"], False)
check("  ...so nothing is lost", blur["loss"]["kept_area_pct"], 100)

check("muting drops the audio track",
      "ac_none" in reframe.plan(source_width=1920, source_height=1080,
                                mute=True)["transformation"], True)
# An unknown ratio or mode must fall back rather than reach Cloudinary as a
# broken component: a rep typing "9x16" is a failed render minutes later.
check("an unknown ratio falls back to the default",
      reframe.plan(source_width=1920, source_height=1080,
                   ratio="9x16")["ratio"], config.DEFAULT_RATIO)
check("an unknown mode falls back to cropping",
      reframe.plan(source_width=1920, source_height=1080,
                   mode="squish")["mode"], "crop")
# Every plan carries the sentence that keeps this tool from being mistaken for
# a substitute for a spot built vertical.
check("every crop says it is a cutdown, not a vertical spot",
      any("built" in n and "vertical" in n for n in crop["notes"]), True)

# ---------------------------------------------------------------------------
section("Reading whatever was pasted into the source box")

URL = ("https://res.cloudinary.com/smart1labs/video/upload/"
       "v1767733205/Icon%20Solar/1920x1080.mp4")
check("a plain delivery URL", sources.public_id_from_url(URL),
      "Icon Solar/1920x1080")
check("  ...with a transformation in front of it",
      sources.public_id_from_url(
          "https://res.cloudinary.com/c1/video/upload/w_1080,c_fill,g_auto/"
          "v1/folder/clip.mp4"), "folder/clip")
check("  ...and with no version segment",
      sources.public_id_from_url(
          "https://res.cloudinary.com/c1/video/upload/folder/clip.mp4"),
      "folder/clip")
# Refusing is the right answer for anything else. A guess here edits the wrong
# asset, which is the one mistake on this page that cannot be undone by
# clicking again.
check("somebody else's URL is refused, not guessed at",
      sources.public_id_from_url("https://example.com/video/upload/clip.mp4"), "")
check("a public id is passed through", sources.resolve(" folder/clip "),
      "folder/clip")
check("a leading slash is not a folder", sources.resolve("/folder/clip"),
      "folder/clip")
try:
    sources.resolve("")
    check("an empty box is refused", False, True)
except sources.SourceError:
    check("an empty box is refused", True, True)
try:
    sources.resolve("https://example.com/clip.mp4")
    check("a non-Cloudinary link is refused", False, True)
except sources.SourceError:
    check("a non-Cloudinary link is refused", True, True)

# ---------------------------------------------------------------------------
section("Both tools are wired the way a tool has to be")

from werkzeug.test import Client  # noqa: E402

import wsgi  # noqa: E402
from hub import auth, help as hub_help, help_coverage, scheduler, sidebar  # noqa: E402

MOUNTS = {"dead_air": "/tools/dead-air", "reframe": "/tools/vertical-reframe"}
TILES = {"/tools/dead-air/": "Dead Air Cutter",
         "/tools/vertical-reframe/": "Vertical Reframe"}

tiles = {t["href"]: t["name"] for t in help_coverage.tiles()}
for href, name in TILES.items():
    check(f"{name} is tiled on a staff index page", tiles.get(href), name)
    check(f"  ...and its mount is mapped for the help audit",
          href in help_coverage.PREFIXES, True)

keys = {h.key for h in hub_help.REGISTRY}
for key in ("video_tools.dead_air", "video_tools.reframe"):
    check(f"{key} is in the help registry", key in keys, True)
for tool, mount in MOUNTS.items():
    check(f"{mount} opens with the creative nav",
          sidebar.collapses_by_default(mount + "/"), True)

client = Client(wsgi.application)
for mount in MOUNTS.values():
    # Blueprints on the hub app are not behind wsgi.py's AuthGuard. The guard
    # is on the blueprint, and this is the proof it is actually installed.
    out = client.get(mount + "/")
    check(f"{mount}/ refuses an anonymous visitor", out.status_code in (302, 401), True)
    api = client.get(mount + "/api/sources")
    check(f"  ...and so does its API", api.status_code in (302, 401), True)

client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"),
                  domain="localhost")
for tool, mount in MOUNTS.items():
    page = client.get(mount + "/")
    check(f"{mount}/ renders for staff", page.status_code, 200)
    body = page.get_data(as_text=True)
    # The help bubble has to be *placed* as well as registered; a key in the
    # registry with nothing on the page is the mirror of the failure above.
    check(f"  ...carrying its help bubble", f"video_tools.{tool}" in body, True)
    check(f"  ...and the source picker", 'id="vt-src"' in body, True)
    listing = client.get(mount + "/api/sources")
    check(f"  ...and lists sources without Cloudinary configured",
          listing.status_code, 200)
    # Cloudinary is unset in this test environment, and an unconfigured
    # account has to read as unconfigured rather than as an empty library.
    check(f"  ...saying so rather than showing an empty library",
          listing.get_json().get("ready"), False)

# The handoff from a finished commercial. An approved spot is filed as a
# Cloudinary asset and the filing report links straight here — which only
# works if the link is on that side and the box is read on this one, and the
# two are in different modules written on different days.
cb = (ROOT / "modules/commercial_builder/static/js/preview.js").read_text()
for mount in MOUNTS.values():
    check(f"a filed commercial offers {mount}", f'href="{mount}/?source=' in cb, True)
check("  ...handing over the STORED copy, not the expiring render URL",
      "approval.stored_url" in cb.split("function nextSteps")[1][:400], True)
shared = (ROOT / "modules/video_tools/templates/_video_tools.html").read_text()
check("  ...and the tools read ?source= on load",
      'params.get("source")' in shared and "URLSearchParams" in shared, True)

# A source the account does not have is refused with a sentence, not a 500.
bad = client.post("/tools/dead-air/api/source",
                  json={"source": "no/such/clip"})
check("an unknown source is refused with a message", bad.status_code, 400)
check("  ...that is fit to put on the page",
      bool(bad.get_json().get("error")), True)

# ---------------------------------------------------------------------------
section("An edit that finishes with nobody watching")

# The failure this whole section exists for: the browser used to be the only
# thing polling, so closing the tab left the edit finishing at Cloudinary with
# nothing writing the answer down. The row stayed `building` for ever and the
# person came back to a job that looked stuck.

from datetime import datetime, timedelta  # noqa: E402

from modules.video_tools import alerts, edits  # noqa: E402
from modules.video_tools.db import db  # noqa: E402
from modules.video_tools.models import VideoJob  # noqa: E402

import hub  # noqa: E402

app = hub.create_hub_app()


def make_job(**kw):
    job = VideoJob(tool=kw.pop("tool", "reframe"),
                   source_public_id=kw.pop("source", "acme/videos/spot"),
                   transformation="g_auto/w_1080,h_1920,c_fill,q_auto",
                   status=kw.pop("status", "building"),
                   actor=kw.pop("actor", "Todd"), **kw)
    job.options = {"ratio": "9:16"}
    db.session.add(job)
    db.session.commit()
    return job


with app.app_context():
    # Cloudinary is stubbed rather than called: the sweep's job is to write
    # down whatever it is told, and what this has to prove is that it writes
    # it down at all when no browser is asking.
    real_poll, real_ready = edits.poll, alerts.sources.ready
    answers = {}
    alerts.sources.ready = lambda: True
    edits.poll = lambda pid, tx: answers.get(pid, {"status": "building"})

    answers["done/clip"] = {"status": "done", "url": "https://res/out.mp4"}
    answers["bad/clip"] = {"status": "failed", "error": "Cloudinary said no."}
    finished = make_job(source="done/clip")
    broken = make_job(source="bad/clip")
    waiting = make_job(source="slow/clip")
    # Older than the page's own patience. The sweep has to give up on the same
    # schedule the page does, or the two disagree about whether it is running.
    stuck = make_job(source="stuck/clip")
    stuck.created_at = datetime.utcnow() - timedelta(
        seconds=alerts.STALE_SECONDS + 60)
    db.session.commit()

    out = alerts.sweep()
    check("the sweep checks everything still building", out["checked"], 4)
    check("  ...and finishes the one that finished",
          (db.session.get(VideoJob, finished.id).status,
           db.session.get(VideoJob, finished.id).result_url),
          ("done", "https://res/out.mp4"))
    check("  ...records the one that failed",
          db.session.get(VideoJob, broken.id).status, "failed")
    check("  ...leaves the one still building alone",
          db.session.get(VideoJob, waiting.id).status, "building")
    check("  ...and gives up on the one that never landed",
          db.session.get(VideoJob, stuck.id).status, "failed")
    check("a finished job is stamped with a finish time",
          bool(db.session.get(VideoJob, finished.id).finished_at), True)

    # An unconfigured deployment must say so rather than looking like a quiet
    # success — the failure hub/google_index.py had to learn to stop making.
    alerts.sources.ready = lambda: False
    check("no Cloudinary is a skip with a reason, not silence",
          "skipped" in alerts.sweep(), True)
    alerts.sources.ready = lambda: True

    # ---------------------------------------------------------------
    notices = alerts.ready_for("Todd")
    ids = [n["id"] for n in notices]
    check("both finished edits are waiting to be announced",
          sorted(ids), sorted([finished.id, broken.id, stuck.id]))
    check("  ...and the one still building is not", waiting.id in ids, False)
    check("the notice carries the link to the job itself",
          next(n["url"] for n in notices if n["id"] == finished.id),
          f"/tools/vertical-reframe/?job={finished.id}")
    check("  ...and says what it is in a sentence",
          "9:16" in next(n["headline"] for n in notices
                         if n["id"] == finished.id), True)
    check("  ...while a failed one reads as a failure",
          next(n["status"] for n in notices if n["id"] == broken.id), "failed")

    # Somebody else's edits are not their business, and are not silenced by
    # them either.
    theirs = make_job(actor="Jim", status="done", source="jim/clip")
    check("an edit is announced to the person who started it",
          [n["id"] for n in alerts.ready_for("Jim")], [theirs.id])
    check("  ...and cannot be marked seen by anybody else",
          alerts.mark_seen([theirs.id], "Todd"), 0)
    check("  ...and still is not, having tried",
          [n["id"] for n in alerts.ready_for("Jim")], [theirs.id])

    check("marking seen stamps the row", alerts.mark_seen([finished.id], "Todd"), 1)
    check("  ...so it is not announced twice",
          finished.id in [n["id"] for n in alerts.ready_for("Todd")], False)
    check("  ...and marking it again stamps nothing",
          alerts.mark_seen([finished.id], "Todd"), 0)

    # A shared-password session has no name to match on. It sees everything,
    # because the one kind of session that cannot be told apart must not also
    # be the one that is never told anything.
    check("an anonymous session sees every outstanding edit",
          theirs.id in [n["id"] for n in alerts.ready_for("")], True)

    edits.poll, alerts.sources.ready = real_poll, real_ready

# ---------------------------------------------------------------------------
section("Where the notice actually shows up")

check("the scheduler polls for finished edits", "video_tools" in scheduler.JOBS, True)
check("  ...every minute, because a late notice is an ignored notice",
      scheduler.JOBS["video_tools"][0], 1)

alerts_api = client.get("/video-tools/api/ready")
check("/video-tools/api/ready answers a signed-in reader", alerts_api.status_code, 200)
check("  ...with a list", isinstance(alerts_api.get_json().get("items"), list), True)

anon = Client(wsgi.application)
check("  ...and refuses an anonymous one",
      anon.get("/video-tools/api/ready").status_code in (302, 401), True)

base = (ROOT / "hub/templates/base.html").read_text()
check("the popup is loaded on every hub page", "/hub-video-alerts.js" in base, True)
alerts_js = (ROOT / "hub/static/hub-video-alerts.js")
check("  ...and the file it names exists", alerts_js.exists(), True)
js = alerts_js.read_text()
# The rule hub-cheers.js arrived at. Marked when SHOWN: a reload must not
# bring the same interruption back.
check("  ...marks a notice seen as it draws it",
      "/video-tools/api/ready/seen" in js, True)
check("  ...and stays silent inside somebody else's iframe", "framed()" in js, True)
served = client.get("/hub-video-alerts.js")
check("  ...and the Hub serves it", served.status_code, 200)

dash = (ROOT / "hub/templates/dashboard.html").read_text()
check("the dashboard reads the same endpoint", "/video-tools/api/ready" in dash, True)
# Deliberately does not POST /seen: the card is what is still waiting, and a
# card that empties because somebody glanced at the dashboard loses the edit
# they had not opened yet.
check("  ...without marking anything seen",
      "/video-tools/api/ready/seen" in dash, False)
check("  ...and stays hidden when there is nothing waiting",
      'id="vt-card" hidden' in dash, True)

check("a notice's link opens straight onto the job",
      'params.get("job")' in shared, True)

print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
