"""Single source of truth for Hub configuration.

Before v7 every module read os.environ directly. That produced three classes of
bug we actually shipped:

  * the same setting under different names (CLOUDINARY_FOLDER vs
    SEO_IMAGES_FOLDER vs IMAGE_CREATOR_FOLDER vs BG_REMOVER_FOLDER),
  * the same setting with *different defaults* per module — OPENAI_MODEL
    defaulted to gpt-4o-mini in five places, gpt-4o in one and gpt-5-mini in
    another, so identical prompts hit different models depending on which
    screen you were on,
  * settings that ship blank and fail silently (PUBLIC_BASE_URL blank left
    every Insites scan hanging on "running" forever).

Everything is read once, coerced, and exposed as attributes. Nothing here
raises at import: a misconfigured Hub must still boot and *tell you* what's
missing rather than 500 on every page.

Usage:
    from hub.config import settings
    if settings.cloudinary_ready: ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _s(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _i(name: str, default: int) -> int:
    try:
        return int(float(_s(name) or default))
    except (TypeError, ValueError):
        return default


def _b(name: str, default: bool = False) -> bool:
    raw = _s(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _first(*names: str, default: str = "") -> str:
    """First of several env names that is actually set.

    Naming drifted across modules and across Render: the stock keys are set as
    PEXELS_API / PIXABAY_API on this deployment, while some code was written
    against PEXELS_API_KEY / PIXABAY_API_KEY. Reading only one spelling means a
    key that IS configured reports as missing, and the tool silently degrades.
    Accept every spelling in use rather than forcing a rename in the dashboard.
    """
    for n in names:
        v = _s(n)
        if v:
            return v
    return default


@dataclass(frozen=True)
class Settings:
    # ---- core ----
    secret_key: str = field(default_factory=lambda: _s("SECRET_KEY") or _s("FLASK_SECRET_KEY"))
    panel_password: str = field(default_factory=lambda: _s("PANEL_PASSWORD"))
    database_url: str = field(default_factory=lambda: _s("DATABASE_URL"))
    public_base_url: str = field(default_factory=lambda: _s("PUBLIC_BASE_URL").rstrip("/"))
    data_dir: str = field(default_factory=lambda: _s("HUB_DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else "data"))
    max_upload_mb: int = field(default_factory=lambda: _i("MAX_UPLOAD_MB", 100))

    # ---- OpenAI ----
    openai_key: str = field(default_factory=lambda: _s("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _s("OPENAI_MODEL", "gpt-4o-mini"))
    openai_vision_model: str = field(default_factory=lambda: _s("OPENAI_VISION_MODEL") or _s("OPENAI_MODEL", "gpt-4o"))
    openai_image_model: str = field(default_factory=lambda: _s("OPENAI_IMAGE_MODEL", "gpt-image-1"))
    openai_timeout: int = field(default_factory=lambda: _i("OPENAI_TIMEOUT", 90))
    openai_retries: int = field(default_factory=lambda: _i("OPENAI_RETRIES", 2))

    # ---- Cloudinary ----
    cloudinary_url: str = field(default_factory=lambda: _s("CLOUDINARY_URL"))

    # ---- image pipeline ----
    max_edge: int = field(default_factory=lambda: _i("SEO_IMAGES_MAX_EDGE", 2400))
    preview_edge: int = field(default_factory=lambda: _i("HUB_PREVIEW_EDGE", 640))

    # ---- providers ----
    pexels_key: str = field(default_factory=lambda: _first("PEXELS_API", "PEXELS_API_KEY", "PEXELS_KEY"))
    pixabay_key: str = field(default_factory=lambda: _first("PIXABAY_API", "PIXABAY_API_KEY", "PIXABAY_KEY"))
    unsplash_key: str = field(default_factory=lambda: _first("UNSPLASH_API", "UNSPLASH_ACCESS_KEY", "UNSPLASH_API_KEY", "UNSPLASH_KEY"))
    remove_bg_key: str = field(default_factory=lambda: _first("REMOVE_BG_API", "REMOVE_BG_API_KEY", "REMOVEBG_API_KEY"))
    brandfetch_key: str = field(default_factory=lambda: _first("BRANDFETCH_API", "BRANDFETCH_API_KEY"))
    google_fonts_key: str = field(default_factory=lambda: _first("GOOGLE_FONTS_API", "GOOGLE_FONTS_API_KEY"))
    insites_key: str = field(default_factory=lambda: _first("INSITES_API", "INSITES_API_KEY"))
    # Commercial Builder's spokesperson scenes. The module read HEYGEN_API_KEY
    # directly at import, which froze the value at boot and missed the other
    # two spellings — the same drift that made Pexels report "no key set" with
    # the key plainly present.
    heygen_key: str = field(default_factory=lambda: _first("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY"))
    # Commercial Builder's AI video scenes. Same three spellings for the same
    # reason as every other provider key here.
    runway_key: str = field(default_factory=lambda: _first("RUNWAY_API", "RUNWAY_API_KEY", "RUNWAY_KEY"))
    knack_app_id: str = field(default_factory=lambda: _s("KNACK_APP_ID"))
    knack_api_key: str = field(default_factory=lambda: _s("KNACK_API_KEY"))
    ghl_token: str = field(default_factory=lambda: _first("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"))
    ghl_company_id: str = field(default_factory=lambda: _first("GHL_COMPANY_ID", "SUITE_COMPANY_ID"))
    # The sub-account leads are written into. Deliberately NOT defaulted to the
    # company id: they are different id spaces, and a companyId sent where a
    # locationId belongs addresses the agency silently.
    ghl_lead_location_id: str = field(default_factory=lambda: _first(
        "GHL_LEAD_LOCATION_ID", "SMART1_MARKETING_LOCATION_ID",
        "GHL_ACCOUNTING_LOCATION_ID"))
    simvoly_key: str = field(default_factory=lambda: _first("SIMVOLY_API_KEY", "SIMVOLY_KEY"))

    # ---- behaviour ----
    ai_usage_log: bool = field(default_factory=lambda: _b("HUB_AI_USAGE_LOG", True))

    # ---- readiness ----
    # Values copied straight out of env.example. These are the dangerous kind
    # of misconfiguration: the variable IS set, so nothing reports it missing,
    # and the failure surfaces later as an auth error from the provider.
    PLACEHOLDERS = (
        "cloudinary://API_KEY:API_SECRET@CLOUD_NAME",
        "change-me-to-something-strong",
        "pit-...", "sk-...", "API_KEY", "CHANGEME", "your-key-here",
    )

    def is_placeholder(self, value: str) -> bool:
        v = (value or "").strip().strip('"').strip("'")
        return bool(v) and v in self.PLACEHOLDERS

    @property
    def cloudinary_ready(self) -> bool:
        if self.is_placeholder(self.cloudinary_url):
            return False
        return self.cloudinary_url.startswith("cloudinary://")

    @property
    def cloudinary_cloud_name(self) -> str:
        """Cloud name alone, for building a delivery URL by hand.

        Every *upload* path goes through hub.storage and never needs this. A
        delivery URL is the exception: hub.video_library builds one per result
        for a gallery, and routing that through the SDK would mean configuring
        and importing Cloudinary to assemble a string that is public, cacheable
        and unsigned anyway. Parsed rather than read from a second variable,
        because CLOUDINARY_CLOUD_NAME is not set on this deployment and a
        module that expected it would report the library as unconfigured while
        CLOUDINARY_URL sat right there.
        """
        if not self.cloudinary_ready:
            return ""
        # cloudinary://<key>:<secret>@<cloud-name>[/...]
        tail = self.cloudinary_url.split("@", 1)[-1]
        return tail.split("/", 1)[0].strip()

    @property
    def openai_ready(self) -> bool:
        return bool(self.openai_key)

    def folder(self, kind: str) -> str:
        """Canonical Cloudinary folder for an asset kind.

        Historically each module invented its own env var. Those are still
        honoured so nothing breaks on upgrade, but the default layout is now
        one predictable tree.
        """
        legacy = {
            "proposals": "CLOUDINARY_FOLDER",
            "seo_images": "SEO_IMAGES_FOLDER",
            "image_projects": "IMAGE_CREATOR_FOLDER",
            "cutouts": "BG_REMOVER_FOLDER",
        }.get(kind)
        if legacy:
            override = _s(legacy)
            if override:
                return override
        defaults = {
            "proposals": "smart1-proposals",
            "seo_images": "smart1-seo-images",
            "image_projects": "smart1-image-projects",
            "cutouts": "smart1-cutouts",
            "commercials": "smart1-commercials",
            "backups": "smart1-backups",
        }
        return defaults.get(kind, f"smart1-{kind}")

    def placeholder_warnings(self) -> list[dict]:
        """Variables left at their env.example placeholder value.

        Worse than unset, because every "is it configured?" check says yes.
        """
        import os as _os
        out = []
        for name in ("CLOUDINARY_URL", "PANEL_PASSWORD", "GHL_PRIVATE_TOKEN",
                     "OPENAI_API_KEY", "SECRET_KEY", "SIMVOLY_API_KEY"):
            raw = _os.environ.get(name, "")
            if self.is_placeholder(raw):
                out.append({"name": name,
                            "detail": f"{name} is still the example value from "
                                      f"env.example. It looks configured and "
                                      f"will fail at the provider."})
        # A quoted value in Render keeps the quotes as literal characters.
        for name in ("SECRET_KEY", "SCANS_CALLBACK_TOKEN", "PANEL_PASSWORD",
                     "FLASK_SECRET_KEY", "GHL_PRIVATE_TOKEN"):
            raw = _os.environ.get(name, "")
            if len(raw) > 1 and raw[0] in "\"'" and raw[-1] == raw[0]:
                out.append({"name": name,
                            "detail": f"{name} is wrapped in quotes. Render "
                                      f"stores them literally, so the real "
                                      f"value includes the quote characters. "
                                      f"Remove them."})
        return out

    def status(self) -> list[dict]:
        """Provider readiness, for /health and the status page.

        Every provider appears whether or not it is configured — a tool that
        is quietly degraded should be visible, not invisible.
        """
        def row(name, ok, required, note):
            return {"name": name, "state": "ok" if ok else ("error" if required else "warn"),
                    "required": required, "note": note}
        return [
            row("Secret key", bool(self.secret_key), True, "SECRET_KEY — sessions are not signed without it."),
            row("Hub password", bool(self.panel_password), True, "PANEL_PASSWORD — login is open without it."),
            row("Database", bool(self.database_url), False, "DATABASE_URL — falls back to local SQLite."),
            row("Public base URL", bool(self.public_base_url), False,
                "PUBLIC_BASE_URL — blank means Insites never posts scan completions back, so scans hang on 'running'."),
            row("Cloudinary", self.cloudinary_ready, False, "CLOUDINARY_URL — assets persist to local disk only."),
            row("OpenAI", self.openai_ready, False, "OPENAI_API_KEY — AI naming, FAQ, schema and copy fall back to templates."),
            row("Pexels", bool(self.pexels_key), False, "PEXELS_API / PEXELS_API_KEY — stock search provider."),
            row("Pixabay", bool(self.pixabay_key), False, "PIXABAY_API / PIXABAY_API_KEY — stock search provider."),
            row("Unsplash", bool(self.unsplash_key), False, "UNSPLASH_ACCESS_KEY — stock search provider."),
            row("remove.bg", bool(self.remove_bg_key), False, "REMOVE_BG_API_KEY — Background Remover is disabled without it."),
            row("Brandfetch", bool(self.brandfetch_key), False, "BRANDFETCH_API_KEY — logo and brand-colour lookup."),
            row("Google Fonts", bool(self.google_fonts_key), False, "GOOGLE_FONTS_API_KEY — optional; curated list used without it."),
            row("Insites", bool(self.insites_key), False, "INSITES_API_KEY — Site Scans disabled without it."),
            row("HeyGen", bool(self.heygen_key), False,
                "HEYGEN_API / HEYGEN_API_KEY — spokesperson scenes run in mock mode without it."),
            row("Runway", bool(self.runway_key), False,
                "RUNWAY_API / RUNWAY_API_KEY — AI video scenes run in mock mode without it."),
            row("Knack", bool(self.knack_app_id and self.knack_api_key), False, "KNACK_APP_ID / KNACK_API_KEY — client registry."),
            row("GoHighLevel", bool(self.ghl_token and self.ghl_company_id), False, "GHL_PRIVATE_TOKEN (or SMART1SUITE_PRIVATE_TOKEN) + GHL_COMPANY_ID (or SUITE_COMPANY_ID)."),
            row("Lead delivery to Suite",
                bool(self.ghl_token and self.ghl_lead_location_id
                     and self.ghl_lead_location_id != self.ghl_company_id),
                False,
                "GHL_LEAD_LOCATION_ID — the Smart 1 Marketing sub-account id, "
                "which must not be the agency company id. This is now the only "
                "delivery route: the HUB_LEAD_WEBHOOK_URL webhook is retired, so "
                "without this leads are stored and queued, not delivered."),
            row("Simvoly", bool(self.simvoly_key), False, "SIMVOLY_API_KEY — Sites admin."),
        ]

    def missing_required(self) -> list[str]:
        return [r["name"] for r in self.status() if r["required"] and r["state"] != "ok"]


settings = Settings()
