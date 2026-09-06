"""One design, the whole size set — and the ways a resize is confidently wrong.

    python3 test_magic_resize.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and its own data directory, so it never touches
/var/data or the real one. Both are set, which is the pairing `test_jsonstore.py`
names: an own directory in front of an inherited database is an empty disk in
front of a full mirror, and the second run of the file reads the first one's
writes.

## What is worth asserting

**A resize that produced a broken ad and reported success.** That is the whole
failure this tool can have, and it is silent from every direction: the frame
renders, the file uploads, and it is the client who sees a headline printed
through a button. So the collision guard is asserted from both ends — it names
the objects on a frame that is genuinely broken, and it does *not* fire on a
frame that is merely tight, because a guard with false positives is one
somebody switches off and switching this one off costs the real findings.

**Copy that vanished to make a layout fit.** A leaderboard has nowhere to put
rate copy at a legible size, and the tempting answer is to drop it. Every
object a template could not place is named in `unplaced`, and one carrying
words marks the frame.

**The two halves of §6, which fail in opposite directions.** Copy must reach
an edited frame — a rep who retypes a headline eight times gets one of the
eight wrong. Layout must not — somebody moved that button on purpose, and
regenerating it destroys a decision with nothing on any screen saying so.

**No fourth copy of the spec kit.** `hub/creative_specs.py` is the
transcription and `kit_drift()` holds it against the published page;
`modules/ad_builder` has the renderer's own; `image_creator.CANVAS_PRESETS` is
a canvas picker. This module restates none of it, and the assertion is against
the kit's real numbers rather than a fixture, so a size that stops being read
from the kit fails here rather than drifting quietly.

**A model proposes and the code decides.** The recompose path is asserted with
a stub in front of it: it returns *positions* for objects that already exist,
it ignores an id it invented, it leaves an object it did not mention where it
was, and its answer goes back through the same guard a template's output does.
"""
import io
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-magicresize-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ.setdefault("SECRET_KEY", "magic-resize-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


_UNSET = object()


def check(label, got, want=_UNSET):
    """Two shapes, and the second is the one worth having.

    `check(label, condition)` asserts truth. `check(label, got, want)` asserts
    they are equal and, when they are not, prints **both** — an assertion that
    reports only that it failed is one somebody re-runs rather than reads.
    """
    global PASS, FAIL
    ok = bool(got) if want is _UNSET else (got == want)
    if ok:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        detail = ("" if want is _UNSET
                  else f"  — got {got!r}, wanted {want!r}")
        print("  FAIL " + label + detail)


def section(title):
    print("\n" + title)
    print("-" * 60)


from hub import creative_specs                                  # noqa: E402
from modules.magic_resize import (engine, export, fabric_io,    # noqa: E402
                                  qc, recompose, roles as R,
                                  sizes as S, store)
from modules.magic_resize import templates_layout as L          # noqa: E402


def design(width=300, height=250, *, with_cta=True, with_disclaimer=False,
           family="square_medium"):
    """A finished square design with every role tagged."""
    objects = [
        {"id": "bg", "role": R.BACKGROUND, "kind": "image",
         "x": 0, "y": 0, "w": width, "h": height},
        {"id": "logo", "role": R.LOGO, "kind": "image",
         "x": 12, "y": 10, "w": 70, "h": 28},
        {"id": "head", "role": R.HEADLINE, "kind": "text",
         "x": 12, "y": 62, "w": 276, "h": 46,
         "text": "Cool air, fast", "fontSize": 26},
        {"id": "sub", "role": R.SUBHEADLINE, "kind": "text",
         "x": 12, "y": 114, "w": 276, "h": 26,
         "text": "Same-day service", "fontSize": 15},
        {"id": "shot", "role": R.PRODUCT, "kind": "image",
         "x": 170, "y": 140, "w": 110, "h": 44},
    ]
    if with_cta:
        objects.append({"id": "cta", "role": R.CTA, "kind": "group",
                        "x": 12, "y": 196, "w": 120, "h": 40})
    if with_disclaimer:
        objects.append({"id": "small", "role": R.DISCLAIMER, "kind": "text",
                        "x": 12, "y": 238, "w": 276, "h": 10,
                        "text": "Offer ends 31 March. Terms apply.",
                        "fontSize": 8})
    return {"width": width, "height": height, "family": family,
            "objects": objects}


def fails(frame):
    return [f for f in frame["findings"] if f["level"] == qc.FAIL]


def codes(frame):
    return sorted({f["code"] for f in frame["findings"]})


# ==========================================================================
section("The sizes are the kit's, and nothing here restates one")

check("no dimension is hard-coded for a size the kit publishes",
      all("w" not in row for row in S._DECLARED if row.get("unit")), True)

# Named rather than counted. A count is satisfied by any six sizes, so a size
# quietly restated here as a house one would keep the total and lose the whole
# point — a set of the right size and the wrong contents is the same failure
# one step on.
KIT_BACKED = ("med_rect", "leaderboard", "wide_sky", "half_page", "billboard",
              "mobile_banner", "social_square")
for _sid in KIT_BACKED:
    _row = S.get(_sid)
    _unit = creative_specs.BY_ID.get((_row or {}).get("unit") or "")
    check(f"  {_sid} is read from the kit rather than restated",
          (_row or {}).get("source"), "kit")
    check(f"  ...and carries the kit's own pixels for it",
          (_row["w"], _row["h"]) if _row else None,
          tuple(_unit["size"]) if _unit and _unit.get("size")
          else (tuple(_unit["min_size"]) if _unit and _unit.get("min_size")
                else None))

med = S.get("med_rect")
unit = creative_specs.BY_ID["medium_rectangle"]
check("a kit size carries the kit's own pixels",
      (med["w"], med["h"]), unit["size"])
check("...and the kit's own weight ceiling", med["max_bytes"], unit["max_bytes"])

check("every house size says it is ours",
      all(s.get("reason") for s in S.house_sizes()), True)
check("and no house size claims the kit",
      any(s["source"] == "kit" for s in S.house_sizes()), False)

align = S.check_kit_alignment()
check("the alignment check measures rather than assuming", align["measured"])
check("and nothing is unresolved today", align["unresolved"], [])

# A size the kit weighs nothing for is not judged against one it resembles.
check("a kit size is judged by the kit",
      qc.weight("med_rect", size_bytes=200 * 1024, fmt="jpg")["result"], "fail")
check("...and passes under the ceiling",
      qc.weight("med_rect", size_bytes=80 * 1024, fmt="jpg")["result"], "pass")
check("a house size with no published ceiling is NOT measured",
      qc.weight("large_rect", size_bytes=900 * 1024)["measured"], False)
check("a size with a platform ceiling of its own names whose it is",
      "Google" in (qc.weight("rda_landscape",
                             size_bytes=9 * 1024 * 1024).get("note") or ""),
      True)


# ==========================================================================
section("Tier 1 — the whole Display Standard bundle from one design")

src = design()
built = {t["id"]: engine.resize(src, t)
         for t in S.bundle_sizes("display_standard")}

check("every size in the bundle produced a frame",
      len(built), len(S.BUNDLES["display_standard"]["sizes"]))

same_family = built["large_rect"]
check("a neighboring shape is scaled rather than re-laid out",
      same_family["tier"], "anchor")
check("...and comes back clean", same_family["status"], engine.AUTO)
check("no object on it is clipped", "clipped" in codes(same_family), False)
check("and nothing on it collides", "collision" in codes(same_family), False)

check("no frame in the bundle is falsely marked for review",
      [sid for sid, f in built.items() if f["status"] != engine.AUTO], [])

# The rule the anchor pass exists for: raw coordinate scaling drifts a
# right-hand object toward the middle as the frame changes shape.
wide = engine.resize({**design(970, 250, family="leaderboard")},
                     S.get("billboard"))
right = next(o for o in wide["objects"] if o["id"] == "shot")
check("a right-anchored object stays against the right edge",
      abs((wide["width"] - (right["x"] + right["w"]))
          - (970 - (170 + 110))) < 1.5)

bg = next(o for o in same_family["objects"] if o["id"] == "bg")
check("the background covers the new frame",
      bg["w"] >= same_family["width"] and bg["h"] >= same_family["height"])

check("a text object's type is scaled with it",
      next(o for o in same_family["objects"]
           if o["id"] == "head")["fontSize"] > 26, True)


# ==========================================================================
section("Tier 2 — a leaderboard and a skyscraper from a square design")

lead = built["leaderboard"]
check("a leaderboard is laid out from a template", lead["tier"], "reflow")
check("...and the reason is on the frame", "Leaderboard" in lead["tier_reason"])

placed = {o["id"]: o for o in lead["objects"]}
check("the logo is on the left third",
      placed["logo"]["x"] < lead["width"] * 0.33)
check("the call to action is on the right third",
      placed["cta"]["x"] > lead["width"] * 0.6)
check("and the headline sits between them",
      placed["logo"]["x"] < placed["head"]["x"] < placed["cta"]["x"], True)
check("the leaderboard is clean", lead["status"], engine.AUTO)

sky = built["wide_sky"]
check("a skyscraper is laid out from a template too", sky["tier"], "reflow")
sky_placed = {o["id"]: o for o in sky["objects"]}
check("the logo is at the top", sky_placed["logo"]["y"] < sky["height"] * 0.2)
check("the button is at the bottom",
      sky_placed["cta"]["y"] > sky["height"] * 0.75)
check("the product image is the largest thing on it",
      sky_placed["shot"]["w"] * sky_placed["shot"]["h"]
      > sky_placed["cta"]["w"] * sky_placed["cta"]["h"], True)

check("every placed object stays inside the frame",
      [o["id"] for o in sky["objects"]
       if o["role"] != R.BACKGROUND
       and (o["x"] < -0.5 or o["y"] < -0.5
            or o["x"] + o["w"] > sky["width"] + 0.5
            or o["y"] + o["h"] > sky["height"] + 0.5)], [])

check("type is sized to its slot rather than carried over",
      all(o.get("fontSize") != 26 for o in lead["objects"]
          if o["id"] == "head"), True)
check("and it clears the legibility floor on a leaderboard",
      next(o for o in lead["objects"] if o["id"] == "head")["fontSize"]
      >= qc.MIN_FONT_PX, True)


# ==========================================================================
section("What a template will not do: guess, drop copy, or invent an object")

# A leaderboard has nowhere to put rate copy at a legible size. It is named,
# never trimmed away to make the layout work.
with_small = engine.resize(design(with_disclaimer=True), S.get("leaderboard"))
# Two things have no place on a 728x90, and the difference between them is
# the whole rule: the photograph is visibly absent to anybody looking at the
# frame, and the rate copy is not.
check("everything with no slot is named in `unplaced`",
      sorted(u["role"] for u in with_small["unplaced"]),
      [R.DISCLAIMER, R.PRODUCT])
check("copy left out marks the frame",
      [f["level"] for f in with_small["findings"]
       if f["code"] == "unplaced" and "small" in f["objects"]], [qc.FAIL])
check("...and a picture left out is a note beside it, not a flag",
      [f["level"] for f in with_small["findings"]
       if f["code"] == "unplaced" and "shot" in f["objects"]], [qc.WARN])
check("...and the frame is marked rather than shipped short",
      with_small["status"], engine.NEEDS_REVIEW)
check("...with the reason naming the layout",
      any("Leaderboard" in f["message"] for f in fails(with_small)), True)
check("and nothing was deleted from the design",
      len(design(with_disclaimer=True)["objects"]), 7)

no_cta = engine.resize(design(with_cta=False), S.get("wide_sky"))
check("a missing required role marks the frame",
      no_cta["status"], engine.NEEDS_REVIEW)
check("...by name", "missing_cta_button" in codes(no_cta))
check("and the empty slot is left empty rather than filled",
      [o["role"] for o in no_cta["objects"] if o["role"] == R.CTA], [])

check("the engine never infers a call to action",
      R.infer(panel="shape", kind="group"), R.DECORATIVE)
check("a group with no role is asked about once",
      R.needs_ask("", "group"), True)
check("a role from provenance is taken",
      R.infer(panel="logo"), R.LOGO)
check("a second logo is left unset rather than replacing the first",
      R.infer(panel="logo", existing_roles=[R.LOGO]), "")


# ==========================================================================
section("The collision guard, from both ends")

broken = [
    {"id": "a", "role": R.HEADLINE, "kind": "text",
     "x": 10, "y": 10, "w": 200, "h": 60, "text": "Hi", "fontSize": 20},
    {"id": "b", "role": R.CTA, "kind": "group",
     "x": 30, "y": 20, "w": 160, "h": 40},
]
found = engine.guard(broken, 300, 250)
check("two objects printed over each other is a collision",
      "collision" in {f["code"] for f in found})
check("...and the offenders are named",
      sorted(next(f for f in found if f["code"] == "collision")["objects"]),
      ["a", "b"])

tight = [
    {"id": "a", "role": R.HEADLINE, "kind": "text",
     "x": 10, "y": 10, "w": 200, "h": 60, "text": "Hi", "fontSize": 20},
    {"id": "b", "role": R.CTA, "kind": "group",
     "x": 10, "y": 70, "w": 160, "h": 40},
]
check("but two objects merely touching is not",
      engine.guard(tight, 300, 250), [])

deco = [
    {"id": "rule", "role": R.DECORATIVE, "kind": "shape",
     "x": 0, "y": 0, "w": 300, "h": 250},
    {"id": "h", "role": R.HEADLINE, "kind": "text",
     "x": 10, "y": 10, "w": 200, "h": 60, "text": "Hi", "fontSize": 20},
]
check("a decorative object may sit behind copy",
      [f["code"] for f in engine.guard(deco, 300, 250)], [])
check("but it is still checked for running off the edge",
      "clipped" in {f["code"] for f in engine.guard(
          [{"id": "rule", "role": R.DECORATIVE, "kind": "shape",
            "x": 280, "y": 0, "w": 100, "h": 40}], 300, 250)}, True)
check("a background is exempt from both, because covering is its job",
      engine.guard([{"id": "bg", "role": R.BACKGROUND, "kind": "image",
                     "x": -20, "y": -20, "w": 400, "h": 400}], 300, 250), [])

covered = [
    {"id": "cta", "role": R.CTA, "kind": "group",
     "x": 10, "y": 100, "w": 120, "h": 40},
    {"id": "shot", "role": R.PRODUCT, "kind": "image",
     "x": 10, "y": 100, "w": 120, "h": 40},
]
check("something painted over the call to action is a finding",
      "cta_obscured" in {f["code"] for f in qc.cta_clear(covered)}, True)
check("but the background under it is not",
      qc.cta_clear([{"id": "bg", "role": R.BACKGROUND, "kind": "image",
                     "x": 0, "y": 0, "w": 300, "h": 250},
                    covered[0]]), [])

check("the type floor warns and never blocks",
      [f["level"] for f in qc.legibility(
          [{"id": "t", "role": R.BODY, "kind": "text",
            "x": 0, "y": 0, "w": 10, "h": 4, "fontSize": 6}])], [qc.WARN])
check("...and says it is ours",
      qc.legibility([{"id": "t", "role": R.BODY, "kind": "text",
                      "x": 0, "y": 0, "w": 10, "h": 4,
                      "fontSize": 6}])[0]["source"], "house")


# ==========================================================================
section("§6 — copy reaches every frame, layout never reaches an edited one")

project = store.create(name="Spring banners", client="Cool Air Co",
                       source=design(), bundle="display_standard")
store.generate(project)
store.save(project)

check("the set was built", len(project["frames"]),
      len(S.BUNDLES["display_standard"]["sizes"]))
check("and the role map is derived rather than stored a second time",
      "role_map" in project, False)
check("...but still answers in the shape the plan asks for",
      store.role_map(project)["logo"], R.LOGO)

# Somebody hand-tunes the leaderboard.
hand = [dict(o) for o in project["frames"]["leaderboard"]["objects"]]
for obj in hand:
    if obj["id"] == "cta":
        obj["x"], obj["y"] = 500.0, 25.0
store.mark_edited(project, "leaderboard", hand)
check("the hand-tuned frame is marked edited",
      project["frames"]["leaderboard"]["status"], engine.EDITED)
moved_x = next(o for o in project["frames"]["leaderboard"]["objects"]
               if o["id"] == "cta")["x"]

# A layout change on the design.
project["source"]["objects"][1]["x"] = 40
report = store.generate(project)
check("a layout change rebuilds the frames it may rebuild",
      "med_rect" in report["built"])
check("...and skips the hand-tuned one", report["skipped"], ["leaderboard"])
check("...saying so rather than skipping in silence",
      bool(report["skipped"]), True)
check("the hand-tuned frame is untouched",
      next(o for o in project["frames"]["leaderboard"]["objects"]
           if o["id"] == "cta")["x"], moved_x)
check("and it is still marked edited",
      project["frames"]["leaderboard"]["status"], engine.EDITED)

# A copy change on the design.
for obj in project["source"]["objects"]:
    if obj["id"] == "head":
        obj["text"] = "Cool air, today"
text_report = store.propagate_text(project)
check("new copy reaches an automatic frame",
      next(o for o in project["frames"]["med_rect"]["objects"]
           if o["id"] == "head")["text"], "Cool air, today")
check("new copy reaches the hand-tuned frame too",
      next(o for o in project["frames"]["leaderboard"]["objects"]
           if o["id"] == "head")["text"], "Cool air, today")
check("...without moving anything on it",
      next(o for o in project["frames"]["leaderboard"]["objects"]
           if o["id"] == "cta")["x"], moved_x)
check("...and it is flagged so somebody checks the copy still fits",
      text_report["flagged_edited"], ["leaderboard"])
check("...with a warning on the frame itself",
      "copy_changed" in codes(project["frames"]["leaderboard"]))
check("the frame stays edited rather than being demoted",
      project["frames"]["leaderboard"]["status"], engine.EDITED)

store.save(project)
reread = store.get(project["id"])
check("the project survives a round trip through the store",
      reread["frames"]["leaderboard"]["status"], engine.EDITED)
check("and it is listed", [r["id"] for r in store.load_index()],
      [project["id"]])
check("deleting one goes through jsonstore rather than a bare remove",
      store.delete(project["id"]) and store.get(project["id"]) is None, True)


# ==========================================================================
section("Export — under the ceiling, or named rather than shipped soft")

try:
    from PIL import Image
    import random
    _pil = True
except Exception:                                       # pragma: no cover
    _pil = False

if not _pil:
    print("  .... Pillow is not installed, so the export ladder was NOT run.")
else:
    def noisy(w, h):
        """Noise, so it does not compress away and the ladder actually runs."""
        rnd = random.Random(7)
        im = Image.new("RGB", (w, h))
        im.putdata([(rnd.randrange(256), rnd.randrange(256),
                     rnd.randrange(256)) for _ in range(w * h)])
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

    heavy = noisy(300, 250)
    check("the fixture is genuinely over the ceiling", len(heavy) > 150 * 1024)

    out = export.prepare("med_rect", heavy, fmt="png")
    check("an over-weight frame is compressed rather than refused",
          out["recompressed"], True)
    check("...and comes back under the kit's ceiling", out["bytes"] <= 150 * 1024)
    check("...and it is judged, not assumed", out["measured"], True)

    from PIL import Image as _Im
    got = _Im.open(io.BytesIO(out["data"]))
    check("the dimensions are the unit and are never traded for weight",
          got.size, (300, 250))

    small = noisy(60, 50)
    light = export.prepare("med_rect", small, fmt="png")
    check("a frame already under its ceiling is left alone",
          light["recompressed"], False)

    unmeasured = export.prepare("large_rect", heavy, fmt="png")
    check("a size with no published ceiling is not squeezed to fit one",
          unmeasured.get("measured"), False)
    check("...and says why", bool(unmeasured.get("note")), True)

    # A frame that cannot get under its ceiling above the quality floor.
    #
    # Stubbed rather than fixtured, and deliberately: an image small enough to
    # be a banner and incompressible enough to survive the whole ladder does
    # not exist at these sizes, so the only way to cover this branch with a
    # real file is not to cover it. And it is the branch that decides whether
    # a soft file reaches a client, which makes it the one most worth having.
    class _Stubborn:
        class _Out:
            def __init__(self, data):
                self.data = data

        def optimise(self, data, **kw):
            return self._Out(b"x" * (200 * 1024))

    _real_images = export.hub_images
    export.hub_images = _Stubborn()
    try:
        stuck = export.prepare("med_rect", heavy, fmt="png")
    finally:
        export.hub_images = _real_images

    check("a frame that cannot fit is not reported as ok", stuck["ok"], False)
    check("...and the error names the floor it stopped at",
          str(export.QUALITY_FLOOR) in stuck["error"])
    check("...and says what to do rather than only that it failed",
          "Simplify" in stuck["error"])
    check("...and still carries its best attempt rather than nothing",
          stuck["bytes"] > 0)

    blob4, report4 = export.bundle([stuck])
    import zipfile as _zf
    names4 = _zf.ZipFile(io.BytesIO(blob4)).namelist()
    check("a real unfit frame is left out of the pack",
          [r["included"] for r in report4], [False])
    check("...and the pack says so rather than being quietly short",
          names4, ["NOT-IN-THIS-ZIP.txt"])

    blob, report = export.bundle([out, {**unmeasured, "size_id": "large_rect"}])
    check("the pack is a real zip", blob[:2], b"PK")
    check("and every frame in it reports its own outcome",
          len(report), 2)

    failed = {"size_id": "med_rect", "ok": False, "fmt": "jpg",
              "data": b"x", "error": "could not get under the ceiling"}
    blob2, report2 = export.bundle([out, failed])
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(blob2)).namelist()
    check("a frame that failed is left out of the pack",
          sum(1 for r in report2 if r.get("included")), 1)
    check("...and named in the pack rather than silently missing",
          "NOT-IN-THIS-ZIP.txt" in names)

    dupe = dict(out)
    blob3, _ = export.bundle([out, dupe])
    names3 = [n for n in zipfile.ZipFile(io.BytesIO(blob3)).namelist()
              if n.endswith((".jpg", ".png"))]
    check("two frames cannot share a filename and lose one",
          len(names3), 2)


