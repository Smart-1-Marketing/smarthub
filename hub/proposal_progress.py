"""Where each launch task and each piece of creative on a plan stands.

`hub/proposal_plan.resolve()` puts a due date on every kept launch task and
creative item the moment the launch date is answered, and the kickoff
document prints them -- and until this module nothing read them back. A
task due last Tuesday looked identical to one due next month, there was no
way to say a task was done, and a banner set the Display Ad Builder had
already delivered for the client sat on the plan exactly as it did the day
the plan was built. The monthly promises had all three answered
(`hub/proposal_promises.py`: landed from the work log, marked by hand, due
or missed against the calendar); this is the same reading for the two
lists that are not monthly.

One state per item, decided in this order and drawn everywhere from it:

* **dropped** -- a person said it is not needed.
* **to review** -- nobody has kept or dropped it yet. Not overdue, whatever
  its date: the plan's own summary already counts what is unreviewed, and
  an unreviewed proposal is not work anybody has agreed to do.
* **done** -- a person pressed Done, with their name and the day on the
  mark. Stored on the item, the one thing here that is written.
* **landed** -- the Hub saw the work for itself: creative filed against
  this client since the run was made, in the activity log under the tool
  that makes that kind of file, or the client uploading through their
  link. Named with the tool and the day and **never written down**: it is
  derived on every read, like the due date beside it, so a log entry that
  turns out to be a different campaign's can be walked back by nothing
  more than a person pressing Done or not.
* **overdue** -- kept, past its date, and neither of the above.
* **open** -- kept and nothing to report yet.

Two rules on the evidence. **It follows the supplier answer**: an item the
client is supplying is proved by the client uploading and not by our
tools, an item Smart 1 produces by our tools and not by an upload, and one
nobody has answered by either. **It starts when the run started** (the
earliest run in the supersede chain), because the same client's display
pack from a campaign two years ago is not this plan's banners. What that
costs is the file that arrived *before* the plan was built, which then
reads as open until somebody presses Done -- the safe direction to be
wrong in, since a false landed hides a gap and a false open costs a press.

The evidence is keyed on the **tool** that makes the item -- the same
table the item's own Make-it-in button is drawn from -- rather than on the
kind of file, because a social post graphic and a banner set are both
images and are made in different tools, and a display pack must not close
the graphic. What it still cannot tell apart is two kept items of one tool
on one plan: a display set and a retargeting set are both a Display Ad
Builder pack, and one delivery reads as evidence for both. The row names
the delivery, so a person can see which it was; the module does not guess.

A log that could not be read is said rather than read as nothing: the
status still counts an item past its date as overdue, because the date is
a fact, and `progress["log_error"]` and the item's `evidence_unknown` say
that whether it landed is not known. `measured` is False only then.
"""
from __future__ import annotations

import json
from datetime import date

DROPPED = "dropped"
TO_REVIEW = "to_review"
DONE = "done"
LANDED = "landed"
OVERDUE = "overdue"
OPEN = "open"

STATE_LABELS = {
    DROPPED: "not needed", TO_REVIEW: "to review", DONE: "done",
    LANDED: "landed", OVERDUE: "overdue", OPEN: "open",
}

# The two lists this reads. Monthly promises are month by month and live in
# hub/proposal_promises.py; a done mark on one of those is a mark on a month.
LISTS = ("creative", "launch")

# A board task in one of these states has drafted the copy it exists for.
TASK_DONE_STATES = ("approved", "completed", "live")

