"""Multi-location content requests, and the locations that make them.

## The problem this replaces

A client account is one social presence. The business behind it is often
several shops, and each shop has somebody who wants something posted. Today
those arrive as separate emails — a photo here, "can we push the Friday sale"
there — into whichever inbox the sender happened to know. Nothing sees them
against each other, so two locations asking for the same week is discovered
when both are already scheduled, a request that has gone stale looks exactly
like one nobody has got to yet, and once a strategist acts on one there is no
trail joining the finished post back to the person who asked for it.

One link per client account fixes all three. Anybody at any location opens it,
says which location they are, and submits the whole ask in one pass. It lands
in one shared queue for that client, sorted by what was asked rather than by
which inbox it arrived in.

## Storage, and why it is not a table

`hub/jsonstore.py`, two files: the locations and the requests. Mirrored into
the database, because a month of client-supplied photographs and copy whose
only copy was the Render disk vanishes on a disk resize with no error
anywhere. Deletes go through `jsonstore.delete_json`; a bare `os.remove`
leaves the database copy to be restored by the next read, so the delete undoes
itself.

Rows carry the client's **name and URL**, never the derived key — the
`hub/client_key.py` rule, so a client renamed in Knack is re-joined on the
next read instead of leaving a stale copy behind.

## Three rules that are easy to get backwards

**A flag is computed on read, never stored.** Overdue and possible-duplicate
are both functions of today's date and of the other rows as they stand now.
Baked into the file at write time, a request that went overdue overnight
would stay green until somebody edited it, and there are two gunicorn workers
to disagree about which copy is current — the failure
`hub/creative_evergreen.py` is written around.

**A location that is not set up must not block a submission.** The intake form
takes a free-text location instead, and the queue shows it as typed with a
note saying it matched nothing. A form that refuses a location manager who is
trying to send us a photograph has cost us the photograph.

**Nothing is auto-merged.** A duplicate flag is advisory and it says so. Two
locations wanting something live the same week is as often two real asks as
it is one ask twice, and this file cannot tell them apart.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone

from hub import jsonstore, social_content
from hub.client_key import normalise_name, same_client

LOCATIONS_FILE = "locations.json"
REQUESTS_FILE = "requests.json"

# The whole book, not one client's. A client sending a request a day for a
# year is 365 rows; the ceiling is what stops a single file growing without
# bound on a 5 GB disk, and it is the *oldest* that fall off, which is why
# rows are kept newest-first.
MAX_REQUESTS = 4000
MAX_LOCATIONS = 600


# ------------------------------------------------------------------ storage
def _dir() -> str:
    return jsonstore.data_dir("social")


def _path(name: str) -> str:
    return os.path.join(_dir(), name)


def _read(name: str, key: str) -> list[dict]:
    blob = jsonstore.read_json(_path(name), default=None)
    if isinstance(blob, dict) and isinstance(blob.get(key), list):
        return [r for r in blob[key] if isinstance(r, dict)]
    return []


def _rows_mutate(name: str, key: str, cap: int, apply):
    """Read, change and write one of these files as a single step.

    The `threading.Lock` this replaces already covered the read as well as the
    write, which is the half `modules/radio_promo` was missing -- so inside one
    worker this was right. It is per-process, and this deployment runs two
    gunicorn workers, so it never saw the other one: two location managers
    submitting from their phones at the same moment land on different workers,
    each writes the whole list back, and the second one to finish drops the
    first request. Both are told it arrived, and losing it is losing exactly
    the photograph this form exists to collect.

    `apply` is handed the rows and returns the rows to write, or None to write
    nothing -- "already there" and "no such row" both mean there is nothing to
    save, and a write queued for them would be one on each worker.
    """
    def _blob(blob):
        rows = ([r for r in blob.get(key) or [] if isinstance(r, dict)]
                if isinstance(blob, dict) else [])
        out = apply(rows)
        return None if out is None else {key: out[:cap]}

    jsonstore.update_json(_path(name), _blob, default=None, indent=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return prefix + format(int(time.time() * 1000), "x")[-8:] + os.urandom(2).hex()


def _text(value, limit: int = 400) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _mine(row: dict, client: str, url: str = "") -> bool:
    """Is this row this client's?

    Exact domain or exact normalised name, through `hub/client_key.py`.
    Never a substring: a queue that collects "Riverside HVAC Supply"'s
    requests into "Riverside HVAC"'s is one location's photographs posted on
    another company's page, which is the worst outcome available to this tool.
    """
    return same_client(client, url, str(row.get("client") or ""),
                       str(row.get("client_url") or ""))


# =====================================================================
# Locations
# =====================================================================
def locations(client: str, url: str = "", *, include_inactive: bool = False) -> list[dict]:
    rows = [r for r in _read(LOCATIONS_FILE, "locations") if _mine(r, client, url)]
    if not include_inactive:
        rows = [r for r in rows if r.get("active", True)]
    rows.sort(key=lambda r: (str(r.get("name") or "").lower(), r.get("created_at") or ""))
    return rows


def location_by_id(loc_id: str) -> dict | None:
    for row in _read(LOCATIONS_FILE, "locations"):
        if row.get("id") == loc_id:
            return row
    return None


def add_location(client: str, url: str = "", *, name: str = "",
                 contact_name: str = "", contact_email: str = "",
                 contact_phone: str = "", address: str = "",
                 actor: str = "") -> dict:
    """Add a location to a client. Names are unique per client, case-folded —
    two "Westside" rows is a dropdown a location manager cannot answer."""
    name = _text(name, 120)
    if not name:
        raise ValueError("A location needs a name.")
    if not _text(client, 200):
        raise ValueError("A location belongs to a client.")
    made: list[dict] = []

    def _apply(rows):
        for row in rows:
            if _mine(row, client, url) and \
                    str(row.get("name") or "").strip().lower() == name.lower():
                made.append(row)
                return None             # already there; adding twice is not an error
        row = {
            "id": _new_id("loc-"),
            "client": _text(client, 200),
            "client_url": _text(url, 300),
            "name": name,
            "contact_name": _text(contact_name, 120),
            "contact_email": _text(contact_email, 200),
            "contact_phone": _text(contact_phone, 40),
            "address": _text(address, 300),
            "active": True,
            "created_at": _now(),
            "created_by": _text(actor, 120),
        }
        rows.insert(0, row)
        made.append(row)
        return rows

    _rows_mutate(LOCATIONS_FILE, "locations", MAX_LOCATIONS, _apply)
    return made[0]


def update_location(loc_id: str, **fields) -> dict | None:
    """Edit a location. The client it belongs to is deliberately not editable:
    moving a location between clients would move every request filed under it,
    and those name the person who submitted them."""
    allowed = ("name", "contact_name", "contact_email", "contact_phone",
               "address")
    found: list[dict] = []

    def _apply(rows):
        for row in rows:
            if row.get("id") != loc_id:
                continue
            for key in allowed:
                if key in fields:
                    row[key] = _text(fields[key], 300)
            if "active" in fields:
                row["active"] = bool(fields["active"])
            row["updated_at"] = _now()
            found.append(row)
            return rows
        return None

    _rows_mutate(LOCATIONS_FILE, "locations", MAX_LOCATIONS, _apply)
    return found[0] if found else None


# =====================================================================
# Requests
# =====================================================================
def submit(client: str, url: str = "", *, payload: dict | None = None,
           source: str = "client_link") -> dict:
    """Take one request. Only two things are actually required.

    A location manager fires this off from a phone with a photograph attached.
    Everything except *which location* and *what it is about* is optional, and
    even the location degrades to free text rather than refusing — a form that
    turns somebody away because their shop is not in a dropdown has cost us
    the photograph, and the dropdown is our housekeeping rather than theirs.
    """
    payload = payload or {}
    client = _text(client, 200)
    if not client:
        raise ValueError("A request belongs to a client.")

    request_type = str(payload.get("request_type") or "post")
    if request_type not in social_content.REQUEST_TYPES:
        request_type = "other"

    loc_id = _text(payload.get("location_id"), 40)
    location = location_by_id(loc_id) if loc_id else None
    if location and not _mine(location, client, url):
        # A location id from another client's link. Refused rather than
        # silently re-filed: it is the one field that decides whose queue this
        # lands in.
        location, loc_id = None, ""

    mode = str(payload.get("requested_date_mode") or "asap")
    if mode not in social_content.DATE_MODES:
        mode = "asap"
    start = _date(payload.get("requested_date_start"))
    end = _date(payload.get("requested_date_end"))
    if mode == "asap":
        start = end = ""
    elif mode == "specific_date":
        end = start
    elif start and end and end < start:
        start, end = end, start

    assets = [_text(a, 300) for a in (payload.get("asset_refs") or [])
              if _text(a, 300)][:20]

    row = {
        "id": _new_id("req-"),
        "client": client,
        "client_url": _text(url, 300),
        "location_id": loc_id if location else "",
        # Kept whether or not the id resolved. It is what the queue shows, and
        # a request whose location was later renamed still says which shop
        # sent it.
        "location_label": (location or {}).get("name", "") or
                          _text(payload.get("location_label"), 120),
        "submitted_by_name": _text(payload.get("submitted_by_name"), 120),
        "submitted_by_email": _text(payload.get("submitted_by_email"), 200),
        "submitted_by_phone": _text(payload.get("submitted_by_phone"), 40),
        "request_type": request_type,
        "copy_suggestion": _text(payload.get("copy_suggestion"), 4000),
        "asset_refs": assets,
        "requested_date_mode": mode,
        "requested_date_start": start,
        "requested_date_end": end,
        "notes": _text(payload.get("notes"), 4000),
        "status": "new",
        "source": _text(source, 40) or "client_link",
        "linked_batch_id": "",
        "linked_slot_id": "",
        "declined_reason": "",
        "duplicate_of_id": "",
        "triaged_by": "",
        "triaged_at": "",
        "created_at": _now(),
    }
    def _apply(rows):
        rows.insert(0, row)
        return rows

    _rows_mutate(REQUESTS_FILE, "requests", MAX_REQUESTS, _apply)
    return row


def _date(value) -> str:
    text = _text(value, 10)
    return text if re.match(r"^\d{4}-\d{2}-\d{2}$", text) else ""


def get(req_id: str) -> dict | None:
    for row in _read(REQUESTS_FILE, "requests"):
        if row.get("id") == req_id:
            return row
    return None


def for_client(client: str, url: str = "", *, statuses=None) -> list[dict]:
    """This client's requests, newest first, with the advisory flags applied.

    Applied here rather than stored, so a request that went overdue overnight
    is overdue on the next open of the page in whichever worker serves it.
    """
    rows = [r for r in _read(REQUESTS_FILE, "requests") if _mine(r, client, url)]
    if statuses:
        wanted = set(statuses)
        rows = [r for r in rows if str(r.get("status") or "new") in wanted]
    return decorate(rows)


def decorate(rows: list[dict], today: date | None = None) -> list[dict]:
    """Overdue, possible-duplicate and the labels a screen needs.

    The duplicate pass is run over the rows handed in, which are one client's:
    `social_content.duplicate_flags()` groups by client itself, so a caller
    passing the whole book still cannot produce a cross-client pairing.
    """
    dupes = social_content.duplicate_flags(rows)
    out = []
    for row in rows:
        item = dict(row)
        item["overdue"] = social_content.is_overdue(row, today)
        item["possible_duplicate_of"] = dupes.get(row.get("id"), [])
        item["type_label"] = social_content.request_type_label(row.get("request_type"))
        item["status_label"] = social_content.status_label(row.get("status"))
        item["when"] = when_label(row)
        item["assets"] = len(row.get("asset_refs") or [])
        out.append(item)
    return out


def when_label(row: dict) -> str:
    """What the client asked for, in their own terms. 'ASAP' is a real answer
    and is not silently converted into today's date."""
    mode = str(row.get("requested_date_mode") or "asap")
    start, end = row.get("requested_date_start") or "", row.get("requested_date_end") or ""
    if mode == "specific_date" and start:
        return f"On {start}"
    if mode == "date_range" and start:
        return f"Between {start} and {end or start}"
    return "As soon as we can"