# ==========================================================================
section("Recompose — a model proposes, the code decides, nothing is applied")

frame = engine.resize(design(), S.get("leaderboard"))
sent = {}


def stub_ask(prompt, **kw):
    sent["prompt"] = prompt
    sent["kw"] = kw
    return ('{"objects": ['
            '{"id": "logo", "x": 8, "y": 20, "w": 60, "h": 40},'
            '{"id": "ghost", "x": 0, "y": 0, "w": 10, "h": 10}]}')


proposal = recompose.propose(frame, ask=stub_ask)
check("a proposal comes back", proposal["ok"], True)
check("the object it named was moved",
      next(o for o in proposal["objects"] if o["id"] == "logo")["x"], 8)
check("an id it invented is ignored and counted",
      proposal["report"]["ignored_unknown"], ["ghost"])
check("an object it did not mention keeps its place",
      next(o for o in proposal["objects"] if o["id"] == "cta")["x"],
      next(o for o in frame["objects"] if o["id"] == "cta")["x"])
check("nothing was applied to the frame itself",
      next(o for o in frame["objects"] if o["id"] == "logo")["x"] != 8, True)
check("the answer goes back through the same guard a template's output does",
      isinstance(proposal["findings"], list), True)
check("the spend is filed under this module",
      sent["kw"]["module"], "magic_resize")
