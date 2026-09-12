"""Profile-dispatched concept packs, processed by the existing creative worker."""
from hub.industry_config import load_pack


def profile_for(row):
    meta = row.get("meta") or {}
    if row.get("source") != "landing" or not meta.get("creative_profile"):
        return None
    try:
        pack = load_pack(meta.get("industry_id"))
    except ValueError:
        return None
    if meta["creative_profile"] != pack["creative"]["id"]:
        return None
    return {"kind": pack["creative"]["kind"], "pack": pack, "selection": meta}


def run(job):
    payload = job.payload()
    pack, selection = payload["pack"], payload["selection"]
    return {"status": "concepts_ready", "profile": pack["creative"]["id"],
            "market": selection.get("market"), "service": selection.get("service"),
            "concepts": [{"state": state, "copy": pack["messaging"][state],
                          "sizes": pack["creative"]["sizes"]} for state in pack["creative"]["states"]],
            "image_direction": pack["creative"]["image_direction"],
            "note": "Copy concepts and size briefs only. Final artwork, audio and video require production and approval."}
