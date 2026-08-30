"""What a campaign is still waiting on — object_135, by media partner.

Two fields on every product record say a campaign cannot be built yet.
`field_2742` (Clarification needed) is what somebody has to go back and ask.
`field_2347` is the extra assets outstanding, and it counts only when
`field_2346` — the tickbox beside it — is ticked. Both have been filled in for
years and nothing has ever read either of them, so the only way to find the
campaigns blocked on an asset was to open the insertion order and look.

This is that list. It is per **campaign** rather than per product, because the
chase is one conversation with one media partner about one campaign, however
many product lines sit under it — but each blocked product is listed inside
its campaign, since a display line waiting on banners and a video line waiting
on a spot are two different asks to two different people.

## The rules, each of which is a way to be quietly wrong

**The tickbox is what says the asset is needed.** `field_2347` is read only
where `field_2346` is ticked, exactly as asked. But text sitting in 2347 with
the box unticked is not thrown away in silence: those rows are counted and
named under the table, because "nobody needs anything" and "somebody typed
what they need and never ticked the box" are different situations and only one
of them is finished. Naming them is not the same as listing them as work — the
box is still the answer.

**Absent is not zero.** The product cache is a flattened copy of object_135,
so a cache written before these three fields were added carries none of them —
and a missing key reads as "no clarification needed" on every campaign in the
Hub. `report()` asks whether the rows can answer the question at all before it
reports that the answer is none, and says so instead of drawing an empty,
confident table. `knack_products.FIELDS_VERSION` makes that state transient;
this makes it visible while it lasts.

**A blank media partner sorts last, not first.** The list is sorted by media
partner then internal sales, as asked. An empty partner is not an early letter
of the alphabet: it goes in its own group at the end, labelled as unrecorded,
so a campaign nobody has filed does not head the queue by accident.

**Waiting on an asset is a question about a campaign that has not run yet.**
`knack_data.is_running` answers "is this delivering today", which is wrong here
by exactly the interval that matters: the campaign starting in three weeks is
the one somebody has to chase artwork for. So a future start counts and only a
finished status or an end date already past takes a row off the list. What is
skipped is counted, and the whole list is one toggle away — "we checked every
campaign" and "we checked the 380 still open" are different claims.

**A pinned id is not a checked id.** These three were pinned from the field
numbers alone. `field_check()` reads object_135's live schema and reports what
Knack calls each one and what type it is, so a renumbered field shows up as a
surprising label rather than as a list that quietly goes empty — and a
`field_2346` that is not a boolean is named, because the entire list is gated
on that tick.
"""
from __future__ import annotations

import datetime as _dt

from hub import knack_products

# What we call each field where Knack's own schema cannot be read. Only the
# first is Knack's wording; the other two are ours, and field_check() says
# which is which rather than presenting a house name as the record's own.
HOUSE_LABELS = {
    knack_products.F_CLARIFICATION: "Clarification needed",
    knack_products.F_ASSETS_FLAG: "Additional assets needed?",
    knack_products.F_ASSETS_NEEDED: "Additional assets",
}

NOT_RECORDED = "— not recorded —"


# ---------------------------------------------------------------------------
# The two fields
# ---------------------------------------------------------------------------

def asset_ask(row: dict) -> str:
    """The outstanding assets on one product, or "" if none are asked for.

    The single place the tickbox gate is applied. `field_2347` means nothing
    on its own — it is the note beside a box, and the box is the answer.
    """
    if not row.get("assets_flag"):
        return ""
    return str(row.get("assets_needed") or "").strip()


def unticked_text(row: dict) -> str:
    """Text in field_2347 with field_2346 unticked. Counted, never listed."""
    if row.get("assets_flag"):
        return ""
    return str(row.get("assets_needed") or "").strip()


def clarification(row: dict) -> str:
    return str(row.get("clarification") or "").strip()


