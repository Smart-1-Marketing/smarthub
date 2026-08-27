"""The Ad Copy Request object — pinned ids, a form drawn from the live
schema, and everything about it this Hub can already answer.

Ad Copy used to be a Campaign Change Request with its subject pre-written:
one object, four boxes, and a rep retyping the client, the campaign, the
order number and the media partner into a form that had all four on the
screen behind it. Every one of those is on the client's own insertion
orders, and the seller and the confirmation address are on the signed-in
account. So the form asks for what nobody else knows and offers the rest.

The field ids are the ones the campaign team gave us, pinned here for the
reason `hub/knack_api.py` gives at length: label matching broke silently
when a label was renamed, and nothing said so. Each is overridable by
environment variable so a Knack restructure is a variable rather than a
deploy.

What is NOT pinned is the *object*. Nobody has told us its number, and
inventing one writes ad copy requests into whatever object happens to
answer — so it is discovered from the pinned ids: whichever object carries
`field_1804` is the Ad Copy Request object. The answer is cached for the
process, `KNACK_AD_COPY_OBJECT` pins it outright, and a discovery that
fails says so by name rather than falling back to a guess.

Three rules the prefill works to, each of which is a way to be confidently
wrong on a form somebody sends without re-reading:

  * **Exactly one candidate, or none.** A client with two campaigns gets a
    dropdown, not the first one. Filling in a plausible campaign name is
    worse than filling in nothing, because nobody proof-reads the box that
    was already answered.
  * **Nothing is invented.** The due date, the deadline and the change
    itself are blank — there is no source for them here, and a date the
    Hub made up is a date the campaign team works to.
  * **An empty answer says which kind of empty it is.** "This client has
    no insertion orders", "we could not read the client list" and "the
    session has no account behind it, so there is no email address" are
    three different situations, and only the first is normal.
"""
import os
import re
from datetime import date

from . import knack_api

# The list the campaign team gave us, verbatim. A rename in Knack cannot
# break these; a renumber is one environment variable each.
AD_COPY_FIELDS = {
    "seller":        "field_1804",   # Seller Name
    "confirm_email": "field_1805",   # Confirmation Email Address
    "client":        "field_1806",   # Client Name
    "campaign":      "field_1807",   # Campaign Name
    "order_number":  "field_1808",   # Current Order Number
    "change_for":    "field_1809",   # Change for What?
    "when":          "field_1810",   # When Should This Change?
    "url_changing":  "field_1811",   # Is The URL Changing?
    "anything_else": "field_1812",   # Is there Something Else we need to know?
    "files":         "field_1813",   # Uploaded Files
    "media_partner": "field_1851",   # Media Partner
    "due_date":      "field_1853",   # Due Date
    "status":        "field_1854",   # Status
    "submitted":     "field_1866",   # Submitted Date
}

# The label Knack shows for each, so the form can name a field before the
# live schema has been read and a renamed field still reads as the name the
# team knows it by. The live label wins where there is one.
AD_COPY_LABELS = {
    "seller":        "Seller Name",
    "confirm_email": "Confirmation Email Address",
    "client":        "Client Name",
    "campaign":      "Campaign Name",
    "order_number":  "Current Order Number",
    "change_for":    "Change for What?",
    "when":          "When Should This Change?",
    "url_changing":  "Is The URL Changing?",
    "anything_else": "Is there Something Else we need to know?",
    "files":         "Uploaded Files",
    "media_partner": "Media Partner",
    "due_date":      "Due Date",
    "status":        "Status",
    "submitted":     "Submitted Date",
}

# Everything on the object except the file field. A Knack file field is not
# written by posting a record: the bytes go to Knack's own asset endpoint
# first and the record then carries the id it hands back. Sending a string
# into it writes nothing, and — because Knack refuses the whole record over
# one bad value — would cost the request rather than the attachment. It is
# drawn on the form, disabled, saying where files go instead, because a
# field that silently is not there is how somebody sends a request believing
# the artwork went with it.
AD_COPY_CREATE_FIELDS = tuple(k for k in AD_COPY_FIELDS if k != "files")

# The order and grouping the form is drawn in, in the order the work is
# done: who is asking, which campaign, what is changing, then the dates the
# queue is worked by. A key in AD_COPY_CREATE_FIELDS and missing here is
# appended under "Other" rather than dropped — the failure `hub/knack_api.py`
# has already had once.
AD_COPY_GROUPS = (
    ("Who is asking",   ("seller", "confirm_email")),
    ("Which campaign",  ("client", "campaign", "order_number", "media_partner")),
    ("What is changing", ("change_for", "when", "url_changing", "anything_else")),
    ("Files",           ("files",)),
    ("Dates",           ("due_date", "submitted", "status")),
)

