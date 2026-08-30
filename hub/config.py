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


# ---------------------------------------------------------------------------
# The names one setting answers to.
#
# Naming drifted across modules and across Render: the stock keys are set as
# PEXELS_API / PIXABAY_API on this deployment, while some code was written
# against PEXELS_API_KEY / PIXABAY_API_KEY. Reading only one spelling means a
# key that IS configured reports as missing, and the tool silently degrades.
# Accept every spelling in use rather than forcing a rename in the dashboard.
#
# It is a table rather than fifteen argument lists because three things need
# the same answer and must not each keep their own copy: the fields below,
# `hub/integrity.py`, which fails any module reading one spelling of a setting
# set under another, and `env_report()`, which tells somebody standing in front
# of a new deployment which name actually supplied each value. When those three
# disagree the check passes while the Hub is misconfigured, which is the exact
# shape of failure this table exists to end.
#
# First name that is set wins, so the order is the order to prefer. Adding a
# spelling here is the whole fix — no call site changes.
# ---------------------------------------------------------------------------
ALIASES: dict[str, tuple[str, ...]] = {
    # The signed-session secret, and the one entry here with a bug behind it.
    # This file read SECRET_KEY / FLASK_SECRET_KEY; hub/auth.py and
    # hub/identity.py read SECRET_KEY / SESSION_SECRET. A deployment setting
    # only FLASK_SECRET_KEY therefore reported a healthy "Secret key" row while
    # auth.py fell through to an ephemeral secret — so every session died on
    # every restart, with nothing on any page saying why. One list, read by all
    # three.
    "secret_key": ("SECRET_KEY", "FLASK_SECRET_KEY", "SESSION_SECRET"),
    "pexels_key": ("PEXELS_API", "PEXELS_API_KEY", "PEXELS_KEY"),
    "pixabay_key": ("PIXABAY_API", "PIXABAY_API_KEY", "PIXABAY_KEY"),
    "unsplash_key": ("UNSPLASH_API", "UNSPLASH_ACCESS_KEY", "UNSPLASH_API_KEY",
                     "UNSPLASH_KEY"),
    "remove_bg_key": ("REMOVE_BG_API", "REMOVE_BG_API_KEY", "REMOVEBG_API_KEY"),
    "brandfetch_key": ("BRANDFETCH_API", "BRANDFETCH_API_KEY"),
    "google_fonts_key": ("GOOGLE_FONTS_API", "GOOGLE_FONTS_API_KEY"),
    "insites_key": ("INSITES_API", "INSITES_API_KEY"),
    # Commercial Builder's spokesperson scenes, AI video, voiceover and final
    # render. Each read os.environ at *import* under one spelling, which froze
    # the value at boot and missed the other two — the same drift that made
    # Pexels report "no key set" with the key plainly present.
    "heygen_key": ("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY"),
    "runway_key": ("RUNWAY_API", "RUNWAY_API_KEY", "RUNWAY_KEY"),
    "elevenlabs_key": ("ELEVENLABS_API", "ELEVENLABS_API_KEY", "ELEVENLABS_KEY"),
    "creatomate_key": ("CREATOMATE_API", "CREATOMATE_API_KEY", "CREATOMATE_KEY"),
    "ghl_token": ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"),
    "ghl_company_id": ("GHL_COMPANY_ID", "SUITE_COMPANY_ID"),
    # The sub-account leads are written into. Deliberately NOT defaulted to the
    # company id: they are different id spaces, and a companyId sent where a
    # locationId belongs addresses the agency silently.
    "ghl_lead_location_id": ("GHL_LEAD_LOCATION_ID", "SMART1_MARKETING_LOCATION_ID",
                             "GHL_ACCOUNTING_LOCATION_ID"),
    "simvoly_key": ("SIMVOLY_API_KEY", "SIMVOLY_KEY"),
}
# Only names that are actually in use — in this repo, or set on the Render
# account this Hub runs in. A speculative spelling costs nothing to resolve and
# a great deal to police: every module reading the one real name is then a
# finding in /api/integrity, and twenty findings about a variable nobody has
# ever set is how a check gets switched off. OPENAI_API_KEY, KNACK_APP_ID and
# KNACK_API_KEY are therefore deliberately absent: each has exactly one
# spelling anywhere, so there is nothing for an alias to fix.


