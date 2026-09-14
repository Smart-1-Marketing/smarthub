"""Durable company aliases and cautious fuzzy identity backfill.

SmartHub has many systems that spell the same company differently.  The
canonical client registry remains the source of truth; this module stores only
facts about how an outside spelling maps back to that registry.  Source rows
are never deleted, renamed, or merged.

The durable state is mirrored by :mod:`hub.jsonstore`, so an accepted alias or
review decision survives a Render disk replacement.  Existing consumers do not
need a new client table: ``augment_registry`` exposes accepted aliases as
virtual rows carrying the canonical row's URL/domain/metadata and its existing
client key.

Safety rules:
* domain/exact-normalized-name evidence may auto-link;
* fuzzy name-only matches need >= .96 and an >= .08 lead over runner-up;
* .90-.959 needs corroborating domain/email evidence;
* .82-.899 goes to review;
* lower scores stay unmatched and are visible in review;
* an ambiguous candidate is never silently selected;
* address, phone, contact, or parent-company overlap never merges companies.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import Iterable

from . import jsonstore

_VERSION = 1
_AUTO_NAME = 0.96
_AUTO_WITH_EVIDENCE = 0.90
_REVIEW = 0.82
_MIN_MARGIN = 0.08
_FREE_EMAIL = {
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "live.com", "icloud.com", "aol.com", "proton.me", "protonmail.com",
}
_NAME_COLUMNS = (
    "client_name", "company_name", "business_name", "customer_name",
    "advertiser_name", "client", "customer",
)
_URL_COLUMNS = (
    "website", "website_url", "url", "domain", "domain_key", "input_url",
    "live_url", "liveurl",
)
_EMAIL_COLUMNS = ("email", "client_email", "customer_email", "contact_email")
_ID_COLUMNS = ("client_id", "customer_id", "account_id", "external_id", "id")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path() -> str:
    return os.path.join(jsonstore.data_root(), "company_identity.json")


def _empty() -> dict:
    return {
        "version": _VERSION,
        "aliases": {},
        "source_ids": {},
        "reviews": {},
        "runs": [],
        "updated_at": "",
    }


def _state() -> dict:
    got = jsonstore.read_json(_path(), default=_empty())
    if not isinstance(got, dict):
        got = _empty()
    for key, default in _empty().items():
        got.setdefault(key, copy.deepcopy(default))
    return got


def _save(mutator):
    def change(data):
        if not isinstance(data, dict):
            data = _empty()
        for key, default in _empty().items():
            data.setdefault(key, copy.deepcopy(default))
        out = mutator(data)
        if out is None:
            return None
        out["version"] = _VERSION
        out["updated_at"] = _now()
        return out
    return jsonstore.update_json(_path(), change, default=_empty(), durable=True,
                                 indent=2)


def normalise(name: str) -> str:
    """Use the Hub's one company-name normaliser without importing it at boot."""
    try:
        from .client_key import normalise_name
        return normalise_name(name)
    except Exception:  # pragma: no cover - defensive boot fallback
        s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
        drop = {"inc", "llc", "ltd", "co", "corp", "corporation", "company",
                "incorporated", "limited", "lp", "llp", "pllc", "pc", "plc",
                "dba", "the", "of", "and"}
        return " ".join(w for w in s.split() if w not in drop)


def _domain(value: str) -> str:
    try:
        from .client_context import canonical_domain
        return canonical_domain(value)
    except Exception:
        return ""


def _email_domain(value: str) -> str:
    text = str(value or "").strip().lower()
    if "@" not in text:
        return ""
    dom = text.rsplit("@", 1)[-1].split(">", 1)[0].strip(" .>")
    return "" if dom in _FREE_EMAIL else dom


def _tokens(name: str) -> set[str]:
    return {x for x in normalise(name).split() if x}