def measurable(row: dict) -> bool:
    """Does this row carry the fields at all?

    A row from the committed export, or from a cache written before these
    fields were read, has no key for them — which is not the same as having
    them empty, and must never be reported as "nothing needed".
    """
    return "assets_flag" in row or "clarification" in row


# ---------------------------------------------------------------------------
# Which campaigns are still ahead of us
# ---------------------------------------------------------------------------

def is_open(row: dict, today: _dt.date | None = None) -> bool:
    """Is this product still to run, or running now?"""
    from hub import dates as _dates
    from hub.knack_data import _FINISHED_STATUSES, is_running

    status = str(row.get("status", "")).strip().lower()
    if status in _FINISHED_STATUSES:
        return False
    if is_running(row):
        return True
    today = today or _dt.date.today()
    end = _dates.to_date(row.get("end"))
    if end and end < today:
        return False
    # Either it starts later, or it carries no usable dates at all. Both stay
    # on the list: widening this queue only adds work to it, and the rows it
    # would hide are the ones with an unanswered question on them.
    return True


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _campaign_key(row: dict) -> tuple:
    """One campaign: a client, a campaign name and the IO it sits on.

    The IO is part of the key deliberately. "Annual" is the campaign name on
    hundreds of insertion orders and on the same client's IO two years
    running; merging those into one row would report last year's outstanding
    artwork against this year's flight.
    """
    return (str(row.get("organization") or row.get("client") or "").strip(),
            str(row.get("campaign") or "").strip(),
            str(row.get("io") or "").strip())


def _sort_key(c: dict) -> tuple:
    """Media partner, then internal sales — blanks last, in their own group."""
    return (1 if not c["partner"] else 0, c["partner"].lower(),
            1 if not c["sales"] else 0, c["sales"].lower(),
            c["client"].lower(), c["campaign"].lower())


