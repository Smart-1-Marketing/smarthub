"""What the Hub's own screens say about a client's social content.

Two readers, one description. The Client 360 card asks about one client; the
dashboard scoreboard asks across the book. Both are answered here rather than
each assembling its own, for the reason this codebase gives at length about
`/api/db/structure` and `/api/integrity`: two checks asking one question will
answer it differently, and both answers end up on screen.

## Why this is the Hub's half rather than the module's

`modules/social_planner` owns requests, ideas and plans. Client 360 and the
dashboard are hub routes and cannot be reached from inside a mounted module,
so the join lives here — the arrangement `hub/ad_builder_link.py` already has
for the TypeScript renderer next door. Nothing is duplicated: every number
below is read from the module's own functions.

## Three rules

**Nothing here may raise.** It is called while rendering a client record and a
dashboard that half the company opens. Every failure resolves to
`measured: False` with the reason named, because *this client has no requests*
and *we could not read the queue* are different answers and only the first one
means there is nothing to do.

**The scoreboard reads requests and nothing else.** Counting posts awaiting a
client's approval means opening every plan file for every client on the book,
on a page that loads on every visit. The per-client card can afford that; a
dashboard cannot, and a number that costs a page load is a number somebody
turns off.

**A count is never a link to a page that cannot show it.** Every row carries
the address that opens exactly what the number counted, so pressing a figure
lands on the rows behind it rather than on a tool the reader then has to
filter themselves.
"""
from __future__ import annotations

MOUNT = "/tools/social"


def _unavailable(exc: Exception) -> dict:
    return {"measured": False,
            "error": f"The social content queue could not be read "
                     f"({type(exc).__name__})."}


def _module():
    from modules.social_planner import ideas, intake, links
    return intake, ideas, links


# =====================================================================
# One client — the Client 360 card
# =====================================================================
def for_client(name: str, url: str = "", base: str = "") -> dict:
    """Requests, ideas, the client's own link, and what is waiting on them.

    Everything on the record a rep actually opens. Before this, a client could
    have three requests overdue, a link nobody had sent them and four posts
    sitting unanswered, and their record said none of it — which is the
    "a link that exists and nobody can reach" failure this repo counts six of.
    """
    name = str(name or "").strip()
    if not name:
        return {"measured": False, "error": "No client was named."}
    try:
        intake, ideas, links = _module()
    except Exception as exc:                              # noqa: BLE001
        return _unavailable(exc)

    out: dict = {"measured": True, "client": name, "error": ""}

    try:
        summary = intake.summary(name, url)
        out["requests"] = {
            "open": summary["open"], "overdue": summary["overdue"],
            "duplicates": summary["possible_duplicates"],
            "total": summary["total"],
            "by_location": summary["by_location"][:6],
            "url": f"{MOUNT}/requests?client={_q(name)}",
        }
        out["recent"] = [
            {"id": r["id"], "location": r.get("location_label") or "Not said",
             "type": r.get("type_label", ""), "when": r.get("when", ""),
             "status": r.get("status_label", ""), "overdue": bool(r.get("overdue"))}
            for r in intake.open_requests(name, url)[:5]
        ]
    except Exception as exc:                              # noqa: BLE001
        out["requests"] = {"measured": False,
                           "error": f"Requests could not be read "
                                    f"({type(exc).__name__})."}
        out["recent"] = []

    try:
        table = ideas.weight_table(name, url)
        answered = [r for r in table if r["answered"]]
        out["ideas"] = {
            "pending": len(ideas.pending(name, url, limit=50)),
            "answered": sum(r["answered"] for r in table),
            "liked": [r["label"] for r in sorted(
                answered, key=lambda r: -r["weight"])[:3]],
            # "Nobody has swiped yet" is a state, not an absence of data, and
            # it is the one that tells a rep to send the link.
            "measured": True,
        }
    except Exception as exc:                              # noqa: BLE001
        out["ideas"] = {"measured": False,
                        "error": f"Ideas could not be read "
                                 f"({type(exc).__name__})."}

    try:
        out["link"] = {
            "revoked": links.is_revoked(name),
            # The base is passed in from the request, because a link printed
            # without an origin is one somebody copies and cannot send.
            # links._origin() trims it, so a mount on the end is harmless.
            "pages": links.all_links(name, url, base),
        }
    except Exception as exc:                              # noqa: BLE001
        out["link"] = {"measured": False,
                       "error": f"The client link could not be built "
                                f"({type(exc).__name__})."}

    out["posts"] = _posts_state(name)
    return out


