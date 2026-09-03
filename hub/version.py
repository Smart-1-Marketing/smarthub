"""Single source of truth for the running build.

Shown in the footer of every Hub page and on each module page, so you can
open the deployed site and confirm at a glance that what's running is what
you last pushed.

Bump VERSION whenever code is deployed. BUILD_DATE is the date of that bump.
"""
import os
import subprocess

VERSION = "1.65.0"
BUILD_DATE = "2026-09-03"
CODENAME = "Hub Gap Work Orders"

_sha_cache: str | None = None


def git_sha() -> str:
    """Short commit hash when the deploy has git available (Render sets
    RENDER_GIT_COMMIT); blank otherwise. Never raises."""
    global _sha_cache
    if _sha_cache is not None:
        return _sha_cache
    sha = (os.environ.get("RENDER_GIT_COMMIT")
           or os.environ.get("GIT_COMMIT") or "").strip()
    if not sha:
        try:
            sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                capture_output=True, text=True, timeout=3).stdout.strip()
        except Exception:                 # noqa: BLE001 — never break a page
            sha = ""
    _sha_cache = sha[:7]
    return _sha_cache


def label() -> str:
    """e.g. 'v1.5.0 · 2026-08-17 · a1b2c3d'"""
    parts = [f"v{VERSION}", BUILD_DATE]
    sha = git_sha()
    if sha:
        parts.append(sha)
    return " · ".join(parts)


def info() -> dict:
    return {"version": VERSION, "build_date": BUILD_DATE,
            "codename": CODENAME, "git_sha": git_sha(), "label": label()}
