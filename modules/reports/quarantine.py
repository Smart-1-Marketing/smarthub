"""Fact rows that cannot be true, held for a person rather than filed.

Every writer in this module -- the provider normalize, the four native
pulls, the CSV parsers -- lands rows through ``store.upsert_rows()``, and
that is the one door this screen stands in. A row that is well-formed and
impossible used to be written like any other: a provider restating a day
with more clicks than impressions, a connector that started reporting
micros where it had reported dollars, a campaign-day dated tomorrow. Each
reached the client's page as a figure, the pacing board as an alert, the
cost report as margin, with every screen internally consistent and nothing
saying the number was the wrong one. That is the confident wrong answer
this Hub keeps having to undo, arriving by the hour.

Four rules, and the source of each is written down because a threshold
with no name on it is an opinion:

* **more clicks than impressions** -- impossible by definition. Every
  platform counts a click against an impression; a row where it is the
  other way round is a restatement gone wrong or two columns swapped.
* **a negative figure** -- spend, impressions, clicks or conversions below
  zero. Some platforms DO issue credits as negative spend on a later day;
  those are a billing adjustment and not delivery, and filed as delivery
  they would draw a negative bar. Held, and a person accepts the credit
  where it is one.
* **dated after today** -- a report of what ran cannot cover a day that
  has not happened. A future date is a timezone or a provider bug, and
  written it would move "figures through" past today on the client's
  page.
* **a spend spike** -- spend over ``SPIKE_MULTIPLIER`` times the campaign's
  own trailing ``BASELINE_DAYS`` average, when there are at least
  ``BASELINE_MIN_DAYS`` of history and the average is at least
  ``BASELINE_MIN_SPEND``. This is the divisor rule: a connector that
  starts reporting micros is a million times yesterday, and one that
  switches currency is a hundred. The floors keep a two-dollar test
  campaign that went live from reading as a spike. These three numbers
  are **house** -- no platform publishes one -- and the page says so.

What is deliberately NOT a rule: a zero. Zero spend on a paused campaign
is the truth, and a rule that holds every zero is one somebody switches
off within the week, taking the four above with it.

**A decision is about the row as it was.** Accept writes the held row --
that row, from ``row_json``, not a re-read -- and remembers its
fingerprint, so the same figure arriving again on the next hourly sync
passes through rather than being held again; a DIFFERENT figure under the
same key is a new proposal and is held afresh. Discard drops it and the
same figure arriving again is dropped in silence and counted, never
re-raised: a queue that fills with the same row every hour is a queue
people stop reading. And a clean figure arriving for a held key -- the
provider restated the day properly -- writes it and marks the held entry
superseded, because holding a stale finding over a row that is now fine is
the permanent red this Hub names elsewhere.

**Nothing here may raise past the store.** The screen runs inside every
sync; a screen that fails must cost the batch nothing, so a baseline that
cannot be read means the spike rule does not run for that batch, said in
the report, rather than the rows being refused.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from . import store

log = logging.getLogger(__name__)

# The spike rule's three numbers. House: no platform publishes a figure for
# "this is not the same campaign any more", and the page prints that.
SPIKE_MULTIPLIER = 50
BASELINE_DAYS = 14
BASELINE_MIN_DAYS = 7
BASELINE_MIN_SPEND = Decimal("1.00")
RULES_SOURCE = "house"

RULES = {
    "clicks_over_impressions": "more clicks than impressions",
    "negative": "a negative figure",
    "future": "dated after today",
    "spend_spike": (f"spend more than {SPIKE_MULTIPLIER}x the campaign's trailing "
                    f"{BASELINE_DAYS}-day average"),
}

_FIGURES = ("spend", "impressions", "clicks", "conversions", "video_views", "completes", "leads")


def _key(v: dict) -> tuple:
    return (v["platform"], v["account_id"], v["campaign_id"], v["date"])


def fingerprint(v: dict) -> str:
    """A digest of the row's figures -- what a decision is about."""
    payload = {k: (str(v.get(k)) if v.get(k) is not None else None) for k in _FIGURES}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _jsonable(v: dict) -> dict:
    out = {}
    for k, val in v.items():
        if isinstance(val, Decimal):
            out[k] = str(val)
        elif isinstance(val, (date, datetime)):
            out[k] = val.isoformat()
        else:
            out[k] = val
    return out


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------

