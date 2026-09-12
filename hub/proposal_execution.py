"""Proposal Execution Center — turn one approved client proposal into a durable work graph.

The proposal is the source of truth for what was sold. This module sits above the
individual SmartHub tools: it parses the proposal, resolves shared client context,
creates dependency-aware tasks, and advances background-capable work through a
single queue. Anything that publishes, schedules client content, changes a live
campaign, or starts spend stops at an approval/handoff gate.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from hub.extensions import db

DRAFT = "draft"
NEEDS_INPUT = "needs_input"
READY = "ready"
QUEUED = "queued"
RUNNING = "running"
BLOCKED = "blocked"
NEEDS_APPROVAL = "needs_approval"
CHANGES_REQUESTED = "changes_requested"
APPROVED = "approved"
SCHEDULED = "scheduled"
LIVE = "live"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TASK_STATES = (DRAFT, NEEDS_INPUT, READY, QUEUED, RUNNING, BLOCKED,
               NEEDS_APPROVAL, CHANGES_REQUESTED, APPROVED, SCHEDULED,
               LIVE, COMPLETED, FAILED, CANCELLED)
FINAL_STATES = {LIVE, COMPLETED, CANCELLED}
RUN_DRAFT = "draft"
RUN_NEEDS_INPUT = "needs_input"
RUN_READY = "ready"
RUN_RUNNING = "running"
RUN_PAUSED = "paused"
RUN_ATTENTION = "attention"
RUN_COMPLETED = "completed"
RUN_SUPERSEDED = "superseded"
MAX_ATTEMPTS = 3
STALE_RUNNING_MINUTES = 10


def _now():
    return datetime.now(timezone.utc)


def _loads(value, default):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _dumps(value):
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


class ProposalExecutionRun(db.Model):
    __tablename__ = "hub_proposal_execution_runs"
    id = db.Column(db.Integer, primary_key=True)
    client = db.Column(db.String(240), nullable=False, index=True)
    proposal_id = db.Column(db.String(120), default="", index=True)
    proposal_title = db.Column(db.String(300), default="")
    proposal_filename = db.Column(db.String(300), default="")
    source_hash = db.Column(db.String(64), default="", index=True)
    state = db.Column(db.String(30), default=RUN_DRAFT, index=True)
    owner = db.Column(db.String(240), default="")
    paused = db.Column(db.Boolean, default=False)
    analysis_json = db.Column(db.Text, default="{}")
    context_json = db.Column(db.Text, default="{}")
    inputs_json = db.Column(db.Text, default="{}")
    previous_run_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=_now, index=True)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def analysis(self): return _loads(self.analysis_json, {})
    def context(self): return _loads(self.context_json, {})
    def inputs(self): return _loads(self.inputs_json, {})

    def as_dict(self, full=False):
        row = {
            "id": self.id, "client": self.client, "proposal_id": self.proposal_id,
            "proposal_title": self.proposal_title, "proposal_filename": self.proposal_filename,
            "source_hash": self.source_hash, "state": self.state, "owner": self.owner,
            "paused": bool(self.paused), "previous_run_id": self.previous_run_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if full:
            row.update(analysis=self.analysis(), context=self.context(), inputs=self.inputs(),
                       summary=summary(self), missing_inputs=missing_input_manifest(self))
            row["tasks"] = [t.as_dict() for t in tasks_for_run(self.id)]
        return row


class ProposalExecutionTask(db.Model):
    __tablename__ = "hub_proposal_execution_tasks"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, nullable=False, index=True)
    task_key = db.Column(db.String(120), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    department = db.Column(db.String(80), default="")
    task_type = db.Column(db.String(80), default="")
    adapter = db.Column(db.String(80), default="brief")
    execution_mode = db.Column(db.String(20), default="auto")
    state = db.Column(db.String(30), default=DRAFT, index=True)
    dependencies_json = db.Column(db.Text, default="[]")
    missing_json = db.Column(db.Text, default="[]")
    payload_json = db.Column(db.Text, default="{}")
    result_json = db.Column(db.Text, default="{}")
    fingerprint = db.Column(db.String(64), default="")
    error = db.Column(db.Text, default="")
    attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=_now, index=True)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def depends(self): return _loads(self.dependencies_json, [])
    def missing(self): return _loads(self.missing_json, [])
    def payload(self): return _loads(self.payload_json, {})
    def result(self): return _loads(self.result_json, {})

    def as_dict(self):
        return {
            "id": self.id, "run_id": self.run_id, "task_key": self.task_key,
            "title": self.title, "department": self.department,
            "task_type": self.task_type, "adapter": self.adapter,
            "execution_mode": self.execution_mode, "state": self.state,
            "dependencies": self.depends(), "missing": self.missing(),
            "payload": self.payload(), "result": self.result(), "error": self.error or "",
            "attempts": self.attempts or 0,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class ProposalExecutionEvent(db.Model):
    __tablename__ = "hub_proposal_execution_events"
    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, nullable=False, index=True)
    task_id = db.Column(db.Integer, nullable=True, index=True)
    state = db.Column(db.String(30), default="")
    message = db.Column(db.Text, default="")
    actor = db.Column(db.String(240), default="")
    created_at = db.Column(db.DateTime, default=_now, index=True)

    def as_dict(self):
        return {"id": self.id, "run_id": self.run_id, "task_id": self.task_id,
                "state": self.state, "message": self.message, "actor": self.actor,
                "created_at": self.created_at.isoformat() if self.created_at else None}


def _event(run_id, state, message, task_id=None, actor=""):
    try:
        db.session.add(ProposalExecutionEvent(run_id=run_id, task_id=task_id,
                                              state=state, message=str(message)[:5000],
                                              actor=str(actor or "")[:240]))
        db.session.commit()
    except Exception:
        db.session.rollback()


def _extract_json(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        return json.loads(text)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except ValueError:
        return {}


CHANNEL_PATTERNS = {
    "retargeting": ("retarget", "website retargeting"),
    "paid_search": ("paid search", "ppc", "google ads"),
    "seo_ai": ("seo", "ai optimization", "ai maintenance", "search engine optimization"),
    "stadium_audio": ("stadium to screen", "stadium to speaker", "venue replay", "streaming audio"),
    "meta": ("meta", "facebook", "instagram", "in-market home buyers"),
    "youtube_ads": ("youtube in-market", "youtube advertising", "youtube ads"),
    "social": ("social posting", "social media", "social outline"),
    "youtube_video": ("youtube sales video", "monthly youtube sales video"),
    "youtube_optimization": ("youtube channel optimization",),
    "ai_ads": ("chatgpt / ai advertising", "chatgpt advertising", "ai advertising"),
}

CHANNEL_LABELS = {
    "retargeting": "Website Retargeting", "paid_search": "Paid Search",
    "seo_ai": "SEO + AI", "stadium_audio": "Stadium to Screen",
    "meta": "Meta In-Market Home Buyers", "youtube_ads": "YouTube Advertising",
    "social": "Social Content", "youtube_video": "Monthly YouTube Sales Video",
    "youtube_optimization": "YouTube Channel Optimization", "ai_ads": "AI Advertising",
}


def _heuristic_analysis(text, client):
    low = text.lower()
    channels = []
    for key, patterns in CHANNEL_PATTERNS.items():
        if any(p in low for p in patterns):
            amounts = []
            for line in text.splitlines():
                if any(p in line.lower() for p in patterns):
                    amounts.extend(re.findall(r"\$[\d,]+(?:\.\d{2})?", line))
            channels.append({"key": key, "name": CHANNEL_LABELS[key],
                             "budgets": list(dict.fromkeys(amounts))[:6], "deliverables": []})
    facts = {}
    if "ohio state" in low or "buckeyes" in low: facts["team"] = "Ohio State Buckeyes"
    if "ohio stadium" in low: facts["venue"] = "Ohio Stadium"
    if "columbus, oh dma" in low or "columbus dma" in low: facts["market"] = "Columbus, OH DMA"
    totals = {}
    for label, key in (("current monthly foundation", "foundation"),
                       ("fall growth plan", "fall"), ("winter plan", "winter")):
        m = re.search(r"\$([\d,]+(?:\.\d{2})?)\s*" + re.escape(label), low, re.I)
        if m: totals[key] = "$" + m.group(1)
    return {"client": client, "channels": channels, "facts": facts,
            "headline_budgets": totals, "objectives": [], "audiences": [],
            "flight_dates": [], "recurring": [], "source": "heuristic"}


def analyze_text(text, client):
    base = _heuristic_analysis(text, client)
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        return base, "heuristic"
    prompt = f"""Read this marketing proposal and return ONLY JSON.
