"""Editable industry-pack catalog loader.

The evaluator remains industry-agnostic. This module only loads seed data that
is copied into the database, where staff can change every threshold and window.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


CATALOG_PATH = Path(__file__).resolve().parent / "catalog" / "industry_packs.json"


@lru_cache(maxsize=1)
def catalog() -> dict:
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("packs"), list):
        raise ValueError("SmartForecast industry catalog has no packs list")
    return data


def packs() -> list[dict]:
    return list(catalog()["packs"])


def get_pack(pack_id: str) -> dict:
    for pack in packs():
        if pack.get("id") == pack_id:
            return pack
    raise LookupError("Industry pack not found")


def all_rules() -> list[dict]:
    seen: dict[str, dict] = {}
    for pack in packs():
        for item in pack.get("rules") or []:
            rule = {key: value for key, value in item.items() if key != "content"}
            rule["industry"] = pack.get("industry") or "General"
            seen.setdefault(rule["id"], rule)
    return list(seen.values())