def _alias(setting: str, default: str = "") -> str:
    """The value of a setting, under whichever of its names is set."""
    for n in ALIASES.get(setting, (setting.upper(),)):
        v = _s(n)
        if v:
            return v
    return default


def _cloudinary_url() -> str:
    """CLOUDINARY_URL, or one built from the three separate parts."""
    url = _s("CLOUDINARY_URL")
    if url:
        return url
    cloud = _s("CLOUDINARY_CLOUD_NAME")
    key = _s("CLOUDINARY_API_KEY")
    secret = _s("CLOUDINARY_API_SECRET")
    if not (cloud and key and secret):
        return ""
    from urllib.parse import quote
    # The secret is what ends up in an Authorization computation, never in a
    # page, so quoting is about a stray character breaking the parse rather
    # than about escaping anything hostile.
    return f"cloudinary://{quote(key, safe='')}:{quote(secret, safe='')}@{quote(cloud, safe='')}"


@dataclass(frozen=True)
class Settings:
    # ---- core ----
    secret_key: str = field(default_factory=lambda: _alias("secret_key"))
    panel_password: str = field(default_factory=lambda: _s("PANEL_PASSWORD"))
    database_url: str = field(default_factory=lambda: _s("DATABASE_URL"))
    public_base_url: str = field(default_factory=lambda: _s("PUBLIC_BASE_URL").rstrip("/"))
    data_dir: str = field(default_factory=lambda: _s("HUB_DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else "data"))
    max_upload_mb: int = field(default_factory=lambda: _i("MAX_UPLOAD_MB", 100))
    # How long a sent proposal's pricing stands. Read by hub/quote_validity.py,
    # which clamps it -- a zero would expire a quote the moment it was sent.
    proposal_validity_days: int = field(default_factory=lambda: _i("PROPOSAL_VALIDITY_DAYS", 30))

    # ---- OpenAI ----
    openai_key: str = field(default_factory=lambda: _s("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _s("OPENAI_MODEL", "gpt-4o-mini"))
    openai_vision_model: str = field(default_factory=lambda: _s("OPENAI_VISION_MODEL") or _s("OPENAI_MODEL", "gpt-4o"))
    openai_image_model: str = field(default_factory=lambda: _s("OPENAI_IMAGE_MODEL", "gpt-image-1"))
    openai_timeout: int = field(default_factory=lambda: _i("OPENAI_TIMEOUT", 90))
    openai_retries: int = field(default_factory=lambda: _i("OPENAI_RETRIES", 2))

    # ---- Cloudinary ----
    # Cloudinary publishes the credential two ways and this Render account sets
    # both: one CLOUDINARY_URL, and the three parts CLOUDINARY_CLOUD_NAME /
    # CLOUDINARY_API_KEY / CLOUDINARY_API_SECRET. Eight modules read the three
    # parts directly and every shared path reads the URL, so a deployment given
    # only the three-part group had a working Image Creator and a
    # `cloudinary_ready` of False — hub.storage silently on local disk, with
    # every screen looking healthy. Compose the URL when it is absent rather
    # than asking somebody to set a fourth variable that repeats the other
    # three.
    cloudinary_url: str = field(default_factory=lambda: _cloudinary_url())

    # ---- image pipeline ----
    max_edge: int = field(default_factory=lambda: _i("SEO_IMAGES_MAX_EDGE", 2400))
    preview_edge: int = field(default_factory=lambda: _i("HUB_PREVIEW_EDGE", 640))

    # ---- providers ----
    # Every provider credential resolves through ALIASES above, so a key set
    # under any spelling this codebase or this Render account has ever used is
    # found. Nothing here restates a name list.
    pexels_key: str = field(default_factory=lambda: _alias("pexels_key"))
    pixabay_key: str = field(default_factory=lambda: _alias("pixabay_key"))
    unsplash_key: str = field(default_factory=lambda: _alias("unsplash_key"))
    remove_bg_key: str = field(default_factory=lambda: _alias("remove_bg_key"))
    brandfetch_key: str = field(default_factory=lambda: _alias("brandfetch_key"))
    google_fonts_key: str = field(default_factory=lambda: _alias("google_fonts_key"))
    insites_key: str = field(default_factory=lambda: _alias("insites_key"))
    heygen_key: str = field(default_factory=lambda: _alias("heygen_key"))
    runway_key: str = field(default_factory=lambda: _alias("runway_key"))
    elevenlabs_key: str = field(default_factory=lambda: _alias("elevenlabs_key"))
    creatomate_key: str = field(default_factory=lambda: _alias("creatomate_key"))
    knack_app_id: str = field(default_factory=lambda: _s("KNACK_APP_ID"))
    knack_api_key: str = field(default_factory=lambda: _s("KNACK_API_KEY"))
    ghl_token: str = field(default_factory=lambda: _alias("ghl_token"))
    ghl_company_id: str = field(default_factory=lambda: _alias("ghl_company_id"))
    ghl_lead_location_id: str = field(default_factory=lambda: _alias("ghl_lead_location_id"))
    simvoly_key: str = field(default_factory=lambda: _alias("simvoly_key"))

    # ---- the proposal's target-area map ----
    # Map tiles, and deliberately no key. Every other provider here bills for
    # something; a base map does not have to, and asking a deployment for a
    # Google Maps key it has never had is the failure `modules/ads_builder`
    # already fixed once -- a page inviting a credential nobody has set reads
    # as broken while the feature it gates works perfectly well without one.
    #
    # So the default is OpenStreetMap's own tiles, whose licence asks for the
    # attribution that `hub/target_map.py` prints onto every image it makes.
    # Both are variables rather than constants for the deployment that wants
    # its own tile server or a keyed one: the URL carries the key if there is
    # a key, which is one setting rather than a second code path nobody here
    # would ever exercise.
    map_tile_url: str = field(default_factory=lambda: _s(
        "MAP_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"))
    map_tile_attribution: str = field(default_factory=lambda: _s(
        "MAP_TILE_ATTRIBUTION", "© OpenStreetMap contributors"))
    # A tile server identifies callers by User-Agent and OSM's policy requires
    # a real one; an unset PUBLIC_BASE_URL must not make this blank.
    map_user_agent: str = field(default_factory=lambda: _s(
        "MAP_USER_AGENT",
        "Smart1Hub/1.0 (+https://smart1.agency; proposal target maps)"))
    map_enabled: bool = field(default_factory=lambda: _b("PROPOSAL_MAP", True))

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
            "ads_logos": "smart1-ads-logos",
            # Logos filed into a client's own gallery from their brand record
            # or their last site scan -- hub/client_logos.py.
            "client_logos": "smart1-client-logos",
            "backups": "smart1-backups",
            # Photographs a client's own location manager attaches to a
            # content request -- modules/social_planner/intake.py.
            "social_requests": "smart1-social-requests",
            # Anything collected against a prospect before they are a client:
            # the mock-up, the screenshot, the signed page, the rate sheet
            # somebody emailed over -- hub/prospect.py. Its own folder rather
            # than the client tree, because a prospect has no client key yet
            # and filing them together is how one company's assets end up on
            # another's record.
            "prospects": "smart1-prospects",
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
        # PUBLIC_BASE_URL is the origin, not a route on it. One env group here
        # carries the Google Ads OAuth callback in it — the same string as
        # GOOGLE_ADS_REDIRECT_URI, a path and all. A service-level value
        # overrides a group's, so this deployment is fine and the next one to
        # link that group would not be: every share link, every landing URL and
        # every Insites callback would be built with /tools/ads/oauth/callback
        # in the middle of it, and each would 404 somewhere nobody is watching.
        base = _os.environ.get("PUBLIC_BASE_URL", "").strip().strip('"').strip("'")
        if base:
            tail = base.split("://", 1)[-1]
            if "/" in tail.rstrip("/"):
                out.append({"name": "PUBLIC_BASE_URL",
                            "detail": "PUBLIC_BASE_URL has a path in it "
                                      f"({base}). It is the site's origin and "
                                      "nothing else — every URL the Hub builds "
                                      "is appended to it, so a path here is "
                                      "carried into share links, landing pages "
                                      "and the Insites scan callback. Set it to "
                                      "the scheme and host only."})
        out.extend(self.env_problems())
        return out

    # What each setting is for, in the words somebody standing in front of a
    # fresh deployment would use. env_report() prints these; nothing else does.
    LABELS = {
        "secret_key": "Signed sessions",
        "pexels_key": "Pexels stock",
        "pixabay_key": "Pixabay stock",
        "unsplash_key": "Unsplash stock",
        "remove_bg_key": "remove.bg",
        "brandfetch_key": "Brandfetch logo lookup",
        "google_fonts_key": "Google Fonts",
        "insites_key": "Insites site scans",
        "heygen_key": "HeyGen spokesperson",
        "runway_key": "Runway AI video",
        "elevenlabs_key": "ElevenLabs voiceover",
        "creatomate_key": "Creatomate render",
        "ghl_token": "Smart 1 Suite token",
        "ghl_company_id": "Suite agency (company) id",
        "ghl_lead_location_id": "Suite sub-account leads are written into",
        "simvoly_key": "Smart 1 Sites",
    }

    def spellings(self, setting: str) -> str:
        """Every name a setting answers to, for a note on a page.

        Read from ALIASES rather than typed into the note, because a note that
        names two of three spellings sends somebody to add a variable the Hub
        would have found anyway.
        """
        return " / ".join(ALIASES.get(setting, ()))

    def env_report(self) -> list[dict]:
        """Which name supplied each setting, and which names were ignored.

        The point of the alias table is that a key set under any spelling
        resolves. The cost of it is that nobody can tell *which* spelling did,
        and that matters twice on a second deployment: a variable set under a
        name this Hub does not read looks exactly like a variable that took
        effect, and two names set to different values silently resolve to
        whichever comes first in ALIASES — the person who set the other one
        sees no sign of it anywhere.

        So: one row per setting, naming every spelling accepted, the one that
        answered, and any that were set and ignored. A value is never carried:
        this is rendered into a page and pasted into chats, the rule
        services/provider_check.py already works to.
        """
        rows = []
        for setting, names in ALIASES.items():
            present = [n for n in names if _s(n)]
            resolved = present[0] if present else ""
            values = {n: _s(n) for n in present}
            ignored = present[1:]
            # Two names holding the *same* value is somebody being thorough.
            # Two holding different values is the silent one.
            conflict = len({v for v in values.values()}) > 1
            rows.append({
                "setting": setting,
                "label": self.LABELS.get(setting, setting),
                "names": list(names),
                "resolved": resolved,
                "set": bool(resolved),
                "ignored": ignored,
                "conflict": conflict,
                "placeholder": self.is_placeholder(values.get(resolved, "")),
                "note": ("Not set under any of these names."
                         if not resolved else
                         f"Read from {resolved}."
                         + (f" Also set, and ignored: {', '.join(ignored)}."
                            if ignored else "")
                         + (" Those hold different values, so one of them is"
                            " doing nothing." if conflict else "")),
            })
        return rows

    def env_problems(self) -> list[dict]:
        """The rows of env_report() that somebody has to act on."""
        out = []
        for r in self.env_report():
            if r["conflict"]:
                out.append({"name": r["resolved"],
                            "detail": f"{r['label']}: {r['resolved']} and "
                                      f"{', '.join(r['ignored'])} are both set to "
                                      f"different values. {r['resolved']} wins and "
                                      f"the rest do nothing — if the value you "
                                      f"just changed is in one of the others, it "
                                      f"has not taken effect."})
            if r["placeholder"]:
                out.append({"name": r["resolved"],
                            "detail": f"{r['label']}: {r['resolved']} is still an "
                                      f"example value. It looks configured and "
                                      f"will fail at the provider."})
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
            row("Secret key", bool(self.secret_key), True,
                f"{self.spellings('secret_key')} — sessions are not signed without it, "
                "so everyone is logged out by every restart."),
            row("Hub password", bool(self.panel_password), True, "PANEL_PASSWORD — login is open without it."),
            row("Database", bool(self.database_url), False, "DATABASE_URL — falls back to local SQLite."),
            row("Public base URL", bool(self.public_base_url), False,
                "PUBLIC_BASE_URL — blank means Insites never posts scan completions back, so scans hang on 'running'."),
            row("Cloudinary", self.cloudinary_ready, False,
                "CLOUDINARY_URL, or CLOUDINARY_CLOUD_NAME + CLOUDINARY_API_KEY + "
                "CLOUDINARY_API_SECRET — assets persist to local disk only."),
            row("OpenAI", self.openai_ready, False,
                "OPENAI_API_KEY — AI naming, FAQ, schema and copy fall back to templates."),
            row("Pexels", bool(self.pexels_key), False, f"{self.spellings('pexels_key')} — stock search provider."),
            row("Pixabay", bool(self.pixabay_key), False, f"{self.spellings('pixabay_key')} — stock search provider."),
            row("Unsplash", bool(self.unsplash_key), False, f"{self.spellings('unsplash_key')} — stock search provider."),
            row("remove.bg", bool(self.remove_bg_key), False, f"{self.spellings('remove_bg_key')} — Background Remover is disabled without it."),
            row("Brandfetch", bool(self.brandfetch_key), False, f"{self.spellings('brandfetch_key')} — logo and brand-color lookup."),
            row("Google Fonts", bool(self.google_fonts_key), False, f"{self.spellings('google_fonts_key')} — optional; curated list used without it."),
            row("Insites", bool(self.insites_key), False, f"{self.spellings('insites_key')} — Site Scans disabled without it."),
            row("HeyGen", bool(self.heygen_key), False,
                f"{self.spellings('heygen_key')} — spokesperson scenes run in mock mode without it."),
            row("Runway", bool(self.runway_key), False,
                f"{self.spellings('runway_key')} — AI video scenes run in mock mode without it."),
            row("ElevenLabs", bool(self.elevenlabs_key), False,
                f"{self.spellings('elevenlabs_key')} — commercial voiceover runs in mock mode without it."),
            row("Creatomate", bool(self.creatomate_key), False,
                f"{self.spellings('creatomate_key')} — commercials cannot be rendered without it."),
            row("Knack", bool(self.knack_app_id and self.knack_api_key), False,
                "KNACK_APP_ID / KNACK_API_KEY — client registry."),
            row("GoHighLevel", bool(self.ghl_token and self.ghl_company_id), False,
                f"{self.spellings('ghl_token')} + {self.spellings('ghl_company_id')}."),
            row("Lead delivery to Suite",
                bool(self.ghl_token and self.ghl_lead_location_id
                     and self.ghl_lead_location_id != self.ghl_company_id),
                False,
                "GHL_LEAD_LOCATION_ID — the Smart 1 Marketing sub-account id, "
                "which must not be the agency company id. This is now the only "
                "delivery route: the HUB_LEAD_WEBHOOK_URL webhook is retired, so "
                "without this leads are stored and queued, not delivered."),
            row("Simvoly", bool(self.simvoly_key), False, f"{self.spellings('simvoly_key')} — Sites admin."),
        ]

    def missing_required(self) -> list[str]:
        return [r["name"] for r in self.status() if r["required"] and r["state"] != "ok"]


def export_cloudinary_url(cfg: "Settings") -> None:
    """Put a composed Cloudinary credential where the SDK will find it.

    A composed Cloudinary credential has to reach the Cloudinary SDK, and the SDK
    reads CLOUDINARY_URL out of the environment itself — `cloudinary.config()`
    with no arguments is how hub/storage.py and nine modules configure it. So a
    deployment given only CLOUDINARY_CLOUD_NAME / CLOUDINARY_API_KEY /
    CLOUDINARY_API_SECRET would have a `settings.cloudinary_url` that nothing
    else could see, and every upload would still go to the local disk that is
    wiped on each redeploy — silently, because a module that cannot reach
    Cloudinary falls back rather than erroring.

    This is the one place that reaches all of them. It never overwrites a
    CLOUDINARY_URL that was set: an explicit value is somebody's decision, and
    the three parts are the fallback for when there is none.
    """
    if cfg.cloudinary_url and not _s("CLOUDINARY_URL"):
        os.environ["CLOUDINARY_URL"] = cfg.cloudinary_url


settings = Settings()
export_cloudinary_url(settings)
