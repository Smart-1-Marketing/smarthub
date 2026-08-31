"""Web tickets raised automatically for SEO work that needs a person.

FAQ sections, schema blocks and blog posts are produced in the Hub, but none
of them reach a client's website until somebody puts them there. That handoff
was a conversation. This turns it into a Knack web ticket (object_107) with a
date on it:

    FAQ page created      -> one ticket, due 5 days from now
    Schema page created   -> one ticket per page, due 5 days from now
    Blog post planned     -> one ticket per post, due 2 days before publish

## Three things this has to get right

**It must never create the same ticket twice.** Regenerating schema for ten
pages, or re-saving an FAQ after a typo, must not put a second ticket in the
queue. Every ticket raised is recorded against the thing that caused it, in
the client's own store, and the key is checked before anything is sent. A
queue that fills with duplicates is a queue people stop reading.

**It must never break the thing that triggered it.** Saving an FAQ page is the
user's work; raising a ticket is ours. If Knack is slow, down, or rejects the
record, the save still succeeds and the failure is reported alongside it
rather than thrown. `SEO_TASKS_ENABLED=0` turns the whole thing off without a
deploy.

**A ticket with no due date is not what was asked for.** Every rule here is
defined by its date, so if the due-date field cannot be resolved the ticket is
still created — the work is real either way — but the result says so plainly,
and the caller passes that on. Silently dropping the date would leave a queue
that looks right and prioritises nothing.
"""
from __future__ import annotations

import datetime as _dt
import os

from . import audit, dates, knack_api

# How long each kind of work gets. Blogs count backwards from the publish date
# because the deadline is the publish, not the making of it.
DUE_DAYS_FAQ = int(os.environ.get("SEO_TASK_FAQ_DAYS", "5"))
DUE_DAYS_SCHEMA = int(os.environ.get("SEO_TASK_SCHEMA_DAYS", "5"))
BLOG_LEAD_DAYS = int(os.environ.get("SEO_TASK_BLOG_LEAD_DAYS", "2"))

# object_107's Type of Ticket (field_2973) is a dropdown, and Knack rejects a
# value that is not one of its choices. Left unset unless someone names one.
# A wrong value no longer costs the ticket — create_ticket checks it against
# the live choices and refuses the field instead — but it does still mean
# these tickets reach the queue untyped, and the refusal is reported below.
TICKET_TYPE = os.environ.get("SEO_TASK_TICKET_TYPE", "").strip()


