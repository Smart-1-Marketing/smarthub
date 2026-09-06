"""QA assignments — give somebody a page to check, and get their answer back.

## What this is for

The Hub has twenty-odd tools and nobody can test all of them. The way a check
actually gets done is that somebody is asked to do it: *open the Proposal
Builder, run a quote end to end, tell me what breaks.* Before this, that ask
lived in a chat message or a hallway conversation, and the answer came back
the same way — so there was no list of what had been asked, no record of what
came back, and no way to tell an open question from one somebody answered
three weeks ago.

This is that ask, written down: a task naming **a page or tool on the Hub**,
the **instructions**, **who it is for**, **when it was created**, and an
optional **need-by date**. The person it is for sees it on the dashboard,
answers it in words or with a file, and the person who raised it either ticks
it off or says what else they need.

## Rules this file works to, and what each one is undoing

**Anyone can assign, and the assigner owns the answer.** Not admins only. The
person who notices a page is wrong is very often not an admin, and a review
queue only an admin can fill is one nobody fills. What that costs is a rule:
a response goes back to *whoever raised the task*, never to a shared inbox —
`waiting_for_owner()` is keyed on `created_by_email` for that reason.

**A task is never deleted, only completed.** "What did we ask people to check
before the last release" is a question this table should be able to answer a
year from now, and a delete button is how it stops being able to.

**Four states, and the two middle ones are the point.** `open` (nobody has
answered), `answered` (the assignee replied and it is now the assigner's
move), `needs_more` (the assigner read it and asked for something else, so it
is the assignee's move again), `complete`. A single `done` flag would have
collapsed the two "somebody is waiting" states into one, which is precisely
the thing anybody looking at this list needs to tell apart.

**An attachment lives in the database, not on the disk and not in Cloudinary.**
The disk on Render is recreated by an infrastructure change and the JSON
mirror in `hub/jsonstore.py` exists because of exactly that; Cloudinary is
configured per environment and a review that cannot be filed because a key is
missing is a review that does not happen. A QA response is small and rare —
a screenshot, a spreadsheet, a PDF — so it goes in a `LargeBinary` column,
inside the database backup, with a hard size cap. `MAX_ATTACHMENT_BYTES` is
the cap, and it is enforced here rather than trusted to the app-wide
`MAX_CONTENT_LENGTH`, which is 512 MB and meant for video.

**The dropdown is built from the Hub's own index pages, not restated here.**
A hand-typed list of tools is a list that goes stale the week a tool is added
— the failure `hub/help_coverage.py` and `hub-crumbs.js` each have at length.
`targets()` reads the nav in `hub/sidebar.py`, the tiles on
`hub/templates/tools.html` and `hub/templates/creative.html`, and the reports
in `hub/qa.py`, so a tool tiled next month appears in the dropdown without
this file being edited. It ends with **"Something else"** and a free-text box
regardless: a dropdown that cannot hold the answer is worse than a text box,
which is a note this repo has already had to write down once.

**Nothing here raises into a page.** Every reader returns a shape with an
`error` string rather than throwing, and "we could not read the table" is
never drawn as "you have nothing to do" — the distinction `hub/presence.py`
makes at length, and it matters more here: an empty QA list is read as
permission to stop looking.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from functools import lru_cache

from sqlalchemy import (Column, Date, DateTime, Integer, LargeBinary, String,
                        Text)

from hub.extensions import db

# A screenshot, a spreadsheet, a PDF. Enforced here rather than left to the
# app-wide MAX_CONTENT_LENGTH, which is 512 MB because one module renders
# video: a QA response that can be half a gigabyte is a database backup
# nobody can restore.
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024

OPEN, ANSWERED, NEEDS_MORE, COMPLETE = "open", "answered", "needs_more", "complete"
STATUSES = (OPEN, ANSWERED, NEEDS_MORE, COMPLETE)

# What each state means on screen, in one place so the page, the dashboard
# card and the sign-in reminder cannot word it three ways.
STATUS_LABEL = {
    OPEN: "Waiting on them",
    ANSWERED: "Answered — your move",
    NEEDS_MORE: "More information asked for",
    COMPLETE: "Complete",
}

# Which side of the task each state is waiting on. Read by both queues rather
# than each of them restating the list, because a fifth state added later must
# not appear in one queue and silently vanish from the other.
ASSIGNEE_STATES = (OPEN, NEEDS_MORE)
OWNER_STATES = (ANSWERED,)


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _aware(value):
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class QaTask(db.Model):
    """One review somebody has been asked to do."""

    __tablename__ = "hub_qa_tasks"

    id = Column(Integer, primary_key=True)

    # What is being reviewed. The key is the href where there is one, so a
    # task links straight to the thing it is about; the label is stored
    # alongside rather than looked up on read, because a tool renamed next
    # year must not silently re-title a review written about the old one.
    target_key = Column(String(160), default="", index=True)
    target_label = Column(String(200), default="")
    target_href = Column(String(300), default="")

    instructions = Column(Text, default="")

    created_by_email = Column(String(255), default="", index=True)
    created_by_name = Column(String(160), default="")
    assigned_to_email = Column(String(255), default="", index=True)
    assigned_to_name = Column(String(160), default="")

    created_at = Column(DateTime, default=_now, index=True)
    # Optional by design — Todd asked for a need-by date that is not required,
    # and a required one would have been filled in with a guess.
    due_on = Column(Date, nullable=True, index=True)

    status = Column(String(20), default=OPEN, index=True)
    completed_at = Column(DateTime, nullable=True)
    # Bumped by every write, so both queues sort by "what moved last" rather
    # than by when the task was raised.
    last_activity_at = Column(DateTime, default=_now, index=True)

    # When each side last opened the task. This is what makes "notify me of
    # any new updates" answerable without a mailer: an update is new to you
    # if it happened after you last looked at it.
    assignee_seen_at = Column(DateTime, nullable=True)
    owner_seen_at = Column(DateTime, nullable=True)

    # ------------------------------------------------------------- derived
    @property
    def overdue(self) -> bool:
        if not self.due_on or self.status == COMPLETE:
            return False
        return self.due_on < _dt.date.today()

    def unread_for(self, email: str) -> bool:
        """Has something happened here that this person has not seen?

        Deliberately not "is there an unread message": the question every
        screen asks is whether *this task* has moved since you last had it
        open, which is one comparison rather than a per-row read flag that
        would have to be written for both people on every post.
        """
        email = (email or "").strip().lower()
        moved = _aware(self.last_activity_at)
        if moved is None:
            return False
        if email == (self.assigned_to_email or "").lower():
            seen = _aware(self.assignee_seen_at)
        elif email == (self.created_by_email or "").lower():
            seen = _aware(self.owner_seen_at)
        else:
            return False
        return seen is None or seen < moved

    def as_dict(self, viewer_email: str = "", responses: bool = False) -> dict:
        from hub import dates
        row = {
            "id": self.id,
            "target_key": self.target_key or "",
            "target_label": self.target_label or "(not named)",
            "target_href": self.target_href or "",
            "instructions": self.instructions or "",
            "created_by_email": self.created_by_email or "",
            "created_by_name": self.created_by_name or self.created_by_email or "",
            "assigned_to_email": self.assigned_to_email or "",
            "assigned_to_name": self.assigned_to_name or self.assigned_to_email or "",
            "created_at": _iso(self.created_at),
            "created_on_pretty": dates.fmt(self.created_at),
            "due_on": self.due_on.isoformat() if self.due_on else "",
            "due_on_pretty": dates.fmt(self.due_on) if self.due_on else "",
            "overdue": self.overdue,
            "status": self.status or OPEN,
            "status_label": STATUS_LABEL.get(self.status or OPEN, self.status or OPEN),
            "completed_at": _iso(self.completed_at),
            "last_activity_at": _iso(self.last_activity_at),
            "unread": self.unread_for(viewer_email),
            "mine_to_answer": (self.status in ASSIGNEE_STATES
                               and _same(self.assigned_to_email, viewer_email)),
            "mine_to_close": (self.status in OWNER_STATES
                              and _same(self.created_by_email, viewer_email)),
        }
        if responses:
            rows = (QaResponse.query.filter_by(task_id=self.id)
                    .order_by(QaResponse.created_at.asc()).all())
            row["responses"] = [r.as_dict() for r in rows]
        return row


class QaResponse(db.Model):
    """One post on a task — an answer, or a request for more information."""

    __tablename__ = "hub_qa_responses"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, nullable=False, index=True)

    author_email = Column(String(255), default="")
    author_name = Column(String(160), default="")

    # "reply" is the assignee answering; "request" is the assigner saying what
    # else they need. Two words rather than a boolean, because a third kind
    # (a note nobody has to act on) is the obvious next addition and a
    # boolean would have to be migrated to hold it.
    kind = Column(String(16), default="reply")

    body = Column(Text, default="")

    file_name = Column(String(255), default="")
    file_type = Column(String(120), default="")
    file_size = Column(Integer, default=0)
    # See the module docstring: in the database, inside the backup, capped.
    file_data = Column(LargeBinary, nullable=True)

    created_at = Column(DateTime, default=_now, index=True)

    def as_dict(self) -> dict:
        from hub import dates
        return {
            "id": self.id,
            "task_id": self.task_id,
            "author_email": self.author_email or "",
            "author_name": self.author_name or self.author_email or "",
            "kind": self.kind or "reply",
            "body": self.body or "",
            "file_name": self.file_name or "",
            "file_type": self.file_type or "",
            "file_size": self.file_size or 0,
            "file_url": (f"/qa-tasks/attachment/{self.id}"
                         if self.file_name else ""),
            "created_at": _iso(self.created_at),
            "created_on_pretty": dates.fmt(self.created_at),
        }


def _iso(value) -> str:
    value = _aware(value)
    return value.isoformat() if value else ""


def _same(a: str, b: str) -> bool:
    return bool(a) and (a or "").strip().lower() == (b or "").strip().lower()


class QaTaskError(Exception):
    """Message is safe to show the person who caused it."""


# ---------------------------------------------------------------------------
# What can be reviewed — read from the Hub, never restated here
# ---------------------------------------------------------------------------

_TILE = re.compile(r'<a class="tool-tile"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_TAGS = re.compile(r"<[^>]+>")

_TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _tiles(filename: str) -> list[tuple[str, str]]:
    """(href, name) for every tile on one index page.

    The same read `test_menu_layout.py` does, for the same reason: the tiles
    are the answer to "which tools exist", so anything else is a second list
    that will disagree with them. Never raises — a dropdown that is short is
    recoverable, a page that 500s because a template moved is not.
    """
    try:
        with open(os.path.join(_TEMPLATES, filename), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return []
    out = []
    for match in _TILE.finditer(text):
        head = _H3.search(match.group(2))
        if not head:
            continue
        name = _TAGS.sub(" ", head.group(1))
        name = re.sub(r"\s+", " ", name).replace("&amp;", "&").strip()
        if name:
            out.append((match.group(1), name))
    return out


@lru_cache(maxsize=1)
def targets() -> list[dict]:
    """Every page and tool somebody can be asked to check, grouped.

    Four sources, none of them a list written out here:

      * the nav in `hub/sidebar.py` — the pages somebody opens by name;
      * the tiles on `/tools` and `/creative` — the tools;
      * the reports on `/qa`;
      * and "Something else", always last, with a free-text box behind it.

    Cached for the life of the process: the templates and the nav are source
    files, so re-reading them per request would be four file reads to get the
    same answer. A deploy is what changes them, and a deploy is a new process.
    """
    groups: dict[str, list[dict]] = {}
    seen: set[str] = set()

    def add(group: str, label: str, href: str, key: str = ""):
        href = (href or "").strip()
        marker = (key or href or label).strip().lower()
        if not label or marker in seen:
            return
        seen.add(marker)
        groups.setdefault(group, []).append({
            "key": key or href or label, "label": label, "href": href})

    try:
        from hub.sidebar import visible_items
        for row in visible_items(is_admin=True):
            key, href, _ico, label = row[0], row[1], row[2], row[3]
            if key.startswith("_sec") or not href:
                continue
            add("Pages in the menu", label, href, key="nav:" + key)
    except Exception:                                   # noqa: BLE001
        pass

    for filename, group in (("tools.html", "Client tools"),
                            ("creative.html", "Creative tools")):
        for href, name in _tiles(filename):
            add(group, name, href)

    try:
        from hub import qa as qa_reports
        for key, meta in getattr(qa_reports, "REPORTS", {}).items():
            add("QA reports", meta.get("title") or key,
                meta.get("href") or f"/qa/{key}")
        for _group, key, meta in getattr(qa_reports, "EXTRAS", []):
            add("QA reports", meta.get("title") or key, meta.get("href") or "")
    except Exception:                                   # noqa: BLE001
        pass

    out = [{"group": g, "items": sorted(items, key=lambda i: i["label"].lower())}
           for g, items in groups.items()]
    # "Something else" last and always present. A dropdown that cannot hold
    # the answer is worse than a text box — this repo has written that down
    # once already, about the ad copy request form.
    out.append({"group": "Not listed", "items": [
        {"key": "other", "label": "Something else — I'll describe it", "href": ""}]})
    return out


def resolve_target(key: str, other: str = "") -> tuple[str, str, str]:
    """(key, label, href) for what the form chose. Raises if it chose nothing.

    `other` is only read when the choice was the free-text option, so a
    stray value in that box cannot quietly retitle a real selection.
    """
    key = (key or "").strip()
    if key == "other":
        label = (other or "").strip()
        if not label:
            raise QaTaskError("Say which page or tool this is about.")
        return "other", label[:200], ""
    for group in targets():
        for item in group["items"]:
            if item["key"] == key:
                return item["key"], item["label"], item["href"]
    raise QaTaskError("Pick the page or tool this is about.")


# ---------------------------------------------------------------------------
# Who a task can be assigned to
# ---------------------------------------------------------------------------

def assignable() -> dict:
    """Every account that can actually sign in and do the work.

    Pending and suspended accounts are left out on purpose: a task assigned to
    somebody who cannot log in sits open for ever with nobody able to answer
    it, and nothing on either screen would say why.

    Returns `{"people": [...], "error": ""}` — an error rather than an empty
    list when the table could not be read, because "there is nobody to assign
    to" and "we could not look" are different answers.
    """
    try:
        from hub.users import User
        rows = (User.query.filter_by(status="active")
                .order_by(User.name, User.email).all())
    except Exception:                                   # noqa: BLE001
        return {"people": [], "error": "the account list could not be read"}
    return {"people": [{"email": u.email, "name": u.name or u.email.split("@")[0]}
                       for u in rows if u.email], "error": ""}


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create(*, target_key: str, target_other: str, instructions: str,
           assigned_to_email: str, due_on: str,
           actor_email: str, actor_name: str) -> QaTask:
    """Raise a task. Every field is validated here, not in the route.

    The date-stamp Todd asked for is `created_at`, and it is set here rather
    than defaulted by the column alone so a row written by a future importer
    carries the same clock as one written by the form.
    """
    key, label, href = resolve_target(target_key, target_other)

    instructions = (instructions or "").strip()
    if not instructions:
        raise QaTaskError("Say what you'd like them to do.")
    if len(instructions) > 20000:
        raise QaTaskError("That is longer than this box can hold — attach a "
                          "document instead and summarize it here.")

    assigned_to_email = (assigned_to_email or "").strip().lower()
    if not assigned_to_email:
        raise QaTaskError("Choose who this is for.")
    people = assignable()
    if people["error"]:
        raise QaTaskError("The account list could not be read, so this cannot "
                          "be assigned yet. Try again in a moment.")
    match = next((p for p in people["people"]
                  if p["email"].lower() == assigned_to_email), None)
    if match is None:
        raise QaTaskError("That person does not have an active Hub account.")

    due = None
    if (due_on or "").strip():
        from hub import dates
        due = dates.to_date(due_on)
        if due is None:
            raise QaTaskError("That need-by date could not be read. It is "
                              "optional — leave it blank if there isn't one.")

    now = _now()
    task = QaTask(
        target_key=key[:160], target_label=label[:200], target_href=(href or "")[:300],
        instructions=instructions,
        created_by_email=(actor_email or "").strip().lower()[:255],
        created_by_name=(actor_name or "").strip()[:160],
        assigned_to_email=match["email"].lower()[:255],
        assigned_to_name=match["name"][:160],
        created_at=now, due_on=due, status=OPEN, last_activity_at=now,
        # The person who raised it has, by definition, seen it.
        owner_seen_at=now, assignee_seen_at=None,
    )
    db.session.add(task)
    db.session.commit()
    _log("assigned", actor=actor_email, task=task.id, target=label,
         assigned_to=match["email"])
    return task


def respond(task_id: int, *, body: str, actor_email: str, actor_name: str,
            file_name: str = "", file_type: str = "",
            file_bytes: bytes | None = None) -> QaResponse:
    """Post an answer, or a request for more information.

    Which of the two it is comes from **who is posting**, not from a flag the
    caller passes: the assignee answering and the assigner asking are the same
    action from the table's point of view, and letting a route decide would be
    a second place the state machine lives.
    """
    task = QaTask.query.get(int(task_id))
    if task is None:
        raise QaTaskError("That task could not be found.")

    email = (actor_email or "").strip().lower()
    is_assignee = _same(task.assigned_to_email, email)
    is_owner = _same(task.created_by_email, email)
    if not (is_assignee or is_owner):
        raise QaTaskError("This task is between somebody else and the person "
                          "they asked. You can read it, but not answer it.")

    body = (body or "").strip()
    if not body and not file_bytes:
        raise QaTaskError("Write something, attach a file, or both.")
    if len(body) > 40000:
        raise QaTaskError("That is longer than this box can hold — attach it "
                          "as a document instead.")

    if file_bytes is not None and len(file_bytes) > MAX_ATTACHMENT_BYTES:
        raise QaTaskError(
            f"That file is {len(file_bytes) // (1024 * 1024)} MB and the limit "
            f"is {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB. Send a link to it "
            f"in the box instead.")

    now = _now()
    post = QaResponse(
        task_id=task.id, author_email=email[:255],
        author_name=(actor_name or "").strip()[:160],
        # The assignee always replies. The owner replying to their own task —
        # which happens, because the owner is sometimes also the assignee —
        # is a reply too, and only an owner who is NOT the assignee is asking
        # for more.
        kind="reply" if is_assignee else "request",
        body=body,
        file_name=(file_name or "")[:255], file_type=(file_type or "")[:120],
        file_size=len(file_bytes) if file_bytes else 0,
        file_data=file_bytes if file_bytes else None,
        created_at=now,
    )
    db.session.add(post)

    if is_assignee:
        task.status = ANSWERED
        task.assignee_seen_at = now
    else:
        task.status = NEEDS_MORE
        task.owner_seen_at = now
    task.last_activity_at = now
    db.session.commit()
    _log("answered" if is_assignee else "more_info_asked", actor=email,
         task=task.id, target=task.target_label)
    return post


def complete(task_id: int, *, actor_email: str) -> QaTask:
    """Tick it off. Only the person who raised it can.

    Not the assignee, and that is the decision: a review the reviewer closes
    themselves is a review nobody read.
    """
    task = QaTask.query.get(int(task_id))
    if task is None:
        raise QaTaskError("That task could not be found.")
    if not _same(task.created_by_email, actor_email):
        raise QaTaskError("Only the person who raised this can close it.")
    now = _now()
    task.status = COMPLETE
    task.completed_at = now
    task.last_activity_at = now
    task.owner_seen_at = now
    db.session.commit()
    _log("completed", actor=actor_email, task=task.id, target=task.target_label)
    return task


def reopen(task_id: int, *, actor_email: str) -> QaTask:
    """Undo a completion. Same hand that closed it, and nothing is deleted."""
    task = QaTask.query.get(int(task_id))
    if task is None:
        raise QaTaskError("That task could not be found.")
    if not _same(task.created_by_email, actor_email):
        raise QaTaskError("Only the person who raised this can reopen it.")
    now = _now()
    task.status = NEEDS_MORE
    task.completed_at = None
    task.last_activity_at = now
    task.owner_seen_at = now
    db.session.commit()
    _log("reopened", actor=actor_email, task=task.id, target=task.target_label)
    return task


def mark_seen(task_id: int, *, actor_email: str) -> bool:
    """Record that this person has now looked at the task.

    This is what makes the "new since you last looked" mark honest, and it is
    written on **opening one task**, never on loading the list: a badge that
    clears itself because somebody glanced at a dashboard is a badge that
    stops meaning anything.
    """
    try:
        task = QaTask.query.get(int(task_id))
        if task is None:
            return False
        email = (actor_email or "").strip().lower()
        now = _now()
        if _same(task.assigned_to_email, email):
            task.assignee_seen_at = now
        elif _same(task.created_by_email, email):
            task.owner_seen_at = now
        else:
            return False
        db.session.commit()
        return True
    except Exception:                                   # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:                               # noqa: BLE001
            pass
        return False


def _log(event: str, **fields) -> None:
    try:
        from hub import audit
        audit.log("qa_tasks", event, actor=fields.pop("actor", None), **fields)
    except Exception:                                   # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _rows(query, viewer: str, limit: int) -> list[dict]:
    return [t.as_dict(viewer_email=viewer)
            for t in query.order_by(QaTask.last_activity_at.desc()).limit(limit).all()]


def for_person(email: str, limit: int = 200) -> dict:
    """Everything on one person's plate, both directions.

    Returns `measured: False` with a sentence rather than empty lists when the
    table could not be read. Both screens that draw this interpolate the
    message straight into the page, so it is a sentence and never the
    exception — the rule `hub/presence.py` states and the image optimizers
    were fixed for.
    """
    email = (email or "").strip().lower()
    out = {
        "measured": True, "error": "", "email": email,
        "to_do": [], "waiting_on_you": [], "raised_by_you": [], "done": [],
        "counts": {"to_do": 0, "overdue": 0, "waiting_on_you": 0,
                   "unread": 0, "open_total": 0},
        "line": "",
    }
    if not email:
        out["measured"] = False
        out["error"] = ("This session has no account behind it, so there is "
                        "nobody to show tasks for. Sign in with your own "
                        "account to use QA tasks.")
        return out
    try:
        mine = QaTask.query.filter(QaTask.assigned_to_email == email,
                                   QaTask.status.in_(ASSIGNEE_STATES))
        back = QaTask.query.filter(QaTask.created_by_email == email,
                                   QaTask.status.in_(OWNER_STATES))
        raised = QaTask.query.filter(QaTask.created_by_email == email,
                                     QaTask.status.in_((OPEN, NEEDS_MORE)))
        done = QaTask.query.filter(
            (QaTask.created_by_email == email) | (QaTask.assigned_to_email == email),
            QaTask.status == COMPLETE)
        out["to_do"] = _rows(mine, email, limit)
        out["waiting_on_you"] = _rows(back, email, limit)
        out["raised_by_you"] = _rows(raised, email, limit)
        out["done"] = _rows(done, email, 50)
    except Exception as exc:                            # noqa: BLE001
        out["measured"] = False
        out["error"] = "the QA task list could not be read"
        _warn("for_person could not read the table", exc)
        return out

    out["counts"] = {
        "to_do": len(out["to_do"]),
        "overdue": sum(1 for r in out["to_do"] if r["overdue"]),
        "waiting_on_you": len(out["waiting_on_you"]),
        "unread": sum(1 for r in out["to_do"] + out["waiting_on_you"]
                      if r["unread"]),
        "open_total": len(out["to_do"]) + len(out["waiting_on_you"]),
    }
    out["line"] = summary_line(out["counts"])
    return out


def summary_line(counts: dict) -> str:
    """The one sentence every screen prints, so none of them word it
    differently — the dashboard card, the QA Tasks page and the sign-in
    reminder all call this."""
    todo = counts.get("to_do", 0)
    back = counts.get("waiting_on_you", 0)
    if not todo and not back:
        return "Nothing is waiting on you."
    parts = []
    if todo:
        parts.append(f"{todo} review{'s' if todo != 1 else ''} to do")
        overdue = counts.get("overdue", 0)
        if overdue:
            parts[-1] += f" ({overdue} past the need-by date)"
    if back:
        parts.append(f"{back} answer{'s' if back != 1 else ''} waiting on your reply")
    return " · ".join(parts) + "."


def board(limit: int = 300) -> dict:
    """Every open task, whoever it belongs to — the whole-team view.

    Todd asked that anyone be able to assign, which makes "what has this team
    been asked to check" a question with no single owner. The board answers
    it. Completed tasks are excluded here and listed on the page's own
    history tab, so the working view is what is outstanding.
    """
    out = {"measured": True, "error": "", "tasks": []}
    try:
        rows = (QaTask.query.filter(QaTask.status != COMPLETE)
                .order_by(QaTask.last_activity_at.desc()).limit(limit).all())
    except Exception as exc:                            # noqa: BLE001
        _warn("board could not read the table", exc)
        return {"measured": False, "error": "the QA task list could not be read",
                "tasks": []}
    out["tasks"] = [t.as_dict() for t in rows]
    return out


def get(task_id: int, viewer_email: str = "") -> dict | None:
    try:
        task = QaTask.query.get(int(task_id))
    except Exception as exc:                            # noqa: BLE001
        _warn("get could not read the table", exc)
        return None
    if task is None:
        return None
    return task.as_dict(viewer_email=viewer_email, responses=True)


def attachment(response_id: int):
    """(bytes, filename, mimetype) for one attachment, or None."""
    try:
        row = QaResponse.query.get(int(response_id))
    except Exception as exc:                            # noqa: BLE001
        _warn("attachment could not read the table", exc)
        return None
    if row is None or not row.file_data:
        return None
    return (row.file_data, row.file_name or "attachment",
            row.file_type or "application/octet-stream")


def _warn(message: str, exc) -> None:
    """The cause goes to the log; the screen gets the sentence above.

    A SQLAlchemy OperationalError carries the database host, the user it tried
    to authenticate as and the SQL it was running, and every one of these
    readers is drawn straight onto a page.
    """
    try:
        import logging
        logging.getLogger(__name__).warning("qa_tasks: %s: %s", message, exc)
    except Exception:                                   # noqa: BLE001
        pass