# What `current_user()` can answer that is not somebody's name. A shared
# password grants a session with no account behind it — `hub/presence.py`
# counts those apart for the same reason — and the campaign team reads Seller
# Name as a person.
NOT_A_PERSON = frozenset({"shared login", "shared password", "panel"})

# The anchor the object is discovered from: whichever object carries this
# field is the Ad Copy Request object.
ANCHOR = "seller"

# How many objects the discovery scan will read fields for before giving up
# and naming the environment variable. A scan is one request per object and
# runs once per process; a Knack with hundreds of objects should pin the id
# rather than pay for the walk.
SCAN_LIMIT = int(os.environ.get("KNACK_AD_COPY_SCAN_LIMIT", "60"))

_cache: dict = {}


def field_ids() -> dict:
    """The pinned ids with their environment overrides applied.

    Touches nothing — a caller that only wants the ids does not have to
    reach Knack for them, the reason `knack_api.field_ids()` exists.
    """
    return {key: (os.environ.get(f"KNACK_AD_COPY_{key.upper()}") or fid).strip()
            for key, fid in AD_COPY_FIELDS.items()}


# ---------------------------------------------------------------- the object
def _object_keys() -> tuple[str, list[str]]:
    """(the answer, the objects to look through) — the first wins if it is set.

    Knack returns each object's fields inline on some plans. Where it does,
    the anchor is found in the one `/objects` call and the walk below never
    happens at all; where it does not, the two request objects this Hub
    already knows are tried before the rest, because an Ad Copy Request
    living on the Campaign Change object is the likeliest single answer and
    finding it there costs one request instead of sixty.
    """
    known = [knack_api.CHANGE_OBJECT, knack_api.SUPPORT_OBJECT,
             knack_api.TICKETS_OBJECT]
    rest: list[str] = []
    anchor = field_ids()[ANCHOR]
    try:
        r = knack_api.requests.get(f"{knack_api.BASE}/objects",
                                   headers=knack_api._headers(), timeout=20)
        r.raise_for_status()
        for obj in (r.json() or {}).get("objects", []):
            key = str(obj.get("key") or "")
            if not key:
                continue
            for f in obj.get("fields") or []:
                if str(f.get("key") or "") == anchor:
                    return key, []
            rest.append(key)
    except Exception:                       # noqa: BLE001 — see resolve()
        pass
    seen, out = set(), []
    for key in known + rest:
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return "", out


def resolve() -> tuple[str, str]:
    """(object key, why there isn't one) — exactly one is set.

    Never raises. A form that cannot find its object has to say so; one
    that falls back to a plausible object writes ad copy requests somewhere
    nobody is reading them, which looks exactly like a form that worked.
    """
    pinned = (os.environ.get("KNACK_AD_COPY_OBJECT") or "").strip()
    if pinned:
        return pinned, ""
    if "object" in _cache:
        return _cache["object"]
    anchor = field_ids()[ANCHOR]
    if not knack_api.configured():
        out = ("", "Knack isn't configured — set KNACK_APP_ID and KNACK_API_KEY.")
        _cache["object"] = out
        return out
    inline, keys = _object_keys()
    if inline:
        _cache["object"] = (inline, "")
        return _cache["object"]
    checked = 0
    for key in keys:
        if checked >= SCAN_LIMIT:
            break
        try:
            fields = knack_api.object_fields(key)
        except Exception:                   # noqa: BLE001 — one bad object
            checked += 1                    #   must not stop the search
            continue
        checked += 1
        if any(str(f.get("key") or "") == anchor for f in fields):
            _cache["object"] = (key, "")
            return _cache["object"]
    why = (f"No object in this Knack app carries {anchor} "
           f"(checked {checked} of {len(keys)}). "
           "Set KNACK_AD_COPY_OBJECT to the Ad Copy Request object.")
    out = ("", why)
    _cache["object"] = out
    return out


def forget() -> None:
    """Drop the discovered object. For tests and for a Knack restructure."""
    _cache.pop("object", None)


# ----------------------------------------------------------------- the form
def _label(f: dict, key: str) -> str:
    return knack_api.field_label(f) or AD_COPY_LABELS.get(key, key)


