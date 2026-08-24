"""The Smart 1 house style, as data — themes, block types and one renderer.

## What this is for

Every industry landing page in the Hub (`/land/hvac`, `/land/boat`,
`/land/ski`, …) is a hand-written HTML file in the same visual language:
Montserrat, navy `#0a2240`, an accent pair, `.wrap` at 1140px, a hero, a
two-column pillar section, a card grid, a navy stat band, a gradient CTA. New
sections for smart1marketing.com were being built by copying one of those
files and editing it, which is how they drift apart — and why a page built
last month and one built today no longer look like the same company.

This module owns that language once. `THEMES` holds the palettes, `BLOCKS`
holds the section types and their fields, and `render_*` turns a list of
blocks into HTML a rep can paste into the website.

## Two rules the output has to obey, and one that follows from them

**Nothing may escape the block.** The industry pages style bare `section`,
`h1`, `body` and `*` — correct for a standalone document and catastrophic
pasted into an existing site, where it would restyle the whole page. So every
selector here is written under `.s1blk`, every class carries an `s1-` prefix,
and the palette lives on `.s1blk` rather than `:root`. `test_site_blocks.py`
asserts it: a bare element selector or an unscoped rule fails the build.

**The CSS must survive being pasted more than once.** A rep pastes one block
into one Custom HTML element, then another block into another. Each block is
emitted self-contained, stylesheet included, and because the stylesheet is
identical and idempotent, N copies of it behave exactly like one. That is why
the per-block output repeats the CSS rather than assuming a page-level
stylesheet the rep may never have pasted — a block that arrives unstyled looks
broken, and the person who pasted it has no way to know why.

Which gives the third: the shared-stylesheet mode exists (`render_blocks`,
`render_page`) for when the whole page is being built at once, and emits the
CSS a single time. Both modes produce the same pixels.

## Adding a block type

Add an entry to `BLOCKS` — `label`, `note`, `fields`, and `sample` — and a
branch in `_markup`. The builder UI reads `BLOCKS` over the API and draws its
own form, so a new block needs no template change. Add its CSS to `_CSS`
under a `.s1blk-<kind>` scope.
"""
from __future__ import annotations

import html
import re

FONT_IMPORT = ("@import url('https://fonts.googleapis.com/css2?"
               "family=Montserrat:wght@400;500;600;700;800;900&display=swap');")

LOGO_URL = ("https://content.app-sources.com/s/30680510049142132/uploads/"
            "Our_Products_/logo-final-cmyk-hz1line-white-9562849.png?format=webp")

SITE_URL = "https://smart1marketing.com"
CONSULT_URL = "https://smart1marketing.com/free-consultation"


# --------------------------------------------------------------- themes
# The shared half of every palette — navy, ink, muted, line, page — is
# identical across all the industry pages and is not a per-theme choice. What
# actually varies between them is the accent pair, so that is all a theme
# holds. `warm` is the third colour used in the stat-band gradient and the
# hero highlight; where a page had no third colour it repeats the accent.
THEMES = {
    "smart1": {
        "label": "Smart 1 blue",
        "note": "The house default — the palette on the Boat, Restaurant and "
                "Recruitment pages. Blue accent, orange call to action.",
        "accent": "#009ed2", "accent_d": "#0879a3",
        "action": "#ff6b3d", "action_d": "#e2542a", "warm": "#ffb04a",
        "tint": "#e7f7fb", "tint2": "#fff2ec",
    },
    "heat": {
        "label": "Comfort & Command",
        "note": "The HVAC page — heat orange against cool blue. Suits urgency, "
                "emergency demand and weather-triggered offers.",
        "accent": "#f2683c", "accent_d": "#d94e23",
        "action": "#f2683c", "action_d": "#d94e23", "warm": "#ffb04a",
        "tint": "#fff2ec", "tint2": "#eef7fb",
    },
    "ice": {
        "label": "Ski and winter",
        "note": "The Ski Resort page — a colder blue, lighter tints. Suits "
                "seasonal, travel and destination offers.",
        "accent": "#3a95e0", "accent_d": "#1f6fb5",
        "action": "#ff6b3d", "action_d": "#e2542a", "warm": "#7fc4f5",
        "tint": "#eaf6ff", "tint2": "#fff2ec",
    },
    "growth": {
        "label": "Growth green",
        "note": "Green accent from the house check-mark colour. Suits results, "
                "reporting and retention offers rather than acquisition.",
        "accent": "#2dbb72", "accent_d": "#1e8f56",
        "action": "#2dbb72", "action_d": "#1e8f56", "warm": "#7fdca8",
        "tint": "#e9f7ef", "tint2": "#e7f7fb",
    },
    "prestige": {
        "label": "Prestige gold",
        "note": "Gold against navy. Suits legal, financial and premium "
                "positioning, where the blue reads as too promotional.",
        "accent": "#c8a24a", "accent_d": "#9d7c2d",
        "action": "#c8a24a", "action_d": "#9d7c2d", "warm": "#e8cf8f",
        "tint": "#faf4e4", "tint2": "#e7f7fb",
    },
}

DEFAULT_THEME = "smart1"


def theme_vars(theme: str) -> str:
    """The palette, as custom properties on the block wrapper.

    On `.s1blk` and not `:root` — a `:root` block pasted into a page would
    rewrite variables the host site is using under the same names.
    """
    t = THEMES.get(theme) or THEMES[DEFAULT_THEME]
    return (
        ".s1blk{"
        "--s1-navy:#0a2240;--s1-navy2:#123863;--s1-deep:#071a30;"
        "--s1-ink:#25364b;--s1-muted:#68798c;--s1-line:#dbe5ed;"
        "--s1-page:#f3f7fa;--s1-white:#fff;--s1-green:#2dbb72;"
        f"--s1-accent:{t['accent']};--s1-accent-d:{t['accent_d']};"
        f"--s1-action:{t['action']};--s1-action-d:{t['action_d']};"
        f"--s1-warm:{t['warm']};--s1-tint:{t['tint']};--s1-tint2:{t['tint2']};"
        "--s1-shadow:0 20px 60px rgba(10,34,64,.11);"
        "--s1-shadow-sm:0 8px 26px rgba(10,34,64,.08)}"
    )


