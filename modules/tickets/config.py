"""Configuration for the Web Tickets audit module.

Everything here is overridable by environment variable or, for the field map,
by the mapping saved from /tools/tickets/setup. Nothing about the Knack schema
is hard-coded, because the field keys differ per app.
"""
import json
import os
from hub import jsonstore

# --------------------------------------------------------------------------
# Knack connection. Reused from the Hub when merged (hub/knack_api.py already
# holds these); read from env here so the module also runs standalone.
# --------------------------------------------------------------------------
KNACK_APP_ID = os.environ.get("KNACK_APP_ID", "")
KNACK_API_KEY = os.environ.get("KNACK_API_KEY", "")
KNACK_BASE = os.environ.get("KNACK_BASE", "https://api.knack.com/v1")

# Object containing web tickets. Defaults to the same object the rest of the
# Hub writes tickets to — this module used to default to "" and then tell you
# to go and map it, on a deployment where hub/knack_api.py had the answer
# pinned all along.
def _hub_tickets_object() -> str:
    try:
        from hub import knack_api
        return knack_api.TICKETS_OBJECT
    except Exception:                    # noqa: BLE001 — standalone use
        return "object_107"


TICKETS_OBJECT = os.environ.get("KNACK_TICKETS_OBJECT", "") or _hub_tickets_object()
# Object containing clients. Left blank when the Hub's own client registry is used.
CLIENTS_OBJECT = os.environ.get("KNACK_CLIENTS_OBJECT", "")

# Where the saved field map and the ticket snapshot live.
# Was a bare relative "data/tickets", which on Render is inside the container
# and therefore wiped by every deploy — the field map saved on the setup page
# lasted until the next ship, then silently fell back to the env defaults with
# nothing to say it had. jsonstore's root is the mounted disk.
DATA_DIR = os.environ.get("TICKETS_DATA_DIR", jsonstore.data_dir("tickets"))

# How long a fetched ticket set is reused before Knack is hit again (seconds).
CACHE_TTL = int(os.environ.get("TICKETS_CACHE_TTL", "900"))

# --------------------------------------------------------------------------
# Logical fields. The setup page maps each of these to a real Knack field key.
# `required` fields are the ones the audit genuinely cannot run without.
# `guesses` drive the auto-detect on the setup page.
# --------------------------------------------------------------------------
# The ids, read from the one place that holds them. This module and
# hub/knack_api.py describe the same Knack object, and for a while they each
# kept their own copy of its ids — two maps of one object, agreeing only for
# as long as somebody kept them in step. The audit's own names for the fields
# stay (its reports are written against `summary`, `details`, `assignee`), but
# the ids come from hub.knack_api.TICKET_FIELDS, and the environment overrides
# that module already honours therefore work here too.
#
# Label guessing stays as the fallback for the fields nobody has pinned —
# ticket_number, the dates, priority, time spent — and guessing is exactly
# what left the Issue column empty on the Accounting report when a label was
# renamed, which is why anything pinned wins outright.
_SHARED = {
    "summary": "title", "client": "client", "website": "website",
    "type": "type", "billable": "billable", "details": "description",
    "assignee": "assigner", "status": "status", "developer": "developer",
}


def _confirmed_fields() -> dict:
    try:
        from hub import knack_api
        ids = knack_api.field_ids()
    except Exception:                    # noqa: BLE001 — standalone use
        return {}
    return {mine: ids[theirs] for mine, theirs in _SHARED.items() if ids.get(theirs)}


CONFIRMED_FIELDS = _confirmed_fields()

FIELDS = [
    dict(key="ticket_number", label="Ticket number", required=False,
         guesses=["ticket number", "ticket id", "ticket #", "number", "id", "ref"]),
    dict(key="client", label="Client", required=True,
         guesses=["client", "customer", "account", "company", "connected client"]),
    dict(key="status", label="Status", required=True,
         guesses=["status", "state", "stage", "ticket status"]),
    dict(key="opened", label="Date opened", required=True,
         guesses=["date opened", "opened", "created", "submitted", "date submitted",
                  "request date", "date"]),
    dict(key="closed", label="Date closed", required=False,
         guesses=["date closed", "closed", "completed", "resolved", "date completed"]),
    dict(key="last_update", label="Last updated", required=False,
         guesses=["last updated", "updated", "modified", "last activity",
                  "last response"]),
    dict(key="type", label="Request type", required=False,
         guesses=["type", "category", "request type", "ticket type", "issue type",
                  "reason"]),
    dict(key="priority", label="Priority", required=False,
         guesses=["priority", "urgency", "severity"]),
    dict(key="website", label="Client website", required=False,
         guesses=["website", "site", "url", "domain", "client website"]),
    dict(key="billable", label="Requires billing", required=False,
         guesses=["requires billing", "billable", "billing", "chargeable"]),
    dict(key="details", label="Describe the changes", required=False,
         guesses=["describe the changes", "changes", "description", "details"]),
    dict(key="developer", label="Developer", required=False,
         guesses=["developer", "dev", "built by", "assigned developer"]),
    dict(key="assignee", label="Assigner", required=False,
         guesses=["assigned to", "assignee", "owner", "tech", "handled by",
                  "assigned"]),
    dict(key="summary", label="Summary", required=False,
         guesses=["summary", "subject", "title", "request", "description",
                  "details", "notes"]),
    dict(key="time_spent", label="Time spent (hours)", required=False,
         guesses=["time spent", "hours", "time", "duration", "billable hours",
                  "minutes"]),
    dict(key="source", label="Source", required=False,
         guesses=["source", "channel", "submitted via", "origin"]),
]