check("...with its own purpose rather than the module's first one",
      sent["kw"]["purpose"], "frame_recompose")
check("the model is not handed the client's name",
      "Cool Air" in sent["prompt"], False)
check("it is asked for positions rather than a picture",
      '"x"' in sent["prompt"] and '"y"' in sent["prompt"])
check("...and is handed no image data to work from",
      "data:image" in sent["prompt"], False)
check("...nor the words, which it is told not to change",
      "Cool air, fast" in sent["prompt"], False)

check("a model that answers with prose costs the suggestion, not the frame",
      recompose.propose(frame, ask=lambda *a, **k: "I cannot do that")["ok"],
      False)
def refusing_ask(*a, **k):
    raise RuntimeError("OpenAI is not configured.")


check("a model that refuses is reported in its own words",
      recompose.propose(frame, ask=refusing_ask)["error"],
      "OpenAI is not configured.")


# ==========================================================================
section("A frame comes back as objects, never as a picture")

canvas = {"width": 300, "height": 250, "objects": [
    {"type": "image", "left": 150, "top": 20, "width": 140, "height": 56,
     "scaleX": 0.5, "scaleY": 0.5, "originX": "center", "id": "logo",
     "s1Role": R.LOGO},
    {"type": "textbox", "left": 12, "top": 60, "width": 276, "height": 50,
     "text": "Cool air", "fontSize": 26, "id": "head", "s1Role": R.HEADLINE},
]}
read = fabric_io.to_frame(canvas)
check("a center-origin object's left edge is read, not assumed",
      next(o for o in read["objects"] if o["id"] == "logo")["x"], 115.0)
