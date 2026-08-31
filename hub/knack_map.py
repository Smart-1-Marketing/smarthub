"""Every Knack object and field this Hub knows about, and who owns each one.

## Why this exists

Knack is the system of record for clients, campaigns, websites and requests,
and this Hub reaches into it from nine modules. What it does **not** have
anywhere is one description of what it thinks each object and field is — so
"which fields have we confirmed, and which are still somebody's assumption?"
was a question you could only answer by reading nine files and holding the
answer in your head.

That question is about to matter much more than it did. Everything written to
Knack today is a request or a small update; pushing campaigns or products in
would be a much larger surface, and **a field id pinned wrongly writes into
the wrong column of a live record**. Knack also refuses the *whole* record over
one bad value, so an unconfirmed mapping does not cost the field — it costs the
write.

## What this is, and what it deliberately is not

It is **not a second copy of the field ids**. Nine modules pin them and each is
the owner of its own; a copy here is the drift `hub/config.py`'s ALIASES table
and `hub/rate_card.py`'s two cards have each paid for. `fields()` **imports
from the owning module** — `knack_api.field_ids()`, `knack_api.SUPPORT_FIELDS`,
`knack_websites.FIELDS`, `ad_copy.field_ids()`, the `knack_products`
constants — so a field renamed or repinned there shows up here without anybody
editing this file, and a field that stops existing cannot linger in a table
nobody re-read.

What lives here is the part no module holds: which **object** each map belongs
to, which **tool** creates the records, whether the ids are **pinned or matched
by label**, and whether a person has **confirmed** the mapping against the live
Knack builder.

## The rules

**A field is confirmed once, against the object, and every tool that uses it
inherits that.** Object 135's monthly cost is read by Client 360, the
dashboard scorecards, the billing reports and the IO reconciliation; checking
it four times would be four chances to disagree. So a confirmation is keyed on
the object and the field rather than on the tool, and the tools are listed
beside the object as the answer to "who does this belong to".

**A confirmation is a person, a date and a field.** Not a module and not an
object: "we checked object_153" is the kind of assurance nobody can act on
later, and the whole point is to be able to say which of the eighteen were
actually looked at. Stored through `jsonstore`, keyed on `object:key`, and it
records **which id was confirmed** — so repinning a field retires its
confirmation rather than carrying it silently onto a different column.

**A field matched by label is a finding, not a mapping.** `object_140`
(Campaign Change Requests) is still matched by label, and `hub/knack_api.py`'s
own comment says why that is dangerous: a renamed label breaks label matching
**silently**, which is exactly the state `object_107` was in before its ids
were pinned. It is reported as unpinned rather than listed as though it were
confirmed.

**Verification needs Knack, and without it this is not measured.** `verify()`
reads the live schema and says, per field, what Knack calls that id and what
type it is. With no credentials it answers `measured: False` and the page shows
the map for review on paper rather than drawing a column of ticks nobody
earned — the rule `services/provider_check.py` works to.

**Nothing here writes to Knack.** It reads the schema and it writes one small
Hub-side overlay of confirmations. It does not create, update or delete a
record, and `test_knack_map.py` asserts that from the AST rather than the text.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from hub import jsonstore

# How a field's id was arrived at. The distinction is the point: a pinned id
# survives a rename in Knack, and a label-matched one breaks silently.
PINNED = "pinned"
BY_LABEL = "by label"

# The objects this Hub reaches, what each is for, and which tool creates the
# records in it. `fields` names the module attribute the mapping is read from,
# so this file never restates an id.
OBJECTS = {
    "object_107": {
        "name": "Web Tickets",
        "purpose": "Website change requests raised from Client 360 and the "
                   "dashboard, worked by the web team.",
        "tool": "Web Tickets · Client 360 · /tools/tickets",
        "env": "KNACK_TICKETS_OBJECT",
        "how": PINNED,
        "fields": ("hub.knack_api", "field_ids"),
        "writes": ("knack_api.create_ticket", "knack_api.update_ticket"),
    },
    "object_121": {
        "name": "Campaign Support Requests",
        "purpose": "Support asked for on a live campaign — pixels, timelines, "
                   "rush work — raised from Client 360 and the dashboard.",
        "tool": "Campaign Support · Client 360",
        "env": "KNACK_SUPPORT_OBJECT",
        "how": PINNED,
        "fields": ("hub.knack_api", "SUPPORT_FIELDS"),
        "writes": ("knack_api.create_campaign_request",),
    },
    "object_140": {
        "name": "Campaign Change Requests",
        "purpose": "A change to a campaign already running.",
        "tool": "Campaign Change · Client 360",
        "env": "KNACK_CHANGE_OBJECT",
        # The one object still matched by label, and it is a write target. See
        # the module note above: a renamed label breaks this silently, which is
        # the state object_107 was in before its ids were pinned.
        "how": BY_LABEL,
        "fields": None,
        "writes": ("knack_api.create_campaign_request",),
    },
    "object_135": {
        "name": "Products / Insertion Orders",
        "purpose": "What is running for whom: one row per product on an "
                   "insertion order, with its flight, status and monthly cost. "
                   "The source behind Client 360, the dashboard scorecards and "
                   "every billing report.",
        "tool": "read by most of the Hub · written only by the dashboard-URL "
                "button on QA → No Dashboards",
        "env": "KNACK_PRODUCTS_OBJECT",
        "how": PINNED,
        "fields": ("hub.knack_map", "_product_fields"),
        "writes": ("knack_api.set_dashboard_url",),
    },
    "object_153": {
        "name": "Website Registry",
        "purpose": "One row per website: the domain, who it belongs to, the "
                   "platform, the analytics ids, and whether Smart 1 bought "
                   "the domain and when it renews.",
        "tool": "Sites Admin · Client 360's domain record · Domain Renewals",
        "env": None,
        "how": PINNED,
        "fields": ("hub.knack_websites", "FIELDS"),
        "writes": ("knack_websites.update_record",
                   "knack_websites.set_analytics_ids"),
    },
    "ad_copy": {
        "name": "Ad Copy Requests",
        "purpose": "A change to the copy running on a campaign.",
        "tool": "Ad Copy · Client 360",
        "env": "KNACK_AD_COPY_OBJECT",
        # Nobody has told us this object's number, so it is discovered from
        # whichever object carries its pinned ids rather than guessed at:
        # inventing one writes ad copy requests into whatever answers.
        "how": PINNED,
        "discovered": True,
        "fields": ("hub.ad_copy", "field_ids"),
        "writes": ("ad_copy.create",),
    },
    "object_109": {
        "name": "People",
        "purpose": "Who a request can be attributed to — the Requested By "
                   "picker. Read for its records, never for its fields.",
        "tool": "Web Tickets · Campaign Support",
        "env": "KNACK_PEOPLE_OBJECTS",
        "how": PINNED,
        "fields": None,
        "writes": (),
        "records_only": True,
    },
}

# object_135's ids are module constants rather than a dict, so the one place
# that turns them into a mapping is here — still reading the constants
# themselves, never restating a number.
_PRODUCT_KEYS = (
    ("product_num", "F_PRODUCT_NUM"), ("product", "F_PRODUCT_NAME"),
    ("product_type", "F_PRODUCT_TYPE"), ("tactics", "F_TACTICS"),
    ("io", "F_IO"), ("io_number", "F_IO_NUM"),
    ("start", "F_START"), ("end", "F_END"),
    ("status", "F_STATUS"), ("io_status", "F_IO_STATUS"),
    ("client", "F_CLIENT"), ("client_org", "F_CLIENT_ORG"),
    ("media_partner", "F_MEDIA_PARTNER"),
    ("io_campaign", "F_IO_CAMPAIGN"), ("product_campaign", "F_PROD_CAMPAIGN"),
    ("display_campaign", "F_DISPLAY_CAMPAIGN"),
    ("monthly_cost", "F_MONTHLY_COST"), ("total_cost", "F_TOTAL_COST"),
    ("mo_billing", "F_MO_BILLING"),
    ("dashboard_url", "F_DASHBOARD_URL"), ("dashboard_value", "F_DASH_VALUE"),
    ("trafficker", "F_TRAFFICKER"), ("sales", "F_SALES"),
    ("record_id", "F_RECORD_ID"),
    ("click_thru", "F_CLICK_THRU"), ("display_click", "F_DISPLAY_CLICK"),
    ("geo", "F_GEO"),
    # The three the campaign-asset queue is gated on. They live on the same
    # object and are pinned in `knack_products` beside the rest, each
    # overridable by its own environment variable.
    ("clarification", "F_CLARIFICATION"), ("assets_flag", "F_ASSETS_FLAG"),
    ("assets_needed", "F_ASSETS_NEEDED"),
)


def _product_fields() -> dict:
    """object_135's mapping, read off `knack_products`' own constants."""
    out = {}
    try:
        from hub import knack_products
    except Exception:                                   # noqa: BLE001
        return out
    for key, const in _PRODUCT_KEYS:
        value = getattr(knack_products, const, "")
        if value:
            out[key] = value
    return out


def _read_map(spec) -> dict:
    """The mapping an object's owning module holds. Never raises."""
    if not spec:
        return {}
    module_name, attr = spec
    try:
        import importlib
        mod = importlib.import_module(module_name)
        value = getattr(mod, attr, None)
        if callable(value):
            value = value()
        return dict(value or {})
    except Exception:                                   # noqa: BLE001
        return {}


