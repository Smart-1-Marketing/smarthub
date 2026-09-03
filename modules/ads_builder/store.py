"""Persistence for the Smart 1 Ads module.

SQLite locally, Postgres on Render via DATABASE_URL — the same dual-mode
pattern the scans and sales_builder modules use, so this is testable offline
and durable in production. All three now get that from hub/extensions rather
than each building an engine of its own; see the note in modules/scans/app.py.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, select
from sqlalchemy.orm import declarative_base

from hub.extensions import create_all_metadata, session_factory, shared_engine

log = logging.getLogger(__name__)

engine = shared_engine()
SessionLocal = session_factory()
Base = declarative_base()


def now():
    return datetime.now(timezone.utc)


class Proposal(Base):
    __tablename__ = "ads_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    public_id = Column(String(40), unique=True, nullable=False, index=True)
    client_name = Column(String(300), default="")
    google_customer_id = Column(String(20), default="")
    status = Column(String(30), default="DRAFT", index=True)
    created_by = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=now)
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)
    campaign_json = Column(Text, default="{}")
    comments_json = Column(Text, default="[]")
    deployment_json = Column(Text, default="")

    # ------------------------------------------------------------ helpers
    @property
    def client_key(self) -> str:
        """The Hub-wide client key for this proposal.

        Derived, not stored: `client_name` is free text typed into the builder,
        so it is "Riverside HVAC" here and "Riverside HVAC LLC" in Google
        Access. The key is what makes those one client. Falls back to the raw
        name if the Hub isn't importable, which is how this module stays
        runnable on its own.
        """
        try:
            from hub.client_key import resolve
            return resolve(name=self.client_name or "")["key"]
        except Exception:                                 # noqa: BLE001
            return ""

    @property
    def campaign(self) -> dict:
        return json.loads(self.campaign_json or "{}")

    @property
    def comments(self) -> list:
        return json.loads(self.comments_json or "[]")

    @property
    def deployment(self) -> dict | None:
        return json.loads(self.deployment_json) if self.deployment_json else None

    def as_dict(self) -> dict:
        campaign = self.campaign
        groups = campaign.get("adGroups") or []
        return {
            "id": self.public_id,
            "client_name": self.client_name,
            "client_key": self.client_key,
            "google_customer_id": self.google_customer_id,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "campaign": campaign,
            "comments": self.comments,
            "deployment": self.deployment,
            "ad_group_count": len(groups),
            "keyword_count": sum(len(g.get("keywords") or []) for g in groups),
            "negative_count": sum(
                len(v or []) for v in (campaign.get("negativeKeywordVault") or {}).values()
            ),
            "estimate_approved": bool((campaign.get("estimate") or {}).get("approved_at")),
        }


class Share(Base):
    """One client-facing estimate link, and whatever the client answered.

    A separate table rather than columns on ``ads_proposals``: ``create_all()``
    creates a missing TABLE but never adds a column to an existing one, so a
    column added here would be silently absent on the live Postgres while every
    local test passed. It also keeps the public review — written by somebody
    with no Hub login — in its own row rather than inside the campaign a rep is
    editing at the same time.
    """
    __tablename__ = "ads_estimate_shares"

    token = Column(String(64), primary_key=True)
    public_id = Column(String(40), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now)
    created_by = Column(String(200), default="")
    revoked = Column(Integer, default=0)

    # Filled in by the client, from the public page. Empty until they answer.
    outcome = Column(String(40), default="")
    reviewer_name = Column(String(200), default="")
    reviewer_email = Column(String(200), default="")
    reviewer_note = Column(Text, default="")
    responded_at = Column(DateTime(timezone=True), nullable=True)

    # Per-section change requests, appended as they arrive. A client can ask
    # for three changes over ten minutes and then answer; each is kept with the
    # name and email of whoever asked, because "the client wants X" is not
    # actionable without knowing which person at the client said it.
    changes_json = Column(Text, default="[]")
    opened_count = Column(Integer, default=0)

    @property
    def changes(self) -> list:
        return json.loads(self.changes_json or "[]")

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "proposal_id": self.public_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "created_by": self.created_by,
            "revoked": bool(self.revoked),
            "outcome": self.outcome or "",
            "reviewer_name": self.reviewer_name or "",
            "reviewer_email": self.reviewer_email or "",
            "reviewer_note": self.reviewer_note or "",
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
            "changes": self.changes,
            "opened_count": self.opened_count or 0,
        }


class OptimizationRun(Base):
    """One account's optimization scan, kept so the answer outlives the click.

    A separate table rather than columns on ``ads_proposals``: a scan result is
    not a proposal, several proposals can name one Google account, and
    ``create_all()`` creates a missing TABLE but never adds a column to an
    existing one — so a column added there would be silently absent on the live
    Postgres while every local test passed.

    ``items_json`` carries the whole ``analyse_rows()`` output for the same
    reason ``Proposal.campaign_json`` carries the whole campaign: the shape
    grows a field every time a detector is added, and a column-per-finding
    schema would have to be migrated for each of them.

    A scan that failed entirely still gets a row, with ``error`` set. Silence
    and "we looked and the account is clean" are different answers and only one
    of them means there is nothing to do.
    """
    __tablename__ = "ads_optimization_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(String(20), default="", index=True)
    client_name = Column(String(300), default="")
    scanned_at = Column(DateTime(timezone=True), default=now, index=True)
    date_range = Column(String(40), default="LAST_30_DAYS")
    item_count = Column(Integer, default=0)
    high_severity_count = Column(Integer, default=0)
    triggered = Column(String(20), default="scheduled")
    items_json = Column(Text, default="{}")
    error = Column(Text, default="")

    @property
    def result(self) -> dict:
        return json.loads(self.items_json or "{}")

    def as_dict(self, *, with_result: bool = False) -> dict:
        row = {
            "id": self.id,
            "customer_id": self.customer_id or "",
            "client_name": self.client_name or "",
            "scanned_at": self.scanned_at.isoformat() if self.scanned_at else None,
            "date_range": self.date_range or "",
            "item_count": self.item_count or 0,
            "high_severity_count": self.high_severity_count or 0,
            "triggered": self.triggered or "",
            "error": self.error or "",
            "measured": not self.error,
        }
        if with_result:
            row["result"] = self.result
        return row


class AutoApply(Base):
    """Whether unattended work may change one Google Ads account, and what of.

    **Off until somebody turns it on, account by account.** Applying a change
    to a client's live account with nobody having pressed anything is a
    business decision rather than an engineering one, so the absence of a row
    here means no — a new account cannot inherit it and a fresh install cannot
    start with it.

    Keyed on the **Google account** rather than on a proposal, which is a
    deliberate departure from the shape of every other setting in this module.
    The sweep is per account: two proposals naming one customer id and
    disagreeing about whether it auto-applies is a question nothing here could
    answer, and picking the more recent one would be the guess
    hub/client_key.py exists to refuse.

    A table rather than columns on ``ads_proposals`` for the reason ``Share``
    already gives: ``create_all()`` creates a missing TABLE and never adds a
    column to an existing one, so a column added there would be silently
    absent on the live Postgres while every local test passed.
    """
    __tablename__ = "ads_auto_apply"

    customer_id = Column(String(20), primary_key=True)
    enabled = Column(Integer, default=0)
    categories_json = Column(Text, default="[]")
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)
    updated_by = Column(String(200), default="")

    def as_dict(self) -> dict:
        return {
            "customer_id": self.customer_id or "",
            "enabled": bool(self.enabled),
            "categories": json.loads(self.categories_json or "[]"),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "updated_by": self.updated_by or "",
        }


class Setting(Base):
    __tablename__ = "ads_settings"
    key = Column(String(80), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime(timezone=True), default=now, onupdate=now)


class Event(Base):
    """Module-local audit trail. Mirrored into the Hub activity log when the
    Hub's own audit module is importable."""
    __tablename__ = "ads_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime(timezone=True), default=now, index=True)
    action = Column(String(80), default="", index=True)
    actor = Column(String(200), default="")
    details_json = Column(Text, default="{}")

    def as_dict(self) -> dict:
        return {
            "timestamp": self.created_at.isoformat() if self.created_at else None,
            "action": self.action,
            "actor": self.actor,
            "details": json.loads(self.details_json or "{}"),
        }