# ------------------------------------------------------------------ triage
def _mutate(req_id: str, fn) -> dict | None:
    found: list[dict] = []

    def _apply(rows):
        for row in rows:
            if row.get("id") != req_id:
                continue
            fn(row)
            found.append(row)
            return rows
        return None

    _rows_mutate(REQUESTS_FILE, "requests", MAX_REQUESTS, _apply)
    return found[0] if found else None


def mark_triaged(req_id: str, actor: str = "") -> dict | None:
    def apply(row):
        if str(row.get("status")) == "new":
            row["status"] = "triaged"
        # Stamped once. The turnaround figure is measured off the first time
        # somebody picked a request up, and re-stamping it on every later edit
        # would report the tool getting faster the more it was fiddled with.
        if not row.get("triaged_at"):
            row["triaged_at"] = _now()
            row["triaged_by"] = _text(actor, 120)
    return _mutate(req_id, apply)


def decline(req_id: str, reason: str = "", actor: str = "") -> dict | None:
    """Decline with a reason, because the client will ask.

    The reason is required. "Declined" with nothing behind it puts a
    strategist back on the phone reconstructing a decision somebody else made
    three weeks ago, which is the same conversation the request form exists to
    stop having.
    """
    reason = _text(reason, 600)
    if not reason:
        raise ValueError("A declined request needs a reason — the person who "
                         "asked for it will want one.")

    def apply(row):
        row["status"] = "declined"
        row["declined_reason"] = reason
        row["triaged_by"] = _text(actor, 120) or row.get("triaged_by", "")
        row["triaged_at"] = row.get("triaged_at") or _now()
    return _mutate(req_id, apply)


