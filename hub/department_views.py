"""Department Views — one curated dashboard per department, in Todd's own words.

There is no such thing as "the Sales view" or "the Creative view" of this Hub
today: everybody signs into the same sidebar and the same tile pages, and a
department that only cares about six of the twenty-odd tools here has to find
those six themselves, every time, among everything else. This is the overlay
that fixes that — an admin picks a name for a department, curates a short list
of tiles, links and notes for it, and assigns staff to it. Signing in and
opening **My View** shows exactly that list and nothing else.

## What it deliberately is not

**It is not a permission system.** Every tool a department's view links to is
already reachable from the sidebar and the tile pages to anyone who could sign
in to see it; a view leaves nothing more or less reachable than before, it
just says which handful of things matter to a given desk. Nobody's access
narrows because they were left off a view, and `/views/<id>` is readable by
any signed-in member of staff — the same "browse a colleague's book" openness
`hub/client_owner.py` gives the owner assignments.

**It does not replace the sidebar.** The sidebar is what the Hub can always
do; a department view is a shortlist someone chose on top of it. A view left
empty is not an error state, it is "nobody has curated this one yet."

## Storage, and why it is an overlay rather than a table

Two JSON files under `jsonstore.data_dir("department_views")`, mirrored the
way every durable JSON store in this Hub is (`hub/jsonstore.py`): departments
(each carrying its own ordered block list) in one file, and the
email -> department-id assignment map in the other — the `hub/client_owner.py`
split, because the two are different kinds of statement and keeping them in
one file would make "which department is Todd in" and "what does Sales' view
contain" two questions answered from the same accidental blob. Nothing here
is a SQLAlchemy model: `create_all()` never adds a column to an existing
table, and there is no existing table this belongs on, so a small hand-rolled
overlay costs less than a migration for a feature this size.

## The rules, each a way this goes quietly wrong otherwise

**A department is stored by its id, a person by their email — never by a
display name.** `hub/celebrations.mine()` already paid for this: two people on
this roster share a first name, so a display name identifies nobody and an
email identifies exactly one account. The id is a slug derived from the name
at creation and kept even if the name is edited later, the way a URL segment
in this Hub always outlives the label typed over it.

**A block is validated on the way in, never trusted on the way out.** A tile's
`href` is refused unless it starts with `/`, `http://` or `https://` — a
`javascript:` URL stored by a mistyped paste would otherwise run in the
browser of everyone that department's view is shown to. Anything else in a
block (labels, notes) is plain text, escaped by Jinja like every other value
in this Hub — never assembled into raw HTML here.

**Assigning nobody leaves an empty view, never a stored default.** There is no
"General" department created automatically to catch everyone: an unassigned
person sees an honest "nobody has put you on a view yet" rather than being
quietly enrolled in something nobody chose for them.

**Deleting a department un-assigns everyone on it rather than leaving a
dangling id.** `hub/client_owner.py`'s `unknown_owner` rule is for an owner who
left the company; a department that stops existing is a different situation —
there is no view left to disagree about, so the assignment is removed rather
than reported as broken.

**Nothing in here may raise past its own boundary.** A store that cannot be
read answers with an empty list rather than taking the page down; a roster
read that hits the database answers `(rows, error)` so the admin screen can
say it could not look rather than drawing an empty roster as a company with
nobody in it.
"""
from __future__ import annotations

import os
import re
import threading
import uuid
from datetime import datetime, timezone

from hub import jsonstore

_LOCK = threading.Lock()
_DEPT_FILE = "departments.json"
_ASSIGN_FILE = "assignments.json"

MAX_DEPARTMENTS = 40
MAX_BLOCKS = 40
MAX_NAME = 60
MAX_DESCRIPTION = 400
MAX_LABEL = 80
MAX_HREF = 300
MAX_NOTE = 600

BLOCK_TYPES = ("tile", "note")
RESERVED_IDS = {"manage", "mine", "admin", "new", "api"}