# --------------------------------------------------------------- block types
#   text   — one line
#   long   — a paragraph
#   url    — a link target
#   list   — repeating rows; `item` names the fields of one row
#   bool   — a switch
BLOCKS = {
    "hero": {
        "label": "Hero",
        "note": "The opening band. Navy gradient, oversized headline with one "
                "highlighted phrase, two buttons and a row of proof pills.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Headline"},
            {"k": "accent", "t": "text", "label": "Highlighted phrase",
             "help": "A phrase inside the headline to print in the accent "
                     "gradient. Leave blank for none. It must appear in the "
                     "headline exactly as typed."},
            {"k": "lede", "t": "long", "label": "Opening paragraph"},
            {"k": "cta_label", "t": "text", "label": "Primary button"},
            {"k": "cta_href", "t": "url", "label": "Primary button link"},
            {"k": "cta2_label", "t": "text", "label": "Secondary button"},
            {"k": "cta2_href", "t": "url", "label": "Secondary button link"},
            {"k": "pills", "t": "list", "label": "Proof pills",
             "item": [{"k": "text", "t": "text", "label": "Pill"}]},
            {"k": "footnote", "t": "text", "label": "Line under the buttons"},
        ],
        "sample": {
            "eyebrow": "Omnichannel Marketing Package",
            "title": "Own every high-intent moment in your market.",
            "accent": "high-intent moment",
            "lede": "An always-on marketing system built for local service "
                    "businesses. We capture search demand the moment it "
                    "appears, surround the same households on every screen, "
                    "and text back every missed call automatically.",
            "cta_label": "Build My Instant Plan", "cta_href": "#plan",
            "cta2_label": "See How It Works", "cta2_href": "#strategy",
            "pills": [{"text": "High-intent search capture"},
                      {"text": "Connected TV"},
                      {"text": "New-mover targeting"},
                      {"text": "Missed-call text back"}],
            "footnote": "Transparent media budgets · No long-term guessing "
                        "games · Finalized with a strategist before "
                        "anything goes live",
        },
    },
    "pillars": {
        "label": "Two pillars",
        "note": "Two large tinted cards side by side. The strategy section on "
                "the HVAC page — for splitting an approach in two.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "items", "t": "list", "label": "Pillars", "max": 2,
             "item": [{"k": "tag", "t": "text", "label": "Tag"},
                      {"k": "icon", "t": "text", "label": "Icon"},
                      {"k": "title", "t": "text", "label": "Title"},
                      {"k": "body", "t": "long", "label": "Body"},
                      {"k": "note", "t": "long", "label": "Callout line"}]},
        ],
        "sample": {
            "eyebrow": "The Strategy",
            "title": "We don’t play guessing games with your ad budget.",
            "intro": "Instead of spreading spend thinly across everything, we "
                     "split it into two disciplined pipelines that together "
                     "cover the whole demand cycle.",
            "items": [
                {"tag": "⚡ Always-On", "icon": "\U0001f525",
                 "title": "The capture pipeline",
                 "body": "When someone needs you right now they do not scroll "
                         "social media for a recommendation — they search. "
                         "We put your business at the top of those results.",
                 "note": "Google Local Service Ads and high-intent Search put "
                         "your phone number above everything else, so you are "
                         "the first call."},
                {"tag": "\U0001f3af Trigger-Based", "icon": "\U0001f4c8",
                 "title": "The demand pipeline",
                 "body": "Programmatic rules turn your video and display ads on "
                         "when the conditions that create demand appear, and "
                         "pause them the moment they pass.",
                 "note": "Budget is preserved for the high-value days rather "
                         "than spread evenly across a month that is not."},
            ],
        },
    },
    "channels": {
        "label": "Channel cards",
        "note": "A three-across grid of cards, each with an icon, a paragraph "
                "and a rate chip. The channels section on every industry page.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "items", "t": "list", "label": "Cards",
             "item": [{"k": "icon", "t": "text", "label": "Icon"},
                      {"k": "title", "t": "text", "label": "Title"},
                      {"k": "body", "t": "long", "label": "Body"},
                      {"k": "chip", "t": "text", "label": "Chip",
                       "help": "The rate or qualifier on the pill at the foot "
                               "of the card. Leave blank to omit it — an "
                               "empty chip is better than an invented rate."}]},
        ],
        "sample": {
            "eyebrow": "Multi-Channel Tactics",
            "title": "We surround local buyers on every screen.",
            "intro": "Premier technology partners and transparent wholesale "
                     "rate-card media, working together across search, TV and "
                     "display.",
            "items": [
                {"icon": "\U0001f50e", "title": "Google LSA & Pay-Per-Click",
                 "body": "We claim and optimize your Google Guaranteed Local "
                         "Services Ads profile, placing your phone number at "
                         "the top of search. You pay when a qualified local "
                         "customer calls, not for empty clicks.",
                 "chip": "Pay per call"},
                {"icon": "\U0001f4fa", "title": "Connected TV",
                 "body": "Non-skippable 15- and 30-second commercials served "
                         "to living-room smart TVs in your service area, "
                         "framing your team as the local answer.",
                 "chip": "$35.00 CPM"},
                {"icon": "\U0001f3e1", "title": "New-mover display",
                 "body": "Households that moved in the last six months are "
                         "materially more likely to buy home services. We "
                         "target them before your competitors do.",
                 "chip": "$5.50 CPM"},
            ],
        },
    },
    "stats": {
        "label": "Stat band",
        "note": "A navy band of large gradient figures. Short, and only for "
                "numbers you can stand behind.",
        "fields": [
            {"k": "items", "t": "list", "label": "Figures",
             "item": [{"k": "num", "t": "text", "label": "Figure"},
                      {"k": "label", "t": "text", "label": "What it means"}]},
        ],
        "sample": {
            "items": [
                {"num": "#1", "label": "Placement above traditional ads with "
                                      "Google Guaranteed LSA"},
                {"num": "50+", "label": "Local directories synced to climb the "
                                        "Maps 3-Pack"},
                {"num": "24/7", "label": "Every missed call answered "
                                         "automatically by text"},
                {"num": "1", "label": "Strategist who owns your account "
                                      "end to end"},
            ],
        },
    },
    "suite": {
        "label": "Smart 1 Suite",
        "note": "Three softly tinted cards on white. The automation section — "
                "what the Suite does once the advertising has worked.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "items", "t": "list", "label": "Cards",
             "item": [{"k": "icon", "t": "text", "label": "Icon"},
                      {"k": "title", "t": "text", "label": "Title"},
                      {"k": "body", "t": "long", "label": "Body"}]},
        ],
        "sample": {
            "eyebrow": "Instant-Intake Automation",
            "title": "The Smart 1 Suite captures the lead the moment it happens.",
            "intro": "Advertising drives the demand. Automation makes sure not "
                     "a single lead slips through while your team is on "
                     "another call.",
            "items": [
                {"icon": "\U0001f4f2", "title": "Missed-Call Text Back",
                 "body": "An instant, friendly text goes out the second a call "
                         "goes unanswered, so the lead is held rather than lost "
                         "to whoever picks up next."},
                {"icon": "\U0001f4e5", "title": "Unified Conversation Inbox",
                 "body": "Google Business messages, website chats, Facebook DMs "
                         "and text replies all pull into a single dashboard, so "
                         "your office staff work one queue."},
                {"icon": "\U0001f31f", "title": "Automated Review Generation",
                 "body": "Every completed job triggers a review invite, "
                         "compounding local trust and search rankings with no "
                         "extra work for your team."},
            ],
        },
    },
    "steps": {
        "label": "How it works",
        "note": "Numbered steps in a row. For explaining a process — "
                "onboarding, a launch sequence, what happens after the call.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "items", "t": "list", "label": "Steps",
             "item": [{"k": "title", "t": "text", "label": "Step"},
                      {"k": "body", "t": "long", "label": "Body"}]},
        ],
        "sample": {
            "eyebrow": "What Happens Next",
            "title": "From first call to live campaign in about two weeks.",
            "intro": "",
            "items": [
                {"title": "Discovery call",
                 "body": "Thirty minutes on your service area, your capacity "
                         "and what a good month looks like."},
                {"title": "Market plan",
                 "body": "We map the geography, the audiences and the channel "
                         "mix, and price it off the wholesale rate card."},
                {"title": "Build and launch",
                 "body": "Creative, tracking and automation are built, "
                         "reviewed with you, and switched on together."},
                {"title": "Monthly review",
                 "body": "One strategist, one report, and the changes we are "
                         "making next month."},
            ],
        },
    },
    "pricing": {
        "label": "Packages",
        "note": "Two or three tier cards, one of which can be featured. Each "
                "carries a price, who it suits, and a tick list.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "items", "t": "list", "label": "Tiers", "max": 3,
             "item": [{"k": "tier", "t": "text", "label": "Tier label"},
                      {"k": "name", "t": "text", "label": "Package name"},
                      {"k": "amount", "t": "text", "label": "Price"},
                      {"k": "per", "t": "text", "label": "Per"},
                      {"k": "who", "t": "long", "label": "Who it suits"},
                      {"k": "features", "t": "list", "label": "Includes",
                       "item": [{"k": "text", "t": "text", "label": "Line"}]},
                      {"k": "cta_label", "t": "text", "label": "Button"},
                      {"k": "cta_href", "t": "url", "label": "Button link"},
                      {"k": "featured", "t": "bool", "label": "Feature this one"},
                      {"k": "badge", "t": "text", "label": "Badge"}]},
            {"k": "note", "t": "long", "label": "Note under the tiers"},
        ],
        "sample": {
            "eyebrow": "Investment",
            "title": "Two ways to start.",
            "intro": "",
            "items": [
                {"tier": "Foundation", "name": "Local Presence",
                 "amount": "$1,500", "per": "/ month",
                 "who": "A single location establishing search presence and "
                        "capturing the demand already there.",
                 "features": [{"text": "Google LSA setup and management"},
                              {"text": "Local listings synced across 50+ directories"},
                              {"text": "Missed-call text back"},
                              {"text": "Monthly reporting with a strategist"}],
                 "cta_label": "Talk to a Strategist", "cta_href": CONSULT_URL,
                 "featured": False, "badge": ""},
                {"tier": "Recommended", "name": "Omnichannel",
                 "amount": "$4,000", "per": "/ month",
                 "who": "A business with capacity to grow, surrounding the "
                        "market on search, TV and display at once.",
                 "features": [{"text": "Everything in Local Presence"},
                              {"text": "Connected TV and programmatic display"},
                              {"text": "New-mover and in-market audiences"},
                              {"text": "Automated review generation"},
                              {"text": "Landing page and call tracking"}],
                 "cta_label": "Build My Instant Plan", "cta_href": "#plan",
                 "featured": True, "badge": "Most chosen"},
            ],
            "note": "Media budgets are quoted off our standard wholesale rate "
                    "card and finalized with a strategist before anything goes "
                    "live.",
        },
    },
    "quote": {
        "label": "Client quote",
        "note": "One testimonial, large, on a tinted band. One quote lands; "
                "a wall of them reads as filler.",
        "fields": [
            {"k": "quote", "t": "long", "label": "Quote"},
            {"k": "name", "t": "text", "label": "Who said it"},
            {"k": "role", "t": "text", "label": "Their role and company"},
        ],
        "sample": {
            "quote": "We stopped guessing. Every month there is a plan, a "
                     "number, and someone who can tell me why it changed.",
            "name": "", "role": "",
        },
    },
    "faq": {
        "label": "Questions",
        "note": "A plain question-and-answer list. Answers the objections a "
                "rep hears on every call, before the form.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "items", "t": "list", "label": "Questions",
             "item": [{"k": "q", "t": "text", "label": "Question"},
                      {"k": "a", "t": "long", "label": "Answer"}]},
        ],
        "sample": {
            "eyebrow": "Before You Ask",
            "title": "The questions we get on every first call.",
            "items": [
                {"q": "Am I locked into a contract?",
                 "a": "Terms are agreed up front and written on the insertion "
                      "order. Nothing renews without you signing it."},
                {"q": "Who owns the ad accounts?",
                 "a": "You do. Everything is built in accounts in your name, "
                      "and they stay with you."},
                {"q": "How much of my budget goes to media?",
                 "a": "Media spend, platform licensing and one-time production "
                      "are quoted as separate lines, so you can see exactly "
                      "what stops if you pause."},
            ],
        },
    },
    "cta": {
        "label": "Call to action",
        "note": "The closing gradient band. Two buttons and a reassurance line.",
        "fields": [
            {"k": "title", "t": "text", "label": "Heading"},
            {"k": "body", "t": "long", "label": "Body"},
            {"k": "cta_label", "t": "text", "label": "Primary button"},
            {"k": "cta_href", "t": "url", "label": "Primary button link"},
            {"k": "cta2_label", "t": "text", "label": "Secondary button"},
            {"k": "cta2_href", "t": "url", "label": "Secondary button link"},
            {"k": "footnote", "t": "text", "label": "Line under the buttons"},
        ],
        "sample": {
            "title": "Let’s build your plan.",
            "body": "Answer a few questions about your service area and goals. "
                    "We will map the geography, the audiences and the channel "
                    "mix for your market. No obligation.",
            "cta_label": "Build My Instant Plan", "cta_href": "#plan",
            "cta2_label": "Talk to a Strategist", "cta2_href": CONSULT_URL,
            "footnote": "A strategist finalizes budgets, geography and "
                        "territorial exclusions before any campaign activates.",
        },
    },
    "embed": {
        "label": "Embedded tool",
        "note": "An iframe band for one of the Hub's public landing tools — "
                "the market-plan builders that already take leads. Paste the "
                "tool's URL; the frame resizes itself if the tool reports its "
                "height.",
        "fields": [
            {"k": "eyebrow", "t": "text", "label": "Eyebrow"},
            {"k": "title", "t": "text", "label": "Section heading"},
            {"k": "intro", "t": "long", "label": "Section intro"},
            {"k": "src", "t": "url", "label": "Tool URL"},
            {"k": "height", "t": "text", "label": "Starting height in pixels"},
        ],
        "sample": {
            "eyebrow": "Build Your Plan",
            "title": "See your market in about three minutes.",
            "intro": "Answer a few questions and the tool maps your trade "
                     "area, audiences and recommended budget on the spot.",
            "src": "", "height": "760",
        },
    },
}

