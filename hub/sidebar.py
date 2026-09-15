"""Shared Hub sidebar, injected into every module page.

Modules keep their own layouts; this adds the same fixed navy sidebar the Hub
shell uses (scoped s1hub- class names to avoid collisions) and shifts the
page right to make room. Below 950px the sidebar becomes a slide-out drawer.

Since 2026-09-14 the nav is two tiers: five pinned rows, then twelve
*departments* (six Departments, six Tools) that stay folded -- see PINNED,
LEAVES and SECTIONS below. `_ITEMS` is still exported as the flat list the
rest of the Hub reads, derived from the tree so the two cannot disagree.

A named group inside a department's flyout is still a mega-menu column --
roll over Sales and Sales Tools still lists every tool under it -- but its
own heading is a link now, to that same group on the department's index page
(/views/<slug>#<group-anchor>). Rolling over is the fast path for somebody
who already knows what they want; clicking the heading is "just show me the
page" for somebody who does not. `department_view.html` renders the anchor
from the identical `_group_anchor()` so the two can never point past each
other.
"""

import re

# The keys are what render_sidebar() matches `active` against, so they are
# fixed points: reordering or relabelling is free, renaming a key silently
# stops that page highlighting itself in the nav.
EVERYONE = "everyone"
ADMIN_ONLY = "admin"

# ----------------------------------------------------------- the icon rail
# Every tool on /creative opens with the nav as an icon rail. A creative tool
# is a workbench -- a canvas with controls either side of it, a storyboard, a
# gallery of squares, a waveform -- and the nav is 224px of a laptop that the
# work itself needs. Losing it is what turns a step into a horizontal scroll,
# which is the note the Display Ad Builder's own entry here made when it was
# the only one named.
#
# The list is the tiles on hub/templates/creative.html, and it has to be:
# that page is the answer to "which tools are creative", so a tool tiled
# there and missing here would open with a nav nobody asked for while every
# tool beside it behaved differently, with nothing on either screen saying
# why. test_menu_layout.py asserts the two agree in both directions.
#
# This is a *default*, not a lock. A stored preference wins in both
# directions (see the JS below), so somebody who opens the menu on Image
# Creator keeps it open there and everywhere else, and the toggle is on
# screen either way. A page that fights the person using it is worse than a
# page with a nav they did not want.
CREATIVE_PREFIXES = (
    # Images
    "/tools/display-ads",
    "/tools/image-creator",
    "/tools/magic-resize",
    "/tools/image",              # Image Optimizer & Resizer
    "/tools/bg-remover",
    "/tools/page-images",
    "/tools/image-picker",
    "/tools/seo-images",
    "/tools/landing-ads",
    "/tools/stock-photos",
    # Videos
    "/tools/commercial-builder",
    "/tools/video-backgrounds",
    "/tools/paint-animation",
    "/tools/vox-explainer",
    "/tools/dead-air",
    "/tools/vertical-reframe",
    # Audio
    "/tools/customer-voices",
    "/tools/radio-promo",
    "/tools/fan-radio",
    "/tools/radio-scripts",
)


def collapses_by_default(path: str) -> bool:
    """Does the tool at ``path`` open with the nav as an icon rail?

    One decision, read by all three places that render the nav: the hub app's
    injector, `HubBar` in wsgi.py for the twenty mounted modules, and the
    `hub_sidebar` global that base.html calls directly. Three copies of a
    prefix test is how two of them come to disagree about one tool.

    Matched on path *segments* through `hub/access.py`'s own matcher, not on
    a bare `startswith`: `/tools/image` must not claim a future
    `/tools/imagery-report`, and `/tools/image-creator` is underneath it on
    purpose. `/creative` itself is an index, not a workbench, and is
    deliberately not in the list.
    """
    try:
        from .access import path_matches
    except Exception:  # noqa: BLE001 — the nav must never break a page
        return False
    return path_matches(path or "", CREATIVE_PREFIXES)



# ------------------------------------------------------------------ the nav
# Two tiers, decided 2026-09-14. Five rows are pinned at the top because they
# are what everybody opens the Hub with. Everything else is a *department* --
# one row that stays folded until it is rolled over (a flyout), pinned open
# (the chevron, and the only way on a phone), or clicked (its index page at
# /views/<slug>, every tool in it as tiles). Twelve rows on a laptop instead
# of forty-one, and nothing less reachable than before.
#
# A tool is defined ONCE, in LEAVES, and referenced by key from as many
# departments as it belongs to: the calculators are a Sales tool and an Ad
# Tool, Reports is Product Success and Client Success and SEO. One label and
# one href per tool, however many places list it, so a rename is one edit.
#
# Keys are what render_sidebar() matches `active` against and what
# hub/__init__.py's _MOUNT_ACTIVE_HUB and wsgi.py's HubBar hand in, so the
# ones that existed before this reshuffle keep their spelling. Renaming a key
# silently stops that page highlighting itself in the nav.

PINNED = [
    ("dashboard", "/", "&#127968;", "Dashboard"),
    ("ask_smarthub", "/ask-smarthub", "&#10024;", "Ask SmartHub"),
    ("c360", "/client360", "&#127919;", "Client 360"),
    ("myclients", "/my-clients", "&#128203;", "My Clients"),
    ("deptviews", "/views", "&#128065;", "My View"),
]