Do not infer products that are not stated. Preserve exact dollar amounts and dates.
Schema:
{{"client":"", "channels":[{{"key":"retargeting|paid_search|seo_ai|stadium_audio|meta|youtube_ads|social|youtube_video|youtube_optimization|ai_ads|other","name":"","budgets":[],"deliverables":[]}}],
"headline_budgets":{{}},"flight_dates":[],"objectives":[],"audiences":[],"recurring":[],
"facts":{{"team":"","venue":"","market":""}}}}
Client: {client}
Proposal:\n{text[:50000]}"""
    try:
        from hub.openai_responses import ask
        parsed = _extract_json(ask(prompt, module="proposal_execution",
                                   purpose="proposal_analysis", max_output_tokens=8000))
        if not isinstance(parsed, dict):
            raise ValueError("analysis was not an object")
        by_key = {c.get("key"): c for c in parsed.get("channels", []) if isinstance(c, dict)}
        for channel in base["channels"]:
            by_key.setdefault(channel["key"], channel)
        parsed["channels"] = list(by_key.values())
        parsed.setdefault("facts", {}).update({k: v for k, v in base["facts"].items()
                                                if not parsed.get("facts", {}).get(k)})
        parsed.setdefault("headline_budgets", {}).update({k: v for k, v in base["headline_budgets"].items()
                                                          if k not in parsed.get("headline_budgets", {})})
        parsed["source"] = "openai+heuristic"
        return parsed, "openai+heuristic"
    except Exception as exc:
        base["analysis_note"] = f"AI analysis unavailable: {type(exc).__name__}"
        return base, "heuristic"


INPUT_CATALOG = {
    "landing_url": {"label": "Primary landing URL", "type": "url",
                    "help": "Main destination for campaign traffic."},
    "target_geography": {"label": "Approved target geography", "type": "text",
                         "help": "DMA, ZIPs, radius or approved market."},
    "conversion_goal": {"label": "Primary conversion goal", "type": "text",
                        "help": "Lead form, call, tour request, appointment, sale, etc."},
    "primary_cta": {"label": "Primary CTA", "type": "text",
                    "help": "The action used consistently across creative and landing destinations."},
    "product_destinations": {"label": "Product/community destination URLs", "type": "textarea",
                             "help": "URLs for communities, available homes, floor plans or other proposal destinations."},
    "video_source": {"label": "Approved YouTube/video source", "type": "text",
                     "help": "Existing video URL/file reference, or note that new production is required."},
    "offer": {"label": "Approved offer", "type": "text",
              "help": "Leave blank when the proposal contains no offer; SmartHub will not invent one."},
}


def _task(key, title, dept, adapter="brief", mode="auto", depends=(), needs=(), channel=None, task_type="plan"):
    payload = {"channel": channel or {}}
    fingerprint = hashlib.sha256(_dumps({"k": key, "p": payload, "d": depends, "n": needs}).encode()).hexdigest()
    return {"key": key, "title": title, "department": dept, "adapter": adapter,
            "mode": mode, "depends": list(depends), "needs": list(needs),
            "payload": payload, "task_type": task_type, "fingerprint": fingerprint}


def build_task_specs(analysis):
    channels = {c.get("key"): c for c in analysis.get("channels", []) if isinstance(c, dict)}
    specs = [
        _task("campaign_foundation", "Campaign foundation & messaging", "Strategy", needs=("landing_url", "primary_cta", "conversion_goal")),
        _task("tracking_plan", "Tracking & conversion plan", "Analytics", depends=("campaign_foundation",), needs=("landing_url", "conversion_goal")),
        _task("budget_calendar", "Budget & flight calendar", "Strategy"),
        _task("reporting_plan", "Cross-channel reporting plan", "Reporting", depends=("tracking_plan",)),
    ]
    if "retargeting" in channels:
        c = channels["retargeting"]
        specs += [_task("retargeting_plan", "Retargeting audience & media plan", "Media", depends=("campaign_foundation",), needs=("landing_url", "target_geography"), channel=c),
                  _task("retargeting_creative", "Retargeting creative brief", "Creative", depends=("retargeting_plan",), channel=c),
                  _task("retargeting_activation", "Retargeting launch packet", "Ad Ops", "launch_packet", "handoff", ("retargeting_creative", "tracking_plan"), channel=c, task_type="activation")]
    if "paid_search" in channels:
        c = channels["paid_search"]
        specs += [_task("paid_search_plan", "Paid Search campaign structure", "Search", depends=("campaign_foundation",), needs=("landing_url", "target_geography", "conversion_goal"), channel=c),
                  _task("paid_search_ads", "Paid Search ad copy & extensions", "Search", depends=("paid_search_plan",), needs=("primary_cta",), channel=c),
                  _task("paid_search_activation", "Paid Search launch packet", "Ad Ops", "launch_packet", "handoff", ("paid_search_ads", "tracking_plan"), channel=c, task_type="activation")]
    if "seo_ai" in channels:
        c = channels["seo_ai"]
        specs += [_task("seo_audit", "SEO + AI discovery audit brief", "SEO", needs=("landing_url",), channel=c),
                  _task("seo_workplan", "SEO + AI prioritized workplan", "SEO", depends=("seo_audit",), channel=c),
                  _task("seo_implementation", "SEO implementation & QA packet", "SEO", "launch_packet", "handoff", ("seo_workplan",), channel=c, task_type="implementation")]
    if "stadium_audio" in channels:
        c = channels["stadium_audio"]
        specs += [_task("stadium_spec", "Stadium to Screen media spec", "Media", depends=("campaign_foundation",), needs=("target_geography",), channel=c),
                  _task("stadium_audio_scripts", "Football audio scripts", "Creative", "radio_scripts", "approval", ("stadium_spec",), needs=("landing_url",), channel=c, task_type="audio_scripts"),
                  _task("stadium_banners", "300x250 companion banner brief", "Creative", depends=("stadium_spec", "campaign_foundation"), needs=("landing_url", "primary_cta"), channel=c),
                  _task("stadium_activation", "Stadium/Venue Replay launch packet", "Ad Ops", "launch_packet", "handoff", ("stadium_audio_scripts", "stadium_banners", "tracking_plan"), channel=c, task_type="activation")]
    if "meta" in channels:
        c = channels["meta"]
        specs += [_task("meta_plan", "Meta home-buyer campaign plan", "Social Ads", depends=("campaign_foundation",), needs=("target_geography", "landing_url"), channel=c),
                  _task("meta_carousel", "Meta carousel copy & creative brief", "Creative", depends=("meta_plan",), needs=("product_destinations", "primary_cta"), channel=c),
                  _task("meta_activation", "Meta launch packet", "Ad Ops", "launch_packet", "handoff", ("meta_carousel", "tracking_plan"), channel=c, task_type="activation")]
    if "youtube_ads" in channels:
        c = channels["youtube_ads"]
        specs += [_task("youtube_ads_plan", "YouTube audience & media plan", "Video Ads", depends=("campaign_foundation",), needs=("target_geography", "landing_url"), channel=c),
                  _task("youtube_ads_creative", "YouTube ad creative brief", "Creative", depends=("youtube_ads_plan",), needs=("video_source", "primary_cta"), channel=c),
                  _task("youtube_ads_activation", "YouTube Ads launch packet", "Ad Ops", "launch_packet", "handoff", ("youtube_ads_creative", "tracking_plan"), channel=c, task_type="activation")]
    if "social" in channels:
        c = channels["social"]
        specs += [_task("social_calendar", "Monthly social content calendar", "Social", depends=("campaign_foundation",), channel=c),
                  _task("social_posts", "Social post copy & asset briefs", "Social", depends=("social_calendar",), channel=c),
                  _task("social_schedule", "Social scheduling packet", "Social", "launch_packet", "handoff", ("social_posts",), channel=c, task_type="scheduling")]
    if "youtube_video" in channels:
        c = channels["youtube_video"]
        specs += [_task("youtube_video_concept", "Monthly YouTube sales video concept & script", "Video", depends=("campaign_foundation",), needs=("primary_cta",), channel=c),
                  _task("youtube_video_production", "YouTube sales video production handoff", "Video", "launch_packet", "handoff", ("youtube_video_concept",), channel=c, task_type="production")]
    if "youtube_optimization" in channels:
        specs.append(_task("youtube_optimization", "YouTube channel optimization plan", "Video/SEO", needs=("landing_url",), channel=channels["youtube_optimization"]))
    if "ai_ads" in channels:
        c = channels["ai_ads"]
        specs += [_task("ai_ads_plan", "AI advertising test plan", "Media", depends=("campaign_foundation",), needs=("landing_url", "conversion_goal"), channel=c),
                  _task("ai_ads_activation", "AI advertising test launch packet", "Ad Ops", "launch_packet", "handoff", ("ai_ads_plan", "tracking_plan"), channel=c, task_type="activation")]
    return specs


def _client_context(client):
    data = {}
    try:
        from hub.client_context import context
        data = context(client) or {}
    except Exception as exc:
        data = {"providers": {"client_context": f"error: {type(exc).__name__}"}, "fields": {}}
    fields = data.get("fields") or {}
    try:
        from hub import google_index
        data["google"] = google_index.for_client(client, fields.get("website", ""))
    except Exception as exc:
        data["google"] = {"error": type(exc).__name__}
    return data


def _initial_inputs(context_data, analysis):
    fields = context_data.get("fields") or {}
    facts = analysis.get("facts") or {}
    return {
        "landing_url": fields.get("website") or "",
        "target_geography": facts.get("market") or "",
        "conversion_goal": "",
        "primary_cta": "",
        "product_destinations": "",
        "video_source": "",
        "offer": "",
        "team": facts.get("team") or "",
        "venue": facts.get("venue") or "",
        "market": facts.get("market") or "",
        "phone": fields.get("phone") or "",
        "logo_url": fields.get("logo_url") or "",
        "brand_primary_color": fields.get("brand_primary_color") or "",
    }


def _proposal_record(client, proposal_id):
    from hub import proposals
    for rec in proposals.list_proposals(client):
        if str(rec.get("id") or "") == str(proposal_id) or rec.get("filename") == proposal_id:
            return rec
    return None


class ProposalRunConflict(ValueError):
    """A newer proposal was analyzed while an earlier run for it is still open.

    An identical file re-analyzed returns the existing run (unchanged
    behaviour). A *different* file for what reads as the same engagement —
    same stored proposal id, or a title that normalises to the same string
    once the "(1)", "updated", "v2" and date noise a re-upload picks up is
    stripped — used to happily start a second run beside one still in
    progress, with its own thirty tasks and its own notifications. This is
    raised instead, carrying enough for the caller to offer exactly two
    doors: open the run that is already there, or supersede it and carry
    the approved work forward. Never a third door that quietly starts a
    parallel run.
    """

    def __init__(self, existing_run,
                 message="This proposal has a newer version pending review."):
        self.existing_run_id = existing_run.id
        self.existing_state = existing_run.state
        self.existing_progress = summary(existing_run).get("progress", 0)
        self.changed = True
        super().__init__(message)

    def payload(self):
        return {"existing_run_id": self.existing_run_id,
                "existing_state": self.existing_state,
                "existing_progress": self.existing_progress,
                "changed": self.changed}


_TITLE_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|txt)$", re.I)
_TITLE_PAREN_RE = re.compile(r"\([^)]*\)")
_TITLE_WORD_RE = re.compile(r"\b(updated|update|revised|revision|final|draft|copy)\b", re.I)
_TITLE_VERSION_RE = re.compile(r"\bv\d+(?:\.\d+)?\b", re.I)
_TITLE_DATE_RE = re.compile(r"\b\d{4}[-_/]\d{1,2}[-_/]\d{1,2}\b|\b\d{1,2}[-_/]\d{1,2}[-_/]\d{2,4}\b")


def _normalize_title(title):
    """Same proposal, re-typed or re-exported, should compare equal.

    Strips the noise a re-upload of the same engagement picks up — a
    trailing "(1)", "updated"/"v2", a date, the file extension — so
    ``Monogram_Homes_2026_2027_Marketing_Proposal(1).pdf`` and
    ``Monogram_Homes_2026_2027_Marketing_Proposal.pdf`` normalise the same.
    """
    text = _TITLE_EXT_RE.sub("", str(title or ""))
    text = _TITLE_PAREN_RE.sub(" ", text)
    text = _TITLE_WORD_RE.sub(" ", text)
    text = _TITLE_VERSION_RE.sub(" ", text)
    text = _TITLE_DATE_RE.sub(" ", text)
    return re.sub(r"[\s_\-]+", " ", text).strip().lower()


def _is_open(run):
    if run.state in {RUN_COMPLETED, RUN_SUPERSEDED}:
        return False
    tasks = tasks_for_run(run.id)
    return not (tasks and all(t.state in FINAL_STATES for t in tasks))


def _find_open_run(client, proposal_id, proposal_title):
    """The open run for this client this proposal already belongs to, if any."""
    norm_title = _normalize_title(proposal_title)
    for run in (ProposalExecutionRun.query.filter_by(client=client)
                .order_by(ProposalExecutionRun.id.desc()).all()):
        matches = (bool(proposal_id) and run.proposal_id == proposal_id) or \
                  (bool(norm_title) and _normalize_title(run.proposal_title) == norm_title)
        if matches and _is_open(run):
            return run
    return None


def _describe_channel_change(old_channel, new_channel):
    old_budgets, new_budgets = old_channel.get("budgets") or [], new_channel.get("budgets") or []
    if old_budgets != new_budgets:
        return f"budget changed {' / '.join(old_budgets) or 'none stated'} → {' / '.join(new_budgets) or 'none stated'}"
    old_name, new_name = old_channel.get("name") or "", new_channel.get("name") or ""
    if old_name != new_name:
        return f'scope changed "{old_name}" → "{new_name}"'
    return "channel details changed"


def _carry_over(old_run, new_run):
    """Move a superseded run's shared inputs and reviewed work onto its successor.

    Shared inputs travel wholesale -- a client's phone number and CTA do not
    change because a budget line did. A task the old run had already carried
    to approval, a handoff or completion carries its result across too, but
    only where the channel it was built for is byte-identical in the new
    graph; anything the new proposal actually changed is left exactly where
    a brand-new task starts, with an event naming what moved instead of
    silently discarding a decision nobody re-made.
    """
    new_run.inputs_json = _dumps(old_run.inputs())
    db.session.commit()
    old_by_key = {t.task_key: t for t in tasks_for_run(old_run.id)}
    for new_task in tasks_for_run(new_run.id):
        old_task = old_by_key.get(new_task.task_key)
        if not old_task:
            continue
        old_channel = (old_task.payload() or {}).get("channel") or {}
        new_channel = (new_task.payload() or {}).get("channel") or {}
        if old_task.state in {APPROVED, LIVE, COMPLETED, NEEDS_APPROVAL} and old_channel == new_channel:
            new_task.state = old_task.state
            new_task.result_json = old_task.result_json
            new_task.attempts = old_task.attempts
            db.session.commit()
            _event(new_run.id, new_task.state, f"Carried over from run #{old_run.id}.", new_task.id)
        elif old_channel != new_channel:
            _event(new_run.id, new_task.state,
                   f"Re-planned: {_describe_channel_change(old_channel, new_channel)}.", new_task.id)


def create_run(client, proposal_id, *, owner="", actor="", force=False, supersede=False):
    client = str(client or "").strip()
    proposal_id = str(proposal_id or "").strip()
    if not client or not proposal_id:
        raise ValueError("Choose a client and proposal first.")
    rec = _proposal_record(client, proposal_id)
    if not rec or rec.get("kind") == "link":
        raise ValueError("That uploaded proposal could not be read as a document.")
    from hub import _proposal_text_for
    text = _proposal_text_for(client, rec.get("id") or rec.get("filename") or proposal_id)
    if not text.strip():
        raise ValueError("No readable text was found in that proposal. It may be a scanned image-only PDF.")
    source_hash = hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    resolved_proposal_id = str(rec.get("id") or proposal_id)
    proposal_title = rec.get("title") or rec.get("filename") or "Proposal"

    if not force:
        existing = (ProposalExecutionRun.query
                    .filter_by(client=client, proposal_id=resolved_proposal_id, source_hash=source_hash)
                    .order_by(ProposalExecutionRun.id.desc()).first())
        if existing:
            return existing, False

    conflict = None if force else _find_open_run(client, resolved_proposal_id, proposal_title)
    if conflict and conflict.source_hash != source_hash and not supersede:
        raise ProposalRunConflict(conflict)

    previous = conflict or (ProposalExecutionRun.query.filter_by(client=client)
                            .order_by(ProposalExecutionRun.id.desc()).first())
    analysis, method = analyze_text(text, client)
    analysis["analysis_method"] = method
    context_data = _client_context(client)
    run = ProposalExecutionRun(client=client, proposal_id=resolved_proposal_id,
                               proposal_title=proposal_title,
                               proposal_filename=rec.get("filename") or "", source_hash=source_hash,
                               state=RUN_DRAFT, owner=owner,
                               previous_run_id=previous.id if previous else None,
                               analysis_json=_dumps(analysis), context_json=_dumps(context_data),
                               inputs_json=_dumps(_initial_inputs(context_data, analysis)))
    db.session.add(run)
    db.session.flush()
    for spec in build_task_specs(analysis):
        row = ProposalExecutionTask(run_id=run.id, task_key=spec["key"], title=spec["title"],
                                    department=spec["department"], task_type=spec["task_type"],
                                    adapter=spec["adapter"], execution_mode=spec["mode"],
                                    state=DRAFT, dependencies_json=_dumps(spec["depends"]),
                                    missing_json=_dumps(spec["needs"]), payload_json=_dumps(spec["payload"]),
                                    fingerprint=spec["fingerprint"])
        db.session.add(row)
    db.session.commit()
    superseded_note = ""
    if conflict and supersede and conflict.id != run.id:
        _carry_over(conflict, run)
        pause_run(conflict.id, actor=actor)
        conflict.state = RUN_SUPERSEDED
        db.session.commit()
        _event(conflict.id, RUN_SUPERSEDED, f"Superseded by run #{run.id}.", actor=actor)
        superseded_note = f" Superseded run #{conflict.id}."
    _revalidate_run(run)
    _event(run.id, run.state,
           f"Analyzed proposal and created {len(build_task_specs(analysis))} execution tasks.{superseded_note}",
           actor=actor)
    return run, True


def get_run(run_id):
    return ProposalExecutionRun.query.get(int(run_id))


def list_runs(client="", limit=50):
    q = ProposalExecutionRun.query
    if client:
        q = q.filter(ProposalExecutionRun.client.ilike(str(client).strip()))
    return [r.as_dict() for r in q.order_by(ProposalExecutionRun.updated_at.desc(), ProposalExecutionRun.id.desc()).limit(max(1, min(int(limit), 200))).all()]


def tasks_for_run(run_id):
    return (ProposalExecutionTask.query.filter_by(run_id=int(run_id))
            .order_by(ProposalExecutionTask.id.asc()).all())


def _resolved(value):
    if value is None: return False
    if isinstance(value, str): return bool(value.strip())
    if isinstance(value, (list, dict)): return bool(value)
    return True


def _dependencies_satisfied(task, by_key):
    for key in task.depends():
        dep = by_key.get(key)
        if not dep or dep.state not in {APPROVED, SCHEDULED, LIVE, COMPLETED}:
            return False
    return True


def _revalidate_run(run):
    tasks = tasks_for_run(run.id)
    values = run.inputs()
    by_key = {t.task_key: t for t in tasks}
    for task in tasks:
        if task.state in FINAL_STATES | {RUNNING, NEEDS_APPROVAL, APPROVED, FAILED, CHANGES_REQUESTED}:
            continue
        missing = [k for k in task.missing() if not _resolved(values.get(k))]
        if missing:
            task.state = NEEDS_INPUT
        elif not _dependencies_satisfied(task, by_key):
            task.state = BLOCKED
        else:
            task.state = QUEUED if run.state == RUN_RUNNING and not run.paused else READY
    states = {t.state for t in tasks}
    if run.paused:
        run.state = RUN_PAUSED
    elif NEEDS_INPUT in states and run.state != RUN_RUNNING:
        run.state = RUN_NEEDS_INPUT
    elif run.state != RUN_RUNNING:
        run.state = RUN_READY
    db.session.commit()


def missing_input_manifest(run):
    values = run.inputs()
    used = {}
    for task in tasks_for_run(run.id):
        if task.state in FINAL_STATES:
            continue
        for key in task.missing():
            if not _resolved(values.get(key)):
                used.setdefault(key, []).append(task.title)
    out = []
    for key, titles in used.items():
        meta = INPUT_CATALOG.get(key, {"label": key.replace("_", " ").title(), "type": "text", "help": "Required input."})
        out.append({"key": key, "label": meta["label"], "type": meta["type"], "help": meta["help"], "value": values.get(key, ""), "used_by": titles})
    return out


def update_inputs(run_id, values, *, actor=""):
    run = get_run(run_id)
    if not run: raise ValueError("That execution run could not be found.")
    current = run.inputs()
    for key, value in (values or {}).items():
        if key in INPUT_CATALOG or key in {"team", "venue", "market", "phone", "logo_url", "brand_primary_color"}:
            current[key] = value.strip() if isinstance(value, str) else value
    run.inputs_json = _dumps(current)
    db.session.commit()
    _revalidate_run(run)
    _event(run.id, run.state, "Updated shared campaign inputs.", actor=actor)
    return run


def start_run(run_id, *, actor=""):
    run = get_run(run_id)
    if not run: raise ValueError("That execution run could not be found.")
    run.paused = False
    run.state = RUN_RUNNING
    db.session.commit()
    _revalidate_run(run)
    _event(run.id, RUN_RUNNING, "Batch execution started.", actor=actor)
    return run


def pause_run(run_id, *, actor=""):
    run = get_run(run_id)
    if not run: raise ValueError("That execution run could not be found.")
    run.paused = True
    run.state = RUN_PAUSED
    for t in tasks_for_run(run.id):
        if t.state == QUEUED: t.state = READY
    db.session.commit()
    _event(run.id, RUN_PAUSED, "Batch execution paused.", actor=actor)
    return run


def retry_failed(run_id, *, actor=""):
    run = get_run(run_id)
    if not run: raise ValueError("That execution run could not be found.")
    for t in tasks_for_run(run.id):
        if t.state == FAILED:
            t.state, t.attempts, t.error = READY, 0, ""
    run.paused, run.state = False, RUN_RUNNING
    db.session.commit()
    _revalidate_run(run)
    _event(run.id, RUN_RUNNING, "Failed tasks returned to the queue.", actor=actor)
    return run


def summary(run):
    tasks = tasks_for_run(run.id) if run.id else []
    counts = {s: 0 for s in TASK_STATES}
    for t in tasks: counts[t.state] = counts.get(t.state, 0) + 1
    done = sum(1 for t in tasks if t.state in FINAL_STATES)
    return {"task_count": len(tasks), "progress": round(done * 100 / len(tasks)) if tasks else 0,
            "counts": counts, "attention_count": counts.get(NEEDS_INPUT, 0) + counts.get(NEEDS_APPROVAL, 0) + counts.get(CHANGES_REQUESTED, 0) + counts.get(FAILED, 0)}


@dataclass(frozen=True)
class Adapter:
    key: str
    label: str
    execution_mode: str
    runner: Callable


_ADAPTERS = {}

def register_adapter(adapter): _ADAPTERS[adapter.key] = adapter

def adapters(): return [{"key": a.key, "label": a.label, "execution_mode": a.execution_mode} for a in _ADAPTERS.values()]


def _brief_runner(run, task):
    fallback = {"summary": f"Prepared the {task.title} working brief from the proposal.",
                "deliverables": (task.payload().get("channel") or {}).get("deliverables") or [],
                "checklist": ["Confirm proposal scope and budget.", "Use shared campaign inputs.", "Complete channel QA before publishing or launch."],
                "qa": ["Destination and CTA match the plan.", "Targeting and budget match the proposal.", "Tracking is confirmed before launch."],
                "generated_by": "template"}
    if not os.environ.get("OPENAI_API_KEY", "").strip(): return fallback
    prompt = f"""Return ONLY JSON with keys summary, deliverables, checklist, creative_or_copy, qa, handoff_notes.
