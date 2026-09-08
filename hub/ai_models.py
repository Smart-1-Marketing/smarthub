"""Shared model profiles and a read-only, source-dated upgrade inventory.

Environment settings remain the deployment authority. Catalog discovery never
changes a model: an API model listing proves visibility, not suitability.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

from hub import jsonstore

SOURCE = "https://developers.openai.com/api/docs/models"
REVIEWED = "2026-09-08"
# profile: label, environment override, legacy environment, legacy default, candidate
PROFILES = {
    "commercial.text": ("Commercial · writing", "COMMERCIAL_OPENAI_MODEL", "OPENAI_TEXT_MODEL", "gpt-4o-mini", "gpt-5.6-terra"),
    "commercial.image": ("Commercial · images", "COMMERCIAL_IMAGE_MODEL", "OPENAI_IMAGE_MODEL", "gpt-image-1", "gpt-image-2"),
    "radio.text": ("Radio Promo · writing", "RADIO_OPENAI_MODEL", "OPENAI_MODEL", "gpt-4o", "gpt-5.6-terra"),
    "radio.image": ("Radio Promo · images", "RADIO_IMAGE_MODEL", "OPENAI_IMAGE_MODEL", "gpt-image-1", "gpt-image-2"),
    "fan_radio.text": ("Fan Radio · writing", "FAN_RADIO_OPENAI_MODEL", "OPENAI_MODEL", "gpt-4o-mini", "gpt-5.6-terra"),
}


def resolve(profile: str) -> dict:
    label, override, legacy, default, candidate = PROFILES[profile]
    for name in (override, legacy):
        value = os.environ.get(name, "").strip()
        if value:
            return {"profile": profile, "label": label, "model": value, "source": name,
                    "setting": override, "candidate": candidate}
    return {"profile": profile, "label": label, "model": default, "source": "default",
            "setting": override, "candidate": candidate}


def model(profile: str) -> str:
    return resolve(profile)["model"]


def _path(name):
    return os.path.join(jsonstore.data_root(), "ai_model_review", name + ".json")


def refresh_catalog() -> dict:
    """Free model-list request, with no generation and no provider error echo."""
    import requests
    from hub.config import settings
    if not settings.openai_key:
        raise ValueError("OpenAI is not configured on this server.")
    try:
        response = requests.get("https://api.openai.com/v1/models",
                                headers={"Authorization": "Bearer " + settings.openai_key},
                                timeout=8)
        if response.status_code != 200:
            raise ValueError(f"Model availability check returned HTTP {response.status_code}.")
        data = response.json()["data"]
        if not isinstance(data, list):
            raise ValueError("Invalid model list.")
        ids = sorted({row["id"] for row in data if isinstance(row, dict)
                      and isinstance(row.get("id"), str)})
    except (requests.RequestException, KeyError, TypeError) as exc:
        raise ValueError("Could not read model availability. Previous results were retained.") from exc
    result = {"checked_at": datetime.now(timezone.utc).isoformat(), "models": ids}
    jsonstore.write_json(_path("catalog"), result, durable=False)
    return result


def history() -> list:
    # One immutable file per decision: two workers cannot overwrite each other's history.
    import glob
    rows = [jsonstore.read_json(path, {}) for path in glob.glob(_path("review-*") )]
    return sorted((r for r in rows if r), key=lambda r: r["at"], reverse=True)[:100]


def record_review(profile, candidate, decision, evidence, actor):
    import re
    if profile not in PROFILES or decision not in {"keep", "test", "recommend", "reject"}:
        raise ValueError("Choose a valid profile and decision.")
    if not isinstance(candidate, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,119}", candidate):
        raise ValueError("Enter a valid model ID.")
    if not isinstance(evidence, str) or not 20 <= len(evidence.strip()) <= 4000:
        raise ValueError("Add 20–4000 characters of review evidence and reasoning.")
    row = {"id": uuid4().hex, "at": datetime.now(timezone.utc).isoformat(),
           "profile": profile, "active_model": model(profile), "candidate": candidate,
           "decision": decision, "evidence": evidence.strip(), "actor": str(actor)}
    jsonstore.write_json(_path("review-" + row["id"]), row)
    return row


def inventory():
    catalog = jsonstore.read_json(_path("catalog"), {})
    available = set(catalog.get("models", []))
    checked = catalog.get("checked_at")
    stale = True
    if checked:
        try:
            stale = (datetime.now(timezone.utc) - datetime.fromisoformat(checked)).total_seconds() > 7 * 86400
        except (ValueError, TypeError):
            pass
    rows = []
    reviews = history()
    for key in PROFILES:
        row = resolve(key)
        row.update({"availability": "unverified" if not checked or stale else
                    "listed" if row["candidate"] in available else "not listed",
                    "reason": "Compare scene fidelity and brand accuracy." if key.endswith("image") else
                    "Compare spoken delivery, required facts, timing and revision quality.",
                    "status": "Already configured" if row["model"] == row["candidate"] else "Needs comparison"})
        rows.append(row)
        latest = next((r for r in reviews if r["profile"] == key and
                       r["active_model"] == row["model"] and r["candidate"] == row["candidate"]), None)
        if latest:
            row["status"] = {"keep": "Keep current", "test": "Needs testing",
                             "recommend": "Recommended by reviewer", "reject": "Rejected by reviewer"}[latest["decision"]]
    known = {v for p in PROFILES.values() for v in (p[3], p[4])}
    discovery = [m for m in sorted(available) if m.startswith(("gpt-", "o3", "o4")) and m not in known]
    return {"profiles": rows, "catalog": catalog, "stale": stale,
            "discovered": discovery, "history": reviews, "source": SOURCE, "reviewed": REVIEWED}
