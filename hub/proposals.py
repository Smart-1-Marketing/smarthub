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
    """Where a proposal PDF lands when Cloudinary is not configured.

    Through `jsonstore.data_dir()` rather than its own copy of the
    /var/data-or-repo expression. Every module that carried that expression
    agreed by luck rather than design, and this one ignored HUB_DATA_DIR
    entirely — so it wrote into the repo even when the data root had been
    pointed somewhere else, which is how the test suite started leaving
    client PDFs in the working tree.
    """
    from . import jsonstore
    return jsonstore.data_dir("client-proposals")


def _safe_name(name: str) -> str:
    name = os.path.basename(str(name or "proposal"))
    name = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip() or "proposal"
    return name[:120]


def _today() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Quote numbers for uploaded proposals.
#
# An uploaded proposal used to have no number at all — its only identity was a
# 16-hex uuid, and the Proposal Builder's "Quote #" column printed the literal
# word "Uploaded" where every other row carried a reference someone could say
# out loud. "What happened with Q-10250?" had no answer for half the pipeline.
#
# The series is deliberately its own. Q- numbers are allocated by the Proposal
# Builder against its own Counter table, and reaching into a module's database
# from hub/ to share that counter would import a whole Flask app to increment
# an integer. U- runs parallel to it from the same base, so the two read as
# siblings and can never collide: a U- number is a quote number AND says where
# the document came from, which is what the badge in the UI repeats.
# ---------------------------------------------------------------------------
QUOTE_PREFIX = "U-"
QUOTE_BASE = 10199              # first uploaded proposal is U-10200
_NUMBER_LOCK = __import__("threading").Lock()


def _postgres_quote_number():
    """Next number from a Postgres sequence, or None when there is no Postgres.

    A threading.Lock only holds inside one process and gunicorn runs two
    workers, so two uploads landing together could read the same counter and
    be handed the same number. A sequence is atomic across every worker, which
    no application-level lock can be. Same reasoning, and the same shape, as
    io_builder's order numbers.
    """
    try:
        from sqlalchemy import text

        from .extensions import engine_for
        engine = engine_for()
        if not engine.dialect.name.startswith("postgres"):
            return None
        with engine.connect() as cx:
            cx.execute(text("CREATE SEQUENCE IF NOT EXISTS uploaded_quote_number_seq "
                            f"START WITH {QUOTE_BASE + 1} INCREMENT BY 1"))
            cx.commit()
            value = cx.execute(text("SELECT nextval('uploaded_quote_number_seq')")).scalar()
            cx.commit()
        return int(value)
    except Exception:                                   # noqa: BLE001
        return None


def _counter_path() -> str:
    from . import jsonstore
    return os.path.join(jsonstore.data_dir("client-proposals"), "quote-counter.json")


def next_quote_number() -> str:
    """Allocate the next uploaded-proposal number: U-10200, U-10201, ...

    Through jsonstore rather than a bare file write: the Render disk is not
    backed up, and a counter that resets to zero after a disk swap would hand
    out numbers that already belong to documents a client has seen.
    """
    value = _postgres_quote_number()
    if value is None:
        from . import jsonstore
        path = _counter_path()
        with _NUMBER_LOCK:
            current = jsonstore.read_json(path, default={}) or {}
            try:
                value = int(current.get("last_quote_number") or QUOTE_BASE) + 1
            except (TypeError, ValueError):
                value = QUOTE_BASE + 1
            jsonstore.write_json(path, {"last_quote_number": value})
    return f"{QUOTE_PREFIX}{int(value)}"


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


def _backfill_numbers(client: str, items: list[dict]) -> list[dict]:
    """Give any proposal uploaded before numbering existed a number, once.

    Converted on read rather than by migrating rows nobody has re-opened —
    the same choice target areas made for its legacy geo fields. The write is
    best-effort: a store that cannot be written still lists, it just gets
    asked again next time. Numbers are allocated oldest-first so the series
    runs in the order the documents were actually sent.
    """
    missing = [i for i in items if not str(i.get("quote_number") or "").strip()]
    if not missing:
        return items
    for item in sorted(missing, key=lambda i: (str(i.get("date_sent") or ""),
                                               str(i.get("uploaded_at") or ""))):
        item["quote_number"] = next_quote_number()
    try:
        _write(client, items)
    except Exception:                                   # noqa: BLE001 — listing must not fail on a write
        pass
    return items


