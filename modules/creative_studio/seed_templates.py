"""The first 12 templates -- CLAUDE.md §15, built to prove the architecture
before the library grows rather than to be a finished catalogue.

Each ships with every variable it references declared, so a rep opening one
against a client with a complete Brand Kit sees every field resolved. What
does **not** ship is a rendered thumbnail: that needs the Creatomate source
builder WO-CS5 adds, and drawing a fake one now would be exactly the
confident-wrong-answer failure this codebase spends most of its own history
undoing. The gallery draws a plain layout-labelled card instead, and says so.

``seed(actor)`` is idempotent -- it skips any id already in the table, so
running it on every boot (as ``__init__.py`` does) costs one query per
missing template and nothing at all once they exist. It never touches a
template an admin has since edited: the id is the only thing it checks.
"""
from __future__ import annotations

import re

from . import config
from .db import db
from .models import CsTemplate, CsTemplateScene, CsTemplateVariable

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")

# Declared once, read by every template that references one of these names,
# rather than re-typed per template -- the drift CLAUDE.md's ALIASES table
# exists to stop, one corner of the Hub over.
VAR_META: dict[str, dict] = {
    "company_name":   {"source": "brand", "required": True,  "default": ""},
    "headline":       {"source": "brief", "required": True,
                       "default": "Your business, done right"},
    "subheadline":    {"source": "brief", "required": False, "default": ""},
    "offer":          {"source": "brief", "required": True,
                       "default": "Call for a free estimate"},
    "phone":          {"source": "brand", "required": True,  "default": ""},
    "website":        {"source": "brand", "required": True,  "default": ""},
    "cta":            {"source": "brief", "required": True,  "default": "Call today"},
    "logo":           {"source": "brand", "required": True,  "default": ""},
    "primary_color":  {"source": "brand", "required": False, "default": "#0B5FFF"},
    "secondary_color": {"source": "brand", "required": False, "default": "#0A1E3C"},
    "accent_color":   {"source": "brand", "required": False, "default": "#FFB800"},
    "tagline":        {"source": "brand", "required": False, "default": ""},
    "body":           {"source": "brief", "required": False, "default": ""},
}


def _scene(position: int, layout_key: str, duration: float, **layers) -> dict:
    return {"position": position, "layout_key": layout_key,
            "default_duration": duration, "layers": layers}


def _commercial_scenes(total: int) -> list[dict]:
    if total == 15:
        return [
            _scene(1, "hook_fullbleed", 4, background="slot:video", headline="{{headline}}"),
            _scene(2, "offer_card", 5, headline="Special offer", offer="{{offer}}", cta="{{cta}}"),
            _scene(3, "end_card", 6, background="slot:image", logo="{{logo}}",
                  phone="{{phone}}", website="{{website}}", cta="{{cta}}"),
        ]
    if total == 30:
        return [
            _scene(1, "hook_fullbleed", 5, background="slot:video", headline="{{headline}}"),
            _scene(2, "problem_split", 7, background="slot:image",
                  headline="{{subheadline}}", logo="{{logo}}"),
            _scene(3, "proof_lower_third", 8, background="slot:image", body="{{tagline}}"),
            _scene(4, "offer_card", 5, headline="Special offer", offer="{{offer}}", cta="{{cta}}"),
            _scene(5, "end_card", 5, background="slot:image", logo="{{logo}}",
                  phone="{{phone}}", website="{{website}}", cta="{{cta}}"),
        ]
    if total == 60:
        return [
            _scene(1, "hook_fullbleed", 8, background="slot:video", headline="{{headline}}"),
            _scene(2, "problem_split", 12, background="slot:image",
                  headline="{{subheadline}}", logo="{{logo}}"),
            _scene(3, "proof_lower_third", 15, background="slot:image", body="{{tagline}}"),
            _scene(4, "offer_card", 15, headline="Special offer", offer="{{offer}}", cta="{{cta}}"),
            _scene(5, "end_card", 10, background="slot:image", logo="{{logo}}",
                  phone="{{phone}}", website="{{website}}", cta="{{cta}}"),
        ]
    raise ValueError(f"no commercial scene plan for {total}s")


def _vertical_scenes(total: int) -> list[dict]:
    if total == 15:
        return [
            _scene(1, "vertical_caption", 6, background="slot:video", headline="{{headline}}"),
            _scene(2, "offer_card", 4, headline="Special offer", offer="{{offer}}", cta="{{cta}}"),
            _scene(3, "end_card", 5, background="slot:image", logo="{{logo}}",
                  phone="{{phone}}", website="{{website}}", cta="{{cta}}"),
        ]
    if total == 30:
        return [
            _scene(1, "vertical_caption", 12, background="slot:video", headline="{{headline}}", body="{{body}}"),
            _scene(2, "proof_lower_third", 10, background="slot:image", body="{{tagline}}"),
            _scene(3, "offer_card", 4, headline="Special offer", offer="{{offer}}", cta="{{cta}}"),
            _scene(4, "end_card", 4, background="slot:image", logo="{{logo}}",
                  phone="{{phone}}", website="{{website}}", cta="{{cta}}"),
        ]
    raise ValueError(f"no vertical scene plan for {total}s")