def _baselines(values: list[dict]) -> dict[tuple, dict] | None:
    """{(platform, account, campaign): {"mean", "days"}} over the
    ``BASELINE_DAYS`` before each key's EARLIEST day in this batch -- so the
    window is the same on every hourly re-read of a restate window, and a
    row already inside that window never sits in its own baseline. One read
    of the platform's rows for the whole batch rather than one per row.
    None when the fact table could not be read: the spike rule then does
    not run for this batch, and says so."""
    firsts: dict[tuple, date] = {}
    for v in values:
        k = (v["platform"], v["account_id"], v["campaign_id"])
        if k not in firsts or v["date"] < firsts[k]:
            firsts[k] = v["date"]
    if not firsts:
        return {}
    platforms = {k[0] for k in firsts}
    lo = min(firsts.values()) - timedelta(days=BASELINE_DAYS)
    hi = max(firsts.values()) - timedelta(days=1)
    db = store.SessionLocal()
    try:
        rows = (db.query(store.AdPerfDaily.platform, store.AdPerfDaily.account_id,
                         store.AdPerfDaily.campaign_id, store.AdPerfDaily.date,
                         store.AdPerfDaily.spend)
                  .filter(store.AdPerfDaily.platform.in_(platforms),
                          store.AdPerfDaily.date >= lo, store.AdPerfDaily.date <= hi).all())
    except Exception as exc:                                # noqa: BLE001
        log.warning("reports quarantine: baseline unreadable: %s", exc)
        return None
    finally:
        db.close()
    per: dict[tuple, dict[date, Decimal]] = {}
    for p, a, c, d, spend in rows:
        k = (p, a, c)
        first = firsts.get(k)
        if first is None or not (first - timedelta(days=BASELINE_DAYS) <= d <= first - timedelta(days=1)):
            continue
        per.setdefault(k, {})[d] = Decimal(spend or 0)
    out = {}
    for k, by_day in per.items():
        days = len(by_day)
        mean = (sum(by_day.values(), Decimal(0)) / Decimal(days)) if days else Decimal(0)
        out[k] = {"mean": mean, "days": days}
    return out


def rules_for(v: dict, baselines: dict | None, today: date) -> list[tuple[str, str]]:
    """Every rule the row breaks, as (rule, sentence with the figures)."""
    hits = []
    neg = [k for k in ("spend", "impressions", "clicks", "conversions")
           if v.get(k) is not None and v[k] < 0]
    if neg:
        hits.append(("negative", "negative " + ", ".join(f"{k} ({v[k]})" for k in neg)))
    if int(v.get("clicks") or 0) > int(v.get("impressions") or 0):
        hits.append(("clicks_over_impressions",
                     f"{v['clicks']:,} clicks against {v['impressions']:,} impressions"))
    if v["date"] > today:
        hits.append(("future", f"dated {v['date'].isoformat()}, after today ({today.isoformat()})"))
    if baselines is not None and v.get("spend") is not None and v["spend"] > 0:
        b = baselines.get((v["platform"], v["account_id"], v["campaign_id"]))
        if b and b["days"] >= BASELINE_MIN_DAYS and b["mean"] >= BASELINE_MIN_SPEND \
                and v["spend"] > b["mean"] * SPIKE_MULTIPLIER:
            hits.append(("spend_spike",
                         f"spend ${v['spend']:,.2f} against a trailing average of "
                         f"${b['mean']:,.2f}/day over {b['days']} days"))
    return hits


# ---------------------------------------------------------------------------
# The screen, and settling what it held
# ---------------------------------------------------------------------------

def _existing(values: list[dict]) -> dict[tuple, dict]:
    """The quarantine entries already there for this batch's keys."""
    if not values:
        return {}
    platforms = {v["platform"] for v in values}
    lo, hi = min(v["date"] for v in values), max(v["date"] for v in values)
    db = store.SessionLocal()
    try:
        rows = (db.query(store.Quarantine)
                  .filter(store.Quarantine.platform.in_(platforms),
                          store.Quarantine.date >= lo, store.Quarantine.date <= hi).all())
        return {(r.platform, r.account_id, r.campaign_id, r.date):
                {"status": r.status, "fingerprint": r.fingerprint} for r in rows}
    except Exception as exc:                                # noqa: BLE001 - no table yet
        log.warning("reports quarantine: entries unreadable: %s", exc)
        return {}
    finally:
        db.close()