# The order the builder offers them in, which is also the order a page reads
# best in. Not alphabetical: a page that opens on its FAQ is a broken page.
BLOCK_ORDER = ["hero", "pillars", "channels", "stats", "suite", "steps",
               "embed", "pricing", "quote", "faq", "cta"]


# --------------------------------------------------------------- the CSS
# Every selector below is scoped to `.s1blk`. Nothing here may match an
# element outside a block: see the module docstring, and the assertion in
# test_site_blocks.py that fails the build if a bare selector appears.
_CSS = """
/* font-family on the descendants, not only the wrapper: a host rule on bare
   `h2` or `p` is more specific than inheritance, so a site with its own
   heading font printed every heading in the block in that font while the
   body copy stayed Montserrat. Inherit puts them back on the block's own
   stack without naming it twice. */
.s1blk,.s1blk *{box-sizing:border-box;font-family:inherit}
/* The last four declarations are an inbound guard, not decoration. A host
   page that styles bare `section` -- border, margin, centred text, letter
   spacing -- would otherwise reach into the block, and the block would look
   wrong on the live site while looking perfect in the builder's preview.
   Leakage the other way is handled by scoping every selector; this is the
   direction scoping cannot help with. */
.s1blk{font-family:Montserrat,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
 color:var(--s1-ink);line-height:1.6;-webkit-font-smoothing:antialiased;padding:72px 0;
 background:var(--s1-page);
 border:0;margin:0;text-align:left;letter-spacing:normal}
.s1blk h1,.s1blk h2,.s1blk h3,.s1blk h4{color:var(--s1-navy);letter-spacing:-.02em;
 line-height:1.15;margin:0}
.s1blk p{margin:0}
.s1blk img{max-width:100%;display:block}
.s1blk a{color:inherit}
.s1blk ul{list-style:none;margin:0;padding:0}
.s1-wrap{width:min(1140px,calc(100% - 40px));margin:0 auto}
/* A div, not a p: `.s1-head p` below would outrank this and print the
   eyebrow in muted grey. The industry pages use a div for the same reason. */
.s1-eyebrow{font-weight:800;letter-spacing:.15em;text-transform:uppercase;
 font-size:.72rem;color:var(--s1-accent);margin:0}
.s1-head{max-width:720px;margin:0 auto 46px;text-align:center}
.s1-head h2{font-size:clamp(1.8rem,3.6vw,2.6rem);font-weight:800;margin:.5rem 0 .8rem}
.s1-head p{color:var(--s1-muted);font-size:1.03rem}
.s1-btn{display:inline-block;border:0;border-radius:10px;padding:13px 22px;
 font-family:inherit;font-size:.82rem;font-weight:800;cursor:pointer;
 text-decoration:none;transition:.2s;text-align:center}
.s1-btn-primary{background:var(--s1-action);color:#fff;
 box-shadow:0 8px 20px rgba(10,34,64,.22)}
.s1-btn-primary:hover{background:var(--s1-action-d);transform:translateY(-1px)}
.s1-btn-ghost{background:rgba(255,255,255,.08);color:#fff;
 border:1px solid rgba(255,255,255,.22)}
.s1-btn-ghost:hover{background:rgba(255,255,255,.16)}
.s1-btn-line{background:#fff;color:var(--s1-navy);border:1px solid var(--s1-line)}
.s1-btn-line:hover{border-color:var(--s1-accent)}
.s1-btn-lg{padding:16px 30px;font-size:.92rem}
.s1-btns{display:flex;gap:14px;flex-wrap:wrap;align-items:center}

.s1blk-hero{background:
 radial-gradient(circle at 12% 8%,rgba(255,255,255,.12) 0,rgba(255,255,255,0) 42%),
 linear-gradient(180deg,var(--s1-navy),var(--s1-navy2));color:#fff;
 overflow:hidden;position:relative;padding:78px 0 88px}
.s1blk-hero .s1-eyebrow{color:var(--s1-warm)}
.s1blk-hero .s1-hero-in{max-width:820px}
.s1blk-hero h1{color:#fff;font-size:clamp(2.3rem,5.6vw,4.15rem);font-weight:900;
 margin:.6rem 0 1.2rem;letter-spacing:-.035em}
.s1blk-hero h1 .s1-hl{background:linear-gradient(90deg,var(--s1-warm),var(--s1-accent));
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.s1blk-hero .s1-lede{font-size:1.12rem;color:#c9d7e6;max-width:660px;margin:0 0 30px}
.s1blk-hero .s1-foot{margin-top:26px;font-size:.78rem;color:#93a7bd;font-weight:600}
.s1-pills{display:flex;flex-wrap:wrap;gap:10px;margin-top:34px}
.s1-pill{background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.16);
 border-radius:999px;padding:9px 15px;font-size:.74rem;font-weight:700;color:#e4edf5}
.s1-pill:before{content:"\\2713";color:var(--s1-warm);font-weight:900;margin-right:7px}

.s1blk-pillars{background:#fff}
.s1-pillar-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
.s1-pillar{border-radius:20px;padding:34px 32px;border:1px solid var(--s1-line);
 background:linear-gradient(180deg,var(--s1-tint),#fff)}
.s1-pillar:nth-child(even){background:linear-gradient(180deg,var(--s1-tint2),#fff)}
.s1-pillar-tag{display:inline-block;font-size:.68rem;font-weight:800;
 letter-spacing:.09em;text-transform:uppercase;padding:6px 12px;border-radius:999px;
 margin-bottom:16px;background:#fff;border:1px solid var(--s1-line);
 color:var(--s1-accent-d)}
.s1-pillar-ico{font-size:2rem;margin-bottom:6px}
.s1-pillar h3{font-size:1.4rem;margin:0 0 12px}
.s1-pillar p{color:var(--s1-ink);margin:0 0 14px;font-size:.96rem}
.s1-pillar .s1-plain{color:var(--s1-muted);font-size:.86rem;
 background:rgba(255,255,255,.7);border-left:3px solid var(--s1-accent);
 padding:12px 14px;border-radius:0 10px 10px 0}

.s1blk-channels{background:var(--s1-page)}
.s1-card-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.s1-card{background:#fff;border:1px solid var(--s1-line);border-radius:16px;
 padding:26px 24px;box-shadow:var(--s1-shadow-sm);transition:.2s}
.s1-card:hover{transform:translateY(-3px);box-shadow:var(--s1-shadow)}
.s1-card .s1-ico{width:48px;height:48px;border-radius:12px;display:grid;
 place-items:center;font-size:1.5rem;background:var(--s1-tint);margin-bottom:16px}
.s1-card:nth-child(even) .s1-ico{background:var(--s1-tint2)}
.s1-card h4{font-size:1.06rem;margin:0 0 8px}
.s1-card p{color:var(--s1-muted);font-size:.86rem}
.s1-chip{display:inline-block;margin-top:14px;font-size:.7rem;font-weight:800;
 letter-spacing:.03em;color:var(--s1-navy);background:#eef3f8;
 border:1px solid var(--s1-line);border-radius:999px;padding:5px 11px}

.s1blk-stats{background:var(--s1-navy);color:#fff}
.s1-stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px;text-align:center}
.s1-stat .s1-num{font-size:clamp(2rem,4vw,2.8rem);font-weight:900;line-height:1;
 background:linear-gradient(90deg,var(--s1-warm),var(--s1-accent));
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.s1-stat .s1-lbl{margin-top:12px;font-size:.8rem;color:#aebfd0;font-weight:600;
 line-height:1.4}

.s1blk-suite{background:#fff}
.s1-suite-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.s1-scard{border:1px solid var(--s1-line);border-radius:16px;padding:26px 24px;
 background:linear-gradient(180deg,#f8fbfd,#fff)}
.s1-scard .s1-ico{width:44px;height:44px;border-radius:11px;display:grid;
 place-items:center;font-size:1.35rem;background:var(--s1-tint);margin-bottom:14px}
.s1-scard h4{font-size:1.02rem;margin:0 0 8px}
.s1-scard p{color:var(--s1-muted);font-size:.85rem}

.s1blk-steps{background:var(--s1-page)}
.s1-step-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}
.s1-step{background:#fff;border:1px solid var(--s1-line);border-radius:16px;
 padding:26px 24px;box-shadow:var(--s1-shadow-sm);position:relative}
.s1-step .s1-n{width:38px;height:38px;border-radius:11px;display:grid;
 place-items:center;font-size:.95rem;font-weight:900;color:#fff;
 background:var(--s1-accent);margin-bottom:14px}
.s1-step h4{font-size:1.02rem;margin:0 0 8px}
.s1-step p{color:var(--s1-muted);font-size:.85rem}

.s1blk-pricing{background:var(--s1-page)}
.s1-price-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;
 max-width:920px;margin:0 auto}
.s1-price-grid.s1-three{grid-template-columns:repeat(3,1fr);max-width:1140px}
.s1-price{background:#fff;border:1px solid var(--s1-line);border-radius:20px;
 padding:34px 30px;position:relative;box-shadow:var(--s1-shadow-sm);
 display:flex;flex-direction:column}
.s1-price.s1-feature{border:2px solid var(--s1-action);
 box-shadow:0 24px 60px rgba(10,34,64,.16)}
.s1-badge{position:absolute;top:-13px;left:30px;background:var(--s1-action);
 color:#fff;font-size:.64rem;font-weight:800;letter-spacing:.06em;
 text-transform:uppercase;padding:6px 14px;border-radius:999px}
.s1-price .s1-tier{font-size:.72rem;font-weight:800;letter-spacing:.09em;
 text-transform:uppercase;color:var(--s1-accent-d)}
.s1-price h3{font-size:1.35rem;margin:6px 0 4px}
.s1-price .s1-amt{font-size:2.5rem;font-weight:900;color:var(--s1-navy);
 line-height:1;margin:14px 0 2px}
.s1-price .s1-amt span{font-size:.9rem;font-weight:700;color:var(--s1-muted)}
.s1-price .s1-who{color:var(--s1-muted);font-size:.83rem;margin:6px 0 20px}
.s1-price li{position:relative;padding:9px 0 9px 28px;font-size:.87rem;
 border-top:1px solid #eef2f6;color:var(--s1-ink)}
.s1-price li:first-child{border-top:0}
.s1-price li:before{content:"\\2713";position:absolute;left:0;top:9px;
 color:var(--s1-green);font-weight:900}
.s1-price .s1-btn{width:100%;margin-top:auto}
.s1-price ul{margin:0 0 24px}
.s1-price-note{text-align:center;color:var(--s1-muted);font-size:.82rem;
 max-width:640px;margin:30px auto 0}

.s1blk-quote{background:#fff}
.s1-quote{max-width:820px;margin:0 auto;text-align:center;
 background:linear-gradient(180deg,var(--s1-tint),#fff);
 border:1px solid var(--s1-line);border-radius:20px;padding:44px 40px}
.s1-quote .s1-mark{font-size:2.6rem;line-height:1;color:var(--s1-accent);
 font-weight:900}
.s1-quote blockquote{margin:10px 0 0;font-size:clamp(1.1rem,2.4vw,1.5rem);
 font-weight:700;color:var(--s1-navy);line-height:1.4;letter-spacing:-.015em}
.s1-quote .s1-by{margin-top:20px;font-size:.82rem;font-weight:700;
 color:var(--s1-navy)}
.s1-quote .s1-role{font-size:.78rem;color:var(--s1-muted);margin-top:3px}

.s1blk-faq{background:var(--s1-page)}
.s1-faq-grid{max-width:820px;margin:0 auto;display:grid;gap:14px}
.s1-faq{background:#fff;border:1px solid var(--s1-line);border-radius:14px;
 padding:22px 24px}
.s1-faq h4{font-size:1rem;margin:0 0 8px}
.s1-faq p{color:var(--s1-muted);font-size:.88rem}

.s1blk-cta{background:
 radial-gradient(circle at 85% 20%,rgba(255,255,255,.14) 0,rgba(255,255,255,0) 50%),
 linear-gradient(120deg,var(--s1-navy),#134077);color:#fff;text-align:center}
.s1blk-cta h2{color:#fff;font-size:clamp(1.9rem,4vw,2.8rem);font-weight:900;
 margin:0 0 14px}
.s1blk-cta p{color:#c9d7e6;font-size:1.05rem;max-width:560px;margin:0 auto 30px}
.s1blk-cta .s1-btns{justify-content:center}
.s1blk-cta .s1-foot{display:block;margin-top:20px;color:#8ea3ba;font-size:.78rem}

.s1blk-embed{background:var(--s1-page)}
.s1-frame{border-radius:20px;overflow:hidden;box-shadow:var(--s1-shadow);
 background:#fff}
.s1-frame iframe{width:100%;border:0;display:block;background:#fff}
.s1-frame-note{text-align:center;color:var(--s1-muted);font-size:.8rem;
 margin:16px auto 0;max-width:640px}

.s1blk-nav{padding:0;background:var(--s1-navy);position:sticky;top:0;z-index:50;
 border-bottom:1px solid rgba(255,255,255,.08)}
.s1-nav-in{display:flex;align-items:center;justify-content:space-between;
 padding:14px 0;gap:20px}
.s1-nav-in img{height:34px;width:auto}
.s1-nav-links{display:flex;align-items:center;gap:28px}
.s1-nav-links a{color:#cdd9e6;text-decoration:none;font-size:.82rem;
 font-weight:600;transition:.2s}
.s1-nav-links a:hover{color:#fff}

.s1blk-footer{background:var(--s1-deep);color:#8ea3ba;padding:46px 0 34px;
 font-size:.82rem}
.s1-foot-top{display:flex;justify-content:space-between;align-items:center;
 gap:24px;flex-wrap:wrap;padding-bottom:26px;
 border-bottom:1px solid rgba(255,255,255,.08)}
.s1-foot-top img{height:30px}
.s1-foot-links{display:flex;gap:24px;flex-wrap:wrap}
.s1-foot-links a{color:#b8c7d6;text-decoration:none}
.s1-foot-links a:hover{color:#fff}
.s1blk-footer .s1-disc{margin-top:24px;line-height:1.6;color:#6f849a;
 font-size:.76rem}
.s1blk-footer .s1-tm{margin-top:14px;color:#5f7488;font-size:.76rem}

@media(max-width:900px){
 .s1-pillar-grid,.s1-price-grid,.s1-price-grid.s1-three{grid-template-columns:1fr}
 .s1-card-grid,.s1-suite-grid,.s1-step-grid{grid-template-columns:1fr 1fr}
 .s1-stat-grid{grid-template-columns:1fr 1fr;gap:30px 20px}
}
@media(max-width:640px){
 .s1-card-grid,.s1-suite-grid,.s1-step-grid,.s1-nav-links{grid-template-columns:1fr}
 .s1-nav-links{display:none}
 .s1blk{padding:52px 0}
 .s1blk-hero{padding:56px 0 60px}
 .s1-btns .s1-btn{flex:1 1 auto}
}
"""