Create the internal working deliverable for this Smart 1 Marketing execution task. Use only facts below; invent no offers, dates, prices, URLs, claims, access or guarantees.
Task: {task.title}\nDepartment: {task.department}\nChannel: {_dumps(task.payload())}\nShared inputs: {_dumps(run.inputs())}\nProposal analysis: {_dumps(run.analysis())[:16000]}"""
    try:
        from hub.openai_responses import ask
        parsed = _extract_json(ask(prompt, module="proposal_execution", purpose=task.task_type, max_output_tokens=5000))
        if parsed:
            parsed["generated_by"] = "openai"
            return parsed
    except Exception as exc:
        fallback["generation_note"] = f"AI draft unavailable: {type(exc).__name__}"
    return fallback


def _radio_runner(run, task):
    from modules.radio_scripts import api as radio_api
    facts, inputs = run.analysis().get("facts") or {}, run.inputs()
    brief = {"company": run.client, "client_name": run.client,
             "market": inputs.get("market") or inputs.get("target_geography") or facts.get("market") or "",
             "team": inputs.get("team") or facts.get("team") or "",
             "package": (task.payload().get("channel") or {}).get("name") or "Stadium to Screen",
             "local_shows": [], "offer": inputs.get("offer") or "", "phone": inputs.get("phone") or "",
             "url": inputs.get("landing_url") or "", "funnel": {}, "lead_id": f"proposal-run-{run.id}"}
    row = radio_api.create_set(brief, actor="proposal-execution")
    return {"summary": f"Generated {len(row.concepts())} radio concept(s).", "set_id": row.id,
            "artifact_url": f"/tools/radio-scripts?set={row.id}",
            "qa": ["Review facts, timing, CTA and pronunciation before voice production."]}


def _launch_runner(run, task):
    by_key = {t.task_key: t for t in tasks_for_run(run.id)}
    upstream = [{"task": by_key[k].title, "state": by_key[k].state, "result": by_key[k].result()} for k in task.depends() if k in by_key]
    return {"summary": f"{task.title} is prepared as a human launch/handoff packet. SmartHub has not published, scheduled, changed a live campaign or started spend.",
            "client": run.client, "proposal": run.proposal_title, "inputs": run.inputs(),
            "channel": task.payload().get("channel") or {}, "upstream": upstream, "handoff": True,
            "checklist": ["Confirm approved creative and destination.", "Confirm targeting, dates and budget.", "Confirm conversion tracking.", "Launch only after approval is recorded here.", "Return and mark Live or Completed after the external action."]}

register_adapter(Adapter("brief", "SmartHub Working Brief", "auto", _brief_runner))
register_adapter(Adapter("radio_scripts", "Radio Scripts", "approval", _radio_runner))
register_adapter(Adapter("launch_packet", "Human Launch Packet", "handoff", _launch_runner))


def _notify(task, kind):
    run = get_run(task.run_id)
    if not run or not run.owner: return
    try:
        from hub import job_notify
        label = f"{run.client}: {task.title} " + ("is ready for approval" if kind == "approval" else "failed and needs attention")
        job_notify.register(owner=run.owner, tool="proposal-execution", label=label,
                            return_url=f"/proposal-execution?run={run.id}&task={task.id}",
                            status="done" if kind == "approval" else "failed",
                            id=f"pex-{task.id}-{kind}")
    except Exception:
        pass


def _reclaim_stale():
    cutoff = _now() - timedelta(minutes=STALE_RUNNING_MINUTES)
    rows = ProposalExecutionTask.query.filter(ProposalExecutionTask.state == RUNNING,
                                               ProposalExecutionTask.updated_at < cutoff).all()
    for t in rows:
        t.state, t.error = QUEUED, "Reclaimed after a worker stopped during this task."
    if rows: db.session.commit()


def run_one():
    _reclaim_stale()
    candidates = ProposalExecutionTask.query.filter_by(state=QUEUED).order_by(ProposalExecutionTask.created_at.asc()).limit(25).all()
    task = None
    for candidate in candidates:
        run = get_run(candidate.run_id)
        # A superseded run never leaves a task QUEUED -- pause_run() flips
        # every QUEUED task to READY before the state below is set -- and
        # this check excludes it a second time regardless: RUN_SUPERSEDED
        # is never RUN_RUNNING, so its tasks are inert here by construction.
        if run and run.state == RUN_RUNNING and not run.paused:
            task = candidate; break
    if not task: return {"claimed": 0}
    run = get_run(task.run_id)
    task.state = RUNNING
    db.session.commit()
    _event(run.id, RUNNING, f"Started {task.title}.", task.id, "scheduler")
    adapter = _ADAPTERS.get(task.adapter)
    if not adapter:
        task.state, task.error = FAILED, f"No adapter registered for {task.adapter}."
        db.session.commit(); _notify(task, "failed")
        return {"claimed": 1, "ok": False, "task_id": task.id}
    try:
        result = adapter.runner(run, task) or {}
    except Exception as exc:
        task.attempts = (task.attempts or 0) + 1
        task.error = f"{type(exc).__name__}: {exc}"[:3000]
        task.state = QUEUED if task.attempts < MAX_ATTEMPTS else FAILED
        db.session.commit()
        _event(run.id, task.state, f"{task.title}: {task.error[:400]}", task.id, "scheduler")
        if task.state == FAILED: _notify(task, "failed")
        return {"claimed": 1, "ok": False, "task_id": task.id, "gave_up": task.state == FAILED}
    task.result_json = _dumps(result)
    task.error = ""
    task.state = COMPLETED if task.execution_mode == "auto" else NEEDS_APPROVAL
    db.session.commit()
    _event(run.id, task.state, f"{task.title} finished background work.", task.id, "scheduler")
    if task.state == NEEDS_APPROVAL: _notify(task, "approval")
    _revalidate_run(run)
    if all(t.state in FINAL_STATES for t in tasks_for_run(run.id)):
        run.state = RUN_COMPLETED; db.session.commit()
    return {"claimed": 1, "ok": True, "task_id": task.id, "state": task.state}


def _get_task(task_id):
    row = ProposalExecutionTask.query.get(int(task_id))
    if not row: raise ValueError("That execution task could not be found.")
    return row


def approve_task(task_id, *, actor=""):
    task = _get_task(task_id)
    if task.state not in {NEEDS_APPROVAL, CHANGES_REQUESTED}: raise ValueError("That task is not waiting for approval.")
    task.state, task.error = APPROVED, ""; db.session.commit()
    _event(task.run_id, APPROVED, f"Approved {task.title}.", task.id, actor)
    run = get_run(task.run_id); _revalidate_run(run)
    return task


def request_changes(task_id, feedback, *, actor=""):
    task = _get_task(task_id)
    if task.state not in {NEEDS_APPROVAL, APPROVED}: raise ValueError("Changes can only be requested on a reviewable task.")
    payload = task.payload(); payload["change_request"] = str(feedback or "").strip()[:4000]
    task.payload_json, task.state = _dumps(payload), CHANGES_REQUESTED; db.session.commit()
    _event(task.run_id, CHANGES_REQUESTED, f"Changes requested on {task.title}: {str(feedback)[:400]}", task.id, actor)
    return task


def rerun_task(task_id, *, actor=""):
    task = _get_task(task_id)
    if task.state not in {FAILED, CHANGES_REQUESTED, NEEDS_APPROVAL, APPROVED}: raise ValueError("That task cannot be re-run from its current state.")
    task.state, task.attempts, task.error = QUEUED, 0, ""; db.session.commit()
    run = get_run(task.run_id); run.state, run.paused = RUN_RUNNING, False; db.session.commit()
    _event(task.run_id, QUEUED, f"Re-queued {task.title}.", task.id, actor)
    return task


def mark_task(task_id, state, *, actor=""):
    if state not in {LIVE, COMPLETED, SCHEDULED, CANCELLED}: raise ValueError("Unsupported task state.")
    task = _get_task(task_id)
    if task.execution_mode == "handoff" and task.state not in {APPROVED, LIVE, SCHEDULED, COMPLETED}:
        raise ValueError("Approve the handoff packet before marking the external action complete.")
    task.state = state; db.session.commit()
    _event(task.run_id, state, f"Marked {task.title} {state}.", task.id, actor)
    run = get_run(task.run_id); _revalidate_run(run)
    return task


def events_for_run(run_id, limit=100):
    return [e.as_dict() for e in ProposalExecutionEvent.query.filter_by(run_id=int(run_id)).order_by(ProposalExecutionEvent.created_at.desc()).limit(max(1, min(int(limit), 300))).all()]


def proposal_choices(client):
    from hub import proposals
    return [{"id": r.get("id") or r.get("filename"), "title": r.get("title") or r.get("filename") or "Proposal",
             "filename": r.get("filename") or "", "date_sent": r.get("date_sent") or "", "quote_number": r.get("quote_number") or "", "status": r.get("status") or ""}
            for r in proposals.list_proposals(client) if r.get("kind") != "link"]


def install_scheduler_bridge():
    """Kept as a no-op so nothing that calls it has to change.

    This used to rebind ``hub.creative_jobs.run_one`` to a wrapper returning
    ``{"creative": ..., "proposal_execution": ...}``, which advanced a proposal
    task on the creative queue's tick and cost a great deal more than it
    bought: *every* caller of that function got a different shape, from the
    moment the routes registered, whether or not it had heard of this module.
    `hub/scheduler.py` reports what it returns onto the scheduler panel, and
    `test_creative_jobs.py` reads `claimed` off it -- which stopped being there
    and raised `KeyError: 'claimed'`, invisibly, because CI aborts at its first
    failing step and had not reached that one.

    The reasoning behind the bridge was right and is kept: the scheduler
    already holds the leader lock, the retry cadence and the deploy safety this
    queue needs, and a second scheduler would be worse. What moved is *where*
    the fan-out happens -- `hub/scheduler.job_creative_queue()` calls both
    halves and labels each, so there is one reader of "what did this tick do"
    rather than a public function whose contract depends on which modules
    happen to have registered.
    """
    return None


__all__ = ["ProposalExecutionRun", "ProposalExecutionTask", "ProposalExecutionEvent",
           "ProposalRunConflict", "create_run", "get_run", "list_runs", "tasks_for_run",
           "proposal_choices", "update_inputs", "start_run", "pause_run", "retry_failed",
           "run_one", "approve_task", "request_changes", "rerun_task", "mark_task",
           "events_for_run", "missing_input_manifest", "summary", "adapters",
           "build_task_specs", "analyze_text", "install_scheduler_bridge", "NEEDS_INPUT",
           "RUNNING", "NEEDS_APPROVAL", "FAILED", "COMPLETED", "LIVE", "SCHEDULED",
           "APPROVED", "CANCELLED", "RUN_SUPERSEDED"]