class DepartmentViewError(Exception):
    """A refusal a caller can show as-is: a bad name, a block that will not
    validate, an id that does not exist. Anything else is a real defect and
    is left to raise past this module."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(event: str, **fields) -> None:
    try:
        from hub import audit
        actor = fields.pop("actor", None)
        audit.log("department_views", event, actor=actor, tool="department_views", **fields)
    except Exception:                                       # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _dept_path() -> str:
    return os.path.join(jsonstore.data_dir("department_views"), _DEPT_FILE)


def _assign_path() -> str:
    return os.path.join(jsonstore.data_dir("department_views"), _ASSIGN_FILE)


def _load_departments() -> list[dict]:
    rows = jsonstore.read_json(_dept_path(), default=[])
    return rows if isinstance(rows, list) else []


def _save_departments(rows: list[dict]) -> bool:
    return jsonstore.write_json(_dept_path(), rows, indent=2)


def _load_assignments() -> dict:
    data = jsonstore.read_json(_assign_path(), default={})
    return data if isinstance(data, dict) else {}


def _save_assignments(data: dict) -> bool:
    return jsonstore.write_json(_assign_path(), data, indent=2)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug or "department"


def _unique_id(name: str, existing: set[str]) -> str:
    base = _slugify(name)
    if base in RESERVED_IDS:
        base = base + "-dept"
    candidate = base
    n = 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

def list_departments() -> list[dict]:
    rows = _load_departments()
    return sorted(rows, key=lambda d: (d.get("name") or "").lower())


def get_department(dept_id: str) -> dict | None:
    dept_id = (dept_id or "").strip().lower()
    for row in _load_departments():
        if row.get("id") == dept_id:
            return row
    return None


def create_department(name: str, description: str = "", actor_email: str = "") -> dict:
    name = (name or "").strip()
    if not name:
        raise DepartmentViewError("Give the department a name.")
    if len(name) > MAX_NAME:
        name = name[:MAX_NAME]
    description = (description or "").strip()[:MAX_DESCRIPTION]

    with _LOCK:
        rows = _load_departments()
        if len(rows) >= MAX_DEPARTMENTS:
            raise DepartmentViewError(
                f"There are already {MAX_DEPARTMENTS} department views — "
                "retire one before adding another.")
        existing_ids = {r.get("id") for r in rows}
        dept = {
            "id": _unique_id(name, existing_ids),
            "name": name,
            "description": description,
            "blocks": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        rows.append(dept)
        _save_departments(rows)
    _log("department_created", actor=actor_email, detail=name)
    return dept


def update_department(dept_id: str, name: str | None = None,
                       description: str | None = None,
                       actor_email: str = "") -> dict:
    with _LOCK:
        rows = _load_departments()
        for row in rows:
            if row.get("id") == dept_id:
                if name is not None:
                    name = name.strip()
                    if not name:
                        raise DepartmentViewError("The name cannot be blank.")
                    row["name"] = name[:MAX_NAME]
                if description is not None:
                    row["description"] = description.strip()[:MAX_DESCRIPTION]
                row["updated_at"] = _now()
                _save_departments(rows)
                _log("department_updated", actor=actor_email, detail=row["name"])
                return row
    raise DepartmentViewError("That department no longer exists.")


def delete_department(dept_id: str, actor_email: str = "") -> int:
    """Delete the department and un-assign anyone on it. Returns how many
    people were un-assigned, so the confirmation can say so rather than
    leaving that silent."""
    with _LOCK:
        rows = _load_departments()
        keep = [r for r in rows if r.get("id") != dept_id]
        if len(keep) == len(rows):
            raise DepartmentViewError("That department no longer exists.")
        name = next((r.get("name") for r in rows if r.get("id") == dept_id), dept_id)
        _save_departments(keep)

        assignments = _load_assignments()
        affected = [email for email, d in assignments.items() if d == dept_id]
        for email in affected:
            assignments.pop(email, None)
        if affected:
            _save_assignments(assignments)
    _log("department_deleted", actor=actor_email, detail=name,
         unassigned=len(affected))
    return len(affected)


# ---------------------------------------------------------------------------
# Blocks — the tiles, links and notes that make up a department's view
# ---------------------------------------------------------------------------

def _clean_block(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().lower()
    if kind not in BLOCK_TYPES:
        return None
    if kind == "note":
        note = str(raw.get("note") or "").strip()[:MAX_NOTE]
        if not note:
            return None
        return {"type": "note", "label": str(raw.get("label") or "").strip()[:MAX_LABEL],
                "note": note}
    # tile
    label = str(raw.get("label") or "").strip()[:MAX_LABEL]
    href = str(raw.get("href") or "").strip()[:MAX_HREF]
    if not label or not href:
        return None
    if not (href.startswith("/") or href.startswith("http://") or href.startswith("https://")):
        return None
    icon = str(raw.get("icon") or "").strip()[:8]
    return {"type": "tile", "label": label, "href": href, "icon": icon}


def save_blocks(dept_id: str, blocks: list, actor_email: str = "") -> tuple[list[dict], int]:
    """Replace a department's whole view in one write. Returns the blocks that
    were kept and how many of the ones sent in were dropped for not
    validating — reported rather than silently discarded, the
    `hub/domain_purchase.py` rule about a bulk write that quietly loses rows."""
    if not isinstance(blocks, list):
        blocks = []
    cleaned: list[dict] = []
    dropped = 0
    for raw in blocks[: MAX_BLOCKS + 20]:
        block = _clean_block(raw)
        if block is None:
            dropped += 1
        else:
            cleaned.append(block)
    if len(cleaned) > MAX_BLOCKS:
        dropped += len(cleaned) - MAX_BLOCKS
        cleaned = cleaned[:MAX_BLOCKS]

    with _LOCK:
        rows = _load_departments()
        found = False
        for row in rows:
            if row.get("id") == dept_id:
                row["blocks"] = cleaned
                row["updated_at"] = _now()
                found = True
                break
        if not found:
            raise DepartmentViewError("That department no longer exists.")
        _save_departments(rows)
    _log("view_saved", actor=actor_email, detail=dept_id, blocks=len(cleaned),
         dropped=dropped)
    return cleaned, dropped


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

def assignment_for(email: str) -> str | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    return _load_assignments().get(email)


def assignment_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for dept_id in _load_assignments().values():
        counts[dept_id] = counts.get(dept_id, 0) + 1
    return counts


def set_assignment(email: str, dept_id: str | None, actor_email: str = "") -> None:
    email = (email or "").strip().lower()
    if not email:
        raise DepartmentViewError("Nobody was named to assign.")
    dept_id = (dept_id or "").strip() or None
    if dept_id and get_department(dept_id) is None:
        raise DepartmentViewError("That department no longer exists.")

    with _LOCK:
        assignments = _load_assignments()
        if dept_id is None:
            assignments.pop(email, None)
        else:
            assignments[email] = dept_id
        _save_assignments(assignments)
    _log("assignment_changed", actor=actor_email, detail=email,
         department=dept_id or "(none)")


# ---------------------------------------------------------------------------
# Roster — who exists to be assigned, read from the account table
# ---------------------------------------------------------------------------

def roster() -> tuple[list[dict], str]:
    """(rows, error). A source that could not be read is named rather than
    drawn as a company with nobody in it — the `connected_accounts_result()`
    rule from Google Finder, applied to the account table."""
    try:
        from hub.users import User
    except Exception as exc:                                  # noqa: BLE001
        return [], f"Could not read the account table: {exc}"
    try:
        accounts = User.query.filter(User.status == "active").order_by(User.name).all()
    except Exception as exc:                                  # noqa: BLE001
        return [], f"Could not read the account table: {exc}"

    assignments = _load_assignments()
    rows = []
    for account in accounts:
        email = (account.email or "").lower()
        rows.append({
            "email": email,
            "name": account.name or account.email,
            "role": account.role,
            "department_id": assignments.get(email),
        })
    return rows, ""


# ---------------------------------------------------------------------------
# Catalog — the pick-list of every tile, report and page in the Hub
# ---------------------------------------------------------------------------

def catalog() -> list[dict]:
    """Every tool, report and menu page a block can point at, grouped the
    way `hub/qa_tasks.py` already builds it for its own "which page or tool"
    picker — reused rather than restated, or the two lists drift the day a
    tool is renamed and only one of the two pickers is fixed."""
    try:
        from hub.qa_tasks import targets
        return targets()
    except Exception:                                        # noqa: BLE001
        return []
