"""Knack REST API — web tickets (object_107) for Client 360.

The field ids are pinned in TICKET_FIELDS and each is overridable by
environment variable, because label matching broke silently when a label was
renamed. What is still read from the live object is everything a *form* needs
and we cannot know: the field types, the dropdown choices, and the records a
connection may point at. Requires KNACK_APP_ID + KNACK_API_KEY.

Read, create and update tickets — nothing else is ever written to Knack.
The updatable set is TICKET_MANAGE_FIELDS, and Title is not in it: renaming a
ticket breaks the thread for whoever raised it.
"""
import json
import os
import re

import requests

BASE = "https://api.knack.com/v1"
TICKETS_OBJECT = os.environ.get("KNACK_TICKETS_OBJECT", "object_107")
CHANGE_OBJECT = os.environ.get("KNACK_CHANGE_OBJECT", "object_140")     # Campaign Change Requests
SUPPORT_OBJECT = os.environ.get("KNACK_SUPPORT_OBJECT", "object_121")   # Campaign Support Requests

_schema_cache: dict = {}


def configured() -> bool:
    return bool(os.environ.get("KNACK_APP_ID") and os.environ.get("KNACK_API_KEY"))


def _headers():
    return {"X-Knack-Application-Id": os.environ.get("KNACK_APP_ID", ""),
            "X-Knack-REST-API-Key": os.environ.get("KNACK_API_KEY", ""),
            "Content-Type": "application/json"}


def object_fields(obj: str) -> list[dict]:
    key = "fields:" + obj
    if key in _schema_cache:
        return _schema_cache[key]
    r = requests.get(f"{BASE}/objects/{obj}/fields", headers=_headers(), timeout=20)
    r.raise_for_status()
    fields = (r.json() or {}).get("fields", [])
    _schema_cache[key] = fields
    return fields


def _fields() -> list[dict]:
    return object_fields(TICKETS_OBJECT)


def field_label(f: dict) -> str:
    """A Knack field's display name.

    Knack's own /objects/<id>/fields returns it as `name`; this module has
    always read `label`, and the audit module at /tools/tickets reads `name`.
    One of the two was matching against None on every field — which is a
    silent, total failure of label matching, and exactly the sort of thing
    that made these ids get pinned in the first place. Reading both is the
    only version that cannot be wrong.
    """
    return str(f.get("label") or f.get("name") or "")


def _find_in(obj: str, *label_keywords, types=None) -> str | None:
    for kw in label_keywords:
        for f in object_fields(obj):
            label = field_label(f).lower()
            if kw in label and (not types or f.get("type") in types):
                return f.get("key")
    return None


def _find_field(*label_keywords, types=None) -> str | None:
    """First tickets-object field whose label contains any keyword."""
    return _find_in(TICKETS_OBJECT, *label_keywords, types=types)


# --- object_107 web tickets: confirmed field ids ------------------------
#
# These were discovered by matching field LABELS, which is why the Issue
# column on the Accounting report came back empty — a renamed label silently
# broke the lookup and nothing said so. These ids are confirmed against the
# live object, so a rename can't break them.
#
# Environment overrides exist for each so a Knack restructure doesn't need a
# code change.
TICKET_FIELDS = {
    "title":            "field_1895",   # Ticket title
    "client":           "field_1784",   # Client organization
    "media_partner":    "field_1785",   # Media Partner
    "partner_contact":  "field_2481",   # Partner Contact
    "notify_partner":   "field_1761",   # Should Partner receive Notifications?
    "website":          "field_2965",   # Client Website URL
    "type":             "field_2973",   # Type of ticket
    "billable":         "field_3160",   # Revision Requires Billing
    "pause_reason":     "field_2968",   # Pause Reason
    "cancel_reason":    "field_2969",   # Cancellation Reason
    "cancel_date":      "field_2970",   # Cancellation Date
    "resume_date":      "field_3010",   # Resume Date
    "web_services":     "field_3099",   # Web Services
    "new_website_url":  "field_1675",   # New Website URL
    "build_website":    "field_3262",   # Build Website
    "hosting_maint":    "field_3264",   # Hosting and Maintenance
    "hourly_maint":     "field_3266",   # Hourly Maintenance
    "purchase_domain":  "field_3263",   # Purchase Domain
    "hosting_only":     "field_3265",   # Hosting Only
    "ecommerce":        "field_3267",   # Ecommerce
    "description":      "field_1923",   # Describe the changes
    "ready_to_submit":  "field_1696",   # Are you ready to submit?
    "assigner":         "field_1653",   # Assigner
    "status":           "field_1657",   # Status
    "developer":        "field_1729",   # Developer
}

# The eight fields a web ticket is made of. The web team named nine and then
# took Partner Contact back off the form — its id stays pinned above and is
# still read, like the rest of the object.
#
# The wider set this module can read (partner contact, web services, the six
# service checkboxes, the new website URL, the pause and cancellation fields)
# is deliberately not written: a form asking for a field nobody fills is how
# twenty questions turned into four filled-in answers and sixteen blanks.
#
# Status, Developer and the lifecycle fields are not here either, and that is
# unchanged: Knack's own workflow opens a ticket, and assigning a developer is
# the web team's decision, not the raiser's.
TICKET_CREATE_FIELDS = (
    "title", "client", "media_partner", "website",
    "type", "billable", "description",
    # Last, because it is the act of submitting rather than a detail of the
    # ticket: Knack's own workflow reads it.
    "ready_to_submit",
)