def stylesheet(theme: str = DEFAULT_THEME) -> str:
    """The whole house stylesheet for one theme, as a `<style>` element."""
    return ("<style>\n" + FONT_IMPORT + "\n" + theme_vars(theme) + "\n"
            + _CSS.strip() + "\n</style>")


# --------------------------------------------------------------- helpers
def e(v) -> str:
    """Escape for text content and attribute values alike."""
    return html.escape(str(v if v is not None else ""), quote=True)


def _href(v) -> str:
    """A link target we are willing to print.

    An anchor, a relative path, http(s) or a tel/mailto. Anything else — a
    `javascript:` URL above all — becomes `#`: this HTML is pasted onto the
    public website, and a block builder is not a place to smuggle script in
    through a link.
    """
    v = str(v or "").strip()
    if not v:
        return "#"
    if re.match(r"^(#|/|\.{0,2}/)", v):
        return e(v)
    if re.match(r"^(https?:|mailto:|tel:)", v, re.I):
        return e(v)
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$|\?)", v, re.I):   # bare domain typed
        return e("https://" + v)
    return "#"


def _rows(block: dict, key: str = "items") -> list[dict]:
    out = []
    for row in (block.get(key) or []):
        if isinstance(row, dict):
            out.append(row)
    return out


def _btn(label, href, cls="s1-btn-primary") -> str:
    label = str(label or "").strip()
    if not label:
        return ""
    return f'<a class="s1-btn {cls} s1-btn-lg" href="{_href(href)}">{e(label)}</a>'