def list_proposals(client: str, backfill: bool = True) -> list[dict]:
    """Uploaded proposals for a client, newest date-sent first."""
    items = seo.load_store(client).get("uploaded_proposals", [])
    items = [i for i in items if isinstance(i, dict)]
    items.sort(key=lambda i: (str(i.get("date_sent") or ""),
                              str(i.get("uploaded_at") or "")), reverse=True)
    if backfill:
        items = _backfill_numbers(client, items)
    return items


def all_proposals(limit: int = 200, q: str = "") -> list[dict]:
    """Uploaded proposals across every client, newest first.

    The Sales Builder list is the one place staff look for "what have we
    quoted lately", and it only ever showed quotes built in the tool. A
    proposal someone wrote elsewhere and uploaded to the client record was
    invisible there, so the list quietly under-reported the pipeline — the
    kind of confident wrong answer this codebase treats as worse than an
    error.

    Each row carries the client name, because unlike list_proposals() the
    caller no longer knows which client it asked about.

    rollup() walks all 950 registry clients to do the same job. Almost none of
    them have an SEO store, so that is ~950 failed file opens on the path of a
    page load. This lists the store directory instead and reads only what
    exists, which is a small fraction of them, falling back to the registry
    walk if the directory cannot be listed.
    """
    try:
        from hub import clients_registry
        names = [c.get("name", "") for c in clients_registry.all_clients()
                 if c.get("name")]
    except Exception:                                   # noqa: BLE001
        return []

    try:
        base = seo._store_base()
        have = {f[:-5] for f in os.listdir(base) if f.endswith(".json")}
        # The store is keyed by slug, so the registry supplies the display
        # name. A store with no matching client keeps its slug rather than
        # being dropped — an orphaned store still holds real proposals.
        by_slug = {seo.slugify(n): n for n in names}
        names = [by_slug.get(sl, sl) for sl in have]
    except OSError:
        pass

    needle = str(q or "").strip().lower()
    out = []
    for name in names:
        try:
            items = list_proposals(name)
        except Exception:                               # noqa: BLE001
            # One unreadable client store must not empty the whole list.
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            if needle:
                hay = " ".join(str(it.get(k) or "") for k in
                               ("title", "filename", "note", "uploaded_by"))
                if needle not in hay.lower() and needle not in name.lower():
                    continue
            row = dict(it)
            row["client"] = name
            out.append(row)

    out.sort(key=lambda i: (str(i.get("date_sent") or ""),
                            str(i.get("uploaded_at") or "")), reverse=True)
    return out[:max(1, int(limit or 200))]


PROPOSAL_STATUSES = ("draft", "sent", "viewed", "won", "lost")