def screen(values: list[dict], today: date | None = None) -> tuple[list[dict], list[dict]]:
    """Split a validated batch into what is written and what is held.

    Returns ``(clean, held)``. ``clean`` is written by the store. ``held``
    is a list of ``{"values", "rule", "reason", "fingerprint", "repeat"}``
    -- ``repeat`` is a decided entry the same figure came back for, which
    ``settle()`` counts rather than re-raising. A clean row for a key with
    a held entry is in ``clean`` and its entry is superseded by ``settle()``.
    """
    today = today or date.today()
    try:
        baselines = _baselines(values)
    except Exception as exc:                                # noqa: BLE001
        log.warning("reports quarantine: baseline failed: %s", exc)
        baselines = None
    existing = _existing(values)
    clean, held = [], []
    for v in values:
        hits = rules_for(v, baselines, today)
        if not hits:
            clean.append(v)
            continue
        fp = fingerprint(v)
        prior = existing.get(_key(v))
        if prior and prior["fingerprint"] == fp and prior["status"] == "accepted":
            # Somebody accepted this very figure; it stands.
            clean.append(v)
            continue
        rule, _ = hits[0]
        held.append({"values": v, "rule": rule,
                     "reason": "; ".join(sentence for _, sentence in hits),
                     "fingerprint": fp,
                     "repeat": prior["status"] if prior and prior["fingerprint"] == fp
                     and prior["status"] == "discarded" else ""})
    return clean, held