def _head(block: dict) -> str:
    """Eyebrow / heading / intro, printing only the parts that are filled in."""
    eyebrow, title = str(block.get("eyebrow") or ""), str(block.get("title") or "")
    intro = str(block.get("intro") or "")
    if not (eyebrow or title or intro):
        return ""
    parts = ['<div class="s1-head">']
    if eyebrow:
        parts.append(f'<div class="s1-eyebrow">{e(eyebrow)}</div>')
    if title:
        parts.append(f"<h2>{e(title)}</h2>")
    if intro:
        parts.append(f"<p>{e(intro)}</p>")
    parts.append("</div>")
    return "".join(parts)


def _highlight(title: str, accent: str) -> str:
    """Print the headline with one phrase in the accent gradient.

    The phrase has to be escaped and matched *after* escaping, or a headline
    containing an apostrophe would never match its own highlight — which is
    most of them, since the house voice uses contractions.
    """
    t, a = e(title), e(str(accent or "").strip())
    if not a or a not in t:
        return t
    return t.replace(a, f'<span class="s1-hl">{a}</span>', 1)


# --------------------------------------------------------------- markup
def _markup(kind: str, b: dict) -> str:
    if kind == "hero":
        pills = "".join(f'<span class="s1-pill">{e(p.get("text"))}</span>'
                        for p in _rows(b, "pills") if str(p.get("text") or "").strip())
        bits = ['<div class="s1-wrap s1-hero-in">']
        if b.get("eyebrow"):
            bits.append(f'<div class="s1-eyebrow">{e(b["eyebrow"])}</div>')
        if b.get("title"):
            bits.append(f'<h1>{_highlight(b["title"], b.get("accent"))}</h1>')
        if b.get("lede"):
            bits.append(f'<p class="s1-lede">{e(b["lede"])}</p>')
        buttons = (_btn(b.get("cta_label"), b.get("cta_href"))
                   + _btn(b.get("cta2_label"), b.get("cta2_href"), "s1-btn-ghost"))
        if buttons:
            bits.append(f'<div class="s1-btns">{buttons}</div>')
        if pills:
            bits.append(f'<div class="s1-pills">{pills}</div>')
        if b.get("footnote"):
            bits.append(f'<p class="s1-foot">{e(b["footnote"])}</p>')
        bits.append("</div>")
        return "".join(bits)

    if kind == "pillars":
        cards = []
        for it in _rows(b)[:2]:
            c = ['<div class="s1-pillar">']
            if it.get("tag"):
                c.append(f'<span class="s1-pillar-tag">{e(it["tag"])}</span>')
            if it.get("icon"):
                c.append(f'<div class="s1-pillar-ico">{e(it["icon"])}</div>')
            if it.get("title"):
                c.append(f'<h3>{e(it["title"])}</h3>')
            if it.get("body"):
                c.append(f'<p>{e(it["body"])}</p>')
            if it.get("note"):
                c.append(f'<div class="s1-plain">{e(it["note"])}</div>')
            c.append("</div>")
            cards.append("".join(c))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-pillar-grid">{"".join(cards)}</div></div>')

    if kind == "channels":
        cards = []
        for it in _rows(b):
            c = ['<div class="s1-card">']
            if it.get("icon"):
                c.append(f'<div class="s1-ico">{e(it["icon"])}</div>')
            if it.get("title"):
                c.append(f'<h4>{e(it["title"])}</h4>')
            if it.get("body"):
                c.append(f'<p>{e(it["body"])}</p>')
            if str(it.get("chip") or "").strip():
                c.append(f'<span class="s1-chip">{e(it["chip"])}</span>')
            c.append("</div>")
            cards.append("".join(c))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-card-grid">{"".join(cards)}</div></div>')

    if kind == "stats":
        cells = "".join(
            f'<div class="s1-stat"><div class="s1-num">{e(it.get("num"))}</div>'
            f'<div class="s1-lbl">{e(it.get("label"))}</div></div>'
            for it in _rows(b))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-stat-grid">{cells}</div></div>')

    if kind == "suite":
        cards = []
        for it in _rows(b):
            c = ['<div class="s1-scard">']
            if it.get("icon"):
                c.append(f'<div class="s1-ico">{e(it["icon"])}</div>')
            if it.get("title"):
                c.append(f'<h4>{e(it["title"])}</h4>')
            if it.get("body"):
                c.append(f'<p>{e(it["body"])}</p>')
            c.append("</div>")
            cards.append("".join(c))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-suite-grid">{"".join(cards)}</div></div>')

    if kind == "steps":
        cards = []
        for n, it in enumerate(_rows(b), start=1):
            c = [f'<div class="s1-step"><div class="s1-n">{n}</div>']
            if it.get("title"):
                c.append(f'<h4>{e(it["title"])}</h4>')
            if it.get("body"):
                c.append(f'<p>{e(it["body"])}</p>')
            c.append("</div>")
            cards.append("".join(c))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-step-grid">{"".join(cards)}</div></div>')

    if kind == "pricing":
        tiers = _rows(b)[:3]
        cards = []
        for it in tiers:
            feat = " s1-feature" if it.get("featured") else ""
            c = [f'<div class="s1-price{feat}">']
            if it.get("featured") and str(it.get("badge") or "").strip():
                c.append(f'<span class="s1-badge">{e(it["badge"])}</span>')
            if it.get("tier"):
                c.append(f'<div class="s1-tier">{e(it["tier"])}</div>')
            if it.get("name"):
                c.append(f'<h3>{e(it["name"])}</h3>')
            if it.get("amount"):
                per = (f' <span>{e(it["per"])}</span>' if it.get("per") else "")
                c.append(f'<div class="s1-amt">{e(it["amount"])}{per}</div>')
            if it.get("who"):
                c.append(f'<p class="s1-who">{e(it["who"])}</p>')
            lines = "".join(f"<li>{e(f.get('text'))}</li>"
                            for f in _rows(it, "features")
                            if str(f.get("text") or "").strip())
            if lines:
                c.append(f"<ul>{lines}</ul>")
            cls = "s1-btn-primary" if it.get("featured") else "s1-btn-line"
            c.append(_btn(it.get("cta_label"), it.get("cta_href"), cls))
            c.append("</div>")
            cards.append("".join(c))
        three = " s1-three" if len(tiers) > 2 else ""
        note = (f'<p class="s1-price-note">{e(b["note"])}</p>'
                if b.get("note") else "")
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-price-grid{three}">{"".join(cards)}</div>'
                f"{note}</div>")

    if kind == "quote":
        by = ""
        if str(b.get("name") or "").strip():
            by += f'<div class="s1-by">{e(b["name"])}</div>'
        if str(b.get("role") or "").strip():
            by += f'<div class="s1-role">{e(b["role"])}</div>'
        return ('<div class="s1-wrap"><div class="s1-quote">'
                '<div class="s1-mark">“</div>'
                f'<blockquote>{e(b.get("quote"))}</blockquote>{by}</div></div>')

    if kind == "faq":
        rows = "".join(
            f'<div class="s1-faq"><h4>{e(it.get("q"))}</h4>'
            f'<p>{e(it.get("a"))}</p></div>' for it in _rows(b))
        return (f'<div class="s1-wrap">{_head(b)}'
                f'<div class="s1-faq-grid">{rows}</div></div>')

    if kind == "cta":
        bits = ['<div class="s1-wrap">']
        if b.get("title"):
            bits.append(f'<h2>{e(b["title"])}</h2>')
        if b.get("body"):
            bits.append(f'<p>{e(b["body"])}</p>')
        buttons = (_btn(b.get("cta_label"), b.get("cta_href"))
                   + _btn(b.get("cta2_label"), b.get("cta2_href"), "s1-btn-ghost"))
        if buttons:
            bits.append(f'<div class="s1-btns">{buttons}</div>')
        if b.get("footnote"):
            bits.append(f'<small class="s1-foot">{e(b["footnote"])}</small>')
        bits.append("</div>")
        return "".join(bits)

    if kind == "embed":
        src = str(b.get("src") or "").strip()
        try:
            height = max(320, min(2000, int(str(b.get("height") or 760))))
        except ValueError:
            height = 760
        if not src:
            # No URL is not an error the prospect should meet. Print the
            # section's copy and say, in the block itself, what is missing --
            # a silently absent iframe looks like a page that failed to load.
            frame = ('<p class="s1-frame-note">No tool URL was set for this '
                     'block yet.</p>')
        else:
            frame = ('<div class="s1-frame">'
                     f'<iframe src="{_href(src)}" loading="lazy" '
                     f'style="height:{height}px" title="Smart 1 planning tool">'
                     "</iframe></div>")
        return f'<div class="s1-wrap">{_head(b)}{frame}</div>'

    return ""


