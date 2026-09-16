"""One industry taxonomy for the Hub, resolved automatically and written down.

Capped at 30 keys including `general`. `resolve_industry()` answers the
question every module here used to answer its own way -- what business is
this -- from whatever the Hub already holds, in a fixed precedence, and
`write_industry()` records the answer on the client's own record so the next
reader does not pay for the resolve again. A manual pick always wins and is
never overwritten by a re-resolve; that is the one deliberate exception to
"observed is offered, not recorded" elsewhere in this Hub, because an
industry key is read by so many modules that leaving it merely offered would
mean each one asking the question its own way again.

**Knack is not a source for this deployment pass.** `clients_registry` and
`hub/knack_data.py` wrap a live Knack read, and this file never calls either
-- there is no "knack" tier in `resolve_industry()`'s precedence, and the
confidences of the tiers that remain are left exactly where they were
written (0.8 / 0.7 / 0.7 / 0.3 / 0.0) rather than renumbered to fill the gap,
so a later pass that adds Knack back is a tier inserted rather than five
tiers renumbered.

Matching is `modules/image_picker/taxonomy.py`'s own algorithm, copied
rather than restated: word-boundary regex per alias, earliest match wins,
a longer alias breaks a positional tie. "Roofing Contractor" must not
resolve to `hvac` because "ac" sits inside "contractor" -- that failure is
the reason the picker's matcher looks the way it does, and this file exists
so the next consumer inherits the fix rather than the bug.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# The taxonomy. 30 keys, `general` always last. Each carries its own aliases
# (matched as whole words) and, where the industry has one, a map of
# subtype -> the words that pick it (legal's `pi` for "injury", for example).
# ---------------------------------------------------------------------------


def _ind(key: str, label: str, aliases: list[str],
         subtypes: dict[str, list[str]] | None = None,
         legacy: list[str] | None = None) -> dict[str, Any]:
    return {
        "key": key, "label": label, "aliases": aliases,
        "subtypes": subtypes or {}, "legacy": legacy or [],
    }


INDUSTRIES: list[dict[str, Any]] = [
    _ind("hvac", "HVAC", [
        "hvac", "heating", "cooling", "air conditioning", "furnace"]),
    _ind("home_services", "Home Services", [
        "plumbing", "electrical", "roofing", "landscaping", "lawn", "pest",
        "cleaning", "garage door", "gutters"]),
    _ind("home_improvement", "Home Improvement", [
        "remodeling", "remodelling", "kitchen remodel", "bath remodel",
        "windows", "siding", "flooring", "painting", "cabinets"]),
    _ind("construction", "Construction", [
        "general contractor", "commercial construction", "excavation",
        "concrete", "paving"]),
    _ind("solar", "Solar", [
        "solar", "battery storage", "ev charging"]),
    _ind("automotive", "Automotive", [
        "dealership", "auto repair", "tire", "body shop", "powersports",
        "motorcycle"], legacy=["auto"]),
    _ind("marine", "Marine", [
        "boat", "marina", "yacht"], legacy=["boat"]),
    _ind("rv", "RV", [
        "rv", "camper", "campground"]),
    _ind("restaurant", "Restaurant", [
        "restaurant", "bar", "brewery", "cafe", "café", "catering", "pizza",
        "grill", "tavern"]),
    _ind("retail", "Retail", [
        "store", "boutique", "furniture", "jewelry", "mattress", "appliance"]),
    _ind("ecommerce", "Ecommerce", [
        "online store", "shop online", "ecommerce", "dtc", "food products",
        "apparel"]),
    _ind("legal", "Legal", [
        "law firm", "attorney", "lawyer", "injury", "bankruptcy", "divorce",
        "criminal defense", "criminal"],
        subtypes={"pi": ["injury"],
                  "general": ["bankruptcy", "divorce", "criminal"]}),
    _ind("healthcare", "Healthcare", [
        "medical", "dental", "dentist", "orthodontic", "med spa",
        "chiropractic", "physical therapy", "urgent care", "clinic",
        "pharmacy", "optometry"], legacy=["medical", "medical_dental"]),
    _ind("senior_care", "Senior Care", [
        "assisted living", "senior living", "memory care", "hospice",
        "home health"]),
    _ind("fitness", "Fitness", [
        "gym", "fitness", "crossfit", "martial arts", "yoga", "pilates"]),
    _ind("beauty_wellness", "Beauty & Wellness", [
        "salon", "spa", "barber", "massage", "tattoo", "nails", "lashes"]),
    _ind("real_estate", "Real Estate", [
        "realtor", "real estate", "brokerage", "home builder", "apartments",
        "property management", "storage"], legacy=["home_builder"]),
    _ind("financial", "Financial", [
        "bank", "credit union", "insurance", "accounting", "cpa",
        "mortgage", "wealth"]),
    _ind("professional_services", "Professional Services", [
        "background check", "fingerprint", "apostille", "notary",
        "staffing", "consulting", "printing", "it services", "marketing",
        "security"], legacy=["professional"]),
    _ind("technology", "Technology", [
        "saas", "software", "telecom", "internet provider", "app"]),
    _ind("manufacturing", "Manufacturing", [
        "manufacturing", "manufacture", "industrial", "machine shop",
        "logistics", "trucking", "freight", "warehouse"]),
    _ind("education", "Education", [
        "school", "college", "university", "trade school", "tutoring",
        "daycare", "childcare", "preschool", "academy"]),
    _ind("recruiting", "Recruiting", [
        "hiring", "careers", "recruiting", "recruit", "workforce", "jobs"],
        legacy=["recruit"]),
    _ind("nonprofit", "Nonprofit", [
        "nonprofit", "charity", "church", "foundation", "association",
        "ministry"]),
    _ind("government", "Government", [
        "city of", "county", "village", "township", "utility", "transit",
        "library", "public health"]),
    _ind("tourism", "Tourism", [
        "hotel", "lodging", "inn", "resort", "attraction", "museum", "zoo",
        "cvb", "visitors bureau"]),
    _ind("ski", "Ski", [
        "ski", "snow", "golf course", "water park"]),
    _ind("events", "Events", [
        "venue", "festival", "fair", "arena", "stadium", "team", "concert",
        "expo"], legacy=["stadium"]),
    _ind("pets_vet", "Pets & Veterinary", [
        "vet", "animal hospital", "grooming", "boarding", "pet store"]),
    _ind("general", "General Business", []),
]

INDUSTRY_BY_KEY: dict[str, dict] = {i["key"]: i for i in INDUSTRIES}

# Legacy key -> canonical key. Covers every legacy spelling declared above
# plus the ones actually found in the taxonomy modules this file replaces.
LEGACY_MAP: dict[str, str] = {}
for _i in INDUSTRIES:
    for _leg in _i.get("legacy") or []:
        LEGACY_MAP[_leg] = _i["key"]
# Legacy spellings this file's own docstring names, not otherwise declared
# above (medical_dental is already on healthcare's own legacy list).
LEGACY_MAP.setdefault("medical_dental", "healthcare")


def industry(key: str | None) -> dict | None:
    """Look up an industry by its canonical key or a legacy one."""
    if not key:
        return None
    k = str(key).strip().lower()
    k = LEGACY_MAP.get(k, k)
    return INDUSTRY_BY_KEY.get(k)


# ---------------------------------------------------------------------------
# resolve() -- the algorithm modules/image_picker/taxonomy.guess_industry()
# already had, copied rather than restated: word-boundary regex per alias,
# earliest match wins, a longer alias breaks a positional tie on where it
# started. "Roofing Contractor" must resolve to home_services, never hvac.
# ---------------------------------------------------------------------------

def resolve(text: str | None) -> tuple[str, str]:
    """(key, subtype) for a piece of free text, or ("general", "")."""
    if not text:
        return "general", ""
    t = str(text).strip().lower()
    if not t:
        return "general", ""

    t = LEGACY_MAP.get(t, t)
    for ind in INDUSTRIES:
        if t == ind["key"] or t == ind["label"].lower():
            return ind["key"], ""

    best: tuple[int, int, str] | None = None   # (position, -len, key)
    for ind in INDUSTRIES:
        for alias in ind["aliases"]:
            m = re.search(r"\b" + re.escape(alias) + r"\b", t)
            if not m:
                continue
            candidate = (m.start(), -len(alias), ind["key"])
            if best is None or candidate < best:
                best = candidate
    if not best:
        return "general", ""

    key = best[2]
    subtype = ""
    sub_best: tuple[int, int, str] | None = None
    for sub_key, words in (INDUSTRY_BY_KEY.get(key, {}).get("subtypes") or {}).items():
        for w in words:
            m = re.search(r"\b" + re.escape(w) + r"\b", t)
            if not m:
                continue
            candidate = (m.start(), -len(w), sub_key)
            if sub_best is None or candidate < sub_best:
                sub_best = candidate
    if sub_best:
        subtype = sub_best[2]
    return key, subtype


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Confidence per source tier, in the precedence order this file resolves
# them. Deliberately not renumbered when Knack's tier was dropped, so a
# later pass that adds it back inserts a tier rather than moving five.
_TIER_CONFIDENCE = {
    "manual": 1.0,
    "scan_primary": 0.8,
    "scan_sectors": 0.7,
    "gbp": 0.7,
    "name": 0.3,
    "general": 0.0,
}


def _stored_industry(client: str) -> dict:
    try:
        from hub import seo
        store = seo.load_store(client) or {}
    except Exception:                                     # noqa: BLE001
        return {}
    ind = store.get("industry")
    return ind if isinstance(ind, dict) else {}


def _scan_field(report: dict, path: str) -> Any:
    try:
        from modules.scans.audit_fields import get_field
        return get_field(report, path)
    except Exception:                                     # noqa: BLE001
        cur: Any = report
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return None
        return cur


def resolve_industry(client: str, domain: str = "", hint: str = "", **_kw) -> dict:
    """The industry key for this client, first source that answers.

    Precedence (Knack is not a tier in this deployment pass): manual (the
    stored record's own pick) -> scan_primary -> scan_sectors -> gbp ->
    name -> general. Never raises -- a failing source is skipped and the
    next tier is tried.
    """
    client = str(client or "").strip()
    domain = str(domain or "").strip()

    try:
        stored = _stored_industry(client) if client else {}
    except Exception:                                     # noqa: BLE001
        stored = {}
    if stored.get("source") == "manual" and stored.get("key"):
        return {
            "key": stored.get("key"), "subtype": stored.get("subtype", ""),
            "source": "manual", "confidence": 1.0,
            "evidence": stored.get("evidence", ""),
        }

    report: dict = {}
    try:
        from hub import scan_facts
        report, _meta, _err = scan_facts.latest_report(domain or client)
    except Exception:                                     # noqa: BLE001
        report = {}

    def _try(text: Any, source: str) -> dict | None:
        text = str(text or "").strip()
        if not text:
            return None
        key, subtype = resolve(text)
        if key == "general":
            return None
        return {"key": key, "subtype": subtype, "source": source,
                "confidence": _TIER_CONFIDENCE[source], "evidence": text}

    primary = _scan_field(report, "meta.primary_industry") or hint
    got = _try(primary, "scan_primary")
    if got:
        return got

    sectors = _scan_field(report, "meta.industry_sectors")
    if isinstance(sectors, (list, tuple)):
        sectors = ", ".join(str(s) for s in sectors if s)
    got = _try(sectors, "scan_sectors")
    if got:
        return got

    gmb = _scan_field(report, "google_business_profile.gmb_industries")
    got = _try(gmb, "gbp")
    if got:
        return got

    got = _try(client, "name")
    if got:
        return got

    return {"key": "general", "subtype": "", "source": "general",
            "confidence": 0.0, "evidence": ""}


def _mirror_picker(client: str, canonical_key: str) -> None:
    """Mirror the resolved key onto the Image Picker's own row, when one
    exists and the picker actually carries content for that key."""
    try:
        from modules.image_picker import models as picker_models
        from modules.image_picker import taxonomy as picker_taxonomy
    except Exception:                                      # noqa: BLE001
        return
    picker_key = picker_taxonomy.picker_key_for(canonical_key)
    if not picker_key:
        return
    try:
        db = picker_models.session()
    except Exception:                                       # noqa: BLE001
        return
    try:
        row = (db.query(picker_models.PickerClient)
                 .filter(picker_models.PickerClient.name == client)
                 .first())
        if row and row.industry_key != picker_key:
            row.industry_key = picker_key
            db.commit()
    except Exception:                                        # noqa: BLE001
        pass
    finally:
        try:
            db.close()
        except Exception:                                     # noqa: BLE001
            pass


def _write(client: str, key: str, subtype: str, source: str, confidence: float,
           evidence: str, actor: str, previous: str) -> bool:
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        store["industry"] = {
            "key": key, "subtype": subtype or "", "source": source,
            "confidence": confidence, "resolved_at": _now(),
            "evidence": evidence or "",
        }
        seo.save_store(client, store)
    except Exception:                                        # noqa: BLE001
        return False
    try:
        _mirror_picker(client, key)
    except Exception:                                        # noqa: BLE001
        pass
    try:
        from hub import audit
        audit.log("hub", "industry_resolved", actor=actor or "", client=client,
                   key=key, source=source, previous=previous)
    except Exception:                                        # noqa: BLE001
        pass
    return True


def write_industry(client: str, result: dict, actor: str = "") -> bool:
    """Write `result` onto the client's record -- the deliberate exception
    to "observed is offered, not recorded".

    Writes when no key is stored yet, or when the stored key came from
    anything other than a manual pick and the new source resolves to a
    different key at higher precedence. Never writes over a manual pick.
    """
    client = str(client or "").strip()
    if not client or not result:
        return False
    key = str(result.get("key") or "general")
    source = str(result.get("source") or "general")

    stored = _stored_industry(client)
    cur_key = stored.get("key") or ""
    cur_source = stored.get("source") or ""
    if cur_source == "manual":
        return False

    if not cur_key:
        should = True
    else:
        cur_conf = _TIER_CONFIDENCE.get(cur_source, 0.0)
        new_conf = _TIER_CONFIDENCE.get(source, result.get("confidence") or 0.0)
        should = (new_conf > cur_conf) and (key != cur_key)
    if not should:
        return False

    return _write(client, key, result.get("subtype", ""), source,
                  _TIER_CONFIDENCE.get(source, result.get("confidence") or 0.0),
                  result.get("evidence", ""), actor, cur_key)


_STALE_DAYS = 30


def _client_universe() -> list[str]:
    """Every client name this file can enumerate without a live Knack call.

    There is no Knack sync in scope for this deployment pass, so the usual
    "every client" list (`clients_registry.all_clients()`) is off limits --
    it wraps `knack_data.websites()` / `.products()`. What is read instead is
    what this Hub already knows on disk: every SEO store's own `client`
    field, plus every Image Picker gallery's name. Real, non-Knack sources;
    narrower than the full book, and named as such rather than pretended to
    be complete.
    """
    names: dict[str, str] = {}
    try:
        from hub import seo, jsonstore
        base = seo._store_base()                          # noqa: SLF001
        for fname in os.listdir(base):
            if not fname.endswith(".json"):
                continue
            try:
                data = jsonstore.read_json(os.path.join(base, fname), default={})
            except Exception:                              # noqa: BLE001
                continue
            name = str((data or {}).get("client") or "").strip()
            if name:
                names[name.lower()] = name
    except Exception:                                      # noqa: BLE001
        pass
    try:
        from modules.image_picker import models as picker_models
        db = picker_models.session()
        try:
            for row in db.query(picker_models.PickerClient).all():
                name = str(row.name or "").strip()
                if name:
                    names.setdefault(name.lower(), name)
        finally:
            db.close()
    except Exception:                                      # noqa: BLE001
        pass
    return list(names.values())


def due_for_resweep(client: str) -> bool:
    """A client is due when nothing is stored yet, a manual pick is not on
    file and the stored reading is more than 30 days old."""
    stored = _stored_industry(client)
    if not stored.get("key"):
        return True
    if stored.get("source") == "manual":
        return False
    at = str(stored.get("resolved_at") or "")
    if not at:
        return True
    try:
        when = datetime.fromisoformat(at)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return True
    return (datetime.now(timezone.utc) - when).days > _STALE_DAYS


def sweep(limit: int = 200) -> dict:
    """Resolve and, where warranted, write every due client -- once a day
    from the scheduler. Per-client error isolation, so one bad row costs
    only itself."""
    checked = written = unchanged = errors = 0
    try:
        names = _client_universe()
    except Exception:                                       # noqa: BLE001
        return {"checked": 0, "written": 0, "unchanged": 0, "errors": 1,
                "note": "could not enumerate clients"}
    for name in names:
        if checked >= limit:
            break
        try:
            if not due_for_resweep(name):
                continue
            checked += 1
            result = resolve_industry(client=name)
            if write_industry(name, result):
                written += 1
            else:
                unchanged += 1
        except Exception:                                   # noqa: BLE001
            errors += 1
    return {"checked": checked, "written": written, "unchanged": unchanged,
            "errors": errors}


def diagnostics_summary(limit: int = 500) -> dict:
    """Read-only counts for /diagnostics: clients on `general`, and clients
    where re-resolving against a different remaining source would land on a
    different key than the one currently stored.

    Never a live re-source -- both counts are read off what is already
    stored, so this stays a cheap panel rather than a per-visit sweep.
    """
    try:
        names = _client_universe()
    except Exception:                                      # noqa: BLE001
        return {"measured": False}
    on_general: list[str] = []
    disagree: list[dict] = []
    for name in names[:limit]:
        stored = _stored_industry(name)
        key = stored.get("key") or ""
        if key in ("", "general"):
            on_general.append(name)
            continue
        try:
            fresh = resolve_industry(client=name)
        except Exception:                                  # noqa: BLE001
            continue
        if (fresh.get("key") and fresh.get("key") != "general"
                and fresh.get("key") != key
                and stored.get("source") != "manual"):
            disagree.append({"client": name, "stored": key,
                              "resolved": fresh.get("key"),
                              "stored_source": stored.get("source", ""),
                              "resolved_source": fresh.get("source", "")})
    return {"measured": True, "general_count": len(on_general),
            "general_clients": on_general[:50],
            "disagree_count": len(disagree), "disagree_clients": disagree[:50]}


def set_manual(client: str, key: str, subtype: str = "", actor: str = "") -> bool:
    """The override path -- a person's own pick, recorded with source
    "manual" so no future re-resolve is ever allowed to move it."""
    client = str(client or "").strip()
    if not client:
        return False
    ind = industry(key)
    canon = ind["key"] if ind else "general"
    stored = _stored_industry(client)
    previous = stored.get("key") or ""
    return _write(client, canon, subtype or "", "manual", 1.0, "", actor, previous)