def settle(held: list[dict], written: list[dict]) -> None:
    """Record what the screen held and supersede what a clean row replaced.
    Never raises: a ledger that fails must not cost the rows just written."""
    if not held and not written:
        return
    db = store.SessionLocal()
    try:
        for h in held:
            v = h["values"]
            row = db.get(store.Quarantine, _key(v))
            if row is None:
                row = store.Quarantine(platform=v["platform"], account_id=v["account_id"],
                                       campaign_id=v["campaign_id"], date=v["date"],
                                       fingerprint=h["fingerprint"], times=0, seen_at=store.now())
                db.add(row)
            if h["repeat"] == "discarded":
                # The same figure a person discarded, back again: counted,
                # never re-raised.
                row.times = int(row.times or 0) + 1
                row.last_seen_at = store.now()
                continue
            if row.fingerprint != h["fingerprint"] or row.status != "held":
                # A new figure under a decided key, or a first sighting:
                # this is a fresh proposal and the old decision is not
                # about it.
                row.times = 0
                row.decided_by = None
                row.decided_at = None
                row.note = ("" if row.status in (None, "held") else
                            f"an earlier figure for this day was {row.status}")
            row.status = "held"
            row.rule = h["rule"]
            row.reason = h["reason"][:2000]
            row.row_json = _jsonable(v)
            row.fingerprint = h["fingerprint"]
            row.source = v.get("source") or ""
            row.times = int(row.times or 0) + 1
            row.last_seen_at = store.now()
        if written:
            keys = {_key(v) for v in written}
            platforms = {k[0] for k in keys}
            lo, hi = min(k[3] for k in keys), max(k[3] for k in keys)
            for row in (db.query(store.Quarantine)
                          .filter(store.Quarantine.platform.in_(platforms),
                                  store.Quarantine.status == "held",
                                  store.Quarantine.date >= lo, store.Quarantine.date <= hi).all()):
                if (row.platform, row.account_id, row.campaign_id, row.date) in keys:
                    row.status = "superseded"
                    row.decided_at = store.now()
                    row.note = "the sync restated the day with a figure the rules accept"
        db.commit()
    except Exception as exc:                                # noqa: BLE001
        log.warning("reports quarantine: settle failed: %s", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Reading it, and deciding
# ---------------------------------------------------------------------------

def _entry(r) -> dict:
    row = r.row_json if isinstance(r.row_json, dict) else {}
    return {
        "platform": r.platform, "platform_label": store.platform_label(r.platform),
        "account_id": r.account_id, "campaign_id": r.campaign_id,
        "campaign_name": row.get("campaign_name") or "",
        "date": r.date.isoformat() if r.date else None,
        "rule": r.rule, "rule_label": RULES.get(r.rule, r.rule), "reason": r.reason or "",
        "spend": row.get("spend"), "impressions": row.get("impressions"),
        "clicks": row.get("clicks"), "conversions": row.get("conversions"),
        "source": r.source or "", "status": r.status, "times": int(r.times or 0),
        "seen_at": store.iso(r.seen_at), "last_seen_at": store.iso(r.last_seen_at),
        "decided_by": r.decided_by or "", "decided_at": store.iso(r.decided_at),
        "note": r.note or "",
    }


def held(limit: int = 500) -> list[dict]:
    """Every held row, newest day first."""
    db = store.SessionLocal()
    try:
        rows = (db.query(store.Quarantine).filter(store.Quarantine.status == "held")
                  .order_by(store.Quarantine.date.desc(), store.Quarantine.platform)
                  .limit(max(1, int(limit))).all())
        return [_entry(r) for r in rows]
    finally:
        db.close()


def decided(limit: int = 50) -> list[dict]:
    """The most recent decisions, accepted, discarded or superseded."""
    db = store.SessionLocal()
    try:
        rows = (db.query(store.Quarantine).filter(store.Quarantine.status != "held")
                  .order_by(store.Quarantine.decided_at.desc().nullslast())
                  .limit(max(1, int(limit))).all())
        return [_entry(r) for r in rows]
    finally:
        db.close()


def counts() -> dict:
    """``{"held": n, "by_platform": {platform: n}}``. Never raises -- a
    count that cannot be taken is ``None``, which the screens print as not
    measured rather than as nought."""
    db = store.SessionLocal()
    try:
        rows = (db.query(store.Quarantine.platform, store.func.count())
                  .filter(store.Quarantine.status == "held")
                  .group_by(store.Quarantine.platform).all())
        by = {p: int(n) for p, n in rows}
        return {"held": sum(by.values()), "by_platform": by}
    except Exception:                                       # noqa: BLE001
        return {"held": None, "by_platform": {}}
    finally:
        db.close()


def held_for_client(client: str) -> list[dict]:
    """Held rows on this client's CONFIRMED campaigns -- the days missing
    from their page, which is the thing the staff page should say."""
    keys = {(m["platform"], m["account_id"], m["campaign_id"])
            for m in store.mapped_campaigns_for(client) if not m.get("pending")}
    if not keys:
        return []
    return [h for h in held() if (h["platform"], h["account_id"], h["campaign_id"]) in keys]


def decide(platform: str, account_id: str, campaign_id: str, day, *, action: str, by: str) -> dict:
    """Accept writes the held row -- that row -- into the fact table and
    remembers its figures; discard drops it and remembers them too, so the
    same figure is not raised again. Raises ValueError on a bad ask; the
    route turns that into a sentence."""
    action = (action or "").strip().lower()
    if action not in ("accept", "discard"):
        raise ValueError(f"Unknown decision {action!r}: accept or discard")
    by = store._text(by, 160)
    if not by:
        raise ValueError("A decision needs a name against it")
    platform = store.check_platform(platform)
    when = store.parse_date(day)
    if when is None:
        raise ValueError("A decision needs the row's date")
    key = (platform, store._text(account_id, 80), store._text(campaign_id, 120), when)
    db = store.SessionLocal()
    try:
        row = db.get(store.Quarantine, key)
        if row is None:
            raise ValueError("That row is not in quarantine")
        if row.status != "held":
            raise ValueError(f"That row was already {row.status}"
                             + (f" by {row.decided_by}" if row.decided_by else ""))
        payload = dict(row.row_json or {})
        if action == "accept":
            values = store._fact_values(payload)
            if fingerprint(values) != row.fingerprint:
                raise ValueError("The held row no longer matches its own record; refusing to write it")
            store._write_values([values])
        row.status = "accepted" if action == "accept" else "discarded"
        row.decided_by = by
        row.decided_at = store.now()
        db.commit()
        db.refresh(row)
        return _entry(row)
    finally:
        db.close()
