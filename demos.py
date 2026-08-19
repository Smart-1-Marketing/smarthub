"""Guided demos that run inside the real tools.

The point is not to show somebody a sandbox copy of the Hub. It's to teach a
specific tool by driving that tool's own screen — filling its real fields,
clicking its real buttons, with narration attached to each step and sample
results supplied where the step would otherwise spend a credit.

Two rules this is built on:

  1. **The demo drives the real UI.** A separate "demo version" of a screen
     teaches the demo version. Muscle memory only transfers if the buttons are
     in the places they'll actually be in on Monday.

  2. **Every step has a "notice this".** A walkthrough that only says "now
     click Save" teaches clicking. The value is in the sentence that explains
     why the field matters — which is why every step here carries one.

Sample results are used only where a step would spend money (Insites credits,
remove.bg credits, OpenAI tokens) or write to a live client system. Everything
else runs for real: the resize genuinely resizes, the UTM builder genuinely
normalises, the crop tool genuinely crops.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    """One move in a demo.

    action:
      fill     — type `value` into `selector`
      choose   — select `value` in a <select> or click a matching option
      click    — click `selector`
      upload   — load `sample` (a bundled file) into the file input at `selector`
      wait     — pause for `selector` to appear (a real result arriving)
      look     — no interaction; just narrate and highlight
    """
    title: str
    body: str                       # what's happening
    notice: str = ""                # the bit worth remembering
    action: str = "look"
    selector: str = ""
    value: str = ""
    sample: str = ""
    simulated: bool = False         # true when a real call would cost money
    autofill: bool = True           # false = the learner types it themselves

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Scenario:
    key: str
    module: str
    title: str
    goal: str                       # what you'll have when you're done
    minutes: int
    path: str                       # where the tool lives
    steps: list[Step] = field(default_factory=list)
    spends: list[str] = field(default_factory=list)   # capabilities simulated

    def as_dict(self) -> dict:
        d = asdict(self)
        d["steps"] = [s.as_dict() for s in self.steps]
        d["step_count"] = len(self.steps)
        return d


SCENARIOS: list[Scenario] = [

    # ------------------------------------------------------------------
    Scenario(
        key="seo_images.first_batch", module="seo_images",
        title="Name and optimise a batch of client photos",
        goal="Three photos renamed for SEO, resized, converted to WebP, and "
             "saved to a client's gallery with alt text — plus the <img> tags "
             "ready to paste.",
        minutes=6, path="/tools/seo-images",
        spends=["openai.text"],
        steps=[
            Step("Start with the client, not the picture",
                 "Fill in the company, the page URL the images will live on, "
                 "and a project name.",
                 "This is the whole trick. The AI gets these three fields along "
                 "with the picture, which is the difference between "
                 "'man-with-tools.webp' and "
                 "'riverside-hvac-technician-servicing-outdoor-ac-unit.webp'. "
                 "Skip them and you get generic names.",
                 action="fill", selector="[name='company']", value="Riverside HVAC"),
            Step("Point it at the page",
                 "The specific page URL, not just the domain.",
                 "A photo destined for the furnace-repair page should be named "
                 "differently from the same photo on the about page.",
                 action="fill", selector="[name='page_url']",
                 value="https://riverside-hvac.example/furnace-repair"),
            Step("Name the project",
                 "Anything you'll recognise later. This becomes the Cloudinary "
                 "folder and the archive filter.",
                 "Use the same shape every time — 'furnace-repair-2026' beats "
                 "'photos'. You'll be searching these in six months.",
                 action="fill", selector="[name='project']", value="furnace-repair-2026"),
            Step("Add the photos",
                 "Drag in up to five. We'll use three sample camera photos — "
                 "full-size, straight off a phone.",
                 "These are 6000 pixels wide, which is what a real phone photo "
                 "looks like. Watch what happens to them.",
                 action="upload", selector="input[type='file']",
                 sample="demo/hvac-batch"),
            Step("Choose the resize cap",
                 "Set the longest edge to 2400.",
                 "This matters more than the file format does. Converting a "
                 "6000-pixel photo to WebP leaves it 6000 pixels wide and still "
                 "enormous — the cap is what actually shrinks it. 2400 for hero "
                 "images, 1600 for in-page, 1200 for thumbnails.",
                 action="choose", selector="[name='max_edge']", value="2400"),
            Step("Let the AI name them",
                 "Click Analyse. Each photo comes back with a proposed filename "
                 "and alt text.",
                 "In this demo the names are pre-supplied so we don't spend "
                 "tokens — but they're exactly what the live call returns for "
                 "these inputs.",
                 action="click", selector="[data-demo='analyse']", simulated=True),
            Step("Read the alt text, then fix it",
                 "Edit anything that's wrong. The counter turns amber past 125 "
                 "characters.",
                 "Screen readers cut off around 125 characters and Google gives "
                 "you nothing past roughly there either. Describe the picture to "
                 "someone who can't see it. Don't stuff keywords — that reads as "
                 "spam to both.",
                 action="look", selector="[data-demo='alt-field']", autofill=False),
            Step("Try 'Ask AI again' on one",
                 "If a name is off, regenerate just that one rather than "
                 "starting the batch over.",
                 "Per-image, not per-batch. You'll use this constantly.",
                 action="look", selector="[data-demo='reask']", simulated=True),
            Step("Save",
                 "Converts to WebP, uploads to the client's folder, and writes "
                 "company, URL, project and alt text into each asset's "
                 "Cloudinary context.",
                 "The context fields are why the archive is searchable later "
                 "instead of being a wall of filenames.",
                 action="click", selector="[data-demo='save']"),
            Step("Look at what you saved",
                 "Compare the before and after sizes.",
                 "A 9 MB phone photo lands around 1.1 MB at 2400px — roughly 88% "
                 "off, with no visible quality loss at web sizes. That is a "
                 "Core Web Vitals score you just moved.",
                 action="look", selector="[data-demo='results']"),
            Step("Copy the tag, not the URL",
                 "'Copy <img> tag' gives you the URL, alt text and width/height "
                 "already filled in.",
                 "The dimensions stop the page jumping around as images load, "
                 "which is its own ranking factor. Copying just the URL loses "
                 "that.",
                 action="click", selector="[data-demo='copy-tag']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="image_creator.promo_post", module="image_creator",
        title="Build a 1080×1080 promo post for a client",
        goal="A finished square social post with a stock background, the "
             "client's logo, brand colours and headline text — saved as a "
             "reopenable project and exported as PNG.",
        minutes=8, path="/tools/image-creator",
        spends=["openai.text"],
        steps=[
            Step("Pick the size first",
                 "Choose 1080 × 1080.",
                 "Changing canvas size later rescales everything on it. Decide "
                 "now: 1080×1080 feed post, 1080×1920 stories and reels, "
                 "1200×630 link preview, 300×250 and 728×90 display ads.",
                 action="choose", selector="[data-demo='canvas-size']", value="1080x1080"),
            Step("Search all three stock libraries at once",
                 "Open Photos and search for an HVAC technician.",
                 "This hits Pexels, Pixabay and Unsplash in parallel and mixes "
                 "the results. You don't pick a provider, and if one is down the "
                 "others still return.",
                 action="fill", selector="[data-demo='photo-search']",
                 value="hvac technician outside home"),
            Step("Or describe it instead",
                 "Stuck for search terms? Switch to 'Describe it' and write the "
                 "situation in plain English.",
                 "Type 'someone frustrated because their AC stopped working' and "
                 "it becomes three searches that match how stock libraries "
                 "actually tag things. Optional — normal keyword search always "
                 "works.",
                 action="look", selector="[data-demo='describe-search']", simulated=True),
            Step("Set it as the background",
                 "Click a photo to add it, then use Fill Canvas.",
                 "Fill crops to cover the whole canvas; Fit letterboxes it. For "
                 "a background you almost always want Fill.",
                 action="click", selector="[data-demo='fill-canvas']"),
            Step("Pull in the client's logo",
                 "Open Logos and type the company name — not just the domain.",
                 "Brandfetch returns the logo variants *and* the brand colours, "
                 "and the colours drop straight into your picker. That's how you "
                 "stay on-brand without asking anyone for hex codes.",
                 action="fill", selector="[data-demo='logo-search']", value="Riverside HVAC"),
            Step("Add a headline",
                 "Text → Add Heading, then type the offer.",
                 "Use the brand colour that just appeared in your recent "
                 "colours. Keep headlines under about six words at this size — "
                 "it's being read at thumbnail scale in a feed.",
                 action="fill", selector="[data-demo='text-input']",
                 value="Beat the Heat — $89 Tune-Up"),
            Step("Check the layers panel",
                 "Every element is still its own object.",
                 "Nothing here is flattened, including anything the AI "
                 "generated. That's the design decision the whole tool is built "
                 "around — reopen this project in six months and the text is "
                 "still editable text.",
                 action="look", selector="[data-demo='layers']"),
            Step("Save the project",
                 "Give it a name and attach it to the client.",
                 "Saves the editable Fabric JSON plus a small preview, on the "
                 "client's record. The next person builds the autumn version "
                 "from this instead of starting over.",
                 action="click", selector="[data-demo='save-project']"),
            Step("Export",
                 "Download → PNG at 1×.",
                 "1× for the web, 2× for print or a retina screen. Transparent "
                 "background only works on PNG and WebP, and only when nothing "
                 "covers the full canvas.",
                 action="click", selector="[data-demo='export']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="utm.campaign_links", module="utm_builder",
        title="Build tracked links for a campaign",
        goal="Three consistent tracked links for one campaign across email, "
             "paid social and Google — saved to the client so nobody invents "
             "their own naming next time.",
        minutes=4, path="/tools/utm",
        steps=[
            Step("Start with the destination",
                 "Paste the landing page the ad points at.",
                 "Use the final URL, not a redirect. A redirect can strip the "
                 "parameters and you'll see the traffic as direct.",
                 action="fill", selector="[name='url']",
                 value="https://riverside-hvac.example/spring-tune-up"),
            Step("Source — where the click came from",
                 "The specific property: google, facebook, linkedin, the "
                 "newsletter's name.",
                 "Source is *which* place. Medium is *what kind* of place. "
                 "Getting these two backwards is the single most common mistake "
                 "in tracked links.",
                 action="fill", selector="[name='utm_source']", value="facebook"),
            Step("Medium — what kind of channel",
                 "cpc, email, social, referral, display.",
                 "Google Analytics groups traffic by this field and it is "
                 "case-sensitive. 'Paid Social' and 'paid-social' become two "
                 "separate rows in every report you ever run. Watch the field "
                 "normalise what you type — that's the whole reason this tool "
                 "exists rather than building links by hand.",
                 action="fill", selector="[name='utm_medium']", value="Paid Social"),
            Step("See what it did to that",
                 "You typed 'Paid Social'. Look at the generated link.",
                 "It became 'paid-social'. Every person on the team building a "
                 "link through this tool produces the same value, so the report "
                 "stays one row.",
                 action="look", selector="[data-demo='utm-preview']"),
            Step("Campaign — name it for your future self",
                 "One name, reused across every channel in the campaign.",
                 "'spring-tuneup-2026' beats 'promo'. You'll read this in a "
                 "report long after you've forgotten the context, and the shared "
                 "campaign name is what lets you compare channels against each "
                 "other.",
                 action="fill", selector="[name='utm_campaign']", value="spring-tuneup-2026"),
            Step("Save it to the client",
                 "Attach to Riverside HVAC.",
                 "Saved links live on the client record. The next person copies "
                 "your naming instead of inventing their own — which is the "
                 "actual problem this solves.",
                 action="click", selector="[data-demo='utm-save']"),
            Step("Now do the other two channels",
                 "Change source to google / medium to cpc, and source to the "
                 "newsletter name / medium to email. Same campaign name.",
                 "Three links, one campaign, comparable in the report. That's "
                 "the finished job.",
                 action="look", autofill=False),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="scans.read_audit", module="scans",
        title="Read a site audit and turn it into talking points",
        goal="A completed audit read the way you'd read it before a sales call, "
             "with three findings you could open a conversation with.",
        minutes=5, path="/scans",
        spends=["insites.scan"],
        steps=[
            Step("Check the domain before you start",
                 "One scan, one domain, one Insites credit.",
                 "Paste the domain, not an email address. The tool rejects "
                 "emails now, but it used to accept them and spend a credit "
                 "auditing nonsense.",
                 action="fill", selector="[name='domain']",
                 value="riverside-hvac.example", simulated=True),
            Step("Start it, then walk away",
                 "Audits take one to four minutes.",
                 "The page pulls results from Insites every 45 seconds for up to "
                 "fifteen minutes. You don't need to sit on it. We'll jump to a "
                 "finished sample audit.",
                 action="click", selector="[data-demo='start-scan']", simulated=True),
            Step("Read the scores first",
                 "Performance, SEO, mobile, security.",
                 "Scores tell you where to look. They're not the pitch — nobody "
                 "buys because a number was 47.",
                 action="look", selector="[data-demo='scan-scores']"),
            Step("Find the findings you can say out loud",
                 "Look for the ones a business owner can feel.",
                 "'No SSL redirect' means their site shows 'Not secure' to some "
                 "visitors. '14 images over 1 MB' means the site is slow on a "
                 "phone. Those are conversations. 'Missing meta descriptions' is "
                 "not — it's a task.",
                 action="look", selector="[data-demo='scan-findings']"),
            Step("Notice what feeds other tools",
                 "The image findings hand straight to the SEO Image Pipeline. "
                 "The missing schema hands to Schema Builder.",
                 "That's the point of having them in one Hub — the audit isn't a "
                 "report, it's a work list.",
                 action="look", selector="[data-demo='scan-actions']"),
            Step("If a scan errors, retry it",
                 "'Try again' re-pulls without spending a second credit.",
                 "An errored scan is usually recoverable. Don't start a fresh "
                 "one — that's a credit you don't get back.",
                 action="look", selector="[data-demo='scan-retry']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="bg_remover.logo_cutout", module="bg_remover",
        title="Cut out a logo the cheap way first",
        goal="A transparent PNG of a client logo, ideally without spending a "
             "credit.",
        minutes=3, path="/tools/bg-remover",
        spends=["removebg.cutout"],
        steps=[
            Step("Look at the credit balance before anything else",
                 "It's shown at the top.",
                 "Every AI cutout spends one. The balance is deliberately shown "
                 "*before* you can click, not after.",
                 action="look", selector="[data-demo='credit-balance']"),
            Step("Upload the logo",
                 "A client logo on a plain white background.",
                 "Most logos you'll be handed look exactly like this.",
                 action="upload", selector="input[type='file']", sample="demo/logo-white-bg"),
            Step("Try the free option first",
                 "Choose 'Remove white background'.",
                 "This runs entirely in your browser. No API call, no credit, no "
                 "waiting. For a logo on flat white it's usually as good as the "
                 "paid version.",
                 action="click", selector="[data-demo='remove-white']"),
            Step("Check the edges",
                 "Zoom in on the curves and any drop shadow.",
                 "If the edges are clean, you're done and it cost nothing. If "
                 "there's a halo or a soft shadow got chewed, that's when the "
                 "paid AI removal earns its credit.",
                 action="look", selector="[data-demo='cutout-preview']"),
            Step("Now try the AI one for comparison",
                 "Same image, 'Remove background'.",
                 "Identical images are cached by content hash, so retrying the "
                 "same file never charges twice. Save this for photographs and "
                 "complicated edges — hair, glass, foliage.",
                 action="click", selector="[data-demo='remove-ai']", simulated=True),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="seo.faq_and_schema", module="seo",
        title="Generate FAQ and schema for a client page",
        goal="Five approved Q&As saved to the client, an accordion block that "
             "matches their site's fonts, and LocalBusiness schema built from "
             "what the Hub already knows.",
        minutes=7, path="/seo",
        spends=["openai.text"],
        steps=[
            Step("Check what's already on file",
                 "Open Schema Builder. It shows what it already has.",
                 "'Already on file: 6 of 8 fields. Still missing: hours, zip.' "
                 "It uses the client record first and only goes looking for "
                 "their Google Business Profile to fill the blanks. It never "
                 "overwrites something you entered.",
                 action="look", selector="[data-demo='schema-known']"),
            Step("Let it find the rest",
                 "Click to look up the missing fields.",
                 "It reads their own site and searches for their GMB listing. "
                 "Check what comes back before accepting — a wrong phone number "
                 "in schema is worse than no schema.",
                 action="click", selector="[data-demo='schema-lookup']", simulated=True),
            Step("Now the FAQ builder",
                 "Paste the page URL you want questions for.",
                 "It reads that page *and* the rest of the site, so the answers "
                 "are consistent with what's already published.",
                 action="fill", selector="[data-demo='faq-url']",
                 value="https://riverside-hvac.example/furnace-repair"),
            Step("Go through them one at a time",
                 "Approve, edit, skip, or ask for another.",
                 "Per question, not per batch. Read every answer — this is going "
                 "on a client's live site with their name on it. An answer "
                 "that's plausible but wrong is the failure mode to watch for.",
                 action="look", selector="[data-demo='faq-review']", autofill=False),
            Step("Save to the client",
                 "Once nothing's pending it offers to save.",
                 "It stays on the client record permanently and flows into their "
                 "compiled schema file.",
                 action="click", selector="[data-demo='faq-save']"),
            Step("Download the accordion",
                 "The `</>` button on the saved row.",
                 "The HTML reads the fonts and colours off the page it came "
                 "from, so it looks native when it's pasted in. Native "
                 "<details> tags — no JavaScript to break on their site.",
                 action="click", selector="[data-demo='faq-export']"),
            Step("Mark it once it's live",
                 "The 'date added to site' column is editable on purpose.",
                 "Until there's a date here, the stale-content audit counts this "
                 "page as generated but never deployed. That column is how you "
                 "keep yourself honest.",
                 action="look", selector="[data-demo='faq-date']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="client360.proposal", module="hub",
        title="Put a sent proposal on the client record",
        goal="The PDF you actually sent, with the date you sent it, living on "
             "the client's record permanently.",
        minutes=3, path="/client360",
        steps=[
            Step("Find the client",
                 "Type any part of the name, domain or city.",
                 "This searches Knack clients, website records and in-house URLs "
                 "together — you don't have to remember which list something is "
                 "on.",
                 action="fill", selector="[data-demo='client-search']", value="Riverside"),
            Step("Open the Proposals card",
                 "Scroll to Proposals on the client record.",
                 "Uploaded proposals sit above anything the Proposal Builder "
                 "generated, in the same card.",
                 action="look", selector="[data-demo='proposals-card']"),
            Step("Upload what you actually sent",
                 "PDF, DOC or DOCX, up to 25 MB.",
                 "Upload the version that went to the client, not the draft. "
                 "This is the record you'll be reading back to them.",
                 action="upload", selector="input[type='file']", sample="demo/proposal"),
            Step("Set the date you sent it",
                 "Not today's date — the date it went out.",
                 "Editable afterwards. When they call in six months asking what "
                 "they were quoted, this is the answer, and the date is what "
                 "makes it credible.",
                 action="fill", selector="[data-demo='date-sent']", value="2026-08-04"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="qa.billing_audit", module="qa",
        title="Run the billing audit and read the result",
        goal="A list of Suite sub-accounts billing with no product on file, and "
             "an understanding of which ones actually matter.",
        minutes=4, path="/qa",
        steps=[
            Step("Open Suite Billing, No Active Product",
                 "Under the Suite (GoHighLevel) group.",
                 "Compares live Suite billing in GoHighLevel against products on "
                 "file in Knack.",
                 action="click", selector="[data-demo='qa-billing']"),
            Step("Read the rows, don't just count them",
                 "Each row is a sub-account we're billing with nothing on file.",
                 "Two very different things land in this list: a data gap where "
                 "somebody forgot to add the product in Knack, and a real case "
                 "where we're charging for something we're not delivering. Only "
                 "one of those is an emergency.",
                 action="look", selector="[data-demo='qa-rows']"),
            Step("Watch for 'no Knack match at all'",
                 "Flagged separately from 'matched but no products'.",
                 "No match usually means a name mismatch, not a missing client. "
                 "Check the spelling before you panic.",
                 action="look", selector="[data-demo='qa-nomatch']"),
            Step("Check the other direction too",
                 "Suite Billing This Month shows what's actually being billed.",
                 "The reverse case — a live product with no billing — is lost "
                 "revenue, and it's the report worth building next.",
                 action="click", selector="[data-demo='qa-thismonth']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="ads_builder.first_campaign", module="ads_builder",
        title="Generate a Google Ads campaign and get it approved",
        goal="An AI-drafted campaign checked against budget, sent for approval, "
             "and deployed paused — with nothing spending until a human "
             "un-pauses it.",
        minutes=9, path="/tools/ads",
        spends=["openai.text"],
        steps=[
            Step("Pick the account first",
                 "Choose the Google Ads account this campaign belongs to.",
                 "Everything downstream is scoped to this. Getting it wrong "
                 "means building a perfectly good campaign in someone else's "
                 "account.",
                 action="choose", selector="#acct", value="Riverside HVAC"),
            Step("Describe the business, not the campaign",
                 "Business name, sector and the geography you want to reach.",
                 "The generator writes better ad copy from what the business "
                 "*is* than from adjectives about the campaign. 'HVAC "
                 "contractor, Columbus metro' beats 'high-converting summer "
                 "promo'.",
                 action="fill", selector="#businessName", value="Riverside HVAC"),
            Step("Set the sector",
                 "This drives the keyword themes and the negative list.",
                 "The negative keyword list matters more than the positive one "
                 "for local service businesses — it's what stops you paying for "
                 "'hvac jobs' and 'hvac school'.",
                 action="choose", selector="#sector", value="Home Services"),
            Step("Geography",
                 "Where the ads should show. Be specific.",
                 "A radius the business can't actually service is the fastest "
                 "way to burn budget. Match it to their real van coverage, not "
                 "their ambition.",
                 action="fill", selector="#geography", value="Columbus, OH + 25 miles"),
            Step("Set the budget",
                 "Daily budget for the campaign.",
                 "Watch the label update. This is the number the budget check "
                 "runs against in two steps' time.",
                 action="fill", selector="#budget", value="60"),
            Step("Add anything the AI can't infer",
                 "Notes: seasonality, the offer, what they don't want to sell.",
                 "This is where you say 'they hate emergency callout work' or "
                 "'the $89 tune-up is the hook'. It changes the copy "
                 "meaningfully.",
                 action="fill", selector="#notes",
                 value="Push the $89 spring tune-up. No emergency callout work."),
            Step("Generate",
                 "The AI drafts campaigns, ad groups, keywords and copy.",
                 "Simulated here so we don't spend tokens. Read what comes back "
                 "as a first draft by a competent junior — good structure, "
                 "always worth editing.",
                 action="click", selector="#gen", simulated=True),
            Step("Run the budget check",
                 "Before anything is deployed.",
                 "This is the guard between a typo and a real overspend. A "
                 "daily budget entered as 600 instead of 60 is caught here or "
                 "it isn't caught at all.",
                 action="click", selector="[data-demo='budget-check']"),
            Step("Try a dry run",
                 "The dry toggle validates the whole deploy without sending it.",
                 "Use this every time on a new account. It surfaces "
                 "permission and structure problems while they're still free to "
                 "fix.",
                 action="click", selector="#dry"),
            Step("Send it for approval",
                 "It goes to the approvals page as a proposal.",
                 "Only proposals marked APPROVED can deploy. That's enforced in "
                 "code, not policy — you cannot skip it by clicking harder.",
                 action="click", selector="[data-demo='send-approval']"),
            Step("Deploy, and note what deploy means",
                 "One atomic mutate. Every campaign is created PAUSED.",
                 "If any single operation fails, Google rolls the entire batch "
                 "back — there is no such thing as a half-built campaign here. "
                 "And nothing spends a cent until a human un-pauses it in Google "
                 "Ads.",
                 action="click", selector="[data-demo='deploy']", simulated=True),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="sales_builder.first_quote", module="sales_builder",
        title="Build a client quote and send it as a PDF",
        goal="A numbered quote in the Q-10200 series with line items, a branded "
             "PDF, and a Word copy — saved so you can duplicate it next time.",
        minutes=8, path="/tools/sales",
        spends=["openai.text"],
        steps=[
            Step("Start a new quote",
                 "It allocates the next number in the Q-10200 series "
                 "automatically.",
                 "Never type a quote number yourself. The counter is shared, "
                 "and two people inventing numbers is how you end up with two "
                 "Q-10247s in the same month.",
                 action="click", selector="[data-demo='new-quote']"),
            Step("Client details",
                 "Who it's for and who's signing it.",
                 "The contact name here is what prints on the PDF cover. Use "
                 "the person who actually decides, not whoever emailed you.",
                 action="fill", selector="[data-demo='client-name']", value="Riverside HVAC"),
            Step("Build the line items",
                 "Add a category, then the services under it.",
                 "Group by what the client thinks they're buying — 'Website', "
                 "'Search', 'Social' — not by how you deliver it internally. "
                 "They read this to decide, not to audit you.",
                 action="click", selector="#addCat"),
            Step("Set audience and exclusions",
                 "Who the media should reach, and who it shouldn't.",
                 "The exclusions field is the one people skip and then "
                 "regret. Existing customers in a new-customer campaign is paid "
                 "reach you already had.",
                 action="fill", selector="#audIn", value="Homeowners 35-70, Columbus metro"),
            Step("Watch the estimate grid",
                 "It updates live as you add items.",
                 "Check the total against what you told the client on the "
                 "phone before you send it. A quote that arrives higher than "
                 "the conversation costs you the deal and the trust.",
                 action="look", selector="#estGrid"),
            Step("Use the AI draft for the narrative",
                 "It writes the covering description from your line items.",
                 "Simulated here. Treat it as a first pass — it's good at "
                 "structure and generic about the client. Edit in the specifics "
                 "you know and it isn't going to.",
                 action="click", selector="#descBtn", simulated=True),
            Step("Generate the PDF",
                 "Smart 1 branded, archived with the quote.",
                 "The archived copy is the one that was actually sent. If you "
                 "edit the quote later, that archive still shows what the client "
                 "received — which is the whole point of archiving it.",
                 action="click", selector="[data-demo='quote-pdf']"),
            Step("Grab the Word copy if you need to edit",
                 "Same quote, .docx.",
                 "For when procurement wants to redline it. Regenerate the PDF "
                 "from the tool afterwards rather than sending a Word file as "
                 "final.",
                 action="click", selector="[data-demo='quote-docx']"),
            Step("Mark it converted when they sign",
                 "The converted flag drives the dashboard.",
                 "This is the field that makes the pipeline numbers real. An "
                 "unmarked won quote is a quote the dashboard thinks you lost.",
                 action="look", selector="[data-demo='mark-converted']"),
            Step("Next time, duplicate instead",
                 "Duplicate copies the whole builder state to a new number.",
                 "Most quotes are a variation on the last one for that "
                 "vertical. Duplicating is faster and keeps your line items "
                 "consistent between clients.",
                 action="look", selector="[data-demo='duplicate']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="suite_panel.new_subaccount", module="suite_panel",
        title="Create a Smart 1 Suite sub-account for a new client",
        goal="A branded GoHighLevel sub-account built from the right snapshot, "
             "with the client's details and prospect contact in place.",
        minutes=6, path="/tools/suite",
        spends=["ghl.write"],
        steps=[
            Step("Check they don't already exist",
                 "Use the client lookup before creating anything.",
                 "Duplicate sub-accounts are painful to unwind and they break "
                 "the billing audit, which matches on name. Thirty seconds here "
                 "saves an afternoon.",
                 action="fill", selector="#clientLookup", value="Riverside"),
            Step("Pick the snapshot",
                 "This decides what the account is preloaded with.",
                 "The snapshot is the hardest thing to change afterwards — "
                 "pipelines, workflows and calendars all come from it. Choose it "
                 "like it's permanent, because in practice it is.",
                 action="choose", selector="[data-demo='snapshot']", value="Smart 1 Standard"),
            Step("Fill in the business details",
                 "Name, address, city, country, timezone.",
                 "The timezone drives every automation the client will ever "
                 "run. A wrong timezone means their appointment reminders go out "
                 "at the wrong hour and nobody works out why for weeks.",
                 action="fill", selector="#address", value="1420 Riverside Dr"),
            Step("Set the city",
                 "Used in the account record and in their local SEO fields.",
                 "",
                 action="fill", selector="#city", value="Columbus"),
            Step("Add the prospect contact",
                 "First name, last name, email, phone of the actual person.",
                 "This becomes their login and the first contact in the CRM. "
                 "Use the person who'll be in the system daily, not the owner "
                 "who'll never log in.",
                 action="fill", selector="[data-demo='prospect-first']", value="Dana"),
            Step("Brand it from their domain",
                 "Enter their domain and preview the branding.",
                 "Pulls their logo and colours automatically. Do it now — an "
                 "account that looks like Smart 1 instead of like the client "
                 "undercuts the whole white-label.",
                 action="fill", selector="#brandDomain", value="riverside-hvac.example"),
            Step("Create it",
                 "One click, written to the Hub activity log with your name on "
                 "it.",
                 "Simulated here — this creates a real sub-account. Every create "
                 "and delete in this tool is attributed, permanently.",
                 action="click", selector="#createBtn", simulated=True),
            Step("Then go add the product in Knack",
                 "Do it the same day.",
                 "This is the single most common cause of a row in the 'Suite "
                 "Billing, No Active Product' audit. Billing starts here; the "
                 "product record doesn't unless somebody adds it.",
                 action="look"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="sites_admin.inventory", module="sites_admin",
        title="Reconcile the Sites inventory and check the margin",
        goal="Every live Simvoly site discovered, matched to a client and a "
             "price, with the cost-versus-revenue margin visible.",
        minutes=6, path="/tools/sites",
        steps=[
            Step("Start with discovery",
                 "Pull the current list of sites straight from Simvoly.",
                 "This is the source of truth for what actually exists. "
                 "Anything in your records but not in this list is something "
                 "you may be billing for and not delivering.",
                 action="click", selector="[data-demo='discover']"),
            Step("Look at the unmatched rows first",
                 "Sites with no internal client name attached.",
                 "These are the ones costing you money anonymously. A site "
                 "nobody owns is either a forgotten test build or a client "
                 "whose record was never linked.",
                 action="look", selector="[data-demo='unmatched']"),
            Step("Attach an internal client name",
                 "Match the site to the client it belongs to.",
                 "Use exactly the name Knack uses. The billing audits match on "
                 "name, so 'Riverside HVAC LLC' here and 'Riverside HVAC' there "
                 "produces a false alarm every month.",
                 action="fill", selector="[name='internal_client_name']",
                 value="Riverside HVAC"),
            Step("Set the client price",
                 "What you actually charge them for this site.",
                 "Leave it blank and the margin calculation silently treats it "
                 "as zero, which makes a profitable site look like a loss.",
                 action="fill", selector="[name='client_price']", value="149"),
            Step("Check the package",
                 "The plan determines your cost side.",
                 "Cost comes from the package, revenue from the client price. "
                 "Both have to be right for the margin to mean anything.",
                 action="look", selector="[data-demo='packages']"),
            Step("Read the margin",
                 "Revenue minus cost across the portfolio.",
                 "A negative or thin margin usually isn't a pricing problem — "
                 "it's a site on a package richer than the client needs, or a "
                 "site still being paid for after the client left.",
                 action="look", selector="[data-demo='margin']"),
            Step("Export it",
                 "CSV of the whole inventory.",
                 "Worth doing monthly and keeping. Comparing this month's "
                 "export to last month's is how you spot a site that quietly "
                 "changed plan.",
                 action="click", selector="[data-demo='export-csv']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="google_finder.client_check", module="google_finder",
        title="Check a client's Google properties and answer a question",
        goal="A read on whether a client's GA4, Search Console and Business "
             "Profile are connected and healthy, plus one question answered "
             "from their analytics.",
        minutes=6, path="/tools/google",
        spends=["openai.text"],
        steps=[
            Step("Refresh the connected properties",
                 "Pulls the current list of GA4, GSC and GMB properties.",
                 "The counts at the top are the fastest health check you have. "
                 "A client with GA4 but no Search Console is a client whose "
                 "organic performance you cannot actually report on.",
                 action="click", selector="[data-demo='refresh']"),
            Step("Look at what's missing, not what's there",
                 "Compare the GA / GSC / GMB counts.",
                 "The gaps are the work. A missing Search Console connection is "
                 "usually five minutes of access, and it unlocks half the SEO "
                 "reporting.",
                 action="look", selector="#cnt-all"),
            Step("Run the SEO snapshot",
                 "Current organic position for the client.",
                 "This is what you'd open a monthly review with. Read the trend, "
                 "not the absolute number — position 14 climbing beats position "
                 "9 falling.",
                 action="click", selector="[data-demo='seo-snapshot']"),
            Step("Check for anomalies",
                 "Unusual movement in the analytics.",
                 "Anomalies are worth checking *before* the client calls about "
                 "one. A traffic drop you noticed first is a conversation; one "
                 "they noticed first is a problem.",
                 action="click", selector="[data-demo='anomalies']"),
            Step("Ask a question in plain English",
                 "Use the ask box rather than building a report.",
                 "Simulated here. Ask what you'd ask a person — 'which pages "
                 "lost the most traffic last month' — not what you'd type into "
                 "a query builder.",
                 action="fill", selector="#ask-q",
                 value="which pages lost the most organic traffic last month",
                 simulated=True),
            Step("Bulk-add to Search Console when you're onboarding",
                 "Adds several properties at once.",
                 "Use this on a new client rather than one at a time. It's the "
                 "difference between a ten-minute job and an hour.",
                 action="look", selector="#btn-run-gsc-bulk"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="proposal_builder.generate", module="proposal_builder",
        title="Generate a client-ready proposal from a brief",
        goal="A branded proposal PDF built from client details, saved to the "
             "searchable log and pushed into Smart 1 Suite as an opportunity.",
        minutes=7, path="/tools/proposals",
        spends=["openai.text", "ghl.write"],
        steps=[
            Step("Pick the industry first",
                 "It sets the template, the language and the default package.",
                 "Get this right before typing anything else — changing it "
                 "later rebuilds the proposal and loses your edits.",
                 action="click", selector="#indGrid"),
            Step("Business details",
                 "Business name, website, city, state, ZIP.",
                 "The ZIP is not decoration — it drives the market sizing that "
                 "appears in the proposal. A wrong ZIP produces a confidently "
                 "wrong market analysis.",
                 action="fill", selector="#c_business_name", value="Riverside HVAC"),
            Step("Their website",
                 "The real one, with https.",
                 "It gets read to pull context. A typo here means the proposal "
                 "is built with no knowledge of their business at all.",
                 action="fill", selector="#c_website",
                 value="https://riverside-hvac.example"),
            Step("Contact details",
                 "Name, email and phone of the person who'll receive it.",
                 "These become the opportunity in Smart 1 Suite. Phone matters "
                 "— an opportunity with no phone number is one nobody follows "
                 "up.",
                 action="fill", selector="#c_contact_name", value="Dana Whitfield"),
            Step("State their budget if you know it",
                 "The monthly figure they've actually said out loud.",
                 "If you enter one, the recommendation is built to it. If you "
                 "leave it blank, the recommendation comes from the market. "
                 "Entering a number you invented is how a proposal ends up "
                 "quoting a tier the client already told you they can't afford.",
                 action="fill", selector="#c_monthly_budget", value="2500"),
            Step("What they want out of it",
                 "Their goals, in their words if you have them.",
                 "Quote the client back to themselves where you can. 'We need "
                 "the phone to ring in summer' lands better than 'increase "
                 "qualified lead volume'.",
                 action="fill", selector="#c_goals",
                 value="Fill the summer schedule; more tune-ups, fewer emergency calls"),
            Step("Generate",
                 "Builds the proposal, the PDF, and the Suite opportunity.",
                 "Simulated here — this writes a real opportunity into "
                 "GoHighLevel. In live use, check the opportunity value looks "
                 "sane before you move on.",
                 action="click", selector="#genBtn", simulated=True),
            Step("Read it before you send it",
                 "Especially the numbers and the recommended tier.",
                 "Everything is defensible only if you can explain it on a "
                 "call. If a figure in there would stump you, edit it now.",
                 action="look", selector="#canvas"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="image_optimizer.resize_crop", module="image_optimizer",
        title="Resize, crop and hit a target file size",
        goal="One image cropped to the ratio you need, at the exact dimensions "
             "and under a specific KB budget.",
        minutes=4, path="/tools/image-optimizer",
        steps=[
            Step("Know which tool you want",
                 "This one is for a single image you need at exact dimensions.",
                 "If you're naming a batch for SEO, use the SEO Image Pipeline "
                 "instead — it does the AI naming and files them against a "
                 "client. This tool is the precision one: exact pixels, exact "
                 "file size.",
                 action="look"),
            Step("Drop the image in",
                 "Drag onto the dropzone or click it.",
                 "",
                 action="upload", selector="#image", sample="demo/hero-photo"),
            Step("Crop before you resize",
                 "Turn on crop and drag the box.",
                 "Crop first. Resizing then cropping throws away resolution you "
                 "just paid for — you end up with a smaller, softer image than "
                 "you needed to.",
                 action="click", selector="#crop_enabled"),
            Step("Lock the aspect ratio",
                 "Keeps the proportions while you drag.",
                 "Unlock it only when you deliberately want to distort "
                 "something, which is almost never.",
                 action="click", selector="#lock_aspect"),
            Step("Set the exact dimensions",
                 "Width and height in pixels.",
                 "Use the size the slot actually needs. Uploading a 2400px "
                 "image into a 600px slot means the visitor downloads four "
                 "times the data for no visible benefit.",
                 action="fill", selector="#width", value="1200"),
            Step("Set a target file size",
                 "Enter a KB budget and let it find the quality.",
                 "This is the feature worth knowing about. Rather than nudging "
                 "the quality slider and re-checking, say 'under 200 KB' and it "
                 "solves for it.",
                 action="fill", selector="#target_kb", value="200"),
            Step("Name the output",
                 "Set the filename before you download.",
                 "Same rules as the SEO pipeline: hyphens, lower case, "
                 "descriptive. A file called 'final-v2-USE-THIS.jpg' will end up "
                 "on a client's site eventually.",
                 action="fill", selector="#output_name",
                 value="riverside-hvac-summer-tune-up-hero"),
            Step("Optimize and download",
                 "Check the before/after in the details panel.",
                 "If the saving is small, the image was probably already "
                 "optimised — don't re-process it repeatedly. Each pass through "
                 "a lossy format loses a little more.",
                 action="click", selector="#submit"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="pdf_optimizer.compress", module="pdf_optimizer",
        title="Shrink a PDF that's too big to email",
        goal="A compressed, faster-loading PDF that's never larger than the "
             "one you started with.",
        minutes=2, path="/tools/pdf-optimizer",
        steps=[
            Step("Use it on the ones that are actually a problem",
                 "Proposals and reports with photos in them.",
                 "A text-only PDF is already small — running it through here "
                 "gains you almost nothing. The wins are in anything with "
                 "images.",
                 action="look"),
            Step("Upload the PDF",
                 "Drop it in.",
                 "",
                 action="upload", selector="input[type='file']", sample="demo/proposal-large"),
            Step("Optimize",
                 "Ghostscript recompresses the images and fonts, then qPDF "
                 "linearizes it.",
                 "Linearizing is the part people don't know about: it lets a "
                 "browser show page one before the rest has downloaded. On a "
                 "40-page proposal opened on a phone, that's the difference "
                 "between reading it and closing it.",
                 action="click", selector="[data-demo='optimize']"),
            Step("Check the result",
                 "Compare the sizes.",
                 "The output is never larger than the input — if compression "
                 "wouldn't help, you get your original back rather than a "
                 "bigger file. So there's no downside to trying.",
                 action="look", selector="[data-demo='pdf-result']"),
            Step("Download it and check it still reads",
                 "Open it before sending.",
                 "Heavy compression can soften screenshots and small text. On a "
                 "client-facing proposal, look at it once before it goes out.",
                 action="click", selector="[data-demo='pdf-download']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="calculators.publish", module="calculators",
        title="Put a lead-capture calculator on a client page",
        goal="A live calculator embedded on a page, capturing name, email and "
             "phone before it releases the full estimate — and you know where "
             "the leads land.",
        minutes=6, path="/tools/calculators/",
        steps=[
            Step("Pick the calculator that matches the pitch",
                 "Digital Audio, Connected TV, DOOH, IMS Trade, or Female 18–34.",
                 "Choose by what you're actually selling that client. A CTV "
                 "calculator on a page selling radio just teaches the prospect "
                 "you weren't listening.",
                 action="look", selector="[data-demo='calc-list']"),
            Step("Run it yourself first",
                 "Fill in the inputs the way a prospect would and press Run.",
                 "Never publish one you haven't run. You want to know what "
                 "number a realistic input produces before a prospect sees it "
                 "and asks you to defend it.",
                 action="click", selector="#runBtn"),
            Step("Notice what comes back before the gate",
                 "Headline metrics only.",
                 "This is the important bit. The withheld detail is never sent "
                 "to the browser — it's stored server-side under a token. It "
                 "isn't a CSS blur someone can delete in devtools, which is how "
                 "every calculator in the old suite leaked its full report.",
                 action="look", selector="#metrics"),
            Step("Now fill the gate",
                 "Name, email and phone.",
                 "All three are validated server-side. Phone is required — it "
                 "was required in none of the eleven apps in the original "
                 "suite, which is why so many leads were uncontactable.",
                 action="fill", selector="#name", value="Dana Whitfield"),
            Step("See the full detail unlock",
                 "The step table and full breakdown appear.",
                 "The unlock trades the token for the rest. Same result the "
                 "prospect gets — worth seeing once so you can talk them "
                 "through it on a call.",
                 action="look", selector="#detailTable"),
            Step("Check the leads page",
                 "Everything captured, exportable as CSV.",
                 "This is behind the Hub login; the calculator itself is "
                 "public. Check it after any campaign that sends traffic — a "
                 "calculator quietly capturing nothing looks identical to one "
                 "nobody used.",
                 action="click", selector="[data-demo='calc-leads']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="image_picker.client_shoot", module="image_picker",
        title="Build a client's image library by industry",
        goal="A set of on-brand stock images saved to one client's library, "
             "sized for the web and ready to push to their site.",
        minutes=5, path="/tools/image-picker/",
        spends=["cloudinary.write"],
        steps=[
            Step("Add the client first",
                 "Name, industry and location.",
                 "Industry drives the suggested search chips, and location is "
                 "what stops a Columbus HVAC company getting palm trees in "
                 "every photo.",
                 action="fill", selector="#newName", value="Riverside HVAC"),
            Step("Set the industry",
                 "Pick from the list rather than typing something new.",
                 "The taxonomy is what makes the chips useful. A one-off "
                 "industry string gets no suggestions at all.",
                 action="choose", selector="#industrySel", value="Home Services"),
            Step("Use the suggested chips before searching",
                 "They're built from the industry you just set.",
                 "Faster than inventing search terms, and more consistent "
                 "across clients in the same vertical — which matters when "
                 "someone else picks up the account.",
                 action="look", selector="#chips"),
            Step("Search across every provider at once",
                 "One box, all three stock libraries.",
                 "Same fan-out as Image Creator. If one provider is "
                 "unconfigured the others still return, so a missing key "
                 "degrades rather than breaks.",
                 action="fill", selector="#q", value="hvac technician service van"),
            Step("Add what fits, then check the count",
                 "Selected images show a running count.",
                 "Aim for a usable set rather than everything that looks nice. "
                 "Ten strong images beat forty you'll never place.",
                 action="look", selector="#count"),
            Step("Save to the client library",
                 "Resized to the max edge, uploaded to their folder.",
                 "Simulated here. In live use these land in the client's own "
                 "Cloudinary folder and show up in Client 360 alongside "
                 "anything the SEO pipeline optimised.",
                 action="click", selector="#addBtn", simulated=True),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="page_image_optimizer.fix_page", module="page_image_optimizer",
        title="Fix the heavy images on a client's live page",
        goal="The heaviest images on one real page found, optimised five at a "
             "time, and the replacement tags ready to paste.",
        minutes=6, path="/tools/page-images",
        spends=["openai.text"],
        steps=[
            Step("Start from a real page, not the homepage",
                 "Paste the URL of the page that's actually slow.",
                 "A site audit will have told you which one. Fixing the "
                 "homepage when the service page is the problem is a common "
                 "waste of an afternoon.",
                 action="fill", selector="#pageUrl",
                 value="https://riverside-hvac.example/furnace-repair"),
            Step("Name the client",
                 "Used for the filenames and the archive.",
                 "Same naming rules as the SEO pipeline — the client's name "
                 "belongs in the filename.",
                 action="fill", selector="#company", value="Riverside HVAC"),
            Step("Read the scan result",
                 "How many images found, and their combined weight.",
                 "The weight number is the one to quote back to the client. "
                 "'Your service page ships 14 MB of images' lands; 'your "
                 "images aren't optimised' doesn't.",
                 action="look", selector="#nWeight"),
            Step("Pick the heavy ones",
                 "You don't have to do all of them.",
                 "Sort by size and take the worst offenders. The top three "
                 "images are usually most of the page weight.",
                 action="look", selector="#choose"),
            Step("Optimise in batches of five",
                 "Deliberately small.",
                 "Five at a time keeps the review honest — you actually look "
                 "at each filename and alt text instead of rubber-stamping "
                 "forty. The scan stays resumable, so you can stop and return.",
                 action="click", selector="#optimizeBtn", simulated=True),
            Step("Copy all the tags",
                 "Complete <img> tags for the whole batch.",
                 "These replace the originals on the client's page. Hand them "
                 "over with the weight-saved figure — that's the proof the "
                 "work happened.",
                 action="click", selector="#copyAll"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="google_access.request", module="google_access",
        title="Get access to a client's Google properties with one link",
        goal="One link sent to the client that puts Smart 1 on their Analytics, "
             "Tag Manager, Ads and Business Profile — without ever handling "
             "their password.",
        minutes=5, path="/tools/google-access/",
        steps=[
            Step("Understand what this replaces",
                 "No more 'can you add us as a user' email threads.",
                 "The client signs in with Google once and grants access "
                 "themselves. We never see or store a credential — which is "
                 "both safer and much easier to explain to a nervous client.",
                 action="look"),
            Step("Create the request",
                 "Client name and the email that owns their Google properties.",
                 "It must be the address that actually owns the accounts. The "
                 "office manager's login usually isn't it, and the link will "
                 "fail confusingly if you guess.",
                 action="fill", selector="[name='client_name']", value="Riverside HVAC"),
            Step("Their Google account email",
                 "The one with admin on Analytics and Ads.",
                 "",
                 action="fill", selector="[name='client_email']",
                 value="dana@riverside-hvac.example"),
            Step("Choose which services to request",
                 "Only tick what you actually need.",
                 "Asking for everything makes the consent screen longer and "
                 "scarier. Request what the engagement needs; you can send a "
                 "second link later.",
                 action="look", selector="[name='service']"),
            Step("Send the link",
                 "Copy it and paste it into your own email to them.",
                 "The link expires — 14 days by default. That's deliberate: an "
                 "access link that lives forever in an inbox is a liability. "
                 "Use Extend if a client goes quiet rather than issuing a new "
                 "one.",
                 action="click", selector="#copyBtn"),
            Step("Watch the status",
                 "Pending until they complete it, then granted per service.",
                 "If one service fails and the others succeed, that's usually "
                 "the client not being an admin on that one property. Check "
                 "the per-service detail before re-sending.",
                 action="click", selector="#refreshBtn"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="fan_radio.spot", module="fan_radio",
        title="Produce a football radio spot for a client",
        goal="A :30 spot written to a daypart, voiced, and on a share page the "
             "client can listen to and approve.",
        minutes=7, path="/tools/fan-radio/",
        spends=["openai.text"],
        steps=[
            Step("Pick the client and the daypart",
                 "Pre-Game Prep, Game Day, or Post-Game.",
                 "The daypart isn't decoration — it changes the whole read. "
                 "Pre-game is anticipation, game day is urgency, post-game is "
                 "the wind-down. A generic spot in a football pod sounds like "
                 "an ad; a daypart-matched one sounds like part of the show.",
                 action="fill", selector="#client", value="Riverside HVAC"),
            Step("Write the brief, not the script",
                 "Offer, audience, must-say, and what to avoid.",
                 "The must-say and avoid fields do the most work. 'Must say "
                 "$89 tune-up' and 'never say emergency callout' produce a "
                 "usable first draft; a vague brief produces vague copy.",
                 action="fill", selector="#b_offer", value="$89 spring tune-up"),
            Step("Fill the avoid list properly",
                 "Anything the client has told you not to say.",
                 "This is the field that saves the re-record. Claims they "
                 "can't substantiate, competitor names, a service they've "
                 "stopped offering.",
                 action="fill", selector="#b_avoid",
                 value="No emergency callout. No price guarantees."),
            Step("Generate and read it aloud",
                 "Actually read it out at pace.",
                 "Radio copy that scans fine on screen often doesn't fit :30 "
                 "when spoken. If you're rushing the last line, it's too long "
                 "— cut before you record, not after.",
                 action="click", selector="[data-demo='fr-generate']", simulated=True),
            Step("Pick the voice and cast it",
                 "Match the voice to the client, not to your own taste.",
                 "A contractor's spot and a law firm's spot want different "
                 "reads. Listen to a line before committing the whole spot.",
                 action="look", selector="#cast"),
            Step("Send the share page",
                 "A clean page where the client listens and approves.",
                 "Don't email an MP3 — the share page keeps the daypart and "
                 "brief context alongside the audio, so their feedback is "
                 "about the right thing.",
                 action="click", selector="#copyurl"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="qa.stale_creative", module="qa",
        title="Find clients whose creative has gone stale",
        goal="A list of paying clients who haven't had new creative in months "
             "— sorted so the ones at real risk surface first.",
        minutes=4, path="/qa/stale-creative",
        steps=[
            Step("Read the buckets, not the total",
                 "30 / 60 / 90 days, plus never.",
                 "The 'never' group is the one that matters. A client paying "
                 "for creative who has received none is the clearest churn "
                 "signal in the Hub.",
                 action="look", selector="[data-demo='sc-buckets']"),
            Step("Check which sources are reporting",
                 "The footer names them.",
                 "If a tool you use is listed under 'no records', the audit "
                 "isn't seeing it and the report is understating the problem. "
                 "An audit that silently finds nothing is worse than no audit.",
                 action="look", selector="[data-demo='sc-sources']"),
            Step("Look at 'unmatched' before you panic",
                 "Creative that didn't match any client.",
                 "Usually a name mismatch between a Cloudinary folder and the "
                 "client registry, not missing work. Fix the name and the row "
                 "moves to the right client.",
                 action="look", selector="[data-demo='sc-unmatched']"),
            Step("Turn it into this week's list",
                 "Cross-reference against who's paying for creative.",
                 "A house URL with no creative is fine. A paying SEO client at "
                 "90+ days is a conversation you want to start before they do.",
                 action="look"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="commercial_builder.first_spot", module="commercial_builder",
        title="Produce a :30 CTV commercial end to end",
        goal="A finished :30 spot — concept, timed script, storyboard, voice, "
             "QC-passed and rendered — for one client.",
        minutes=10, path="/tools/commercial-builder/",
        spends=["openai.text"],
        steps=[
            Step("Set the brand profile before the brief",
                 "Logo, colours, fonts, phone, CTA, tagline.",
                 "Everything downstream reads from here — the end card, the "
                 "persistent logo, the QC check for brand logo. Filling it "
                 "once saves re-doing the CTA on every spot.",
                 action="look", selector="[data-demo='cb-brand']"),
            Step("Choose length and platform together",
                 ":05 / :15 / :30 / :60, and CTV / YouTube / Both.",
                 "These change the rules, not just the runtime. CTV requires a "
                 "QR code and gets bigger end-card text for living-room "
                 "viewing; YouTube gets checked for a hook inside the first "
                 "five seconds because that's when it becomes skippable.",
                 action="choose", selector="[data-demo='cb-length']", value="30"),
            Step("Write the brief, get three concepts",
                 "Materially different angles, not three phrasings of one.",
                 "Pick the one you'd defend to the client, not the safest. You "
                 "can regenerate — it costs a call, not a re-shoot.",
                 action="click", selector="[data-demo='cb-concepts']", simulated=True),
            Step("Generate the timed script",
                 "Word count is enforced against the length.",
                 "65–75 words for a :30. It follows a Hook/Value/Close beat "
                 "structure rather than splitting time evenly, and flags "
                 "itself in QC if it lands outside the range. Read it aloud "
                 "anyway.",
                 action="click", selector="[data-demo='cb-script']", simulated=True),
            Step("Build the storyboard",
                 "Per scene: find stock, generate, use a spokesperson, upload, "
                 "or pull a client asset.",
                 "This is the centrepiece. Scene durations must sum exactly to "
                 "your length — the tool enforces it, so shortening one scene "
                 "means lengthening another.",
                 action="look", selector="[data-demo='cb-storyboard']"),
            Step("Set the voice and the music level",
                 "Music level maps to real dB ducking.",
                 "Voice always dominates. A music bed that competes with the "
                 "read is the most common reason a spot gets rejected.",
                 action="look", selector="[data-demo='cb-voice']"),
            Step("Build the CTA — and enable the QR on CTV",
                 "QR code plus a persistent logo.",
                 "On CTV there's nothing to click, so the QR is how the spot "
                 "converts at all. It must hold at least 8 seconds on the end "
                 "card; QC hard-fails without it on a CTV spot.",
                 action="click", selector="[data-demo='cb-qr']"),
            Step("Run QC before you render",
                 "Eleven checks: timing, voice fit, CTA, logo, resolution, "
                 "aspect, safe area, spelling, QR, persistent logo, hook.",
                 "The render is blocked by default if QC fails. That's a hard "
                 "gate on purpose — catching an incomplete commercial here is "
                 "the entire point of automatic QC.",
                 action="click", selector="[data-demo='cb-qc']"),
            Step("Render, then reuse it",
                 "Create Variation clones the storyboard and re-runs only "
                 "what changed.",
                 "Offer, location, weather, CTA, voice, duration. Locked "
                 "scenes and footage choices survive — so the second spot for "
                 "a client costs a fraction of the first.",
                 action="look", selector="[data-demo='cb-variations']"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="radio_promo.first_spot", module="radio_promo",
        title="Write and voice a radio spot",
        goal="A scripted spot, voiced, saved to the library and ready to send.",
        minutes=5, path="/tools/radio-promo/",
        spends=["openai.text"],
        steps=[
            Step("Say who it's for and what's on offer",
                 "Client, offer, and the length you're buying.",
                 "Length is a hard constraint, not a preference. :30 is about "
                 "70 words spoken at a natural pace — write 90 and the read "
                 "either gallops or gets cut.",
                 action="fill", selector="[name='client']", value="Riverside HVAC"),
            Step("Add the must-say and the must-not",
                 "Anything legal requires, and anything they've asked you to "
                 "avoid.",
                 "The avoid list saves the re-record. A claim they can't "
                 "substantiate is the expensive kind of mistake here, because "
                 "it's caught after the voice work is paid for.",
                 action="look"),
            Step("Generate, then read it aloud",
                 "Actually say it at pace.",
                 "Copy that scans fine on screen often doesn't fit when "
                 "spoken. If you're rushing the last line, cut before you "
                 "record.",
                 action="click", selector="[data-demo='rp-generate']", simulated=True),
            Step("Pick a voice that matches the client",
                 "Not the one you like most.",
                 "A contractor and a law firm want different reads. Listen to "
                 "one line before committing the whole spot — regenerating "
                 "audio costs credits.",
                 action="look", selector="[data-demo='rp-voice']"),
            Step("Save it to the library",
                 "So the next spot for this client starts from something.",
                 "Most spots are a variation on the last one. The library is "
                 "what makes the second one quick.",
                 action="look"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="landing_ads.from_page", module="landing_ads",
        title="Generate ads from a landing page",
        goal="Search, social, display and audio ad copy drawn from a real "
             "Suite landing page, consistent with what the page actually says.",
        minutes=5, path="/tools/landing-ads/",
        spends=["openai.text"],
        steps=[
            Step("Pick the landing page, not the homepage",
                 "The ads should match the page the click lands on.",
                 "This is the whole point of generating from the page rather "
                 "than from a brief: promising something in the ad that isn't "
                 "on the page is what kills conversion rate and quality score "
                 "at the same time.",
                 action="look", selector="[data-demo='la-page']"),
            Step("Check what it read off the page",
                 "The offer and proof points it extracted.",
                 "If this is wrong the ads will be too. Faster to correct here "
                 "than to regenerate.",
                 action="look"),
            Step("Generate the set",
                 "Search, social, display and audio together.",
                 "Simulated here. Generating them together keeps the message "
                 "consistent across channels, which is the thing that usually "
                 "drifts when each is written separately.",
                 action="click", selector="[data-demo='la-generate']", simulated=True),
            Step("Read the headlines against the character limits",
                 "Each channel truncates differently.",
                 "A headline cut mid-word reads as careless. The counters show "
                 "you before the platform does.",
                 action="look"),
            Step("Export what you'll actually upload",
                 "Copy per channel.",
                 "Paste straight into the ad platform — no reformatting, which "
                 "is where typos get introduced.",
                 action="look"),
        ]),

    # ------------------------------------------------------------------
    Scenario(
        key="tickets.triage", module="tickets",
        title="Work the website request queue",
        goal="A clear view of what's open, what's gone stale, and what to "
             "chase — with the client history behind each request.",
        minutes=4, path="/tools/tickets/",
        steps=[
            Step("Start with what's stale, not what's newest",
                 "Sort by age.",
                 "A new request has someone's attention already. A three-week-"
                 "old one has a client wondering whether we forgot, and that's "
                 "the one that costs you the relationship.",
                 action="look", selector="[data-demo='tk-age']"),
            Step("Read the client's history before replying",
                 "Open the client to see their other requests.",
                 "Their fourth request this month about the same page means "
                 "something different from their first. Context changes the "
                 "answer.",
                 action="look"),
            Step("Look for the repeats",
                 "The same request from several clients.",
                 "Three people asking for the same thing is a product gap, not "
                 "three tickets. Fixing it once beats answering it three times.",
                 action="look"),
            Step("Export when you need to work offline",
                 "CSV of the current view.",
                 "Useful for a weekly review with whoever does the work — the "
                 "queue is easier to triage in a spreadsheet than one ticket "
                 "at a time.",
                 action="look", selector="[data-demo='tk-export']"),
        ]),
]

_BY_KEY = {s.key: s for s in SCENARIOS}


def get(key: str) -> Scenario | None:
    return _BY_KEY.get(key)


def for_module(module: str) -> list[dict]:
    return [s.as_dict() for s in SCENARIOS if s.module == module]


def catalogue() -> list[dict]:
    """Everything, for the 'Learn the tools' index page."""
    return [{"key": s.key, "module": s.module, "title": s.title, "goal": s.goal,
             "minutes": s.minutes, "path": s.path, "step_count": len(s.steps),
             "spends": s.spends} for s in SCENARIOS]


def total_minutes() -> int:
    return sum(s.minutes for s in SCENARIOS)


def modules_covered() -> list[str]:
    return sorted({s.module for s in SCENARIOS})


def missing_for(expected: list[str]) -> list[str]:
    """Tools with no walkthrough yet — coverage audit."""
    have = set(modules_covered())
    return sorted(m for m in expected if m not in have)