# key -> (href, icon, label). Alphabetical by key so a duplicate is caught by
# eye; the order tools appear on screen is the departments' business below.
LEAVES = {
    "acct_requests":     ("/qa/accounting-requests", "&#128203;", "Accounting Requests"),
    "active_clients":    ("/qa/active-clients", "&#9679;", "Active Clients"),
    "activity":          ("/activity", "&#128220;", "Activity Log"),
    "ad_assets":         ("/tools/ad-assets", "&#128193;", "Ad Assets"),
    "ads":               ("/tools/ads/", "&#128227;", "PPC Builder"),
    "ads_grader":        ("/tools/ads-grader/", "&#128200;", "Google Ads Grader"),
    "assign_clients":    ("/qa/client-owners", "&#128100;", "Assign Clients"),
    "bg_remover":        ("/tools/bg-remover/", "&#9986;", "Background Remover"),
    "billing_cmp":       ("/qa/billing-comparison", "&#128181;", "Customer Billing Comparison"),
    "calc_audio":        ("/tools/calculators/internal/digital-audio", "&#127911;", "Digital Audio Calculator"),
    "calc_ctv":          ("/tools/calculators/internal/ctv", "&#128250;", "Connected TV Calculator"),
    "calc_dooh":         ("/tools/calculators/internal/dooh", "&#128253;", "DOOH Calculator"),
    "calc_ims":          ("/tools/calculators/internal/trade", "&#129309;", "IMS Calculator"),
    "campaign_assets":   ("/tools/campaign-assets", "&#128230;", "Campaign Assets Needed"),
    "check_recon":       ("/tools/check-reconciliation/", "&#129534;", "Check Reconciliation"),
    "clients":           ("/clients", "&#128101;", "Clients"),
    "commercial":        ("/tools/commercial-builder/", "&#127916;", "Commercial Builder"),
    "commercial_lib":    ("/tools/commercial-builder/library", "&#128218;", "Commercial Library"),
    "creative":          ("/creative", "&#127912;", "Creative"),
    "cs_ai_tools":       ("/creative-studio/ai-tools", "&#129302;", "AI Tools"),
    "cs_approvals":      ("/creative-studio/approvals", "&#9989;", "Approvals"),
    "cs_brand_kits":     ("/creative-studio/brand-kits", "&#127912;", "Brand Kits"),
    "cs_campaigns":      ("/creative-studio/campaigns", "&#128188;", "Campaigns"),
    "cs_dashboard":      ("/creative-studio/", "&#127912;", "Create"),
    "cs_library":        ("/creative-studio/library", "&#128218;", "Spot Library"),
    "cs_media":          ("/creative-studio/media", "&#128247;", "Media Library"),
    "cs_projects":       ("/creative-studio/projects", "&#128196;", "Projects"),
    "cs_templates":      ("/creative-studio/templates", "&#128209;", "Templates"),
    "cs_usage":          ("/creative-studio/usage", "&#128176;", "Usage &amp; Costs"),
    "customer_voices":   ("/tools/customer-voices/", "&#127908;", "Customer Voices"),
    "dead_air":          ("/tools/dead-air/", "&#9986;", "Dead Air Cutter"),
    "deptviews_manage":  ("/views/manage", "&#9881;", "Department Views"),
    "diagnostics":       ("/diagnostics", "&#128300;", "Diagnostics"),
    "display_ads":       ("/tools/display-ads/_hub/start", "&#128444;", "Display Ad Builder"),
    "domains":           ("/tools/domains", "&#128197;", "Domain Renewals"),
    "fan_radio":         ("/tools/fan-radio/", "&#127944;", "Fan Radio"),
    "ga4":               ("/google/ga-tools", "&#128200;", "GA4 Tools"),
    "gbp":               ("/google/gmb-tools", "&#128205;", "Business Profile"),
    "ghl_billing_month": ("/qa/ghl-billing-this-month", "&#128179;", "Suite Billing This Month"),
    "ghl_billing_none":  ("/qa/ghl-billing-no-products", "&#128681;", "Suite Billing, No Active Product"),
    "google":            ("/google/", "&#128202;", "Google"),
    "google_access":     ("/tools/google-access/", "&#128273;", "Google Access"),
    "google_accounts":   ("/qa/google-accounts", "&#128506;", "Google Accounts &amp; Mapping"),
    "google_finder":     ("/google/", "&#128269;", "Google Finder"),
    "google_history":    ("/google/history", "&#128220;", "Google History &amp; Logs"),
    "gpt_ads":           ("/tools/gpt-ads/", "&#129302;", "GPT Ads Builder"),
    "gtm":               ("/google/gtm-tools", "&#127991;", "GTM Tools"),
    "house_urls":        ("/tools/seo-images/house", "&#127968;", "House URLs"),
    "image_creator":     ("/tools/image-creator/", "&#128444;", "Image Creator"),
    "image_opt":         ("/tools/image/", "&#128444;", "Image Optimizer &amp; Resizer"),
    "image_picker":      ("/tools/image-picker/", "&#128228;", "Client Image Uploads"),
    "inactive_ga":       ("/tools/google-access/qa-inactive/", "&#128201;", "Inactive GA &amp; GTM"),
    "industry_factory":  ("/sales/industry-factory", "&#127968;", "Industry Factory"),
    "industry_prospects": ("/sales/industry-prospects", "&#128269;", "Industry Prospects"),
    "invoice_off":       ("/qa/invoice-off", "&#9878;", "Invoice Off Report"),
    "io_builder":        ("/tools/io/", "&#128221;", "IO Builder"),
    "io_money":          ("/qa/io-money-mismatch", "&#9878;", "Campaigns Not At Order Value"),
    "io_not_in_knack":   ("/qa/io-not-in-knack", "&#128203;", "Orders With No Campaign"),
    "knack_field_map":   ("/qa/knack-field-map", "&#128279;", "Knack Field Map"),
    "land_boat":         ("/land/boat/", "&#128676;", "Boat Dealer Landing Page"),
    "land_hvac":         ("/land/hvac/", "&#127777;", "HVAC Landing Page"),
    "land_legal":        ("/land/legal/", "&#9878;", "Legal Landing Page"),
    "land_recruit":      ("/land/recruit/", "&#128188;", "Recruitment Landing Page"),
    "land_restaurant":   ("/land/restaurant/", "&#127860;", "Restaurant Landing Page"),
    "land_rv":           ("/land/rv/", "&#128656;", "RV Dealer Landing Page"),
    "land_ski":          ("/land/ski/", "&#127954;", "Ski Resort Landing Page"),
    "land_stadium":      ("/land/stadium/", "&#127944;", "Stadium to Screen"),
    "land_tourism":      ("/land/tourism/", "&#127796;", "Tourism Landing Page"),
    "landing":           ("/sales/landing", "&#128187;", "Landing Pages"),
    "landing_ads":       ("/tools/landing-ads/", "&#128444;", "Landing Page Ads"),
    "landing_maker":     ("/sales/landing", "&#128187;", "Landing Page Maker"),
    "leads":             ("/sales/leads", "&#128229;", "Leads"),
    "leads_existing":    ("/qa/sell-to-clients", "&#128176;", "Existing Client Leads"),
    "lost_by_partner":   ("/qa/lost-by-partner", "&#8595;", "Ran Last Month, Not This Month"),
    "lsa":               ("/tools/lsa/", "&#128200;", "LSA Ads"),
    "youtube_ads":       ("/tools/youtube-ads/", "&#9654;", "YouTube Ads"),
    "magic_resize":      ("/tools/magic-resize/", "&#128207;", "Magic Resize"),
    "marketing_audit":   ("/tools/marketing-audit/", "&#128202;", "Marketing Efficiency Audit"),
    "match_google":      ("/tools/google-match", "&#128202;", "Match Google Accounts"),
    "match_sites":       ("/tools/sites-match", "&#128279;", "Match Sites to Clients"),
    "match_suite":       ("/tools/suite-match", "&#128279;", "Match Suite Sub-accounts"),
    "media_calcs":       ("/tools/calculators/", "&#128425;", "Media Calculators"),
    "monthly_promises":  ("/qa/monthly-promises", "&#128197;", "Promises Not Kept This Month"),
    "msa":               ("/msa/", "&#128220;", "Master Services Agreement"),
    "myclients":         ("/my-clients", "&#128203;", "My Clients"),
    "no_analytics":      ("/qa/no-analytics", "&#128200;", "Clients Without Analytics"),
    "no_dashboards":     ("/qa/no-dashboards", "&#9888;", "No Dashboards"),
    "no_gtm":            ("/qa/no-gtm", "&#127991;", "Clients Without GTM"),
    "page_images":       ("/tools/page-images/", "&#128444;", "Page Image Optimizer"),
    "paint_animation":   ("/tools/paint-animation/", "&#127912;", "Paint Animation"),
    "partner_scorecard": ("/qa/partner-scorecard", "&#129309;", "Partner Scorecard"),
    "pdf":               ("/tools/pdf/", "&#128462;", "PDF Optimizer"),
    "proposal_execution": ("/proposal-execution", "&#9881;", "Proposal Execution"),
    "qa":                ("/qa", "&#9989;", "QA Reports"),
    "qatasks":           ("/qa-tasks", "&#128221;", "QA Tasks"),
    "radio_promo":       ("/tools/radio-promo/", "&#128251;", "Radio Ad Creator"),
    "radio_scripts":     ("/tools/radio-scripts/", "&#128221;", "Radio Scripts"),
    "reports":           ("/reports/", "&#128202;", "Reports"),
    "reports_cost":      ("/reports/cost", "&#128181;", "Cost Report"),
    "reports_pacing":    ("/reports/pacing", "&#9201;", "Pacing"),
    "sales_scorecard":   ("/qa/sales-scorecard", "&#127942;", "Salesperson Scorecard"),
    "salesb":            ("/sales/builder/", "&#128196;", "Proposal Builder"),
    "scan_all":          ("/scans/bulk", "&#9776;", "Scan All Clients"),
    "scan_widgets":      ("/scans/widgets", "&#128269;", "Scan Widgets"),
    "scans":             ("/scans/", "&#128200;", "Site Scans"),
    "sell_to_clients":   ("/qa/sell-to-clients", "&#128176;", "What We Could Sell Each Client"),
    "seo":               ("/seo", "&#128269;", "SEO Clients"),
    "seo_images":        ("/tools/seo-images/", "&#128444;", "SEO Image Pipeline"),
    "seo_intelligence":  ("/seo/intelligence/", "&#129504;", "SEO Intelligence"),
    "short_links":       ("/tools/short-links", "&#128279;", "Client Links"),
    "site_blocks":       ("/tools/site-blocks/", "&#129513;", "Website Blocks"),
    "sites":             ("/sites/", "&#127760;", "Sites"),
    "sites_billing":     ("/qa/sites-billing", "&#127760;", "Sites Billing Report"),
    "sites_builder":     ("/tools/sites-builder", "&#10024;", "Smart 1 Sites Builder"),
    "skills360":         ("/tools/360-skills/", "&#129513;", "360 Skills"),
    "smartforecast":     ("/tools/smartforecast/", "&#127780;", "SmartForecast Dynamic Website"),
    "social":            ("/tools/social/", "&#128172;", "Social Content Planner"),
    "stale_90":          ("/qa/stale-90", "&#8987;", "No Live Product in 90 Days"),
    "stale_creative":    ("/qa/stale-creative", "&#9203;", "Stale Creative"),
    "status":            ("/status", "&#128678;", "System Status"),
    "stock_photos":      ("/tools/stock-photos/", "&#128247;", "Stock Photo Search"),
    "studio":            ("/creative-studio/", "&#127916;", "Studio"),
    "suite":             ("/suite/", "&#129520;", "Suite"),
    "tickets":           ("/tools/tickets/", "&#127915;", "Web Tickets"),
    "tools":             ("/tools", "&#128295;", "Client Tools"),
    "unattached_images": ("/qa/unattached-images", "&#128444;", "Unattached Images"),
    "users":             ("/diagnostics/users", "&#128100;", "Users"),
    "utm":               ("/tools/utm/", "&#128279;", "UTM Builder"),
    "vertical_reframe":  ("/tools/vertical-reframe/", "&#128241;", "Vertical Reframe"),
    "video_search":      ("/tools/video-backgrounds/", "&#127909;", "Video Search"),
    "vox_explainer":     ("/tools/vox-explainer/", "&#127908;", "Vox Explainer"),
    "weather_setup":     ("/tools/weather-setup/", "&#127780;", "Weather Trigger Setup"),
    "webmaster_reports": ("/seo/webmaster", "&#128202;", "Webmaster Reports"),
    "webmaster_tools":   ("/google/webmaster-tools", "&#128421;", "Webmaster Tools"),
    "website_audit":     ("/tools/website-audit", "&#128269;", "Website Audit"),
}