def _schema_default(f: dict):
    """The default Knack itself publishes for a field, or nothing.

    Reading the object's own default is not the same as choosing one: an
    opening status invented here is a status the campaign team's queue is
    sorted by. Where Knack publishes none, none is written and Knack's own
    workflow decides.
    """
    fmt = f.get("format") or {}
    d = fmt.get("default")
    if d in (None, "", []):
        return ""
    if isinstance(d, list):
        d = d[0] if d else ""
    options = [str(o) for o in (fmt.get("options") or [])]
    if options and str(d) not in options:
        return ""
    return str(d)


def form_fields(obj: str = "") -> list[dict]:
    """What the form should draw, read from the live object.

    The ids are ours; the *controls* cannot be. A dropdown's choices live in
    Knack and a form that guesses one writes a value Knack refuses — which
    costs the whole request, not the one field.
    """
    obj = obj or resolve()[0]
    ids = field_ids()
    meta = knack_api.object_meta(obj) if obj else {}
    ordered = [(g, k) for g, keys in AD_COPY_GROUPS for k in keys if k in ids]
    placed = {k for _, k in ordered}
    ordered += [("Other", k) for k in ids if k not in placed]
    seen, out = set(), []
    for group, key in ordered:
        if key in seen:
            continue
        seen.add(key)
        fid = ids[key]
        f = meta.get(fid) or {}
        control, choices = (knack_api.control_for(f, obj) if f else ("text", []))
        out.append({
            "key": key,
            "field": fid,
            "group": group,
            "label": _label(f, key),
            "control": control,
            "choices": choices,
            "required": bool(f.get("required")),
            # What Knack itself publishes as this field's default. Reading it
            # is not the same as choosing one — see _schema_default().
            "default": _schema_default(f),
            "writable": key in AD_COPY_CREATE_FIELDS,
            # False means the pinned id is not on the object any more. The
            # form still draws the field and says so, rather than dropping it.
            "known": bool(f),
        })
    return out


def _uniq(values) -> list[str]:
    seen, out = set(), []
    for v in values:
        v = re.sub(r"\s+", " ", str(v or "")).strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _one(values: list[str]) -> str:
    """The single candidate, or nothing.

    Two campaigns is a dropdown. Filling in the first is a plausible answer
    on a form nobody re-reads, which is worse than a blank.
    """
    return values[0] if len(values) == 1 else ""


def client_options(client: str) -> dict:
    """What this client's own insertion orders can answer, and what they cannot.

    Read through `knack_data.search_client` — the same record Client 360
    draws — so the campaign, the order number, the media partner and the
    seller come from the client's real IOs rather than a box somebody types
    into. `error` is set when the client list could not be read at all:
    "this client has no orders" and "we could not look" are different
    answers, and only the first means the boxes are genuinely empty.
    """
    out = {"client": client, "orders": [], "campaigns": [], "order_numbers": [],
           "partners": [], "sellers": [], "urls": [], "error": "", "matched": False}
    name = str(client or "").strip()
    if not name:
        return out
    try:
        from . import knack_data
        groups = knack_data.search_client(name) or []
    except Exception as exc:                # noqa: BLE001 — see the docstring
        out["error"] = str(exc)
        return out

    exact = [g for g in groups
             if str(g.get("client", "")).strip().lower() == name.lower()]
    grp = (exact or (groups if len(groups) == 1 else []))
    if not grp:
        return out
    g = grp[0]
    out["client"] = g.get("client") or name
    out["matched"] = True

    def io_num(p):
        try:
            return int(str(p.get("io") or "0"))
        except ValueError:
            return 0

    for p in sorted(g.get("products") or [], key=io_num, reverse=True):
        out["orders"].append({
            "campaign": str(p.get("campaign") or "").strip(),
            "io": str(p.get("io") or "").strip(),
            "product": str(p.get("product") or "").strip(),
            "partner": str(p.get("partner") or "").strip(),
            "seller": str(p.get("sales") or "").strip(),
            "status": str(p.get("status") or "").strip(),
        })
    out["campaigns"] = _uniq(o["campaign"] for o in out["orders"])
    out["order_numbers"] = _uniq(o["io"] for o in out["orders"])
    out["partners"] = _uniq(o["partner"] for o in out["orders"])
    out["sellers"] = _uniq(o["seller"] for o in out["orders"])
    out["urls"] = _uniq(
        [w.get("liveUrl") for w in (g.get("websites") or [])] +
        [w.get("domain") for w in (g.get("websites") or [])])
    return out


def _today() -> str:
    """Today, spelled the way Knack shows a date.

    Knack renders MM/DD/YYYY and takes it back the same way; a date input's
    YYYY-MM-DD would not be the value that came out of the record, which is
    the note `hub/static/web-ticket.js` already carries about date controls.
    """
    return date.today().strftime("%m/%d/%Y")


