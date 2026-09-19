"""The YouTube channel section of a client's report: subscribers, lifetime
views and the number of videos, as YouTube answered last night.

``gate(client_key, client_name)`` decides whether a client gets the section
and ``section(link, today)`` builds it, the ``organic.py`` shape: both are
read by ``client_view.build()``, so the page, its ``data.json`` and the PDF
carry one answer, and by the staff page, which prints the gate so a rep
can see why the section is or is not on the client's report.

## The gate

Two things have to be true, and each is read from the module that owns it:

* **The client has a live video or social product** -- a row on the live
  product book (``hub.knack_data._product_source()``, the same live-first
  read the SEO section makes) whose medium ``hub.creative_needs.medium_of``
  calls video or social, matched on the exact normalised name and never a
  substring. A TrueView buy, a bumper, a social-media retainer: the
  products whose work touches the channel. A client with none gets no
  channel section however large their channel is, because the section
  sits on a report about work Smart 1 does.
* **A person has confirmed which channel is theirs** -- ``hub.youtube.record``,
  keyed on the client's name. A link on the SEO record is a hint the
  lookup reads first and never a confirmation.

A confirmed channel with no reading yet is gated in and absent from the
page: ``public_view()`` drops a block nothing measured, because "confirmed
and not read yet" is a sentence about our tooling on a document about
their business. The staff page keeps the reason.

## What is measured, and what is said

Three counts and their date, and the 30-day change -- views gained in the
last thirty days is the one period figure the keyed API can give, and it
is arithmetic on two of our own readings (``hub/youtube.py``). A hidden
subscriber count reads as hidden, never as zero. "YouTube" is named,
because it is the client's own channel and not a vendor Smart 1 buys
from -- ``products.ALLOWED`` carries the reason -- and the labels are what
a client reads.

**Watch time, subscribers gained and traffic sources ride beside it, when
they exist.** That is the OAuth half, ``hub/youtube_analytics.py`` --
read through a ``modules/youtube_studio`` connection for a channel we
actively manage, which most confirmed channels are not. It is never part
of the gate: a client with the keyed counts and no Studio connection gets
the section with that half simply absent, the same way Search Console is
optional inside the organic section. ``analytics.public_view()`` is what
decides whether it reaches the client at all -- ``not_connected`` and
every other kind of nothing stay staff-only, the rule this whole module
follows one level up.
"""
from __future__ import annotations

import logging
from datetime import date

log = logging.getLogger(__name__)

LABELS = {
    "section": "YouTube channel",
    "subscribers": "Subscribers",
    "views": "Video views, all time",
    "videos": "Videos on the channel",
    "change": "Over the last 30 days",
    "hidden": "hidden by the channel",
    "watch_minutes": "Minutes watched",
    "subscribers_net": "Subscribers gained",
    "period_views": "Views in the period",
    "sources": "Where views came from",
}

# The mediums whose work touches the channel. creative_needs.VIDEO covers
# YouTube, bumpers, OTT and CTV; SOCIAL covers the retainers that post to
# it. Read from that module rather than restated, so the classifier the
# proposal gate uses is the one this gate uses.
def _channel_mediums() -> tuple[str, ...]:
    from hub import creative_needs
    return (creative_needs.VIDEO, creative_needs.SOCIAL)


# ---------------------------------------------------------------------------
# Sources -- each behind its own function, so a test stands in for one
# ---------------------------------------------------------------------------

def _product_rows() -> list[dict]:
    from hub import knack_data
    return knack_data._product_source()[0]


def _running(row: dict) -> bool:
    from hub import knack_data
    return bool(knack_data.is_running(row))


def _medium(row: dict) -> str:
    from hub import creative_needs
    return creative_needs.medium_of({"product": str(row.get("product") or ""),
                                     "description": str(row.get("tactics") or "")})


def _record(name: str) -> dict | None:
    from hub import youtube as hub_youtube
    return hub_youtube.record(name)


def _reading(name: str, today: date) -> dict:
    from hub import youtube as hub_youtube
    return hub_youtube.reading(name, today=today)


def _public(r: dict) -> dict | None:
    from hub import youtube as hub_youtube
    return hub_youtube.public_view(r)


def _analytics_reading(name: str, today: date) -> dict:
    from hub import youtube_analytics
    return youtube_analytics.reading(name, today=today)


def _analytics_public(r: dict) -> dict | None:
    from hub import youtube_analytics
    return youtube_analytics.public_view(r)