# Reused groupings, so the same list is not typed twice.
_CALCULATORS = ["calc_audio", "calc_ctv", "calc_dooh", "calc_ims"]
_GOOGLE_TOOLS = ["google_access", "google_finder", "ga4", "gtm", "webmaster_tools",
                 "gbp", "google_history"]
_MODULES = ["clients", "google", "sites", "suite"]
_AUDIO = ["customer_voices", "radio_promo", "fan_radio", "radio_scripts"]
_VIDEOS = ["commercial", "commercial_lib", "video_search", "paint_animation",
           "vox_explainer", "dead_air", "vertical_reframe"]
_IMAGES = ["display_ads", "image_creator", "magic_resize", "image_opt", "bg_remover",
           "landing_ads", "stock_photos", "page_images", "seo_images", "image_picker"]
_SOCIAL = ["display_ads", "social", "image_creator", "magic_resize", "image_opt",
           "bg_remover"]
_LANDING = ["land_boat", "land_hvac", "land_legal", "land_recruit", "land_restaurant",
            "land_rv", "land_ski", "land_stadium", "land_tourism"]


def _dept(slug, label, ico, groups, level=EVERYONE, blurb=""):
    """A department: `groups` is [(group label or "", [leaf keys])]. The index
    page is always /views/<slug> -- every department gets one, even the ones
    (SEO, QA) that also have a page of their own, so "click the name to see
    everything in it" is true of every row rather than most of them."""
    return {"key": "dept_" + slug.replace("-", "_"), "slug": slug, "label": label,
            "ico": ico, "href": "/views/" + slug, "level": level, "blurb": blurb,
            "groups": [(g, list(keys)) for g, keys in groups]}


