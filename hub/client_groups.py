"""Two client records, one company — the parent/member overlay behind the
Client 360 **Group** button.

## The thing this models

National Background Check and Fast Fingerprints are one business. Every
insertion order, every invoice and every proposal is filed under National
Background Check, and Fast Fingerprints exists in Knack as its own client
record because that is the name on the campaign. Open Fast Fingerprints in
Client 360 and it reads as a client with no products, no invoices and no
history — a confidently wrong answer of exactly the kind this codebase treats
as worse than an error.

Grouping them makes one record out of the two: products and IOs, creative,
notes, work, proposals and invoices are read across every member of the group,
whichever member you opened.

## What is stored, and what is not

An **overlay**, the same shape as `hub/client_urls.py`'s discovered URLs.
Knack owns the client records and this Hub does not write to them, so nothing
here renames, merges or deletes anything: removing the group leaves both
records exactly as they were. Written through `hub/jsonstore.py`, so the only
copy is not a file on a disk that is outside the backup.

Each member is stored as **the name and URL it was grouped under, never the
derived key**. `hub/client_key.py` says why at length: a stored key is a second
copy of the answer, and it starts drifting the moment somebody renames a client
in Knack. The key is derived on read here too.

## The rules, each of which is a way to be wrong quietly

* **One client is in at most one group.** Two groups claiming the same client
  makes "whose bill is this on?" unanswerable, and an aggregate built from an
  arbitrary pick of the two is a number nobody can reproduce. `add_member()`
  refuses and names the group that already holds them.

* **Membership reads the same from either end.** Open the member and you see
  the parent's records; open the parent and you see the member's. A group that
  is only visible from the parent means half the staff see the relationship and
  half do not, which is the situation grouping exists to end.

* **Never match a member on a substring.** Members resolve by canonical domain
  first and exact normalised name second, through `client_key`. "Riverside
  HVAC" must not pick up "Riverside HVAC Supply" — attributing one company's
  insertion orders to another is the worst outcome available here.

* **Every aggregated row says which member it came from.** The group is a
  billing relationship, not a rename: an IO that is Fast Fingerprints' work
  must still read as Fast Fingerprints' work on the parent's record. Callers
  get `member` on every row they merged in, and Client 360 prints it.

* **Deduplicate before totalling.** A product row filed under the organisation
  name matches the parent *and* the member, and counting it twice inflates the
  "Active billing" pill — a wrong number presented as confidently as a right
  one. `merge_rows()` is the one place that decides.

* **A member that could not be read is named, not dropped.** "This member has
  no invoices" and "we never managed to look this member up" are different
  claims, and only one of them is a reason to stop chasing a bill.
"""
from __future__ import annotations

import copy
import os
import threading
import uuid
from datetime import datetime, timezone

from hub import jsonstore
from hub.client_context import canonical_domain
from hub.client_key import client_key, normalise_name

_lock = threading.Lock()

# Grouping is a deliberate, rare act — a handful of parent companies across the
# whole book — so one file holds the lot rather than one per client. It is
# small, it is read on every Client 360 load, and a single file means "show me
# every group we have" is a read rather than a directory walk.
_FILE = "groups.json"