# What proves a creative item was made by us, keyed on the **tool that makes
# it** -- `hub/proposal_plan.tool_for()`, the same reading the item's own
# Make-it-in button is drawn from -- so a Display Ad Builder pack closes a
# banner set and not the social graphic beside it, which Image Creator or
# the planner makes. Spelled as (module, action fragments) the way
# hub/proposal_plan.PROMISE_KINDS spells them, and only modules
# hub/client_brand.WORK_KINDS can name, or the row never reaches the work
# index at all (the display_ads failure, one reader over).
# `creative_attached` / `animation_attached` are the two Display Ad Builder
# events that carry a client; the proxy's own actions know no client.
TOOL_EVIDENCE: dict[str, tuple] = {
    "display": (("display_ads", ("creative_attached", "animation_attached")),
                ("magic_resize", ("pack_exported",))),
    "image": (("image_creator", ("export_saved", "animated_export")),
              ("magic_resize", ("pack_exported",)),
              ("stock_photos", ())),
    "social": (("image_creator", ("export_saved", "animated_export")),
               ("social_planner", ("exported", "post_pushed"))),
    "gpt": (("gpt_ads", ("exported", "image_generated", "image_uploaded")),),
    "video": (("commercial_builder", ("commercial_approved",)),
              ("video_tools", ("_saved",)),
              ("vox_explainer", ()), ("paint_animation", ())),
    "audio": (("radio_promo", ("project.mix", "project.render")),
              ("fan_radio", ("spot_mixed", "spot_recorded"))),
}
# A file of any kind arriving through the client's own upload link.
CLIENT_EVIDENCE: tuple = (("image_picker", ("client_upload",)),)

MAX_EVIDENCE = 3


def done_key(run_id, item_id: str) -> str:
    """The subject a client-health issue about an overdue item carries, and
    the key `hub/proposal_execution.done_index()` files a done mark under --
    one spelling, so a Done pressed on the plan page can take the issue off
    My Clients on read. Two parts, where a promise mark has three."""
    return f"{int(run_id)}|{str(item_id or '')}"


def _day(value) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def tool_key(item: dict) -> str:
    """The key of the tool that makes this item, off the same table the
    item's action button is drawn from; blank for copy."""
    from hub import proposal_plan
    try:
        tool = proposal_plan.tool_for(item or {})
    except Exception:                                   # noqa: BLE001
        tool = None
    return str((tool or {}).get("key") or "")


def evidence_rules(item: dict) -> tuple:
    """Which log rows may prove this item, decided by the tool that makes
    it and its supplier answer. A tool the table does not know proves
    nothing by itself -- `test_proposal_progress.py` holds the table to
    `proposal_plan.CREATIVE_TOOLS` so that cannot happen quietly -- and the
    client's upload still can."""
    key = tool_key(item)
    who = str((item or {}).get("supplier") or "")
    tools = tuple(TOOL_EVIDENCE.get(key) or ())
    if who == "client":
        return CLIENT_EVIDENCE
    if who == "smart1":
        return tools
    return tools + CLIENT_EVIDENCE


def evidence_for(item: dict, rows: list[dict], since: str = "", cap: int = MAX_EVIDENCE) -> list[dict]:
    """The work rows that prove this creative item landed, newest first,
    capped -- a tag is a sentence, not a log. `since` is an ISO day; rows
    before it are another campaign's."""
    from hub import proposal_promises
    rules = evidence_rules(item)
    floor = str(since or "")[:10]
    hits = [r for r in rows
            if (not floor or str(r.get("when") or "")[:10] >= floor)
            and proposal_promises._matches(rules, r)]
    hits.sort(key=lambda r: str(r.get("when") or ""), reverse=True)
    return [{"when": str(r.get("when") or "")[:10], "source": r.get("source") or "",
             "module": r.get("module") or "", "action": r.get("action") or "",
             "detail": r.get("detail") or ""} for r in hits[:cap]]


def status_of(item: dict, *, today: date, landed: bool) -> tuple[str, int]:
    """One item's state and, for an overdue one, how many days past."""
    if item.get("accepted") is False:
        return DROPPED, 0
    if item.get("accepted") is not True:
        return TO_REVIEW, 0
    if item.get("done"):
        return DONE, 0
    if landed:
        return LANDED, 0
    due = _day(item.get("due"))
    if due and due < today:
        return OVERDUE, (today - due).days
    return OPEN, 0