SECTIONS = [
    ("Departments", [
        _dept("sales", "Sales", "&#128188;", [
            ("", ["website_audit", "salesb", "proposal_execution", "io_builder",
                  "pdf", "short_links"]),
            ("Sales Tools", ["gpt_ads", "social", "smartforecast", *_CALCULATORS, "ads",
                             "weather_setup", "sites_builder", "landing_maker"]),
            ("Leads", ["leads", "sell_to_clients", "msa"]),
            ("Sales QA", ["myclients", "monthly_promises", "sales_scorecard",
                          "partner_scorecard", "assign_clients", "sell_to_clients",
                          "stale_90", "lost_by_partner", "active_clients"]),
        ], blurb="Reading the business, quoting the work and booking it."),
        _dept("client-success", "Client Success", "&#129309;", [
            ("", ["reports_pacing", "reports", "studio", "tools"]),
            ("Ad Tools", ["utm", "ads", "gpt_ads", "smartforecast", *_CALCULATORS]),
            ("Creative", ["creative", *_AUDIO, *_VIDEOS, *_IMAGES]),
            ("Google Tools", _GOOGLE_TOOLS),
            ("", ["skills360"]),
            ("Issues", ["ad_assets", "stale_creative", "campaign_assets", "no_analytics",
                        "no_gtm", "lost_by_partner", "sell_to_clients"]),
            ("", ["suite"]),
        ], blurb="Keeping the clients we have: pacing, reporting and what each one is missing."),
        _dept("product-success", "Product Success", "&#128640;", [
            ("Ad Builders", ["ads", "lsa", "youtube_ads", "gpt_ads", "utm", "short_links"]),
            ("", ["reports", "reports_pacing", "reports_cost", "campaign_assets", "social"]),
            ("Videos", ["commercial", "commercial_lib", "video_search", "dead_air",
                        "vertical_reframe"]),
            ("Images", ["image_creator", "magic_resize", "image_opt"]),
        ], blurb="The campaigns themselves -- what ran, what it cost, what it still needs."),
        _dept("seo", "SEO", "&#128269;", [
            ("", ["seo", "webmaster_reports", "seo_intelligence", "suite"]),
            ("SEO Tools", ["scans", "reports", "google_finder", "ga4", "gtm", "webmaster_tools",
                           "gbp", "google_history", "ad_assets", "seo_images", "page_images"]),
        ], blurb="Search and AI visibility, per client and across the book."),
        _dept("web-dev", "Web Dev", "&#127760;", [
            ("", ["sites", "site_blocks", "tickets", "website_audit", "smartforecast",
                  "seo_images", "sites_builder", "clients", "house_urls"]),
            ("Google Tools", ["google", "inactive_ga", "google_accounts", "no_analytics",
                              "no_gtm", "match_google"]),
            ("Web QA", ["weather_setup", "sites", "domains", "match_sites"]),
            ("Suite QA", ["match_suite", "suite"]),
        ], blurb="Building and running client websites."),
        _dept("accounting", "Accounting", "&#128181;", [
            ("", ["acct_requests", "check_recon", "msa"]),
            ("Billing", ["invoice_off", "billing_cmp", "io_money", "sites_billing",
                         "io_not_in_knack"]),
            ("Web Issues", ["domains", "ghl_billing_none", "ghl_billing_month", *_MODULES]),
        ], blurb="Where invoicing and the client record disagree."),
    ]),
    ("Tools", [
        _dept("creative", "Creative", "&#127912;", [
            ("Audio", _AUDIO),
            ("Videos", _VIDEOS),
            ("Display / Images", _IMAGES),
            ("Social", _SOCIAL),
        ], blurb="Images, video and audio. Approved work lands on the client's 360 record."),
        _dept("studio", "Studio", "&#127916;", [
            ("", ["cs_dashboard", "cs_projects", "cs_campaigns", "cs_templates", "cs_library",
                  "cs_ai_tools", "cs_brand_kits", "cs_media", "cs_approvals", "cs_usage"]),
        ], blurb="What am I making for this client, and where did I leave it."),
        _dept("ad-tools", "Ad Tools", "&#128227;", [
            ("", ["utm", "ads", "gpt_ads", "short_links", "smartforecast"]),
            ("Calculators", _CALCULATORS),
        ], blurb="Building, tagging and sizing the buys."),
        _dept("leads", "Leads", "&#128229;", [
            ("", ["leads", "leads_existing", "industry_factory", "industry_prospects"]),
            ("Landing Pages", ["landing", "scan_widgets", "ads_grader", "media_calcs",
                               "marketing_audit"]),
            ("Industry Pages", _LANDING),
        ], blurb="Public lead-capture pages, and the panel every submission lands in."),
        _dept("qa", "QA", "&#9989;", [
            ("", ["qa", "qatasks"]),
            ("Client QA", ["myclients", "assign_clients", "unattached_images", "scan_all",
                           "no_analytics", "no_gtm", "io_not_in_knack", "no_dashboards",
                           "stale_90", "lost_by_partner"]),
            ("Web QA", ["inactive_ga", "domains", "match_sites", "match_google", "match_suite",
                        "knack_field_map", "no_analytics", "no_gtm", *_MODULES]),
        ], blurb="What is wrong, and who has been asked to go and look."),
        _dept("utilities", "Utilities", "&#128295;", [
            ("", ["diagnostics", "status", "users", "deptviews_manage", "activity"]),
            ("Modules", _MODULES),
        ], level=ADMIN_ONLY, blurb="The machinery. Admin only."),
    ]),
]


def departments(is_admin: bool = True) -> list[dict]:
    """Every department row, in nav order, Utilities dropped for General."""
    out = []
    for _title, depts in SECTIONS:
        out.extend(d for d in depts if is_admin or d["level"] != ADMIN_ONLY)
    return out


def department(slug: str) -> dict | None:
    slug = (slug or "").strip().lower()
    for d in departments(True):
        if d["slug"] == slug:
            return d
    return None


def _group_anchor(label: str) -> str:
    """A stable, URL-safe id for a flyout group's own section of the
    department's index page. Read by both `_department_html()` below and
    `department_view.html` (via `hub/department_views.py`), so a group
    heading that links out and the page it lands on cannot disagree about
    the anchor -- one function rather than two spellings of the same slug."""
    return re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")


def department_tiles(dept: dict) -> list[tuple[str, list[tuple]]]:
    """[(group label, [(key, href, ico, label), ...])] -- what the index page
    and the flyout both draw. A key named in a department but missing from
    LEAVES is dropped here rather than raising: the nav must never break a
    page, and test_menu_layout.py is where a typo is caught."""
    out = []
    for group, keys in dept["groups"]:
        rows = [(k, *LEAVES[k]) for k in keys if k in LEAVES]
        if rows:
            out.append((group, rows))
    return out


def _flatten() -> list[tuple]:
    """The nav as the flat 5-tuple list the rest of the Hub reads
    (`hub/search_index.py`, `hub/qa_tasks.py`, four test files, and the
    Proposal Execution row that inserts itself after `salesb` at boot):
    pinned rows, then each section header, its department rows, and every
    leaf under them once. A leaf listed under three departments appears once,
    at its first mention, so `_ITEMS` stays a set of pages rather than a
    transcript of the menu."""
    rows: list[tuple] = [(*row, EVERYONE) for row in PINNED]
    seen = {row[0] for row in PINNED}
    for n, (title, depts) in enumerate(SECTIONS):
        rows.append((f"_sec{n}", "", "", title, EVERYONE))
        for d in depts:
            rows.append((d["key"], d["href"], d["ico"], d["label"], d["level"]))
            for _group, leaves in department_tiles(d):
                for key, href, ico, label in leaves:
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append((key, href, ico, label, d["level"]))
    return rows


_ITEMS = _flatten()

# Departments a flyout or index page must NOT draw twice: a leaf listed in
# two groups of the same department (Sales QA and Leads both name
# sell_to_clients) is drawn in both, because each group is its own list and
# Todd wrote both. Across departments it is intentional. Nothing to dedupe.