def set_status(client: str, rec_id: str, status: str, actor: str = "") -> dict:
    """Move a proposal along the pipeline. Won/lost is what makes the rollup
    mean anything -- an unmarked won proposal reads as still outstanding."""
    if status not in PROPOSAL_STATUSES:
        raise ValueError("Unknown proposal status.")
    items = list_proposals(client)
    for it in items:
        if it.get("id") == rec_id:
            it["status"] = status
            it["status_changed_at"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
            it["status_by"] = str(actor or "").strip()
            _write(client, items)
            try:
                from hub import audit
                audit.log("proposals", "status_changed", actor=actor,
                          client=client, proposal=rec_id, status=status)
            except Exception:  # noqa: BLE001
                pass
            return it
    raise ValueError("No such proposal.")


def upsert_from_ghl(client: str, opportunity_id: str, title: str,
                    value: float, status: str, when: str = "") -> dict:
    """Create or update the proposal row for a Suite opportunity.

    Keyed on the opportunity id so repeated webhooks update one row instead of
    piling up duplicates — GoHighLevel fires on create, update and every stage
    change, so an append-only handler would produce five rows for one deal.
    """
    items = list_proposals(client)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for it in items:
        if opportunity_id and it.get("opportunity_id") == opportunity_id:
            it.update({"title": title or it.get("title"),
                       "value": round(float(value or 0), 2) or it.get("value", 0),
                       "status": status, "status_changed_at": now,
                       "updated_at": now})
            _write(client, items)
            return it
    record = {
        "id": uuid.uuid4().hex[:12],
        "opportunity_id": opportunity_id,
        "title": title or "Suite opportunity",
        "date_sent": _iso_date(when, _today()),
        "uploaded_at": now,
        "uploaded_by": "Smart 1 Suite",
        "value": round(float(value or 0), 2),
        "term": "monthly",
        "status": status,
        "status_changed_at": now,
        "note": "",
        "url": "",
        "storage": "ghl",
        "public_id": "",
        "kind": "opportunity",
        "size": 0,
        "source": "ghl",
    }
    items.append(record)
    _write(client, items)
    return record


def rollup(client: str = "") -> dict:
    """Totals by status, for the client card and the pipeline report."""
    if client:
        clients = [client]
    else:
        try:
            from hub import clients_registry
            clients = [c.get("name", "") for c in clients_registry.all_clients()
                       if c.get("name")]
        except Exception:  # noqa: BLE001
            clients = []
    out = {s: {"count": 0, "value": 0.0} for s in PROPOSAL_STATUSES}
    for c in clients:
        for it in list_proposals(c):
            st = it.get("status") or "sent"
            if st not in out:
                st = "sent"
            out[st]["count"] += 1
            out[st]["value"] += float(it.get("value") or 0)
    for v in out.values():
        v["value"] = round(v["value"], 2)
    open_value = round(sum(out[s]["value"] for s in ("draft", "sent", "viewed")), 2)
    won = out["won"]["value"]
    decided = won + out["lost"]["value"]
    return {"by_status": out, "open_value": open_value, "won_value": won,
            "win_rate": (round(100 * won / decided) if decided else None),
            "clients": len(clients)}


def _write(client: str, items: list[dict]):
    store = seo.load_store(client)
    store["uploaded_proposals"] = items
    seo.save_store(client, store)


def _money(value) -> float:
    """Parse a proposal value from whatever a human typed.

    "$2,500/mo", "2500", "2,500.00" all become 2500.0. Deliberately strict
    about ranges: "2,500-5,000" would otherwise strip to 25005000, which is
    the exact bug that pushed a $300,060,000 opportunity into GoHighLevel in
    the suite audit. A range returns the lower bound instead.
    """
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    raw = raw.replace(",", "")
    parts = re.findall(r"\d+(?:\.\d+)?", raw)
    if not parts:
        return 0.0
    # A dash between two numbers means a range -- take the lower bound.
    return round(float(parts[0]), 2)


def add_proposal(client: str, filename: str, data: bytes, date_sent: str = "",
                 title: str = "", note: str = "", actor: str = "",
                 value: str = "", term: str = "monthly",
                 status: str = "sent") -> dict:
    """Store one uploaded proposal file and attach it to the client.

    `value` and `status` exist so proposals are trackable rather than just
    filed: what was quoted, and what happened to it. Without them the
    Proposals card is an archive; with them it's a pipeline.
    """
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
        # One Cloudinary operation, for the credit estimate on /diagnostics.
        try:
            from hub import quotas as _q
            _q.record_asset(module="proposals", nbytes=len(data),
                            detail=public_id)
        except Exception:                     # noqa: BLE001
            pass

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
        "value": _money(value),
        "term": (term if term in ("monthly", "one_time", "annual") else "monthly"),
        "status": (status if status in PROPOSAL_STATUSES else "sent"),
        "status_changed_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": "uploaded",
        "quote_number": next_quote_number(),
    }
    items = list_proposals(client)
    items.insert(0, record)
    _write(client, items)
    return record