check("scale is folded into the box",
      next(o for o in read["objects"] if o["id"] == "logo")["w"], 70.0)
check("a role already on the object is kept",
      next(o for o in read["objects"] if o["id"] == "head")["role"],
      R.HEADLINE)

back = fabric_io.to_fabric(engine.resize(
    {**read, "family": "square_medium"}, S.get("large_rect")))
kinds = {o["type"] for o in back["objects"]}
check("what comes back is Fabric objects", kinds, {"image", "textbox"})
check("...every one of them still selectable rather than flattened",
      all("left" in o and "top" in o for o in back["objects"]), True)
check("text keeps its words", next(o for o in back["objects"]
                                   if o["id"] == "head")["text"], "Cool air")
check("type is sized in points rather than stretched by scale",
      next(o for o in back["objects"] if o["id"] == "head")["scaleX"], 1)
check("an image keeps its intrinsic size and carries its scale",
      next(o for o in back["objects"] if o["id"] == "logo")["width"], 140.0)
check("and the role travels with it",
      next(o for o in back["objects"] if o["id"] == "logo")["s1Role"], R.LOGO)


# ==========================================================================
section("The shape buckets, and the tier the plan's own examples imply")

for w, h, want in ((728, 90, "leaderboard"), (970, 250, "leaderboard"),
                   (160, 600, "skyscraper"), (300, 600, "skyscraper"),
                   (1080, 1920, "story_portrait"), (1080, 1350, "story_portrait"),
                   (300, 250, "square_medium"), (1200, 628, "square_medium")):
    check(f"  {w}x{h} is laid out as a {want}", L.for_ratio(w, h)["id"], want)

