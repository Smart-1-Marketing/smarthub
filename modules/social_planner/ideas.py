"""The client-facing suggestion engine: ideas, swipes and what they steer.

## What this is for, and what it deliberately is not

`intake.py` is for a client who already knows what they want posted. This is
the other direction: filling a month for a client who does not, without asking
them to write a brief. One line per idea, Like or Pass, no explanation
required — deliberately lighter than the request form or the approval screen,
because the whole value is that it gets answered often.

A swipe steers the **mix**, never the content. `hub/social_content.tag_weight`
is one line of arithmetic a person can reproduce in their head from the two
counts printed beside it, and it decides only which kinds of post get offered
next. Nothing a client swipes on writes copy, and nothing here publishes.

## Two empty answers, and only one of them is this client's

"We could not ask the model" and "there is nothing worth suggesting" read
identically as an empty screen, and only the second means stop. So a failed
model call still returns a batch — built from the tag prompts and the client's
own preference notes, marked `source: "house"` — and the screen says which it
got. That is the rule `modules/image_picker/profile.py` arrived at for the
same failure: coming back empty from a question that was perfectly answerable
is worse than a plainer answer.

## Storage

`hub/jsonstore.py`, two files under the social data directory: the ideas
themselves, and one row per client carrying that client's preferences and tag
weights together. Together on purpose — they are read on the same screen and
written by the same two buttons, and a second file is a second thing to keep
in step. Rows carry the client's name and URL and never the derived key.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone

from hub import jsonstore, social_content
from hub.client_key import same_client

IDEAS_FILE = "ideas.json"
PREFS_FILE = "preferences.json"

MAX_IDEAS = 4000
MAX_PREFS = 800

_lock = threading.Lock()


def _path(name: str) -> str:
    return os.path.join(jsonstore.data_dir("social"), name)


def _read(name: str, key: str) -> list[dict]:
    blob = jsonstore.read_json(_path(name), default=None)
    if isinstance(blob, dict) and isinstance(blob.get(key), list):
        return [r for r in blob[key] if isinstance(r, dict)]
    return []


def _write(name: str, key: str, rows: list[dict], cap: int) -> bool:
    return jsonstore.write_json(_path(name), {key: rows[:cap]}, indent=1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return prefix + format(int(time.time() * 1000), "x")[-8:] + os.urandom(2).hex()


def _text(value, limit: int = 400) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _mine(row: dict, client: str, url: str = "") -> bool:
    return same_client(client, url, str(row.get("client") or ""),
                       str(row.get("client_url") or ""))


# =====================================================================
# Preferences and weights — one row per client
# =====================================================================
def _blank_prefs(client: str, url: str) -> dict:
    return {"client": _text(client, 200), "client_url": _text(url, 300),
            "topics_wanted": [], "topics_avoid": [], "tone": "",
            "standing_notes": "", "weights": {}, "updated_at": ""}


def preferences(client: str, url: str = "") -> dict:
    for row in _read(PREFS_FILE, "clients"):
        if _mine(row, client, url):
            out = _blank_prefs(client, url)
            out.update({k: v for k, v in row.items() if k in out})
            out["weights"] = row.get("weights") if isinstance(row.get("weights"), dict) else {}
            return out
    return _blank_prefs(client, url)


def save_preferences(client: str, url: str = "", **fields) -> dict:
    """Write what the client said. Only the fields they were shown.

    `weights` is never writable from here: it is the record of what they
    actually swiped, and a preference form that could overwrite it would let
    one press erase the only measured thing on the row.
    """
    with _lock:
        rows = _read(PREFS_FILE, "clients")
        row = next((r for r in rows if _mine(r, client, url)), None)
        if row is None:
            row = _blank_prefs(client, url)
            rows.insert(0, row)
        if "topics_wanted" in fields:
            row["topics_wanted"] = [t for t in (fields["topics_wanted"] or [])
                                    if t in social_content.IDEA_TAGS][:9]
        for key, limit in (("topics_avoid", 1000), ("tone", 300),
                           ("standing_notes", 2000)):
            if key in fields:
                value = fields[key]
                if key == "topics_avoid" and isinstance(value, (list, tuple)):
                    value = ", ".join(str(v) for v in value)
                row[key] = _text(value, limit)
        row["client"] = row.get("client") or _text(client, 200)
        row["client_url"] = row.get("client_url") or _text(url, 300)
        row["updated_at"] = _now()
        _write(PREFS_FILE, "clients", rows, MAX_PREFS)
    return preferences(client, url)


def weights(client: str, url: str = "") -> dict:
    return preferences(client, url).get("weights") or {}


def weight_table(client: str, url: str = "") -> list[dict]:
    """Every tag with its counts and weight, for the staff screen.

    All nine, including the ones nobody has answered on — a table showing only
    the tags with history cannot say that six of them have never been offered,
    which is the thing the exploration share exists to fix.
    """
    stored = weights(client, url)
    out = []
    for tag, meta in social_content.IDEA_TAGS.items():
        row = stored.get(tag) or {}
        liked, passed = int(row.get("liked_count") or 0), int(row.get("passed_count") or 0)
        out.append({"tag": tag, "label": meta["label"],
                    "liked": liked, "passed": passed,
                    "answered": liked + passed,
                    "weight": social_content.tag_weight(liked, passed)})
    out.sort(key=lambda r: (-r["answered"], -r["weight"], r["label"]))
    return out


# =====================================================================
# Ideas
# =====================================================================
def for_client(client: str, url: str = "", *, responses=None,
               limit: int = 200) -> list[dict]:
    rows = [r for r in _read(IDEAS_FILE, "ideas") if _mine(r, client, url)]
    if responses:
        wanted = set(responses)
        rows = [r for r in rows if str(r.get("client_response") or "pending") in wanted]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[:limit]


def pending(client: str, url: str = "", limit: int = 20) -> list[dict]:
    """What the client has not answered yet, oldest first.

    Oldest first on purpose: the swipe screen shows one card at a time, and
    working through the backlog beats re-offering last week's newest idea
    while a three-week-old one sits behind it for ever.
    """
    rows = [r for r in for_client(client, url, responses=("pending",))]
    rows.sort(key=lambda r: r.get("created_at") or "")
    return rows[:limit]


def get(idea_id: str) -> dict | None:
    for row in _read(IDEAS_FILE, "ideas"):
        if row.get("id") == idea_id:
            return row
    return None


def add(client: str, url: str = "", *, title: str = "", idea_tag: str = "",
        origin: str = "staff", note: str = "", source: str = "house") -> dict:
    title = _text(title, 300)
    if not title:
        raise ValueError("An idea needs a title.")
    if idea_tag not in social_content.IDEA_TAGS:
        idea_tag = "evergreen"
    row = {"id": _new_id("idea-"), "client": _text(client, 200),
           "client_url": _text(url, 300), "title": title,
           "idea_tag": idea_tag, "origin": _text(origin, 30) or "staff",
           "source": _text(source, 20) or "house",
           "note": _text(note, 600),
           "client_response": "pending", "responded_at": "",
           "promoted_batch_id": "", "promoted_slot_id": "",
           "created_at": _now()}
    with _lock:
        rows = _read(IDEAS_FILE, "ideas")
        rows.insert(0, row)
        _write(IDEAS_FILE, "ideas", rows, MAX_IDEAS)
    return row


def _mutate(idea_id: str, fn) -> dict | None:
    with _lock:
        rows = _read(IDEAS_FILE, "ideas")
        for row in rows:
            if row.get("id") == idea_id:
                fn(row)
                _write(IDEAS_FILE, "ideas", rows, MAX_IDEAS)
                return row
    return None


def respond(idea_id: str, response: str) -> dict | None:
    """Record Like or Pass, and fold it into this client's tag weights.

    Answering twice is not counted twice. The swipe screen is used on a phone,
    a double tap is ordinary, and a second Like that moved the weight would
    make the tool learn from the touchscreen rather than from the client.
    """
    if response not in ("liked", "passed"):
        raise ValueError("An idea is liked or passed.")
    row = get(idea_id)
    if not row:
        return None
    if str(row.get("client_response") or "pending") != "pending":
        return row

    def apply(item):
        item["client_response"] = response
        item["responded_at"] = _now()
    updated = _mutate(idea_id, apply)
    if not updated:
        return None

    client, url = row.get("client", ""), row.get("client_url", "")
    with _lock:
        rows = _read(PREFS_FILE, "clients")
        prefs = next((r for r in rows if _mine(r, client, url)), None)
        if prefs is None:
            prefs = _blank_prefs(client, url)
            rows.insert(0, prefs)
        prefs["weights"] = social_content.apply_response(
            prefs.get("weights") if isinstance(prefs.get("weights"), dict) else {},
            str(row.get("idea_tag")), response)
        prefs["updated_at"] = _now()
        _write(PREFS_FILE, "clients", rows, MAX_PREFS)
    return updated


def mark_promoted(idea_id: str, batch_id: str, slot_id: str) -> dict | None:
    def apply(row):
        row["promoted_batch_id"] = _text(batch_id, 40)
        row["promoted_slot_id"] = _text(slot_id, 20)
    return _mutate(idea_id, apply)


# =====================================================================
# Generating a batch
# =====================================================================
def _house_titles(tags: list[str], context: dict, prefs: dict) -> list[dict]:
    """The batch when the model could not be asked.

    Every one of these is a real, answerable prompt rather than a placeholder:
    a client swiping on "A customer's own words — a short quote or story from
    a happy customer" is telling us something useful even though no model
    wrote the line. Marked `source: "house"` so the screen can say so, and so
    a staff member reading the backlog can tell which batch was which.
    """
    business = _text(context.get("client") or context.get("name"), 120) or "this business"
    out = []
    for tag in tags:
        meta = social_content.IDEA_TAGS[tag]
        out.append({"idea_tag": tag, "source": "house",
                    "title": f"{meta['label']} — {meta['prompt']}",
                    "note": f"Suggested for {business} because nobody has "
                            "answered on this kind of post yet."
                            if not (prefs.get("weights") or {}).get(tag)
                            else ""})
    return out


def _model_messages(tags: list[str], context: dict, prefs: dict) -> list[dict]:
    lines = [
        "You write one-line social post ideas for a local business. The "
        "client reads them on a phone and answers Like or Pass, so each one "
        "must be a specific, single idea in plain American English, under 90 "
        "characters, with no hashtags, no emoji and no markdown.",
        "",
        f"Business: {_text(context.get('client'), 120)}",
    ]
    for key, label in (("industry", "Industry"), ("description", "What they do"),
                       ("products", "What they sell")):
        value = context.get(key)
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(v) for v in value[:8])
        if value:
            lines.append(f"{label}: {_text(value, 600)}")
    if prefs.get("standing_notes"):
        lines.append(f"They have told us: {_text(prefs['standing_notes'], 600)}")
    if prefs.get("topics_avoid"):
        # A "never mention" list is a check as well as a request — the
        # hub/blog_spec.py rule. This half is the request; the caller runs the
        # check over what comes back.
        lines.append(f"Never mention: {_text(prefs['topics_avoid'], 400)}")
    lines += [
        "",
        "Write exactly one idea for each of these kinds of post, in this order:",
    ]
    for tag in tags:
        lines.append(f"  - {tag}: {social_content.IDEA_TAGS[tag]['prompt']}")
    lines += [
        "",
        "Claim no price, no percentage, no deadline and no phone number: "
        "nobody has supplied one, and an idea carrying an invented offer "
        "gets the client a phone call about a deal they never ran.",
        'Answer as JSON: {"ideas":[{"tag":"...","title":"..."}]}',
    ]
    return [{"role": "system",
             "content": "You are a social media strategist for a local "
                        "marketing agency. You answer only in JSON."},
            {"role": "user", "content": "\n".join(lines)}]


def generate(client: str, url: str = "", *, context: dict | None = None,
             size: int | None = None, origin: str = "agent",
             extra_tags: list[str] | None = None) -> dict:
    """Build and store one batch of ideas for a client.

    Returns `{"ideas": [...], "source": "model"|"house", "note": str}` — the
    source is on the answer rather than inferred from it, because an empty
    reason is what makes a house batch look like a broken model call.
    """
    prefs = preferences(client, url)
    size = max(1, int(size or social_content.batch_size()))
    wanted = list(prefs.get("topics_wanted") or [])
    for tag in (extra_tags or []):
        if tag in social_content.IDEA_TAGS and tag not in wanted:
            wanted.append(tag)
    tags = social_content.idea_mix(prefs.get("weights"), size=size, wanted=wanted)

    context = dict(context or {})
    context.setdefault("client", client)

    drafted, source, note = [], "house", ""
    try:
        from hub import ai
        result = ai.chat_json(_model_messages(tags, context, prefs),
                              module="social_planner", purpose="social:ideas",
                              max_tokens=700, temperature=0.8)
        rows = result.get("ideas") if isinstance(result, dict) else None
        for item in (rows or []):
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "")
            title = _text(item.get("title"), 300)
            if title and tag in social_content.IDEA_TAGS:
                drafted.append({"idea_tag": tag, "title": title, "source": "model",
                                "note": ""})
        if drafted:
            source, note = "model", ""
    except Exception as exc:                              # noqa: BLE001
        # The provider's own wording never reaches a client-facing screen —
        # it has echoed key prefixes before. hub/ai.py already logged it.
        note = (f"These are our standing prompts rather than ideas written for "
                f"this client — we could not reach the writer just now "
                f"({type(exc).__name__}).")

    if not drafted:
        drafted = _house_titles(tags, context, prefs)
        if not note:
            note = ("These are our standing prompts rather than ideas written "
                    "for this client.")

    # The "never mention" list is enforced, not requested. A model told not to
    # say something says it often enough to matter, and this is the one screen
    # a client reads unsupervised.
    avoid = [w.strip().lower() for w in
             str(prefs.get("topics_avoid") or "").replace(";", ",").split(",")
             if w.strip()]
    kept = []
    dropped = 0
    for item in drafted:
        low = item["title"].lower()
        if avoid and any(word in low for word in avoid):
            dropped += 1
            continue
        kept.append(item)
    if dropped:
        note = (note + " " if note else "") + \
            f"{dropped} suggestion(s) were dropped for naming something on " \
            "this client's do-not-mention list."
    if not kept:
        kept = _house_titles(tags[:1], context, prefs)

    stored = [add(client, url, title=item["title"], idea_tag=item["idea_tag"],
                  origin=origin, note=item.get("note", ""),
                  source=item.get("source", "house"))
              for item in kept[:size]]
    return {"ideas": stored, "source": source, "note": note, "tags": tags}


# =====================================================================
# The weekly sweep
#
# `generate()` was only ever reachable from a button in the staff queue, so a
# client who opened their swipe link saw "Nothing to look at just yet" — for
# ever, unless a strategist had remembered that week. A link nobody has a
# reason to open is a link nobody opens, which is the whole feature failing in
# the one place it is visible to the client.
#
# This is the scheduler's half. Every rule in it is about the two ways a
# recurring model call goes wrong: spending on clients nobody is listening to,
# and burying the ones who are.
# =====================================================================
SWEEP_INTERVAL_DAYS = 7      # a client is offered a batch at most this often
SWEEP_PENDING_FLOOR = 3      # ...and only when fewer than this are unanswered
SWEEP_MAX_CLIENTS = 8        # per run, whatever the budget allows
SWEEP_BUDGET_SECONDS = 180.0


def _swept_at(prefs: dict) -> float:
    from datetime import datetime, timezone as _tz
    text = str(prefs.get("swept_at") or "").strip()
    if not text:
        return 0.0
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if not stamp.tzinfo:
        stamp = stamp.replace(tzinfo=_tz.utc)
    return stamp.timestamp()


def _mark_swept(client: str, url: str) -> None:
    with _lock:
        rows = _read(PREFS_FILE, "clients")
        row = next((r for r in rows if _mine(r, client, url)), None)
        if row is None:
            row = _blank_prefs(client, url)
            rows.insert(0, row)
        row["swept_at"] = _now()
        _write(PREFS_FILE, "clients", rows, MAX_PREFS)


def sweep_candidates() -> list[dict]:
    """Clients a batch is worth generating for, and why the rest were not.

    Three gates, each a way to spend a model call on nobody:

      * **Somebody at the client has answered at least once.** The first batch
        is a deliberate act by a strategist, from the button in the queue. A
        client who has never opened the link would otherwise accumulate ideas
        nobody will ever read, at a model call a week for ever — the shape
        `hub/google_index.py` had to learn to stop, where an unconfigured Hub
        wrote eight identical failures a day into the activity log.
      * **Their deck is nearly empty.** Topping up a client with nine
        unanswered cards makes the backlog grow faster than anyone answers it,
        and a deck that never ends is one people stop opening.
      * **Not more often than weekly**, measured from the last sweep of that
        client rather than from this run, so a scheduler that fires twice
        cannot offer two batches in a day.

    Returns rows carrying `skip` where a client was passed over, because
    "nobody was eligible" and "we could not read the client list" are
    different answers and only the first one is the system working.
    """
    import time as _time
    out: list[dict] = []
    now = _time.time()
    week = SWEEP_INTERVAL_DAYS * 86400
    for prefs in _read(PREFS_FILE, "clients"):
        client = str(prefs.get("client") or "").strip()
        if not client:
            continue
        url = str(prefs.get("client_url") or "")
        weights = prefs.get("weights") if isinstance(prefs.get("weights"), dict) else {}
        answered = sum(int((w or {}).get("liked_count") or 0) +
                       int((w or {}).get("passed_count") or 0)
                       for w in weights.values())
        row = {"client": client, "url": url, "answered": answered, "skip": ""}
        if not answered:
            row["skip"] = ("nobody here has swiped yet — the first batch is "
                           "the strategist's to send")
        elif now - _swept_at(prefs) < week:
            row["skip"] = f"offered a batch within the last {SWEEP_INTERVAL_DAYS} days"
        elif len(pending(client, url, limit=SWEEP_PENDING_FLOOR + 1)) >= SWEEP_PENDING_FLOOR:
            row["skip"] = "still has unanswered ideas waiting"
        out.append(row)
    return out


def sweep(*, limit: int | None = None, budget_seconds: float | None = None,
          actor: str = "scheduler") -> dict:
    """Generate one batch for each client due one. Never raises.

    Bounded on **both** axes, for the reason `video_library.index_backlog()`
    gives: scheduler jobs share one thread and a model call has no useful
    ceiling, so a count limit alone lets one slow run hold up every job behind
    it. A client is marked swept whether the batch came back from the model or
    from our own prompts — the point of the mark is that this client was
    offered something this week, and a house batch is something.
    """
    import time as _time
    limit = SWEEP_MAX_CLIENTS if limit is None else max(1, int(limit))
    budget = SWEEP_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    started = _time.time()

    try:
        rows = sweep_candidates()
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The client preferences could not be "
                                      f"read ({type(exc).__name__})."}

    due = [r for r in rows if not r["skip"]]
    done, failed, out_of_time = [], [], 0
    for row in due:
        if len(done) + len(failed) >= limit:
            break
        if _time.time() - started >= budget:
            out_of_time = len(due) - len(done) - len(failed)
            break
        try:
            context = {}
            try:
                from hub.client_context import tool_context
                context = tool_context(row["client"], row["url"], gallery=False)
            except Exception:                             # noqa: BLE001
                # The context is an enrichment, not a requirement. A client
                # book that would not answer costs the batch its detail, not
                # its existence.
                context = {}
            context["client"] = row["client"]
            result = generate(row["client"], row["url"], context=context,
                              origin="agent")
            _mark_swept(row["client"], row["url"])
            done.append({"client": row["client"], "ideas": len(result["ideas"]),
                         "source": result["source"]})
        except Exception as exc:                          # noqa: BLE001
            # Named, never counted: a client whose batch failed is a client
            # whose deck is still empty, and a total says nothing about which.
            failed.append({"client": row["client"], "why": type(exc).__name__})

    return {"ok": True, "clients": len(rows), "due": len(due),
            "generated": done, "failed": failed,
            "out_of_time": out_of_time,
            "skipped": len(rows) - len(due),
            "seconds": round(_time.time() - started, 1),
            "actor": actor}