def report(q: str = "", scope: str = "open", today: _dt.date | None = None) -> dict:
    """Campaigns with something outstanding, by media partner then sales."""
    today = today or _dt.date.today()
    data = knack_products.rows()
    rows = data.get("rows") or []

    # Can these rows answer the question at all? Asked before anything is
    # counted, so an old cache reports itself rather than reporting zero.
    readable = [r for r in rows if measurable(r)]
    # Nothing read is not the same as nothing outstanding, whether the rows
    # are missing entirely or merely predate the two fields.
    measured = bool(readable)

    scope = "all" if str(scope).lower() == "all" else "open"
    campaigns: dict[tuple, dict] = {}
    closed_skipped = 0
    unticked = 0
    unticked_rows: list[dict] = []

    for r in readable:
        open_now = is_open(r, today)
        if scope == "open" and not open_now:
            # Counted before it is dropped: "no outstanding assets" and "we
            # only looked at the live ones" are different answers.
            if clarification(r) or asset_ask(r):
                closed_skipped += 1
            continue

        stray = unticked_text(r)
        if stray:
            unticked += 1
            unticked_rows.append({
                "client": r.get("organization") or r.get("client") or "",
                "campaign": r.get("campaign") or "",
                "io": r.get("io") or "",
                "product": r.get("product") or "",
                "partner": str(r.get("partner") or "").strip(),
                # The rep, same as on the main table: this list is a chase
                # list too, and one with no name against a row is a row
                # nobody picks up.
                "sales": str(r.get("sales") or "").strip(),
            })

        clar, assets = clarification(r), asset_ask(r)
        if not clar and not assets:
            continue

        key = _campaign_key(r)
        c = campaigns.get(key)
        if not c:
            c = campaigns[key] = {
                "client": key[0], "campaign": key[1], "io": key[2],
                "partner": str(r.get("partner") or "").strip(),
                "sales": str(r.get("sales") or "").strip(),
                "partners": set(), "sales_people": set(),
                "products": [], "open": False,
                "start": "", "end": "",
            }
        if r.get("partner"):
            c["partners"].add(str(r["partner"]).strip())
        if r.get("sales"):
            c["sales_people"].add(str(r["sales"]).strip())
        c["open"] = c["open"] or open_now
        # The campaign's flight is the earliest start and the latest end of
        # the lines under it. Dates arrive ISO-first from _date(), so a string
        # compare orders them; a line with no date moves neither end.
        start, end = str(r.get("start") or ""), str(r.get("end") or "")
        if start and (not c["start"] or start < c["start"]):
            c["start"] = start
        if end and (not c["end"] or end > c["end"]):
            c["end"] = end
        c["products"].append({
            "product": r.get("product") or r.get("kind") or "",
            "product_num": r.get("product_num") or "",
            "status": r.get("status") or "",
            "start": r.get("start") or "", "end": r.get("end") or "",
            "clarification": clar,
            "assets": assets,
            "assets_flag": bool(r.get("assets_flag")),
        })

    out = []
    for c in campaigns.values():
        partners = sorted(c.pop("partners"))
        sales = sorted(c.pop("sales_people"))
        # A campaign whose product lines disagree about the partner or the
        # rep is filed under the first and carries the rest, rather than
        # being silently split across two groups or filed under one with the
        # other lost.
        c["partner"] = partners[0] if partners else ""
        c["sales"] = sales[0] if sales else ""
        c["other_partners"] = partners[1:]
        c["other_sales"] = sales[1:]
        c["clarifications"] = sum(1 for p in c["products"] if p["clarification"])
        c["asset_asks"] = sum(1 for p in c["products"] if p["assets"])
        c["products"].sort(key=lambda p: (p["start"], p["product"].lower()))
        out.append(c)

    unticked_rows.sort(key=lambda x: (
        1 if not x["partner"] else 0, x["partner"].lower(),
        1 if not x["sales"] else 0, x["sales"].lower(),
        x["client"].lower(), x["campaign"].lower()))

    needle = str(q or "").strip().lower()
    if needle:
        out = [c for c in out if needle in " ".join([
            c["client"], c["campaign"], c["io"], c["partner"], c["sales"],
            " ".join(p["clarification"] + " " + p["assets"] for p in c["products"]),
        ]).lower()]

    out.sort(key=_sort_key)

    groups = []
    for c in out:
        name = c["partner"] or NOT_RECORDED
        if not groups or groups[-1]["partner"] != name:
            groups.append({"partner": name,
                           "recorded": bool(c["partner"]),
                           "campaigns": []})
        groups[-1]["campaigns"].append(c)
    for g in groups:
        g["count"] = len(g["campaigns"])
        g["clarifications"] = sum(x["clarifications"] for x in g["campaigns"])
        g["asset_asks"] = sum(x["asset_asks"] for x in g["campaigns"])
        g["sales"] = sorted({x["sales"] or NOT_RECORDED for x in g["campaigns"]})

    _field_check = field_check()
    return {
        "campaigns": out,
        "groups": groups,
        "count": len(out),
        "clarifications": sum(c["clarifications"] for c in out),
        "asset_asks": sum(c["asset_asks"] for c in out),
        "partners": len({c["partner"] for c in out if c["partner"]}),
        "scope": scope,
        "closed_skipped": closed_skipped,
        "unticked": unticked,
        "unticked_rows": unticked_rows[:50],
        "measured": measured,
        "products_read": len(readable),
        "products_total": len(rows),
        "source": data.get("source", ""),
        "age_minutes": data.get("age_minutes"),
        "q": q,
        "today": today.isoformat(),
        "note": _note(data, measured, rows),
        "fields": labels(_field_check),
        # The whole point of pinning three field ids is that a renumbered one
        # reads back empty on every record, which looks exactly like a client
        # base with nothing outstanding. `field_check()` computes exactly that
        # warning and `labels()` copied out only the label and its source, so
        # the page's own `(d.warnings||[])` loop read a key `report()` never
        # wrote and was always empty. Rename `field_2346` in Knack and the
        # report says "No campaign in scope is waiting on a clarification or
        # an asset" about the whole book -- `measurable()` still passes,
        # because `clarification` is present -- with nothing anywhere saying
        # the tick it is gated on has stopped answering.
        "warnings": _field_check.get("warnings") or [],
    }


