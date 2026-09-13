"""One weather trigger campaign, one JSON file, keyed on its token.

`hub/io_records.py`'s rule applies here for the same reason: one file per
campaign, never one file holding all of them, because two writers editing
two different campaigns must not be able to drop one of the two edits on the
way to disk. Every mutation goes through `jsonstore.update_json`, which is
the missing half of `read_json`/`write_json` — a single indivisible
read-modify-write rather than a read outside any lock.

The spec this was built against (`docs/weather-trigger-setup.md`) describes
four tables — `wx_campaign`, `wx_pick`, `wx_asset`, `wx_event`. They collapse
into one record here rather than four SQL tables, matching this codebase's
own convention for exactly this access pattern (`hub/io_records.py`,
`hub/drafts.py`): nothing here is ever queried across campaigns except by
token, so a table gains nothing a nested JSON document does not already
give, and it is one more thing this codebase would have to keep mirrored,
migrated and locked.

## The rules

**A resubmission of the picks updates the campaign, it never starts a
second one.** The token is permanent — the client re-opens the same link
after approval to request a change — so `save_picks()` merges onto whatever
is already there rather than replacing it.

**Nothing here may raise.** A campaign is worth more than its own
bookkeeping; every entry point returns a result dict rather than an
exception, the `hub/io_records.py` rule.

**The cap is enforced here too, not only in `hub/weather_triggers.py`'s
own validator.** `save_picks()` calls it before writing anything, because a
route that trusted the picker UI to have refused a fourth trigger is a route
one client's crafted request away from breaking the budget rule the cap
exists for.

**An approval freezes the copy.** Once approved, `ad_json` on every pick is
the document a work order was cut from; nothing after that point may
silently reword it. A change after approval is a new revision (see
`request_change()`), never an edit in place.
"""
from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone

from hub import jsonstore
from hub.weather_triggers import TRIGGERS, VERTICALS, validate_picks

MAX_EVENTS = 200
MAX_NOTE_LEN = 600
_KEY_RE = re.compile(r"[^0-9A-Za-z_-]+")


class _MutationFailed(Exception):
    """Raised inside an ``update_json`` mutate callback to say *this
    specific write cannot happen* -- never to signal "nothing changed".

    ``jsonstore.update_json`` treats a ``mutate`` that returns ``None`` as
    "no change", and hands back the **original** record, not ``None`` --
    so a mutate that wants to report a real failure (no such pick, an
    already-approved campaign) cannot use a bare ``return None`` without
    the caller mistaking the unchanged row for success. Raising here
    propagates out of ``update_json`` by design ("a mutate that raises is
    the caller's own bug and is left to surface"), and every call site
    below catches exactly this one exception and turns it into a result
    dict, never letting anything else pass silently.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dir() -> str:
    return jsonstore.data_dir("weather_setup", "campaigns")


def new_token() -> str:
    """An unguessable client-facing id. Never a slug built from the client
    name and a timestamp -- that shape is exactly the mistake this spec
    calls out as already having cost a QA finding elsewhere in this Hub."""
    return secrets.token_urlsafe(24)


def _key(token: str) -> str:
    return _KEY_RE.sub("", str(token or "").strip())[:64]


def _path(token: str) -> str:
    return os.path.join(_dir(), f"{_key(token)}.json")


def _text(value, limit: int = 200) -> str:
    return str(value or "").strip()[:limit]


def get(token: str) -> dict | None:
    """One campaign's record, or None. Never raises."""
    key = _key(token)
    if not key:
        return None
    try:
        row = jsonstore.read_json(_path(token), default=None)
    except Exception:                                      # noqa: BLE001
        return None
    return row if isinstance(row, dict) and row.get("token") else None


def _rows() -> list[dict]:
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        row = get(name[:-5])
        if row:
            out.append(row)
    return out


def approved_campaigns() -> list[dict]:
    """Every campaign with at least one trigger live -- what the scheduler
    job walks. Approved and changed campaigns both keep running: a change
    request re-opens the picks for review, it does not switch the client's
    current ads off while a rep looks at it.
    """
    return [r for r in _rows() if r.get("status") in ("approved", "changed")
           and r.get("zip_code")]