# Advisory-locked (two gunicorn workers issue this concurrently on every
# deploy) and reported rather than raised.
DB_BOOT_ERROR = create_all_metadata(Base.metadata)
if DB_BOOT_ERROR:
    log.error("ads_builder: table creation reported: %s", DB_BOOT_ERROR)

STATUSES = ("DRAFT", "IN_REVIEW", "CHANGES_REQUESTED", "APPROVED", "DEPLOYED", "ARCHIVED")
OPEN_STATUSES = ("DRAFT", "IN_REVIEW", "CHANGES_REQUESTED")


# --------------------------------------------------------------- settings
def get_setting(key: str) -> str:
    with SessionLocal() as s:
        row = s.get(Setting, key)
        return row.value if row else ""


def set_setting(key: str, value: str):
    with SessionLocal() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        s.commit()


# -------------------------------------------------------------- proposals
def new_public_id() -> str:
    return "prop_" + secrets.token_hex(5)


def create_proposal(client_name, campaign, created_by="", google_customer_id="") -> dict:
    with SessionLocal() as s:
        row = Proposal(
            public_id=new_public_id(),
            client_name=client_name or "",
            google_customer_id=google_customer_id or "",
            created_by=created_by or "",
            status="DRAFT",
            campaign_json=json.dumps(campaign),
            comments_json="[]",
        )
        s.add(row)
        s.commit()
        return row.as_dict()