def enabled() -> bool:
    return (os.environ.get("SEO_TASKS_ENABLED", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def due_field() -> str | None:
    """The Knack field id that holds a ticket's due date.

    Not in TICKET_FIELDS: nothing in the Hub had a use for it until now, and
    it is not in the field list we were given. So it is looked up — an
    explicit id first, then the live object's labels. Runtime discovery is
    what the rest of this module already does for `date` and `requested_by`.
    """
    explicit = os.environ.get("KNACK_TICKET_DUE_DATE", "").strip()
    if explicit:
        return explicit
    try:
        return knack_api._find_field("due date", "date due", "due", "deadline",
                                     "target date")
    except Exception:                                   # noqa: BLE001
        return None


# --------------------------------------------------------------- the store
def _tasks(store: dict) -> dict:
    return store.setdefault("seo_tasks", {})


def page_key(url: str) -> str:
    """One page, however its URL happens to have been written.

    The dedupe key was the raw URL string, and the title beside it was
    `_short()` -- so the module already knew how to reduce a URL to the page a
    person means, and used that for what somebody reads while comparing the
    unreduced string. Five ordinary spellings of one page therefore raised
    five tickets, four of them with an identical title:

        https://acme.com/services          Add schema markup to /services
        https://acme.com/services/         Add schema markup to /services
        http://acme.com/services           Add schema markup to /services
        https://www.acme.com/services      Add schema markup to /services
        https://acme.com/services?utm=x    Add schema markup to /services?utm=x

    Which is the thing this module's own docstring opens by forbidding: "It
    must never create the same ticket twice ... A queue that fills with
    duplicates is a queue people stop reading." The URLs reach it from a
    crawled sitemap or a list posted by the browser, so trailing-slash and
    www variation between a crawl and a typed entry is ordinary, and an
    http -> https migration would duplicate the whole book at once.

    Host AND path, because the store is per client and a client with two
    domains would otherwise collide on `/services`. The query string is
    dropped: `?utm_source=x` is the same page to somebody adding schema to it.
    """
    s = str(url or "").strip()
    if not s:
        return ""
    s = s.split("#", 1)[0].split("?", 1)[0]
    s = s.split("://", 1)[-1]
    host, _, path = s.partition("/")
    host = host.lower().removeprefix("www.")
    path = "/" + path.strip("/")
    return host + (path if path != "/" else "")


def already(store: dict, kind: str, key: str) -> dict | None:
    """The ticket already raised for this thing, under either spelling.

    The stored keys were raw URLs, and every record written before this
    carries one. Reading the canonical key alone would make all of them
    invisible and raise a second ticket for everything already ticketed --
    the migration this codebase refuses everywhere else (`audit.LOG_NAMES`,
    `video_library.TAG_ALIASES`): match the old spelling rather than
    re-indexing what is already on disk.
    """
    rows = _tasks(store).get(kind, {})
    hit = rows.get(str(key))
    if hit is not None:
        return hit
    canon = page_key(key)
    if not canon:
        return None
    for stored_key, row in rows.items():
        if page_key(stored_key) == canon:
            return row
    return None


def _record(store: dict, kind: str, key: str, value: dict) -> None:
    # Written under the canonical key, so the next spelling of the same page
    # finds it without the walk above. The raw URL stays on the row, because
    # that is what the ticket body quotes.
    rows = _tasks(store).setdefault(kind, {})
    rows[page_key(key) or str(key)] = {**value, "url": str(key)}


def _short(url: str) -> str:
    """A page as a person refers to it: the path, or the host for a home page."""
    s = str(url or "").strip()
    s = s.split("://", 1)[-1]
    host, _, path = s.partition("/")
    path = "/" + path.strip("/")
    return path if path != "/" else host.replace("www.", "")


def _site_of(store: dict, fallback: str = "") -> str:
    for key in ("site_url", "url", "website"):
        v = (store.get("setup") or {}).get(key) or store.get(key)
        if v:
            return str(v)
    return fallback


# --------------------------------------------------------------- creating
def _raise_ticket(client: str, website: str, title: str, body: str,
                  due: _dt.date, actor: str = "") -> dict:
    """Send one ticket. Returns {ok, id, due, note} and never raises."""
    if not enabled():
        return {"ok": False, "skipped": "SEO_TASKS_ENABLED is off"}
    if not knack_api.configured():
        return {"ok": False, "skipped": "Knack is not configured"}

    note = ""
    field = due_field()
    if not field:
        # Say it once, clearly, rather than producing a queue with no dates in
        # it and letting somebody discover that later.
        note = ("No due-date field could be found on the web-ticket object, so "
                "this ticket was raised without one. Set KNACK_TICKET_DUE_DATE "
                "to the field id to fix it.")

    body = body + f"\n\nDue {dates.fmt(due)}. Raised automatically by Smart 1 Hub."

    try:
        created = knack_api.create_ticket(
            client=client, website=website, subject=title, description=body,
            author=actor or "Smart 1 Hub", requested_by=actor or "Smart 1 Hub",
            ticket_type=TICKET_TYPE,
            extra={field: due.isoformat()} if field else None)
    except Exception as exc:                            # noqa: BLE001
        # The caller's own work has already succeeded by the time we get here.
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}

    # Anything Knack would have refused is named rather than dropped — an
    # SEO_TASK_TICKET_TYPE that is not one of the object's choices otherwise
    # goes unwritten with nothing to say so.
    refused = created.get("rejected") or []
    if refused:
        note = (note + " " if note else "") + "Not written: " + "; ".join(refused)

    return {"ok": True, "id": created.get("id") or (created.get("record") or {}).get("id"),
            "due": due.isoformat(), "note": note}


def _ensure(client: str, kind: str, key: str, title: str, body: str,
            due: _dt.date, actor: str = "") -> dict:
    """Raise a ticket for this item unless one already exists."""
    from . import seo

    store = seo.load_store(client)
    seen = already(store, kind, key)
    if seen:
        return {"ok": True, "existing": True, "id": seen.get("id"),
                "due": seen.get("due")}

    website = _site_of(store)
    result = _raise_ticket(client, website, title, body, due, actor)
    if not result.get("ok"):
        return result

    # Written only on success, so a failed send is retried next time rather
    # than being remembered as done.
    store = seo.load_store(client)
    _record(store, kind, key, {"id": result.get("id"), "due": result.get("due"),
                               "title": title, "created": _dt.date.today().isoformat()})
    seo.save_store(client, store)

    audit.log("seo", "seo_task_created", actor=actor or "system", client=client,
              detail=f"{kind}: {title}", tool="seo_tasks")
    return result


# ------------------------------------------------------------------ rules
def for_faq(client: str, url: str, page_title: str = "", actor: str = "") -> dict:
    """One ticket per FAQ page, due 5 days out.

    "For the customer and for the page" is one ticket, not two: a web ticket
    already carries both — Client Organization and Client Website URL — and
    raising a second, client-level ticket every time an FAQ is written would
    put one row per page in the queue plus one nobody can close.
    """
    due = _dt.date.today() + _dt.timedelta(days=DUE_DAYS_FAQ)
    where = _short(url)
    return _ensure(
        client, "faq", url,
        f"Add FAQ section to {where}",
        (f"An FAQ section has been written for {url}"
         + (f" ({page_title})" if page_title else "")
         + ".\n\nIt needs adding to the live page. The questions, the accordion "
           "markup and the FAQ schema are all on the client's SEO page in "
           "Smart 1 Hub."),
        due, actor)


def for_schema(client: str, url: str, actor: str = "") -> dict:
    """One ticket per page that got schema, due 5 days out."""
    due = _dt.date.today() + _dt.timedelta(days=DUE_DAYS_SCHEMA)
    where = _short(url)
    return _ensure(
        client, "schema", url,
        f"Add schema markup to {where}",
        (f"Structured data has been generated for {url}.\n\n"
         "It needs adding to the live page. The JSON-LD block is on the "
         "client's SEO page in Smart 1 Hub, under Saved schema pages."),
        due, actor)


def for_blog(client: str, post: dict, actor: str = "") -> dict:
    """One ticket per planned post, due two days before it publishes.

    The date to work to is the publish date, so the ticket is dated to land
    before it rather than on it. A post already inside the lead time — or past
    it — gets tomorrow, because a ticket that opens overdue reads as a data
    problem rather than as urgent.
    """
    publish = dates.to_date(post.get("date"))
    today = _dt.date.today()
    if publish:
        due = publish - _dt.timedelta(days=BLOG_LEAD_DAYS)
        if due <= today:
            due = today + _dt.timedelta(days=1)
    else:
        due = today + _dt.timedelta(days=1)

    title = str(post.get("title") or f"Blog post {post.get('id')}").strip()
    return _ensure(
        client, "blog", post.get("id"),
        f"Publish blog: {title[:70]}",
        (f"Blog post scheduled to publish {dates.fmt(post.get('date'))}.\n\n"
         f"Title: {title}\n"
         + (f"Summary: {post.get('summary')}\n" if post.get("summary") else "")
         + "\nThe draft is on the client's SEO page in Smart 1 Hub, under Blogs."),
        due, actor)


# ------------------------------------------------------------------ bulk
def for_pages(client: str, urls, kind: str = "schema", actor: str = "") -> dict:
    """Tickets for a batch of pages. Reports each outcome separately.

    A summary of "10 tickets raised" when three of them failed is the kind of
    confident wrong answer this codebase keeps having to undo.
    """
    fn = for_faq if kind == "faq" else for_schema
    created, existing, failed, note = [], [], [], ""
    for url in urls or []:
        r = fn(client, url, actor=actor)
        note = note or r.get("note") or ""
        if r.get("existing"):
            existing.append(url)
        elif r.get("ok"):
            created.append(url)
        else:
            failed.append({"url": url, "why": r.get("error") or r.get("skipped") or "unknown"})
    return {"created": created, "existing": existing, "failed": failed,
            "note": note}


def for_posts(client: str, posts, actor: str = "") -> dict:
    created, existing, failed, note = [], [], [], ""
    for post in posts or []:
        r = for_blog(client, post, actor=actor)
        note = note or r.get("note") or ""
        if r.get("existing"):
            existing.append(post.get("id"))
        elif r.get("ok"):
            created.append(post.get("id"))
        else:
            failed.append({"id": post.get("id"),
                           "why": r.get("error") or r.get("skipped") or "unknown"})
    return {"created": created, "existing": existing, "failed": failed, "note": note}


def status(client: str) -> dict:
    """What has already been raised for this client, for the SEO page."""
    from . import seo
    t = _tasks(seo.load_store(client))
    return {kind: len(items) for kind, items in t.items()}