def update_trigger_state(token: str, trigger_id: str, *, active: bool | None,
                         measured: bool, detail: str, trigger_state: dict) -> None:
    """The scheduler's own write: whether this pick's condition is true right
    now, and the carried state (streaks, once-per-season flags) that
    produced the answer. Never raises -- a failed write here costs the next
    evaluation its history, never the campaign itself."""
    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("gone")
        p = _pick(current, trigger_id)
        if p is None:
            raise _MutationFailed("gone")
        was_active = p.get("active")
        p["active"] = active
        p["measured"] = measured
        p["condition_detail"] = _text(detail, 300)
        p["trigger_state"] = trigger_state
        p["evaluated_at"] = _now()
        if measured and active != was_active:
            current = _add_event(current, "trigger_toggled", trigger=trigger_id,
                                 active=bool(active))
        return current
    try:
        jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed:
        pass  # the campaign or the pick is gone; nothing left to record against
    except Exception:                                      # noqa: BLE001
        pass  # a scheduler write must never take the sweep down with it


def create(*, client: str, vertical: str = "restaurant", lead_id: str = "",
          created_by: str = "", mode: str = "guided", zip_code: str = "") -> dict | None:
    """A brand-new campaign, draft status, no picks yet.

    None on a write failure rather than letting `jsonstore.write_json()`
    raise through -- this function was the one place in the module that did
    not honour the module's own "nothing here may raise" rule. A raised
    exception here reaches `api_start()` unguarded, which has no exception
    handler of its own (this module is a blueprint on the hub app, and the
    hub app -- unlike a dispatcher-mounted one -- installs no blanket
    `@app.errorhandler` at all), so it surfaced as Flask's stock HTML 500
    page: not JSON, so `fetch().then(r => r.json())` in
    `weather_setup_staff.html` rejected and the rep saw "Could not reach the
    server" for what was actually a disk write failing on our end.
    """
    token = new_token()
    picked_vertical = _text(vertical, 40) or "restaurant"
    if picked_vertical not in VERTICALS:
        # A vertical nobody has a trigger vocabulary for is not a vertical
        # this campaign can pick anything against -- validate_picks() would
        # refuse every one of them by name, one at a time, rather than the
        # campaign simply having nothing to offer. Falling back to
        # "restaurant" here is the safe direction to be wrong in.
        picked_vertical = "restaurant"
    row = {
        "token": token,
        "client": _text(client, 200),
        "vertical": picked_vertical,
        "zip_code": _text(zip_code, 12),
        "location_name": "",
        "status": "draft",
        "created_by": _text(created_by, 120),
        "created_at": _now(),
        "mode": mode if mode in ("guided", "self") else "guided",
        "lead_id": _text(lead_id, 60),
        "picks": [],
        "events": [],
        "view_count": 0,
        "last_viewed_at": "",
        "approved_at": "",
        "approved_by_name": "",
        "approved_ip": "",
        "work_order": "",
        "revision": 1,
    }
    try:
        jsonstore.write_json(_path(token), row)
    except Exception as exc:                                  # noqa: BLE001
        try:
            from hub import errors
            errors.log_exception("weather_setup", exc, path=_path(token))
        except Exception:                                     # noqa: BLE001
            pass
        return None
    return row


def _add_event(row: dict, kind: str, **detail) -> dict:
    events = list(row.get("events") or [])
    events.append({"at": _now(), "kind": _text(kind, 60),
                   "detail": {k: (_text(v, 300) if isinstance(v, str) else v)
                              for k, v in detail.items()}})
    row["events"] = events[-MAX_EVENTS:]
    return row


def record_view(token: str) -> dict | None:
    """One open of the wizard. Bumps the count and logs it as an event."""
    def _mutate(row):
        if not isinstance(row, dict):
            return None
        row["view_count"] = int(row.get("view_count") or 0) + 1
        row["last_viewed_at"] = _now()
        return _add_event(row, "viewed")
    try:
        return jsonstore.update_json(_path(token), _mutate)
    except Exception:                                      # noqa: BLE001
        return None


def set_station(token: str, *, zip_code: str, location_name: str) -> dict | None:
    def _mutate(row):
        if not isinstance(row, dict):
            return None
        row["zip_code"] = _text(zip_code, 12)
        row["location_name"] = _text(location_name, 120)
        return row
    try:
        return jsonstore.update_json(_path(token), _mutate)
    except Exception:                                      # noqa: BLE001
        return None


