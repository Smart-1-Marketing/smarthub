"""Inspect downloaded output bytes, with bounded network and decoder work."""
import ipaddress
import json
import math
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import urlparse, urljoin

import requests

from ..db import db
from ..finishing_models import RenderInspection
from ..config import OUTPUT_FORMATS

MAX_BYTES = 150 * 1024 * 1024


def _allowed(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    # Only our render/storage providers and the configured renderer can supply
    # a file. No arbitrary client URL is passed to a decoder or fetched here.
    configured = urlparse(os.environ.get("HF_RENDER_SERVICE_URL", "")).hostname
    trusted = (host == "res.cloudinary.com" or host == "creatomate.com"
               or host.endswith(".creatomate.com") or host.endswith(".creatomateusercontent.com")
               or bool(configured and host == configured))
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or not trusted:
        raise ValueError("The finished file is not on a supported render or storage host.")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("The finished file must be on a public media host.")


def _download(url, path):
    deadline = time.monotonic() + 15
    with requests.Session() as session:
        session.trust_env = False
        for _ in range(4):
            _allowed(url)
            with session.get(url, stream=True, allow_redirects=False, timeout=(5, 5)) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                response.raise_for_status()
                size = 0
                with open(path, "wb") as target:
                    for block in response.iter_content(65536):
                        size += len(block)
                        if size > MAX_BYTES or time.monotonic() > deadline:
                            raise ValueError("The finished file exceeded the inspection size or time limit.")
                        target.write(block)
                if not size:
                    raise ValueError("The finished file is empty.")
                return
    raise ValueError("The finished file redirected too many times.")


def evaluate(probe, expected, decoded=True, content_log=""):
    streams = probe.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    try:
        duration = float((probe.get("format") or {}).get("duration", 0))
        if not math.isfinite(duration):
            duration = 0
    except (ValueError, TypeError):
        duration = 0
    checks = [
        {"label": "Playable video", "passed": bool(video) and decoded,
         "detail": "Video stream decodes without errors." if video and decoded else "Video is missing or damaged."},
        {"label": "Duration", "passed": duration > 0 and abs(duration - expected.get("duration", 0)) <= .5,
         "detail": f"Measured {duration:.2f}s; expected {expected.get('duration', 0)}s (±0.5s)."},
        {"label": "Resolution", "passed": (video.get("width"), video.get("height")) == (expected.get("width"), expected.get("height")),
         "detail": f"Measured {video.get('width', 0)} × {video.get('height', 0)}; expected {expected.get('width')} × {expected.get('height')}."},
        {"label": "Audio stream", "passed": bool(audio) or not expected.get("audio_required", True),
         "detail": "Audio stream present." if audio else "No audio stream in the finished file."},
    ]
    warnings = []
    for start, end in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", content_log):
        if float(end) - float(start) >= .75:
            warnings.append(f"Black picture from {float(start):.1f}s to {float(end):.1f}s: check for missing footage or an intentional fade.")
    for end, span in re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", content_log):
        if float(span) >= 2:
            warnings.append(f"Quiet audio for {float(span):.1f}s ending at {float(end):.1f}s: check for missing speech or intentional silence.")
    status = "failed" if not all(c["passed"] for c in checks) else "review" if warnings else "passed"
    return {"status": status, "checks": checks, "warnings": warnings[:12],
            "note": "Watch the cut to verify lip-sync, pronunciation, captions, missing visual content and audio quality. Stream checks cannot verify those."}


def expected_output(project, fmt, scenes):
    dims = next((f for f in OUTPUT_FORMATS if f["id"] == fmt), {})
    return {"duration": project.length_seconds, "width": dims.get("width"), "height": dims.get("height"),
            "creative_key": creative_key(project, scenes),
            "audio_required": bool(any(s.get("narration") for s in scenes) or (project.music or {}).get("music_track_url"))}


def creative_key(project, scenes):
    from .media_state import fingerprint
    return fingerprint({"duration": project.length_seconds, "platform": project.platform,
                        "music": project.music, "cta": project.cta,
                        "scenes": [{k: v for k, v in s.items() if k not in ("locked", "project_id")} for s in scenes]})


def inspect_job(job, *, force=False):
    row = db.session.get(RenderInspection, job.id)
    if not row:
        # Old jobs have no render-time expectation; current editable inputs
        # cannot establish which duration the old output was submitted for.
        return {"status": "unverified", "checks": [], "note": "This older render has no saved output specification. Watch it before filing, or render a new cut for automatic verification."}
    if row.checked_at and row.output_url == job.output_url and not force:
        return row.result
    if job.status != "succeeded" or not job.output_url:
        return {"status": "pending", "checks": [], "note": "Waiting for a finished file."}
    result = {"status": "unverified", "checks": [], "note": "The file could not be inspected. Retry the check or watch it before filing."}
    try:
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            raise ValueError("Video inspection tools are unavailable on this server.")
        with tempfile.TemporaryDirectory(prefix="cb-inspect-") as folder:
            path = os.path.join(folder, "output.mp4")
            _download(job.output_url, path)
            probe = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-f", "mov", "-show_streams", "-show_format", "-of", "json", path],
                                   capture_output=True, text=True, timeout=8, check=True)
            metadata = json.loads(probe.stdout)
            filters = ["-vf", "blackdetect=d=0.75:pic_th=0.98"]
            if any(s.get("codec_type") == "audio" for s in metadata.get("streams", [])):
                filters += ["-af", "silencedetect=noise=-50dB:d=2"]
            decoded = subprocess.run(["ffmpeg", "-v", "info", "-xerror", "-threads", "1", "-protocol_whitelist", "file,pipe", "-f", "mov", "-i", path,
                                      "-map", "0:v:0", "-map", "0:a?", *filters, "-f", "null", "-"],
                                     capture_output=True, text=True, timeout=15)
            result = evaluate(metadata, row.expected, decoded.returncode == 0, decoded.stderr)
    except ValueError as exc:
        result["note"] = str(exc)
    except (requests.RequestException, OSError, subprocess.SubprocessError):
        pass  # Do not expose signed URLs or provider responses in the UI.
    row.result, row.output_url, row.checked_at = result, job.output_url, datetime.utcnow()
    row.status = result["status"]
    db.session.commit()
    return result