# Fields editable in the Manage Ticket section — the same nine without Title,
# which is not editable after creation: renaming a ticket breaks the thread for
# whoever raised it.
TICKET_MANAGE_FIELDS = tuple(k for k in TICKET_CREATE_FIELDS if k != "title")


# The label Knack shows for each field. A form can then name a field before
# the live schema has been read, and a field whose label is renamed still
# reads as the name the team knows it by. The live label wins where there is
# one — this is the fallback, not the source of truth.
TICKET_LABELS = {
    "title":            "Ticket Title",
    "client":           "Client Organization",
    "media_partner":    "Media Partner",
    "partner_contact":  "Partner Contact",
    "notify_partner":   "Should Partner receive Notifications?",
    "website":          "Client Website URL",
    "type":             "Type of Ticket",
    "billable":         "Revision Requires Billing",
    "pause_reason":     "Pause Reason",
    "cancel_reason":    "Cancellation Reason",
    "cancel_date":      "Cancellation Date",
    "resume_date":      "Resume Date",
    "web_services":     "Web Services",
    "new_website_url":  "New Website URL",
    "build_website":    "Build Website",
    "hosting_maint":    "Hosting and Maintenance",
    "hourly_maint":     "Hourly Maintenance",
    "purchase_domain":  "Purchase Domain",
    "hosting_only":     "Hosting Only",
    "ecommerce":        "Ecommerce",
    "description":      "Describe the changes",
    "ready_to_submit":  "Are you ready to submit?",
    "assigner":         "Assigner",
    "status":           "Status",
    "developer":        "Developer",
}

# The order and grouping a ticket form is drawn in. A key that is in
# TICKET_CREATE_FIELDS or TICKET_MANAGE_FIELDS but missing here is appended
# under "Other" rather than dropped: a field added to one of those tuples and
# forgotten here appears at the end of the form instead of silently not
# existing at all, which is the failure this module has already had once.
TICKET_GROUPS = (
    ("The ticket",    ("title", "client", "website", "type", "billable",
                       "description")),
    ("Media partner", ("media_partner",)),
    ("Submit",        ("ready_to_submit",)),
)

# How many records a connection field's picker offers. A connection needs a
# record id, so the picker is the only way to write one from a form.
CONNECTION_LIMIT = int(os.environ.get("KNACK_CONNECTION_LIMIT", "500"))

_RECORD_ID = re.compile(r"^[0-9a-f]{24}$", re.I)


def field_ids() -> dict:
    """The pinned ids with their environment overrides applied.

    `field_map()` is this plus the two fields still discovered by label, which
    costs a schema read. This one touches nothing, so a caller that only wants
    the ids — the audit module at /tools/tickets builds its field map from
    them — does not have to reach Knack, or be import-order sensitive, to get
    what this file already knows.
    """
    return {key: (os.environ.get(f"KNACK_TICKET_{key.upper()}") or fid).strip()
            for key, fid in TICKET_FIELDS.items()}


def field_map() -> dict:
    """The confirmed ids, with a label-matched fallback for anything absent.

    Confirmed ids win. The old discovery is kept only for keys nobody has
    pinned yet, so an unmapped extra field still resolves rather than being
    silently dropped.
    """
    if "map" in _schema_cache:
        return _schema_cache["map"]
    m = field_ids()
    # Date isn't in the confirmed set — still discovered.
    m["date"] = _find_field("date created", "created", "date")
    m["requested_by"] = _find_field("requested by", "submitted by", "created by")
    _schema_cache["map"] = m
    return m


def ticket_value(rec: dict, key: str):
    """Read one mapped field off a Knack ticket record.

    Knack returns a `_raw` variant for connections and multiple-choice, which
    holds the usable value; the plain key holds display HTML. Preferring raw
    is what stops a connection field rendering as an anchor tag.
    """
    fid = field_map().get(key)
    if not fid:
        return ""
    raw = rec.get(f"{fid}_raw")
    if raw not in (None, "", []):
        if isinstance(raw, list):
            parts = []
            for item in raw:
                if isinstance(item, dict):
                    parts.append(item.get("identifier") or item.get("label")
                                 or item.get("value") or "")
                else:
                    parts.append(str(item))
            return ", ".join(p for p in parts if p)
        if isinstance(raw, dict):
            return (raw.get("identifier") or raw.get("label")
                    or raw.get("value") or "")
        return raw
    import re as _re
    return _re.sub(r"<[^>]+>", " ", str(rec.get(fid) or "")).strip()


def object_meta(obj: str) -> dict:
    """Live metadata for any object, keyed by field id."""
    return {f.get("key"): f for f in object_fields(obj)}


def _meta() -> dict:
    """Live metadata for the tickets object, keyed by field id."""
    return object_meta(TICKETS_OBJECT)


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def connection_choices(field_id: str, obj: str = TICKETS_OBJECT) -> list[dict]:
    """The records a connection field may point at, as {id, label}.

    A connection needs a Knack record id; writing the display name creates
    nothing and clears the link, which is why create_ticket has always
    skipped these fields entirely. Offering the real records is what lets a
    form write one at all.

    Never raises: a picker that cannot be built becomes a text box asking for
    an id, not a dead form.
    """
    key = f"conn:{obj}:{field_id}"
    if key in _schema_cache:
        return _schema_cache[key]
    out: list[dict] = []
    try:
        f = object_meta(obj).get(field_id) or {}
        # The object this connection POINTS AT, which is not the object the
        # field lives on — named apart from `obj` so the two cannot be
        # confused now that the owning object is a parameter.
        target = (f.get("relationship") or {}).get("object") or ""
        ident = _object_identifier(target) if target else None
        if target and ident:
            r = requests.get(f"{BASE}/objects/{target}/records", headers=_headers(),
                             params={"rows_per_page": CONNECTION_LIMIT,
                                     "sort_field": ident, "sort_order": "asc"},
                             timeout=20)
            r.raise_for_status()
            for rec in (r.json() or {}).get("records", []):
                label = _plain(rec.get(f"{ident}_raw") or rec.get(ident))
                if label and rec.get("id"):
                    out.append({"id": rec.get("id"), "label": label})
    except Exception:            # noqa: BLE001 — see the docstring
        out = []
    _schema_cache[key] = out
    return out