def name_score(a: str, b: str) -> float:
    """Conservative fuzzy score; containment is useful but not auto-proof."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    union = ta | tb
    jac = len(ta & tb) / len(union) if union else 0.0
    weighted = (seq * 0.68) + (jac * 0.32)
    # "Icon Solar" vs "Icon Solar Power" is a strong candidate, but the extra
    # business word is meaningful; cap it below name-only auto acceptance.
    if min(len(ta), len(tb)) >= 2 and (ta <= tb or tb <= ta):
        weighted = max(weighted, 0.94)
    return round(min(weighted, 1.0), 4)


def _rid(source: str, source_id: str, name: str) -> str:
    raw = f"{source}|{source_id}|{normalise(name)}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def aliases() -> dict:
    return dict(_state().get("aliases") or {})


def reviews(status: str = "pending") -> list[dict]:
    rows = list((_state().get("reviews") or {}).values())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return sorted(rows, key=lambda r: (-(float(r.get("score") or 0)),
                                       r.get("source_name", "").lower()))


def status() -> dict:
    s = _state()
    rv = list((s.get("reviews") or {}).values())
    return {
        "aliases": len(s.get("aliases") or {}),
        "source_ids": len(s.get("source_ids") or {}),
        "reviews_pending": sum(1 for r in rv if r.get("status") == "pending"),
        "reviews_approved": sum(1 for r in rv if r.get("status") == "approved"),
        "reviews_rejected": sum(1 for r in rv if r.get("status") == "rejected"),
        "last_run": (s.get("runs") or [])[-1] if s.get("runs") else None,
        "updated_at": s.get("updated_at") or "",
    }


def augment_registry(rows: list[dict]) -> list[dict]:
    """Return registry rows plus approved aliases inheriting canonical data.

    Virtual alias rows are deliberately product_count=-1 so client_key's
    display-name chooser always keeps the actual registry row as canonical.
    """
    if not rows:
        return rows
    store = aliases()
    if not store:
        return rows

    base = list(rows)
    by_key = {str(r.get("key") or ""): r for r in rows if r.get("key")}
    by_norm = {normalise(r.get("name", "")): r for r in rows
               if normalise(r.get("name", ""))}
    seen_raw = {str(r.get("name") or "").strip().lower() for r in rows}

    for rec in store.values():
        if rec.get("status", "approved") != "approved":
            continue
        alias = str(rec.get("alias") or "").strip()
        if not alias or alias.lower() in seen_raw:
            continue
        target = by_key.get(str(rec.get("canonical_key") or ""))
        if target is None:
            target = by_norm.get(normalise(rec.get("canonical_name", "")))
        if target is None:
            continue
        virtual = dict(target)
        virtual.update({
            "name": alias,
            "canonical_name": target.get("name", ""),
            "canonical_key": target.get("key", rec.get("canonical_key", "")),
            "key": target.get("key", rec.get("canonical_key", "")),
            "is_alias": True,
            "alias_source": rec.get("source", ""),
            "alias_confidence": rec.get("confidence", ""),
            "product_count": -1,
            "products": [],
            "running_count": 0,
            "running_products": [],
        })
        base.append(virtual)
        seen_raw.add(alias.lower())
    return base


def _canonical_entries(rows: list[dict]) -> list[dict]:
    """Collapse registry rows by client key; domain-backed records win."""
    from .client_key import client_key
    out: dict[str, dict] = {}
    for row in rows:
        if row.get("is_alias"):
            continue
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or row.get("domain") or "").strip()
        key = str(row.get("key") or client_key(name, url))
        if not name or not key:
            continue
        cur = out.get(key)
        item = {
            "key": key, "name": name,
            "domain": _domain(url), "url": str(row.get("url") or ""),
            "source": str(row.get("source") or "registry"),
        }
        if cur is None or (item["domain"] and not cur.get("domain")):
            out[key] = item
    return list(out.values())


def _observe_db(limit_per_table: int = 5000) -> list[dict]:
    """Read distinct company spellings from tables that explicitly name one.

    Only columns whose names mean client/company/customer/advertiser are read.
    This intentionally does not infer a company from address, phone, contact,
    or arbitrary account labels.
    """
    observations: list[dict] = []
    try:
        from sqlalchemy import inspect as sa_inspect, text
        from .extensions import shared_engine
        engine = shared_engine()
        insp = sa_inspect(engine)
        tables = insp.get_table_names()
    except Exception:
        return observations

    for table in tables:
        try:
            cols = {c["name"] for c in insp.get_columns(table)}
        except Exception:
            continue
        name_col = next((c for c in _NAME_COLUMNS if c in cols), "")
        if not name_col:
            continue
        url_col = next((c for c in _URL_COLUMNS if c in cols), "")
        email_col = next((c for c in _EMAIL_COLUMNS if c in cols), "")
        id_col = next((c for c in _ID_COLUMNS if c in cols), "")
        chosen = [name_col] + [c for c in (url_col, email_col, id_col)
                               if c and c != name_col]
        # identifiers came from SQLAlchemy metadata, not request input.
        query = f"SELECT DISTINCT {', '.join(chosen)} FROM {table} WHERE {name_col} IS NOT NULL LIMIT :n"
        try:
            with engine.connect() as cx:
                found = cx.execute(text(query), {"n": limit_per_table}).mappings().all()
        except Exception:
            continue
        for row in found:
            name = str(row.get(name_col) or "").strip()
            if not name:
                continue
            observations.append({
                "source": f"db:{table}",
                "source_id": str(row.get(id_col) or "") if id_col else "",
                "name": name,
                "url": str(row.get(url_col) or "") if url_col else "",
                "email": str(row.get(email_col) or "") if email_col else "",
            })
    return observations


def _observe_overlays() -> list[dict]:
    out: list[dict] = []
    try:
        from . import io_clients
        for key, row in (io_clients.overlay() or {}).items():
            out.append({"source": "io", "source_id": str(key),
                        "name": str(row.get("name") or ""),
                        "url": str(row.get("url") or row.get("domain") or ""),
                        "email": ""})
    except Exception:
        pass
    try:
        from . import client_urls
        for key, row in (client_urls.overlay() or {}).items():
            out.append({"source": "client_urls", "source_id": str(key),
                        "name": str(row.get("client") or row.get("name") or key),
                        "url": str(row.get("url") or row.get("domain") or ""),
                        "email": ""})
    except Exception:
        pass
    return out


def _dedupe_observations(rows: Iterable[dict]) -> list[dict]:
    seen = set()
    out = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        sig = (str(row.get("source") or ""), str(row.get("source_id") or ""),
               name.lower(), str(row.get("url") or "").lower())
        if sig in seen:
            continue
        seen.add(sig)
        out.append({**row, "name": name})
    return out


def _rank(obs: dict, canonical: list[dict]) -> list[dict]:
    name = obs.get("name", "")
    dom = _domain(obs.get("url", ""))
    email_dom = _email_domain(obs.get("email", ""))
    ranked = []
    for c in canonical:
        score = name_score(name, c["name"])
        evidence = []
        if dom and c.get("domain") and dom == c["domain"]:
            score = 1.0
            evidence.append("domain")
        if email_dom and c.get("domain") and email_dom == c["domain"]:
            score = max(score, 0.95)
            evidence.append("email-domain")
        if normalise(name) == normalise(c["name"]):
            score = 1.0
            evidence.append("normalized-name")
        ranked.append({**c, "score": round(score, 4), "evidence": evidence})
    ranked.sort(key=lambda x: (-x["score"], x["name"].lower()))
    return ranked


def _decision(ranked: list[dict]) -> tuple[str, dict | None, float]:
    if not ranked:
        return "unmatched", None, 0.0
    first = ranked[0]
    second = ranked[1]["score"] if len(ranked) > 1 else 0.0
    margin = first["score"] - second
    corroborated = bool(set(first.get("evidence") or []) & {"domain", "email-domain", "normalized-name"})
    if first["score"] >= _AUTO_NAME and margin >= _MIN_MARGIN:
        return "auto", first, margin
    if first["score"] >= _AUTO_WITH_EVIDENCE and corroborated and margin >= _MIN_MARGIN:
        return "auto", first, margin
    if first["score"] >= _REVIEW:
        return "review", first, margin
    return "unmatched", first, margin


def _alias_record(obs: dict, hit: dict, margin: float, method: str) -> dict:
    return {
        "alias": obs["name"],
        "normalised_alias": normalise(obs["name"]),
        "canonical_key": hit["key"],
        "canonical_name": hit["name"],
        "domain": hit.get("domain") or "",
        "url": hit.get("url") or "",
        "source": obs.get("source") or "",
        "source_id": obs.get("source_id") or "",
        "confidence": round(float(hit.get("score") or 0), 4),
        "margin": round(float(margin), 4),
        "evidence": list(hit.get("evidence") or []),
        "method": method,
        "status": "approved",
        "created_at": _now(),
    }


def backfill(*, limit_per_table: int = 5000) -> dict:
    """One safe, idempotent pass over known company spellings."""
    from . import clients_registry

    # Clear the short registry cache so the canonical book reflects upstream
    # data at run time. Existing aliases may appear as virtual rows and are
    # filtered by _canonical_entries.
    registry = clients_registry.all_clients(refresh=True)
    canonical = _canonical_entries(registry)

    observations = []
    observations.extend({"source": f"registry:{r.get('source','registry')}",
                         "source_id": str(r.get("slug") or ""),
                         "name": str(r.get("name") or ""),
                         "url": str(r.get("url") or r.get("domain") or ""),
                         "email": ""}
                        for r in registry if not r.get("is_alias"))
    observations.extend(_observe_overlays())
    observations.extend(_observe_db(limit_per_table=limit_per_table))
    observations = _dedupe_observations(observations)

    before = status()
    counts = {"observed": len(observations), "canonical": len(canonical),
              "auto": 0, "review": 0, "unmatched": 0, "already_known": 0,
              "conflicts": 0}

    def apply(data):
        alias_map = data.setdefault("aliases", {})
        source_ids = data.setdefault("source_ids", {})
        review_map = data.setdefault("reviews", {})
        for obs in observations:
            norm = normalise(obs["name"])
            if not norm:
                continue
            ranked = _rank(obs, canonical)
            decision, hit, margin = _decision(ranked)
            if not hit:
                counts["unmatched"] += 1
                continue

            # A canonical registry row is already known; no alias is necessary.
            if obs.get("source", "").startswith("registry:") and \
                    normalise(obs["name"]) == normalise(hit["name"]):
                counts["already_known"] += 1
                continue

            existing = alias_map.get(norm)
            if existing:
                if existing.get("canonical_key") != hit.get("key") and decision == "auto":
                    counts["conflicts"] += 1
                    decision = "review"
                else:
                    counts["already_known"] += 1
                    sid = str(obs.get("source_id") or "")
                    if sid:
                        source_ids[f"{obs.get('source','')}:{sid}"] = existing.get("canonical_key", "")
                    continue

            sid = str(obs.get("source_id") or "")
            if decision == "auto":
                rec = _alias_record(obs, hit, margin, "backfill")
                alias_map[norm] = rec
                if sid:
                    source_ids[f"{obs.get('source','')}:{sid}"] = hit["key"]
                counts["auto"] += 1
                continue

            review_id = _rid(str(obs.get("source") or ""), sid, obs["name"])
            old = review_map.get(review_id, {})
            review_map[review_id] = {
                "id": review_id,
                "source": obs.get("source") or "",
                "source_id": sid,
                "source_name": obs["name"],
                "source_url": obs.get("url") or "",
                "candidate_key": hit.get("key") or "",
                "candidate_name": hit.get("name") or "",
                "candidate_domain": hit.get("domain") or "",
                "score": hit.get("score") or 0,
                "margin": round(float(margin), 4),
                "evidence": hit.get("evidence") or [],
                "alternatives": [
                    {"key": x["key"], "name": x["name"], "score": x["score"]}
                    for x in ranked[:5]
                ],
                "status": old.get("status") if old.get("status") in {"approved", "rejected"} else "pending",
                "reason": "ambiguous" if decision == "review" else "no strong match",
                "created_at": old.get("created_at") or _now(),
                "updated_at": _now(),
            }
            if decision == "review":
                counts["review"] += 1
            else:
                counts["unmatched"] += 1

        run = {"at": _now(), **counts, "before": before}
        data.setdefault("runs", []).append(run)
        data["runs"] = data["runs"][-20:]
        return data

    _save(apply)
    # Any process-local registry/client-key cache needs to see new aliases now.
    try:
        clients_registry._cache["at"] = 0.0
        clients_registry._cache["rows"] = []
    except Exception:
        pass
    try:
        from . import client_key
        client_key._index_cache["at"] = 0.0
        client_key._index_cache["value"] = None
    except Exception:
        pass

    after = status()
    return {**counts, "before": before, "after": after, "at": _now()}


def approve(review_id: str, canonical_key: str = "", actor: str = "") -> dict:
    result = {}
    def apply(data):
        nonlocal result
        row = (data.get("reviews") or {}).get(review_id)
        if not row:
            raise KeyError(review_id)
        chosen = canonical_key or row.get("candidate_key") or ""
        alt = next((x for x in row.get("alternatives") or [] if x.get("key") == chosen), None)
        cname = (alt or {}).get("name") or row.get("candidate_name") or ""
        if not chosen or not cname:
            raise ValueError("A canonical company is required.")
        rec = {
            "alias": row["source_name"], "normalised_alias": normalise(row["source_name"]),
            "canonical_key": chosen, "canonical_name": cname,
            "domain": row.get("candidate_domain") if chosen == row.get("candidate_key") else "",
            "url": "", "source": row.get("source") or "",
            "source_id": row.get("source_id") or "", "confidence": row.get("score") or 0,
            "margin": row.get("margin") or 0, "evidence": row.get("evidence") or [],
            "method": "human-review", "status": "approved", "created_at": _now(),
            "approved_by": actor,
        }
        data.setdefault("aliases", {})[rec["normalised_alias"]] = rec
        sid = row.get("source_id") or ""
        if sid:
            data.setdefault("source_ids", {})[f"{row.get('source','')}:{sid}"] = chosen
        row["status"] = "approved"
        row["reviewed_at"] = _now()
        row["reviewed_by"] = actor
        row["selected_key"] = chosen
        result = rec
        return data
    _save(apply)
    return result


def reject(review_id: str, actor: str = "") -> dict:
    result = {}
    def apply(data):
        nonlocal result
        row = (data.get("reviews") or {}).get(review_id)
        if not row:
            raise KeyError(review_id)
        row["status"] = "rejected"
        row["reviewed_at"] = _now()
        row["reviewed_by"] = actor
        result = dict(row)
        return data
    _save(apply)
    return result


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="SmartHub company identity resolver")
    p.add_argument("command", choices=("backfill", "status", "pending"))
    p.add_argument("--limit-per-table", type=int, default=5000)
    args = p.parse_args(argv)
    import json
    if args.command == "backfill":
        value = backfill(limit_per_table=max(100, min(args.limit_per_table, 20000)))
    elif args.command == "pending":
        value = reviews("pending")
    else:
        value = status()
    print(json.dumps(value, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
