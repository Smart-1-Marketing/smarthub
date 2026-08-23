"""Help content: one registry, three surfaces.

Bubbles, guided tours and (later) the Ask assistant all read from here, so a
piece of explanation is written once and shows up everywhere it's relevant.
Content lives in Python rather than inside templates for three reasons: it can
be edited without touching markup, it can be searched, and it can be audited
for coverage — `missing_for()` will tell you which screens have no help at all.

Keys are `module.screen.element`. Anything with a `step` is also a tour stop,
ordered by that number.

Writing guidance, learned from watching people use these tools: say what the
field *does to the output*, not what it is. "Medium is the channel type —
`cpc`, `email`, `social`" is a definition. "Google groups your traffic by
medium, so `cpc` and `CPC` become two separate rows in your report" is why
anyone should care.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Help:
    key: str
    title: str
    body: str
    step: int | None = None          # tour order within its screen
    selector: str = ""               # CSS target for the tour highlight
    link: str = ""
    link_text: str = ""

    @property
    def screen(self) -> str:
        return ".".join(self.key.split(".")[:2])

    def as_dict(self) -> dict:
        return {"key": self.key, "title": self.title, "body": self.body,
                "step": self.step, "selector": self.selector,
                "link": self.link, "linkText": self.link_text}


def _h(*a, **kw) -> Help:
    return Help(*a, **kw)


REGISTRY: list[Help] = [

    # ---------------- Dashboard ----------------
    _h("hub.dashboard.tiles", "Your tools",
       "Every tool lives behind this one login. Tiles you haven't set up yet "
       "still open — they'll tell you which key is missing rather than failing "
       "quietly.", step=1, selector="[data-tour='tiles']"),
    _h("hub.dashboard.client_search", "Find a client fast",
       "Type any part of a client's name, domain or city. This searches Knack "
       "clients, website records and in-house URLs together, so you don't have "
       "to remember which list something is on.", step=2,
       selector="[data-tour='client-search']"),
    _h("hub.dashboard.activity", "What the team has been doing",
       "Every action across every tool, attributed to whoever did it. Useful "
       "when something changed and nobody remembers changing it.", step=3,
       selector="[data-tour='activity']"),
    _h("hub.dashboard.version", "Which build is live",
       "The version in the footer is read from the running code, not from a "
       "config file. If it doesn't match what you last deployed, the deploy "
       "didn't take."),

    # ---------------- Client 360 ----------------
    _h("hub.client360.header", "One record per client",
       "Everything the Hub knows about this client: products, website, scans, "
       "images, proposals, schema and FAQ pages.", step=1,
       selector="[data-tour='client-header']"),
    _h("hub.client360.products", "Products on file",
       "Pulled from Knack. This is what the billing audits compare against — "
       "a client billing for Suite with no Suite product here is what "
       "'Suite Billing, No Active Product' flags.", step=2,
       selector="[data-tour='products']"),
    _h("hub.client360.proposals", "Proposal history",
       "Upload the PDF you actually sent, with the date you sent it. It stays "
       "on the client record permanently, so when they call in six months you "
       "can see exactly what they were quoted.", step=3,
       selector="[data-tour='proposals']"),
    _h("hub.client360.images", "Client image library",
       "Everything the SEO Image Pipeline has optimised for this client, plus "
       "screenshots and logos pulled from their site audits.", step=4,
       selector="[data-tour='client-images']"),

    # ---------------- SEO Image Pipeline ----------------
    _h("seo_images.upload.details", "Fill these in first",
       "Company, page URL and project name are sent to the AI along with the "
       "picture. That's the difference between an image named "
       "'man-with-tools.webp' and one named "
       "'riverside-hvac-technician-servicing-outdoor-ac-unit.webp'.", step=1,
       selector="[data-tour='project-details']"),
    _h("seo_images.upload.max_edge", "Resize before converting",
       "This caps the longest edge before the image is converted. It matters "
       "more than the format does: converting a 6000-pixel camera photo to "
       "WebP leaves it 6000 pixels wide and still enormous. Capping it at 2400 "
       "first takes the same photo down by about 88%. Use 2400 for hero "
       "images, 1600 for in-page content, 1200 for thumbnails.", step=2,
       selector="[data-tour='max-edge']"),
    _h("seo_images.review.alt", "Alt text, 125 characters",
       "Screen readers cut off around 125 characters and Google gives you no "
       "credit past roughly that either. Describe what's in the picture as if "
       "to someone who can't see it — don't stuff keywords; that reads as spam "
       "to both.", step=3, selector="[data-tour='alt-field']"),
    _h("seo_images.review.filename", "Filename is a ranking signal",
       "Hyphens between words, lower case, no underscores, no dates, no 'v2'. "
       "The client's name and the page's subject should both appear.", step=4,
       selector="[data-tour='filename-field']"),
    _h("seo_images.results.copy_tag", "Copy the whole tag",
       "Gives you a complete <img> element with the URL, alt text and "
       "dimensions filled in. Dimensions prevent the page jumping around as "
       "images load, which is a Core Web Vitals score.", step=5,
       selector="[data-tour='copy-tag']"),

    # ---------------- Image Creator ----------------
    _h("image_creator.canvas.size", "Pick your size first",
       "Changing canvas size later rescales everything on it. 1080x1080 for a "
       "feed post, 1080x1920 for stories and reels, 1200x630 for a link "
       "preview, 300x250 and 728x90 for display ads.", step=1,
       selector="[data-tour='canvas-size']"),
    _h("image_creator.photos.search", "One search, three libraries",
       "Searches Pexels, Pixabay and Unsplash at once and mixes the results, "
       "so you don't have to check three sites. If one provider is down or "
       "unconfigured the others still return.", step=2,
       selector="[data-tour='photo-search']"),
    _h("image_creator.photos.describe", "Describe it instead",
       "Stuck for search terms? Write the situation — 'someone frustrated "
       "because their AC stopped working' — and the AI turns it into three "
       "searches that actually match stock library tagging.", step=3,
       selector="[data-tour='describe-search']"),
    _h("image_creator.logos.search", "Logos by company name",
       "Type a company name or domain. Brandfetch returns the logo variants "
       "and the brand colours, and the colours drop straight into your picker.",
       step=4, selector="[data-tour='logo-search']"),
    _h("image_creator.layers.panel", "Nothing is flattened",
       "Every element stays a live, editable object — including anything the "
       "AI generated. Save the project and reopen it months later with the "
       "text still editable.", step=5, selector="[data-tour='layers']"),
    _h("image_creator.export.scale", "Export at 2x for print or retina",
       "1x is right for the web. 2x when it'll be viewed on a high-density "
       "screen or printed. Transparent background only works on PNG and WebP.",
       step=6, selector="[data-tour='export']"),

    # ---------------- Background Remover ----------------
    _h("bg_remover.upload.credits", "This one costs money",
       "Each cutout spends a remove.bg credit. The remaining balance is shown "
       "before you spend one, and identical images are cached — retrying the "
       "same file never charges twice.", step=1,
       selector="[data-tour='credit-balance']"),
    _h("bg_remover.upload.white_bg", "Try the free option first",
       "For a logo on a plain white background, 'Remove white background' runs "
       "in your browser, costs nothing, and is usually as good. Save the paid "
       "AI removal for photographs and complicated edges.", step=2,
       selector="[data-tour='white-bg']"),

    # ---------------- UTM Builder ----------------
    _h("utm.form.source", "Where the click came from",
       "The specific property: google, facebook, linkedin, the name of a "
       "newsletter. Not the type of channel — that's medium.", step=1,
       selector="[data-tour='utm-source']"),
    _h("utm.form.medium", "What kind of channel it was",
       "cpc, email, social, referral, display. Google Analytics groups traffic "
       "by this field, and it is case-sensitive: 'Paid Social' and "
       "'paid-social' become two separate rows in every report you ever run. "
       "That's the whole reason this tool normalises values for you.", step=2,
       selector="[data-tour='utm-medium']"),
    _h("utm.form.campaign", "Name it so you'll recognise it in six months",
       "Pick a shape and keep it — 'spring-tuneup-2026' beats 'promo'. You'll "
       "be reading these in a report long after you've forgotten the context.",
       step=3, selector="[data-tour='utm-campaign']"),
    _h("utm.form.save", "Save it against the client",
       "Saved links stay on the client record, so the next person building a "
       "link for that client copies your naming instead of inventing their own.",
       step=4, selector="[data-tour='utm-save']"),

    # ---------------- Site Scans ----------------
    _h("scans.new.domain", "One domain per scan",
       "Each scan spends an Insites credit, so double-check the domain before "
       "you start. Paste the domain, not an email address.", step=1,
       selector="[data-tour='scan-domain']"),
    _h("scans.table.status", "What the statuses mean",
       "Running means Insites is still working — audits take one to four "
       "minutes. Complete means results are in. Error means something went "
       "wrong and 'Try again' will re-pull it without spending a second "
       "credit.", step=2, selector="[data-tour='scan-status']"),
    _h("scans.table.refresh", "Results pull automatically",
       "The page checks Insites directly every 45 seconds for up to fifteen "
       "minutes. You don't need to sit on it — come back later and it'll be "
       "there.", step=3, selector="[data-tour='scan-refresh']"),

    # ---------------- Schema & FAQ ----------------
    _h("seo.schema.known_first", "It uses what we already know",
       "Anything already on the client record is used first. It only goes "
       "looking for their Google Business Profile to fill fields that are "
       "still blank, and it never overwrites something you've entered.",
       step=1, selector="[data-tour='schema-known']"),
    _h("seo.faq.accordion", "The accordion export matches their site",
       "The downloaded HTML reads the fonts and colours off the page it came "
       "from, so it looks native when it's pasted in. Plain <details> tags — "
       "no JavaScript to break.", step=2, selector="[data-tour='faq-export']"),
    _h("seo.schema.date_added", "Mark it once it's live",
       "Editable on purpose. Until a date is here, the stale-content audit "
       "counts this page as generated but never deployed."),

    # ---------------- QA / audits ----------------
    _h("qa.reports.billing", "Billing vs products",
       "Compares Suite billing in GoHighLevel against products on file in "
       "Knack. A sub-account billing with nothing on file is either a data "
       "gap or something we're charging for and not delivering — worth "
       "checking either way.", step=1, selector="[data-tour='qa-billing']"),
    _h("qa.reports.spend", "What the AI tools are costing",
       "Every OpenAI call is logged with its module, tokens and estimated "
       "cost. If one tool starts spending unexpectedly, this is where it "
       "shows up.", step=2, selector="[data-tour='qa-spend']"),

    # ---------------- Demo ----------------
    _h("demo.banner.what", "You're in a demo session",
       "Everything is sample data. Scans, AI calls, uploads and Suite changes "
       "are all intercepted — nothing you click here spends a credit or "
       "touches a real client. Break whatever you like.", step=1,
       selector="[data-tour='demo-banner']"),

    # ---------------- Sales leads ----------------
    _h("hub.leads.store", "Every lead, one list",
       "Landing pages, calculators and the ad builder all write here before "
       "anything is sent anywhere else. That order matters: a GoHighLevel "
       "outage or a rotated token can delay delivery, but it can no longer "
       "lose a lead you already have.",
       step=1, selector="[data-tour='leads-store']"),
    _h("hub.leads.delivery", "sent vs queued",
       "\u201csent\u201d means GoHighLevel accepted it. \u201cqueued\u201d means we still hold it "
       "and delivery has not succeeded yet \u2014 hover it for the last error. "
       "Retry pushes every queued row again; nothing is lost in the meantime.",
       step=2, selector="[data-tour='leads-delivery']"),
    _h("hub.leads.convert", "Linking a lead to a client",
       "A client here is anyone with a product in Knack, because that is what "
       "billing reads. So this links the lead to an account that already "
       "exists rather than creating one \u2014 create the account in Knack "
       "first, then link it, and the history of where it came from survives.",
       step=3, selector="[data-tour='leads-convert']"),

    # ---------------- System status ----------------
    _h("hub.status.checks", "What these checks actually do",
       "Each one makes a real call with the key that is configured, rather "
       "than reporting whether a variable is set. A key that is present but "
       "expired, revoked or pointed at the wrong account fails here \u2014 "
       "which is the whole point.",
       step=1, selector="[data-tour='status-checks']"),
    _h("hub.status.errors", "The error log",
       "Boot-time failures are caught so one broken module cannot take the "
       "Hub down, but they are recorded here rather than swallowed. If a tool "
       "is missing from the sidebar or a page 404s for no reason, this is "
       "where the reason is.",
       step=2, selector="[data-tour='status-errors']"),

    # ---------------- Activity ----------------
    _h("hub.activity.log", "Who did what",
       "Every action across every tool, attributed by name. Anything recorded "
       "against a client also appears on that client's 360 record, so the "
       "question \u201cwho changed this account and when\u201d has an answer "
       "without asking around.",
       step=1, selector="[data-tour='activity-log']"),

    # ---------------- SEO clients ----------------
    _h("hub.seo.list", "Where this list comes from",
       "Clients with a live SEO product in Knack \u2014 the same source "
       "billing uses, read live rather than from an export. A client who "
       "belongs here but is missing usually has the product recorded under a "
       "slightly different name.",
       step=1, selector="[data-tour='seo-list']"),

    # ---------------- Creative ----------------
    _h("hub.creative.pick", "Which of these you want",
       "Image Creator is the full editor for making something new. Client "
       "Image Uploads builds a client's library from stock search. Page Image "
       "Optimizer fixes images already live on a page. Image Optimizer is for "
       "one file to an exact size \u2014 for naming a batch for SEO, use the "
       "SEO Image Pipeline instead.",
       step=1, selector="[data-tour='creative-tiles']"),

    # ---------------- Display ads ----------------
    _h("display_ads.start.kind", "Client or prospect",
       "This is asked rather than guessed because the two are filed "
       "differently. A client is matched to the registry so the creative "
       "lands on the right account even if the name is typed differently. A "
       "prospect becomes a lead you can find in Sales leads afterwards, "
       "instead of a name that exists only on this one build.",
       step=1, selector="[data-tour='ads-kind']"),
    _h("display_ads.attach.filing", "What attaching does",
       "The renderer has already put every finished ad in Cloudinary, in this "
       "same account, so filing records the image that is already there "
       "rather than uploading it again. That keeps one copy, and keeps the "
       "link the renderer is holding working.",
       step=1, selector="[data-tour='ads-attach']"),

]

_BY_KEY = {h.key: h for h in REGISTRY}


def get(key: str) -> Help | None:
    return _BY_KEY.get(key)


def text(key: str) -> str:
    h = _BY_KEY.get(key)
    return h.body if h else ""


def tour(screen: str) -> list[dict]:
    """Ordered tour steps for a screen, e.g. tour('seo_images.upload')."""
    steps = [h for h in REGISTRY if h.step and h.key.startswith(screen + ".")]
    if not steps:
        prefix = screen.split(".")[0]
        steps = [h for h in REGISTRY if h.step and h.key.startswith(prefix + ".")]
    return [h.as_dict() for h in sorted(steps, key=lambda x: x.step or 999)]


def screens() -> list[str]:
    return sorted({h.screen for h in REGISTRY})


def search(term: str, limit: int = 8) -> list[dict]:
    """Backs the 'how do I…' half of the Ask box."""
    t = (term or "").lower().strip()
    if not t:
        return []
    scored = []
    for h in REGISTRY:
        score = 0
        if t in h.title.lower():
            score += 3
        if t in h.body.lower():
            score += 2
        if t in h.key.lower():
            score += 1
        for word in t.split():
            if len(word) > 3 and word in h.body.lower():
                score += 1
        if score:
            scored.append((score, h))
    scored.sort(key=lambda x: -x[0])
    return [h.as_dict() for _, h in scored[:limit]]


def missing_for(expected_screens: list[str]) -> list[str]:
    """Coverage audit — screens with no help written yet."""
    have = set(screens())
    return sorted(s for s in expected_screens if s not in have)


def as_json() -> dict:
    return {"help": {h.key: h.as_dict() for h in REGISTRY},
            "screens": screens()}