def list_proposals(limit=200, status=None) -> list:
    """Proposals, newest first, optionally narrowed to one status.

    Filtered in the query rather than by the caller: the scheduled sweep wants
    only DEPLOYED rows and reading the whole book to throw most of it away is a
    full campaign-blob deserialisation per proposal, twice a day, for nothing.
    """
    with SessionLocal() as s:
        query = select(Proposal).order_by(Proposal.updated_at.desc())
        if status:
            query = query.where(Proposal.status == str(status))
        return [r.as_dict() for r in s.scalars(query.limit(limit)).all()]


def get_proposal(public_id) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        return row.as_dict() if row else None


def set_status(public_id, status) -> dict | None:
    if status not in STATUSES:
        raise ValueError(f'Invalid status "{status}".')
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.status = status
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def add_comment(public_id, author, text) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        comments = json.loads(row.comments_json or "[]")
        comments.append({
            "author": author or "Team",
            "text": str(text)[:4000],
            "created_at": now().isoformat(),
        })
        row.comments_json = json.dumps(comments)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def set_customer_id(public_id, customer_id) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.google_customer_id = customer_id or ""
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def mark_deployed(public_id, deployment) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.status = "DEPLOYED"
        row.deployment_json = json.dumps(deployment)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def record_client_link(public_id, link: dict) -> dict | None:
    """Keep what the client join actually did, inside the campaign JSON.

    Not a new column: ``create_all()`` creates missing tables and never adds a
    column to an existing one, so a column added here would be silently absent
    on the live Postgres while every local test passed. The campaign blob is
    already there and already migrates itself.
    """
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        campaign = json.loads(row.campaign_json or "{}")
        campaign["clientLink"] = {**(campaign.get("clientLink") or {}), "result": link}
        row.campaign_json = json.dumps(campaign, default=str)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


def update_campaign(public_id, campaign: dict) -> dict | None:
    """Replace the campaign blob after an edit.

    The whole blob, because an edit reaches keywords, negatives, budget and the
    intake at once and a field-by-field API would have to be extended for every
    new thing a campaign carries. Callers hand back what they read.
    """
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return None
        row.campaign_json = json.dumps(campaign, default=str)
        row.updated_at = now()
        s.commit()
        return row.as_dict()