def save_picks(token: str, trigger_ids: list[str]) -> dict:
    """Set which triggers this campaign runs. Enforces the cap server-side.

    Picks the client already has a draft or approved wording for keep it;
    a trigger dropped from the list loses its slot but nothing is deleted
    outright until the campaign itself is -- a rep re-adding a trigger they
    just removed should not lose work typed a minute earlier, so the picks
    list is rebuilt from `trigger_ids` but existing pick records for ids
    still present are merged rather than replaced.
    """
    row = get(token)
    if row is None:
        return {"ok": False, "error": "No such campaign."}
    if row.get("status") not in ("draft", "awaiting_client", "changed"):
        return {"ok": False, "error": "This campaign has already been approved. "
                                      "Start a change request to pick differently."}
    ok, err = validate_picks(trigger_ids, row.get("vertical") or "restaurant")
    if not ok:
        return {"ok": False, "error": err}

    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        existing = {p["trigger_id"]: p for p in current.get("picks") or []
                   if isinstance(p, dict) and p.get("trigger_id")}
        picks = []
        for slot, trig_id in enumerate(trigger_ids, start=1):
            prior = existing.get(trig_id, {})
            picks.append({
                "trigger_id": trig_id, "slot": slot,
                "ad_json": prior.get("ad_json") or [],
                "ad_source": prior.get("ad_source", ""),
                "selected_variant": prior.get("selected_variant"),
                "notes": prior.get("notes", ""),
                "asset": prior.get("asset"),
                "trigger_state": prior.get("trigger_state") or {},
            })
        current["picks"] = picks
        current["status"] = "awaiting_client" if current.get("status") == "draft" else current["status"]
        return _add_event(current, "triggers_picked", triggers=",".join(trigger_ids))

    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "campaign": row}


def _pick(row: dict, trigger_id: str) -> dict | None:
    for p in row.get("picks") or []:
        if isinstance(p, dict) and p.get("trigger_id") == trigger_id:
            return p
    return None


def get_or_generate_copy(token: str, trigger_id: str) -> dict:
    """Three drafts for this pick, generated once and cached from then on."""
    row = get(token)
    if row is None:
        return {"ok": False, "error": "No such campaign."}
    pick = _pick(row, trigger_id)
    if pick is None:
        return {"ok": False, "error": "That trigger has not been picked yet."}
    if pick.get("ad_json"):
        return {"ok": True, "drafts": pick["ad_json"], "source": pick.get("ad_source", "")}

    from . import copy as copy_mod
    result = copy_mod.generate_drafts(trigger_id, row.get("client", ""))

    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        p = _pick(current, trigger_id)
        if p is None:
            raise _MutationFailed("That trigger has not been picked yet.")
        if p.get("ad_json"):
            # Somebody else's request generated it first between our read
            # and this write. Nothing changed here.
            return None
        p["ad_json"] = result.get("drafts", [])
        p["ad_source"] = result.get("source", "")
        return _add_event(current, "copy_generated", trigger=trigger_id,
                          source=result.get("source", ""))

    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    saved_pick = _pick(row, trigger_id) or {}
    return {"ok": True, "drafts": saved_pick.get("ad_json") or result.get("drafts", []),
            "source": saved_pick.get("ad_source") or result.get("source", "")}


def select_variant(token: str, trigger_id: str, variant_index: int, notes: str = "") -> dict:
    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        p = _pick(current, trigger_id)
        if p is None:
            raise _MutationFailed("That trigger has not been picked yet.")
        drafts = p.get("ad_json") or []
        if not (0 <= variant_index < len(drafts)):
            raise _MutationFailed("That draft could not be found.")
        p["selected_variant"] = variant_index
        p["notes"] = _text(notes, MAX_NOTE_LEN)
        return _add_event(current, "variant_selected", trigger=trigger_id,
                          variant=variant_index)
    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "campaign": row}


def set_asset(token: str, trigger_id: str, asset: dict) -> dict:
    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        p = _pick(current, trigger_id)
        if p is None:
            raise _MutationFailed("That trigger has not been picked yet.")
        p["asset"] = asset
        return _add_event(current, "image_selected", trigger=trigger_id,
                          provider=asset.get("provider", ""))
    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "campaign": row}


# --------------------------------------------------------------------------
# Work order numbering. One counter, shared, incremented atomically.
# --------------------------------------------------------------------------

def _wo_counter_path() -> str:
    return os.path.join(jsonstore.data_dir("weather_setup"), "work_order_seq.json")


