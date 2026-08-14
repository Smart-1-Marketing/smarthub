"""Uploaded client proposals — the PDFs and Word docs that were sent to a
client outside the Proposal Builder.

The file itself goes to Cloudinary (raw asset, one folder per client) so it
survives deploys and can be opened straight from Client 360. The record —
date sent to the client, title, who uploaded it, the Cloudinary URL — lives
in the client's Hub store next to their notes, socials and schema, so an
uploaded proposal stays with the client forever.

Falls back to the persistent disk when Cloudinary isn't configured, exactly
like the Proposal Builder does, so nothing breaks in local development.
"""
import datetime as _dt
import os
import re
import uuid

from . import seo

FOLDER = os.environ.get("CLOUDINARY_FOLDER", "smart1-proposals")
CLOUD_READY = (os.environ.get("CLOUDINARY_URL") or "").startswith("cloudinary://")

try:
    import cloudinary
    import cloudinary.uploader
    if CLOUD_READY:
        cloudinary.config(secure=True)          # reads CLOUDINARY_URL
except ImportError:                             # pragma: no cover
    cloudinary = None
    CLOUD_READY = False

ALLOWED = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_BYTES = int(os.environ.get("MAX_PROPOSAL_MB", "25")) * 1024 * 1024


def cloudinary_ready() -> bool:
    return CLOUD_READY


def _local_dir() -> str:
    base = "/var/data" if os.path.isdir("/var/data") else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    path = os.path.join(base, "client-proposals")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(name: str) -> str:
    name = os.path.basename(str(name or "proposal"))
    name = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip() or "proposal"
    return name[:120]


def _today() -> str:
    return _dt.date.today().isoformat()


def _iso_date(value, fallback="") -> str:
    """Accept 2026-08-14 or 08/14/2026; empty string when unparseable."""
    s = str(value or "").strip()
    if not s:
        return fallback
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return _dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return fallback


def list_proposals(client: str) -> list[dict]:
    """Uploaded proposals for a client, newest date-sent first."""
    items = seo.load_store(client).get("uploaded_proposals", [])
    items = [i for i in items if isinstance(i, dict)]
    items.sort(key=lambda i: (str(i.get("date_sent") or ""),
                              str(i.get("uploaded_at") or "")), reverse=True)
    return items


def _write(client: str, items: list[dict]):
    store = seo.load_store(client)
    store["uploaded_proposals"] = items
    seo.save_store(client, store)


def add_proposal(client: str, filename: str, data: bytes, date_sent: str = "",
                 title: str = "", note: str = "", actor: str = "") -> dict:
    """Store one uploaded proposal file and attach it to the client."""
    client = str(client or "").strip()
    if not client:
        raise ValueError("A client is required.")
    filename = _safe_name(filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED:
        raise ValueError("Only PDF, DOC and DOCX files can be uploaded.")
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"That file is larger than {MAX_BYTES // (1024 * 1024)} MB.")

    rec_id = uuid.uuid4().hex[:16]
    public_id = f"{FOLDER}/uploads/{seo.slugify(client)}/{rec_id}{ext}"
    url, storage = "", "local"

    if CLOUD_READY:
        import base64
        data_uri = (f"data:{ALLOWED[ext]};base64," + base64.b64encode(data).decode())
        res = cloudinary.uploader.upload(
            data_uri, resource_type="raw", public_id=public_id,
            overwrite=True, invalidate=True,
            context={"client": client, "filename": filename},
        )
        url, storage = res.get("secure_url", ""), "cloudinary"

    if not url:                                  # no Cloudinary — keep it on disk
        disk_name = f"{seo.slugify(client)}-{rec_id}{ext}"
        with open(os.path.join(_local_dir(), disk_name), "wb") as fh:
            fh.write(data)
        url = "/api/client/proposals/file/" + disk_name

    record = {
        "id": rec_id,
        "filename": filename,
        "title": str(title or "").strip() or os.path.splitext(filename)[0],
        "date_sent": _iso_date(date_sent, _today()),
        "uploaded_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "uploaded_by": str(actor or "").strip(),
        "note": str(note or "").strip()[:500],
        "url": url,
        "storage": storage,
        "public_id": public_id if storage == "cloudinary" else "",
        "kind": "pdf" if ext == ".pdf" else "doc",
        "size": len(data),
        "source": "uploaded",
    }
    items = list_proposals(client)
    items.insert(0, record)
    _write(client, items)
    return record


def update_proposal(client: str, pid: str, updates: dict) -> dict | None:
    """Edit the date sent, title or note on an uploaded proposal."""
    items = list_proposals(client)
    hit = next((i for i in items if i.get("id") == pid), None)
    if hit is None:
        return None
    if "date_sent" in updates:
        hit["date_sent"] = _iso_date(updates["date_sent"], hit.get("date_sent", ""))
    for key, cap in (("title", 200), ("note", 500)):
        if key in updates:
            hit[key] = str(updates[key] or "").strip()[:cap]
    _write(client, items)
    return hit


def delete_proposal(client: str, pid: str) -> bool:
    items = list_proposals(client)
    hit = next((i for i in items if i.get("id") == pid), None)
    if hit is None:
        return False
    if hit.get("storage") == "cloudinary" and CLOUD_READY and hit.get("public_id"):
        try:
            cloudinary.uploader.destroy(hit["public_id"], resource_type="raw",
                                        invalidate=True)
        except Exception as exc:                 # noqa: BLE001 — record still goes
            print("cloudinary delete failed:", exc)
    else:
        path = os.path.join(_local_dir(), os.path.basename(str(hit.get("url", ""))))
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass
    _write(client, [i for i in items if i.get("id") != pid])
    return True


def local_file_path(name: str) -> str | None:
    """Path for a locally-stored proposal (Cloudinary-less fallback)."""
    name = os.path.basename(str(name or ""))
    if not re.match(r"^[A-Za-z0-9._-]+\.(pdf|docx?|DOCX?|PDF)$", name):
        return None
    path = os.path.join(_local_dir(), name)
    return path if os.path.isfile(path) else None