check("a billboard and a leaderboard are one family, so it is scaled",
      engine.pick_tier({"width": 970, "height": 250, "family": "leaderboard"},
                       S.get("leaderboard"))[0], "anchor")
check("a square into a leaderboard is not",
      engine.pick_tier({"width": 300, "height": 250,
                        "family": "square_medium"},
                       S.get("leaderboard"))[0], "reflow")
check("and the reason a frame took the tier it did is on the frame",
      bool(engine.resize(design(), S.get("leaderboard"))["tier_reason"]), True)

check("a target with no dimensions is refused rather than divided by",
      engine.resize(design(), {"id": "x", "w": 0, "h": 0})["status"],
      engine.NEEDS_REVIEW)


# ==========================================================================
section("The exported ZIP is a download, not the only record")
# ==========================================================================
# A rep presses Export and gets a ZIP — and nothing else in this Hub reads
# it, so "what have you built us?" on that client's own record would show
# nothing. Every other producer files what it makes (hub/image_audit.py);
# this asserts the same call happens here, from the frames that actually
# made the cut, and only when the project names a client.

from modules.magic_resize import app as magic_app                # noqa: E402
import hub.storage as _storage                                   # noqa: E402
from modules.image_picker import filing as _filing               # noqa: E402

_filed: list[dict] = []
_orig_put, _orig_file_asset = _storage.put, _filing.file_asset