_CSS = """
<style>
@media (min-width: 950px) {
  /* Offset the page for the fixed sidebar — but only when the host page
     isn't already doing it. hub.css lays the Hub's own pages out with
     .main{margin-left:224px}, so applying it to <body> as well pushed the
     content 448px right.

     The guard is `.shell > .main`, not `.main`. "main" is one of the most
     ordinary class names there is, and matching it anywhere in the document
     meant any *module* that happened to use it got no offset at all: the
     client lookup at /clients names its content wrapper `.main`, so the whole
     React app was laid out from x=0 and its first column of tiles sat behind
     the sidebar — on every visit, with nothing erroring. Only the Hub's own
     base.html puts a `.main` directly inside `.shell`, which is precisely the
     layout this needs to keep its hands off. */
  body:not(:has(.shell > .main)) { margin-left: 224px; }
  /* Published so anything that has to sit clear of the rail can measure it
     rather than guessing: full-height tools (Image Creator) size themselves
     against it, and .s1-demo-fab is positioned from it.

     Set on EVERY body rather than only the ones that get margin-left above.
     The two are different questions -- the Hub's own pages offset their own
     .main and must not be offset again, and a *fixed* element on those pages
     still has to clear the same 224px. Tying the variable to the margin left
     it unset exactly there, so the walkthrough launcher sat behind the rail
     on every hub page: at 224px it was completely covered and could not be
     pressed at all, and at 56px its left edge was a nav link, so pressing
     the visible ▶ navigated to /status instead of starting the walkthrough.
     Nothing reported it -- the button renders, every check is green, and it
     is the entry point to all 28 scenarios. */
  body { --s1hub-offset: 224px; }
  body.s1hub-collapsed { --s1hub-offset: 56px; }
  .s1hub-chip { display: none !important; }
}
/* Below 950px the sidebar becomes a slide-out drawer rather than vanishing.
   It used to be display:none with a chip that only linked to the dashboard,
   which meant a phone had no navigation at all -- every tool was unreachable
   unless you typed its URL. */
@media (max-width: 949.98px) {
  .s1hub-sb { transform: translateX(-100%); transition: transform .22s ease;
              width: min(280px, 84vw) !important; box-shadow: 0 0 40px rgba(6,18,32,.45); }
  .s1hub-sb.s1hub-open { transform: translateX(0); }
  .s1hub-burger { display: flex !important; }
  .s1hub-scrim { position: fixed; inset: 0; z-index: 99988; background: rgba(9,22,38,.5);
                 opacity: 0; pointer-events: none; transition: opacity .2s; }
  .s1hub-scrim.s1hub-open { opacity: 1; pointer-events: auto; }
  .s1hub-sb a.s1hub-item { padding: 12px 18px; }   /* bigger tap targets */
}
@media (prefers-reduced-motion: reduce) {
  /* body.s1hub-collapsed .s1hub-sb outranks a bare .s1hub-sb, so the hover
     peek's width transition needs its own entry here or it animates for
     exactly the readers who asked it not to. */
  .s1hub-sb, body.s1hub-collapsed .s1hub-sb { transition: none }
  .s1hub-scrim { transition: none }
}
/* Collapsed state: the nav folds to a 56px icon rail rather than vanishing.
   Hiding it entirely is what the old mobile behavior did, and it left people
   with no way back — a hide control has to be reversible from the hidden
   state, so the toggle stays visible either way. */
body.s1hub-collapsed .s1hub-sb { width: 56px !important; }
body.s1hub-collapsed .s1hub-sb .s1hub-label,
body.s1hub-collapsed .s1hub-sb .s1hub-sec,
body.s1hub-collapsed .s1hub-sb .s1hub-foot,
body.s1hub-collapsed .s1hub-sb .s1hub-logo span { display: none !important; }
body.s1hub-collapsed .s1hub-sb a.s1hub-item { justify-content: center; padding: 11px 0; }
body.s1hub-collapsed .s1hub-sb .s1hub-ico { margin: 0 }
body.s1hub-collapsed:not(:has(.shell > .main)) { margin-left: 56px; }
body.s1hub-collapsed .shell > .main { margin-left: 56px !important; }
/* Hover peek: a collapsed rail expands while the cursor (or keyboard focus)
   is over it and folds back the moment it leaves. It is a *peek*, not a
   choice — nothing is written to localStorage, so it cannot become the
   stored-preference bug coll(persist) exists to prevent — and the page
   keeps its 56px offset, so the expanded rail overlays the work rather
   than reflowing it (the sidebar already sits at z-index 99990).
   Desktop pointers only: on touch, :hover sticks after a tap, and below
   950px the slide-out drawer is the navigation anyway.
   `.s1hub-nopeek` suppresses the peek for one exit: pressing "Hide menu"
   leaves the cursor over the rail, and without it the peek re-expands the
   nav in the same instant, which reads as the button having done nothing. */
@media (min-width: 950px) and (hover: hover) {
  body.s1hub-collapsed .s1hub-sb { transition: width .15s ease; overflow-x: hidden; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within { width: 224px !important; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover .s1hub-label,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within .s1hub-label,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover .s1hub-logo span,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within .s1hub-logo span { display: inline !important; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover .s1hub-sec,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within .s1hub-sec { display: block !important; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover a.s1hub-item,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within a.s1hub-item {
    justify-content: flex-start; padding: 9px 18px; white-space: nowrap; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover .s1hub-toggle,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within .s1hub-toggle { right: 8px; }
}
.s1hub-toggle { position: absolute; top: 10px; right: 8px; z-index: 2;
  width: 24px; height: 24px; border: 0; border-radius: 6px; cursor: pointer;
  background: rgba(255,255,255,.08); color: #c9d4ea; font-size: 13px;
  line-height: 1; padding: 0; }
.s1hub-toggle:hover { background: rgba(255,255,255,.18); }
body.s1hub-collapsed .s1hub-toggle { right: 4px; }
@media (max-width: 949.98px) { .s1hub-toggle { display: none } }

.s1hub-burger { display: none; position: fixed; top: 12px; left: 12px; z-index: 99991;
  width: 42px; height: 42px; align-items: center; justify-content: center;
  border: 0; border-radius: 10px; background: #1a2e58; color: #fff;
  font-size: 19px; line-height: 1; cursor: pointer;
  box-shadow: 0 3px 12px rgba(6,18,32,.3); }
/* This markup is injected into 13 modules whose CSS we do not control, so it
   has to assert its own layout rather than inherit whatever the host page
   happens to set. sites_admin ships `header>div,nav{display:flex;align-items:
   center}` -- a bare element selector -- which turned this sidebar into a
   horizontal, vertically-centered row with every item overflowing off-screen.
   Hence the explicit display/flex resets and the !important on the few
   properties a host stylesheet can plausibly clobber. */
.s1hub-sb { position: fixed !important; top: 0 !important; bottom: 0 !important;
  left: 0 !important; width: 224px !important; height: auto !important;
  z-index: 99990;
  display: block !important; flex-direction: initial !important;
  align-items: stretch !important; justify-content: flex-start !important;
  gap: 0 !important; margin: 0 !important; padding: 0 !important;
  background: #1a2e58 !important; color: #c9d4ea; overflow-y: auto;
  font: 13.5px 'Segoe UI', system-ui, sans-serif; text-align: left; }
.s1hub-sb * { box-sizing: border-box; }
.s1hub-sb .s1hub-logo { display: flex !important; align-items: center; gap: 10px;
  padding: 18px 18px 14px; width: auto !important;
  border-bottom: 1px solid rgba(255,255,255,.08); }
.s1hub-sb .s1hub-mark { width: 34px; height: 34px; border-radius: 10px; background: rgba(255,255,255,.12);
  color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 14px; }
.s1hub-sb .s1hub-name { font-weight: 700; font-size: 16px; color: #fff; }
.s1hub-sb .s1hub-sec { display: block !important; padding: 14px 18px 4px;
  font-size: 10.5px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 1px; color: #7d8db2; }
.s1hub-sb a.s1hub-item { display: flex !important; align-items: center; gap: 10px;
  padding: 9px 18px; width: auto !important; float: none !important;
  color: #c9d4ea; text-decoration: none; border-left: 3px solid transparent;
  font: inherit; text-transform: none; letter-spacing: normal; }
.s1hub-sb a.s1hub-item:hover { background: rgba(255,255,255,.06); color: #fff; }
.s1hub-sb a.s1hub-item.s1hub-on { background: rgba(255,255,255,.1); color: #fff;
  border-left-color: #5b8bff; font-weight: 600; }
.s1hub-sb .s1hub-ico { width: 18px; text-align: center; font-size: 15px; }
.s1hub-chip { position: fixed; bottom: 14px; left: 14px; z-index: 99999; background: #1a2e58;
  color: #fff; padding: 8px 14px; border-radius: 20px; font: 600 12.5px 'Segoe UI', system-ui, sans-serif;
  text-decoration: none; box-shadow: 0 6px 18px rgba(0,0,0,.3); }

/* ---- departments: one row each, folded until rolled over or pinned. ---- */
.s1hub-sb .s1hub-dept { position: relative; }
.s1hub-sb .s1hub-dept-row { display: flex !important; align-items: center;
  border-left: 3px solid transparent; color: #c9d4ea; }
.s1hub-sb .s1hub-dept-row:hover { background: rgba(255,255,255,.06); color: #fff; }
.s1hub-sb .s1hub-dept-row.s1hub-on { background: rgba(255,255,255,.1); color: #fff;
  border-left-color: #5b8bff; font-weight: 600; }
.s1hub-sb a.s1hub-dept-link { display: flex !important; flex: 1; align-items: center;
  gap: 10px; min-width: 0; padding: 9px 0 9px 18px; color: inherit;
  text-decoration: none; font: inherit; text-transform: none; letter-spacing: normal; }
/* !important throughout: this button lands inside 20 modules whose own
   stylesheets style a bare <button> (hub.css paints them as blue pills). */
.s1hub-sb .s1hub-chev { flex: none !important; width: 26px !important; height: 26px !important;
  min-width: 0 !important; margin: 0 8px 0 0 !important; padding: 0 !important;
  border: 0 !important; border-radius: 6px !important; box-shadow: none !important;
  background: transparent !important; color: #7d8db2 !important;
  font: 12px/1 'Segoe UI', system-ui, sans-serif !important; cursor: pointer;
  transition: transform .15s ease; }
.s1hub-sb .s1hub-chev:hover, .s1hub-sb .s1hub-chev:focus-visible {
  background: rgba(255,255,255,.14) !important; color: #fff !important; }
.s1hub-sb .s1hub-dept.s1hub-pinned .s1hub-chev { transform: rotate(90deg); }
/* Pinned open: the tree stacked under the row, indented one step. */
.s1hub-sb .s1hub-inline { display: none; padding: 2px 0 8px; }
.s1hub-sb .s1hub-dept.s1hub-pinned .s1hub-inline { display: block; }
.s1hub-sb .s1hub-inline .s1hub-g { padding: 8px 18px 2px 46px; font-size: 10px;
  font-weight: 700; letter-spacing: .9px; text-transform: uppercase; color: #7d8db2; }
/* A group heading is a link now (to that group's own spot on the department
   page), so it needs to read as one without losing the small-caps label
   styling every other rule above already gives `.s1hub-g` by class alone. */
.s1hub-sb a.s1hub-g { display: block; text-decoration: none; cursor: pointer; }
.s1hub-sb a.s1hub-g:hover { color: #fff; text-decoration: underline; }
.s1hub-sb .s1hub-inline a.s1hub-leaf { display: flex !important; align-items: center;
  gap: 8px; padding: 5px 18px 5px 46px; font-size: 12.5px; color: #c9d4ea;
  text-decoration: none; }
.s1hub-sb .s1hub-inline a.s1hub-leaf .s1hub-ico { width: 14px; font-size: 12px; }
.s1hub-sb .s1hub-inline a.s1hub-leaf:hover { background: rgba(255,255,255,.06); color: #fff; }
.s1hub-sb .s1hub-inline a.s1hub-leaf.s1hub-on { color: #fff; font-weight: 600; }
/* The flyout: fixed, placed by script beside the row it belongs to. */
.s1hub-sb .s1hub-fly { position: fixed; z-index: 99995; display: none;
  background: #22396b; color: #c9d4ea; border: 1px solid rgba(255,255,255,.12);
  border-radius: 10px; box-shadow: 0 18px 50px rgba(6,18,32,.45);
  padding: 12px 6px 10px; min-width: 240px; max-width: calc(100vw - 250px);
  max-height: calc(100vh - 16px); overflow: auto; }
.s1hub-sb .s1hub-dept.s1hub-peek .s1hub-fly { display: block; }
.s1hub-sb .s1hub-fly-head { display: flex; justify-content: space-between;
  align-items: baseline; gap: 16px; padding: 0 12px 10px; margin-bottom: 6px;
  border-bottom: 1px solid rgba(255,255,255,.12); }
.s1hub-sb .s1hub-fly-head b { color: #fff; font-size: 14px; }
.s1hub-sb .s1hub-fly-head a { font-size: 11.5px; color: #5b8bff; text-decoration: none; }
.s1hub-sb .s1hub-cols { display: flex; flex-wrap: wrap; gap: 4px; align-items: flex-start; }
.s1hub-sb .s1hub-col { min-width: 200px; max-width: 250px; padding: 0 6px; }
.s1hub-sb .s1hub-col .s1hub-g { padding: 8px 8px 3px; font-size: 10px; font-weight: 700;
  letter-spacing: .9px; text-transform: uppercase; color: #7d8db2; }
.s1hub-sb .s1hub-col a.s1hub-leaf { display: flex !important; align-items: center;
  gap: 8px; padding: 4px 8px; border-radius: 5px; font-size: 12.5px; color: #c9d4ea;
  text-decoration: none; white-space: nowrap; }
.s1hub-sb .s1hub-col a.s1hub-leaf .s1hub-ico { width: 14px; font-size: 12px; }
.s1hub-sb .s1hub-col a.s1hub-leaf:hover { background: rgba(255,255,255,.1); color: #fff; }
.s1hub-sb .s1hub-col a.s1hub-leaf.s1hub-on { color: #fff; font-weight: 600; }
/* Collapsed rail: the chevron and labels fold away; the flyout still works
   because it is placed from the rail's right edge, whatever its width. */
body.s1hub-collapsed .s1hub-sb .s1hub-chev,
body.s1hub-collapsed .s1hub-sb .s1hub-inline { display: none !important; }
body.s1hub-collapsed .s1hub-sb a.s1hub-dept-link { justify-content: center; padding: 11px 0; }
@media (min-width: 950px) and (hover: hover) {
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover .s1hub-chev,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within .s1hub-chev { display: block !important; }
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:hover a.s1hub-dept-link,
  body.s1hub-collapsed:not(.s1hub-nopeek) .s1hub-sb:focus-within a.s1hub-dept-link {
    justify-content: flex-start; padding: 9px 0 9px 18px; white-space: nowrap; }
}
/* Phones: no hover, so no flyout; the chevron and the inline list are the
   whole control, with bigger tap targets. */
@media (max-width: 949.98px) {
  .s1hub-sb .s1hub-fly { display: none !important; }
  .s1hub-sb a.s1hub-dept-link { padding: 12px 0 12px 18px; }
  .s1hub-sb .s1hub-chev { width: 40px; height: 40px; margin-right: 4px; }
}
@media (prefers-reduced-motion: reduce) { .s1hub-sb .s1hub-chev { transition: none; } }
</style>
"""


