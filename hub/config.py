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
    pexels_key: str = field(default_factory=lambda: _s("PEXELS_API_KEY"))
    pixabay_key: str = field(default_factory=lambda: _s("PIXABAY_API_KEY"))
    unsplash_key: str = field(default_factory=lambda: _s("UNSPLASH_ACCESS_KEY"))
    remove_bg_key: str = field(default_factory=lambda: _s("REMOVE_BG_API_KEY"))
    brandfetch_key: str = field(default_factory=lambda: _s("BRANDFETCH_API_KEY"))
    google_fonts_key: str = field(default_factory=lambda: _s("GOOGLE_FONTS_API_KEY"))
    insites_key: str = field(default_factory=lambda: _s("INSITES_API_KEY"))
    knack_app_id: str = field(default_factory=lambda: _s("KNACK_APP_ID"))
    knack_api_key: str = field(default_factory=lambda: _s("KNACK_API_KEY"))
    ghl_token: str = field(default_factory=lambda: _s("GHL_PRIVATE_TOKEN"))
    ghl_company_id: str = field(default_factory=lambda: _s("GHL_COMPANY_ID"))
    simvoly_key: str = field(default_factory=lambda: _s("SIMVOLY_API_KEY"))

    # ---- behaviour ----
    ai_usage_log: bool = field(default_factory=lambda: _b("HUB_AI_USAGE_LOG", True))

    # ---- readiness ----
    @property
    def cloudinary_ready(self) -> bool:
        return self.cloudinary_url.startswith("cloudinary://")

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
            row("Pexels", bool(self.pexels_key), False, "PEXELS_API_KEY — stock search provider."),
            row("Pixabay", bool(self.pixabay_key), False, "PIXABAY_API_KEY — stock search provider."),
            row("Unsplash", bool(self.unsplash_key), False, "UNSPLASH_ACCESS_KEY — stock search provider."),
            row("remove.bg", bool(self.remove_bg_key), False, "REMOVE_BG_API_KEY — Background Remover is disabled without it."),
            row("Brandfetch", bool(self.brandfetch_key), False, "BRANDFETCH_API_KEY — logo and brand-colour lookup."),
            row("Google Fonts", bool(self.google_fonts_key), False, "GOOGLE_FONTS_API_KEY — optional; curated list used without it."),
            row("Insites", bool(self.insites_key), False, "INSITES_API_KEY — Site Scans disabled without it."),
            row("Knack", bool(self.knack_app_id and self.knack_api_key), False, "KNACK_APP_ID / KNACK_API_KEY — client registry."),
            row("GoHighLevel", bool(self.ghl_token and self.ghl_company_id), False, "GHL_PRIVATE_TOKEN / GHL_COMPANY_ID — Suite panel and billing audits."),
            row("Simvoly", bool(self.simvoly_key), False, "SIMVOLY_API_KEY — Sites admin."),
        ]

    def missing_required(self) -> list[str]:
        return [r["name"] for r in self.status() if r["required"] and r["state"] != "ok"]


settings = Settings()