# ------------------------------------------------------------- confirmations
def _path() -> str:
    return os.path.join(jsonstore.data_dir("hub"), "knack_map_confirmed.json")


def confirmations() -> dict:
    rows = jsonstore.read_json(_path(), default={})
    return rows if isinstance(rows, dict) else {}


def confirm(obj: str, key: str, field_id: str, actor: str = "",
            note: str = "") -> dict:
    """Record that a person checked this field against the live Knack builder.

    The **field id is stored with the confirmation**, so repinning a field
    retires it rather than carrying a tick from one column silently onto
    another — which is the single way this record could become worse than
    having none.
    """
    obj, key = str(obj or "").strip(), str(key or "").strip()
    field_id = str(field_id or "").strip()
    if not obj or not key or not field_id:
        return {"ok": False, "error": "An object, a field and its id are all "
                                      "required to record a confirmation."}
    rows = confirmations()
    rows[f"{obj}:{key}"] = {
        "object": obj, "key": key, "field": field_id,
        "by": str(actor or "")[:60], "note": str(note or "")[:300],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    jsonstore.write_json(_path(), rows)
    return {"ok": True, "row": rows[f"{obj}:{key}"]}


def unconfirm(obj: str, key: str) -> bool:
    rows = confirmations()
    if f"{obj}:{key}" not in rows:
        return False
    rows.pop(f"{obj}:{key}", None)
    jsonstore.write_json(_path(), rows)
    return True


# -------------------------------------------------------------------- the map
def fields() -> list[dict]:
    """Every mapping this Hub holds, one row per field.

    Assembled from the owning modules rather than restated, so this cannot
    describe a field that no longer exists or miss one that was just added.
    """
    marks = confirmations()
    out = []
    for obj, meta in OBJECTS.items():
        mapping = _read_map(meta.get("fields"))
        for key, field_id in sorted(mapping.items()):
            mark = marks.get(f"{obj}:{key}")
            # A confirmation belongs to the id it was given for. Repinned, the
            # tick is retired and says so rather than reading as current.
            stale = bool(mark and mark.get("field") != field_id)
            out.append({
                "object": obj,
                "object_name": meta["name"],
                "tool": meta["tool"],
                "key": key,
                "field": field_id,
                "how": meta["how"],
                "written": key in _write_keys(obj),
                "confirmed": bool(mark) and not stale,
                "confirmed_by": (mark or {}).get("by", "") if not stale else "",
                "confirmed_at": (mark or {}).get("at", "") if not stale else "",
                "superseded": stale,
                "was_confirmed_as": (mark or {}).get("field", "") if stale else "",
            })
    return out


def _write_keys(obj: str) -> set:
    """Which of an object's fields the Hub actually writes.

    Read from the modules' own write sets: a field the Hub only reads is a
    mapping worth confirming eventually, and one it writes is a mapping that
    has to be right before the next push.
    """
    try:
        from hub import knack_api
        if obj == "object_107":
            return set(knack_api.TICKET_CREATE_FIELDS) | set(
                knack_api.TICKET_MANAGE_FIELDS)
        if obj == "object_121":
            return set(knack_api.SUPPORT_CREATE_FIELDS)
        if obj == "object_140":
            return set(knack_api.CHANGE_CREATE_FIELDS)
    except Exception:                                   # noqa: BLE001
        pass
    if obj == "object_135":
        # One field, from one button: the dashboard URL on QA → No Dashboards.
        return {"dashboard_url", "dashboard_value"}
    if obj == "object_153":
        # update_record writes whatever it is given, so every mapping on this
        # object is a write target.
        return set(_read_map(OBJECTS["object_153"]["fields"]))
    if obj == "ad_copy":
        return set(_read_map(OBJECTS["ad_copy"]["fields"]))
    return set()


def summary() -> dict:
    """The counts a page leads with, and what is still somebody's assumption."""
    rows = fields()
    unpinned = [o for o, m in OBJECTS.items() if m["how"] == BY_LABEL]
    return {
        "objects": len(OBJECTS),
        "fields": len(rows),
        "confirmed": sum(1 for r in rows if r["confirmed"]),
        "written": sum(1 for r in rows if r["written"]),
        "written_unconfirmed": sum(1 for r in rows
                                   if r["written"] and not r["confirmed"]),
        "superseded": sum(1 for r in rows if r["superseded"]),
        "unpinned_objects": unpinned,
        "write_paths": sorted({w for m in OBJECTS.values()
                               for w in (m.get("writes") or ())}),
    }


def verify(obj: str = "") -> dict:
    """What Knack itself calls each pinned id. Not measured without Knack.

    A schema read and nothing else: this never creates, updates or deletes a
    record. A field the live object does not publish is the finding — that is
    an id pinned to a column that is not there any more, and Knack refuses the
    *whole* record over one bad value, so it costs the write rather than the
    field.
    """
    try:
        from hub import knack_api
        if not knack_api.configured():
            return {"measured": False, "rows": [],
                    "error": "Knack is not configured on this deployment, so "
                             "the pinned ids could not be checked against the "
                             "live builder."}
    except Exception as exc:                            # noqa: BLE001
        return {"measured": False, "rows": [],
                "error": f"Knack could not be reached ({type(exc).__name__})."}

    rows, errors = [], []
    for obj_key, meta in OBJECTS.items():
        if obj and obj_key != obj:
            continue
        if meta.get("records_only") or not meta.get("fields"):
            continue
        target = _live_object(obj_key, meta)
        if not target:
            errors.append(f"{meta['name']}: this deployment does not name the "
                          "object, so its fields could not be read")
            continue
        try:
            live = {f.get("key"): f for f in knack_api.object_fields(target)}
        except Exception as exc:                        # noqa: BLE001
            errors.append(f"{meta['name']}: {type(exc).__name__}")
            continue
        for key, field_id in sorted(_read_map(meta["fields"]).items()):
            found = live.get(field_id)
            rows.append({
                "object": obj_key, "object_name": meta["name"],
                "key": key, "field": field_id,
                "found": bool(found),
                "knack_label": (found or {}).get("label", ""),
                "knack_type": (found or {}).get("type", ""),
            })
    return {"measured": not errors or bool(rows), "rows": rows,
            "errors": errors, "error": "; ".join(errors) if not rows else ""}


def _live_object(obj_key: str, meta: dict) -> str:
    """The object number this deployment actually uses for this map."""
    if meta.get("discovered"):
        try:
            from hub import ad_copy
            # (object key, why there isn't one) — exactly one is set.
            found, _why = ad_copy.resolve()
            return found or ""
        except Exception:                               # noqa: BLE001
            return ""
    env = meta.get("env")
    if env:
        value = (os.environ.get(env) or "").split(",")[0].strip()
        if value:
            return value
    return obj_key if obj_key.startswith("object_") else ""