# ------------------------------------------------- live account monitoring
def deployed_accounts(limit=500) -> list:
    """Every live Google Ads account this Hub deployed, once each.

    "Live" is a DEPLOYED proposal carrying a customer id, which is the only
    thing here that says we actually put a campaign into somebody's account.
    Deduped on the account rather than the proposal, because two proposals for
    one client are one account to scan and scanning it twice spends the daily
    operation budget to learn the same thing.

    The client name is whichever proposal touched the account most recently —
    ``list_proposals`` orders on ``updated_at``, so a client renamed on a later
    proposal wins, which is the answer somebody reading a scan wants.
    """
    seen: dict[str, dict] = {}
    for row in list_proposals(limit=limit, status="DEPLOYED"):
        cid = "".join(ch for ch in str(row.get("google_customer_id") or "") if ch.isdigit())
        if not cid or cid in seen:
            continue
        seen[cid] = {"customer_id": cid,
                     "client_name": row.get("client_name") or "",
                     "proposal_id": row.get("id") or ""}
    return list(seen.values())


def record_optimization_run(customer_id, *, client_name="", date_range="",
                            result=None, error="", triggered="scheduled") -> dict:
    """Keep one account's scan. A failed scan is a row, not a silence."""
    result = result or {}
    items = result.get("items") or []
    with SessionLocal() as s:
        row = OptimizationRun(
            customer_id=str(customer_id or "")[:20],
            client_name=str(client_name or "")[:300],
            date_range=str(date_range or "")[:40],
            item_count=int(result.get("item_count") or len(items)),
            high_severity_count=sum(1 for i in items if i.get("severity") == "high"),
            triggered=str(triggered or "scheduled")[:20],
            items_json=json.dumps(result, default=str),
            error=str(error or "")[:2000],
        )
        s.add(row)
        s.commit()
        return row.as_dict()


def latest_optimization_run(customer_id, *, with_result=False) -> dict | None:
    cid = str(customer_id or "")
    with SessionLocal() as s:
        row = s.scalar(
            select(OptimizationRun)
            .where(OptimizationRun.customer_id == cid)
            .order_by(OptimizationRun.scanned_at.desc(), OptimizationRun.id.desc())
        )
        return row.as_dict(with_result=with_result) if row else None


def latest_optimization_runs(limit=100) -> list:
    """The newest run per account, for the panel that opens before anyone scans."""
    out: dict[str, dict] = {}
    with SessionLocal() as s:
        rows = s.scalars(
            select(OptimizationRun)
            .order_by(OptimizationRun.scanned_at.desc(), OptimizationRun.id.desc())
            .limit(max(1, int(limit)) * 10)
        ).all()
    for row in rows:
        cid = row.customer_id or ""
        if cid and cid not in out:
            out[cid] = row.as_dict()
        if len(out) >= limit:
            break
    return list(out.values())


# ---------------------------------------------------------- auto-apply
# The whole universe of findings unattended work may act on, and it is
# deliberately the two lowest-blast-radius ones. Adding a negative keyword
# stops spend on a term we are not bidding on deliberately; pausing a keyword
# keeps the criterion and its history and is one press in Google Ads to undo.
# Everything else in ACTION_CONFIRMATIONS -- applying one of Google's own
# recommendations, removing a criterion outright, creating sitelinks or images,
# changing a Target CPA -- either cannot be undone or changes how the account
# bids, and none of those belongs to a job nobody is watching.
AUTO_APPLY_CATEGORIES = ("keyword_pauses", "search_terms")

# One account, one run. A cap rather than a rate: what this is protecting
# against is one badly-measured account firing hundreds of mutates unattended,
# and whatever is left is offered again on the next sweep with a rep able to
# read it first.
AUTO_APPLY_MAX_PER_RUN = 10