def add_link_proposal(client: str, url: str, title: str, *, ref: str = "",
                      module: str = "", note: str = "", actor: str = "",
                      value: str = "", term: str = "monthly",
                      status: str = "sent", date_sent: str = "") -> dict:
    """File a proposal that lives on a Hub page rather than in a file.

    ``add_proposal`` takes bytes because the archive was built for PDFs a rep
    emailed. Smart 1 Ads produces a proposal that *is* a page — versioned,
    commentable and re-read as it changes — so uploading a snapshot of it would
    put a stale copy on the client record beside the live one, and the client
    record is where people go to find out what was actually quoted.

    ``ref`` is the producing module's own id for the proposal, and this
    **updates the row carrying it** rather than appending. Every re-file of one
    campaign is the same row: without that, approving a proposal, commenting on
    it and deploying it would leave three identical entries on Client 360 and
    no way to tell which one is current. It is the same lesson
    ``upsert_from_ghl`` learned from GoHighLevel firing on every stage change.
    """
    client = str(client or "").strip()
    if not client:
        raise ValueError("A client is required.")
    url = str(url or "").strip()
    if not url:
        raise ValueError("A link is required.")

    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    items = list_proposals(client)

    if ref:
        for it in items:
            if it.get("ref") == ref and it.get("kind") == "link":
                it.update({
                    "title": str(title or "").strip() or it.get("title"),
                    "url": url,
                    "note": str(note or "").strip()[:500] or it.get("note", ""),
                    "value": _money(value) or it.get("value", 0),
                    "status": (status if status in PROPOSAL_STATUSES else it.get("status", "sent")),
                    "status_changed_at": now,
                    "updated_at": now,
                })
                _write(client, items)
                return it

    record = {
        "id": uuid.uuid4().hex[:16],
        "ref": str(ref or ""),
        "filename": "",
        "title": str(title or "").strip() or "Proposal",
        "date_sent": _iso_date(date_sent, _today()),
        "uploaded_at": now,
        "uploaded_by": str(actor or "").strip(),
        "note": str(note or "").strip()[:500],
        "url": url,
        # "hub" rather than "cloudinary"/"local": nothing was stored, and a
        # reader that assumes a file behind the URL has to be able to tell.
        "storage": "hub",
        "public_id": "",
        "kind": "link",
        "size": 0,
        "value": _money(value),
        "term": (term if term in ("monthly", "one_time", "annual") else "monthly"),
        "status": (status if status in PROPOSAL_STATUSES else "sent"),
        "status_changed_at": now,
        "source": str(module or "hub"),
        "quote_number": next_quote_number(),
    }
    items.insert(0, record)
    _write(client, items)
    return record


def update_proposal(client: str, pid: str, updates: dict) -> dict | None:
    """Edit the date sent, title, note or Suite opportunity id of a proposal.

    `opportunity_id` is writable so the Suite push can record what it created
    without a second store; keyed on it, `upsert_from_ghl` then updates this
    same row when GoHighLevel fires a status change instead of appending a
    duplicate beside it.
    """
    items = list_proposals(client)
    hit = next((i for i in items if i.get("id") == pid), None)
    if hit is None:
        return None
    if "date_sent" in updates:
        hit["date_sent"] = _iso_date(updates["date_sent"], hit.get("date_sent", ""))
    for key, cap in (("title", 200), ("note", 500), ("opportunity_id", 64)):
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


def proposals_for(client: str) -> dict:
    """Both kinds of proposal on one client, kept apart rather than merged.

    A quote built here has a number, a revision and a status this Hub owns.
    An uploaded proposal is a file with a date on it. Flattening the two into
    one row shape means inventing values for half the columns, and an invented
    "Draft" on a document nobody here drafted is the sort of confident wrong
    answer this codebase treats as worse than an error. So they come back
    labelled and the page groups them.
    """
    want = re.sub(r"[^a-z0-9]+", "", str(client or "").lower())
    out = {"client": client, "saved": [], "uploaded": [], "note": "", "count": 0}
    if not want:
        return out

    # Saved quotes, from the live builder's table.
    try:
        from modules.sales_builder.app import Quote, SessionLocal
        db = SessionLocal()
        try:
            for q in (db.query(Quote)
                        .order_by(Quote.updated_at.desc()).limit(400).all()):
                if re.sub(r"[^a-z0-9]+", "", str(q.client or "").lower()) != want:
                    continue
                out["saved"].append({
                    "id": str(q.id),
                    "quote_number": q.quote_number or "",
                    "status": q.status or "",
                    "products": q.products_summary or "",
                    "monthly": q.monthly_budget or 0,
                    "updated": q.updated_at.isoformat() if q.updated_at else "",
                })
        finally:
            db.close()
    except Exception:                                   # noqa: BLE001
        out["note"] = "Saved proposals could not be read."

    # Proposals written elsewhere and uploaded onto the client record.
    try:
        from hub import proposals as hub_proposals
        for rec in hub_proposals.list_proposals(client):
            out["uploaded"].append({
                "id": rec.get("id", ""),
                "title": rec.get("title") or rec.get("filename", ""),
                "filename": rec.get("filename", ""),
                "date_sent": rec.get("date_sent", ""),
                "value": rec.get("value", 0),
                "kind": rec.get("kind", ""),
                "readable": bool(rec.get("url")),
            })
    except Exception:                                   # noqa: BLE001
        out["note"] = (out["note"] + " Uploaded proposals could not be read.").strip()

    out["count"] = len(out["saved"]) + len(out["uploaded"])
    return out