def _path() -> str:
    return os.path.join(jsonstore.data_dir("client_groups"), _FILE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# One Client 360 load resolves the roster once per group in the result and
# again per card route, and jsonstore.read_json falls through to a database
# query every time the file is *absent* — which is the state on a deployment
# where nobody has grouped anything yet. That is a dozen round-trips added to
# a page load for a feature not in use. Held for a few seconds instead: a
# write busts it locally, and the other gunicorn worker is at most _TTL behind,
# which is well under the time it takes to read a reloaded page.
_TTL_SECONDS = 5.0
_cache: dict = {"at": 0.0, "value": None}


def _read() -> dict:
    import time
    with _lock:
        cached = _cache["value"]
        if cached is not None and time.time() - _cache["at"] < _TTL_SECONDS:
            return copy.deepcopy(cached)
    data = jsonstore.read_json(_path(), default={"groups": []})
    if not isinstance(data, dict):
        data = {"groups": []}
    if not isinstance(data.get("groups"), list):
        data["groups"] = []
    with _lock:
        _cache["value"] = copy.deepcopy(data)
        _cache["at"] = time.time()
    return data


def _write(data: dict) -> None:
    import time
    with _lock:
        jsonstore.write_json(_path(), data, indent=1)
        _cache["value"] = copy.deepcopy(data)
        _cache["at"] = time.time()


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def _entry(name: str, url: str = "") -> dict:
    """One member record: what a human typed, plus nothing derived."""
    return {"name": str(name or "").strip(),
            "url": str(url or "").strip()}


def _keys(name: str, url: str = "") -> set[str]:
    """Every key this client can legitimately be recognised by.

    A domain is an identifier and a name is not, so both are offered when both
    are known: a member grouped before its URL was on file must still match
    once the URL arrives, and one grouped by URL must still match a record that
    only carries the name. Neither is a substring test.
    """
    out = set()
    dom = canonical_domain(url) or canonical_domain(name)
    if dom:
        out.add(f"d:{dom}")
    if normalise_name(name):
        out.add(client_key(name=name))
    return {k for k in out if k}


def same_record(a_name: str, a_url: str, b_name: str, b_url: str) -> bool:
    """Are these two (name, url) pairs the same client record?

    Exact only — a shared canonical domain, or identical normalised names.
    `client_key.resolve()` refuses a substring for the reason given there and
    so does this.
    """
    return bool(_keys(a_name, a_url) & _keys(b_name, b_url))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def groups() -> list[dict]:
    """Every group on file, parent first inside each."""
    out = []
    for g in _read()["groups"]:
        if isinstance(g, dict) and isinstance(g.get("parent"), dict):
            out.append(g)
    out.sort(key=lambda g: str((g.get("parent") or {}).get("name") or "").lower())
    return out


def _members(group: dict) -> list[dict]:
    parent = dict(group.get("parent") or {})
    parent["role"] = "parent"
    rows = [parent]
    for m in group.get("members") or []:
        if isinstance(m, dict) and str(m.get("name") or "").strip():
            row = dict(m)
            row["role"] = "member"
            rows.append(row)
    return rows


def group_for(name: str, url: str = "") -> dict | None:
    """The group this client belongs to, from either end, or None."""
    if not str(name or "").strip() and not str(url or "").strip():
        return None
    for g in groups():
        for m in _members(g):
            if same_record(name, url, m.get("name", ""), m.get("url", "")):
                return g
    return None


def roster(name: str, url: str = "") -> dict:
    """What Client 360 needs to draw the group, for one client.

    Always answers. An ungrouped client gets `grouped: False` and a `names`
    list holding only itself, so every caller can aggregate over `names`
    unconditionally rather than branching.
    """
    asked = str(name or "").strip()
    g = group_for(asked, url)
    if not g:
        return {"grouped": False, "client": asked, "is_parent": False,
                "parent": None, "members": [], "names": [asked] if asked else [],
                "others": [], "id": ""}

    members = _members(g)
    parent = next((m for m in members if m["role"] == "parent"), members[0])
    is_parent = same_record(asked, url, parent.get("name", ""), parent.get("url", ""))
    mine = [m for m in members
            if same_record(asked, url, m.get("name", ""), m.get("url", ""))]
    others = [m for m in members
              if not same_record(asked, url, m.get("name", ""), m.get("url", ""))]
    # The client asked for stays first: it is the record on screen, and a list
    # that reorders under you is how somebody reads the wrong row.
    ordered = mine + others
    return {
        "grouped": True,
        "id": str(g.get("id") or ""),
        "client": asked,
        "is_parent": is_parent,
        "parent": parent,
        "members": ordered,
        "others": others,
        "names": [str(m.get("name") or "") for m in ordered if m.get("name")],
        "created": g.get("created", ""),
        "created_by": g.get("created_by", ""),
        "note": (f"Records from {len(others)} other client "
                 f"record{'s' if len(others) != 1 else ''} are shown on this "
                 f"page. Each row says which one it came from."),
    }


def member_names(name: str, url: str = "") -> list[str]:
    """Every client name to read a grouped record across, this one first."""
    return roster(name, url)["names"]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def add_member(parent_name: str, member_name: str, *, parent_url: str = "",
               member_url: str = "", actor: str = "") -> dict:
    """Attach `member` to `parent`, creating the group if there isn't one.

    Returns `{"ok": True, "roster": ...}` or `{"error": ...}`. Every refusal
    says which client is in the way and why, because "it didn't work" on a
    button that silently changes what a billing page totals is unusable.
    """
    parent_name = str(parent_name or "").strip()
    member_name = str(member_name or "").strip()
    if not parent_name or not member_name:
        return {"error": "Both a parent company and a company to attach are required."}
    if same_record(parent_name, parent_url, member_name, member_url):
        return {"error": f"“{member_name}” is the same client record as "
                         f"“{parent_name}”. Nothing to group."}

    data = _read()
    member_group = group_for(member_name, member_url)
    parent_group = group_for(parent_name, parent_url)

    same_group = (member_group is not None and parent_group is not None
                  and str(member_group.get("id")) == str(parent_group.get("id")))
    if member_group and parent_group and not same_group:
        pn = (member_group.get("parent") or {}).get("name", "")
        return {"error": f"“{member_name}” is already grouped under “{pn}”. "
                         "A client can only be in one group — ungroup it there "
                         "first, so there is never a second answer to which "
                         "record their billing sits on."}
    if member_group and not parent_group:
        pn = (member_group.get("parent") or {}).get("name", "")
        return {"error": f"“{member_name}” is already grouped under “{pn}”. "
                         "Ungroup it there first."}

    group = parent_group if parent_group is not None else member_group
    if group is None:
        group = {
            "id": uuid.uuid4().hex[:12],
            "parent": _entry(parent_name, parent_url),
            "members": [],
            "created": _now(),
            "created_by": str(actor or "")[:60],
        }
        data["groups"].append(group)
    else:
        # Re-read the stored dict so the mutation lands on the file's copy.
        group = next((g for g in data["groups"]
                      if str(g.get("id")) == str(group.get("id"))), group)
        group.setdefault("members", [])

    for m in _members(group):
        if same_record(member_name, member_url, m.get("name", ""), m.get("url", "")):
            return {"ok": True, "already": True,
                    "roster": roster(parent_name, parent_url)}

    entry = _entry(member_name, member_url)
    entry["added"] = _now()
    entry["added_by"] = str(actor or "")[:60]
    group["members"].append(entry)
    _write(data)
    return {"ok": True, "roster": roster(parent_name, parent_url)}


def remove_member(name: str, url: str = "", *, actor: str = "") -> dict:
    """Detach one client from its group.

    Removing the **parent** dissolves the group rather than promoting a member:
    which of three siblings holds the bill is not a question this file can
    answer, and guessing it would move every invoice on the page.
    """
    name = str(name or "").strip()
    if not name:
        return {"error": "A client is required."}
    data = _read()
    for i, g in enumerate(list(data["groups"])):
        if not isinstance(g, dict):
            continue
        parent = g.get("parent") or {}
        if same_record(name, url, parent.get("name", ""), parent.get("url", "")):
            data["groups"].pop(i)
            _write(data)
            return {"ok": True, "dissolved": True,
                    "note": f"The group under “{parent.get('name')}” was "
                            "dissolved. Every client record is exactly as it "
                            "was — nothing was written to Knack."}
        kept = [m for m in (g.get("members") or [])
                if not (isinstance(m, dict)
                        and same_record(name, url, m.get("name", ""), m.get("url", "")))]
        if len(kept) != len(g.get("members") or []):
            g["members"] = kept
            if not kept:
                data["groups"].pop(i)
                _write(data)
                return {"ok": True, "dissolved": True,
                        "note": "That was the last member, so the group was "
                                "removed. Nothing was written to Knack."}
            _write(data)
            return {"ok": True, "roster": roster(str(parent.get("name") or ""),
                                                 str(parent.get("url") or ""))}
    return {"error": f"“{name}” is not in a group."}


# ---------------------------------------------------------------------------
# Aggregation helpers — one place that decides what a duplicate is
# ---------------------------------------------------------------------------

def merge_rows(rows: list[dict], key, member: str = "", into: list | None = None,
               seen: set | None = None) -> tuple[list, set]:
    """Append `rows` to `into`, skipping ones already there, tagging the source.

    `key` is a callable returning the identity of a row. A product filed under
    the organisation name is found under the parent *and* the member; merged
    twice it doubles the billing total on the header, which is a wrong number
    that looks exactly like a right one.

    The first sighting wins and keeps whichever `member` tag it arrived with,
    so a row that genuinely belongs to the record on screen is not relabelled
    as somebody else's.
    """
    out = [] if into is None else into
    have = set() if seen is None else seen
    if into is not None and seen is None:
        have = {key(r) for r in out}
    for r in rows:
        if not isinstance(r, dict):
            continue
        k = key(r)
        if k in have:
            continue
        have.add(k)
        row = dict(r)
        if member:
            row["member"] = member
        out.append(row)
    return out, have