# The category says which finding; this says which mutate. Both, because a
# category is a heading a detector chose and an action is what actually reaches
# Google -- a detector added under an allowed category tomorrow must not become
# an unattended write by inheriting the heading.
AUTO_APPLY_ACTIONS = ("add_negative_keyword", "pause_keyword")


def auto_apply_settings(customer_id) -> dict:
    """What unattended work may do to one account. Absent means no."""
    cid = str(customer_id or "")
    with SessionLocal() as s:
        row = s.get(AutoApply, cid)
        if row:
            return row.as_dict()
    return {"customer_id": cid, "enabled": False, "categories": [],
            "updated_at": None, "updated_by": ""}


def set_auto_apply(customer_id, *, enabled, categories=None, actor="") -> dict:
    """Turn it on or off for one account, and say which findings.

    A category outside AUTO_APPLY_CATEGORIES is dropped rather than stored: the
    allowlist is the safety rule, and a value that survived here would be one
    the sweep then had to re-check.
    """
    cid = str(customer_id or "").strip()
    if not cid:
        raise ValueError("customer_id is required.")
    kept = [c for c in (categories or []) if c in AUTO_APPLY_CATEGORIES]
    with SessionLocal() as s:
        row = s.get(AutoApply, cid)
        if not row:
            row = AutoApply(customer_id=cid)
            s.add(row)
        row.enabled = 1 if enabled else 0
        row.categories_json = json.dumps(kept)
        row.updated_by = str(actor or "")[:200]
        row.updated_at = now()
        s.commit()
        return row.as_dict()


# --------------------------------------------------- client estimate links
def create_share(public_id, created_by="") -> dict:
    """A fresh, unguessable link for one proposal.

    New each time rather than reused: a link that has been answered is a record
    of that answer, and handing the same URL to a second person would overwrite
    the first one's response with no trace of it.
    """
    token = secrets.token_urlsafe(24)
    with SessionLocal() as s:
        s.add(Share(token=token, public_id=public_id, created_by=created_by or ""))
        s.commit()
    return get_share(token)


def get_share(token) -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Share).where(Share.token == str(token or "")))
        return row.as_dict() if row else None


def shares_for(public_id) -> list:
    with SessionLocal() as s:
        rows = s.scalars(
            select(Share).where(Share.public_id == public_id).order_by(Share.created_at.desc())
        ).all()
        return [r.as_dict() for r in rows]


def latest_share(public_id) -> dict | None:
    rows = [r for r in shares_for(public_id) if not r["revoked"]]
    return rows[0] if rows else None


def note_share_opened(token) -> None:
    """A link that was never opened and one that was read and ignored are
    different situations, and only one of them means chase the email."""
    with SessionLocal() as s:
        row = s.scalar(select(Share).where(Share.token == str(token or "")))
        if row:
            row.opened_count = (row.opened_count or 0) + 1
            s.commit()


def revoke_share(token) -> bool:
    with SessionLocal() as s:
        row = s.scalar(select(Share).where(Share.token == str(token or "")))
        if not row:
            return False
        row.revoked = 1
        s.commit()
        return True


def add_change_request(token, section, text, name, email) -> dict | None:
    """One change a client asked for, against one section of the estimate."""
    with SessionLocal() as s:
        row = s.scalar(select(Share).where(Share.token == str(token or "")))
        if not row:
            return None
        items = json.loads(row.changes_json or "[]")
        items.append({
            "id": secrets.token_hex(6),
            "section": str(section or "")[:80],
            "text": str(text or "").strip()[:4000],
            "name": str(name or "").strip()[:200],
            "email": str(email or "").strip()[:200],
            "at": now().isoformat(),
        })
        row.changes_json = json.dumps(items)
        s.commit()
        return row.as_dict()