def _control_for(f: dict, obj: str = TICKETS_OBJECT) -> tuple[str, list]:
    """Which control a field needs, and what it may be set to.

    Every answer a field publishes is offered: a connection becomes a picker
    of the records it may point at, a multiple choice becomes its own choices,
    a boolean becomes yes/no. What is deliberately never invented is a choice
    Knack has not published — a form that guesses one writes a value Knack
    refuses, and Knack refuses the whole record rather than the field. A field
    with no published choices degrades to a text box, which is honest.
    """
    t = f.get("type")
    fmt = f.get("format") or {}
    if t == "connection":
        return "connection", connection_choices(f.get("key"), obj)
    if t == "multiple_choice":
        opts = [str(o) for o in (fmt.get("options") or []) if str(o).strip()]
        multi = str(fmt.get("type") or "").lower() in ("multi", "checkboxes")
        return ("multi" if multi else "select"), opts
    if t == "boolean":
        return "boolean", []
    if t in ("date_time", "date"):
        return "date", []
    if t in ("file", "image"):
        # A Knack file field is written by a separate upload call, not by a
        # value on the record — so a text box here would accept a filename and
        # drop it. The form draws a note instead and says where files go.
        return "file", []
    if t in ("paragraph_text", "rich_text"):
        return "textarea", []
    return "text", []


def ticket_form_fields(scope: str = "create") -> list[dict]:
    """What a ticket form should draw, read from the live object.

    The field ids are pinned in TICKET_FIELDS, but the *controls* cannot be:
    a dropdown's choices live in Knack, and a form that guesses them writes a
    value Knack refuses — which loses the whole record, not the one field.
    So the choices come from the schema and the ids come from us.
    """
    allowed = TICKET_MANAGE_FIELDS if scope == "manage" else TICKET_CREATE_FIELDS
    m = field_map()
    meta = _meta()
    ordered = [(g, k) for g, keys in TICKET_GROUPS for k in keys if k in allowed]
    placed = {k for _, k in ordered}
    ordered += [("Other", k) for k in allowed if k not in placed]
    seen, out = set(), []
    for group, key in ordered:
        if key in seen:
            continue
        seen.add(key)
        fid = m.get(key)
        f = meta.get(fid) or {}
        control, choices = _control_for(f) if f else ("text", [])
        out.append({
            "key": key,
            "field": fid,
            "group": group,
            "label": field_label(f) or TICKET_LABELS.get(key, key),
            "control": control,
            "choices": choices,
            "required": bool(f.get("required")),
            # False means the pinned id is not on the object any more. The
            # form still draws the field, and says so, rather than dropping it.
            "known": bool(f),
        })
    return out


def coerce_field(field_id: str, value, *, obj: str = TICKETS_OBJECT,
                 label: str = "", meta: dict | None = None) -> tuple:
    """(what to write, why it cannot be written) — exactly one is set.

    A value Knack would refuse is refused here, with a reason. Knack rejects
    the whole record rather than the offending field, so a mistyped dropdown
    choice would cost the record, not the field; caught here it costs the
    choice and the caller is told which one.

    Object-agnostic on purpose: object_153's website records are written the
    same way object_107's tickets are, and a second copy of these rules is a
    second set of rules to keep in step.
    """
    meta = object_meta(obj) if meta is None else meta
    f = meta.get(field_id) or {}
    label = field_label(f) or label or field_id
    control, choices = _control_for(f, obj) if f else ("text", [])
    if value in (None, "", [], {}):
        return None, ""
    if control == "connection":
        wanted = value if isinstance(value, list) else [value]
        ids = []
        for v in wanted:
            v = str(v).strip()
            if _RECORD_ID.match(v):
                ids.append(v)
                continue
            hit = [c for c in choices if _norm(c.get("label")) == _norm(v)]
            if len(hit) == 1:
                ids.append(hit[0]["id"])
            elif choices:
                return None, (f"{label}: \u201c{v}\u201d matches no record on "
                              f"this connection (of {len(choices)})")
            else:
                return None, f"{label}: needs a Knack record id, not a name"
        return (ids if len(ids) > 1 else ids[0]), ""
    if control == "file":
        # Named rather than dropped: a file field is written by Knack's own
        # upload call, and a filename put on the record writes nothing.
        return None, (f"{label}: files are uploaded in Knack, not written "
                      "with the record")
    if control == "boolean":
        s = str(value).strip().lower()
        if s in ("1", "true", "yes", "y", "on", "checked"):
            return True, ""
        if s in ("0", "false", "no", "n", "off"):
            return False, ""
        return None, f"{label}: expected yes or no, got \u201c{value}\u201d"
    if control in ("select", "multi"):
        if not choices:                 # no choices published — treat as text
            return str(value), ""
        wanted = value if isinstance(value, list) else [value]
        picked = []
        for v in wanted:
            hit = [o for o in choices if _norm(o) == _norm(v)]
            if not hit:
                return None, (f"{label}: \u201c{v}\u201d is not one of its "
                              f"{len(choices)} choices")
            picked.append(hit[0])
        return (picked if control == "multi" else picked[0]), ""
    return str(value), ""