def mark_duplicate(req_id: str, of_id: str = "", actor: str = "") -> dict | None:
    """Confirm a flagged duplicate, or clear it.

    Passing no `of_id` clears the mark and puts the request back in the queue.
    Confirming one never touches the row it points at: the other request is
    the one being kept, and editing both from one press is how a pair of
    requests ends up with neither of them live.
    """
    def apply(row):
        if of_id:
            row["status"] = "duplicate"
            row["duplicate_of_id"] = _text(of_id, 40)
        else:
            row["duplicate_of_id"] = ""
            if str(row.get("status")) == "duplicate":
                row["status"] = "new"
        row["triaged_by"] = _text(actor, 120) or row.get("triaged_by", "")
        row["triaged_at"] = row.get("triaged_at") or _now()
    return _mutate(req_id, apply)


def link_post(req_id: str, batch_id: str, slot_id: str, actor: str = "") -> dict | None:
    """Join a request to the slot it became.

    This is what turns the form submission into an audit trail: the post on
    the calendar can say it came from Eastside's request on the 14th, and the
    request can say which post answered it. Without the join the request is a
    dead form entry and somebody re-reads the whole queue to work out which
    ones were done.
    """
    def apply(row):
        row["linked_batch_id"] = _text(batch_id, 40)
        row["linked_slot_id"] = _text(slot_id, 20)
        if str(row.get("status")) in ("new", "triaged"):
            row["status"] = "triaged"
        if not row.get("triaged_at"):
            row["triaged_at"] = _now()
            row["triaged_by"] = _text(actor, 120)
    return _mutate(req_id, apply)