FEEDBACK_FORM_URL = "https://api.leadconnectorhq.com/widget/form/XOszuVj3bHvyOasIeGhw"

FOOTER_HTML = """
<style>
/* Collapsed to a "?" so it stops competing with the page, and widens to
   its full label on hover or keyboard focus. The label stays in the markup
   rather than appearing on hover, so screen readers and search-in-page still
   find it and the width animates from real text. */
.s1hub-feed{position:fixed;bottom:10px;right:14px;z-index:99991;
  display:inline-flex;align-items:center;
  font:600 12px 'Segoe UI',system-ui,sans-serif;color:#64748b;background:rgba(255,255,255,.92);
  border:1px solid #e2e8f0;border-radius:20px;padding:5px;cursor:pointer;
  box-shadow:0 4px 14px rgba(15,23,42,.10);text-decoration:none;
  transition:padding .18s ease,color .18s ease,border-color .18s ease}
.s1hub-feed-q{flex:none;width:22px;height:22px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:14px;font-weight:700;line-height:1}
.s1hub-feed-t{max-width:0;overflow:hidden;white-space:nowrap;
  transition:max-width .22s ease,padding .22s ease}
.s1hub-feed:hover,.s1hub-feed:focus-visible{color:#1a2e58;border-color:#cbd5e1;
  padding:5px 13px 5px 5px}
.s1hub-feed:hover .s1hub-feed-t,.s1hub-feed:focus-visible .s1hub-feed-t{
  max-width:16rem;padding-left:3px}
/* Touch has no hover: a tap opens the form, which is the point of the button,
   so the collapsed "?" is the whole control there. */
@media (prefers-reduced-motion:reduce){
  .s1hub-feed,.s1hub-feed-t{transition:none}
}
</style>
<a class="s1hub-feed" onclick="s1hubFeedback();return false" href="#"
   aria-label="Issues, suggestions, problems?" title="Issues, suggestions, problems?"
   ><span class="s1hub-feed-q" aria-hidden="true">?</span><span class="s1hub-feed-t">Issues, Suggestions, Problems?</span></a>
<script>
function s1hubFeedback(){
  var m=document.getElementById('s1hubFeedModal');
  if(m){m.remove();return;}
  m=document.createElement('div');
  m.id='s1hubFeedModal';
  m.style.cssText='position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:2147483000;display:flex;align-items:center;justify-content:center;padding:16px';
  m.innerHTML='<div style="background:#fff;border-radius:14px;width:640px;max-width:100%;height:86vh;display:flex;flex-direction:column;overflow:hidden">'
    +'<div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid #e2e8f0">'
    +'<b style="color:#1a2e58;font:600 14px \\'Segoe UI\\',system-ui,sans-serif">Issues, Suggestions, Problems?</b>'
    +'<button onclick="document.getElementById(\\'s1hubFeedModal\\').remove()" style="border:0;background:none;font-size:22px;cursor:pointer;color:#64748b">&times;</button></div>'
    +'<iframe src="__FORM_URL__" style="flex:1;border:0;width:100%"></iframe></div>';
  m.onclick=function(e){if(e.target===m)m.remove();};
  document.body.appendChild(m);
}
</script>
""".replace("__FORM_URL__", FEEDBACK_FORM_URL)



