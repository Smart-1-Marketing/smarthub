"""Smart 1 PDF Optimizer — Flask port of the original FastAPI service.

Behaviour is identical: Ghostscript compresses images/fonts, qPDF linearizes,
the result is never larger than the original, and temp files are removed
after the download response is sent.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flask import Flask, after_this_request, jsonify, request, send_file

# Activity logging — so this tool's output appears on the client's
# 360 record. This module processes client documents, and work that isn't logged is work
# nobody can point to later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("pdf_optimizer")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


app = Flask(__name__)

STATIC_DIR = Path(__file__).parent / "static"

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 1024  # headroom for form overhead

PROFILES = {
    "maximum": {"color_dpi": 96, "gray_dpi": 96, "mono_dpi": 200, "jpeg_quality": 65},
    "web": {"color_dpi": 150, "gray_dpi": 150, "mono_dpi": 300, "jpeg_quality": 82},
    "high": {"color_dpi": 220, "gray_dpi": 220, "mono_dpi": 400, "jpeg_quality": 90},
}


@app.route("/")
def home():
    return send_file(STATIC_DIR / "index.html")


@app.route("/health")
def health():
    """Whether this tool can actually do its one job.

    It answered {"status": "ok"} unconditionally, and this tool is a wrapper
    around two binaries: with Ghostscript missing, every optimize returns an
    error and the health probe went on saying ok. The Hub's own /status has
    known that all along -- it reports "Ghostscript / qPDF" as an error when
    either is absent -- so the module's health and the status page were two
    answers to one question, and the module's was the confident wrong one.
    Same fact, read the same way, so the two cannot disagree.
    """
    gs, qpdf = shutil.which("gs"), shutil.which("qpdf")
    ready = bool(gs and qpdf)
    missing = [n for n, p in (("gs", gs), ("qpdf", qpdf)) if not p]
    return jsonify({
        "status": "ok" if ready else "degraded",
        "ready": ready,
        "ghostscript": bool(gs),
        "qpdf": bool(qpdf),
        "detail": "PDF optimizer ready."
        if ready else
        "Missing " + " and ".join(missing) + " — optimizing will fail. The "
        "Docker image installs both; see Ghostscript / qPDF on /status.",
    })


def _run(command: list[str], timeout: int) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "Unknown processing error").strip()
        raise RuntimeError(message[-2000:])


@app.route("/optimize", methods=["POST"])
def optimize_pdf():
    quality = request.form.get("quality", "web")
    if quality not in PROFILES:
        return jsonify({"detail": "Invalid optimization level."}), 400

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"detail": "A PDF file is required."}), 400

    filename = Path(upload.filename or "document.pdf").name
    if not filename.lower().endswith(".pdf"):
        return jsonify({"detail": "Only PDF files are supported."}), 400

    temp_dir = tempfile.mkdtemp(prefix="smart1_pdf_")
    input_path = Path(temp_dir) / "input.pdf"
    gs_path = Path(temp_dir) / "ghostscript.pdf"
    output_path = Path(temp_dir) / "optimized.pdf"

    def _cleanup():
        shutil.rmtree(temp_dir, ignore_errors=True)

    try:
        # Stream the upload to disk with a size cap.
        total = 0
        with input_path.open("wb") as out:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    _cleanup()
                    return jsonify({"detail": f"PDF exceeds the {MAX_UPLOAD_MB} MB upload limit."}), 413
                out.write(chunk)

        if total < 5:
            _cleanup()
            return jsonify({"detail": "The uploaded PDF is empty."}), 400
        with input_path.open("rb") as src:
            if src.read(5) != b"%PDF-":
                _cleanup()
                return jsonify({"detail": "The uploaded file is not a valid PDF."}), 400

        profile = PROFILES[quality]
        _run([
            "gs", "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dNOPAUSE", "-dBATCH",
            "-dSAFER", "-dDetectDuplicateImages=true", "-dCompressFonts=true", "-dSubsetFonts=true",
            "-dAutoRotatePages=/None",
            "-dDownsampleColorImages=true", "-dColorImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={profile['color_dpi']}",
            "-dDownsampleGrayImages=true", "-dGrayImageDownsampleType=/Bicubic",
            f"-dGrayImageResolution={profile['gray_dpi']}",
            "-dDownsampleMonoImages=true", "-dMonoImageDownsampleType=/Subsample",
            f"-dMonoImageResolution={profile['mono_dpi']}",
            f"-dJPEGQ={profile['jpeg_quality']}",
            f"-sOutputFile={gs_path}", str(input_path),
        ], timeout=180)

        try:
            _run(["qpdf", "--linearize", "--object-streams=generate", str(gs_path), str(output_path)], timeout=90)
        except Exception:  # noqa: BLE001 — linearization is best-effort
            shutil.copy2(gs_path, output_path)

        original_size = input_path.stat().st_size
        optimized_size = output_path.stat().st_size
        if optimized_size >= original_size:
            shutil.copy2(input_path, output_path)
            optimized_size = original_size
        savings = ((original_size - optimized_size) / original_size * 100) if original_size else 0.0

        # The row this module has always described in its own docstring and
        # never written: _audit was imported, wrapped in a no-op fallback and
        # called nowhere, so every document it has ever compressed was work
        # nobody could point to. Logged here rather than at the top of the
        # route, so a PDF that failed Ghostscript is not filed as delivered
        # work -- the rule approve_render arrived at for a mock render.
        #
        # Every value is a plain read, never an expression: audit.log swallows
        # what it is *given*, and a caller that raises while building its own
        # arguments is the failure that 500'd every Commercial Builder render.
        _audit("pdf_optimized", quality=quality, original_bytes=original_size,
               optimized_bytes=optimized_size, saved_percent=round(savings, 1))

        @after_this_request
        def _remove(response):
            _cleanup()
            return response

        resp = send_file(
            output_path, mimetype="application/pdf", as_attachment=True,
            download_name=f"{Path(filename).stem}-optimized.pdf",
        )
        resp.headers["X-Original-Size"] = str(original_size)
        resp.headers["X-Optimized-Size"] = str(optimized_size)
        resp.headers["X-Savings-Percent"] = f"{savings:.1f}"
        resp.headers["Access-Control-Expose-Headers"] = (
            "Content-Disposition, X-Original-Size, X-Optimized-Size, X-Savings-Percent"
        )
        return resp

    except subprocess.TimeoutExpired:
        _cleanup()
        return jsonify({"detail": "PDF optimization timed out."}), 408
    except FileNotFoundError:
        # Ghostscript or qPDF is not on PATH. The Docker image installs both,
        # so this is a broken deployment rather than a bad document -- and
        # "[Errno 2] No such file or directory: 'gs'" tells whoever uploaded
        # the PDF nothing they can act on. /status carries the same fact under
        # "Ghostscript / qPDF", which is where somebody can do something
        # about it, so the message points there rather than restating it.
        app.logger.exception("PDF optimizer: gs/qpdf missing")
        _cleanup()
        return jsonify({"detail": "The PDF tools are not available on this "
                                  "server. Check Ghostscript / qPDF on the "
                                  "status page."}), 503
    except Exception:  # noqa: BLE001
        # Never the exception text. _run() raises RuntimeError carrying the
        # last 2000 characters of Ghostscript's stderr, which is absolute
        # temp-directory paths and the uploader's own filename, handed
        # straight to a browser. The rule modules/fan_radio.fail() states:
        # no provider bodies and no tracebacks on a screen. The cause goes to
        # the log, with the traceback, where it is diagnosable.
        app.logger.exception("PDF optimization failed")
        _cleanup()
        return jsonify({"detail": "That PDF could not be optimized. It may be "
                                  "encrypted, damaged, or an unusual format."}), 500