def coerce_value(key: str, value, meta: dict | None = None) -> tuple:
    """The ticket-object wrapper: a logical ticket key, coerced for writing."""
    fid = field_map().get(key)
    label = TICKET_LABELS.get(key, key)
    if not fid:
        return None, f"{label}: no field id is mapped"
    return coerce_field(fid, value, obj=TICKETS_OBJECT, label=label,
                        meta=_meta() if meta is None else meta)


def _build_payload(allowed, values: dict, meta: dict | None = None) -> tuple:
    """Turn {logical key: value} into {field id: value}, plus what was refused."""
    meta = _meta() if meta is None else meta
    m = field_map()
    payload, rejected = {}, []
    for key, value in (values or {}).items():
        if key not in allowed:
            rejected.append(f"{key}: not writable here")
            continue
        out, why = coerce_value(key, value, meta)
        if why:
            rejected.append(why)
            continue
        if out is None:
            continue
        payload[m[key]] = out
    return payload, rejected


def form_value(rec: dict, key: str, meta: dict | None = None):
    """The value a form control should open on.

    ticket_value() is the human reading of a field; a form needs what can be
    written back — a record id for a connection, a list for a multi-select.
    Writing a connection's label back is what clears the link.
    """
    fid = field_map().get(key)
    if not fid:
        return ""
    meta = _meta() if meta is None else meta
    f = meta.get(fid) or {}
    raw = rec.get(f"{fid}_raw")
    if f.get("type") == "connection":
        items = raw if isinstance(raw, list) else ([raw] if raw else [])
        ids = [i.get("id") for i in items if isinstance(i, dict) and i.get("id")]
        return ids[0] if len(ids) == 1 else ids
    if f.get("type") == "boolean":
        return "" if raw in (None, "") else bool(raw)
    if f.get("type") == "multiple_choice" and isinstance(raw, list):
        return [str(x) for x in raw]
    return ticket_value(rec, key)