def _templates() -> list[dict]:
    out = []

    for industry, offer_default in (
        ("general", "Call for a free estimate"),
        ("hvac", "$79 seasonal tune-up"),
        ("restaurant", "Buy one, get one free"),
    ):
        durations = (15, 30, 60) if industry == "general" else (15, 30)
        for dur in durations:
            tid = f"{industry}-{dur}"
            out.append({
                "id": tid,
                "name": f"{config.industry_label(industry)} :{dur}",
                "description": f"A :{dur} commercial built from hook, proof and offer beats.",
                "category": "commercial", "industry": industry,
                "duration": dur, "aspect_ratio": "16:9",
                "creative_type": "video_commercial",
                "tags": [industry, f":{dur}", "commercial"],
                "scenes": _commercial_scenes(dur),
                "offer_default": offer_default,
            })

    out.append({
        "id": "home_services-30",
        "name": "Home Services :30",
        "description": "A :30 commercial for a general home-services business.",
        "category": "commercial", "industry": "home_services",
        "duration": 30, "aspect_ratio": "16:9",
        "creative_type": "video_commercial",
        "tags": ["home_services", ":30", "commercial"],
        "scenes": _commercial_scenes(30),
        "offer_default": "Free in-home estimate",
    })

    out.append({
        "id": "social-vertical-promo-15",
        "name": "Vertical Promotional :15",
        "description": "A :15 vertical cut for feed and story placements.",
        "category": "social", "industry": "general",
        "duration": 15, "aspect_ratio": "9:16",
        "creative_type": "social_video",
        "tags": ["social", "9:16", ":15"],
        "scenes": _vertical_scenes(15),
        "offer_default": "Call for a free estimate",
    })
    out.append({
        "id": "social-ugc-vertical-30",
        "name": "UGC Vertical :30",
        "description": "A :30 vertical cut in the testimonial / UGC shape.",
        "category": "social", "industry": "general",
        "duration": 30, "aspect_ratio": "9:16",
        "creative_type": "social_video",
        "tags": ["social", "9:16", ":30", "ugc"],
        "scenes": _vertical_scenes(30),
        "offer_default": "Call for a free estimate",
    })

    out.append({
        "id": "brand-logo-intro-5",
        "name": "Logo Intro :05",
        "description": "A five-second animated logo reveal.",
        "category": "brand", "industry": "general",
        "duration": 5, "aspect_ratio": "16:9",
        "creative_type": "intro_outro",
        "tags": ["brand", ":05", "intro"],
        "scenes": [_scene(1, "logo_reveal", 5, logo="{{logo}}")],
        "offer_default": "",
    })
    out.append({
        "id": "brand-cta-outro-5",
        "name": "CTA Outro :05",
        "description": "A five-second branded call-to-action outro.",
        "category": "brand", "industry": "general",
        "duration": 5, "aspect_ratio": "16:9",
        "creative_type": "intro_outro",
        "tags": ["brand", ":05", "outro"],
        "scenes": [_scene(1, "end_card", 5, logo="{{logo}}", phone="{{phone}}",
                          website="{{website}}", cta="{{cta}}")],
        "offer_default": "",
    })

    return out


def _variable_names(scenes: list[dict]) -> list[str]:
    names: set[str] = set()
    for scene in scenes:
        for value in (scene.get("layers") or {}).values():
            names.update(_VAR_RE.findall(str(value)))
    return sorted(names)


def seed(actor: str = "system") -> int:
    """Insert any of the 12 templates not already present. Returns how many
    were created. Never raises past its own transaction -- a seed failure
    must not take the module down, only leave the gallery a little emptier
    than it should be, which `/api/integrity`-style tooling can notice."""
    created = 0
    for spec in _templates():
        if CsTemplate.query.get(spec["id"]) is not None:
            continue
        tmpl = CsTemplate(
            id=spec["id"], name=spec["name"], description=spec["description"],
            category=spec["category"], industry=spec["industry"],
            duration=spec["duration"], aspect_ratio=spec["aspect_ratio"],
            creative_type=spec["creative_type"], status="published", version=1,
            source="seed", created_by=actor)
        tmpl.tags = spec["tags"]
        db.session.add(tmpl)

        for scene in spec["scenes"]:
            row = CsTemplateScene(template_id=tmpl.id, position=scene["position"],
                                  default_duration=scene["default_duration"],
                                  layout_key=scene["layout_key"])
            row.layers = scene["layers"]
            db.session.add(row)

        for name in _variable_names(spec["scenes"]):
            meta = dict(VAR_META.get(name, {"source": "brief", "required": False, "default": ""}))
            default = meta["default"]
            if name == "offer" and spec.get("offer_default"):
                default = spec["offer_default"]
            db.session.add(CsTemplateVariable(
                template_id=tmpl.id, name=name, source=meta["source"],
                default=default, required=meta["required"]))

        created += 1

    if created:
        db.session.commit()
    return created