def render_footer() -> bytes:
    return FOOTER_HTML.encode()


def visible_items(is_admin: bool = True) -> list[tuple]:
    """The flat nav rows this person should see, section headers included.

    A section whose every entry was filtered out has its header dropped too —
    a bare "Utilities" heading with nothing under it reads as a nav that
    failed to load rather than as a section that isn't yours.
    """
    rows = [r for r in _ITEMS if is_admin or r[4] != ADMIN_ONLY]
    out = []
    for i, row in enumerate(rows):
        if row[0].startswith("_sec"):
            follows = rows[i + 1:]
            nxt = next((r for r in follows if not r[0].startswith("_sec")), None)
            has_entries = nxt is not None and not any(
                r[0].startswith("_sec") for r in follows[:follows.index(nxt)])
            if not has_entries:
                continue
        out.append(row)
    return out


def _active_department(active: str, is_admin: bool) -> str:
    """Which department row lights up for an active key: its own key, or the
    first department that lists the leaf. A leaf under three departments
    lights the first, which is the one nearest the top of the nav."""
    if not active:
        return ""
    for d in departments(is_admin):
        if d["key"] == active:
            return d["key"]
    for d in departments(is_admin):
        for _g, leaves in department_tiles(d):
            if any(k == active for k, *_ in leaves):
                return d["key"]
    return ""


def _leaf_html(key, href, ico, label, active, cls=""):
    on = " s1hub-on" if key == active else ""
    return (f'<a class="s1hub-leaf{on}{(" " + cls) if cls else ""}" href="{href}" title="{label}">'
            f'<span class="s1hub-ico">{ico}</span><span class="s1hub-label"> {label}</span></a>')


def _department_html(d: dict, active: str, lit: str) -> str:
    tiles = department_tiles(d)
    count = len({href for _g, leaves in tiles for _k, href, *_ in leaves})
    on = " s1hub-on" if d["key"] == lit else ""
    admin = ' data-s1hub-admin="1"' if d["level"] == ADMIN_ONLY else ""
    # The flyout: one column per group, up to four across. A named group's
    # heading is a link to that same group on the department's index page --
    # the mega-menu still lists every tool under it on hover, and clicking
    # the heading is "just take me to the page" for whoever would rather not
    # hover at all.
    cols = []
    for group, leaves in tiles:
        head = (f'<a class="s1hub-g" href="{d["href"]}#{_group_anchor(group)}">'
                f'{group} &rarr;</a>') if group else ""
        cols.append('<div class="s1hub-col">' + head
                    + "".join(_leaf_html(*leaf, active) for leaf in leaves)
                    + "</div>")
    fly = (f'<div class="s1hub-fly" role="group" aria-label="{d["label"]}">'
           f'<div class="s1hub-fly-head"><b>{d["label"]}</b>'
           f'<a href="{d["href"]}">{count} tools &middot; open &rarr;</a></div>'
           f'<div class="s1hub-cols">{"".join(cols)}</div></div>')
    # The inline list: the same tree, stacked, for a pinned-open row and for
    # phones (no hover there, so the chevron is the whole control).
    inline = []
    for group, leaves in tiles:
        if group:
            inline.append(f'<a class="s1hub-g" href="{d["href"]}#{_group_anchor(group)}">'
                          f'{group} &rarr;</a>')
        inline.extend(_leaf_html(*leaf, active) for leaf in leaves)
    # The row is a div holding a link and a button side by side: a button
    # inside an anchor is not HTML a browser has to honour, and a chevron
    # that also navigated would make "pin open" and "open the page" the
    # same click.
    return (f'<div class="s1hub-dept" data-s1hub-dept="{d["slug"]}"{admin}>'
            f'<div class="s1hub-dept-row{on}">'
            f'<a class="s1hub-dept-link" href="{d["href"]}" title="{d["label"]}">'
            f'<span class="s1hub-ico">{d["ico"]}</span>'
            f'<span class="s1hub-label"> {d["label"]}</span></a>'
            f'<button class="s1hub-chev" type="button" aria-expanded="false" '
            f'aria-label="Pin {d["label"]} open" title="Pin open">&#9656;</button></div>'
            f'<div class="s1hub-inline">{"".join(inline)}</div>'
            + fly + "</div>")


