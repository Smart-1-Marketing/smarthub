"""SQLite persistence for SmartForecast configuration, snapshots and history."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .engine import advance_state, choose_winner, empty_state, iso, parse_time, simulate, utcnow


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS clients (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL,
  business_goals_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id),
  name TEXT NOT NULL, domain TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'Smart 1 Sites',
  enabled INTEGER NOT NULL DEFAULT 1, check_interval_minutes INTEGER NOT NULL DEFAULT 30,
  weather_provider TEXT NOT NULL DEFAULT 'WeatherAPI', branding_json TEXT NOT NULL DEFAULT '{}',
  state_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  label TEXT NOT NULL, postal_code TEXT NOT NULL, latitude REAL, longitude REAL,
  timezone TEXT NOT NULL DEFAULT 'America/New_York', is_primary INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS weather_snapshots (
  id INTEGER PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES locations(id),
  observed_at TEXT NOT NULL, expires_at TEXT NOT NULL, source TEXT NOT NULL,
  payload_json TEXT NOT NULL, temperature REAL, feels_like REAL, humidity REAL,
  dew_point REAL, forecast_high REAL, forecast_low REAL, rain_probability REAL,
  snow_inches REAL, wind_mph REAL, official_alerts_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS trigger_templates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, industry TEXT NOT NULL DEFAULT 'universal',
  description TEXT NOT NULL DEFAULT '', rule_json TEXT NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_triggers (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  template_id TEXT NOT NULL REFERENCES trigger_templates(id), enabled INTEGER NOT NULL DEFAULT 1,
  priority INTEGER NOT NULL DEFAULT 50, lead_hours REAL NOT NULL DEFAULT 24,
  min_duration_hours REAL NOT NULL DEFAULT 0, post_hours REAL NOT NULL DEFAULT 0,
  cooldown_hours REAL NOT NULL DEFAULT 0, activation_checks INTEGER NOT NULL DEFAULT 2,
  clear_checks INTEGER NOT NULL DEFAULT 2, overrides_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(site_id, template_id)
);
CREATE TABLE IF NOT EXISTS content_slots (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  slot_key TEXT NOT NULL, label TEXT NOT NULL, default_variant_id INTEGER,
  UNIQUE(site_id, slot_key)
);
CREATE TABLE IF NOT EXISTS content_variants (
  id INTEGER PRIMARY KEY, slot_id INTEGER NOT NULL REFERENCES content_slots(id),
  trigger_key TEXT NOT NULL, phase TEXT NOT NULL, name TEXT NOT NULL,
  eyebrow TEXT NOT NULL DEFAULT '', headline TEXT NOT NULL, body TEXT NOT NULL,
  cta_label TEXT NOT NULL, cta_url TEXT NOT NULL, desktop_image_url TEXT,
  mobile_image_url TEXT, alt_text TEXT NOT NULL DEFAULT '', desktop_focal TEXT NOT NULL DEFAULT '65% 50%',
  mobile_focal TEXT NOT NULL DEFAULT '58% 45%', overlay_opacity REAL NOT NULL DEFAULT .18,
  metadata_json TEXT NOT NULL DEFAULT '{}', UNIQUE(slot_id, trigger_key, phase)
);
CREATE TABLE IF NOT EXISTS content_publications (
  id INTEGER PRIMARY KEY, variant_id INTEGER NOT NULL UNIQUE REFERENCES content_variants(id),
  site_id INTEGER NOT NULL REFERENCES sites(id), payload_json TEXT NOT NULL,
  approved_by TEXT NOT NULL, approved_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trigger_events (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  trigger_key TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
  activated_at TEXT NOT NULL, deactivated_at TEXT, last_snapshot_id INTEGER,
  content_variant_id INTEGER, manual_override INTEGER NOT NULL DEFAULT 0,
  activation_reason TEXT NOT NULL DEFAULT '', state_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS trigger_event_history (
  id INTEGER PRIMARY KEY, event_id INTEGER, site_id INTEGER NOT NULL REFERENCES sites(id),
  trigger_key TEXT, phase TEXT NOT NULL, event_type TEXT NOT NULL, recorded_at TEXT NOT NULL,
  source TEXT NOT NULL, snapshot_json TEXT NOT NULL DEFAULT '{}', rule_json TEXT NOT NULL DEFAULT '{}',
  content_variant_id INTEGER, manual_override INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS embed_tokens (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  token TEXT NOT NULL UNIQUE, label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  allowed_origins_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_overrides (
  id INTEGER PRIMARY KEY, site_id INTEGER NOT NULL REFERENCES sites(id),
  content_variant_id INTEGER, trigger_key TEXT, phase TEXT,
  starts_at TEXT NOT NULL, ends_at TEXT, active INTEGER NOT NULL DEFAULT 1,
  note TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weather_location_time ON weather_snapshots(location_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_site_time ON trigger_event_history(site_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_site_active ON trigger_events(site_id, deactivated_at);
CREATE INDEX IF NOT EXISTS idx_variants_trigger_phase ON content_variants(trigger_key, phase);
CREATE INDEX IF NOT EXISTS idx_publications_site ON content_publications(site_id, approved_at DESC);
"""

SCHEMA_VERSION = 2
CONTENT_FIELDS = ("id", "slot_id", "trigger_key", "phase", "name", "eyebrow", "headline", "body",
                  "cta_label", "cta_url", "desktop_image_url", "mobile_image_url", "alt_text",
                  "desktop_focal", "mobile_focal", "overlay_opacity", "metadata")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value) if value else fallback
    except (TypeError, json.JSONDecodeError):
        return fallback


def default_path() -> Path:
    explicit = (os.environ.get("SMARTFORECAST_DB_PATH") or "").strip()
    if explicit:
        return Path(explicit)
    try:
        from hub import jsonstore
        return Path(jsonstore.data_dir("smartforecast")) / "smartforecast.sqlite3"
    except Exception:
        return Path(__file__).resolve().parent / "data" / "smartforecast.sqlite3"


class SmartForecastStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or default_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if not self.path.exists():
            self._restore_latest_backup()
        self.initialize()

    @property
    def backup_path(self) -> Path:
        return self.path.parent / "latest-backup.json"

    def _restore_latest_backup(self) -> bool:
        """Restore a fresh Render disk from the Postgres-mirrored SQL dump."""
        try:
            from hub import jsonstore
            payload = jsonstore.read_json(str(self.backup_path), default={}) or {}
        except Exception:  # noqa: BLE001 — absence of backup means seed normally
            return False
        sql = payload.get("sql") if isinstance(payload, dict) else None
        checksum = payload.get("sha256") if isinstance(payload, dict) else None
        if not sql or checksum != hashlib.sha256(sql.encode("utf-8")).hexdigest():
            return False
        try:
            con = sqlite3.connect(str(self.path), timeout=30)
            try:
                con.executescript(sql)
                con.commit()
            finally:
                con.close()
            return True
        except sqlite3.Error:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(str(self.path), timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def initialize(self) -> None:
        with self._lock, self.connect() as con:
            con.executescript(SCHEMA)
            count = con.execute("SELECT COUNT(*) FROM sites").fetchone()[0]
            if not count:
                self._seed(con)
            self._backfill_publications(con)

    def _backfill_publications(self, con: sqlite3.Connection) -> None:
        """Treat content that predates approvals as the currently published copy."""
        rows = con.execute(
            """SELECT v.*,s.site_id FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
               LEFT JOIN content_publications p ON p.variant_id=v.id WHERE p.id IS NULL"""
        ).fetchall()
        stamp = iso(utcnow())
        for row in rows:
            variant = self._variant_row(row)
            con.execute(
                """INSERT INTO content_publications(variant_id,site_id,payload_json,approved_by,
                   approved_at,created_at) VALUES(?,?,?,?,?,?)""",
                (variant["id"], row["site_id"], _json(self._content_payload(variant)),
                 "SmartForecast migration", stamp, stamp),
            )

    def _seed(self, con: sqlite3.Connection) -> None:
        now = utcnow()
        stamp = iso(now)
        con.execute(
            "INSERT INTO clients(id,name,industry,business_goals_json,created_at) VALUES(1,?,?,?,?)",
            ("Quality Air Columbus", "HVAC",
             _json(["Emergency AC Repair", "AC Tune-Ups", "Furnace Repair", "Furnace Tune-Ups"]), stamp),
        )
        state = empty_state()
        state.update({
            "status": "active", "current_trigger": "hvac_extreme_heat",
            "phase": "pre_event", "activated_at": iso(now - timedelta(hours=4)),
            "phase_changed_at": iso(now - timedelta(hours=4)),
            "reason": "forecast high 96", "qualify_count": 2,
        })
        branding = _default_branding()
        con.execute(
            """INSERT INTO sites(id,client_id,name,domain,platform,enabled,check_interval_minutes,
               weather_provider,branding_json,state_json,created_at,updated_at)
               VALUES(1,1,?,?,?,?,?,?,?,?,?,?)""",
            ("Quality Air Columbus Website", "qualityaircolumbus.com", "Smart 1 Sites", 1, 30,
             "WeatherAPI", _json(branding), _json(state), stamp, stamp),
        )
        con.execute(
            "INSERT INTO locations(id,site_id,label,postal_code,timezone,is_primary) VALUES(1,1,?,?,?,1)",
            ("Columbus, Ohio", "43215", "America/New_York"),
        )

        rules = _seed_rules()
        for rule in rules:
            con.execute(
                "INSERT INTO trigger_templates(id,name,industry,description,rule_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (rule["id"], rule["name"], rule["industry"], rule["description"], _json(rule), stamp, stamp),
            )
            con.execute(
                """INSERT INTO site_triggers(site_id,template_id,enabled,priority,lead_hours,
                   min_duration_hours,post_hours,cooldown_hours,activation_checks,clear_checks)
                   VALUES(1,?,?,?,?,?,?,?,?,?)""",
                (rule["id"], 1, rule["priority"], rule["lead_hours"],
                 rule["min_duration_hours"], rule["post_hours"], rule["cooldown_hours"],
                 rule["activation_checks"], rule["clear_checks"]),
            )

        con.execute("INSERT INTO content_slots(id,site_id,slot_key,label) VALUES(1,1,'hero','Homepage hero')")
        variants = _seed_variants()
        for index, variant in enumerate(variants, 1):
            con.execute(
                """INSERT INTO content_variants(id,slot_id,trigger_key,phase,name,eyebrow,headline,body,
                   cta_label,cta_url,desktop_image_url,mobile_image_url,alt_text,desktop_focal,
                   mobile_focal,overlay_opacity,metadata_json) VALUES(?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (index, variant["trigger_key"], variant["phase"], variant["name"],
                 variant["eyebrow"], variant["headline"], variant["body"],
                 variant["cta_label"], variant["cta_url"], variant["desktop_image_url"],
                 variant.get("mobile_image_url"), variant["alt_text"],
                 variant["desktop_focal"], variant["mobile_focal"],
                 variant["overlay_opacity"], "{}"),
            )
        con.execute("UPDATE content_slots SET default_variant_id=1 WHERE id=1")
        token = "sf_demo_" + secrets.token_urlsafe(18)
        con.execute(
            "INSERT INTO embed_tokens(site_id,token,label,created_at) VALUES(1,?,'Primary website embed',?)",
            (token, stamp),
        )
        payload = {
            "temperature": 87, "feels_like": 91, "humidity": 68, "dew_point": 72,
            "forecast_high": 96, "forecast_low": 74, "rain_probability": 10,
            "snow_inches": 0, "wind_mph": 12, "hours_until_event": 42,
            "official_alerts": [],
        }
        con.execute(
            """INSERT INTO weather_snapshots(location_id,observed_at,expires_at,source,payload_json,
               temperature,feels_like,humidity,dew_point,forecast_high,forecast_low,rain_probability,
               snow_inches,wind_mph,official_alerts_json) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (stamp, iso(now + timedelta(minutes=30)), "WeatherAPI demo", _json(payload),
             87, 91, 68, 72, 96, 74, 10, 0, 12, "[]"),
        )
        event = con.execute(
            """INSERT INTO trigger_events(site_id,trigger_key,status,phase,activated_at,last_snapshot_id,
               content_variant_id,activation_reason,state_json) VALUES(1,'hvac_extreme_heat','active',
               'pre_event',?,1,2,'Forecast high reached 96°F',?)""",
            (state["activated_at"], _json(state)),
        ).lastrowid
        con.execute(
            """INSERT INTO trigger_event_history(event_id,site_id,trigger_key,phase,event_type,recorded_at,
               source,snapshot_json,rule_json,content_variant_id,reason) VALUES(?,1,'hvac_extreme_heat',
               'pre_event','activated',?,'WeatherAPI demo',?,?,2,'Forecast high reached 96°F')""",
            (event, state["activated_at"], _json(payload), _json(rules[0])),
        )

    def bootstrap(self, site_id: int = 1) -> dict:
        with self.connect() as con:
            site = con.execute(
                """SELECT s.*,c.name client_name,c.industry,c.business_goals_json,
                   l.id location_id,l.label location_label,l.postal_code,l.timezone
                   FROM sites s JOIN clients c ON c.id=s.client_id
                   JOIN locations l ON l.site_id=s.id AND l.is_primary=1 WHERE s.id=?""",
                (site_id,),
            ).fetchone()
            if not site:
                raise LookupError("Site not found")
            weather = con.execute(
                "SELECT * FROM weather_snapshots WHERE location_id=? ORDER BY observed_at DESC LIMIT 1",
                (site["location_id"],),
            ).fetchone()
            token = con.execute(
                "SELECT token FROM embed_tokens WHERE site_id=? AND active=1 ORDER BY id LIMIT 1",
                (site_id,),
            ).fetchone()
            rules = self._rules(con, site_id)
            variants = [self._decorate_publication(con, self._variant_row(row)) for row in con.execute(
                """SELECT v.* FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
                   WHERE s.site_id=? ORDER BY v.trigger_key,v.phase""", (site_id,))]
            history = [self._history_row(row) for row in con.execute(
                "SELECT * FROM trigger_event_history WHERE site_id=? ORDER BY recorded_at DESC LIMIT 100",
                (site_id,))]
            state = _loads(site["state_json"], empty_state())
            selected = self._select_variant(con, site_id, state.get("current_trigger"), state.get("phase"))
            selected = self._decorate_publication(con, selected) if selected else None
            return {
                "sites": self._sites(con),
                "site": {
                    "id": site["id"], "client_name": site["client_name"], "name": site["name"],
                    "domain": site["domain"], "industry": site["industry"], "platform": site["platform"],
                    "enabled": bool(site["enabled"]), "check_interval_minutes": site["check_interval_minutes"],
                    "weather_provider": site["weather_provider"], "location_label": site["location_label"],
                    "postal_code": site["postal_code"], "timezone": site["timezone"],
                    "business_goals": _loads(site["business_goals_json"], []),
                    "branding": _loads(site["branding_json"], {}),
                    "embed_token": token["token"] if token else None,
                },
                "state": state,
                "weather": self._weather_row(weather) if weather else {},
                "rules": rules,
                "variants": variants,
                "current_variant": selected,
                "history": history,
                "report": self._report(con, site_id),
            }

    def list_sites(self) -> list[dict]:
        with self.connect() as con:
            return self._sites(con)

    @staticmethod
    def _sites(con: sqlite3.Connection) -> list[dict]:
        rows = con.execute(
            """SELECT s.id,s.client_id,c.name client_name,c.industry,s.name site_name,s.domain,
               s.platform,s.enabled,l.postal_code,l.label location_label
               FROM sites s JOIN clients c ON c.id=s.client_id
               LEFT JOIN locations l ON l.site_id=s.id AND l.is_primary=1
               ORDER BY c.name,s.name,s.id"""
        ).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def _rules(self, con: sqlite3.Connection, site_id: int) -> list[dict]:
        rows = con.execute(
            """SELECT t.*,s.enabled,s.priority,s.lead_hours,s.min_duration_hours,s.post_hours,
               s.cooldown_hours,s.activation_checks,s.clear_checks,s.overrides_json
               FROM site_triggers s JOIN trigger_templates t ON t.id=s.template_id
               WHERE s.site_id=? ORDER BY s.priority DESC""", (site_id,))
        out = []
        for row in rows:
            rule = _loads(row["rule_json"], {})
            rule.update(_loads(row["overrides_json"], {}))
            rule.update({key: row[key] for key in (
                "id", "name", "industry", "description", "priority", "lead_hours",
                "min_duration_hours", "post_hours", "cooldown_hours",
                "activation_checks", "clear_checks")})
            rule["enabled"] = bool(row["enabled"])
            out.append(rule)
        return out

    def save_setup(self, body: dict, site_id: int = 1) -> dict:
        with self._lock, self.connect() as con:
            current = con.execute("SELECT branding_json FROM sites WHERE id=?", (site_id,)).fetchone()
            if not current:
                raise LookupError("Site not found")
            branding = _loads(current["branding_json"], {})
            branding.update(body.get("branding") or {})
            enabled = 1 if body.get("enabled", True) else 0
            interval = min(1440, max(5, int(body.get("check_interval_minutes") or 30)))
            con.execute(
                """UPDATE sites SET name=?,domain=?,platform=?,enabled=?,check_interval_minutes=?,
                   weather_provider=?,branding_json=?,updated_at=? WHERE id=?""",
                (str(body.get("name") or "Website")[:200], str(body.get("domain") or "")[:255],
                 str(body.get("platform") or "Smart 1 Sites")[:80], enabled, interval,
                 str(body.get("weather_provider") or "WeatherAPI")[:80], _json(branding), iso(utcnow()), site_id),
            )
            con.execute(
                "UPDATE locations SET label=?,postal_code=?,timezone=? WHERE site_id=? AND is_primary=1",
                (str(body.get("location_label") or "")[:200], str(body.get("postal_code") or "")[:20],
                 str(body.get("timezone") or "America/New_York")[:80], site_id),
            )
            goals = [str(item)[:120] for item in (body.get("business_goals") or [])][:30]
            industry = str(body.get("industry") or "General")[:80]
            client_name = str(body.get("client_name") or "").strip()[:200]
            con.execute(
                """UPDATE clients SET name=CASE WHEN ?='' THEN name ELSE ? END,
                   industry=?,business_goals_json=? WHERE id=(SELECT client_id FROM sites WHERE id=?)""",
                (client_name, client_name, industry, _json(goals), site_id),
            )
        return self.bootstrap(site_id)

    def create_site(self, body: dict, user: str = "SmartHub user") -> dict:
        """Create one isolated client/site with baseline content and an embed token."""
        client_name = str(body.get("client_name") or "").strip()[:200]
        domain = str(body.get("domain") or "").strip()[:255]
        postal_code = str(body.get("postal_code") or "").strip()[:20]
        if not client_name:
            raise ValueError("Client name is required")
        if not domain:
            raise ValueError("Domain is required")
        if not postal_code:
            raise ValueError("Postal code is required")
        industry = str(body.get("industry") or "General").strip()[:80]
        now = iso(utcnow())
        with self._lock, self.connect() as con:
            client_id = body.get("client_id")
            client = None
            if client_id:
                client = con.execute("SELECT id FROM clients WHERE id=?", (int(client_id),)).fetchone()
                if not client:
                    raise LookupError("Client not found")
            else:
                client = con.execute("SELECT id FROM clients WHERE lower(name)=lower(?)", (client_name,)).fetchone()
            if client:
                client_id = client["id"]
                con.execute("UPDATE clients SET industry=? WHERE id=?", (industry, client_id))
            else:
                client_id = con.execute(
                    "INSERT INTO clients(name,industry,business_goals_json,created_at) VALUES(?,?,?,?)",
                    (client_name, industry, "[]", now),
                ).lastrowid
            site_id = con.execute(
                """INSERT INTO sites(client_id,name,domain,platform,enabled,check_interval_minutes,
                   weather_provider,branding_json,state_json,created_at,updated_at)
                   VALUES(?,?,?,?,1,30,'WeatherAPI',?,?,?,?)""",
                (client_id, str(body.get("site_name") or f"{client_name} Website")[:200], domain,
                 str(body.get("platform") or "Smart 1 Sites")[:80], _json(_default_branding()),
                 _json(empty_state()), now, now),
            ).lastrowid
            con.execute(
                """INSERT INTO locations(site_id,label,postal_code,timezone,is_primary)
                   VALUES(?,?,?,?,1)""",
                (site_id, str(body.get("location_label") or postal_code)[:200], postal_code,
                 str(body.get("timezone") or "America/New_York")[:80]),
            )
            templates = con.execute("SELECT * FROM trigger_templates ORDER BY id").fetchall()
            for template in templates:
                rule = _loads(template["rule_json"], {})
                enabled = template["industry"].lower() in {"universal", industry.lower()}
                con.execute(
                    """INSERT INTO site_triggers(site_id,template_id,enabled,priority,lead_hours,
                       min_duration_hours,post_hours,cooldown_hours,activation_checks,clear_checks)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (site_id, template["id"], 1 if enabled else 0, rule.get("priority", 50),
                     rule.get("lead_hours", 24), rule.get("min_duration_hours", 0),
                     rule.get("post_hours", 0), rule.get("cooldown_hours", 0),
                     rule.get("activation_checks", 2), rule.get("clear_checks", 2)),
                )
            slot_id = con.execute(
                "INSERT INTO content_slots(site_id,slot_key,label) VALUES(?,'hero','Homepage hero')",
                (site_id,),
            ).lastrowid
            root_url = domain if domain.startswith(("https://", "http://")) else f"https://{domain}"
            variant_id = con.execute(
                """INSERT INTO content_variants(slot_id,trigger_key,phase,name,eyebrow,headline,body,
                   cta_label,cta_url,desktop_image_url,mobile_image_url,alt_text,desktop_focal,
                   mobile_focal,overlay_opacity,metadata_json) VALUES(?,'default','default',?,?,?,?,?,
                   ?,NULL,NULL,'','50% 50%','50% 50%',.15,'{}')""",
                (slot_id, "Default website message", "Local service",
                 f"{industry} Services from {client_name}",
                 "Reliable local help, ready when customers need it.", "Contact Us", root_url),
            ).lastrowid
            con.execute("UPDATE content_slots SET default_variant_id=? WHERE id=?", (variant_id, slot_id))
            self._publish_variant(con, variant_id, site_id, user)
            token = "sf_" + secrets.token_urlsafe(24)
            con.execute(
                "INSERT INTO embed_tokens(site_id,token,label,created_at) VALUES(?,?,'Primary website embed',?)",
                (site_id, token, now),
            )
            con.execute(
                """INSERT INTO trigger_event_history(site_id,phase,event_type,recorded_at,source,reason)
                   VALUES(?,'default','site_created',?,'SmartHub',?)""",
                (site_id, now, f"Site created by {user[:120]}"),
            )
        return self.bootstrap(site_id)

    def save_rule(self, rule_id: str, body: dict, site_id: int = 1) -> dict:
        allowed = {"active_conditions", "forecast_conditions", "official_alerts", "condition_mode"}
        overrides = {key: body[key] for key in allowed if key in body}
        with self._lock, self.connect() as con:
            row = con.execute("SELECT 1 FROM site_triggers WHERE site_id=? AND template_id=?",
                              (site_id, rule_id)).fetchone()
            if not row:
                raise LookupError("Trigger not found")
            con.execute(
                """UPDATE site_triggers SET enabled=?,priority=?,lead_hours=?,min_duration_hours=?,
                   post_hours=?,cooldown_hours=?,activation_checks=?,clear_checks=?,overrides_json=?
                   WHERE site_id=? AND template_id=?""",
                (1 if body.get("enabled", True) else 0,
                 min(1000, max(0, int(body.get("priority") or 0))),
                 min(240, max(0, float(body.get("lead_hours") or 0))),
                 min(240, max(0, float(body.get("min_duration_hours") or 0))),
                 min(336, max(0, float(body.get("post_hours") or 0))),
                 min(336, max(0, float(body.get("cooldown_hours") or 0))),
                 min(10, max(1, int(body.get("activation_checks") or 2))),
                 min(10, max(1, int(body.get("clear_checks") or 2))),
                 _json(overrides), site_id, rule_id),
            )
            return next(rule for rule in self._rules(con, site_id) if rule["id"] == rule_id)

    def save_variant(self, variant_id: int, body: dict, site_id: int = 1) -> dict:
        fields = ("name", "eyebrow", "headline", "body", "cta_label", "cta_url",
                  "desktop_image_url", "mobile_image_url", "alt_text", "desktop_focal", "mobile_focal")
        values = [str(body.get(key) or "")[:2000] for key in fields]
        opacity = min(.9, max(0, float(body.get("overlay_opacity") or 0)))
        with self._lock, self.connect() as con:
            row = con.execute(
                """SELECT v.id FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
                   WHERE v.id=? AND s.site_id=?""", (variant_id, site_id)).fetchone()
            if not row:
                raise LookupError("Content variant not found")
            con.execute(
                """UPDATE content_variants SET name=?,eyebrow=?,headline=?,body=?,cta_label=?,cta_url=?,
                   desktop_image_url=?,mobile_image_url=?,alt_text=?,desktop_focal=?,mobile_focal=?,overlay_opacity=?
                   WHERE id=?""", (*values, opacity, variant_id))
            variant = self._variant_row(
                con.execute("SELECT * FROM content_variants WHERE id=?", (variant_id,)).fetchone())
            return self._decorate_publication(con, variant)

    def publish_variant(self, variant_id: int, site_id: int = 1,
                        user: str = "SmartHub user") -> dict:
        with self._lock, self.connect() as con:
            variant = con.execute(
                """SELECT v.* FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
                   WHERE v.id=? AND s.site_id=?""", (variant_id, site_id),
            ).fetchone()
            if not variant:
                raise LookupError("Content variant not found")
            self._publish_variant(con, variant_id, site_id, user)
            con.execute(
                """INSERT INTO trigger_event_history(site_id,trigger_key,phase,event_type,recorded_at,
                   source,content_variant_id,reason) VALUES(?,?,?,?,?,'SmartHub',?,?)""",
                (site_id, variant["trigger_key"], variant["phase"], "content_published",
                 iso(utcnow()), variant_id, f"Approved by {user[:120]}"),
            )
            return self._decorate_publication(con, self._variant_row(variant))

    def rotate_embed_token(self, site_id: int = 1, user: str = "SmartHub user") -> dict:
        with self._lock, self.connect() as con:
            if not con.execute("SELECT 1 FROM sites WHERE id=?", (site_id,)).fetchone():
                raise LookupError("Site not found")
            con.execute("UPDATE embed_tokens SET active=0 WHERE site_id=?", (site_id,))
            token = "sf_" + secrets.token_urlsafe(24)
            con.execute(
                "INSERT INTO embed_tokens(site_id,token,label,created_at) VALUES(?,?,'Rotated website embed',?)",
                (site_id, token, iso(utcnow())),
            )
            con.execute(
                """INSERT INTO trigger_event_history(site_id,phase,event_type,recorded_at,source,reason)
                   VALUES(?,'manual','embed_token_rotated',?,'SmartHub',?)""",
                (site_id, iso(utcnow()), f"Embed token rotated by {user[:120]}"),
            )
        return {"ok": True, "site_id": site_id, "token": token}

    def _publish_variant(self, con: sqlite3.Connection, variant_id: int,
                         site_id: int, user: str) -> None:
        variant = self._variant_row(
            con.execute("SELECT * FROM content_variants WHERE id=?", (variant_id,)).fetchone())
        if not variant:
            raise LookupError("Content variant not found")
        stamp = iso(utcnow())
        con.execute(
            """INSERT INTO content_publications(variant_id,site_id,payload_json,approved_by,approved_at,created_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(variant_id) DO UPDATE SET payload_json=excluded.payload_json,
               approved_by=excluded.approved_by,approved_at=excluded.approved_at""",
            (variant_id, site_id, _json(self._content_payload(variant)), user[:120], stamp, stamp),
        )

    def run_simulation(self, snapshot: dict, site_id: int = 1, persist: bool = False,
                       source: str = "Simulator") -> dict:
        normalized = normalize_snapshot(snapshot)
        with self._lock, self.connect() as con:
            rules = self._rules(con, site_id)
            result = simulate(rules, normalized)
            trigger = result.get("winner") or {}
            variant = self._select_variant(con, site_id, trigger.get("id"), result["phase"])
            if not variant:
                variant = self._select_variant(con, site_id, "default", "default")
            result.update({"snapshot": normalized, "content": variant})
            if persist:
                location = con.execute("SELECT id FROM locations WHERE site_id=? AND is_primary=1", (site_id,)).fetchone()
                snap_id = self._insert_snapshot(con, location["id"], normalized, source)
                site = con.execute("SELECT state_json FROM sites WHERE id=?", (site_id,)).fetchone()
                old_state = _loads(site["state_json"], empty_state())
                if old_state.get("current_trigger") and not old_state.get("rule"):
                    old_state["rule"] = next(
                        (rule for rule in rules if rule["id"] == old_state["current_trigger"]), {})
                winner = choose_winner(rules, normalized)
                new_state, transition = advance_state(old_state, winner)
                variant = self._select_variant(
                    con, site_id, new_state.get("current_trigger"), new_state.get("phase"))
                variant = variant or self._select_variant(con, site_id, "default", "default")
                result["content"] = variant
                con.execute("UPDATE sites SET state_json=?,updated_at=? WHERE id=?",
                            (_json(new_state), iso(utcnow()), site_id))
                if transition in {"activated", "phase_changed", "deactivated"}:
                    self._record_transition(con, site_id, old_state, new_state, transition,
                                            normalized, snap_id, variant, source)
                result.update({"transition": transition, "state": new_state,
                               "snapshot_id": snap_id, "source": source})
        return result

    def due_sites(self, now: datetime | None = None) -> list[dict]:
        """Enabled sites whose cached weather has reached its expiry time."""
        stamp = iso(now or utcnow())
        with self.connect() as con:
            rows = con.execute(
                """SELECT s.id,l.postal_code,s.check_interval_minutes,
                   MAX(w.expires_at) latest_expiry
                   FROM sites s JOIN locations l ON l.site_id=s.id AND l.is_primary=1
                   LEFT JOIN weather_snapshots w ON w.location_id=l.id
                   WHERE s.enabled=1 GROUP BY s.id,l.postal_code,s.check_interval_minutes
                   HAVING latest_expiry IS NULL OR latest_expiry<=? ORDER BY s.id""",
                (stamp,),
            )
            return [dict(row) for row in rows]

    def backup(self) -> dict:
        """Write one verified SQL dump and mirror it through hub.jsonstore.

        The live SQLite database stays on Render's persistent disk. The fixed
        JSON backup key is also mirrored into the Hub's managed Postgres store,
        so a recreated or replaced disk can restore before seeding a new site.
        """
        with self._lock, self.connect() as con:
            sql = "\n".join(con.iterdump())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_at": iso(utcnow()),
            "database_name": self.path.name,
            "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
            "sql": sql,
        }
        try:
            from hub import jsonstore
            written = jsonstore.write_json(str(self.backup_path), payload, durable=True, indent=1)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": type(exc).__name__, "bytes": len(sql.encode("utf-8"))}
        return {"ok": bool(written), "path": str(self.backup_path),
                "bytes": len(sql.encode("utf-8")), "sha256": payload["sha256"]}

    def preflight(self, site_id: int = 1, provider_configured: bool = False) -> dict:
        """Return an executable release checklist for one site.

        A warning keeps internal preview available; a failed blocking check
        means the site should not be installed on a client website yet.
        """
        checks: list[dict] = []

        def add(key: str, label: str, passed: bool, detail: str,
                *, blocking: bool = True, warning: bool = False) -> None:
            status = "pass" if passed else ("warn" if warning or not blocking else "fail")
            checks.append({"key": key, "label": label, "status": status,
                           "detail": detail, "blocking": blocking})

        with self.connect() as con:
            integrity = con.execute("PRAGMA quick_check").fetchone()[0]
            add("database", "Database integrity", integrity == "ok",
                "SQLite quick check passed." if integrity == "ok" else str(integrity))
            site = con.execute(
                """SELECT s.*,c.name client_name,c.industry,l.postal_code,l.timezone
                   FROM sites s JOIN clients c ON c.id=s.client_id
                   LEFT JOIN locations l ON l.site_id=s.id AND l.is_primary=1
                   WHERE s.id=?""", (site_id,),
            ).fetchone()
            if not site:
                add("site", "Site record", False, "Site not found.")
                return {"ok": False, "site_id": site_id, "ran_at": iso(utcnow()),
                        "checks": checks, "summary": {"passed": 1, "warnings": 0, "failed": 1}}

            add("enabled", "Site enabled", bool(site["enabled"]),
                "Weather evaluation is enabled." if site["enabled"] else "Site is paused.")
            postal = str(site["postal_code"] or "").strip()
            add("location", "Primary weather location", bool(postal),
                f"Primary postal code: {postal}." if postal else "Add a primary postal code.")
            token = con.execute(
                "SELECT token FROM embed_tokens WHERE site_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                (site_id,),
            ).fetchone()
            add("embed_token", "Active embed token", bool(token),
                "A public token is active." if token else "Create or rotate an embed token.")
            add("weather_provider", "Live weather provider", provider_configured,
                "WeatherAPI is configured." if provider_configured else
                "WEATHERAPI_KEY is missing; only simulator/manual mode works.")

            rules = self._rules(con, site_id)
            enabled_rules = [rule for rule in rules if rule["enabled"]]
            add("rules", "Enabled trigger rules", bool(enabled_rules),
                f"{len(enabled_rules)} rule(s) enabled." if enabled_rules else "Enable at least one rule.")
            variants = [self._variant_row(row) for row in con.execute(
                """SELECT v.* FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
                   WHERE s.site_id=?""", (site_id,))]
            baseline = next((item for item in variants
                             if item["trigger_key"] == "default" and item["phase"] == "default"), None)
            baseline_complete = bool(baseline and baseline.get("headline") and baseline.get("cta_label")
                                     and baseline.get("cta_url") and
                                     (not baseline.get("desktop_image_url") or baseline.get("alt_text")))
            add("baseline_content", "Baseline SEO content", baseline_complete,
                "Baseline headline, CTA, URL and alt text are complete." if baseline_complete else
                "Complete the default headline, CTA, URL and image alt text.")

            covered = {(item["trigger_key"], item["phase"]) for item in variants}
            missing = [f"{rule['name']} / {phase.replace('_', ' ')}"
                       for rule in enabled_rules for phase in ("pre_event", "active_event", "post_event")
                       if (rule["id"], phase) not in covered]
            add("content_coverage", "Lifecycle content coverage", not missing,
                "Every enabled rule has pre-, active- and post-event content." if not missing else
                f"{len(missing)} lifecycle variant(s) still use baseline fallback.",
                blocking=False, warning=True)

            invalid_urls = [item["name"] for item in variants
                            if not str(item.get("cta_url") or "").startswith(("https://", "http://", "/"))]
            add("cta_urls", "CTA destinations", not invalid_urls,
                "All CTA destinations use HTTP(S) or a site-relative path." if not invalid_urls else
                f"{len(invalid_urls)} content variant(s) have an invalid CTA URL.")
            missing_alt = [item["name"] for item in variants
                           if item.get("desktop_image_url") and not str(item.get("alt_text") or "").strip()]
            add("image_alt", "Image accessibility", not missing_alt,
                "All configured images have alt text." if not missing_alt else
                f"{len(missing_alt)} image(s) need alt text.")

            latest = con.execute(
                """SELECT w.expires_at FROM weather_snapshots w JOIN locations l ON l.id=w.location_id
                   WHERE l.site_id=? ORDER BY w.observed_at DESC LIMIT 1""", (site_id,)
            ).fetchone()
            fresh = bool(latest and parse_time(latest["expires_at"]) and
                         parse_time(latest["expires_at"]) >= utcnow())
            add("weather_cache", "Fresh weather cache", fresh,
                "The latest weather snapshot is current." if fresh else
                "No current snapshot; refresh weather before installation.",
                blocking=False, warning=True)

        backup_exists = self.backup_path.exists()
        add("recovery_backup", "Recovery backup", backup_exists,
            "A local recovery snapshot exists and is eligible for durable mirroring." if backup_exists else
            "Run the backup job before the pilot.", blocking=False, warning=True)
        failed = sum(item["status"] == "fail" for item in checks)
        warnings = sum(item["status"] == "warn" for item in checks)
        passed = sum(item["status"] == "pass" for item in checks)
        return {"ok": failed == 0, "site_id": site_id, "ran_at": iso(utcnow()),
                "checks": checks, "summary": {"passed": passed, "warnings": warnings,
                                                "failed": failed}}

    def qa_suite(self, site_id: int = 1) -> dict:
        """Exercise representative lifecycle winners without mutating history."""
        base = {"temperature": 72, "feels_like": 72, "humidity": 45, "dew_point": 48,
                "forecast_high": 75, "forecast_low": 55, "rain_probability": 10,
                "snow_inches": 0, "wind_mph": 8, "hours_until_event": 0,
                "official_alerts": []}
        scenarios = [
            ("Baseline", None, base, False),
            ("Extreme heat", "hvac_extreme_heat",
             {**base, "temperature": 96, "feels_like": 103, "forecast_high": 98}, False),
            ("Official heat alert", "hvac_extreme_heat",
             {**base, "official_alerts": ["Heat Advisory"]}, True),
            ("Hard freeze", "hvac_hard_freeze",
             {**base, "temperature": 20, "forecast_low": 18}, False),
            ("Cold snap", "hvac_cold_snap",
             {**base, "temperature": 29, "forecast_low": 29}, False),
            ("Heavy rain", "heavy_rain", {**base, "rain_probability": 88}, False),
            ("High wind", "high_wind", {**base, "wind_mph": 48}, False),
        ]
        with self.connect() as con:
            rules = self._rules(con, site_id)
        results = []
        for name, expected, snapshot, immediate in scenarios:
            answer = simulate(rules, snapshot)
            winner = answer.get("winner") or {}
            actual = winner.get("id")
            is_immediate = bool(answer.get("immediate"))
            passed = actual == expected and (not immediate or is_immediate)
            results.append({"name": name, "expected": expected or "default",
                            "actual": actual or "default", "phase": answer.get("phase"),
                            "immediate": is_immediate, "passed": passed})
        return {"ok": all(item["passed"] for item in results), "site_id": site_id,
                "ran_at": iso(utcnow()), "passed": sum(item["passed"] for item in results),
                "total": len(results), "results": results}

    def set_paused(self, paused: bool, site_id: int = 1) -> dict:
        with self._lock, self.connect() as con:
            con.execute("UPDATE sites SET enabled=?,updated_at=? WHERE id=?",
                        (0 if paused else 1, iso(utcnow()), site_id))
            con.execute(
                """INSERT INTO trigger_event_history(site_id,phase,event_type,recorded_at,source,reason)
                   VALUES(?,?,?,?,?,?)""",
                (site_id, "manual", "paused" if paused else "resumed", iso(utcnow()), "SmartHub",
                 "SmartForecast paused by staff" if paused else "SmartForecast resumed by staff"),
            )
        return self.bootstrap(site_id)

    def force_override(self, body: dict, site_id: int = 1, user: str = "SmartHub user") -> dict:
        now = utcnow()
        hours = min(168, max(1, int(body.get("hours") or 4)))
        trigger_key = str(body.get("trigger_key") or "manual")[:120]
        phase = str(body.get("phase") or "active_event")[:40]
        variant_id = body.get("content_variant_id")
        note = str(body.get("note") or "Manual message override")[:500]
        with self._lock, self.connect() as con:
            con.execute("UPDATE manual_overrides SET active=0 WHERE site_id=?", (site_id,))
            con.execute(
                """INSERT INTO manual_overrides(site_id,content_variant_id,trigger_key,phase,starts_at,ends_at,
                   active,note,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (site_id, variant_id, trigger_key, phase, iso(now), iso(now + timedelta(hours=hours)),
                 1, note, user[:120], iso(now)),
            )
            con.execute(
                """INSERT INTO trigger_event_history(site_id,trigger_key,phase,event_type,recorded_at,
                   source,content_variant_id,manual_override,reason) VALUES(?,?,?,?,?,'Manual',?,1,?)""",
                (site_id, trigger_key, phase, "override_started", iso(now), variant_id, note),
            )
        return self.bootstrap(site_id)

    def embed_payload(self, token: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT site_id FROM embed_tokens WHERE token=? AND active=1", (token,)).fetchone()
            if not row:
                return None
            site_id = row["site_id"]
            site = con.execute("SELECT enabled,branding_json,state_json FROM sites WHERE id=?", (site_id,)).fetchone()
            state = _loads(site["state_json"], empty_state())
            override = con.execute(
                """SELECT * FROM manual_overrides WHERE site_id=? AND active=1
                   AND (ends_at IS NULL OR ends_at>?) ORDER BY id DESC LIMIT 1""",
                (site_id, iso(utcnow())),
            ).fetchone()
            if override and override["content_variant_id"]:
                variant = self._published_variant_by_id(con, override["content_variant_id"])
                state.update({"current_trigger": override["trigger_key"], "phase": override["phase"],
                              "status": "manual", "manual_override": True})
            elif site["enabled"]:
                variant = self._select_variant(
                    con, site_id, state.get("current_trigger"), state.get("phase"), published=True)
            else:
                variant = self._select_variant(con, site_id, "default", "default", published=True)
                state = empty_state()
            variant = variant or self._select_variant(con, site_id, "default", "default", published=True)
            return {"site_id": site_id, "state": state, "content": variant,
                    "branding": _loads(site["branding_json"], {})}

    def report_csv(self, site_id: int = 1) -> str:
        import csv
        import io
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["recorded_at", "trigger", "phase", "event", "source", "reason", "manual_override"])
        with self.connect() as con:
            for row in con.execute(
                "SELECT * FROM trigger_event_history WHERE site_id=? ORDER BY recorded_at DESC", (site_id,)):
                writer.writerow([row["recorded_at"], row["trigger_key"] or "", row["phase"],
                                 row["event_type"], row["source"], row["reason"], bool(row["manual_override"])])
        return output.getvalue()

    def save_weather(self, snapshot: dict, source: str = "WeatherAPI", site_id: int = 1) -> int:
        normalized = normalize_snapshot(snapshot)
        with self._lock, self.connect() as con:
            location = con.execute("SELECT id FROM locations WHERE site_id=? AND is_primary=1", (site_id,)).fetchone()
            return self._insert_snapshot(con, location["id"], normalized, source)

    def _insert_snapshot(self, con: sqlite3.Connection, location_id: int,
                         snapshot: dict, source: str) -> int:
        now = utcnow()
        return con.execute(
            """INSERT INTO weather_snapshots(location_id,observed_at,expires_at,source,payload_json,
               temperature,feels_like,humidity,dew_point,forecast_high,forecast_low,rain_probability,
               snow_inches,wind_mph,official_alerts_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (location_id, iso(now), iso(now + timedelta(minutes=30)), source, _json(snapshot),
             snapshot["temperature"], snapshot["feels_like"], snapshot["humidity"], snapshot["dew_point"],
             snapshot["forecast_high"], snapshot["forecast_low"], snapshot["rain_probability"],
             snapshot["snow_inches"], snapshot["wind_mph"], _json(snapshot["official_alerts"])),
        ).lastrowid

    def _record_transition(self, con: sqlite3.Connection, site_id: int, old: dict, new: dict,
                           transition: str, snapshot: dict, snapshot_id: int,
                           variant: dict | None, source: str) -> None:
        trigger = new.get("current_trigger") or old.get("current_trigger")
        rule = new.get("rule") or old.get("rule") or {}
        active = con.execute(
            "SELECT id FROM trigger_events WHERE site_id=? AND deactivated_at IS NULL ORDER BY id DESC LIMIT 1",
            (site_id,),
        ).fetchone()
        event_id = active["id"] if active else None
        if transition == "activated":
            if active:
                # A higher-priority winner closes the previous event before a
                # new one opens. Both records remain immutable in history.
                old_trigger = old.get("current_trigger")
                con.execute(
                    "UPDATE trigger_events SET status='inactive',deactivated_at=? WHERE id=?",
                    (iso(utcnow()), event_id),
                )
                con.execute(
                    """INSERT INTO trigger_event_history(event_id,site_id,trigger_key,phase,event_type,
                       recorded_at,source,snapshot_json,rule_json,content_variant_id,manual_override,reason)
                       VALUES(?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (event_id, site_id, old_trigger, old.get("phase", "default"), "deactivated",
                     iso(utcnow()), source, _json(snapshot), _json(old.get("rule") or {}),
                     None, "Superseded by a higher-priority trigger"),
                )
            event_id = con.execute(
                """INSERT INTO trigger_events(site_id,trigger_key,status,phase,activated_at,last_snapshot_id,
                   content_variant_id,activation_reason,state_json) VALUES(?,?,?,?,?,?,?,?,?)""",
                (site_id, trigger, "active", new["phase"], new["activated_at"], snapshot_id,
                 variant.get("id") if variant else None, new.get("reason", ""), _json(new)),
            ).lastrowid
        elif active:
            con.execute(
                """UPDATE trigger_events SET status=?,phase=?,deactivated_at=?,last_snapshot_id=?,
                   content_variant_id=?,state_json=? WHERE id=?""",
                ("inactive" if transition == "deactivated" else "active", new.get("phase", "default"),
                 iso(utcnow()) if transition == "deactivated" else None, snapshot_id,
                 variant.get("id") if variant else None, _json(new), event_id),
            )
        con.execute(
            """INSERT INTO trigger_event_history(event_id,site_id,trigger_key,phase,event_type,recorded_at,
               source,snapshot_json,rule_json,content_variant_id,manual_override,reason)
               VALUES(?,?,?,?,?,?,?,?,?,?,0,?)""",
            (event_id, site_id, trigger, new.get("phase", "default"), transition, iso(utcnow()), source,
             _json(snapshot), _json(rule), variant.get("id") if variant else None, new.get("reason", "")),
        )

    def _select_variant(self, con: sqlite3.Connection, site_id: int,
                        trigger_key: str | None, phase: str | None,
                        published: bool = False) -> dict | None:
        row = con.execute(
            """SELECT v.* FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
               WHERE s.site_id=? AND v.trigger_key=? AND v.phase=? LIMIT 1""",
            (site_id, trigger_key or "default", phase or "default"),
        ).fetchone()
        if not row:
            row = con.execute(
                """SELECT v.* FROM content_variants v JOIN content_slots s ON s.id=v.slot_id
                   WHERE s.site_id=? AND v.trigger_key='default' AND v.phase='default' LIMIT 1""",
                (site_id,),
            ).fetchone()
        if not row:
            return None
        variant = self._variant_row(row)
        return self._published_variant_by_id(con, variant["id"]) if published else variant

    def _published_variant_by_id(self, con: sqlite3.Connection, variant_id: int) -> dict | None:
        row = con.execute(
            "SELECT payload_json,approved_by,approved_at FROM content_publications WHERE variant_id=?",
            (variant_id,),
        ).fetchone()
        if not row:
            return None
        payload = _loads(row["payload_json"], {})
        payload.update({"approval_status": "published", "approved_by": row["approved_by"],
                        "approved_at": row["approved_at"]})
        return payload

    def _decorate_publication(self, con: sqlite3.Connection, variant: dict) -> dict:
        row = con.execute(
            "SELECT payload_json,approved_by,approved_at FROM content_publications WHERE variant_id=?",
            (variant["id"],),
        ).fetchone()
        published = _loads(row["payload_json"], {}) if row else {}
        is_current = bool(row and published == self._content_payload(variant))
        variant.update({"approval_status": "published" if is_current else "draft",
                        "approved_by": row["approved_by"] if row else None,
                        "approved_at": row["approved_at"] if row else None})
        return variant

    @staticmethod
    def _content_payload(variant: dict) -> dict:
        return {key: variant.get(key) for key in CONTENT_FIELDS}

    @staticmethod
    def _variant_row(row: sqlite3.Row | None) -> dict | None:
        if not row:
            return None
        data = dict(row)
        data["overlay_opacity"] = float(data.get("overlay_opacity") or 0)
        data["metadata"] = _loads(data.pop("metadata_json", "{}"), {})
        return data

    @staticmethod
    def _weather_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        payload = _loads(data.pop("payload_json", "{}"), {})
        data["official_alerts"] = _loads(data.pop("official_alerts_json", "[]"), [])
        data.update({key: value for key, value in payload.items() if key not in data})
        return data

    @staticmethod
    def _history_row(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["snapshot"] = _loads(data.pop("snapshot_json", "{}"), {})
        data["rule"] = _loads(data.pop("rule_json", "{}"), {})
        data["manual_override"] = bool(data["manual_override"])
        return data

    @staticmethod
    def _report(con: sqlite3.Connection, site_id: int) -> dict:
        since = iso(utcnow() - timedelta(days=30))
        rows = list(con.execute(
            "SELECT event_type,trigger_key,recorded_at FROM trigger_event_history WHERE site_id=? AND recorded_at>=?",
            (site_id, since),
        ))
        activations = sum(row["event_type"] in {"activated", "override_started"} for row in rows)
        categories: dict[str, int] = {}
        for row in rows:
            if row["trigger_key"]:
                categories[row["trigger_key"]] = categories.get(row["trigger_key"], 0) + 1
        active_rows = list(con.execute(
            "SELECT activated_at,COALESCE(deactivated_at,?) ended FROM trigger_events WHERE site_id=? AND activated_at>=?",
            (iso(utcnow()), site_id, since),
        ))
        hours = 0.0
        for row in active_rows:
            try:
                start = datetime.fromisoformat(row["activated_at"])
                end = datetime.fromisoformat(row["ended"])
                hours += max(0, (end - start).total_seconds() / 3600)
            except (TypeError, ValueError):
                pass
        return {"weather_events": len(categories), "activations": activations,
                "hours_personalized": round(hours, 1), "transitions": len(rows),
                "categories": categories}


def normalize_snapshot(body: dict) -> dict:
    def number(key: str, default: float = 0) -> float:
        try:
            return float(body.get(key, default))
        except (TypeError, ValueError):
            return default
    alerts = body.get("official_alerts") or body.get("official_alert") or []
    if isinstance(alerts, str):
        alerts = [alerts] if alerts.strip() and alerts.lower() != "none" else []
    return {
        "temperature": number("temperature", 72), "feels_like": number("feels_like", number("temperature", 72)),
        "humidity": number("humidity", 50), "dew_point": number("dew_point", 50),
        "forecast_high": number("forecast_high", number("temperature", 72)),
        "forecast_low": number("forecast_low", number("temperature", 72)),
        "rain_probability": number("rain_probability"), "snow_inches": number("snow_inches"),
        "wind_mph": number("wind_mph"), "hours_until_event": number("hours_until_event", 0),
        "official_alerts": [str(item)[:160] for item in alerts][:20],
    }


def _default_branding() -> dict:
    return {
        "font": "inherit", "headline_weight": 800, "body_weight": 400,
        "headline_color": "#ffffff", "body_color": "#dce7f2",
        "button_color": "#f6b544", "button_text": "#071726",
        "border_radius": 18, "desktop_headline_size": 54,
        "mobile_headline_size": 38,
    }


def _seed_rules() -> list[dict]:
    return [
        {"id": "hvac_extreme_heat", "name": "Extreme Heat", "industry": "HVAC", "priority": 90,
         "description": "High-load AC conditions or an official heat alert.", "lead_hours": 72,
         "min_duration_hours": 6, "post_hours": 24, "cooldown_hours": 6,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "any",
         "forecast_conditions": [{"metric": "forecast_high", "operator": ">=", "value": 92},
                                 {"metric": "feels_like", "operator": ">=", "value": 98}],
         "active_conditions": [{"metric": "temperature", "operator": ">=", "value": 92},
                               {"metric": "feels_like", "operator": ">=", "value": 98}],
         "official_alerts": ["Heat Advisory", "Excessive Heat Warning"]},
        {"id": "hvac_hard_freeze", "name": "Hard Freeze", "industry": "HVAC", "priority": 85,
         "description": "Furnace and pipe risk at 25°F or below.", "lead_hours": 72,
         "min_duration_hours": 6, "post_hours": 36, "cooldown_hours": 12,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "any",
         "forecast_conditions": [{"metric": "forecast_low", "operator": "<=", "value": 25}],
         "active_conditions": [{"metric": "temperature", "operator": "<=", "value": 25}],
         "official_alerts": ["Freeze Warning", "Hard Freeze Warning"]},
        {"id": "hvac_cold_snap", "name": "Cold Snap", "industry": "HVAC", "priority": 75,
         "description": "Freezing weather furnace readiness.", "lead_hours": 72,
         "min_duration_hours": 6, "post_hours": 24, "cooldown_hours": 8,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "any",
         "forecast_conditions": [{"metric": "forecast_low", "operator": "<=", "value": 32}],
         "active_conditions": [{"metric": "temperature", "operator": "<=", "value": 32}],
         "official_alerts": ["Wind Chill Warning"]},
        {"id": "hot_weather", "name": "Hot Weather", "industry": "universal", "priority": 60,
         "description": "A configurable warm-weather opportunity.", "lead_hours": 48,
         "min_duration_hours": 6, "post_hours": 12, "cooldown_hours": 6,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "all",
         "forecast_conditions": [{"metric": "forecast_high", "operator": ">=", "value": 90}],
         "active_conditions": [{"metric": "temperature", "operator": ">=", "value": 90}],
         "official_alerts": []},
        {"id": "heavy_rain", "name": "Heavy Rain", "industry": "universal", "priority": 70,
         "description": "High-probability rain and severe-storm alerts.", "lead_hours": 24,
         "min_duration_hours": 3, "post_hours": 12, "cooldown_hours": 6,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "all",
         "forecast_conditions": [{"metric": "rain_probability", "operator": ">=", "value": 70}],
         "active_conditions": [{"metric": "rain_probability", "operator": ">=", "value": 80}],
         "official_alerts": ["Severe Thunderstorm Warning", "Tornado Warning", "Flash Flood Warning"]},
        {"id": "high_wind", "name": "High Wind", "industry": "universal", "priority": 72,
         "description": "Damaging wind conditions and official warnings.", "lead_hours": 24,
         "min_duration_hours": 3, "post_hours": 24, "cooldown_hours": 8,
         "activation_checks": 2, "clear_checks": 2, "condition_mode": "all",
         "forecast_conditions": [{"metric": "wind_mph", "operator": ">=", "value": 40}],
         "active_conditions": [{"metric": "wind_mph", "operator": ">=", "value": 40}],
         "official_alerts": ["High Wind Warning", "Severe Thunderstorm Warning"]},
    ]


def _seed_variants() -> list[dict]:
    image = "/tools/smartforecast/static/smartforecast-hvac-hero.png"
    common = {"desktop_image_url": image, "mobile_image_url": None,
              "alt_text": "HVAC technician inspecting an outdoor air-conditioning unit",
              "desktop_focal": "66% 48%", "mobile_focal": "58% 44%", "overlay_opacity": .16,
              "cta_url": "https://qualityaircolumbus.com/schedule"}
    return [
        {**common, "trigger_key": "default", "phase": "default", "name": "Default SEO hero",
         "eyebrow": "Comfort for every season", "headline": "Heating & Cooling Services in Columbus, Ohio",
         "body": "Local experts for dependable repair, replacement, and seasonal maintenance.",
         "cta_label": "Schedule Service"},
        {**common, "trigger_key": "hvac_extreme_heat", "phase": "pre_event", "name": "Heat Wave #3 · Pre-event",
         "eyebrow": "Heat wave approaching", "headline": "96° Weather Is Headed to Columbus. Is Your AC Ready?",
         "body": "Avoid a breakdown at the hottest moment. Schedule a quick AC check before the heat arrives.",
         "cta_label": "Prepare My AC"},
        {**common, "trigger_key": "hvac_extreme_heat", "phase": "active_event", "name": "Heat Wave #3 · Active",
         "eyebrow": "Extreme heat today", "headline": "It’s 94° Today. Having AC Trouble?",
         "body": "Our Columbus cooling team is ready for urgent repairs during the heat.",
         "cta_label": "Get AC Help"},
        {**common, "trigger_key": "hvac_extreme_heat", "phase": "post_event", "name": "Heat Wave #3 · Post-event",
         "eyebrow": "After the heat", "headline": "Did the Heat Push Your AC Too Hard?",
         "body": "Strange sounds, warm rooms, or a rising energy bill can be signs your system needs attention.",
         "cta_label": "Check My System"},
        {**common, "trigger_key": "hvac_hard_freeze", "phase": "pre_event", "name": "Hard Freeze · Pre-event",
         "eyebrow": "Hard freeze approaching", "headline": "25° Weather Is Coming. Make Sure Your Furnace Is Ready.",
         "body": "A quick tune-up now can prevent a no-heat emergency when temperatures fall.",
         "cta_label": "Prepare My Furnace"},
        {**common, "trigger_key": "hvac_hard_freeze", "phase": "active_event", "name": "Hard Freeze · Active",
         "eyebrow": "Hard freeze now", "headline": "No Heat During the Freeze? We’re Ready.",
         "body": "Fast furnace diagnostics and repair for Columbus-area homeowners.",
         "cta_label": "Get Furnace Help"},
        {**common, "trigger_key": "hvac_hard_freeze", "phase": "post_event", "name": "Hard Freeze · Post-event",
         "eyebrow": "After the freeze", "headline": "Did the Cold Reveal a Heating Problem?",
         "body": "Let us inspect unusual cycling, cold rooms, or system noise before the next cold snap.",
         "cta_label": "Schedule an Inspection"},
    ]