def sync_from_post(req_id: str, slot_status: str, batch_status: str = "") -> dict | None:
    """Move the request as the post it became moves.

    The request statuses after `triaged` are not somebody's to set by hand —
    they are statements about the linked post, and a queue where a request
    says "triaged" over a post that went out last Tuesday is a queue people
    stop reading. It only ever moves *forward*: a strategist un-approving a
    slot to fix a typo must not walk the client's request backwards to New.
    """
    order = {"new": 0, "triaged": 1, "scheduled": 2, "posted": 3}
    if slot_status == "published" or batch_status == "published":
        wanted = "posted"
    elif slot_status in ("approved", "pushed", "scheduled") or batch_status == "approved":
        wanted = "scheduled"
    else:
        wanted = "triaged"

    def apply(row):
        now = order.get(str(row.get("status")), -1)
        if now < 0:                              # declined or duplicate: answered already
            return
        if order[wanted] > now:
            row["status"] = wanted
    return _mutate(req_id, apply)


# ------------------------------------------------------------------ reporting
def summary(client: str, url: str = "") -> dict:
    """The counts a staff queue puts at the top, plus the turnaround note.

    `by_location` is what answers the question the agent asks in §8 — a shop
    that has gone quiet and a shop flooding the queue are both worth knowing
    before anybody promises a turnaround time — and a request whose location
    never resolved is counted under its typed label rather than dropped, or
    the totals disagree with the list underneath them.
    """
    rows = for_client(client, url)
    counts = {key: 0 for key in social_content.REQUEST_STATUSES}
    by_location: dict[str, dict] = {}
    overdue = duplicates = 0
    for row in rows:
        counts[str(row.get("status") or "new")] = \
            counts.get(str(row.get("status") or "new"), 0) + 1
        overdue += 1 if row.get("overdue") else 0
        duplicates += 1 if row.get("possible_duplicate_of") else 0
        label = row.get("location_label") or "Not said"
        slot = by_location.setdefault(label, {"location": label, "total": 0, "open": 0})
        slot["total"] += 1
        if str(row.get("status") or "new") in social_content.OPEN_STATUSES:
            slot["open"] += 1
    return {
        "total": len(rows),
        "counts": counts,
        "open": sum(counts.get(s, 0) for s in social_content.OPEN_STATUSES),
        "overdue": overdue,
        "possible_duplicates": duplicates,
        "by_location": sorted(by_location.values(),
                              key=lambda r: (-r["total"], r["location"])),
        "turnaround": social_content.turnaround_note(rows),
    }