def _norm(name: str) -> str:
    try:
        from hub import client_key as ck
        return ck.normalise_name(name)
    except Exception:                                   # noqa: BLE001
        return str(name or "").strip().lower()


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def gate(client_key: str, client_name: str = "") -> dict:
    """Whether this client gets the section, and why or why not.

    ``{"gated", "product", "channel", "read", "products", "name",
    "channel_title", "channel_url", "why": [...], "errors": [...]}``.
    """
    name = (client_name or "").strip()
    if not name:
        try:
            from hub import client_key as ck
            name = ck.key_label(client_key)
        except Exception:                               # noqa: BLE001
            name = client_key
    out = {"gated": False, "product": False, "channel": False, "read": False,
           "products": [], "name": name, "channel_title": "", "channel_url": "",
           "why": [], "errors": []}
    # 1. the product
    try:
        want = _norm(name)
        mediums = _channel_mediums()
        seen = []
        for row in _product_rows():
            if _norm(str(row.get("client") or "")) != want or not _running(row):
                continue
            if _medium(row) in mediums:
                p = str(row.get("product") or "").strip()
                if p and p not in seen:
                    seen.append(p)
        if seen:
            out["product"] = True
            out["products"] = seen
    except Exception as exc:                            # noqa: BLE001
        out["errors"].append(f"the product book could not be read ({type(exc).__name__})")
    # 2. the channel
    try:
        rec = _record(name)
    except Exception as exc:                            # noqa: BLE001
        rec = None
        out["errors"].append(f"the channel store could not be read ({type(exc).__name__})")
    if rec:
        out["channel"] = True
        out["channel_title"] = str(rec.get("title") or "")
        out["channel_url"] = str(rec.get("url") or "")
    if not out["product"]:
        out["why"].append("no live video or social product on the client's book")
    if not out["channel"]:
        out["why"].append("no YouTube channel has been confirmed for the client")
    out["gated"] = out["product"] and out["channel"]
    return out


# ---------------------------------------------------------------------------
# The section
# ---------------------------------------------------------------------------

def section(link, today: date | None = None, gate_: dict | None = None) -> dict | None:
    """The channel block of the aggregate, or None when the client is not
    gated in. ``link`` is the ReportLink row."""
    today = today or date.today()
    g = gate_ or gate(link.client, link.client_name)
    if not g["gated"]:
        return None
    out = {"labels": dict(LABELS), "measured": False, "staff_note": "", "state": ""}
    try:
        r = _reading(g["name"], today)
    except Exception as exc:                            # noqa: BLE001
        out["staff_note"] = f"The channel store could not be read ({type(exc).__name__})."
        return out
    card = _public(r)
    if not card:
        out["staff_note"] = r.get("staff_note") or r.get("error") or "No reading."
        out["state"] = r.get("state") or ""
        return out
    out.update(card)
    out["staff_note"] = r.get("staff_note") or ""
    out["state"] = "ok"
    # The OAuth half rides beside the keyed one, never gating it: most
    # confirmed channels have no youtube_studio connection, and that is the
    # ordinary answer rather than a reason to withhold the counts above.
    try:
        ar = _analytics_reading(g["name"], today)
        out["analytics"] = _analytics_public(ar)
        out["analytics_staff_note"] = ar.get("staff_note") or ar.get("error") or ""
    except Exception as exc:                            # noqa: BLE001
        out["analytics"] = None
        out["analytics_staff_note"] = f"The Analytics reading could not be taken ({type(exc).__name__})."
    return out


def public_view(block: dict | None) -> dict | None:
    """The block as the client's page and data.json carry it: nothing
    measured is no block at all, and the staff wording never travels."""
    if not block or not block.get("measured"):
        return None
    out = {k: v for k, v in block.items()}
    out.pop("staff_note", None)
    out.pop("analytics_staff_note", None)
    out.pop("state", None)
    if not out.get("analytics"):
        out.pop("analytics", None)
    return out


def staff_gate(client: str, name: str) -> dict:
    """The gate for the staff page's settings row, plus whether a reading
    exists. Never raises -- a gate that 500s the page it explains is worse
    than none."""
    try:
        g = gate(client, name)
    except Exception as exc:                            # noqa: BLE001
        g = {"gated": False, "product": False, "channel": False, "read": False,
             "products": [], "name": name, "channel_title": "", "channel_url": "",
             "why": [f"the gate could not be read ({type(exc).__name__})"], "errors": []}
    if g.get("channel"):
        try:
            r = _reading(g.get("name") or name, date.today())
            g["read"] = r.get("state") == "ok"
            g["read_note"] = r.get("staff_note") or ("" if g["read"] else r.get("error") or "")
            g["as_of"] = r.get("as_of") or ""
        except Exception as exc:                        # noqa: BLE001
            g["errors"].append(f"the reading could not be taken ({type(exc).__name__})")
        try:
            ar = _analytics_reading(g.get("name") or name, date.today())
            g["analytics_state"] = ar.get("state") or ""
            g["analytics_note"] = ar.get("staff_note") or ar.get("error") or ""
        except Exception as exc:                        # noqa: BLE001
            g["errors"].append(f"the Analytics reading could not be taken ({type(exc).__name__})")
    return g