def prefill(client: str, *, user_name: str = "", user_email: str = "",
            fields: list[dict] | None = None,
            options: dict | None = None) -> tuple[dict, list[str]]:
    """(what the form opens on, what could not be filled in and why).

    Every value here comes from somewhere nameable — the signed-in account,
    the client's own insertion orders, the clock, or the field's own Knack
    default. Nothing is guessed between two candidates, and the notes name
    each box left blank that a reader might expect to be filled.
    """
    obj = resolve()[0]
    fields = form_fields(obj) if fields is None else fields
    opts = client_options(client) if options is None else options
    by_key = {f["key"]: f for f in fields}
    values: dict = {}
    notes: list[str] = []

    def put(key, value):
        if value not in (None, "", []):
            values[key] = value

    # --- who is asking ----------------------------------------------------
    # The signed-in account first; the client's own rep only when the orders
    # name exactly one, because attributing a request to the wrong seller is
    # attributing the campaign.
    put("seller", user_name or _one(opts.get("sellers") or []))
    if not values.get("seller"):
        notes.append("Seller Name: nobody is named on this client's orders — pick one.")
    if user_email:
        put("confirm_email", user_email)
    else:
        notes.append("Confirmation Email Address: this session has no account "
                     "behind it, so there is no address to fill in.")

    # --- which campaign ---------------------------------------------------
    # A connection needs a record id and matches exactly or not at all: a
    # near match here files one company's ad copy against another.
    cf = by_key.get("client") or {}
    if cf.get("control") == "connection":
        hit = [c for c in (cf.get("choices") or [])
               if str(c.get("label", "")).strip().lower()
               == str(opts.get("client") or client).strip().lower()]
        if len(hit) == 1:
            values["client"] = hit[0]["id"]
        else:
            notes.append(f"Client Name: “{client}” matches no single record on "
                         "that connection — pick the client.")
    else:
        put("client", opts.get("client") or client)

    put("campaign", _one(opts.get("campaigns") or []))
    put("order_number", _one(opts.get("order_numbers") or []))
    put("media_partner", _one(opts.get("partners") or []))
    if opts.get("error"):
        notes.append("The client's orders could not be read (" +
                     opts["error"] + ") — the campaign, order number and "
                     "media partner are blank because we could not look, "
                     "not because there are none.")
    elif not opts.get("orders"):
        notes.append("This client has no insertion orders on file, so there is "
                     "no campaign or order number to offer.")
    else:
        for key, label, pool in (
                ("campaign", "Campaign Name", "campaigns"),
                ("order_number", "Current Order Number", "order_numbers"),
                ("media_partner", "Media Partner", "partners")):
            n = len(opts.get(pool) or [])
            if n > 1 and not values.get(key):
                notes.append(f"{label}: this client has {n} — pick one.")

    # --- dates ------------------------------------------------------------
    # Submitted is today, because sending the form is the act of submitting.
    # Due Date and "when should this change" are not: there is no source for
    # either here, and a date the Hub made up is a date somebody works to.
    put("submitted", _today())
    put("status", (by_key.get("status") or {}).get("default"))

    # A pinned id that is no longer on the object. The form marks it against
    # the field too, but a request sent with one of these silently absent is
    # a request the campaign team reads as answered, so it is counted here
    # as well as drawn.
    for key in AD_COPY_CREATE_FIELDS:
        if not (by_key.get(key) or {}).get("known"):
            notes.append(f"{AD_COPY_LABELS.get(key, key)}: this field is not on "
                         "the object any more — check it in Knack.")
    return values, notes


def _decorate(fields: list[dict], opts: dict) -> list[dict]:
    """Hang this client's own answers on the fields that can offer them.

    `hub/static/knack-form.js` draws a text box with `suggest` as a datalist —
    which suggests and never restricts, the right shape for a value the Hub
    happens to know on a field Knack publishes no choices for. Deciding it
    here rather than in the browser is the same rule the prefill follows:
    target areas and the creative classifier each carry a JavaScript mirror
    already, and each needs a test proving the halves still agree.
    """
    suggest = {
        "seller": (opts.get("sellers") or [],
                   "Named on this client's insertion orders."),
        "campaign": (opts.get("campaigns") or [],
                     "From this client's insertion orders — or type another, "
                     "for a campaign being set up now."),
        "order_number": (opts.get("order_numbers") or [],
                         "Fills itself in from the campaign above where the "
                         "insertion order says which."),
        "media_partner": (opts.get("partners") or [],
                          "From this client's insertion orders."),
        "url_changing": (opts.get("urls") or [],
                         "This client's websites, from their record."),
    }
    # Said on the field rather than left as a blank somebody reads as an
    # oversight: there is no source here for either, so neither is filled in.
    hints = {
        "due_date": "Blank on purpose — the Hub has no source for this date.",
        "when": "Blank on purpose — only you know when it has to change.",
        "files": "Attached on the record in Knack after this is created — "
                 "the Hub cannot upload them, so nothing is sent from here.",
    }
    for f in fields:
        items, note = suggest.get(f["key"], ([], ""))
        if items:
            f["suggest"] = items
            f["hint"] = note
        if f["key"] in hints:
            f["hint"] = hints[f["key"]]
    return fields