def _note(data: dict, measured: bool, rows: list) -> str:
    """The one thing prose has to carry: that nothing could be read."""
    if not measured:
        source = data.get("source") or "an unknown source"
        if not rows:
            return ("No products could be read at all (" + source + "), so "
                    "this is empty rather than clear. " +
                    str(data.get("note") or ""))
        return (
            f"The {len(rows)} product rows the Hub is holding carry neither "
            "field, so this is not a list of campaigns needing nothing — it "
            "is a list nothing could be read from. The Hub is showing " +
            source + ", which predates the two fields this report reads. It "
            "clears itself on the next live pull from Knack, and the button "
            "above does that now.")
    # Nothing when the report can answer the question. The page's own
    # heading already says what the list is, the scope toggle says what is in
    # it, and each panel carries its own count — a paragraph restating all
    # three above the table is read once and skipped for ever afterwards.
    # What survives is the case above: a report that could not measure has to
    # say so, because there is nothing on the screen to say it for it.
    return ""


# ---------------------------------------------------------------------------
# What Knack calls these fields
# ---------------------------------------------------------------------------

def labels(check: dict | None = None) -> dict:
    """Field id -> the label to print, and where that label came from.

    Takes the `field_check()` answer when the caller already has one. It read
    the live schema itself before, so `report()` asking for the labels and for
    the warnings off the same run would otherwise have made two Knack calls --
    and two answers that can disagree about the field they describe.
    """
    check = field_check() if check is None else check
    out = {}
    for f in check["fields"]:
        out[f["id"]] = {"label": f["label"], "source": f["label_source"]}
    return out


def field_check() -> dict:
    """The three pinned ids against object_135's live schema.

    Pinning an id stops a renamed label from silently breaking a read. It does
    nothing about a *renumbered* field, which reads back empty on every record
    and looks exactly like a client base with nothing outstanding. So the
    schema is asked what each id actually is, and the answer is printed on the
    page rather than kept for a diagnostic nobody opens.
    """
    wanted = [
        (knack_products.F_CLARIFICATION, "clarification", ("paragraph_text",
                                                           "short_text", "rich_text")),
        (knack_products.F_ASSETS_FLAG, "assets_flag", ("boolean",)),
        (knack_products.F_ASSETS_NEEDED, "assets_needed", ("paragraph_text",
                                                           "short_text", "rich_text")),
    ]
    out = {"object": knack_products.OBJECT, "fields": [], "error": "",
           "configured": knack_products.configured()}

    live: dict[str, dict] = {}
    if out["configured"]:
        try:
            from hub import knack_api
            for f in knack_api.object_fields(knack_products.OBJECT):
                live[str(f.get("key") or "")] = f
        except Exception as exc:                        # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
    else:
        out["error"] = ("KNACK_APP_ID / KNACK_API_KEY are not set, so the "
                        "field names below are ours and unverified.")

    for fid, role, types in wanted:
        f = live.get(fid) or {}
        label = ""
        if f:
            from hub import knack_api
            label = knack_api.field_label(f)
        ftype = str(f.get("type") or "")
        warn = ""
        if live and not f:
            warn = ("object_135 has no " + fid + ". Anything this report says "
                    "about it is blank because the field is missing, not "
                    "because the campaigns are clear.")
        elif f and types and ftype and ftype not in types:
            warn = (fid + " is a " + ftype + " on the live object, not a "
                    + "/".join(types) + ". The id may have moved.")
        out["fields"].append({
            "id": fid, "role": role,
            "label": label or HOUSE_LABELS.get(fid, fid),
            "label_source": "knack" if label else "house",
            "type": ftype, "found": bool(f), "warning": warn,
        })
    out["warnings"] = [f["warning"] for f in out["fields"] if f["warning"]]
    return out