def open_requests(client: str, url: str = "") -> list[dict]:
    """Requests still waiting on somebody, overdue first.

    Sorted by what is most at risk rather than by arrival: a request whose day
    has passed is the one that costs us something, and one with no date at all
    is genuinely last because the client said so.
    """
    rows = for_client(client, url, statuses=social_content.OPEN_STATUSES)
    def sort_key(row):
        window = social_content.request_window(row)
        return (0 if row.get("overdue") else 1,
                window[1].isoformat() if window else "9999-12-31",
                row.get("created_at") or "")
    return sorted(rows, key=sort_key)


def clients_with_open_requests() -> list[dict]:
    """Every client with something waiting, for the staff landing screen.

    Derived on read from the rows themselves rather than kept as a second
    list: a count that is stored beside the thing it counts always eventually
    disagrees with it.
    """
    rows = [r for r in _read(REQUESTS_FILE, "requests")
            if str(r.get("status") or "new") in social_content.OPEN_STATUSES]
    out: dict[str, dict] = {}
    for row in decorate(rows):
        key = normalise_name(str(row.get("client") or ""))
        if not key:
            continue
        item = out.setdefault(key, {"client": row.get("client", ""),
                                    "url": row.get("client_url", ""),
                                    "open": 0, "overdue": 0})
        item["open"] += 1
        item["overdue"] += 1 if row.get("overdue") else 0
    return sorted(out.values(), key=lambda r: (-r["overdue"], -r["open"], r["client"]))