def form(client: str, *, user_name: str = "", user_email: str = "") -> dict:
    """Everything a caller needs to draw the form, in one read.

    One round trip on purpose: the shape of the object, what this client can
    answer, and what it cannot are three halves of one question, and a page
    that fetches them separately can draw a field before it knows whether
    there is anything to put in it.
    """
    if not knack_api.configured():
        return {"configured": False, "fields": [], "values": {},
                "options": {}, "notes": []}
    obj, why = resolve()
    if not obj:
        return {"configured": True, "error": why, "fields": [], "values": {},
                "options": {}, "notes": []}
    opts = client_options(client)
    fields = _decorate(form_fields(obj), opts)
    values, notes = prefill(client, user_name=user_name, user_email=user_email,
                            fields=fields, options=opts)
    # ...and then whatever this client's own record already answers, into the
    # boxes the orders left blank. The same reader the web ticket and the
    # campaign support request use: a rep should not type a website the Hub
    # has held since that client's last site scan. It never overwrites, so
    # everything decided above still wins.
    try:
        from hub.client_context import offer_into
        values, more = offer_into(fields, values, opts.get("client") or client,
                                  _one(opts.get("urls") or []))
        notes += more
    except Exception:                                     # noqa: BLE001
        pass
    return {"configured": True, "object": obj, "fields": fields,
            "values": values, "options": opts, "notes": notes,
            "client": opts.get("client") or client}


# ----------------------------------------------------------------- the write
def create(client: str, values: dict, *, author: str = "") -> dict:
    """Create one Ad Copy Request.

    Every value is checked against the live field before it is sent, through
    the same `knack_api.coerce_field` that writes tickets and website
    records — Knack refuses a whole record over one bad dropdown choice, so
    a value it would refuse is dropped here and named in the result instead.

    The record carries `written` and `rejected`: what reached Knack, and
    what did not and why. A field quietly dropped is how a form comes to
    look like it works while half of it goes nowhere.
    """
    obj, why = resolve()
    if not obj:
        raise RuntimeError(why)
    ids = field_ids()
    meta = knack_api.object_meta(obj)
    payload, rejected = {}, []
    for key, value in (values or {}).items():
        if key not in AD_COPY_CREATE_FIELDS:
            rejected.append(f"{AD_COPY_LABELS.get(key, key)}: not writable here")
            continue
        if value in (None, "", [], {}):
            continue
        out, reason = knack_api.coerce_field(
            ids[key], value, obj=obj, label=AD_COPY_LABELS.get(key, key),
            meta=meta)
        if reason:
            rejected.append(reason)
            continue
        if out is None:
            continue
        payload[ids[key]] = out

    # The client is the one field this cannot be created without: an ad copy
    # request filed against nobody joins to no campaign and reaches no queue.
    if not payload.get(ids["client"]):
        raise RuntimeError(
            f"Client Name is required and did not resolve on {obj}."
            + (f" Refused: {'; '.join(rejected)}" if rejected else ""))

    # Written but never asked for: whoever pressed the button. A request the
    # campaign team cannot put a name to is one they have to come asking
    # about — but only where the author names a person. `current_user()`
    # answers "Shared login" for a PANEL_PASSWORD session, which is a true
    # statement about the session and a wrong one in the Seller Name box, and
    # the prefill already refuses it: a rule the form keeps and the write
    # breaks is not a rule.
    if author and author.strip().lower() not in NOT_A_PERSON \
            and not payload.get(ids["seller"]):
        if meta.get(ids["seller"], {}).get("type") not in ("connection",):
            payload[ids["seller"]] = author

    r = knack_api.requests.post(
        f"{knack_api.BASE}/objects/{obj}/records",
        headers=knack_api._headers(), json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError("Knack rejected the ad copy request "
                           f"(HTTP {r.status_code}): {r.text[:200]}")
    rec = r.json()
    if not isinstance(rec, dict):
        return rec
    return {**rec, "written": sorted(payload), "rejected": rejected}