REQUIRED_FIELDS = [f["key"] for f in FIELDS if f["required"]]

# Only used when the Hub's own client registry is not importable.
CLIENT_FIELDS = [
    dict(key="name", label="Client name", required=True,
         guesses=["client name", "company name", "name", "client", "company",
                  "account name", "business name"]),
    dict(key="monthly_price", label="Monthly price", required=False,
         guesses=["monthly price", "monthly", "mrr", "monthly fee", "price",
                  "monthly total", "retainer"]),
    dict(key="products", label="Products", required=False,
         guesses=["products", "services", "product", "plan", "package"]),
    dict(key="status", label="Client status", required=False,
         guesses=["status", "active", "client status", "account status"]),
]

# --------------------------------------------------------------------------
# Status vocabulary. Matching is case-insensitive and substring-based, so
# "In Progress - Waiting on Client" lands in open.
# --------------------------------------------------------------------------
def _env_list(name, default):
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


OPEN_STATUSES = _env_list("TICKETS_OPEN_STATUSES", [
    "open", "new", "in progress", "in-progress", "working", "assigned",
    "pending", "waiting", "on hold", "hold", "review", "scheduled", "reopened",
])
CLOSED_STATUSES = _env_list("TICKETS_CLOSED_STATUSES", [
    "closed", "complete", "completed", "resolved", "done", "finished",
    "cancelled", "canceled", "declined", "duplicate", "rejected",
])
# Statuses that mean "we are waiting on the client", so aging is not our fault.
BLOCKED_STATUSES = _env_list("TICKETS_BLOCKED_STATUSES", [
    "waiting on client", "waiting for client", "pending client", "on hold",
])

# --------------------------------------------------------------------------
# Thresholds. All tunable without touching code.
# --------------------------------------------------------------------------
SLA_DAYS = int(os.environ.get("TICKETS_SLA_DAYS", "5"))          # open longer than this = breach
SLA_WARN_DAYS = int(os.environ.get("TICKETS_SLA_WARN_DAYS", "3"))  # approaching
STALE_DAYS = int(os.environ.get("TICKETS_STALE_DAYS", "7"))       # open, untouched
QUIET_DAYS = int(os.environ.get("TICKETS_QUIET_DAYS", "90"))      # paying, no tickets
REPEAT_WINDOW_DAYS = int(os.environ.get("TICKETS_REPEAT_WINDOW_DAYS", "30"))
REPEAT_THRESHOLD = int(os.environ.get("TICKETS_REPEAT_THRESHOLD", "3"))

# Load vs. plan. Cost to serve = hours x internal rate. When a client's cost to
# serve passes these shares of their monthly revenue, the audit flags them.
INTERNAL_HOURLY_RATE = float(os.environ.get("TICKETS_HOURLY_RATE", "85"))
LOAD_WARN_PCT = float(os.environ.get("TICKETS_LOAD_WARN_PCT", "15"))
LOAD_FLAG_PCT = float(os.environ.get("TICKETS_LOAD_FLAG_PCT", "25"))
# Fallback when a ticket carries no time: assume this many hours per ticket.
ASSUMED_HOURS_PER_TICKET = float(os.environ.get("TICKETS_ASSUMED_HOURS", "0.75"))
# Tickets per month a plan is expected to absorb, by monthly price band.
# (lower bound of monthly price, tickets included)
ALLOWANCE_BANDS = [
    (0, 2),
    (500, 4),
    (1000, 6),
    (2500, 10),
    (5000, 16),
]

# Analysis window for the load reports.
LOAD_WINDOW_DAYS = int(os.environ.get("TICKETS_LOAD_WINDOW_DAYS", "90"))

# Demo mode is entered automatically when Knack is not configured.
FORCE_DEMO = os.environ.get("TICKETS_DEMO", "").lower() in ("1", "true", "yes")


def fieldmap_path():
    return os.path.join(DATA_DIR, "fieldmap.json")


def load_saved_config():
    """Field map plus object keys saved from the setup page."""
    data = jsonstore.read_json(fieldmap_path(), default={})
    return data if isinstance(data, dict) else {}


def save_config(payload):
    # Hand-mapped Knack field ids. Opaque, entered once, and re-deriving them
    # means someone reading field_2338 off the Knack builder again — so this
    # is backed up rather than left on the disk alone.
    os.makedirs(DATA_DIR, exist_ok=True)
    jsonstore.write_json(fieldmap_path(), payload, indent=2)
    return payload


def effective():
    """Env defaults overlaid with anything saved on the setup page."""
    saved = load_saved_config()
    return {
        "tickets_object": saved.get("tickets_object") or TICKETS_OBJECT,
        "clients_object": saved.get("clients_object") or CLIENTS_OBJECT,
        "fieldmap": saved.get("fieldmap") or {},
        "client_fieldmap": saved.get("client_fieldmap") or {},
    }


def allowance_for(monthly_price):
    included = ALLOWANCE_BANDS[0][1]
    for floor, count in ALLOWANCE_BANDS:
        if (monthly_price or 0) >= floor:
            included = count
    return included