def apply(plan: dict, *, client: str, run_id=0, since: str = "", today: date | None = None,
          work: dict | None = None, task_states: dict | None = None) -> dict:
    """The resolved plan with a `status` on every launch and creative item
    and `resolved["progress"]` counting them. Derived on every read and
    written nowhere. Never raises: a log that could not be read costs the
    landed marks and is named, never the plan it sits on."""
    from hub import proposal_plan, proposal_promises
    plan = json.loads(json.dumps(plan or {}))
    today = today or date.today()
    states = {str(k): str(v or "") for k, v in (task_states or {}).items()}
    rows: list[dict] = []
    log_error = ""
    try:
        rows, index = proposal_promises.client_rows(client, work)
        log_error = str((index or {}).get("error") or "")
    except Exception as exc:                            # noqa: BLE001
        log_error = f"The activity log could not be read ({type(exc).__name__})."
    progress = {
        "measured": not log_error, "log_error": log_error,
        "since": str(since or "")[:10], "today": today.isoformat(),
        "lists": {}, "overdue": 0, "done": 0, "landed": 0, "open": 0,
        "overdue_items": [],
    }
    for name in LISTS:
        counts = {"kept": 0, "done": 0, "landed": 0, "overdue": 0, "open": 0}
        for it in plan.get(name) or []:
            evidence: list[dict] = []
            kept = it.get("accepted") is True
            if name == "creative" and kept and not it.get("done"):
                if it.get("kind") == "copy":
                    # Copy is drafted by a board task, not filed by a tool;
                    # the task reaching a done state is the evidence.
                    key = proposal_plan.COPY_TASKS.get(str(it.get("channel") or ""), "")
                    if key and states.get(key) in TASK_DONE_STATES:
                        evidence = [{"when": "", "source": "the board", "module": "",
                                     "action": states.get(key, ""), "detail": f"task {key}"}]
                elif not log_error:
                    evidence = evidence_for(it, rows, since)
            it["landed"] = evidence[0] if evidence else {}
            it["landed_evidence"] = evidence
            it["evidence_unknown"] = bool(log_error and name == "creative" and kept
                                          and not it.get("done") and it.get("kind") != "copy")
            state, days = status_of(it, today=today, landed=bool(evidence))
            it["status"], it["status_label"], it["overdue_days"] = state, STATE_LABELS[state], days
            if kept:
                counts["kept"] += 1
                if state in counts:
                    counts[state] += 1
            if state == OVERDUE:
                progress["overdue_items"].append({
                    "key": done_key(run_id, it.get("id") or "") if run_id else str(it.get("id") or ""),
                    "id": it.get("id") or "", "list": name, "title": it.get("title") or "",
                    "due": it.get("due") or "", "due_label": it.get("due_label") or "",
                    "days": days, "owner": it.get("owner") or "",
                })
        progress["lists"][name] = counts
        for k in ("done", "landed", "overdue", "open"):
            progress[k] += counts[k]
    plan.setdefault("resolved", {})["progress"] = progress
    return plan


def counts(plan: dict) -> dict:
    """The numbers a record or a report reads off a plan this module has
    been over -- never the items. What `_run_plan_summary()` carries as
    `progress`."""
    p = ((plan or {}).get("resolved") or {}).get("progress") or {}
    return {
        "measured": bool(p.get("measured", False)) if p else False,
        "log_error": p.get("log_error") or "",
        "overdue": int(p.get("overdue") or 0), "done": int(p.get("done") or 0),
        "landed": int(p.get("landed") or 0), "open": int(p.get("open") or 0),
        "lists": dict(p.get("lists") or {}),
        "overdue_items": list(p.get("overdue_items") or []),
    }


__all__ = ["DROPPED", "TO_REVIEW", "DONE", "LANDED", "OVERDUE", "OPEN", "STATE_LABELS", "LISTS",
           "TASK_DONE_STATES", "TOOL_EVIDENCE", "CLIENT_EVIDENCE", "done_key", "tool_key",
           "evidence_rules", "evidence_for", "status_of", "apply", "counts"]