def create_ticket(client: str, website: str, subject: str,
                  description: str, author: str = "",
                  requested_by: str = "", ticket_type: str = "",
                  billable: str = "", extra: dict | None = None,
                  values: dict | None = None) -> dict:
    """Create a web ticket in Knack against the confirmed field ids.

    `values` is the rest of the ticket, keyed by TICKET_FIELDS name: the media
    partner and their contact, the type, the billing flag, and the ready-to-
    submit answer Knack's workflow reads. Only the keys in
    TICKET_CREATE_FIELDS are accepted, and each is checked against the live
    field before it is sent — Knack refuses a whole record over one bad
    dropdown value, so a value it would refuse is dropped here and named in
    the result instead.

    Status and Developer are not written — Knack's own workflow sets the
    opening status, and assigning a developer is the web team's decision.

    `extra` is {field_id: value} for fields this signature does not name — the
    due date the SEO tasks set, for one. It keeps the old connection guard, so
    a caller cannot write display text into a connection field by accident.

    The returned record carries `written` and `rejected`: what reached Knack,
    and what did not and why. A field that was quietly dropped is how a form
    comes to look like it works while half of it goes nowhere.
    """
    meta = _meta()
    m = field_map()
    vals = {k: v for k, v in (values or {}).items()
            if v not in (None, "", [], {})}
    body = description
    if author:
        body = f"{description}\n\n— submitted by {author} via Smart 1 Hub"
    vals["title"] = subject[:120]
    vals["description"] = body
    # The named arguments are defaults: an explicit value from the form wins.
    for key, value in (("client", client), ("website", website),
                       ("type", ticket_type), ("billable", billable)):
        if value and not vals.get(key):
            vals[key] = value
    payload, rejected = _build_payload(TICKET_CREATE_FIELDS, vals, meta)

    # Attribution, written but never asked for: Assigner is pinned, Requested
    # By is discovered by label, and neither is a question anyone answers — so
    # neither is in the write set. They are still written, because a ticket the
    # web team cannot put a name to is a ticket they have to come asking about.
    who = requested_by or author
    for fid in (m.get("assigner"), m.get("requested_by")):
        if fid and who and meta.get(fid, {}).get("type") != "connection":
            payload.setdefault(fid, who)

    for fid, value in (extra or {}).items():
        if not fid or value in (None, ""):
            continue
        if meta.get(fid, {}).get("type") == "connection":
            rejected.append(f"{fid}: connection fields need a record id")
            continue
        payload.setdefault(fid, value)

    if not payload:
        raise RuntimeError(
            "Nothing writable resolved on "
            f"{TICKETS_OBJECT}. The field ids are pinned in TICKET_FIELDS, so "
            "this means the object itself changed — check it in Knack."
            + (f" Refused: {'; '.join(rejected)}" if rejected else ""))
    r = requests.post(f"{BASE}/objects/{TICKETS_OBJECT}/records",
                      headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Knack rejected the ticket (HTTP {r.status_code}): {r.text[:200]}")
    rec = r.json()
    if not isinstance(rec, dict):
        return rec
    return {**rec, "written": sorted(payload), "rejected": rejected}


# ---------------- Campaign Change (object_140) / Support (object_121) ----
#
# Two objects, two ways of finding their fields, and the difference is the
# whole reason this section is shaped the way it is.
#
# Campaign Support (object_121) has its ids pinned below, from the list the
# campaign team gave us. Campaign Change (object_140) does not, so it is still
# matched by label — the thing that broke silently on object_107 and is why
# TICKET_FIELDS was pinned in the first place. A change request therefore
# still writes six fields; a support request writes twenty-two, each checked
# against the live field before it is sent.
#
# Both come out of campaign_form_fields() in the same shape, so the form has
# one code path and a field pinned for object_140 later needs no new one.


def campaign_object(kind: str) -> str:
    return CHANGE_OBJECT if kind == "change" else SUPPORT_OBJECT


# --- object_121 campaign support requests: confirmed field ids ----------
#
# Pinned, not discovered, for the reason TICKET_FIELDS gives: a renamed label
# breaks label matching silently, and a support request that quietly wrote
# four of its twenty-three fields is exactly the state the web ticket was in
# before its ids were pinned. Each is overridable by environment variable
# (KNACK_SUPPORT_<KEY>) so a Knack restructure is a variable, not a release.
SUPPORT_FIELDS = {
    "client":           "field_2597",   # Client
    "campaign":         "field_2614",   # Campaign
    "io_number":        "field_2613",   # IO#
    "insertion_order":  "field_2593",   # Insertion Order
    "io_product":       "field_2793",   # IO Product
    "product":          "field_3419",   # Product
    "support_type":     "field_1818",   # Campaign Support
    "description":      "field_1819",   # Describe Your Campaign Support Issue
    "pixel_url":        "field_2851",   # URL for Pixel to Add
    "notes":            "field_2612",   # Notes
    "uploaded_files":   "field_1820",   # Uploaded Files
    "due_date":         "field_1859",   # Due Date
    "timeline":         "field_2780",   # Timeline
    "rush":             "field_2786",   # Rush
    "rush_reason":      "field_2787",   # Reason for Rush
    "media_partner":    "field_2596",   # Media Partner
    "partner_contact":  "field_2608",   # Partner Contact
    "client_contact":   "field_2609",   # Client Contact
    "notify_client":    "field_2610",   # Notify Client?
    "notify_partner":   "field_2611",   # Notify Partner?
    "iop_status":       "field_3347",   # IOP Status
    "submitted_by":     "field_2595",   # Submitted By
    "submitted_date":   "field_1867",   # Submitted Date
}

# The label Knack shows for each, so a form can name a field before the live
# schema has been read and a renamed label still reads as the name the team
# knows it by. The live label wins where there is one.
SUPPORT_LABELS = {
    "client":           "Client",
    "campaign":         "Campaign",
    "io_number":        "IO#",
    "insertion_order":  "Insertion Order",
    "io_product":       "IO Product",
    "product":          "Product",
    "support_type":     "Campaign Support",
    "description":      "Describe Your Campaign Support Issue",
    "pixel_url":        "URL for Pixel to Add",
    "notes":            "Notes",
    "uploaded_files":   "Uploaded Files",
    "due_date":         "Due Date",
    "timeline":         "Timeline",
    "rush":             "Rush",
    "rush_reason":      "Reason for Rush",
    "media_partner":    "Media Partner",
    "partner_contact":  "Partner Contact",
    "client_contact":   "Client Contact",
    "notify_client":    "Notify Client?",
    "notify_partner":   "Notify Partner?",
    "iop_status":       "IOP Status",
    "submitted_by":     "Submitted By",
    "submitted_date":   "Submitted Date",
}

# The order and grouping the form is drawn in. A key in SUPPORT_CREATE_FIELDS
# and missing here is appended under "Other" rather than dropped — the
# TICKET_GROUPS rule, for the same reason: a field added to the write set and
# forgotten here appears at the end of the form instead of silently not
# existing at all.
SUPPORT_GROUPS = (
    ("Which campaign",  ("client", "insertion_order", "campaign", "io_number",
                         "io_product", "product")),
    ("What you need",   ("support_type", "description", "pixel_url", "notes",
                         "uploaded_files")),
    ("When",            ("due_date", "timeline", "rush", "rush_reason")),
    ("Who to tell",     ("media_partner", "partner_contact", "client_contact",
                         "notify_client", "notify_partner")),
    ("Status",          ("iop_status",)),
    ("Submitted",       ("submitted_date",)),
)

# Everything on the list except Uploaded Files, which is a Knack file field:
# it is written by Knack's own upload call, not by a value on the record, so a
# box for it here would take a filename and drop it. It is still drawn — as a
# note saying where files go — because a deliverable left off the form
# entirely is one nobody knows to supply.
SUPPORT_CREATE_FIELDS = tuple(
    k for k in SUPPORT_FIELDS if k != "uploaded_files")

# Drawn, and not asked for. Submitted By is answered by the Requested by
# picker at the top of the form, which is the same control the web ticket
# uses and reads the same people objects; drawing it twice is two answers to
# one question.
SUPPORT_NOT_ASKED = ("submitted_by",)

# The roles the label-matched change request still writes. Named here so the
# two kinds come back in one shape.
CHANGE_LABELS = {
    "title": "Subject",
    "description": "Details",
    "client": "Client name",
    "campaign": "Campaign / product",
    "io": "IO number",
    "requested_by": "Requested by",
}
CHANGE_GROUPS = (
    ("The request", ("title", "client", "campaign", "io", "description")),
)
CHANGE_CREATE_FIELDS = ("title", "description", "client", "campaign",
                        "io", "requested_by")


def support_field_ids() -> dict:
    """The pinned support ids with their environment overrides applied.

    Touches nothing — the ids are ours, so a caller that only wants them does
    not have to reach Knack, the reason field_ids() exists beside field_map().
    """
    return {key: (os.environ.get(f"KNACK_SUPPORT_{key.upper()}") or fid).strip()
            for key, fid in SUPPORT_FIELDS.items()}


def campaign_field_map(kind: str) -> dict:
    """Best-effort label→field mapping for the campaign request objects,
    with the matched labels included so the UI can show exactly what will
    be written before anything is sent.

    Support requests resolve from the pinned ids above; a change request is
    still matched by label, because nobody has pinned object_140 yet.
    """
    obj = campaign_object(kind)
    cache_key = f"cmap:{obj}"
    if cache_key in _schema_cache:
        return _schema_cache[cache_key]
    labels = {f.get("key"): field_label(f) for f in object_fields(obj)}
    if kind == "support":
        m = support_field_ids()
    else:
        text_types = ("short_text", "paragraph_text", "rich_text")
        m = {
            "title": _find_in(obj, "request name", "subject", "title", "request",
                              "name", types=text_types),
            "description": _find_in(obj, "description", "details", "change",
                                    "request", "notes", "issue",
                                    types=("paragraph_text", "rich_text", "short_text")),
            "client": _find_in(obj, "client", "customer", "business", types=text_types),
            "campaign": _find_in(obj, "campaign", "product", types=text_types),
            "io": _find_in(obj, "io", "insertion", "order", types=text_types),
            "requested_by": _find_in(obj, "requested by", "submitted by",
                                     "created by", types=text_types),
        }
        # never map two purposes onto the same field
        seen = set()
        for k in list(m):
            if m[k] in seen:
                m[k] = None
            elif m[k]:
                seen.add(m[k])
    fallback = SUPPORT_LABELS if kind == "support" else CHANGE_LABELS
    out = {"object": obj,
           "map": {k: {"field": v, "label": labels.get(v) or fallback.get(k, "")}
                   if v else None
                   for k, v in m.items()},
           "all_fields": [{"key": f.get("key"), "label": field_label(f),
                           "type": f.get("type")} for f in object_fields(obj)]}
    _schema_cache[cache_key] = out
    return out


def campaign_form_fields(kind: str) -> list[dict]:
    """What a campaign request form should draw, read from the live object.

    The ids are ours and the *controls* are Knack's: a dropdown's choices, the
    records a connection may point at, whether a field is a date or a
    paragraph. A form that guesses any of those writes a value Knack refuses,
    and Knack refuses the whole record rather than the field — so every option
    on this form comes off the live schema, and a field that publishes none
    comes back as a text box rather than an empty picker.
    """
    obj = campaign_object(kind)
    ids = campaign_field_map(kind)["map"]
    allowed = SUPPORT_CREATE_FIELDS if kind == "support" else CHANGE_CREATE_FIELDS
    groups = SUPPORT_GROUPS if kind == "support" else CHANGE_GROUPS
    labels = SUPPORT_LABELS if kind == "support" else CHANGE_LABELS
    not_asked = SUPPORT_NOT_ASKED if kind == "support" else ("requested_by",)
    meta = object_meta(obj)
    # What the form draws is the write set plus Uploaded Files, which is drawn
    # and cannot be written: see the note on SUPPORT_CREATE_FIELDS. Carried in
    # here rather than by widening `allowed`, which decides what a POST may
    # contain.
    drawn = tuple(allowed) + (("uploaded_files",) if kind == "support" else ())
    ordered = [(g, k) for g, keys in groups for k in keys
               if k in drawn and k not in not_asked]
    placed = {k for _, k in ordered}
    ordered += [("Other", k) for k in drawn
                if k not in placed and k not in not_asked]
    seen, out = set(), []
    for group, key in ordered:
        if key in seen:
            continue
        seen.add(key)
        slot = ids.get(key)
        fid = slot["field"] if isinstance(slot, dict) else slot
        f = meta.get(fid) or {}
        control, choices = _control_for(f, obj) if f else ("text", [])
        writable = control != "file" and bool(fid)
        out.append({
            "key": key,
            "field": fid or "",
            "group": group,
            "label": field_label(f) or labels.get(key, key),
            "control": control,
            "choices": choices,
            "required": bool(f.get("required")),
            # A file field is drawn and cannot be written: see the note on
            # SUPPORT_CREATE_FIELDS. So is a role nothing on the object
            # matched — a box whose value would be dropped is worse than a
            # line saying why there is no box.
            "writable": writable,
            "hint": ("" if writable else
                     ("Attached in Knack after the request is created — files "
                      "cannot be sent with it." if control == "file" else
                      f"No matching field on {obj} — this would be skipped.")),
            # False means the pinned id is not on the object any more. The
            # form still draws the field, and says so, rather than dropping it.
            "known": bool(f),
        })
    return out


def _campaign_payload(kind: str, values: dict, meta: dict | None = None) -> tuple:
    """{role: value} → {field id: value}, plus what was refused.

    Every value goes through coerce_field against the live field, so a
    dropdown choice this object does not publish, a connection handed a name
    that matches no record, or a file field handed a filename is refused here
    by name — rather than costing the whole request in Knack, which rejects
    the record over one bad value rather than the field.

    One builder for both objects. The support ids are pinned and the change
    ids are label-matched, and that difference is entirely upstream of here:
    what reaches this function is a role, a field id and a value.
    """
    obj = campaign_object(kind)
    meta = object_meta(obj) if meta is None else meta
    info = campaign_field_map(kind)
    ids = {k: (slot or {}).get("field") for k, slot in info["map"].items()}
    known = SUPPORT_FIELDS if kind == "support" else CHANGE_LABELS
    allowed = SUPPORT_CREATE_FIELDS if kind == "support" else CHANGE_CREATE_FIELDS
    labels = SUPPORT_LABELS if kind == "support" else CHANGE_LABELS
    payload, rejected = {}, []
    for key, value in (values or {}).items():
        label = labels.get(key, key)
        if key not in known:
            rejected.append(f"{key}: not a field on this request")
            continue
        if key not in allowed:
            rejected.append(f"{label}: not writable here")
            continue
        fid = ids.get(key)
        if not fid:
            # A role nothing on the object matched. Named, because a value
            # dropped in silence is how a form comes to look like it works.
            rejected.append(f"{label}: no matching field on {obj}")
            continue
        out, why = coerce_field(fid, value, obj=obj, label=label, meta=meta)
        if why:
            rejected.append(why)
            continue
        if out is None:
            continue
        payload[fid] = out
    return payload, rejected


def create_campaign_request(kind: str, client: str, campaign: str, io: str,
                            subject: str, description: str,
                            author: str = "", requested_by: str = "",
                            values: dict | None = None) -> dict:
    """Create a campaign change or support request in Knack.

    `values` is the rest of the request, keyed by SUPPORT_FIELDS name for a
    support request and by role for a change request. The named arguments stay
    the defaults they have always been: an explicit value from the form wins.

    The returned record carries `written` and `rejected` — what reached Knack
    and what did not and why. A field quietly dropped is how a form comes to
    look like it works while half of it goes nowhere.
    """
    obj = campaign_object(kind)
    meta = object_meta(obj)
    vals = {k: v for k, v in (values or {}).items()
            if v not in (None, "", [], {})}
    body = description
    if author:
        body = f"{description}\n\n— submitted by {author} via Smart 1 Hub"

    if kind == "support":
        # object_121 publishes no subject field, so the subject leads the
        # issue rather than being written somewhere it does not belong — the
        # label-matched map used to put it on whichever field matched "name"
        # first, which is how a subject came to land on a field nobody reads.
        issue = f"{subject}\n\n{body}".strip() if subject else body
        defaults = (("client", client), ("campaign", campaign),
                    ("io_number", io), ("description", issue),
                    ("submitted_by", requested_by or author))
    else:
        defaults = (("title", subject[:120]), ("description", body),
                    ("client", client), ("campaign", campaign), ("io", io),
                    ("requested_by", requested_by or author))
    for key, value in defaults:
        if value and not vals.get(key):
            vals[key] = value

    payload, rejected = _campaign_payload(kind, vals, meta)
    if not payload:
        raise RuntimeError(
            f"Nothing writable resolved on {obj}."
            + (" The support ids are pinned in SUPPORT_FIELDS, so this means "
               "the object itself changed — check it in Knack."
               if kind == "support"
               else " Check the object's field labels.")
            + (f" Refused: {'; '.join(rejected)}" if rejected else ""))
    r = requests.post(f"{BASE}/objects/{obj}/records",
                      headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Knack rejected the request (HTTP {r.status_code}): {r.text[:200]}")
    rec = r.json()
    if not isinstance(rec, dict):
        return rec
    return {**rec, "written": sorted(payload), "rejected": rejected}


# ---------------- People (Requested By dropdown: object_161 + object_109) ----
PEOPLE_OBJECTS = tuple(
    x.strip() for x in os.environ.get(
        "KNACK_PEOPLE_OBJECTS", "object_161,object_109").split(",") if x.strip())


def _object_identifier(obj: str) -> str | None:
    """The object's display/identifier field key."""
    key = "ident:" + obj
    if key in _schema_cache:
        return _schema_cache[key]
    ident = None
    try:
        r = requests.get(f"{BASE}/objects/{obj}", headers=_headers(), timeout=20)
        if r.ok:
            ident = ((r.json() or {}).get("object") or {}).get("identifier")
    except requests.RequestException:
        ident = None
    if not ident:
        for f in object_fields(obj):
            if f.get("type") in ("name", "short_text"):
                ident = f.get("key")
                break
    _schema_cache[key] = ident
    return ident


def people_names() -> list[str]:
    """Names pulled from the people objects, merged + deduped, for the
    Requested By dropdowns."""
    if "people" in _schema_cache:
        return _schema_cache["people"]
    names = set()
    for obj in PEOPLE_OBJECTS:
        try:
            ident = _object_identifier(obj)
            if not ident:
                continue
            r = requests.get(f"{BASE}/objects/{obj}/records",
                             headers=_headers(),
                             params={"rows_per_page": 1000,
                                     "sort_field": ident, "sort_order": "asc"},
                             timeout=20)
            r.raise_for_status()
            for rec in (r.json() or {}).get("records", []):
                n = _plain(rec.get(ident))
                if n:
                    names.add(n)
        except Exception:  # noqa: BLE001 — one bad object shouldn't kill the list
            continue
    out = sorted(names, key=str.lower)
    _schema_cache["people"] = out
    return out


def _plain(v) -> str:
    if isinstance(v, dict):
        return str(v.get("identifier") or v.get("value") or
                   v.get("url") or v.get("date") or "")
    if isinstance(v, list):
        return ", ".join(_plain(x) for x in v)
    return re.sub(r"<[^>]+>", " ", str(v or "")).strip()


def list_tickets(client: str, website: str = "", limit: int = 25) -> list[dict]:
    """Tickets whose client/website field mentions this client (or domain)."""
    m = field_map()
    rules = []
    if m["client"]:
        rules.append({"field": m["client"], "operator": "contains", "value": client})
    dom = str(website or "").replace("https://", "").replace("http://", "").split("/")[0]
    if m["website"] and dom:
        rules.append({"field": m["website"], "operator": "contains", "value": dom})
    if not rules:
        return []
    filters = {"match": "or", "rules": rules}
    r = requests.get(f"{BASE}/objects/{TICKETS_OBJECT}/records",
                     headers=_headers(),
                     params={"filters": json.dumps(filters),
                             "rows_per_page": int(limit),
                             "sort_field": m["date"] or "id",
                             "sort_order": "desc"},
                     timeout=20)
    r.raise_for_status()
    meta = _meta()
    out = []
    for rec in (r.json() or {}).get("records", []):
        row = {
            "id": rec.get("id"),
            "title": _plain(rec.get(m["title"])) if m["title"] else "(ticket)",
            "description": _plain(rec.get(m["description"]))[:400] if m["description"] else "",
            "website": _plain(rec.get(m["website"])) if m["website"] else "",
            "status": _plain(rec.get(m["status"])) if m["status"] else "",
            "date": _plain(rec.get(m["date"])) if m["date"] else "",
        }
        # Everything Manage Ticket can edit, in the form the form needs, so
        # opening a ticket to change one field costs no second read — and so
        # the type, the billing flag and the web services are visible at all,
        # which is the whole reason those ids were pinned.
        row["values"] = {k: form_value(rec, k, meta) for k in TICKET_MANAGE_FIELDS}
        row["shown"] = {k: ticket_value(rec, k) for k in
                        ("type", "billable", "developer", "media_partner",
                         "web_services")}
        out.append(row)
    return out


def update_ticket(record_id: str, values: dict | None = None, **kw) -> dict:
    """Update a ticket from the Manage section.

    Only the keys in TICKET_MANAGE_FIELDS may be written, so a mistyped key
    cannot quietly write to something else on the record — and Title stays
    read-only after creation, since renaming a ticket breaks the thread for
    whoever raised it.

    Connections are writable now: a record id goes through as given, and a
    name that matches exactly one record on the connected object is resolved
    to its id. Anything else is refused by name in `rejected` rather than
    written as display text, which would clear the link.
    """
    payload, rejected = _build_payload(TICKET_MANAGE_FIELDS,
                                       {**(values or {}), **kw})
    if not payload:
        return {"ok": False, "error": "Nothing to update.", "rejected": rejected}
    r = requests.put(f"{BASE}/objects/{TICKETS_OBJECT}/records/{record_id}",
                     headers=_headers(), json=payload, timeout=20)
    if not r.ok:
        return {"ok": False, "error": f"Knack returned HTTP {r.status_code}.",
                "rejected": rejected}
    return {"ok": True, "updated": list(payload), "rejected": rejected}


# --- object_135 IO products: writing a dashboard URL ----------------------

PRODUCTS_OBJECT = os.environ.get("KNACK_PRODUCTS_OBJECT", "object_135")
F_DASHBOARD_URL = os.environ.get("KNACK_DASHBOARD_FIELD", "field_2820")
F_CLIENT_NAME = os.environ.get("KNACK_PRODUCT_CLIENT_FIELD", "field_2308")


def set_dashboard_url(client: str, url: str, live_only: bool = True) -> dict:
    """Write a dashboard URL onto a client's product records.

    Writes to every matching product rather than one, because the report asks
    "does this client have a dashboard on ANY live product" — setting it on a
    single row would leave the client on the list.

    Returns what it changed. Never partial-silent: if some rows fail, the
    count says so rather than reporting success.
    """
    if not configured():
        return {"ok": False, "error": "Knack API credentials aren't set."}
    if not str(url or "").strip().startswith("http"):
        return {"ok": False, "error": "That doesn't look like a URL."}

    import re as _re
    want = _re.sub(r"[^a-z0-9]+", "", str(client or "").lower())
    if not want:
        return {"ok": False, "error": "No client given."}

    try:
        r = requests.get(
            f"{BASE}/objects/{PRODUCTS_OBJECT}/records",
            headers=_headers(),
            params={"rows_per_page": 1000, "page": 1},
            timeout=45)
        r.raise_for_status()
        records = (r.json() or {}).get("records") or []
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"Couldn't read products ({type(exc).__name__})."}

    targets = []
    for rec in records:
        raw = rec.get(f"{F_CLIENT_NAME}_raw") or rec.get(F_CLIENT_NAME) or ""
        if isinstance(raw, list):
            raw = " ".join(str((x or {}).get("identifier", x)) for x in raw)
        if isinstance(raw, dict):
            raw = raw.get("identifier") or ""
        name = _re.sub(r"<[^>]+>", " ", str(raw))
        if _re.sub(r"[^a-z0-9]+", "", name.lower()) != want:
            continue
        if live_only and str(rec.get("field_2300") or "").lower() != "live":
            continue
        targets.append(rec.get("id"))

    if not targets:
        return {"ok": False,
                "error": f"No {'live ' if live_only else ''}products found for "
                         f"{client} in Knack."}

    updated, failed = 0, []
    for rid in targets:
        try:
            resp = requests.put(
                f"{BASE}/objects/{PRODUCTS_OBJECT}/records/{rid}",
                headers=_headers(), json={F_DASHBOARD_URL: url}, timeout=25)
            if resp.ok:
                updated += 1
            else:
                failed.append(f"{rid}: HTTP {resp.status_code}")
        except Exception as exc:                        # noqa: BLE001
            failed.append(f"{rid}: {type(exc).__name__}")

    return {"ok": updated > 0, "updated": updated, "failed": failed,
            "note": (f"Dashboard URL written to {updated} product(s)."
                     + (f" {len(failed)} failed." if failed else ""))}