def _posts_state(name: str) -> dict:
    """Plans on file for this client, and how many posts are with them.

    Deliberately per-client only. Opening every plan file for every client is
    what would make this too expensive for the dashboard, which is why the
    scoreboard does not ask.
    """
    try:
        from modules.social_planner import app as planner
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False,
                "error": f"Plans could not be read ({type(exc).__name__})."}
    want = str(name or "").strip().lower()
    waiting = approved = changes = 0
    latest = None
    try:
        for row in planner._read_index():                 # noqa: SLF001
            if str(row.get("client") or "").strip().lower() != want:
                continue
            if latest is None:
                latest = {"id": row.get("id", ""), "month": row.get("month", ""),
                          "status": row.get("status", ""),
                          "slots": row.get("slots", 0),
                          "drafted": row.get("drafted", 0)}
            batch = planner.load_batch(row.get("id", ""))
            for slot in (batch or {}).get("slots") or []:
                state = slot.get("client_state") or ""
                waiting += 1 if state == "pending_client_approval" else 0
                approved += 1 if state == "approved" else 0
                changes += 1 if state == "changes_requested" else 0
    except Exception as exc:                              # noqa: BLE001
        return {"measured": False,
                "error": f"Plans could not be read ({type(exc).__name__})."}
    return {"measured": True, "waiting_on_client": waiting,
            "approved": approved, "changes_requested": changes,
            "latest": latest,
            "url": f"{MOUNT}/" if latest is None else f"{MOUNT}/?plan={latest['id']}"}


# =====================================================================
# Across the book — the dashboard scoreboard
# =====================================================================
def scoreboard(limit: int = 8) -> dict:
    """Who is waiting on us, most at risk first.

    This is the half that was missing entirely: a location manager submitted
    at four on a Friday and it sat in a queue only somebody who opened the
    tool would ever see. The counts are on the dashboard now, and each one
    opens the rows behind it.

    Overdue leads because it is the only figure here that costs us something —
    a request whose day has gone. `waiting` is the workload and `clients` is
    how many conversations it is spread across, which are different questions
    and are not folded into one number.
    """
    try:
        intake, _ideas, _links = _module()
    except Exception as exc:                              # noqa: BLE001
        return _unavailable(exc)
    try:
        rows = intake.clients_with_open_requests()
    except Exception as exc:                              # noqa: BLE001
        return _unavailable(exc)

    waiting = sum(int(r.get("open") or 0) for r in rows)
    overdue = sum(int(r.get("overdue") or 0) for r in rows)
    return {
        "measured": True,
        "waiting": waiting,
        "overdue": overdue,
        "clients": len(rows),
        "queue_url": f"{MOUNT}/requests",
        "rows": [{"client": r.get("client", ""),
                  "open": int(r.get("open") or 0),
                  "overdue": int(r.get("overdue") or 0),
                  "url": f"{MOUNT}/requests?client={_q(r.get('client', ''))}"}
                 for r in rows[:max(1, int(limit))]],
        "more": max(0, len(rows) - max(1, int(limit))),
        # Said in words beside the figure rather than left to a colour: a
        # queue with nothing in it and a queue nobody has sent a link for read
        # identically as a zero, and only the second is somebody's to fix.
        "line": _scoreboard_line(waiting, overdue, len(rows)),
    }


def _scoreboard_line(waiting: int, overdue: int, clients: int) -> str:
    if not waiting:
        return ("Nothing waiting. A client with no requests may simply not "
                "have been sent their link yet — that is on their record.")
    who = "1 client" if clients == 1 else f"{clients} clients"
    if overdue:
        return (f"{waiting} waiting across {who}, {overdue} past the day it "
                "was asked for.")
    return f"{waiting} waiting across {who}, none overdue."


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(str(value or ""), safe="")
