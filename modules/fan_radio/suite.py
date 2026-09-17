"""Fan Radio's finished work, filed as a Smart 1 Suite opportunity.

The Radio Ad Creator has been able to do this since it was ported: approve a
spot, press once, and the client's opportunity in the Suite carries the audio
and the script. Fan Radio ended at the share link and an optional
`FAN_RADIO_NOTIFY_URL` ping, so a finished football spot reached the CRM where
the calls, the texts and the pipeline live only if somebody pasted a link into
it by hand. That was the one difference between the two builders nobody had
recorded a decision about — the module README called it open rather than
deliberate, and this closes it.

## Through the Hub's one contact write path, not a third webhook

The Radio Ad Creator posts a payload of its own to
`GHL_OPPORTUNITY_WEBHOOK_URL`. That works, and it is deliberately **not** what
this copies. `modules/commercial_builder/routes/suite.py` already faced this
exact choice and wrote down the answer: `hub/ghl_contacts.py` is "one token,
one location id and one contact write path for the whole Hub", and a second raw
webhook there "would be a third answer to *how do we reach GoHighLevel*". A
third tool posting its own payload would be the fourth.

So this composes a note and lets `hub/suite_opportunity.push_proposal` do the
talking. That function already finds the contact, refuses to invent one, and
answers in three shapes rather than two — which is what makes "no contact in
the Suite for this client" a thing a rep can fix rather than a failure.

## What may be pushed

**A spot the client approved, and nothing else.** This is where Fan Radio
differs from the Radio Ad Creator and is the better of the two: that tool
gates on a *staff* `approve-spot` press, and Fan Radio has the customer's own
decision — `hub/radio_share.record_decision` writes `status`, `decided_at` and
`decided_by` when the client presses **Approve this spot** on the share page.
A record of what the client approved is strictly stronger than a record of
what a rep thought was ready, so no staff approval is added here; adding one
would be a second, weaker gate in front of a stronger one.

**The finished mix where a bed was chosen.** `public_view()` falls back to the
raw read so a client has something to listen to while a bed is being composed,
which is right for a review page and wrong for delivery: a spot with a bed
selected and no saved mix is unfinished, and sending the naked read as the
final file delivers a commercial nobody made. Held back and named.

**Audio that is actually somewhere a salesperson can open.** A Cloudinary URL
is already absolute. A local-disk render is stored relative (`audio/x.mp3`) and
served under this module's mount, so it is absolutized against
`PUBLIC_BASE_URL` — and where that is unset there is no absolute form to make,
so the spot is held rather than delivered with a URL that resolves to nothing.
The Commercial Builder's "a cut that is actually somewhere" rule, one medium
over.

**Not a spec spot.** An opportunity for a business that has not asked for one
pollutes the pipeline — the rule the Radio Ad Creator's push already states.

## Pushing twice must not open a second opportunity

The `opportunity_id` is kept on the project and handed back on every later
press, so a second press revises rather than opening a second opportunity for
one job on one client's pipeline. Without it a rep who pushed on Tuesday and
again on Thursday leaves two, with nothing on either saying which is current.

## A refusal is recorded as a refusal

"Nobody has pushed this", "we pushed it and Suite refused" and "Suite has it"
are three states, and the middle one is the one somebody has to act on.
Collapsing it into the first makes the button read as never pressed.
"""

from __future__ import annotations

import os

from . import catalog

# A spot held back, and why, in the words a rep can act on. Keyed so the page
# can draw them beside the spot rather than as one sentence about the project.
HELD_STALE = "The script changed after this was recorded, so the read is of " \
             "wording nobody approved. Re-record it."
HELD_UNMIXED = "A music bed is selected but the mix has not been saved, so " \
               "the only audio is the naked read. Render the mix first."
HELD_NO_AUDIO = "Nothing has been recorded for this spot yet."
HELD_NO_URL = "This render is on the Hub's own disk and PUBLIC_BASE_URL is " \
              "not set, so there is no address a salesperson could open. " \
              "Set PUBLIC_BASE_URL, or re-record with Cloudinary configured."