def record_response(token, outcome, name, email, note="") -> dict | None:
    with SessionLocal() as s:
        row = s.scalar(select(Share).where(Share.token == str(token or "")))
        if not row:
            return None
        row.outcome = str(outcome or "")[:40]
        row.reviewer_name = str(name or "").strip()[:200]
        row.reviewer_email = str(email or "").strip()[:200]
        row.reviewer_note = str(note or "").strip()[:4000]
        row.responded_at = now()
        s.commit()
        return row.as_dict()


def review_state(public_id) -> dict:
    """What the client has said about this proposal, for the approval hub.

    Answers even when nothing has been sent, so the hub never has to branch:
    ``outcome`` empty and ``colour`` grey is a real state — "not sent to the
    client yet" — and is different from red.
    """
    from .spec import NO_RESPONSE_COLOUR, outcome_colour
    rows = shares_for(public_id)
    live = [r for r in rows if not r["revoked"]]
    answered = [r for r in live if r["outcome"]]
    latest = answered[0] if answered else (live[0] if live else None)
    changes = sum(len(r["changes"]) for r in live)
    if not latest:
        return {"sent": False, "outcome": "", "color": NO_RESPONSE_COLOUR,
                "changes": 0, "reviewer": "", "responded_at": None, "opened": False}
    return {
        "sent": True,
        "outcome": latest["outcome"],
        "color": outcome_colour(latest["outcome"]),
        "changes": changes,
        "reviewer": latest["reviewer_name"] or latest["reviewer_email"],
        "responded_at": latest["responded_at"],
        "opened": bool(latest["opened_count"]),
    }


def delete_proposal(public_id) -> bool:
    with SessionLocal() as s:
        row = s.scalar(select(Proposal).where(Proposal.public_id == public_id))
        if not row:
            return False
        # The client links go with it. A live token pointing at a proposal that
        # no longer exists is a URL somebody has already emailed to a client,
        # and it would open on an error page with our name on it.
        for share in s.scalars(select(Share).where(Share.public_id == public_id)).all():
            s.delete(share)
        s.delete(row)
        s.commit()
        return True


# ------------------------------------------------------------------ audit
try:  # the Hub's shared activity log, when we are running inside the Hub
    from hub import audit as hub_audit
except Exception:  # noqa: BLE001 — standalone / dev
    hub_audit = None


def log_event(action, actor="System", **details) -> dict:
    """Record locally, then mirror into the Hub — and **report the mirror**.

    Returning it rather than swallowing it is the point: this mirror was broken
    for months and no screen could have said so, because a caller that ignores
    the result cannot tell "written" from "raised and caught". Anything that
    tells a person their work was filed on a client record has to be able to
    ask.
    """
    with SessionLocal() as s:
        s.add(Event(action=action, actor=actor or "System",
                    details_json=json.dumps(details, default=str)))
        s.commit()

    # audit.log(module, type_, actor=..., **extra) -- MODULE is the first
    # positional, and this used to pass "ads.<action>" as it with no type_ at
    # all. That is a TypeError, and the except below swallowed it, so every
    # event this module has ever recorded stopped at its own table: nothing
    # reached the Hub activity log, and nothing reached Client 360 even though
    # hub/client_brand.py has carried an "ads_builder" entry in WORK_KINDS the
    # whole time. It is the exact trap CLAUDE.md names, and it is silent by
    # construction -- the module's own Activity page looked complete.
    if hub_audit is None or not callable(getattr(hub_audit, "log", None)):
        return {"mirrored": False, "error": "The Hub activity log is not available here."}
    try:
        hub_audit.log("ads_builder", action.lower(), actor=actor, **details)
        return {"mirrored": True, "error": ""}
    except Exception as exc:  # noqa: BLE001 — never break a request over logging
        log.warning("ads_builder: activity log mirror failed: %s", exc)
        return {"mirrored": False, "error": str(exc)}


def list_events(limit=250) -> list:
    with SessionLocal() as s:
        rows = s.scalars(
            select(Event).order_by(Event.created_at.desc()).limit(limit)
        ).all()
        return [r.as_dict() for r in rows]
