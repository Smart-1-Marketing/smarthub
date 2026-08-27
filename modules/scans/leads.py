"""State for the public scan widget: placements, and one row per scan run.

Two tables, both on the Scans engine so they live wherever the scans do:

``ScanWidget``  one row per placement. A widget is a slug, a headline and a
                tag; the tag is what tells you six months later which site a
                lead came off. New placements are added from the Hub, not by a
                deploy, because the whole point is to drop these on client
                sites as fast as someone asks.

``ScanRun``     one row per person who ran a scan, keyed by an unguessable
                token. This is the widget's own state — which domain was
                checked, what the free pre-check found, which audit was bought
                for it — not a second lead book.

**The lead itself belongs to ``hub/leads.py``**, the single store every
landing page and calculator already writes to. This module does not own a
webhook, a delivery status or a leads page; v1.42.0 consolidated those for
exactly the reason the Suite audit found, and a second panel here would undo
it. What is kept locally is the state the shared store has no place for: the
token, the pre-check result, and the link to the paid audit.

The scan is deliberately *not* on the critical path. A lead is a lead whether
or not Insites ever answers; the audit attaches to the row when it completes.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import (Column, DateTime, Integer, String, Text, and_, func,
                        or_)
from sqlalchemy.orm import declarative_base

Base = declarative_base()
_LOCK = threading.Lock()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:                  # stored naive-UTC, as elsewhere here
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat(timespec="seconds")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def slugify(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s[:48] or "scan"


# ==========================================================================
# Tables
# ==========================================================================

class ScanWidget(Base):
    """One embeddable placement."""

    __tablename__ = "scan_widgets"

    id = Column(Integer, primary_key=True)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)      # internal, e.g. "Smart 1 home page"
    tag = Column(String(64), nullable=False, index=True)   # what the lead is tagged with
    # Which of the two scans this placement serves. "aeo" is the free
    # five-second AI-visibility pre-check; "audit" asks the business a handful
    # of questions and gives back the full website audit.
    #
    # A column on the one placement table rather than a second table, because
    # a placement is a placement: it has a slug, a tag, an embed line and a
    # lead count whichever scan sits behind it, and two tables would be two
    # descriptions of that. `create_all()` never adds a column to an existing
    # table -- the trap CLAUDE.md names -- so `_add_missing_columns()` below
    # is what puts it on a database that already exists, and every read goes
    # through `kind_of()`, which reads a NULL from a row written before this
    # column as "aeo": that is what every existing placement is.
    kind = Column(String(16), default="aeo", index=True)
    headline = Column(String(300))
    subhead = Column(String(500))
    button_label = Column(String(80))
    accent = Column(String(16))                     # hex, for the host site's brand
    active = Column(Integer, default=1)
    created_at = Column(DateTime, nullable=False, default=_now)
    created_by = Column(String(120))

    def as_row(self, base_url: str = "") -> dict:
        return {
            "id": self.id, "slug": self.slug, "name": self.name,
            "tag": self.tag, "kind": kind_of(self.kind),
            "kind_label": KINDS[kind_of(self.kind)]["label"],
            "headline": self.headline or _default(self.kind, "headline"),
            "subhead": self.subhead or _default(self.kind, "subhead"),
            "button_label": self.button_label or _default(self.kind, "button_label"),
            "accent": self.accent or _default(self.kind, "accent"),
            "active": bool(self.active),
            "created_at": _iso(self.created_at),
            "created_by": self.created_by or "",
            "page_url": f"{base_url}/scans/w/{self.slug}",
            "embed_url": f"{base_url}/scans/embed/{self.slug}",
        }


DEFAULTS = {
    "headline": "Can AI find your business?",
    "subhead": "ChatGPT, Gemini and Perplexity now answer the questions your "
               "customers used to type into Google. Enter your website and "
               "see what they can — and can't — tell people about you.",
    "button_label": "Check my website",
    "accent": "#009ED2",
}

# The two placements. `aeo` is the original free pre-check; `audit` gathers a
# handful of answers from the business and gives back the full website audit.
#
# Two kinds rather than two widget systems, because a placement is a placement
# whichever scan sits behind it. What genuinely differs is the wording the
# visitor reads and how much they are asked for, so that is what is in the
# table and nothing else.
KINDS = {
    "aeo": {
        "label": "AI visibility check",
        "blurb": "A free five-second check of whether the AI assistants can "
                 "read a website, with the rest of the findings behind a name, "
                 "business, email and phone.",
        "defaults": DEFAULTS,
    },
    "audit": {
        "label": "Full website audit",
        "blurb": "Asks the business what they sell, where their customers come "
                 "from and what they are spending, then gives back the full "
                 "audit of their website — what they are already running, what "
                 "is missing and what it is costing them.",
        "defaults": {
            "headline": "How is your website really doing?",
            "subhead": "A free audit of everything a customer sees before they "
                       "call you — your Google listing, your reviews, what your "
                       "competitors are already running, and what is stopping "
                       "people getting in touch. Tell us a little about the "
                       "business and we will read the rest off your website.",
            "button_label": "Audit my website",
            "accent": "#0a2240",
        },
    },
}

DEFAULT_KIND = "aeo"


def kind_of(value) -> str:
    """The placement kind, reading anything unrecognised as the original one.

    A row written before the column existed carries NULL, and every one of
    those is an AI-visibility placement — that is all there was. Reading NULL
    as the new kind would silently change what a live embed on a client's
    website serves, which is the one thing this column must not be able to do.
    """
    v = str(value or "").strip().lower()
    return v if v in KINDS else DEFAULT_KIND


def _default(kind, field: str):
    return KINDS[kind_of(kind)]["defaults"][field]


def defaults_for(kind: str) -> dict:
    return dict(KINDS[kind_of(kind)]["defaults"])


class ScanRun(Base):
    """One run of the widget: a token, a domain, a pre-check, maybe an audit."""

    __tablename__ = "scan_widget_runs"

    id = Column(Integer, primary_key=True)
    token = Column(String(64), unique=True, nullable=False, index=True)
    widget_slug = Column(String(64), index=True)
    tag = Column(String(64), index=True)

    created_at = Column(DateTime, nullable=False, default=_now)
    unlocked_at = Column(DateTime)

    # Contact. Null until the gate is passed — an anonymous row is still worth
    # keeping, because it says which domain was checked and from what page.
    name = Column(String(200))
    email = Column(String(320))
    phone = Column(String(64))
    company = Column(String(200))

    domain = Column(String(255), index=True)
    site_url = Column(String(500))
    source = Column(String(300))            # page the widget was embedded on
    ip = Column(String(64))
    user_agent = Column(String(300))

    precheck_score = Column(Integer)
    precheck_json = Column(Text)

    # What the business itself told us, on an audit placement. Kept apart from
    # everything the crawler observed and never merged into it: where the two
    # disagree the disagreement is the finding, and folding one into the other
    # destroys the only evidence of it.
    kind = Column(String(16), default="aeo", index=True)
    intake_json = Column(Text)

    # The paid audit, attached when it lands. Never blocks the lead.
    scan_public_id = Column(String(64), index=True)
    scan_status = Column(String(32), default="pending")
    scan_reused = Column(Integer, default=0)   # 1 = no credit spent

    # Set once the row has been handed to hub.leads. Not a delivery status —
    # that lives with the lead, in the one place that owns delivery.
    lead_id = Column(String(32))

    def contact(self) -> dict:
        return {"name": self.name or "", "email": self.email or "",
                "phone": self.phone or "", "company": self.company or ""}

    def as_row(self) -> dict:
        d = self.contact()
        d.update({
            "id": self.id, "token": self.token,
            "widget_slug": self.widget_slug or "", "tag": self.tag or "",
            "created_at": _iso(self.created_at),
            "unlocked_at": _iso(self.unlocked_at),
            "domain": self.domain or "", "site_url": self.site_url or "",
            "source": self.source or "",
            "kind": kind_of(self.kind),
            "precheck_score": self.precheck_score,
            "scan_public_id": self.scan_public_id or "",
            "scan_status": self.scan_status or "",
            "scan_reused": bool(self.scan_reused),
            "lead_id": self.lead_id or "",
            "scan_url": (f"/scans/scan/{self.scan_public_id}"
                         if self.scan_public_id else ""),
            "report_url": (f"/scans/report/{self.scan_public_id}/seo_aeo"
                           if self.scan_public_id and
                           self.scan_status == "complete" else ""),
            "pdf_url": (f"/scans/report/{self.scan_public_id}/seo_aeo.pdf"
                        if self.scan_public_id and
                        self.scan_status == "complete" else ""),
        })
        return d


# ==========================================================================
# What a placement has produced
# ==========================================================================

def _iso_any(value) -> str | None:
    """``_iso`` for a value that came back from an aggregate.

    ``func.max()`` over a DateTime column is handed back as a datetime by both
    backends we run on, but a string here would raise inside a page that is
    only reporting a count, so it is passed through rather than crashed on.
    """
    if isinstance(value, str):
        return value or None
    return _iso(value)


def placement_stats_result(session, placements) -> tuple[dict | None, str]:
    """``(stats, error)`` — what each placement has produced, per slug.

    A pair rather than a bare dict, for the reason ``connected_accounts_result``
    gives in google_finder: **"this placement has produced nothing" and "we
    could not count" are different answers**, and only the first says anything
    about the placement. A count that falls back to zero on a failed read is a
    confident wrong number on the page somebody uses to decide whether a
    placement is worth keeping.

    A **lead** is a run with ``unlocked_at`` set — the moment somebody handed
    over a name, business, email and phone, which is the same moment the row is
    written to ``hub.leads``. Counting runs would report every anonymous domain
    check as a lead, and a public box on someone else's home page is typed into
    by passers-by far more often than it is converted.

    ``filed`` is the subset that reached ``hub.leads`` and came back with an id.
    A lead that was captured and not filed is a real person sitting in this
    table and in nobody's panel, so the difference is counted rather than
    averaged away.

    Runs count only from the placement's own ``created_at``. A slug deleted and
    created again is a *different* placement at the same address — the embed
    code is identical — so without that the old placement's leads would be
    added to the new one's total, which is the single number this column
    exists to state.

    ``placements`` is any iterable of rows carrying ``slug`` and ``created_at``.
    """
    wanted = [(p.slug, getattr(p, "created_at", None)) for p in placements
              if getattr(p, "slug", "")]
    if not wanted:
        return {}, ""

    conds = []
    for slug, since in wanted:
        cond = ScanRun.widget_slug == slug
        if since is not None:
            cond = and_(cond, ScanRun.created_at >= since)
        conds.append(cond)

    try:
        counted = (session.query(
            ScanRun.widget_slug,
            func.count(ScanRun.id),
            func.count(ScanRun.unlocked_at),
            # nullif, because _capture_lead writes "" when hub.leads answers
            # without an id -- and "" is not null, so a plain count would file
            # a lead nobody can find in the panel as filed.
            func.count(func.nullif(ScanRun.lead_id, "")),
            func.max(ScanRun.unlocked_at))
            .filter(or_(*conds))
            .group_by(ScanRun.widget_slug)
            .all())
    except Exception as exc:            # noqa: BLE001 - reported, never raised
        return None, str(exc)[:300]

    out = {slug: {"checks": 0, "leads": 0, "filed": 0, "unfiled": 0,
                  "last_lead": None} for slug, _ in wanted}
    for slug, checks, leads, filed, last in counted:
        if slug not in out:
            continue
        leads, filed = int(leads or 0), int(filed or 0)
        out[slug] = {"checks": int(checks or 0), "leads": leads,
                     "filed": filed, "unfiled": max(0, leads - filed),
                     "last_lead": _iso_any(last)}
    return out, ""


# ==========================================================================
# Validation
# ==========================================================================

def validate_contact(payload: dict) -> tuple[dict, dict]:
    """(contact, errors). Phone is required and checked for real digits.

    Phone was collected and validated in zero of the eleven Suite apps, which
    is why so many captured leads turned out not to be callable.
    """
    contact = {
        "name": str(payload.get("name") or "").strip()[:200],
        "email": str(payload.get("email") or "").strip()[:320],
        "phone": str(payload.get("phone") or "").strip()[:64],
        "company": str(payload.get("company") or "").strip()[:200],
    }
    errors: dict[str, str] = {}
    if len(contact["name"]) < 2:
        errors["name"] = "Enter your name."
    if not EMAIL_RE.match(contact["email"]):
        errors["email"] = "Enter a working email address."
    if len(re.sub(r"\D", "", contact["phone"])) < 10:
        errors["phone"] = "Enter a phone number with at least 10 digits."
    if len(contact["company"]) < 2:
        errors["company"] = "Enter your business name."
    return contact, errors


def validate_audit(payload: dict) -> tuple[dict, dict, dict]:
    """``(contact, intake, errors)`` for a full-audit placement.

    The audit widget asks for the contact *and* the handful of answers a
    crawler cannot get at — what they sell, where their customers are, what
    they are already spending. Three rules on it:

    * **The contact rules are the ones above, unchanged.** A second copy of
      "is this a real phone number" is a second answer to it.
    * **Only what `hub/website_audit.py` declares is accepted.** The questions
      live in one place and are read by the customer form, the staff form and
      the proposal prefill alike; a field invented here would be captured and
      read by nothing, which is the failure `current_marketing.py` shipped
      four of.
    * **Required means required, and "not asked" is never "no".** A question
      the visitor skipped is stored absent rather than as an empty answer, or
      a proposal prints a confident No the prospect never gave.
    """
    contact, errors = validate_contact(payload)
    intake: dict = {}
    try:
        from hub.website_audit import questions as _questions
        asked = _questions("customer")
    except Exception:                       # noqa: BLE001 - standalone/dev
        asked = []
    for q in asked:
        value = str((payload.get("intake") or {}).get(q["key"]) or "").strip()[:600]
        if value:
            intake[q["key"]] = value
        elif q.get("required"):
            errors[f"intake.{q['key']}"] = "This one we do need."
    return contact, intake, errors
