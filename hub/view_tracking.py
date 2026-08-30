"""What counts as somebody having read a document we sent them.

## Why this is a module rather than a `COUNT(*)`

"The client opened the proposal three times" is a sentence a rep acts on —
they call, they follow up, they stop chasing. So the number has to mean what
it says, and there are four well-known ways a read receipt comes to mean
something else:

  * **A mail security gateway opens every link in the message.** Mimecast,
    Proofpoint, Defender and the rest fetch a URL within seconds of delivery,
    before any human has seen it. Counted, that reports every proposal as read
    the moment it is sent, which is worse than counting nothing: it is a
    confident wrong answer, and the rep stops chasing a client who never
    opened it.
  * **A chat client makes a preview card.** Slack, iMessage and WhatsApp fetch
    the page to draw a thumbnail. Same problem, smaller.
  * **The rep opens it themselves** to check the link works. This one is the
    reason the feature was asked for with a rule attached: staff must be able
    to read the document without marking it read.
  * **A reload is not a second read.** Somebody scrolling back up, or a phone
    waking a tab, is one person reading once.

So a view is counted only when a **browser actually rendered the page** — the
page reports itself, and a scanner fetching HTML runs no JavaScript — and only
when the visitor is not staff, is not obviously a machine, and has not already
been counted inside `COUNT_WINDOW`.

Nothing here stores an IP address. `visitor_hash` is a keyed digest used to
recognise a repeat visit inside the window and for nothing else; the panel
that reads these rows shows counts and times, never a person or a place.

None of this makes the number perfect — a client who forwards the link to
three colleagues reads as three opens, which is arguably right, and a client
with JavaScript off reads as none, which is not. The panel says "opens" and
says what an open means, because a number whose definition is written down
can be argued with and a number that is merely displayed cannot.
"""
from __future__ import annotations

import hashlib
import re

# A reload, a back button, a phone waking the tab. One person, one read.
COUNT_WINDOW = 30 * 60          # seconds

# Matched against the User-Agent, lowercased. Deliberately short: these are
# the families that actually fetch a link nobody clicked. A list that tries to
# name every crawler ends up matching a real browser's UA string one day and
# silently stops counting a client who did read it.
BOT_PATTERNS = (
    "bot", "crawler", "spider", "slurp",
    "preview", "fetcher", "scanner", "monitor",
    "curl", "wget", "python-requests", "httpclient", "okhttp", "libwww",
    "headlesschrome",              # link unfurlers and screenshot services
    "slackbot", "whatsapp", "telegrambot", "discordbot", "twitterbot",
    "facebookexternalhit", "linkedinbot", "skypeuripreview",
    "proofpoint", "mimecast", "barracuda", "symantec", "microsoft office",
)

# Browsers announce a prefetch or a prerender. Chrome sends Sec-Purpose,
# older builds and Firefox send Purpose or X-Moz.
_PREFETCH_HEADERS = (("sec-purpose", "prefetch"), ("sec-purpose", "prerender"),
                     ("purpose", "prefetch"), ("x-moz", "prefetch"),
                     ("x-purpose", "preview"))


def looks_automated(user_agent: str = "", headers=None) -> tuple[bool, str]:
    """Whether this request is a machine rather than a reader, and why.

    Returns the reason as well as the verdict, because "we did not count this
    one" is a thing somebody will eventually need explained.
    """
    agent = str(user_agent or "").lower()
    if not agent:
        # Every real browser sends one. A request without a User-Agent is a
        # script, and counting it is how a proposal reads as opened by a
        # client who has never seen it.
        return True, "no browser identified itself"
    for pattern in BOT_PATTERNS:
        if pattern in agent:
            return True, f"automated client ({pattern})"
    for name, value in _PREFETCH_HEADERS:
        supplied = ""
        try:
            supplied = str((headers or {}).get(name) or "").lower()
        except Exception:                       # noqa: BLE001
            supplied = ""
        if value in supplied:
            return True, "the browser prefetched the page rather than showing it"
    return False, ""


def visitor_hash(ip: str, secret: str = "") -> str:
    """A stable, non-reversible id for one visitor, for the window check only.

    Keyed with the deployment's secret so the digests are useless anywhere
    else, and truncated because collisions between two readers inside the same
    half hour cost one uncounted view, while a longer digest buys nothing a
    panel of counts can use. The rule `hub/auth.py` already applies to the
    addresses in its lockout table: it is read into a page, so it is hashed.
    """
    raw = f"{secret}|{str(ip or '').strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def counts_as_new_view(last_seen: float | None, now: float,
                       window: int = COUNT_WINDOW) -> bool:
    """Whether this visit is a new read rather than the same one continuing."""
    if last_seen is None:
        return True
    try:
        return (float(now) - float(last_seen)) >= window
    except (TypeError, ValueError):
        return True


_MOBILE = re.compile(r"\b(iphone|ipad|ipod|android|mobile)\b", re.I)


def device_kind(user_agent: str = "") -> str:
    """"phone" or "computer" — the one thing about a reader worth recording.

    A rep reads this as "they opened it on their phone", which changes what
    they send next. Nothing narrower is stored: a full User-Agent string is a
    fingerprint, and this panel has no use for one.
    """
    return "phone" if _MOBILE.search(str(user_agent or "")) else "computer"
