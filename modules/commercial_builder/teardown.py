"""What a delete actually takes with it, read in one place.

`Client.projects` and `CommercialProject.scenes`/`.render_jobs` cascade, so
deleting either end takes the obvious rows. What it does *not* take are the
three tables keyed on a project that sit outside those relationships — the
render approvals, the review shares with their decisions and comments, and
the compliance acknowledgments. Those stayed behind pointing at ids that no
longer resolve, which is not a record: a compliance sign-off naming a project
nobody can look up says nothing, and those three are precisely the rows that
exist for the day a client says **"we never signed off on that"**.

Both delete routes had the identical failure and would have grown identical
fixes. Two readings of one question drift the day either is edited -- the
rule this codebase applies to rate cards, client keys and gallery labels
alike -- so the counting, the sweep and the wording are here and both routes
read them.

**Nothing in this module may raise.** A count that cannot be taken must not
cost the refusal it informs, and a sweep that fails must not strand the
delete somebody asked for; each entry point answers with something usable
instead. That is the same rule `hub/audit.py` works to, for the same reason:
bookkeeping that can break the thing it describes is worse than none.
"""

from .db import db
from .models import (ComplianceAck, CommercialProject, RenderApproval,
                     RenderJob, ReviewShare, Scene, Variation)

# Keyed on a project, and outside every cascade that reaches one.
ORPHANABLE = (RenderApproval, ReviewShare, ComplianceAck)

# What each count is called in a sentence somebody reads. Ordered: the spot
# first, then the three records whose loss is the reason a delete is refused
# at all. Scenes and render jobs are deliberately absent -- they are parts of
# a spot rather than things anybody weighs separately, and naming seven
# figures in a refusal is a refusal nobody reads to the end of.
NAMED = (
    ("projects", "spot", "spots"),
    ("approved_cuts", "approved cut", "approved cuts"),
    ("review_rounds", "review round", "review rounds"),
    ("compliance_acks", "compliance sign-off", "compliance sign-offs"),
)

_ZERO = {"projects": 0, "scenes": 0, "render_jobs": 0, "approved_cuts": 0,
         "review_rounds": 0, "compliance_acks": 0, "variations": 0}


def project_ids_for_client(client_id) -> list:
    """Every project id on this client. An empty list on any failure."""
    try:
        return [row[0] for row in db.session.query(CommercialProject.id)
                .filter(CommercialProject.client_id == client_id).all()]
    except Exception:  # noqa: BLE001
        return []


def work_behind(project_ids) -> dict:
    """Counts of what would go, taken before anything is deleted.

    Counts rather than rows: this answers a refusal and an activity entry,
    and neither is improved by carrying a client's whole book into a response
    or a log line.
    """
    out = dict(_ZERO)
    pids = list(project_ids or ())
    out["projects"] = len(pids)
    if not pids:
        return out
    try:
        for key, model in (("scenes", Scene), ("render_jobs", RenderJob),
                           ("approved_cuts", RenderApproval),
                           ("review_rounds", ReviewShare),
                           ("compliance_acks", ComplianceAck)):
            out[key] = model.query.filter(model.project_id.in_(pids)).count()
        out["variations"] = Variation.query.filter(
            Variation.parent_project_id.in_(pids)
            | Variation.child_project_id.in_(pids)).count()
    except Exception:  # noqa: BLE001
        pass
    return out


def summarize(counts, include_projects: bool = True) -> str:
    """The counts as a sentence, naming only what is actually there.

    Empty when there is nothing behind the delete, which is what the routes
    branch on: a client or a spot with no work is a row somebody is tidying
    up, and asking them to type a name for it is friction with nothing behind
    it.

    `include_projects` is the difference between the two callers, and it is a
    parameter rather than a second function because getting it wrong is
    silent in both directions. Deleting a **client** weighs the spots that go
    with them. Deleting a **spot** does not weigh itself: counted, every
    delete of an untouched draft would come back asking the rep to type its
    title, which is the friction that gets a confirmation clicked through
    without being read -- and then it is not a confirmation.
    """
    parts = []
    for key, one, many in NAMED:
        if key == "projects" and not include_projects:
            continue
        n = (counts or {}).get(key) or 0
        if n:
            parts.append(f"{n} {one if n == 1 else many}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def sweep_orphans(project_ids) -> int:
    """Remove the rows keyed on these projects that no cascade reaches.

    Returns how many went. A failure rolls back its own statements and
    answers 0 rather than raising, so the delete the caller was asked for
    still happens -- a half-swept teardown is the state this exists to
    prevent, but stranding the delete is worse than sweeping it later.
    """
    pids = list(project_ids or ())
    if not pids:
        return 0
    gone = 0
    try:
        for model in ORPHANABLE:
            gone += (model.query.filter(model.project_id.in_(pids))
                     .delete(synchronize_session=False))
        gone += (Variation.query.filter(
            Variation.parent_project_id.in_(pids)
            | Variation.child_project_id.in_(pids))
            .delete(synchronize_session=False))
    except Exception:  # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return gone


def confirmation_error(name, summary):
    """The refusal both routes give, with the way through named in it.

    Not a wall: a check that refuses the correct thing is one somebody
    switches off, and switching this off would cost the recording too. The
    way through carries the thing's own name, so the press is deliberate
    rather than a second OK button -- the rule `modules/image_picker` applies
    to deleting a gallery.
    """
    return (f"{name} has {summary} behind it, and deleting it deletes all of "
            f"that too. This cannot be undone. Send confirm=\"{name}\" to go "
            f"ahead.")


def confirmed(request, name) -> bool:
    """Did the caller type this exact name?

    Read from the JSON body or the query string, because a DELETE carrying a
    body is awkward from some clients and refusing one spelling would leave
    the way through unreachable from the other.
    """
    given = ""
    try:
        if request.is_json:
            given = ((request.get_json(silent=True) or {}).get("confirm") or "").strip()
        given = given or (request.args.get("confirm") or "").strip()
    except Exception:  # noqa: BLE001
        return False
    return bool(name) and given == name
