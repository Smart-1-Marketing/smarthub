"""Whether anybody has actually seen a built landing page.

`hub/landing_maker.py` builds a page, files the leads it takes, and could not
answer the first question anybody asks about one: **did anyone look at it?**
So every conversion figure the tool produced was a ratio with no denominator
-- four leads is a good week off two hundred visits and a catastrophe off four
thousand, and nothing here could tell those apart. A rep pausing a campaign,
rewriting a headline or telling a client the page is working was doing it
from the lead count alone.

`hub/view_tracking.py` had already written down what a read receipt means and
every one of the four ways it comes to mean something else, and it had
**exactly one caller** -- the proposal share page. This is the second, and it
reads that module rather than carrying a second set of rules: a page that
counted a mail gateway as a visitor and a proposal that did not would be two
answers to one question, which is the drift this Hub keeps having to undo.

## Four rules, none of them this module's own

  * **The page reports itself.** A view is recorded from a beacon the browser
    fires, never on the HTML request, because a mail security gateway and a
    chat client's preview card both fetch the URL and run no JavaScript.
    Counted on the request, a page pasted into an email reads as seen by
    everyone it was sent to, the moment it was sent.
  * **Staff do not count.** A rep opening their own page to check the link is
    not a visit, and this is the failure most likely to go unnoticed: the
    number is simply a little high and nothing on any screen says why.
  * **A reload is not a second visit** -- `counts_as_new_view`, per visitor,
    inside `COUNT_WINDOW`.
  * **No address is stored.** `visitor_hash` is a keyed digest used for the
    window check and for nothing else. The panel shows counts, dates and
    whether it was a phone; it cannot show a person or a place because
    nothing here records one.

## What is deliberately not counted

**There is no revision axis.** The quote share page counts per revision,
because "have they opened the one I sent on Tuesday" is the whole question a
revised quote asks of one named reader. A landing page is ad traffic: nobody
sends revision 3 to anybody, and a visit is a visit. What a rewrite *does*
need is a line on the screen saying when it happened, so a count can be read
against it, and `hub/landing_maker.py` carries that already.

**Nothing here may raise, and nothing may refuse.** The beacon is fired by a
page a stranger is reading on somebody else's website. A view we could not
record costs a number; an exception on that route costs nothing visible and
is still not something this module is allowed to produce, because the same
`db.session` is the one the lead capture on that page uses.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from hub.extensions import db
from hub import view_tracking

log = logging.getLogger(__name__)


class LandingView(db.Model):
    """One counted visit to one built page.

    Its own table rather than a list on the landing page's own row.
    `landing_pages.json` is a single mirrored file holding the whole rendered
    HTML of every page -- several hundred kilobytes a row -- so appending a
    visit to it would rewrite the lot, under a cross-worker lock, on every
    request from a page taking paid traffic. That is the read-modify-write
    `hub/jsonstore.py` documents at length, at the one write rate in this Hub
    that could actually meet it.
    """

    __tablename__ = "hub_landing_views"
    id = db.Column(db.String(32), primary_key=True)
    # The page's own slug, which is what the form posts as `page` and what
    # every lead is filed under -- so a view and a lead can be counted
    # against each other without a second join key being invented.
    slug = db.Column(db.String(200), nullable=False, index=True)
    at = db.Column(db.DateTime, nullable=False, index=True)
    visitor = db.Column(db.String(64), nullable=False, default="")
    device = db.Column(db.String(20), nullable=False, default="")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record(slug: str, *, user_agent: str = "", headers=None, ip: str = "",
           secret: str = "", staff: bool = False,
           now: datetime | None = None) -> tuple[bool, str]:
    """Count this visit, or say why it was not counted.

    Returns `(counted, reason)` rather than a bare bool, because "we did not
    count that one" is a thing somebody eventually needs explained -- the
    rule `view_tracking.looks_automated` already works to, carried one step
    further out so the route can answer with it.

    Never raises. Every refusal and every failure is a `(False, reason)`.
    """
    slug = str(slug or "").strip()
    if not slug:
        return False, "no page named"
    if staff:
        # The rep checking their own link. Named rather than silently
        # dropped: a count that is quietly a little high is the version of
        # this nobody ever notices.
        return False, "staff preview — not counted"
    automated, why = view_tracking.looks_automated(user_agent, headers)
    if automated:
        return False, why

    visitor = view_tracking.visitor_hash(ip, secret)
    when = now or _now()
    try:
        last = (LandingView.query
                .filter(LandingView.slug == slug,
                        LandingView.visitor == visitor)
                .order_by(LandingView.at.desc()).first())
        last_seen = last.at.replace(tzinfo=timezone.utc).timestamp() if last and last.at else None
        if not view_tracking.counts_as_new_view(
                last_seen, when.replace(tzinfo=timezone.utc).timestamp()):
            return False, "already counted this visit"
        db.session.add(LandingView(
            id=uuid.uuid4().hex, slug=slug, at=when, visitor=visitor,
            device=view_tracking.device_kind(user_agent)))
        db.session.commit()
        return True, ""
    except Exception as exc:                                # noqa: BLE001
        # A page a stranger is reading must not show an error because we
        # could not write a row about them.
        try:
            db.session.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        log.warning("landing view not recorded for %s: %s", slug, exc)
        return False, "not recorded"


# A visit older than this is not what anybody means by "is this page
# working". The lifetime total is reported beside it, so a page that ran a
# campaign last spring does not read as a page nobody has ever seen.
RECENT_DAYS = 30


def summary_for(slugs) -> dict:
    """Counts per slug, or one honest statement that they could not be read.

    Answers `{"measured": False, "error": ...}` rather than a page of zeroes.
    A table that will not answer and a page nobody has visited render
    identically as a nought, and only one of them is a reason to stop
    spending on the campaign.
    """
    wanted = [str(s or "").strip() for s in (slugs or []) if str(s or "").strip()]
    if not wanted:
        return {"measured": True, "pages": {}}
    try:
        cutoff = _now() - timedelta(days=RECENT_DAYS)
        rows = (LandingView.query
                .filter(LandingView.slug.in_(wanted)).all())
    except Exception as exc:                                # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:                                   # noqa: BLE001
            pass
        return {"measured": False, "pages": {},
                "error": "The visit counts could not be read."
                         f" ({type(exc).__name__})"}

    pages: dict[str, dict] = {
        s: {"views": 0, "recent": 0, "phone": 0, "first": "", "last": ""}
        for s in wanted}
    for r in rows:
        p = pages.get(r.slug)
        if p is None:
            continue
        p["views"] += 1
        if r.at and r.at >= cutoff:
            p["recent"] += 1
        if r.device == "phone":
            p["phone"] += 1
        stamp = r.at.isoformat() if r.at else ""
        if stamp and (not p["first"] or stamp < p["first"]):
            p["first"] = stamp
        if stamp and (not p["last"] or stamp > p["last"]):
            p["last"] = stamp
    return {"measured": True, "pages": pages, "recent_days": RECENT_DAYS}


def line_for(counts: dict) -> str:
    """The one wording of a visit count, so no two screens can word it twice.

    It says **opens** rather than "visitors": one person who forwards the link
    to three colleagues is three, which is arguably right, and one with
    JavaScript off is none, which is not. A number whose definition is on the
    screen beside it can be argued with; one that is merely displayed cannot.
    """
    if not counts:
        return ""
    views = int(counts.get("views") or 0)
    if not views:
        return "No opens recorded yet."
    recent = int(counts.get("recent") or 0)
    phone = int(counts.get("phone") or 0)
    out = f"{views} open{'' if views == 1 else 's'}"
    if recent != views:
        out += f", {recent} in the last {RECENT_DAYS} days"
    if phone:
        out += f" · {phone} on a phone"
    return out + "."


# How many opens before a percentage means anything. Nobody publishes a
# figure for this, so it is **ours** and the screen says so -- the rule
# `services/abcd_service.py` applies to its own house threshold. Under it a
# page with two opens and one lead would read as converting at fifty per
# cent, which is a number somebody would repeat to a client.
MIN_OPENS = 25
MIN_OPENS_SOURCE = "house"


def conversion(counts: dict, leads: int = 0, leads_before: int = 0) -> dict:
    """What share of the people who opened this page became a lead.

    The number the whole of Tier 2 existed to make computable: before opens
    were counted this was a ratio with no denominator, and four leads read
    the same off two hundred visits as off four thousand.

    Five answers, because there are five situations and only one of them is
    a percentage:

      * **not measured** -- the view table would not answer. A rate of
        nought over a denominator nobody could read is the confident wrong
        answer this Hub keeps having to undo.
      * **nothing yet** -- nobody has opened it. Not a rate of nought:
        a page nobody has seen has not failed to convert anybody.
      * **too early** -- fewer than `MIN_OPENS`. The counts are shown and
        no percentage is, because the percentage would be noise with a
        decimal point on it.
      * **over** -- more leads than opens. That is not a page converting
        above a hundred per cent; it means the opens are undercounted,
        which happens when a privacy extension or a host's CSP blocks the
        beacon while the form still posts. Saying so is the only honest
        reading, and rounding it down to 100% would hide the one state
        that tells somebody the denominator is wrong.
      * a **rate**, with the two numbers it came from beside it.

    `leads_before` is the load-bearing one and it is why the numerator is
    not simply "every lead this page has taken". Opens have only been
    counted since this shipped, so a page that ran a campaign before then
    has leads with no visits behind them -- divided by the opens since, it
    would read as converting several hundred per cent. Those are counted
    apart and named rather than folded in or dropped.
    """
    if not counts:
        return {"measured": False, "state": "not_measured",
                "line": "Opens were not measured, so there is no rate."}
    opens = int(counts.get("views") or 0)
    leads = max(0, int(leads or 0))
    earlier = max(0, int(leads_before or 0))
    out = {"measured": True, "opens": opens, "leads": leads,
           "leads_before": earlier, "min_opens": MIN_OPENS,
           "min_opens_source": MIN_OPENS_SOURCE, "rate": None}
    tail = (f" {earlier} lead{'' if earlier == 1 else 's'} came in before "
            "opens were counted and are not in this."
            if earlier else "")
    if not opens:
        out["state"] = "none_yet"
        out["line"] = "Nobody has opened it yet, so there is no rate." + tail
        return out
    if leads > opens:
        out["state"] = "over"
        out["line"] = (
            f"{leads} leads from {opens} recorded opens — more leads than "
            "opens, so the opens are undercounted rather than the page "
            "converting above 100%. A privacy extension or the host's own "
            "content policy can block the beacon while the form still "
            "posts." + tail)
        return out
    if opens < MIN_OPENS:
        out["state"] = "too_early"
        out["line"] = (
            f"{leads} from {opens} open{'' if opens == 1 else 's'} — too "
            f"early to call a rate. Under {MIN_OPENS} opens a percentage is "
            "noise with a decimal point on it." + tail)
        return out
    out["state"] = "measured"
    out["rate"] = round(leads * 100.0 / opens, 1)
    out["line"] = (f"{out['rate']}% — {leads} lead"
                   f"{'' if leads == 1 else 's'} from {opens} opens." + tail)
    return out
