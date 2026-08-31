"""Prompts harvested from Pickaxe, ready for hub/ai.py.

Eight tools from the "Smart 1 Test" workspace, absorbed rather than called:
none of them carries a knowledge base the Hub cannot hold (see
hub/pickaxe_registry.py for the two that do). Each entry preserves the
Pickaxe's working prompt as closely as possible — these prompts have been
producing accepted output for two years and are not to be "improved" in the
same change that moves them.

What WAS changed, uniformly:

  * The email/PDF outro is gone. Every Pickaxe ended by offering to email
    the answer and build a PDF; inside a Hub tool that reads as the bot
    interrogating the rep.
  * Form fields became named {placeholders}. The Hub prefills them from the
    client record instead of a rep retyping what Client 360 already knows.
  * The page analyzers now receive {page_text} — the actual fetched page.
    The originals said "analyze the website URL", which a model cannot open;
    they have been running on the model's guess about the page. Fetch with
    the same helper the FAQ Builder uses and pass the text in.

Each entry:

    "prompt"       the template; format with .format(**prefill)
    "temperature"  the Pickaxe's own setting — deliberate, keep it
                   (script writers run at 0 for consistency; idea
                   generators at 0.8)
    "module"       where it lands, and the module= for ai.chat()
    "purpose"      the purpose= for ai.chat(), so spend shows per tool
    "prefill"      placeholder -> where the Hub gets it
    "notes"        anything the integrator must know

Usage shape (identical for every entry):

    from hub import ai
    from hub.prompts_harvested import RADIO_SCRIPT

    copy = ai.chat(
        [{"role": "user", "content": RADIO_SCRIPT["prompt"].format(
            client=name, topic=topic, cta=cta, length=":30")}],
        module=RADIO_SCRIPT["module"], purpose=RADIO_SCRIPT["purpose"],
        temperature=RADIO_SCRIPT["temperature"])

Anything whose output can reach a proposal goes through
proposal_spec.clean_ai_text() after, like every other piece of copy.

Wiring status — because a prompt landed here and wired nowhere is the
declared-but-unwired failure CLAUDE.md counts six of. AD_COPY and
AD_EXTENSIONS are wired (modules/ads_builder/copy_ideas.py, this change). The
remaining seven land with the module each names, in the order
docs/pickaxe-integration.md gives, so the two-year-old wording travels here
once rather than being re-harvested per PR.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Proposal Builder — Executive Summary step
# ---------------------------------------------------------------------------
# Source: "Spend and Demo" (Spend_and_Demo_IHN83), temp 0.
# Feeds the summary/objectives intake, NOT the Cover — OUTLINE's cover is
# visual and carries no copy. The "Hmm, I am not sure." guard is the
# original's own hallucination brake; keep it.
SPEND_AND_DEMO = {
    "module": "sales_builder",
    "purpose": "spend_demo_briefing",
    "temperature": 0,
    "prefill": {
        "client":    "client_context — client name",
        "website":   "client_context — canonical site URL",
        "industry":  "client record / industries.py",
        "locations": "client record; fall back to target_areas summary()",
    },
    "prompt": (
        "Act as a digital marketing sales assistant for Smart 1 Marketing. "
        "Your name is 'Smart 1 Assistant'.\n"
        "The client is {client}.\n"
        "The client website is: {website}.\n"
        "The client's industry is: {industry}.\n"
        "Business locations is: {locations}\n\n"
        "Using the information that has been provided, give the salesperson "
        "info on what might be the amount that a business spends on digital "
        "marketing. They have the understanding it can vary widely depending "
        "on various factors, including the size of the business, its "
        "location, marketing objectives, and overall budget. Try to provide "
        "as much information as you can. Do not suggest they find a "
        "professional but give them a summary that would be helpful, even "
        "adding any industry information that you can obtain. Then provide "
        "any information you have on the demographics of the location, "
        "including population size, age distribution, income levels, and "
        "other relevant factors. If you cannot find specific information on "
        "the location or city itself, look at the larger metro area or DMA "
        "that the location is in. Provide growth trends and economic "
        "conditions in the area. If possible give recommendations that "
        "relate to growth trends, economic conditions, demographics, and "
        "psychographics of the area. Exclude influencers as a part of your "
        "digital strategy. If the answer is not included, say exactly "
        "'Hmm, I am not sure.' and stop after that."
    ),
    "notes": "Internal sales briefing. If any of it is pasted toward the "
             "proposal it passes through clean_ai_text() like all copy.",
}

# ---------------------------------------------------------------------------
# Smart 1 Ads — Google Ads extensions
# ---------------------------------------------------------------------------
# Source: "Suggest Ad Extension Ideas" (Suggest_Ad_Extension_Idea_H328Z), 0.8.
AD_EXTENSIONS = {
    "module": "ads",
    "purpose": "ad_extensions",
    "temperature": 0.8,
    "prefill": {
        "client":       "client_context",
        "landing_page": "campaign landing page URL",
        "industry":     "client record",
        "page_text":    "fetched landing page text (FAQ Builder's fetcher)",
    },
    "prompt": (
        "Client Name: {client}\n"
        "Landing Page for Client: {landing_page}\n"
        "Industry: {industry}\n\n"
        "The landing page's content:\n{page_text}\n\n"
        "Based on the landing page above, generate a list of Google Ads "
        "Extension recommendations that are relevant to the client's "
        "industry and business objectives. Include various extension types "
        "such as callout, sitelink, location, promotion, and structured "
        "snippets, ensuring that the suggestions enhance the ad's "
        "visibility and performance."
    ),
    "notes": "The original asked the model to read the URL itself — it "
             "cannot. {page_text} is the fix; everything else is verbatim.",
}

# ---------------------------------------------------------------------------
# Smart 1 Ads — ad copy
# ---------------------------------------------------------------------------
# Source: "Ad Copy Creation" (Ad_Copy_Creation_NNP1I), 0.8. Its eight form
# fields are all already on the client record or the campaign — prefill all
# of them; the rep should type nothing. Objective was a checkbox: Drive
# Sales / Leads / Sign Ups / Increase total traffic (comma-join if several).
AD_COPY = {
    "module": "ads",
    "purpose": "ad_copy",
    "temperature": 0.8,
    "prefill": {
        "client":       "client_context",
        "landing_page": "campaign landing page URL",
        "industry":     "client record",
        "objective":    "campaign — one or more of: Drive Sales, Leads, "
                        "Sign Ups, Increase total traffic",
        "audience":     "audience_spec.for_prompt() once the Audience "
                        "Finder integration lands; typed until then",
        "products":     "client record / campaign",
        "usp":          "client record (business description)",
        "cta":          "campaign",
    },
    "prompt": (
        "Client Name: {client}\n"
        "Landing Page for Client: {landing_page}\n"
        "Industry: {industry}\n"
        "Campaign Objective: {objective}\n"
        "Target Audience: {audience}\n"
        "Key Products/Services: {products}\n"
        "Unique Selling Proposition (USP): {usp}\n"
        "Call to Action (CTA): {cta}\n\n"
        "Create Google Ads copy for the client's campaign, focusing on "
        "their target audience and unique selling points. Generate multiple "
        "variations of headlines and descriptions, ensuring they are "
        "compelling, keyword-rich, and aligned with the campaign objective. "
        "Incorporate a clear call to action that drives engagement and "
        "conversions. Additionally, suggest any improvements or A/B testing "
        "ideas for the ad copy to enhance performance."
    ),
    "notes": "Respect platform character limits at render time — the ads "
             "module already knows them; the prompt deliberately does not.",
}

# ---------------------------------------------------------------------------
# Shared helper — CTA review of any landing page
# ---------------------------------------------------------------------------
# Source: "Analyze a Page for CTA" (Analyze_a_Page_for_CTA_URAHF), 0.8.
# Callable from the Landing Page Maker, the SEO client page and the
# Homepage review — one function, three buttons.
CTA_ANALYZER = {
    "module": "seo",
    "purpose": "cta_review",
    "temperature": 0.8,
    "prefill": {
        "client":    "client_context",
        "url":       "the page under review",
        "industry":  "client record",
        "page_text": "fetched page text — REQUIRED, this is the analysis input",
    },
    "prompt": (
        "Client: {client}\n"
        "Website URL: {url}\n"
        "Client's Industry: {industry}\n\n"
        "The page's content:\n{page_text}\n\n"
        "Analyze the current calls to action (CTAs) on the page above. "
        "Evaluate the placement, wording, and effectiveness of each CTA. "
        "Consider how well these CTAs align with the needs of the client's "
        "industry audience. Then provide suggestions for improvement, "
        "focusing on how to make the CTAs more actionable, engaging, and "
        "tailored to different customer journey stages. Include specific "
        "recommendations for changes in wording, placement, and design to "
        "increase engagement and conversion rates, particularly in the "
        "context of the client's industry."
    ),
    "notes": "Placement/design judgments come from the text alone — say so "
             "in the UI ('reviewed from page copy, not a rendered view').",
}

# ---------------------------------------------------------------------------
# Social content planner — page review
# ---------------------------------------------------------------------------
# Source: "Analyze Current Social Media Pages" (43WNC), 0.8. The original
# listed four platform URLs and told the model to analyze them — pages a
# model can neither open nor, for Facebook/GMB, even fetch reliably from a
# server. The rubric is what's worth keeping. {pages_block} is whatever the
# Hub can actually gather per platform: page name, follower counts, recent
# post text, GMB listing data. For a platform with nothing fetchable, put
# the URL and the line "no data could be retrieved" — the model must be told
# that rather than left to invent an analysis.
SOCIAL_PAGES_REVIEW = {
    "module": "social_planner",
    "purpose": "pages_review",
    "temperature": 0.8,
    "prefill": {
        "client":      "client_context",
        "pages_block": "per-platform block the Hub assembles; absent data "
                       "labeled 'no data could be retrieved'",
    },
    "prompt": (
        "This is client: {client}\n\n"
        "Their social pages, with the data that could be retrieved for "
        "each:\n{pages_block}\n\n"
        "For each page that has data, identify and list five positive "
        "aspects. Review any consistency issues across the pages and give "
        "suggestions. Provide a detailed set of recommendations for "
        "improving each page's performance and engagement. Offer strategic "
        "suggestions for marketing these pages moving forward to enhance "
        "visibility, audience growth, and conversion rates. For a page "
        "marked as having no retrievable data, say that it was not "
        "reviewed rather than guessing about it."
    ),
    "notes": "The 'not reviewed rather than guessing' sentence is new and "
             "load-bearing — the original invented reviews of pages it "
             "never saw. Absent data reads as 'not measured', per CLAUDE.md.",
}

# ---------------------------------------------------------------------------
# Social content planner — the calendar itself
# ---------------------------------------------------------------------------
# Source: "Content Calendar Builder" (A4CRI8JC16QF) — inspected and found
# EMPTY: a bare gpt-5 chat with no prompt frame. Nothing existed to harvest,
# so this starter is NEW, written against the planner's needs. Edit freely —
# unlike every other entry, there is no two-years-of-accepted-output reason
# to keep its wording.
CONTENT_CALENDAR = {
    "module": "social_planner",
    "purpose": "calendar_draft",
    "temperature": 0.8,
    "prefill": {
        "client":   "client_context",
        "industry": "client record",
        "month":    "target month",
        "focus":    "what the client wants pushed (from the rep, or the "
                    "campaign's products)",
        "channels": "the platforms this client actually posts to",
    },
    "prompt": (
        "You plan social media content for {client}, a business in the "
        "{industry} industry. Draft a content calendar for {month} for "
        "these channels: {channels}. This month's focus: {focus}.\n\n"
        "Produce one row per post: date, channel, content idea in one or "
        "two sentences, suggested format (photo, reel, carousel, text), "
        "and a first-draft caption. Two to three posts per week per "
        "channel unless the focus demands more. Vary the mix between "
        "promotional, educational and community posts, and tie ideas to "
        "the month's real seasonality where it genuinely fits. Do not "
        "invent client-specific facts, offers or events — where one would "
        "help, mark the slot as 'needs a real offer/date from the client'."
    ),
    "notes": "NEW prompt, not harvested — the source Pickaxe was empty.",
}

# ---------------------------------------------------------------------------
# Landing Page Maker — the Smart 1 Snap concept
# ---------------------------------------------------------------------------
# Source: "Create a mockup of view for pages" (7M0IB), 0.8. The value is the
# Snap positioning language — the product framing lives in this prompt and
# nowhere else in writing. The original ended with "Create images for the
# first 3 suggested pages"; image generation is the Hub's job now (ai.image()
# from the page outlines) — dropped from the text prompt.
SNAP_CONCEPT = {
    "module": "landing_maker",
    "purpose": "snap_concept",
    "temperature": 0.8,
    "prefill": {
        "client":    "client_context",
        "website":   "client_context",
        "industry":  "client record",
        "location":  "client record",
        "extra":     "free-text from the rep (optional; pass '' if none)",
        "snap_type": "what kind of Snap — event, promotion, lead capture...",
    },
    "prompt": (
        "I want you to act as a digital marketing professional for "
        "https://www.smart1marketing.com and https://www.smart1snap.com. "
        "Your name is 'Smart 1 Snap Assistant'.\n"
        "Company Name: {client}\n"
        "Company Website: {website}\n"
        "Industry: {industry}\n"
        "Location of Company: {location}\n"
        "Additional Information: {extra}\n"
        "Type of Smart 1 Snap to create: {snap_type}\n\n"
        "Using the information provided, create an idea for a microsite. "
        "The microsite will also have functions like a progressive web "
        "app. The microsite will be mobile-focused. Don't reference it as "
        "a microsite but call it a 'Smart 1 Snap'. The microsite could be "
        "accessed by a QR code, a short link, or a text with a short code "
        "and message that gives them a link to the site. When giving the "
        "suggestion of text marketing, assume that the client does not "
        "have a list that they can text to — text marketing would be to "
        "gather a database and market consistently. The microsite will "
        "also be able to take a pixel to remarket to. Explain the benefits "
        "of creating a Smart 1 Snap to market their business. When you "
        "give the suggestion, outline 5 pages of content they could create "
        "to help the customer that views it. Then give at least 5 "
        "suggestions for marketing the microsite. Do not suggest SEO "
        "optimization as a suggestion. If it is an event, give suggestions "
        "for marketing pre-event, during the event, and post-event."
    ),
    "notes": "Follow with ai.image() per outlined page if the concept is "
             "kept — the original generated 3 images here.",
}

# ---------------------------------------------------------------------------
# Fan Radio / Radio Promo — radio spot scripts
# ---------------------------------------------------------------------------
# Source: "Radio Scripts" (Radio_Scripts_ZVFM4), temp 0 — deliberate: the
# same brief should produce the same script. Length choices were :15/:30/:60.
# Also the audio half of the creative-needs gate: an audio line item with no
# spot can now carry a drafted one.
RADIO_SCRIPT = {
    "module": "radio_promo",
    "purpose": "radio_script",
    "temperature": 0,
    "prefill": {
        "client": "client_context",
        "topic":  "what the spot is about (from the rep or the campaign)",
        "cta":    "campaign CTA",
        "length": "one of ':15', ':30', ':60'",
    },
    "prompt": (
        "You are an audio scriptwriter for Smart 1 Marketing, an ad "
        "agency. You are creating a script for a radio commercial.\n"
        "For this client: {client}\n"
        "Create a commercial for this topic: {topic}\n"
        "Call to action: {cta}\n"
        "Length of script: {length}\n\n"
        "Based on the information provided, create an audio script that is "
        "friendly and explains the topic to the target audience. Just "
        "provide the text for the script — no need to add sound effects. "
        "There will be only one narrator."
    ),
    "notes": "",
}

# ---------------------------------------------------------------------------
# Commercial Builder — streaming / TV scripts
# ---------------------------------------------------------------------------
# Source: "TV Scripts" (TV_Scripts_Z9BIX), temp 0. The CTV half of the
# creative-needs gate.
TV_SCRIPTS = {
    "module": "commercial_builder",
    "purpose": "tv_scripts",
    "temperature": 0,
    "prefill": {
        "client":   "client_context",
        "website":  "client_context",
        "industry": "client record",
        "phone":    "client record contact",
        "products": "campaign / client record",
        "cta":      "campaign CTA",
    },
    "prompt": (
        "You are an online video creator for Smart 1 Marketing, assisting "
        "a client in building out ideas for TV commercial scripts for "
        "online video.\n"
        "The client is {client}\n"
        "Client website: {website}\n"
        "Client Industry: {industry}\n"
        "Client Phone number: {phone}\n"
        "Services or Products offered: {products}\n"
        "Call to action: {cta}\n\n"
        "Please provide:\n"
        "1. Creative ideas for streaming video commercials that align with "
        "the client's brand and objectives.\n"
        "2. Scripts for video spots of different lengths: :06, :15, :30, "
        "and :60 seconds.\n"
        "3. Suggestions for visual and audio elements to enhance the "
        "commercials.\n"
        "4. Best practices for capturing and retaining viewer attention in "
        "short and long video formats.\n"
        "5. Any additional tips for optimizing video content for streaming "
        "platforms."
    ),
    "notes": "",
}

# Deliberately absent: "ROI for Digital Products". Its nominated home was
# the proposal's ROI section, and proposal_spec's standing directive is that
# Expected Results & ROI is computed from the rate card, never model-written
# — a generated projection beside a computed table they may disagree with,
# on the page a client signs. Excluded at the owner's direction.

ALL = {
    "spend_and_demo": SPEND_AND_DEMO,
    "ad_extensions": AD_EXTENSIONS,
    "ad_copy": AD_COPY,
    "cta_analyzer": CTA_ANALYZER,
    "social_pages_review": SOCIAL_PAGES_REVIEW,
    "content_calendar": CONTENT_CALENDAR,
    "snap_concept": SNAP_CONCEPT,
    "radio_script": RADIO_SCRIPT,
    "tv_scripts": TV_SCRIPTS,
}