def public_base() -> str:
    """The base a relative render is absolutized against, or empty.

    Read the same way `hub/radio_share.share_url()` reads it, because a client
    who can open the share link can open the audio on it — two readings of
    "what is this Hub's public address" is how one of them comes to be right.
    """
    return (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")


def absolute_audio(url: str, mount: str) -> str:
    """An address a salesperson can open, or empty where there is none.

    Empty rather than the relative path: a CRM note carrying `audio/x.mp3`
    names nothing, and a note naming nothing is worse than a spot held back
    with a reason.
    """
    url = str(url or "").strip()
    if not url:
        return ""
    if url.lower().startswith(("http://", "https://")):
        return url
    base = public_base()
    if not base:
        return ""
    # The mount goes on unless it is already there. `store.store_audio()`
    # returns `audio/x.mp3` for a local render and that is served under this
    # module's prefix, so a root-absolute `/audio/x.mp3` -- which nothing
    # writes today -- would 404 if it were taken as already correct. Prefixing
    # a path that already carries the mount would break it the other way.
    mount = mount.rstrip("/")
    path = url if url.startswith("/") else "/" + url
    if not path.startswith(mount + "/"):
        path = mount + path
    return base + path


def final_audio(spot: dict, mount: str) -> tuple[str, float, bool, str]:
    """The one file this spot delivers: (url, seconds, is_mix, held_reason).

    The mix wins where there is one. Where a bed was chosen and no mix was
    saved, nothing is delivered -- that is the case `public_view()`
    deliberately falls back on for review and must not fall back on here.
    """
    mix = spot.get("mix") or {}
    has_bed = bool((spot.get("bed") or {}).get("audio_url"))
    if spot.get("audio_stale"):
        return "", 0.0, False, HELD_STALE
    if mix.get("audio_url"):
        url = absolute_audio(mix["audio_url"], mount)
        return (url, float(mix.get("seconds") or 0), True,
                "" if url else HELD_NO_URL)
    if has_bed:
        return "", 0.0, False, HELD_UNMIXED
    if not spot.get("audio_url"):
        return "", 0.0, False, HELD_NO_AUDIO
    url = absolute_audio(spot["audio_url"], mount)
    return (url, float(spot.get("audio_seconds") or 0), False,
            "" if url else HELD_NO_URL)


def units(project: dict, mount: str) -> dict:
    """What this project would deliver, and what it is holding back.

    Both halves, always. A screen that lists only what is ready cannot answer
    "why is this spot not on the list", which is the question a rep actually
    has -- the four-answer rule `commercial_builder/routes/suite.py` states.
    """
    ready, held = [], []
    for spot in project.get("spots") or []:
        if spot.get("hidden") or not spot.get("script"):
            continue
        if (spot.get("status") or "") != "approved":
            continue
        url, seconds, is_mix, why = final_audio(spot, mount)
        dp = catalog.daypart(spot.get("daypart") or "")
        row = {"id": spot.get("id"),
               "daypart": spot.get("daypart") or "",
               "daypart_label": dp["label"],
               "length_label": catalog.budget(spot.get("seconds") or 30)["label"],
               "seconds": spot.get("seconds"),
               "outcome": spot.get("outcome") or "neutral",
               "script": spot.get("script") or "",
               "approved_by": spot.get("decided_by") or "",
               "approved_at": spot.get("decided_at") or "",
               "audio_url": url, "audio_seconds": seconds, "is_mix": is_mix}
        if why:
            held.append(dict(row, why=why))
        else:
            ready.append(row)
    order = {"pregame": 0, "gameday": 1, "postgame": 2}
    for rows in (ready, held):
        rows.sort(key=lambda r: (order.get(r["daypart"], 9), r["seconds"] or 0))
    return {"ready": ready, "held": held}


def blockers(project: dict, rows: dict) -> list:
    """Why this project cannot be pushed at all, if it cannot.

    Project-level refusals, separate from a spot held back: a spec spot and an
    unapproved project are answers about the job rather than about one read.
    """
    out = []
    if project.get("scope") != "client" or not (project.get("client") or "").strip():
        out.append("This is a spec spot. Attach it to a client before filing "
                   "it in the Suite — an opportunity for a business that has "
                   "not asked for one pollutes the pipeline.")
    if not rows["ready"]:
        if rows["held"]:
            out.append("Every approved spot is held back — see the reasons "
                       "beside each one.")
        else:
            out.append("The client has not approved a spot yet. Send the share "
                       "link and let them press Approve; what goes to the "
                       "Suite is what the client signed off, not what we "
                       "thought was ready.")
    return out


def note_lines(project: dict, ready: list) -> list:
    """The note on the opportunity, as a salesperson reads it.

    Named lines rather than a JSON blob: this lands on the one record a
    salesperson opens, and `hub/proposal_plan.py` is the note about a plan that
    comes back as JSON being a plan nobody reads.
    """
    company = project.get("company") or project.get("client") or ""
    lines = [f"Fan Radio spots delivered: {company}".strip(),
             f"{len(ready)} spot{'' if len(ready) == 1 else 's'}, "
             f"approved by the client on the share page."]
    voice = (project.get("voice") or {}).get("name") or ""
    if voice:
        lines.append(f"Voice: {voice}")
    for row in ready:
        lines.append("")
        lines.append(f"{row['daypart_label']} {row['length_label']}"
                     + (" (if it went well)" if row["outcome"] == "win" else
                        " (if it didn't)" if row["outcome"] == "loss" else ""))
        if row["approved_by"]:
            lines.append(f"Approved by {row['approved_by']}"
                         + (f" on {row['approved_at'][:10]}"
                            if row["approved_at"] else ""))
        lines.append(f"{'With music' if row['is_mix'] else 'Straight read'}: "
                     f"{row['audio_url']}")
        lines.append(row["script"])
    return lines


def record(project: dict) -> dict:
    """What the Suite holds for this project, as stored on its own row."""
    return dict(project.get("suite") or {})
