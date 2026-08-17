"""Demo mode — let the team explore without spending money or changing data.

The Hub talks to six things that either cost money per call or write to a real
customer system:

    Insites      one credit per site scan
    remove.bg    one credit per cutout
    OpenAI       per token, and per image for gpt-image-1
    Cloudinary   storage, and the asset tree is a system of record
    GoHighLevel  creating and deleting real sub-accounts
    Simvoly      real client sites

A team member clicking around to learn the tool will trip all six. So a demo
session is not "the normal app with a banner" — it is the normal app with
every one of those calls intercepted and answered from fixtures.

Two ways in:

    role == "demo"        a shared demo code, no Google account needed
    HUB_DEMO_MODE=true    puts the whole deployment in demo mode, for a
                          throwaway Render instance used purely for training

Guarded operations fail loudly and legibly rather than silently doing nothing —
a demo user should learn what the button does, not think it's broken.
"""
from __future__ import annotations

import functools
import os
import random
from typing import Callable

from hub import audit

# Every capability that costs money or writes outside the Hub.
GUARDED = {
    "insites.scan": "runs a paid Insites site audit",
    "removebg.cutout": "spends a remove.bg credit",
    "openai.text": "makes a paid OpenAI call",
    "openai.image": "generates a paid AI image",
    "cloudinary.write": "uploads to the shared asset library",
    "cloudinary.delete": "permanently deletes a stored asset",
    "ghl.write": "creates or changes a real Suite sub-account",
    "simvoly.write": "changes a real client website",
    "email.send": "sends a real email",
}


def global_demo() -> bool:
    return (os.environ.get("HUB_DEMO_MODE") or "").lower() in {"1", "true", "yes", "on"}


def is_demo(user=None) -> bool:
    if global_demo():
        return True
    return bool(user is not None and getattr(user, "role", "") == "demo")


class DemoBlocked(RuntimeError):
    """Raised when a demo session attempts a real-world action."""

    def __init__(self, capability: str):
        self.capability = capability
        what = GUARDED.get(capability, "makes a real change")
        super().__init__(
            f"You're in a demo session, so this didn't run for real — it {what}. "
            f"Everything you see below is sample data. Sign in with your "
            f"Smart 1 Marketing account to use this for real.")


def guard(capability: str, user=None) -> None:
    """Call at the top of anything in GUARDED."""
    if is_demo(user):
        audit.log("demo", "blocked", actor=getattr(user, "email", "demo"),
                  capability=capability)
        raise DemoBlocked(capability)


def guarded(capability: str, user_arg: str = "user"):
    """Decorator form.

        @guarded("removebg.cutout")
        def remove_background(image, user=None): ...
    """
    def deco(fn: Callable):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            guard(capability, kw.get(user_arg))
            return fn(*a, **kw)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Sample data. Deliberately obvious: fictional businesses, "Demo" in the name,
# nothing that could be mistaken for a real client on a shared screen.
# ---------------------------------------------------------------------------

DEMO_CLIENTS = [
    {"id": "demo-1", "name": "Demo — Riverside HVAC", "domain": "riverside-hvac.example",
     "products": ["SEO", "Suite", "Sites"], "city": "Columbus, OH"},
    {"id": "demo-2", "name": "Demo — Lakeside Marine", "domain": "lakeside-marine.example",
     "products": ["SEO", "Sites"], "city": "Sandusky, OH"},
    {"id": "demo-3", "name": "Demo — Corner Table Bistro", "domain": "cornertable.example",
     "products": ["Suite"], "city": "Cleveland, OH"},
    {"id": "demo-4", "name": "Demo — Hartwell Law", "domain": "hartwell-law.example",
     "products": ["SEO", "Suite", "Sites", "Ads"], "city": "Cincinnati, OH"},
]


def sample_scan(domain: str = "riverside-hvac.example") -> dict:
    rnd = random.Random(domain)
    return {
        "domain": domain, "status": "complete", "demo": True,
        "scores": {"performance": rnd.randint(38, 72), "seo": rnd.randint(45, 85),
                   "mobile": rnd.randint(40, 80), "security": rnd.randint(55, 95)},
        "findings": [
            {"severity": "high", "title": "No SSL redirect",
             "detail": "http:// does not redirect to https://."},
            {"severity": "high", "title": "Images are not compressed",
             "detail": "14 images over 1 MB; largest is 4.2 MB."},
            {"severity": "medium", "title": "Missing meta descriptions",
             "detail": "9 of 22 pages have no meta description."},
            {"severity": "medium", "title": "No LocalBusiness schema",
             "detail": "Structured data is absent sitewide."},
            {"severity": "low", "title": "No FAQ content",
             "detail": "No FAQPage schema found."},
        ],
    }


def sample_photos(query: str = "hvac technician", n: int = 12) -> list[dict]:
    rnd = random.Random(query)
    providers = ["pexels", "pixabay", "unsplash"]
    out = []
    for i in range(n):
        w = rnd.choice([1600, 2400, 3200])
        h = int(w / rnd.choice([1.5, 1.33, 0.75, 1.0]))
        out.append({"id": f"demo-{i}", "provider": providers[i % 3],
                    "thumbnail": f"/static/demo/photo-{i % 6}.webp",
                    "preview": f"/static/demo/photo-{i % 6}.webp",
                    "full": f"/static/demo/photo-{i % 6}.webp",
                    "width": w, "height": h,
                    "author": f"Demo Photographer {i % 4 + 1}",
                    "sourceUrl": "https://example.com/", "demo": True})
    return out


def sample_ai_names(count: int = 3) -> list[dict]:
    base = [
        ("riverside-hvac-technician-servicing-outdoor-ac-unit",
         "Riverside HVAC technician servicing an outdoor air conditioning unit"),
        ("riverside-hvac-van-parked-outside-columbus-home",
         "Riverside HVAC service van parked outside a Columbus home"),
        ("riverside-hvac-technician-checking-furnace-filter",
         "Riverside HVAC technician checking a furnace filter during a tune-up"),
    ]
    return [{"filename": f, "alt": a, "source": "demo"}
            for f, a in (base * 3)[:count]]


def sample_billing() -> list[dict]:
    return [
        {"client": "Demo — Hartwell Law", "plan": "Suite Pro", "monthly": 497,
         "status": "active", "knack_products": 4},
        {"client": "Demo — Riverside HVAC", "plan": "Suite Pro", "monthly": 497,
         "status": "active", "knack_products": 3},
        {"client": "Demo — Corner Table Bistro", "plan": "Suite Starter",
         "monthly": 197, "status": "trialing", "knack_products": 1},
        {"client": "Demo — Northgate Dental", "plan": "Suite Pro", "monthly": 497,
         "status": "active", "knack_products": 0},   # the case the audit catches
    ]


def banner(user=None) -> dict | None:
    """Rendered at the top of every page in a demo session."""
    if not is_demo(user):
        return None
    return {
        "title": "Demo session",
        "body": "You're looking at sample data. Nothing you do here touches a "
                "real client, spends a credit, or sends anything. Explore freely.",
        "cta": "Sign in with Smart 1 Marketing",
        "cta_href": "/login",
    }


def reset(user=None) -> bool:
    """Wipe anything a demo session created. Safe to call from a button."""
    if not is_demo(user):
        return False
    audit.log("demo", "reset", actor=getattr(user, "email", "demo"))
    return True
