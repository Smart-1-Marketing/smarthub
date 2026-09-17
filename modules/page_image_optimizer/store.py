"""Where a scan lives between the moment it is run and the moment it is saved.

Deliberately on disk rather than in a module-level dict: the Hub runs more than
one worker, and an in-memory batch would vanish the moment a save landed on a
different worker than the scan. Job metadata is a small JSON file; the image
bytes sit beside it and are swept on a TTL.
"""

import json
import os
import secrets
import shutil
import threading
import time

from . import bytes_store, settings
from hub import jsonstore

_LOCK = threading.Lock()

# Was os.environ.get("HUB_DATA_DIR", "data") — and HUB_DATA_DIR is not set on
# this service, so jobs were landing in ./data inside the container and being
# lost on *every deploy*, not merely if the disk were recreated. jsonstore's
# root reads HUB_DATA_DIR first and falls back to the mounted /var/data, which
# is what every other module already resolved to.
DATA_DIR = os.environ.get("PAGE_IMAGES_DATA_DIR",
                          jsonstore.data_dir("page_image_optimizer"))


def _job_dir(job_id):
    return os.path.join(DATA_DIR, job_id)


def _meta_path(job_id):
    return os.path.join(_job_dir(job_id), "job.json")


def new_id():
    return secrets.token_urlsafe(12)


def _valid_id(job_id):
    return bool(job_id) and job_id.replace("-", "").replace("_", "").isalnum()


# --------------------------------------------------------------------------- #

def create_job(payload):
    job_id = new_id()
    payload = dict(payload)
    payload["id"] = job_id
    payload["created"] = time.time()
    payload["updated"] = time.time()
    payload.setdefault("saved", [])
    payload.setdefault("batches", {})
    with _LOCK:
        os.makedirs(_job_dir(job_id), exist_ok=True)
        _write(job_id, payload)
    sweep()
    return payload


def _write(job_id, payload):
    payload["updated"] = time.time()
    jsonstore.write_json(_meta_path(job_id), payload)


def load_job(job_id):
    if not _valid_id(job_id):
        return None
    return jsonstore.read_json(_meta_path(job_id), default=None)


def save_job(job):
    with _LOCK:
        os.makedirs(_job_dir(job["id"]), exist_ok=True)
        _write(job["id"], job)
    return job


# --------------------------------------------------------------------------- #

def put_bytes(job_id, key, data):
    """The database first, the disk only if it will not answer.

    These bytes were the last thing in the repo written to the Render disk
    with no copy anywhere else. The disk is shared by the two gunicorn workers
    and local to one INSTANCE, so a scan on one instance and a save routed to
    another answered "The optimized file expired before saving" about a file
    that had not expired. bytes_store.py carries the whole reasoning.

    The disk write stays as the fallback rather than being deleted. A render
    that cost a download and real CPU is not thrown away because a backend
    would not answer -- Fan Radio's rule -- and a per-instance copy is exactly
    the behaviour this replaces, so a database outage leaves this tool no
    worse than it was.
    """
    if bytes_store.put(job_id, key, data):
        return key
    path = os.path.join(_job_dir(job_id), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return key


def get_bytes(job_id, key):
    """The database first, then the disk.

    Both halves are needed for the length of one TTL after a deploy: a batch
    scanned by the previous release has its bytes on the disk and nowhere
    else, and reading only the table would expire it early -- which is the
    same message this change exists to stop people seeing.
    """
    if not _valid_id(job_id) or ".." in key or key.startswith("/"):
        return None
    found = bytes_store.get(job_id, key)
    if found is not None:
        return found
    try:
        with open(os.path.join(_job_dir(job_id), key), "rb") as fh:
            return fh.read()
    except OSError:
        return None


def drop_job(job_id):
    if _valid_id(job_id):
        bytes_store.drop(job_id)
        shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def sweep():
    """Delete anything older than the TTL. Cheap, so it runs on every scan.

    Both stores, because for one TTL after a deploy both hold rows: the table
    is one indexed DELETE, and the directory walk stays for what the previous
    release left. Neither raises -- a cleanup that stops a scan is worse than
    the bytes it was cleaning.
    """
    bytes_store.sweep(settings.PAGE_IMAGES_TTL_MINUTES)
    cutoff = time.time() - settings.PAGE_IMAGES_TTL_MINUTES * 60
    try:
        entries = os.listdir(DATA_DIR)
    except OSError:
        return
    for name in entries:
        path = os.path.join(DATA_DIR, name)
        try:
            if os.path.getmtime(path) < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