# --------------------------------------------------------------- rendering
def render_block(block: dict, theme: str = DEFAULT_THEME, *,
                 with_css: bool = True) -> str:
    """One block. Self-contained by default — stylesheet and section together.

    `with_css=False` is for the page modes, where the stylesheet is emitted
    once above all the sections.
    """
    kind = str(block.get("kind") or "")
    if kind not in BLOCKS:
        return ""
    inner = _markup(kind, block)
    if not inner:
        return ""
    anchor = re.sub(r"[^a-z0-9-]", "", str(block.get("anchor") or "").lower())
    at = f' id="{anchor}"' if anchor else ""
    section = f'<section class="s1blk s1blk-{kind}"{at}>{inner}</section>'
    if not with_css:
        return section
    return stylesheet(theme) + "\n" + section


def render_blocks(blocks: list, theme: str = DEFAULT_THEME) -> str:
    """Every block, with the stylesheet once. For pasting a whole page body."""
    body = "\n".join(x for x in
                     (render_block(b, theme, with_css=False) for b in blocks) if x)
    return stylesheet(theme) + "\n" + body


def nav_html(links: list, cta_label: str = "Free Consultation",
             cta_href: str = CONSULT_URL) -> str:
    items = "".join(
        f'<a href="{_href(l.get("href"))}">{e(l.get("label"))}</a>'
        for l in links if isinstance(l, dict) and str(l.get("label") or "").strip())
    cta = (f'<a class="s1-btn s1-btn-primary" href="{_href(cta_href)}">'
           f"{e(cta_label)}</a>" if str(cta_label or "").strip() else "")
    return ('<header class="s1blk s1blk-nav"><div class="s1-wrap s1-nav-in">'
            f'<a href="{SITE_URL}" aria-label="Smart 1 Marketing home">'
            f'<img src="{LOGO_URL}" alt="Smart 1 Marketing"></a>'
            f'<nav class="s1-nav-links">{items}</nav>{cta}</div></header>')


