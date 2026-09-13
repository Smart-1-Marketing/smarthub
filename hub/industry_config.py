"""Canonical industry packs. Weather rules remain owned by their source catalogs."""
from copy import deepcopy
import json
from pathlib import Path

PACK_DIR = Path(__file__).with_name("industry_packs")


def triggers(pack):
    from hub.weather_triggers import MAX_TRIGGERS, TRIGGERS
    from modules.smartforecast.packs import get_pack
    result = []
    for ref in pack["trigger_refs"]:
        if ref["catalog"] == "smartforecast":
            rule = next((r for r in get_pack(ref["pack"])["rules"] if r["id"] == ref["id"]), None)
            if rule is None:
                raise ValueError("Unknown SmartForecast trigger")
            result.append(dict(deepcopy(rule), catalog="smartforecast"))
        elif ref["catalog"] == "hub":
            from dataclasses import asdict
            rule = TRIGGERS.get(ref["id"])
            if rule is None:
                raise ValueError("Unknown Hub trigger")
            result.append(dict(asdict(rule), catalog="hub"))
        else:
            raise ValueError("Unknown weather catalog")
    if not 0 < len(result) <= MAX_TRIGGERS:
        raise ValueError("Invalid recommended trigger count")
    return result


def load_pack(industry_id):
    # Resolve by catalog membership, never a caller-controlled file path.
    for path in sorted(PACK_DIR.glob("*.json")):
        pack = json.loads(path.read_text(encoding="utf-8"))
        if pack.get("id") != industry_id:
            continue
        required = ("services", "conversion_goals", "messaging", "widget", "creative", "industry_family", "trigger_profile")
        if pack.get("schema_version") != 1 or any(not pack.get(k) for k in required):
            raise ValueError("Invalid industry pack")
        if pack["default_goal"] not in pack["conversion_goals"]:
            raise ValueError("Invalid default conversion goal")
        triggers(pack)
        return pack
    raise ValueError("Unknown industry")


def catalog():
    return [load_pack(json.loads(p.read_text(encoding="utf-8"))["id"]) for p in sorted(PACK_DIR.glob("*.json"))]


def selection(data):
    pack = load_pack(data.get("industry_id", "roofing"))
    service = data.get("service", "")
    goal = data.get("conversion_goal", pack["default_goal"])
    market = str(data.get("market", "")).strip()
    radius = data.get("radius", 25)
    if isinstance(radius, bool) or not str(radius).isdigit() or not 1 <= int(radius) <= 200:
        raise ValueError("Radius must be a whole number from 1 to 200 miles")
    if service not in pack["services"] or goal not in pack["conversion_goals"]:
        raise ValueError("Choose a supported service and conversion goal")
    if not market or len(market) > 120:
        raise ValueError("Enter a city/state or ZIP, up to 120 characters")
    from hub.weather_triggers import MAX_TRIGGERS
    ids = data.get("trigger_ids", [r["id"] for r in triggers(pack)])
    allowed = {r["id"] for r in triggers(pack)}
    if not isinstance(ids, list) or not ids or len(ids) > MAX_TRIGGERS or any(not isinstance(x, str) or x not in allowed for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("Choose valid, distinct weather triggers")
    return pack, dict(industry_id=pack["id"], service=service, market=market, radius=int(radius), conversion_goal=goal, trigger_ids=ids)