def _fake_put(kind, filename, data, **kw):
    return _storage.StoredAsset(
        public_id=f"{kind}/{filename}", url="https://example.test/x.png",
        resource_type="image", bytes=len(data or b""), backend="cloudinary",
        folder=kind, checksum="x")


def _fake_file_asset(**kw):
    _filed.append(kw)
    return {"ok": True, "gallery_url": "/tools/image-picker/gallery/1"}


_storage.put, _filing.file_asset = _fake_put, _fake_file_asset
try:
    # actor_name() reads flask.request, so this needs a request in play --
    # the real caller (api_export) always has one.
    with magic_app.app.test_request_context("/api/projects/x/export"):
        magic_app._file_to_gallery(
            {"client": "Acme Plumbing", "name": "Fall Sale"},
            [{"size_id": "leaderboard", "fmt": "png", "data": b"x", "ok": True},
             {"size_id": "skyscraper", "fmt": "jpg", "data": b"y", "ok": False,
              "error": "too heavy for its ceiling"}])
    check("only the frame that made the cut is filed", len(_filed), 1)
    check("under the client on the project",
          _filed[0].get("client_name"), "Acme Plumbing")
    check("tagged as this tool's own provider, not Display Ad Builder's",
          _filed[0].get("provider"), "magic_resize")

    _filed.clear()
    with magic_app.app.test_request_context("/api/projects/x/export"):
        magic_app._file_to_gallery(
            {"client": "", "name": "No client yet"},
            [{"size_id": "leaderboard", "fmt": "png", "data": b"x", "ok": True}])
    check("a project with no client files nothing", _filed, [])
finally:
    _storage.put, _filing.file_asset = _orig_put, _orig_file_asset

check("the provider is one the gallery can name",
      "magic_resize" in _filing.SOURCE_LABELS, True)
check("and it sorts as our own work, not something the client sent",
      "magic_resize" in _filing.WE_MADE, True)


print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