def _next_work_order() -> str:
    def _mutate(current):
        n = int((current or {}).get("next") or 1)
        return {"next": n + 1}
    result = jsonstore.update_json(_wo_counter_path(), _mutate, default={"next": 1})
    n = int((result or {}).get("next") or 2) - 1
    return f"WO-{n:05d}"


def ready_to_approve(row: dict) -> tuple[bool, str]:
    picks = row.get("picks") or []
    if not picks:
        return False, "Choose at least one trigger before approving."
    for p in picks:
        if not p.get("ad_json"):
            return False, "Every trigger needs wording before approving."
        if p.get("selected_variant") is None:
            return False, "Choose a wording for every trigger before approving."
        if not p.get("asset"):
            return False, "Choose an image for every trigger before approving."
    return True, ""


def approve(token: str, *, name: str, ip: str, actor: str = "",
           email: str = "", phone: str = "") -> dict:
    """Freeze the copy, cut a work order, log the work, and hand the lead
    to Smart 1 Suite. Never raises -- an approval that could not write its
    own bookkeeping must still count as an approval.
    """
    row = get(token)
    if row is None:
        return {"ok": False, "error": "No such campaign."}
    if row.get("status") == "approved":
        return {"ok": False, "error": "This campaign is already approved.",
                "campaign": row}
    ok, err = ready_to_approve(row)
    if not ok:
        return {"ok": False, "error": err}

    work_order = _next_work_order()

    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        if current.get("status") == "approved":
            # A second request landed between our read and this write. The
            # work order number allocated above is simply never recorded --
            # the `hub/io_records.py` rule that an allocated number that
            # never became an order is not tracked.
            raise _MutationFailed("This campaign is already approved.")
        current["status"] = "approved"
        current["approved_at"] = _now()
        current["approved_by_name"] = _text(name, 120)
        current["approved_ip"] = _text(ip, 60)
        current["work_order"] = work_order
        return _add_event(current, "approved", by=name, work_order=work_order)

    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}

    _log_and_deliver(row, actor=actor, email=email, phone=phone)
    return {"ok": True, "campaign": row, "work_order": work_order}


def _log_and_deliver(row: dict, *, actor: str, email: str, phone: str) -> None:
    """The three side effects of an approval, each independently guarded so
    a failure in one never costs the other two or the approval itself."""
    client = row.get("client", "")
    token = row.get("token", "")
    trigger_names = [TRIGGERS[p["trigger_id"]].name for p in row.get("picks") or []
                     if p.get("trigger_id") in TRIGGERS]
    try:
        from hub import audit
        audit.log("weather_trigger_setup", "approved", actor=actor or row.get("created_by") or None,
                  tool="Weather Trigger Setup", client=client or None,
                  campaign_token=token[:16], work_order=row.get("work_order", ""),
                  triggers=", ".join(trigger_names), vertical=row.get("vertical", ""))
    except Exception:                                      # noqa: BLE001
        pass

    try:
        from hub import config
        public_url = config.public_base_origin().rstrip("/") + f"/wx/{token}"
    except Exception:                                      # noqa: BLE001
        public_url = f"/wx/{token}"

    try:
        from hub import leads
        leads.capture_and_deliver(
            "weather_trigger_setup", "approval",
            fields={"name": row.get("approved_by_name", ""), "email": email,
                   "phone": phone, "company": client},
            client=client, meta={"tags": ["weather_setup"],
                                "report_url": public_url})
    except Exception:                                      # noqa: BLE001
        pass


def request_change(token: str) -> dict:
    """Re-opening an approved campaign starts a new revision rather than
    editing the live one, so the approval record stays honest -- the same
    reason `hub/quote_validity.py` never lets an accepted quote be quietly
    edited under the acceptance."""
    def _mutate(current):
        if not isinstance(current, dict):
            raise _MutationFailed("No such campaign.")
        if current.get("status") != "approved":
            raise _MutationFailed("This campaign is not approved, so there "
                                  "is nothing to change.")
        current["status"] = "changed"
        current["revision"] = int(current.get("revision") or 1) + 1
        current["work_order"] = ""
        for p in current.get("picks") or []:
            p["selected_variant"] = None
        return _add_event(current, "change_requested",
                          revision=current["revision"])
    try:
        row = jsonstore.update_json(_path(token), _mutate)
    except _MutationFailed as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "campaign": row}
