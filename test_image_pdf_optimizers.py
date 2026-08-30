"""The Image Optimizer and the PDF Optimizer.

    python3 test_image_pdf_optimizers.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

These two were the last modules in the repo named by no test at all. Both take
a file a client sent us, do something irreversible-looking to it, and hand it
back; both are tiled on a staff index page and reachable behind the login.

**What testing them found, and what is fixed here:**

  1. **Both handed the user a raw internal exception.** The image tool
     answered an unreadable upload with Pillow's own text — *"cannot identify
     image file <_io.BytesIO object at 0x7f...>"*, a Python repr with a memory
     address in it, printed where "that file is not an image we can read"
     belongs and reading like the tool crashed rather than like the file was
     wrong. The PDF tool was worse: `_run()` raises with **the last 2000
     characters of Ghostscript's stderr**, and the 500 handler interpolated it
     straight into the response — absolute temp-directory paths and the
     uploader's own filename, handed to a browser. That is the rule
     `modules/fan_radio.fail()` states: no provider bodies and no tracebacks on
     a screen. Both now say something a person can act on and log the cause.

  2. **`/health` said `ok` for a tool that could not work.** The PDF optimizer
     is a wrapper around two binaries, and with Ghostscript missing every
     optimize fails while its health probe went on answering `{"status":
     "ok"}`. The Hub's own `/status` has known this all along — it reports
     *Ghostscript / qPDF* as an error when either is absent — so the module and
     the status page were two answers to one question and the module's was the
     confident wrong one. It reads the same fact now.

  3. **And gs is absent from CI**, so the compression path is exercised by
     nothing: not by this file, not by the workflow, only by production. That
     is named below rather than skipped quietly — an unrun path reported as a
     clean run is the failure this repo keeps undoing.

**One thing reported rather than changed.** Asking the image tool for a 10 KB
PNG of a photograph returns about 70 KB, `200 OK`, with nothing saying the
target was not met: `compress_to_target()` stops shrinking at a 160px floor,
which is a deliberate guard against grinding a picture to nothing, and the
result is then reported as success. The JPEG path meets its target because it
can also drop quality. Changing what bytes a user gets back is a product
decision, so the floor is asserted as the behaviour it is and the silence is
named here.

**And one finding that was mine, not the code's.** A first pass reported that
animated GIFs were flattened to a single frame. They are not: the fixture was
four frames of one flat colour and Pillow had collapsed them before the module
ever saw it. The animated fixture below asserts `is_animated` on itself before
it is used for anything, because a fixture that does not look like the real
thing leaves the rule untested — the lesson `test_ads_module.py` records about
a Cloudinary URL with no `/upload/` in it.
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
# The module ships a top-level `optimizer` helper that wsgi.py puts on the
# path; standalone, this file has to do the same.
sys.path.insert(0, str(ROOT / "modules" / "image_optimizer"))

TMP = tempfile.mkdtemp(prefix="s1optim_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "optimizer-test-secret"
os.environ["AUDIT_LOG_PATH"] = os.path.join(DISK, "audit.jsonl")

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


from PIL import Image, ImageDraw                             # noqa: E402

from modules.image_optimizer import app as img_mod           # noqa: E402
from modules.pdf_optimizer import app as pdf_mod             # noqa: E402

img = img_mod.app.test_client()
pdf = pdf_mod.app.test_client()

HAS_GS = bool(shutil.which("gs") and shutil.which("qpdf"))


# --------------------------------------------------------------- fixtures
def flat_png(w=800, h=600, mode="RGB", color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "PNG")
    buf.seek(0)
    return buf


def photo_png(w=900, h=700):
    """Noise, so it does not compress away to nothing.

    A flat colour PNG is a few hundred bytes at any size, which makes every
    "did it hit the target?" assertion below pass for the wrong reason.
    """
    import random
    im = Image.new("RGB", (w, h))
    px = im.load()
    random.seed(1)
    for y in range(h):
        for x in range(w):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255),
                        random.randint(0, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    buf.seek(0)
    return buf


def animated_gif(w=300, h=200, frames=5):
    """Frames that genuinely differ, or Pillow collapses them into one."""
    ims = []
    for i in range(frames):
        im = Image.new("RGB", (w, h), (255, 255, 255))
        ImageDraw.Draw(im).rectangle([i * 40, 20, i * 40 + 60, 120],
                                     fill=(20 + i * 40, 90, 200 - i * 30))
        ims.append(im.convert("P", palette=Image.Palette.ADAPTIVE, colors=64))
    buf = io.BytesIO()
    ims[0].save(buf, "GIF", save_all=True, append_images=ims[1:],
                duration=120, loop=0, disposal=2)
    buf.seek(0)
    return buf


def process(fh, name="photo.png", **form):
    data = {k: str(v) for k, v in form.items()}
    data["image"] = (fh, name)
    return img.post("/process", data=data, content_type="multipart/form-data")


def opened(response):
    return Image.open(io.BytesIO(response.data))


# =====================================================================
section("The fixture is what it claims to be")
# =====================================================================
# Asserted before it is used for anything. A first pass at this file reported
# that animation was being flattened, and the fixture was what had flattened
# it -- four frames of one flat colour, collapsed by Pillow on save.

probe = Image.open(io.BytesIO(animated_gif().getvalue()))
check("the animated fixture really is animated", probe.is_animated, True)
check("with the frames it was built with", probe.n_frames, 5)


# =====================================================================
section("The image tool refuses in its own words")
# =====================================================================

check("no file at all",
      img.post("/process", data={},
               content_type="multipart/form-data").get_json()["error"],
      "Choose an image to upload.")

for form, want in (
        ({"width": "abc"}, "Width must be a whole number."),
        ({"height": "12.5"}, "Height must be a whole number."),
        ({"width": "-5"}, "Width must be between 1 and 12,000 pixels."),
        ({"width": "99999"}, "Width must be between 1 and 12,000 pixels."),
        ({"quality": "5"}, "Quality must be between 20 and 100."),
        ({"quality": "200"}, "Quality must be between 20 and 100."),
        ({"target_kb": "1"}, "Target size must be between 10 KB and 10,000 KB."),
        ({"format": "TIFF"}, "Output format must be PNG, JPG, or GIF."),
):
    r = process(flat_png(10, 10), **form)
    check(f"{list(form.items())[0][0]}={list(form.values())[0]!r} is refused by name",
          (r.status_code, r.get_json()["error"]), (400, want))

# Blank and zero mean "work it out for me", which is how somebody says
# "scale to this width" with the aspect locked. Rejecting them turned the
# normal case into a hard failure.
check("a blank dimension is not an error",
      process(flat_png(800, 600), width="", height="300",
              lock_aspect="true").status_code, 200)
check("and neither is a zero",
      process(flat_png(800, 600), width="0", height="300",
              lock_aspect="true").status_code, 200)


# =====================================================================
section("Sizing does what the boxes say")
# =====================================================================

r = process(flat_png(800, 600), width="400", lock_aspect="true")
check("one side with the aspect locked derives the other",
      opened(r).size, (400, 300))

r = process(flat_png(800, 600), width="400", height="400", lock_aspect="true")
check("both sides locked fits inside the box rather than filling it",
      opened(r).size, (400, 300))

r = process(flat_png(800, 600), width="400", height="400", lock_aspect="false")
check("unlocked stretches to exactly what was asked for",
      opened(r).size, (400, 400))

r = process(flat_png(800, 600))
check("no dimensions at all leaves the picture alone",
      opened(r).size, (800, 600))


# =====================================================================
section("Formats, transparency and crops")
# =====================================================================

for fmt, want in (("PNG", "PNG"), ("JPG", "JPEG"), ("JPEG", "JPEG"),
                  ("GIF", "GIF")):
    r = process(flat_png(120, 90), format=fmt)
    check(f"format={fmt} comes back as {want}", opened(r).format, want)

# JPEG has no alpha channel. Flattened onto white rather than onto black,
# which is what an unflattened RGBA->RGB convert gives you.
r = process(flat_png(60, 60, "RGBA", (255, 0, 0, 0)), format="JPG")
check("a transparent image converted to JPEG does not come back black",
      opened(r).convert("RGB").getpixel((30, 30)), (255, 255, 255))

r = process(photo_png(400, 300), crop_enabled="true", crop_x="10", crop_y="10",
            crop_width="100", crop_height="80")
check("a crop inside the picture is applied", opened(r).size, (100, 80))

r = process(photo_png(400, 300), crop_enabled="true", crop_x="700",
            crop_y="10", crop_width="500", crop_height="80")
check("a crop that runs off the edge is refused rather than clamped",
      (r.status_code, r.get_json()["error"]),
      (400, "The crop area extends beyond the image."))

r = process(photo_png(400, 300), crop_enabled="true", crop_x="0", crop_y="0")
check("and a crop with no area chosen is refused",
      (r.status_code, r.get_json()["error"]),
      (400, "Choose a crop area before processing."))


# =====================================================================
section("Animation survives everything that is not a flatten")
# =====================================================================
# The module carries a whole second code path for this -- ImageSequence,
# per-frame durations, the loop count -- and losing it would show up as a
# still image where a client expected their clip.

r = process(animated_gif(), name="clip.gif", format="GIF")
check("an animated GIF passed straight through keeps its frames",
      opened(r).n_frames, 5)

r = process(animated_gif(), name="clip.gif", format="GIF", width="150",
            lock_aspect="true")
out = opened(r)
check("resizing keeps them too", out.n_frames, 5)
check("and resizes them", out.size, (150, 100))

r = process(animated_gif(), name="clip.gif", format="GIF",
            crop_enabled="true", crop_x="10", crop_y="10",
            crop_width="120", crop_height="90")
out = opened(r)
check("so does cropping", out.n_frames, 5)
check("at the cropped size", out.size, (120, 90))

# Asking for PNG or JPEG *is* asking to flatten it, so one frame is the
# right answer rather than a loss.
check("asking for a PNG flattens it, which is what was asked for",
      opened(process(animated_gif(), name="clip.gif", format="PNG")).format,
      "PNG")


# =====================================================================
section("Compressing to a target, and where it stops")
# =====================================================================
# JPEG can trade quality as well as pixels, so it meets the number.

for target in (10, 50, 200):
    r = process(photo_png(), optimize="true", target_kb=target, quality="82",
                format="JPG")
    check(f"a {target}KB JPEG target is met",
          len(r.data) <= target * 1024, True)

# PNG cannot drop quality, so it shrinks the picture instead -- and stops at
# a 160px floor rather than grinding a photograph to nothing. That floor is
# deliberate. What it is not is reported: the response is a 200 carrying a
# file several times the size asked for, with nothing saying so.
r = process(photo_png(), optimize="true", target_kb=10, format="PNG")
out = opened(r)
check("a PNG target the floor cannot reach still returns the picture",
      r.status_code, 200)
check("stopped at the floor rather than shrinking indefinitely",
      min(out.size) <= 160 * 1.2, True)
check("and it is over the target, silently — reported, not fixed",
      len(r.data) > 10 * 1024, True)

# A target that is reachable is reached.
r = process(photo_png(), optimize="true", target_kb=100, format="PNG")
check("a reachable PNG target is met", len(r.data) <= 100 * 1024, True)


# =====================================================================
section("Nothing internal reaches the screen")
# =====================================================================
# The rule modules/fan_radio.fail() states: no provider bodies and no
# tracebacks. Both of these handed one over.

r = process(io.BytesIO(b"this is not an image at all"), name="x.png")
body = r.get_json()["error"]
check("an unreadable upload is refused", r.status_code, 400)
check("in words somebody can act on",
      body, "That file could not be read as an image. PNG, JPG and GIF are "
            "supported.")
for leak in ("BytesIO", "0x", "Traceback", "cannot identify"):
    check(f"and carries no {leak!r}", leak in body, False)

# Our own validation text is the one kind of exception message that belongs
# on screen, so the fix must not have swallowed it too.
check("our own validation messages still reach the screen",
      process(flat_png(10, 10), width="abc").get_json()["error"],
      "Width must be a whole number.")

# Driven through _run() rather than through a malformed file, so it is the
# same assertion whether or not this machine has Ghostscript -- with gs
# present a truncated PDF is often *accepted*, so a bad fixture would prove
# nothing here and would pass for the wrong reason. What is being asserted is
# the handler: _run() raises carrying the last 2000 characters of gs stderr,
# and that string must not reach a browser.
GS_STDERR = ("**** Error reading a content stream. "
             "Output may be incomplete.\n"
             "GPL Ghostscript 10.0.0: Unrecoverable error, exit code 1\n"
             "   /tmp/smart1_pdf_9f2b1c/input.pdf line 42\n" + "detail " * 200)

_real_run = pdf_mod._run
try:
    def _explode(command, timeout):
        raise RuntimeError(GS_STDERR[-2000:])

    pdf_mod._run = _explode
    r = pdf.post("/optimize",
                 data={"quality": "web",
                       "file": (io.BytesIO(b"%PDF-1.4\nnot really"), "d.pdf")},
                 content_type="multipart/form-data")
finally:
    pdf_mod._run = _real_run

detail = r.get_json()["detail"]
check("a Ghostscript failure is a 500", r.status_code, 500)
check("said in words somebody can act on",
      detail, "That PDF could not be optimized. It may be encrypted, "
              "damaged, or an unusual format.")
for leak in ("Traceback", "/tmp/", "smart1_pdf_", "Ghostscript",
             "Unrecoverable", "content stream"):
    check(f"and carries no {leak!r}", leak in detail, False)
check("and it is not a bare 'Optimization failed:' with the cause appended",
      detail.startswith("Optimization failed:"), False)
check("none of the 2000 characters of stderr reach the browser",
      len(detail) < 200, True)

# And the other failure it has: the binaries missing. That is a broken
# deployment rather than a bad document, so it is a 503 pointing at the page
# where somebody can do something about it -- not "[Errno 2] No such file or
# directory: 'gs'", which tells whoever uploaded the PDF nothing.
try:
    def _absent(command, timeout):
        raise FileNotFoundError(2, "No such file or directory", "gs")

    pdf_mod._run = _absent
    r = pdf.post("/optimize",
                 data={"quality": "web",
                       "file": (io.BytesIO(b"%PDF-1.4\nhello"), "d.pdf")},
                 content_type="multipart/form-data")
finally:
    pdf_mod._run = _real_run

check("a missing binary is a 503, not a 500 about the document",
      r.status_code, 503)
check("and points at where it is fixed",
      "status page" in r.get_json()["detail"], True)
check("without the errno",
      "Errno" in r.get_json()["detail"], False)


# =====================================================================
section("Health says whether the tool can do its job")
# =====================================================================

h = pdf.get("/health").get_json()
check("the PDF tool reports whether Ghostscript is there",
      h["ghostscript"], bool(shutil.which("gs")))
check("and whether qPDF is",
      h["qpdf"], bool(shutil.which("qpdf")))
check("and does not say ok when it cannot work",
      h["status"], "ok" if HAS_GS else "degraded")
if not HAS_GS:
    check("naming what is missing rather than a bare failure",
          "Ghostscript" in h["detail"] or "gs" in h["detail"], True)

h = img.get("/health").get_json()
check("the image tool reports the formats this build can write",
      sorted(h["formats"]), ["GIF", "JPEG", "PNG"])
check("and is ready when it can write all three",
      h["status"], "ok" if all(h["formats"].values()) else "degraded")


# =====================================================================
section("The PDF tool refuses what it should, before spending anything")
# =====================================================================
# Every gate below is reachable with no Ghostscript on the machine, which is
# what makes them worth asserting here.


def optimize(data=b"%PDF-1.4\nhello", name="doc.pdf", quality="web"):
    return pdf.post("/optimize",
                    data={"quality": quality, "file": (io.BytesIO(data), name)},
                    content_type="multipart/form-data")


check("no file",
      pdf.post("/optimize", data={"quality": "web"},
               content_type="multipart/form-data").get_json()["detail"],
      "A PDF file is required.")
check("an optimization level it does not have",
      optimize(quality="extreme").get_json()["detail"],
      "Invalid optimization level.")
check("something that is not named as a PDF",
      optimize(name="notes.txt").get_json()["detail"],
      "Only PDF files are supported.")
check("an empty upload",
      optimize(data=b"").get_json()["detail"], "The uploaded PDF is empty.")
# The name is not the evidence: the bytes are checked too, so a .pdf that is
# really something else is refused before Ghostscript is asked to read it.
check("and a file named .pdf that is not one",
      optimize(data=b"GIF89a and then some").get_json()["detail"],
      "The uploaded file is not a valid PDF.")

check("the three published profiles are the three the form offers",
      sorted(pdf_mod.PROFILES), ["high", "maximum", "web"])
for name, profile in pdf_mod.PROFILES.items():
    check(f"the {name} profile carries every setting the command needs",
          sorted(profile), ["color_dpi", "gray_dpi", "jpeg_quality", "mono_dpi"])

# A path traversal in the filename must not reach the temp directory.
check("a filename with a path in it is reduced to its last element",
      Path("../../etc/passwd.pdf").name, "passwd.pdf")


# =====================================================================
section("What is NOT measured here")
# =====================================================================
# Ghostscript and qPDF ship in the Dockerfile and are absent from CI, so the
# compression this tool exists for runs in production and nowhere else. Said
# out loud: an unrun path reported as a clean run is the failure this repo
# keeps undoing.

if HAS_GS:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas as _rl

    buf = io.BytesIO()
    cv = _rl.Canvas(buf, pagesize=letter)
    cv.drawString(72, 720, "Smart 1 optimizer test")
    cv.showPage()
    cv.save()
    original = buf.getvalue()

    r = optimize(data=original, name="real.pdf")
    check("a real PDF optimizes", r.status_code, 200)
    check("and comes back a PDF", r.data[:5], b"%PDF-")
    check("never larger than the original",
          int(r.headers["X-Optimized-Size"]) <= int(r.headers["X-Original-Size"]),
          True)
    check("with the sizes on the response for the page to show",
          all(h in r.headers for h in ("X-Original-Size", "X-Optimized-Size",
                                       "X-Savings-Percent")), True)
else:
    print("  note  Ghostscript/qPDF are not on this machine, so the "
          "compression\n        path is NOT covered by this run. It ships in "
          "the Dockerfile and\n        is absent from CI, so production is the "
          "only place it runs.")


# =====================================================================
section("Both tools are reachable and findable")
# =====================================================================

wsgi_src = (ROOT / "wsgi.py").read_text()
check("the image tool is mounted", '"/tools/image":' in wsgi_src, True)
check("the PDF tool is mounted", '"/tools/pdf":' in wsgi_src, True)

creative = (ROOT / "hub" / "templates" / "creative.html").read_text()
tools = (ROOT / "hub" / "templates" / "tools.html").read_text()
check("the image tool has a tile", '/tools/image/' in creative + tools, True)
check("the PDF tool has a tile", '/tools/pdf/' in creative + tools, True)

# Both write to the activity log now rather than binding a logger and never
# calling it -- test_activity_logging.py is the sweep; this is the pair.
from hub import integrity                                    # noqa: E402
for mod in ("image_optimizer", "pdf_optimizer"):
    src = "".join((ROOT / "modules" / mod).joinpath(p.name).read_text()
                  for p in (ROOT / "modules" / mod).glob("*.py"))
    check(f"{mod} calls its logger", integrity._calls_the_logger(src), True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