def render_sidebar(active: str = "", is_admin: bool = True,
                   collapsed_default: bool = False) -> bytes:
    """The nav. ``is_admin=False`` drops the Utilities department.

    Defaults to True because every existing caller renders for a signed-in
    session and passing the flag is the new part; a caller that cannot work
    out the role gets the full nav and the server-side gate still refuses the
    click. The reverse default would hide Diagnostics from an admin whenever
    the role lookup hiccuped, which is a bug nobody would report as one.

    ``collapsed_default`` starts the nav as an icon rail on tools that are
    themselves a full-width workbench -- every creative tool, which is what
    `CREATIVE_PREFIXES` above lists and `collapses_by_default()` decides. It
    is a *default*, not a lock: a stored preference always wins, so somebody
    who opens the menu there keeps it open, and the toggle still works
    either way. Without that distinction it would be a page fighting the
    person using it.
    """
    lit = _active_department(active, is_admin)
    rows = []
    rows.append('<div class="s1hub-logo"><div class="s1hub-mark">S1</div><span class="s1hub-name">Smart 1 Hub</span></div>')
    rows.append('<div class="s1hub-sec">Overview</div>')
    for key, href, ico, label in PINNED:
        on = " s1hub-on" if key == active else ""
        rows.append(f'<a class="s1hub-item{on}" href="{href}" title="{label}">'
                    f'<span class="s1hub-ico">{ico}</span>'
                    f'<span class="s1hub-label"> {label}</span></a>')
    for title, depts in SECTIONS:
        shown = [d for d in depts if is_admin or d["level"] != ADMIN_ONLY]
        if not shown:
            continue
        rows.append(f'<div class="s1hub-sec">{title}</div>')
        rows.extend(_department_html(d, active, lit) for d in shown)
    # The burger replaces the old chip, which only linked to the dashboard.
    # Inline vanilla JS with no dependencies, because this markup is injected
    # into 20 modules whose own scripts we do not control.
    _JS = (
        "<script>(function(){"
        "var b=document.querySelector('.s1hub-burger'),"
        "n=document.querySelector('.s1hub-sb'),"
        "s=document.querySelector('.s1hub-scrim');"
        "if(!b||!n||!s)return;"
        "function set(o){n.classList.toggle('s1hub-open',o);"
        "s.classList.toggle('s1hub-open',o);"
        "b.setAttribute('aria-expanded',o?'true':'false');}"
        "b.addEventListener('click',function(){"
        "set(!n.classList.contains('s1hub-open'));});"
        "s.addEventListener('click',function(){set(false);});"
        "document.addEventListener('keydown',function(e){"
        "if(e.key==='Escape'){set(false);"
        "n.querySelectorAll('.s1hub-dept.s1hub-peek').forEach(function(d){d.classList.remove('s1hub-peek');});}});"
        # Follow a link and the drawer closes itself, otherwise it covers
        # the page you just navigated to.
        "n.addEventListener('click',function(e){"
        "if(e.target.closest('a')&&!e.target.closest('.s1hub-chev'))set(false);});"
        # ---- departments. Roll over a row: the flyout. Click the chevron:
        # pinned open inline, remembered per person in localStorage under
        # s1hub:open. Click the name: the index page, an ordinary link.
        # The flyout is position:fixed and placed from the row's own
        # rectangle, because the nav scrolls (overflow-y:auto), and an
        # absolutely positioned child of a scroll container is clipped at its
        # edge -- the flyout would have been cut off at 224px and nothing
        # would have said why.
        "var open={};try{open=JSON.parse(localStorage.getItem('s1hub:open')||'{}')||{};}catch(e){}"
        "n.querySelectorAll('.s1hub-dept').forEach(function(d){"
        "var slug=d.getAttribute('data-s1hub-dept'),row=d.querySelector('.s1hub-dept-row'),"
        "chev=d.querySelector('.s1hub-chev'),fly=d.querySelector('.s1hub-fly');"
        "function pin(on){d.classList.toggle('s1hub-pinned',on);"
        "if(chev)chev.setAttribute('aria-expanded',on?'true':'false');}"
        "if(open[slug])pin(true);"
        "if(chev)chev.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();"
        "var on=!d.classList.contains('s1hub-pinned');pin(on);open[slug]=on;"
        "try{localStorage.setItem('s1hub:open',JSON.stringify(open));}catch(x){}});"
        "function place(){if(!fly||window.innerWidth<950)return;"
        "var r=row.getBoundingClientRect(),nr=n.getBoundingClientRect();"
        "fly.style.left=Math.round(nr.right)+'px';fly.style.top=Math.round(r.top-6)+'px';"
        "d.classList.add('s1hub-peek');"
        "var fr=fly.getBoundingClientRect(),over=fr.bottom-(window.innerHeight-8);"
        "if(over>0)fly.style.top=Math.max(8,Math.round(r.top-6-over))+'px';}"
        "d.addEventListener('mouseenter',place);"
        "d.addEventListener('mouseleave',function(){d.classList.remove('s1hub-peek');});"
        "d.addEventListener('focusin',place);"
        "d.addEventListener('focusout',function(e){if(!d.contains(e.relatedTarget))d.classList.remove('s1hub-peek');});"
        "});"
        "n.addEventListener('scroll',function(){n.querySelectorAll('.s1hub-dept.s1hub-peek').forEach(function(d){d.classList.remove('s1hub-peek');});});"
        # Collapse to an icon rail, remembered across pages. Applied before
        # paint where possible so the layout doesn't jump on every navigation.
        #
        # A page asks for the rail two ways, and both are honored because
        # both are in use: `collapsed_default` above, decided server-side from
        # the path (every creative tool), and `data-s1hub-collapse="1"` on
        # the body, declared by the page's own template (the Proposal
        # Builder's wizard). The wide tools lose 224px of a laptop's width to
        # a nav nobody is reading while they work, which is what turns a step
        # into a horizontal scroll. The body attribute simply feeds the same
        # flag, so there is one decision rather than two racing each other.
        "var t=document.querySelector('.s1hub-toggle');"
        "if(document.body&&document.body.getAttribute("
        "'data-s1hub-collapse')==='1')window.__s1hubCollapseDefault=true;"
        # `persist` is what keeps a page default from becoming everybody's
        # preference. `coll()` writes localStorage, and the automatic call
        # below used to write it too -- so one visit to a collapsed-by-default
        # tool stored 's1hub:collapsed=1' globally and every other screen in
        # the Hub came up collapsed, without anybody having pressed anything.
        # It also meant the page default was only ever consulted once, since
        # after that first visit there was no longer such a thing as "no
        # stored preference". Only a real press of the toggle records a
        # preference now; asking for the rail is per page and per visit.
        "function coll(on,persist){"
        "document.body.classList.toggle('s1hub-collapsed',on);"
        "if(t){t.innerHTML=on?'\\u276F':'\\u276E';"
        "t.title=on?'Show menu':'Hide menu';"
        "t.setAttribute('aria-label',t.title);}"
        "if(persist!==false){"
        "try{localStorage.setItem('s1hub:collapsed',on?'1':'0');}catch(e){}}}"
        # A stored preference wins over the page default in both directions,
        # so a tool that starts collapsed can still be opened for good.
        "try{var sv=localStorage.getItem('s1hub:collapsed');"
        "if(sv==='1'||(sv===null&&window.__s1hubCollapseDefault))coll(true,false);}"
        "catch(e){if(window.__s1hubCollapseDefault)coll(true,false);}"
        # Pressing "Hide menu" leaves the cursor sitting over the rail, and
        # the hover peek would expand it again in the same instant -- a
        # button that visibly does nothing. `s1hub-nopeek` holds the peek
        # off until the pointer (or focus) has left the nav once; it is
        # never persisted, so it costs one exit and nothing else.
        "if(t)t.addEventListener('click',function(){"
        "var on=!document.body.classList.contains('s1hub-collapsed');"
        "if(on)document.body.classList.add('s1hub-nopeek');"
        "coll(on);});"
        "n.addEventListener('mouseleave',function(){"
        "document.body.classList.remove('s1hub-nopeek');});"
        "n.addEventListener('focusout',function(){"
        "document.body.classList.remove('s1hub-nopeek');});"
        "})();</script>"
    )
    html = (
        (f"<script>window.__s1hubCollapseDefault="
         f"{'true' if collapsed_default else 'false'};</script>")
        + _CSS
        + '<button class="s1hub-burger" aria-label="Open menu" '
          'aria-expanded="false" aria-controls="s1hub-nav">&#9776;</button>'
        + '<div class="s1hub-scrim"></div>'
        + '<nav class="s1hub-sb" id="s1hub-nav">'
        + '<button class="s1hub-toggle" type="button" aria-label="Hide menu" '
          'title="Hide menu">&#10094;</button>'
        + "".join(rows) + "</nav>"
        + FOOTER_HTML
        + _JS
    )
    return html.encode()