def footer_html(links: list, disclaimer: str = "") -> str:
    items = "".join(
        f'<a href="{_href(l.get("href"))}">{e(l.get("label"))}</a>'
        for l in links if isinstance(l, dict) and str(l.get("label") or "").strip())
    disc = f'<p class="s1-disc">{e(disclaimer)}</p>' if disclaimer else ""
    return ('<footer class="s1blk s1blk-footer"><div class="s1-wrap">'
            '<div class="s1-foot-top">'
            f'<img src="{LOGO_URL}" alt="Smart 1 Marketing">'
            f'<div class="s1-foot-links">{items}</div></div>{disc}'
            '<p class="s1-tm">© Smart 1 Marketing. All rights reserved.'
            "</p></div></footer>")


def render_page(page: dict) -> str:
    """A complete standalone document — for a page on its own URL.

    The nav and footer are only added when the page asks for them, because
    the usual destination is inside an existing site that has its own.
    """
    theme = str(page.get("theme") or DEFAULT_THEME)
    blocks = page.get("blocks") or []
    title = str(page.get("title") or "Smart 1 Marketing")
    desc = str(page.get("description") or "")

    nav_links = [{"label": b.get("nav_label") or BLOCKS.get(b.get("kind"), {}).get("label"),
                  "href": "#" + str(b.get("anchor") or "")}
                 for b in blocks if str(b.get("anchor") or "").strip()]
    head_extra = f'<meta name="description" content="{e(desc)}">' if desc else ""
    nav = nav_html(nav_links) if page.get("with_nav") else ""
    foot = (footer_html(nav_links, str(page.get("disclaimer") or ""))
            if page.get("with_footer") else "")
    body = "\n".join(x for x in
                     (render_block(b, theme, with_css=False) for b in blocks) if x)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f"<title>{e(title)}</title>\n{head_extra}\n"
            f"{stylesheet(theme)}\n"
            "<style>body{margin:0;background:#f3f7fa}</style>\n"
            "</head>\n<body>\n"
            f"{nav}\n{body}\n{foot}\n</body>\n</html>")


def sample_page(theme: str = DEFAULT_THEME) -> dict:
    """A page in the house shape, for someone starting from nothing.

    The order is the one the industry pages use and the one that reads best:
    hero, strategy, channels, proof, automation, close.
    """
    order = ["hero", "pillars", "channels", "stats", "suite", "cta"]
    return {
        "title": "New landing page",
        "theme": theme if theme in THEMES else DEFAULT_THEME,
        "with_nav": False, "with_footer": False,
        "blocks": [dict(BLOCKS[k]["sample"], kind=k, anchor="") for k in order],
    }


def new_block(kind: str) -> dict:
    """A block of one kind, prefilled with its sample copy."""
    if kind not in BLOCKS:
        raise ValueError(f"No such block type: {kind}")
    return dict(BLOCKS[kind]["sample"], kind=kind, anchor="")


def catalogue() -> dict:
    """Everything the builder UI needs to draw itself."""
    return {
        "themes": [dict(v, key=k) for k, v in THEMES.items()],
        "blocks": [dict(BLOCKS[k], key=k) for k in BLOCK_ORDER],
        "order": BLOCK_ORDER,
        "default_theme": DEFAULT_THEME,
    }
