"""The Site Scan detail keeps every useful brand element Insites returned.

Run directly, like the rest of this repository's focused regression tests:

    python test_scan_brand.py
"""
from pathlib import Path

from modules.scans import brand

try:
    from flask import Flask, render_template
except ModuleNotFoundError:  # The bundled local Python is intentionally lean.
    Flask = None
    render_template = None


ROOT = Path(__file__).parent
_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


PAYLOAD = {
    "domain": "acme.example",
    "logo": {
        "has_detected_logo": True,
        "logo_url": "https://cdn.example/acme-primary.svg",
        # Insites currently documents one logo. Preserve extra variants if a
        # plan or future response includes them instead of silently dropping
        # everything after logo_url.
        "variants": [
            {"src": "/assets/acme-dark.svg", "theme": "dark"},
            {"image_url": "https://cdn.example/acme-icon.png"},
            {"src": "javascript:alert(1)"},
        ],
    },
    "favicon": {
        "has_favicon": True,
        "favicon_location": "/favicon.ico",
        "favicon_type": "image/x-icon",
        "favicon_is_too_small": True,
        "favicon_is_recommended_type": False,
    },
    "colour_scheme": {
        "primary_accent_colour": "#123",
        "secondary_accent_colour": "rgb(20, 40, 60)",
        "primary_background_colour": "#223344",
        "secondary_background_colour": "#fff",
        "primary_text_colour": "#123",  # same value, second role
        "secondary_text_colour": "not-a-color",
    },
    "website_screenshot": {
        "desktop_screenshot_url": "https://shots.example/desktop.jpg",
        "mobile_screenshot_url": "https://shots.example/mobile.jpg",
    },
    "mobile": {
        "mobile_screenshot_url": "https://shots.example/mobile.jpg",
        "tablet_screenshot_url": "https://shots.example/tablet.jpg",
    },
    "page_titles_and_descriptions": {
        "homepage_title_tag": "  ACME   makes everything better  ",
        "homepage_meta_description": "Tools built for people who build.",
    },
    "gdpr": {"has_google_font_api": True},
}


section("Every observed visual asset survives normalization")
identity = brand.identity(PAYLOAD, base_url="https://acme.example")
check("the card has something to show", identity["found"], True)
check("every unique color is shown", len(identity["palette"]), 4)
check("short hex expands", identity["palette"][0]["hex"], "#112233")
check("duplicate colors keep every role",
      identity["palette"][0]["roles"], ["Primary accent", "Primary text"])
check("rgb colors normalize safely", identity["palette"][1]["hex"], "#14283C")
check("invalid CSS never reaches an inline style",
      any(item["hex"] == "not-a-color" for item in identity["palette"]), False)

mark_urls = [item["url"] for item in identity["marks"]]
check("primary logo stays first", mark_urls[0],
      "https://cdn.example/acme-primary.svg")
check("future logo variants are retained", mark_urls[1:3], [
    "https://acme.example/assets/acme-dark.svg",
    "https://cdn.example/acme-icon.png",
])
check("favicon is displayed as a brand mark", mark_urls[-1],
      "https://acme.example/favicon.ico")
check("unsafe asset schemes are discarded",
      any(url.startswith("javascript:") for url in mark_urls), False)
check("favicon quality notes travel with the mark", len(identity["favicon_notes"]), 2)

check("desktop, mobile, and tablet previews are all present",
      [item["label"] for item in identity["previews"]],
      ["Desktop", "Mobile", "Tablet"])
check("duplicate mobile screenshot fields make one preview",
      len(identity["previews"]), 3)
check("homepage title is cleaned for display", identity["homepage_title"],
      "ACME makes everything better")
check("the font signal remains observed, not invented",
      identity["uses_google_fonts"], True)


section("The scan detail renders the brand as a first-class card")
template_path = ROOT / "modules" / "scans" / "templates" / "scan_detail.html"
if Flask is not None:
    render_app = Flask("scan_brand_test", template_folder=str(template_path.parent))
    with render_app.test_request_context("/"):
        page = render_template(
            "scan_detail.html",
            scan={
                "public_id": "scan_test", "domain": "acme.example",
                "business_name": "ACME", "status": "complete", "score": 82,
                "tier": "Dominant", "created_at": "2026-08-31",
            },
            brand=identity,
            fixes={"measured": False},
            speed={"score": None, "headline": "Not measured", "available": False},
            reports_menu=[], linkcheck_state="", detected={"name": "ACME"},
            insites_report_id="report-1", raw_json="",
        )

    check("brand card is visible", "Brand found on this website" in page, True)
    check("all logo and icon tiles render", page.count('class="brand-mark '), 4)
    check("all palette swatches render", page.count('class="brand-swatch"'), 4)
    check("all three website views render", page.count('class="brand-preview"'), 3)
    check("observed assets carry an approval caution",
          "brand candidates until the client approves them" in page, True)
    check("homepage messaging is visible",
          "Tools built for people who build." in page, True)
else:
    template = template_path.read_text(encoding="utf-8")
    check("brand card is wired into the scan detail",
          "Brand found on this website" in template, True)
    check("the template iterates every mark",
          "{% for mark in brand.marks %}" in template, True)
    check("the template iterates every color",
          "{% for color in brand.palette %}" in template, True)
    check("the template iterates every preview",
          "{% for preview in brand.previews %}" in template, True)
    check("observed assets carry an approval caution",
          "{{ brand.note }}" in template, True)
    app_source = (ROOT / "modules" / "scans" / "app.py").read_text(encoding="utf-8")
    check("the route passes normalized brand data",
          "brand=brand.identity(" in app_source, True)


section("A sparse Insites response is honest")
empty = brand.identity({"domain": "empty.example"})
check("missing brand data is not called a brand", empty["found"], False)
check("missing palette is empty", empty["palette"], [])
check("missing marks are empty", empty["marks"], [])
check("the empty note names what was absent",
      "did not return a logo" in empty["note"], True)


print(f"\n{_passed} passed, {_failed} failed")
raise SystemExit(1 if _failed else 0)
