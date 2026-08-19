"""Turning raw Knack records into something the audit can reason about.

Knack hands back both a display value (`field_104`) and a raw value
(`field_104_raw`) whose shape depends on the field type. Everything in here
exists to flatten that down to plain strings, floats and datetimes.
"""
import re
from datetime import datetime

from . import config

_DATE_FORMATS = [
    "%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d",
    "%d/%m/%Y %I:%M %p", "%d/%m/%Y",
]

_SUFFIXES = {"llc", "inc", "co", "corp", "ltd", "lp", "llp", "plc", "pc",
             "company", "the", "and", "of"}


# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------
def text(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(text(v) for v in value if v not in (None, ""))
    if isinstance(value, dict):
        for k in ("identifier", "label", "name", "value", "full", "email"):
            if value.get(k):
                return text(value[k])
        return ""
    return str(value).strip()


def strip_html(value):
    return re.sub(r"<[^>]+>", " ", text(value)).replace("&nbsp;", " ").strip()


def number(value):
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_date(display, raw=None):
    """Prefer the raw ISO timestamp; fall back to parsing the display string."""
    if isinstance(raw, dict):
        for key in ("iso_timestamp", "date_formatted", "date"):
            val = raw.get(key)
            if val:
                parsed = _try_formats(str(val).replace("Z", ""))
                if parsed:
                    return parsed
        ts = raw.get("unix_timestamp") or raw.get("timestamp")
        if ts:
            try:
                return datetime.fromtimestamp(float(ts) / 1000.0)
            except Exception:
                pass
    if isinstance(raw, str) and raw:
        parsed = _try_formats(raw.replace("Z", ""))
        if parsed:
            return parsed
    return _try_formats(text(display))


def _try_formats(value):
    value = (value or "").strip()
    if not value:
        return None
    value = value.split("+")[0].strip()
    if "." in value and "T" in value:
        value = value.split(".")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------
def classify_status(value):
    """-> open | closed | unknown"""
    low = text(value).lower()
    if not low:
        return "unknown"
    for token in config.CLOSED_STATUSES:
        if token in low:
            return "closed"
    for token in config.OPEN_STATUSES:
        if token in low:
            return "open"
    return "unknown"


def is_blocked(value):
    low = text(value).lower()
    return any(token in low for token in config.BLOCKED_STATUSES)


# --------------------------------------------------------------------------
# Client names
# --------------------------------------------------------------------------
def client_ref(record, field_key):
    """(knack_id, display_name) from a connection or plain text field."""
    if not field_key:
        return "", ""
    raw = record.get(f"{field_key}_raw")
    if isinstance(raw, list) and raw:
        first = raw[0]
        if isinstance(first, dict):
            return text(first.get("id")), text(first.get("identifier"))
    if isinstance(raw, dict):
        return text(raw.get("id")), text(raw.get("identifier") or raw.get("name"))
    return "", strip_html(record.get(field_key))


def name_key(name):
    """Normalized form used for matching. 'The Vaughn Family Law, LLC' -> 'vaughn family law'."""
    low = re.sub(r"[^a-z0-9 ]", " ", text(name).lower())
    parts = [p for p in low.split() if p and p not in _SUFFIXES]
    return " ".join(parts)


def similarity(a, b):
    ta, tb = set(name_key(a).split()), set(name_key(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def match_client(name, clients_by_key, clients):
    """Exact normalized match first, then best token overlap above 0.6."""
    key = name_key(name)
    if not key:
        return None
    if key in clients_by_key:
        return clients_by_key[key]
    best, score = None, 0.0
    for client in clients:
        s = similarity(name, client["name"])
        if s > score:
            best, score = client, s
    return best if score >= 0.6 else None


# --------------------------------------------------------------------------
# Field auto-detect for the setup page
# --------------------------------------------------------------------------
def guess_fieldmap(fields, spec=None):
    """Best-guess {logical_key: knack_field_key} from field names."""
    spec = spec or config.FIELDS
    used = set()
    guessed = {}

    # Confirmed ids win outright. Guessing by name is a fallback for fields
    # nobody has pinned — it is not a second opinion on ones that are, and a
    # renamed label must not be able to move a mapping that's already known
    # to be right.
    present = {f.get("key") for f in fields}
    for key, fid in getattr(config, "CONFIRMED_FIELDS", {}).items():
        if fid in present or not present:
            guessed[key] = fid
            used.add(fid)
    for entry in spec:
        if entry["key"] in guessed:
            continue                      # already pinned
        best, best_score = None, 0.0
        for field in fields:
            fname = text(field.get("name")).lower().strip()
            if not fname or field.get("key") in used:
                continue
            score = 0.0
            for i, guess in enumerate(entry["guesses"]):
                weight = 1.0 - (i * 0.02)
                if fname == guess:
                    score = max(score, 1.0 * weight)
                elif fname.startswith(guess) or fname.endswith(guess):
                    score = max(score, 0.85 * weight)
                elif guess in fname:
                    score = max(score, 0.7 * weight)
            if score > best_score:
                best, best_score = field, score
        if best and best_score >= 0.7:
            guessed[entry["key"]] = best["key"]
            used.add(best["key"])
    return guessed
